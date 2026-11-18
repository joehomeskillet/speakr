"""Ingest recovery sweep against a real directory and a real SQLite database."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest

from src.app import app, db
from src.clinic.ingest import IngestService, storage_key_for
from src.clinic.ingest_recovery import ORIGINALS_DIR, sweep_originals
from src.models import User

PAYLOAD = b'\x03ds2' + bytes(range(256))


@pytest.fixture
def opened(tmp_path):
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        user = User(username='rc' + suffix, email=suffix + '@example.invalid', password='x')
        db.session.add(user)
        db.session.commit()
        yield IngestService(tmp_path), user.id, tmp_path


def _deliver(service, author, key, data=PAYLOAD):
    return service.store_original(
        BytesIO(data),
        declared_sha256=hashlib.sha256(data).hexdigest(),
        declared_size=len(data),
        origin='agent',
        origin_ref='agent:a:src-1:v1',
        author_user_id=author,
        idempotency_key=key,
    )


def _age(path: Path, seconds: int) -> None:
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp), follow_symlinks=False)


def test_empty_store_reports_nothing(opened):
    _service, _author, root = opened
    report = sweep_originals(root)
    assert report.counts == {
        'scanned': 0, 'removed_leftovers': 0, 'incomplete': 0, 'awaiting_row': 0, 'errors': 0,
    }


def test_partial_hardlink_of_a_published_file_is_removed(opened):
    service, author, root = opened
    receipt = _deliver(service, author, 'k-a')
    final = service.file_path(receipt.storage_key)
    leftover = final.parent / '.partial-crashed'
    os.link(final, leftover)
    assert final.stat().st_nlink == 2

    report = sweep_originals(root)

    assert [e.path for e in report.removed_leftovers] == [f'{ORIGINALS_DIR}/.partial-crashed']
    assert not leftover.exists()
    assert final.read_bytes() == PAYLOAD
    assert final.stat().st_nlink == 1
    assert report.incomplete == [] and report.awaiting_row == []


def test_sweep_is_idempotent(opened):
    service, author, root = opened
    receipt = _deliver(service, author, 'k-idem')
    final = service.file_path(receipt.storage_key)
    os.link(final, final.parent / '.partial-x')
    orphan = final.parent / '.partial-orphan'
    orphan.write_bytes(b'half')
    _age(orphan, 7200)

    first = sweep_originals(root)
    second = sweep_originals(root)

    assert first.counts['removed_leftovers'] == 1
    assert second.counts['removed_leftovers'] == 0
    assert [e.path for e in second.incomplete] == [e.path for e in first.incomplete]
    assert orphan.read_bytes() == b'half'


def test_old_partial_without_final_is_reported_not_deleted(opened):
    service, _author, root = opened
    directory = root / ORIGINALS_DIR
    directory.mkdir(parents=True)
    old = directory / '.partial-old'
    old.write_bytes(b'unfinished')
    _age(old, 7200)

    report = sweep_originals(root)

    assert [e.path for e in report.incomplete] == [f'{ORIGINALS_DIR}/.partial-old']
    assert report.incomplete[0].size == len(b'unfinished')
    assert report.incomplete[0].age_seconds >= 7200
    assert report.removed_leftovers == []
    assert old.read_bytes() == b'unfinished'


def test_fresh_partial_without_final_is_left_alone_and_unreported(opened):
    _service, _author, root = opened
    directory = root / ORIGINALS_DIR
    directory.mkdir(parents=True)
    fresh = directory / '.partial-fresh'
    fresh.write_bytes(b'in flight')

    report = sweep_originals(root)

    assert report.incomplete == [] and report.removed_leftovers == []
    assert fresh.exists()
    assert sweep_originals(root, stale_after=timedelta(seconds=0)).incomplete != []


def test_final_file_without_row_is_reported_not_deleted(opened):
    service, _author, root = opened
    key = storage_key_for('k-norow')
    final = service.file_path(key)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(PAYLOAD)

    report = sweep_originals(root)

    assert [e.path for e in report.awaiting_row] == [key]
    assert final.read_bytes() == PAYLOAD
    assert report.removed_leftovers == [] and report.incomplete == []


def test_file_with_row_is_not_reported(opened):
    service, author, root = opened
    receipt = _deliver(service, author, 'k-row')
    report = sweep_originals(root)
    assert report.awaiting_row == []
    assert service.file_path(receipt.storage_key).read_bytes() == PAYLOAD
    assert report.counts['scanned'] == 1


def test_awaiting_row_heals_with_the_same_delivery(opened):
    service, author, root = opened
    key = 'k-heal'
    final = service.file_path(storage_key_for(key))
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(PAYLOAD)
    assert len(sweep_originals(root).awaiting_row) == 1

    _deliver(service, author, key)

    assert sweep_originals(root).awaiting_row == []
