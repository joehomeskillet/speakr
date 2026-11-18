"""Immutable dictation ingest: bytes, storage-before-row, idempotent receipt.

The original store is a real directory. The database error is injected by
replacing the commit call. Nothing here is evidence for a device or for ASR.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic import agent_channel_ingest as ing
from src.clinic import ingest as ingest_mod
from src.clinic.agent_channel import ChannelPolicy
from src.clinic.ingest import IngestError, IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
PAYLOAD = b'\x03ds2' + bytes(range(256)) + b'\x00\xff' * 40
KEY = 'idem-key-0001'


@pytest.fixture
def opened(tmp_path):
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        user = User(
            username='ig' + suffix,
            email=suffix + '@example.invalid',
            password='x',
        )
        db.session.add(user)
        db.session.commit()
        yield IngestService(tmp_path), user.id


def _files(root: Path) -> list[Path]:
    found = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            found.append(Path(dirpath) / name)
    return found


def _deliver(service, author, data=PAYLOAD, key=KEY, **overrides):
    digest = hashlib.sha256(data).hexdigest()
    kwargs = dict(
        declared_sha256=digest,
        declared_size=len(data),
        origin='agent',
        origin_ref='agent:a:src-1:v1',
        author_user_id=author,
        idempotency_key=key,
        now=NOW,
    )
    kwargs.update(overrides)
    return service.store_original(BytesIO(data), **kwargs)


def test_stored_bytes_match_the_stream_and_are_not_replaced(opened):
    service, author = opened
    receipt = _deliver(service, author)
    path = service.file_path(receipt.storage_key)
    assert path.read_bytes() == PAYLOAD
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt.sha256
    row = db.session.get(ClinicOriginal, receipt.original_id)
    assert row.content_hash == receipt.sha256
    assert int(row.size_bytes) == len(PAYLOAD)
    inode = path.stat().st_ino
    with pytest.raises(IngestError) as exc:
        _deliver(service, author, data=b'not-the-original')
    assert exc.value.code == 'conflict'
    assert path.read_bytes() == PAYLOAD
    assert path.stat().st_ino == inode
    assert ClinicOriginal.query.count() == 1


def test_verified_stored_path_rejects_mismatched_metadata(opened):
    service, author = opened
    fields = dict(idempotency_key=KEY, declared_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                  declared_size=len(PAYLOAD))
    assert service.stored_path(**fields) is None
    receipt = _deliver(service, author)
    assert service.stored_path(**fields) == service.file_path(receipt.storage_key)
    for override in (dict(declared_sha256="ab" * 32), dict(declared_size=len(PAYLOAD) + 1)):
        with pytest.raises(IngestError) as exc:
            service.stored_path(**{**fields, **override})
        assert exc.value.code == "conflict"
    assert service.file_path(receipt.storage_key).read_bytes() == PAYLOAD


def test_verified_stored_path_rejects_a_symlink(opened):
    service, author = opened
    receipt = _deliver(service, author)
    other_key = KEY + "-symlink"
    path = service.file_path(ingest_mod.storage_key_for(other_key))
    path.symlink_to(service.file_path(receipt.storage_key))
    with pytest.raises(IngestError) as exc:
        service.stored_path(idempotency_key=other_key, declared_sha256=receipt.sha256,
                            declared_size=len(PAYLOAD))
    assert exc.value.code == "conflict"
    assert service.file_path(receipt.storage_key).read_bytes() == PAYLOAD


def test_deferred_ingest_commit_joins_the_callers_transaction(opened):
    service, author = opened
    receipt = _deliver(service, author, commit=False)
    path = service.file_path(receipt.storage_key)
    inode = path.stat().st_ino
    assert db.session.get(ClinicOriginal, receipt.original_id) is not None
    db.session.rollback()
    assert ClinicOriginal.query.count() == 0
    assert ClinicDictation.query.count() == 0
    assert Recording.query.filter_by(user_id=author).count() == 0
    assert path.read_bytes() == PAYLOAD
    retried = _deliver(service, author)
    assert db.session.get(ClinicOriginal, retried.original_id) is not None
    assert path.stat().st_ino == inode
    assert path.read_bytes() == PAYLOAD


def test_wrong_declared_hash_writes_no_file(opened):
    service, author = opened
    with pytest.raises(IngestError) as exc:
        _deliver(service, author, declared_sha256='ab' * 32)
    assert exc.value.code == 'invalid'
    assert _files(service.root) == []
    assert ClinicOriginal.query.count() == 0


def test_database_error_leaves_no_row_and_retry_reuses_the_file(opened, monkeypatch):
    service, author = opened
    real = ingest_mod._commit
    state = {'fail': True}

    def flaky():
        if state['fail']:
            raise OperationalError('INSERT', {}, Exception('db down'))
        real()

    monkeypatch.setattr(ingest_mod, '_commit', flaky)
    with pytest.raises(OperationalError):
        _deliver(service, author)
    assert ClinicOriginal.query.count() == 0
    assert ClinicDictation.query.count() == 0
    assert Recording.query.filter_by(user_id=author).count() == 0
    files = _files(service.root)
    assert len(files) == 1
    assert files[0].read_bytes() == PAYLOAD
    inode = files[0].stat().st_ino
    state['fail'] = False
    receipt = _deliver(service, author)
    assert ClinicOriginal.query.filter_by(id=receipt.original_id).count() == 1
    assert ClinicDictation.query.count() == 1
    assert Recording.query.filter_by(user_id=author).count() == 1
    assert files[0].stat().st_ino == inode
    assert files[0].read_bytes() == PAYLOAD
    assert _files(service.root) == files


def test_same_delivery_returns_the_same_receipt(opened):
    service, author = opened
    first = _deliver(service, author)
    path = service.file_path(first.storage_key)
    inode = path.stat().st_ino
    saved = db.session.get(ClinicOriginal, first.original_id).receipt
    second = _deliver(service, author)
    assert second == first
    assert path.stat().st_ino == inode
    assert path.read_bytes() == PAYLOAD
    assert ClinicOriginal.query.count() == 1
    assert ClinicDictation.query.count() == 1
    assert db.session.get(ClinicOriginal, first.original_id).receipt == saved
    assert len(_files(service.root)) == 1


def test_missing_author_keeps_the_file_until_an_author_is_known(opened):
    service, author = opened
    with pytest.raises(IngestError) as exc:
        _deliver(service, None)
    assert exc.value.code == 'author_required'
    assert ClinicOriginal.query.count() == 0
    files = _files(service.root)
    assert len(files) == 1
    assert files[0].read_bytes() == PAYLOAD
    inode = files[0].stat().st_ino
    receipt = _deliver(service, author)
    assert receipt.dictation_id
    assert files[0].stat().st_ino == inode
    assert ClinicOriginal.query.count() == 1


def test_finalize_ingest_can_call_store_original(opened):
    """The agent channel calls store_original with these keywords and reads the result back."""
    service, author = opened
    signature = inspect.signature(service.store_original)
    for name in (
        'declared_sha256', 'declared_size', 'origin', 'origin_ref',
        'author_user_id', 'idempotency_key', 'now',
    ):
        assert name in signature.parameters
    source = inspect.getsource(ing.finalize_ingest)
    assert 'author_user_id=None' in source

    ch.create_tables()
    policy = ChannelPolicy(master_secret=b's' * 32, authorise=lambda _user, _action: True)
    reg = ch.register_agent(
        policy, actor_user_id=author, display_name='Agent', allowed_sources=['usb-1'],
        credential_fingerprint='aa' * 20,
    )
    principal = ch.authenticate_agent('aa' * 20)
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    ch.report_inventory(principal, policy, {'items': [{
        'source_id': 'src-1', 'root_id': 'usb-1', 'relative_path': 'a/b.dss',
        'size': len(PAYLOAD), 'mtime_utc': '2026-11-17T07:00:00.1234567+00:00',
        'sha256': digest, 'snapshot_version': 'v1',
    }]}, now=NOW)
    intent = ch.create_intent(
        policy, actor_user_id=author, agent_id=reg['agent_id'], source_id='src-1',
        snapshot_version='v1', now=NOW,
    )
    begun = ing.begin_ingest(
        principal, policy, intent_id=intent['intent_id'], declared_size=len(PAYLOAD),
        declared_sha256=digest, idempotency_key=KEY, now=NOW,
    )
    ing.put_chunk(
        principal, policy, ingest_id=begun['ingest_id'], sequence=0, offset=0,
        data=PAYLOAD, chunk_sha256=digest, now=NOW,
    )
    body = {
        'chunks': [{'sequence': 0, 'bytes': len(PAYLOAD), 'sha256': digest}],
        'total_bytes': len(PAYLOAD), 'sha256': digest,
    }
    first = ing.finalize_ingest(
        principal, policy, ingest_id=begun['ingest_id'], body=body,
        store_original=service.store_original, now=NOW,
    )
    # The channel passes no author, so the row cannot be inserted yet.
    assert first['state'] == 'UNKNOWN'
    assert first['original_id'] is None
    assert ClinicOriginal.query.count() == 0
    files = _files(service.root)
    assert len(files) == 1
    assert files[0].read_bytes() == PAYLOAD
    inode = files[0].stat().st_ino

    agent_key = f"agent-ingest:{reg['agent_id']}:{KEY}"
    healed = service.store_original(
        BytesIO(PAYLOAD),
        declared_sha256=digest,
        declared_size=len(PAYLOAD),
        origin='agent',
        origin_ref=f"agent:{reg['agent_id']}:src-1:v1",
        author_user_id=author,
        idempotency_key=agent_key,
        now=NOW,
    )
    again = ing.finalize_ingest(
        principal, policy, ingest_id=begun['ingest_id'], body=body,
        store_original=service.store_original, now=NOW,
    )
    assert again['state'] == 'STORED'
    assert again['original_id'] == healed.original_id
    assert again['dictation_id'] == healed.dictation_id
    assert files[0].stat().st_ino == inode
    assert files[0].read_bytes() == PAYLOAD
    assert len(_files(service.root)) == 1
    assert ClinicOriginal.query.count() == 1
