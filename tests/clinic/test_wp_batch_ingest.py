"""Bounded multi-file import (w035). Synthetic bytes only; authorization is injected."""
from __future__ import annotations

import uuid

import pytest

from src.app import app, db
from src.clinic import batch_ingest as bi
from src.clinic.batch_ingest import BatchFile, BatchIngestError, ingest_batch
from src.clinic.ingest import IngestError, IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import history
from src.models import Recording, User


def _wav(tag: bytes) -> bytes:
    return b"RIFF\x24\x00\x00\x00WAVE" + tag.ljust(32, b".")


@pytest.fixture
def ctx(tmp_path):
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        users = [User(username=f"{n}{suffix}", email=f"{n}{suffix}@example.invalid", password="x")
                 for n in ("a", "b")]
        db.session.add_all(users)
        db.session.commit()
        author, other = users
        recording = Recording(user_id=author.id, status="PENDING", is_inbox=False)
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
        db.session.add(dictation)
        db.session.commit()
        yield IngestService(tmp_path), author.id, other.id, dictation.id


def _target(allowed_authors, allowed_pools=(None, 1)):
    def check(actor, author, pool):
        return (author is None or author in allowed_authors) and pool in allowed_pools
    return check


def _doc_auth(dictation, actor):
    return dictation.author_user_id == actor


def _run(ctx, files, *, batch="b1", actor=None, authors=None, pools=(None, 1)):
    service, author, _other, _d = ctx
    return ingest_batch(
        service, batch_id=batch, actor_user_id=actor or author, files=files,
        authorize_target=_target(authors if authors is not None else {author}, pools),
        authorize_document=_doc_auth,
    )


def _originals(batch_key_part):
    return ClinicOriginal.query.filter(ClinicOriginal.receipt.contains(batch_key_part)).count()


def test_each_file_gets_own_key_and_outcome(ctx):
    _s, author, _o, _d = ctx
    files = [BatchFile("a.wav", _wav(b"a"), author), BatchFile("b.wav", _wav(b"b"), author)]
    out = _run(ctx, files, batch="own-key")
    assert [o.status for o in out] == [bi.STORED, bi.STORED]
    assert out[0].import_key != out[1].import_key
    assert out[0].original_id != out[1].original_id
    assert out[0].dictation_id != out[1].dictation_id


def test_mixed_batch_keeps_good_files(ctx):
    _s, author, other, dictation_id = ctx
    files = [
        BatchFile("good.wav", _wav(b"g"), author),
        BatchFile("note.exe", b"MZ", author),
        BatchFile("fake.wav", b"not a wav at all", author),
        BatchFile("rest.wav", _wav(b"r"), other),  # author not allowed
        BatchFile("text.txt", "Befund unauffällig.".encode(), author, dictation_id=dictation_id),
        BatchFile("late.wav", _wav(b"l"), author),
    ]
    out = _run(ctx, files, batch="mixed")
    assert [o.status for o in out] == [
        bi.STORED, bi.REJECTED, bi.REJECTED, bi.REJECTED, bi.STORED, bi.STORED]
    assert out[1].code == "unsupported"
    assert out[2].code == "type_mismatch"
    assert out[3].code == "forbidden" and out[3].message
    assert ClinicOriginal.query.filter_by(id=out[0].original_id).one()
    assert ClinicOriginal.query.filter_by(id=out[5].original_id).one()
    assert out[3].original_id is None
    assert len(history(db.session.get(ClinicDictation, dictation_id))) == 1


def test_duplicate_content_inside_batch_is_rejected(ctx):
    _s, author, _o, _d = ctx
    out = _run(ctx, [BatchFile("a.wav", _wav(b"x"), author), BatchFile("copy.wav", _wav(b"x"), author)],
               batch="dup")
    assert [o.status for o in out] == [bi.STORED, bi.REJECTED]
    assert out[1].code == "duplicate"


def test_retry_reuses_receipts_even_reordered(ctx):
    _s, author, _o, _d = ctx
    a, b = BatchFile("a.wav", _wav(b"1"), author), BatchFile("b.wav", _wav(b"2"), author)
    first = _run(ctx, [a, b], batch="retry")
    again = _run(ctx, [b, a], batch="retry")
    assert [o.status for o in again] == [bi.REPLAYED, bi.REPLAYED]
    assert again[0].original_id == first[1].original_id
    assert again[1].original_id == first[0].original_id
    assert ClinicOriginal.query.filter(ClinicOriginal.id.in_(
        [first[0].original_id, first[1].original_id])).count() == 2


def test_retry_after_interruption_completes_only_missing_file(ctx):
    _s, author, _o, _d = ctx
    a, b = BatchFile("a.wav", _wav(b"3"), author), BatchFile("b.wav", _wav(b"4"), author)
    first = _run(ctx, [a], batch="interrupt")
    full = _run(ctx, [a, b], batch="interrupt")
    assert [o.status for o in full] == [bi.REPLAYED, bi.STORED]
    assert full[0].original_id == first[0].original_id


def test_author_and_pool_are_checked_for_every_file(ctx):
    _s, author, other, _d = ctx
    files = [
        BatchFile("a.wav", _wav(b"5"), author, pool_id=1),
        BatchFile("b.wav", _wav(b"6"), author, pool_id=99),   # pool not allowed
        BatchFile("c.wav", _wav(b"7"), other, pool_id=1),     # author not allowed
    ]
    out = _run(ctx, files, batch="authz")
    assert [o.status for o in out] == [bi.STORED, bi.REJECTED, bi.REJECTED]
    assert [o.code for o in out[1:]] == ["forbidden", "forbidden"]


def test_audio_without_author_is_rejected(ctx):
    out = _run(ctx, [BatchFile("a.wav", _wav(b"8"))], batch="noauthor")
    assert out[0].status == bi.REJECTED and out[0].code == "author_required"


def test_replay_with_other_author_conflicts(ctx):
    _s, author, other, _d = ctx
    _run(ctx, [BatchFile("a.wav", _wav(b"9"), author)], batch="bound")
    out = ingest_batch(
        ctx[0], batch_id="bound", actor_user_id=author,
        files=[BatchFile("a.wav", _wav(b"9"), other)],
        authorize_target=_target({author, other}), authorize_document=_doc_auth,
    )
    assert out[0].status == bi.REJECTED and out[0].code == "conflict"


def test_document_needs_dictation_and_permission(ctx):
    _s, author, other, dictation_id = ctx
    doc = "Text".encode()
    out = _run(ctx, [BatchFile("n.txt", doc, author)], batch="d1")
    assert out[0].code == "dictation_required"
    out = _run(ctx, [BatchFile("n.txt", doc, author, dictation_id=dictation_id)],
               batch="d2", actor=other, authors={None, other})
    assert out[0].status == bi.REJECTED and out[0].code == "forbidden"


def test_document_stale_version_and_replay(ctx):
    _s, author, _o, dictation_id = ctx
    doc = BatchFile("n.txt", "Kontrolle.".encode(), author, dictation_id=dictation_id)
    stale = BatchFile("m.txt", "Anderer Text.".encode(), author, dictation_id=dictation_id,
                      expected_version=5)
    out = _run(ctx, [stale], batch="d3")
    assert out[0].status == bi.REJECTED and out[0].code == "stale_version"
    first = _run(ctx, [doc], batch="d3")
    assert first[0].status == bi.STORED
    assert _run(ctx, [doc], batch="d3")[0].status == bi.REPLAYED


def test_storage_failure_is_reported_without_rolling_back_others(ctx, monkeypatch):
    service, author, _o, _d = ctx
    real = service.store_original
    calls = []

    def flaky(stream, **kw):
        calls.append(kw["origin_ref"])
        if kw["origin_ref"] == "b.wav":
            raise IngestError("storage_failed", "disk")
        return real(stream, **kw)

    monkeypatch.setattr(service, "store_original", flaky)
    files = [BatchFile(n, _wav(n.encode()), author) for n in ("a.wav", "b.wav", "c.wav")]
    out = _run(ctx, files, batch="fail")
    assert [o.status for o in out] == [bi.STORED, bi.FAILED, bi.STORED]
    assert out[1].code == "storage_failed"
    monkeypatch.undo()
    retry = _run(ctx, files, batch="fail")
    assert [o.status for o in retry] == [bi.REPLAYED, bi.STORED, bi.REPLAYED]


def test_invalid_batch_arguments_write_nothing(ctx):
    service, author, _o, _d = ctx
    with pytest.raises(BatchIngestError):
        ingest_batch(service, batch_id="", actor_user_id=author, files=[],
                     authorize_target=_target({author}), authorize_document=_doc_auth)
    with pytest.raises(BatchIngestError):
        ingest_batch(service, batch_id="x", actor_user_id=author, files=["a.wav"],
                     authorize_target=_target({author}), authorize_document=_doc_auth)
