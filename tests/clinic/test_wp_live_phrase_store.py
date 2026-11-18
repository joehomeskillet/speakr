"""Synthetic phrase persistence, idempotency, holder fencing and failure recovery."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import audio_revisions, live_phrases as phrases, live_session as live, revisions
from src.clinic.ingest import IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User


NOW = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def scene(tmp_path):
    with app.app_context():
        live.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        phrases.create_tables()
        suffix = uuid.uuid4().hex[:10]
        owner = User(username="phrase" + suffix, email=suffix + "@example.invalid", password="x")
        other = User(username="other" + suffix, email="other" + suffix + "@example.invalid", password="x")
        db.session.add_all([owner, other])
        db.session.flush()
        recording = Recording(user_id=owner.id, title="synthetic live", status="PENDING")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64,
                                  size_bytes=10, storage_key="synthetic/original",
                                  receipt="synthetic", origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        audio_revision = audio_revisions.init_audio_revision(dictation, original, 3000)
        transcript = revisions.add_asr_revision(dictation, original, "Eine Phrase", "local-test")
        session = live.start(dictation.id, owner.id, NOW, client_id="browser")
        values = dict(store=IngestService(tmp_path), dictation_id=dictation.id,
                      session_id=session.id, user_id=owner.id, client_id="browser",
                      now=NOW + timedelta(seconds=1), generation=1, sequence=1,
                      event_id="event-1", audio_start_ms=0, audio_end_ms=1000,
                      audio=b"synthetic-raw-audio", audio_revision_id=audio_revision.id,
                      transcript_revision_id=transcript.id,
                      text_hash=transcript.normalised_text_hash)
        db.session.commit()
        yield values, other.id, dictation.id, original.id
        db.session.rollback()
        db.session.remove()
        phrases.ClinicLivePhrase.__table__.drop(db.engine, checkfirst=True)


def _next(first, **changes):
    values = dict(first, sequence=2, generation=2, event_id="event-2",
                  audio_start_ms=1000, audio_end_ms=2000,
                  audio=b"synthetic-next-audio", now=NOW + timedelta(seconds=2))
    values.update(changes)
    return values


def test_persist_and_retrieve_after_reconnect(scene):
    first, _, _, _ = scene
    ack = phrases.store_phrase(**first)
    assert ack.status == "server_saved"
    assert ack.sequence == 1 and ack.generation == 1
    assert first["store"].file_path(ack.audio_storage_key).read_bytes() == first["audio"]
    db.session.remove()
    row = db.session.get(phrases.ClinicLivePhrase, ack.phrase_id)
    session = db.session.get(live.ClinicLiveSession, first["session_id"])
    assert (row.dictation_id, row.session_id, row.audio_revision_id) == (
        first["dictation_id"], first["session_id"], first["audio_revision_id"])
    assert (row.audio_start_ms, row.audio_end_ms, row.transcript_revision_id,
            row.text_hash) == (0, 1000, first["transcript_revision_id"], first["text_hash"])
    assert (session.ack_seq, session.audio_offset_ms, session.text_ref) == (
        1, 1000, first["transcript_revision_id"])
    assert phrases.store_phrase(**first) == ack
    assert phrases.ClinicLivePhrase.query.count() == 1


def test_retry_after_later_phrase_and_same_sequence_conflicts(scene):
    first, _, _, _ = scene
    ack = phrases.store_phrase(**first)
    second = _next(first)
    assert phrases.store_phrase(**second).generation == 2
    assert phrases.store_phrase(**first) == ack
    assert phrases.ClinicLivePhrase.query.count() == 2
    for change in ({"audio": b"different"}, {"event_id": "new-event"},
                   {"text_hash": "0" * 64}, {"audio_start_ms": 1}):
        with pytest.raises(phrases.PhraseError, match="sequence_conflict"):
            phrases.store_phrase(**dict(first, **change))
    with pytest.raises(phrases.PhraseError, match="stale_sequence"):
        phrases.store_phrase(**dict(second, sequence=3, event_id="event-3", generation=1,
                                    audio_start_ms=2000, audio_end_ms=3000))
    with pytest.raises(phrases.PhraseError, match="event_conflict"):
        phrases.store_phrase(**_next(first, sequence=3, generation=3, event_id="event-1",
                                     audio_start_ms=2000, audio_end_ms=3000))
    with pytest.raises(phrases.PhraseError, match="stale_sequence"):
        phrases.store_phrase(**_next(first, sequence=3, generation=3, event_id="event-3",
                                     audio_start_ms=500, audio_end_ms=3000))


def test_unauthorized_client_and_expired_session_never_append(scene):
    first, other, dictation_id, _ = scene
    for changes in ({"user_id": other}, {"client_id": "other-browser"}):
        with pytest.raises(live.LiveSessionError) as error:
            phrases.store_phrase(**dict(first, **changes))
        assert error.value.reason == live.NOT_HOLDER
    with pytest.raises(live.LiveSessionError) as error:
        phrases.store_phrase(**dict(first, now=NOW + timedelta(minutes=2)))
    assert error.value.reason == live.EXPIRED
    live.start(dictation_id, other, NOW + timedelta(minutes=2), client_id="new-browser")
    db.session.commit()
    with pytest.raises(live.LiveSessionError) as error:
        phrases.store_phrase(**dict(first, now=NOW + timedelta(minutes=2, seconds=1)))
    assert error.value.reason == live.ENDED_SESSION
    assert phrases.ClinicLivePhrase.query.count() == 0


def test_stale_text_and_invalid_sequence_leave_no_saved_phrase(scene):
    first, _, dictation_id, original_id = scene
    with pytest.raises(phrases.PhraseError, match="stale_sequence"):
        phrases.store_phrase(**dict(first, sequence=3))
    with pytest.raises(phrases.PhraseError, match="invalid_audio"):
        phrases.store_phrase(**dict(first, audio=b""))
    dictation = db.session.get(ClinicDictation, dictation_id)
    original = db.session.get(ClinicOriginal, original_id)
    revisions.add_asr_revision(dictation, original, "Korrigierte Phrase", "local-test")
    with pytest.raises(phrases.PhraseError, match="stale_version"):
        phrases.store_phrase(**first)
    assert phrases.ClinicLivePhrase.query.count() == 0


def test_stale_audio_revision_rejected(scene):
    first, _, dictation_id, _ = scene
    dictation = db.session.get(ClinicDictation, dictation_id)
    audio_revisions.apply_cut(dictation, 2000, 3000)
    with pytest.raises(phrases.PhraseError, match="stale_version"):
        phrases.store_phrase(**first)
    assert phrases.ClinicLivePhrase.query.count() == 0


def test_three_durable_phrases_after_reconnect(scene):
    first, _, _, _ = scene
    input_rows = [
        first,
        _next(first),
        _next(first, sequence=3, generation=3, event_id="event-3",
              audio_start_ms=2000, audio_end_ms=3000, now=NOW + timedelta(seconds=3)),
    ]
    receipts = [phrases.store_phrase(**values) for values in input_rows]
    db.session.remove()
    saved = (phrases.ClinicLivePhrase.query
             .filter_by(session_id=first["session_id"])
             .order_by(phrases.ClinicLivePhrase.sequence).all())
    assert [row.id for row in saved] == [receipt.phrase_id for receipt in receipts]
    assert [row.audio_start_ms for row in saved] == [0, 1000, 2000]
    assert [first["store"].file_path(row.audio_storage_key).read_bytes() for row in saved] == [
        values["audio"] for values in input_rows
    ]
    assert all(phrases.store_phrase(**values) == receipt
               for values, receipt in zip(input_rows, receipts))


def test_new_phrase_respects_existing_session_receipt(scene):
    first, _, _, _ = scene
    live.acknowledge(first["session_id"], first["user_id"], NOW + timedelta(seconds=1),
                     1, 500, first["transcript_revision_id"])
    db.session.commit()
    next_phrase = _next(first, generation=1, audio_start_ms=500, audio_end_ms=1500)
    assert phrases.store_phrase(**next_phrase).sequence == 2
    assert db.session.get(live.ClinicLiveSession, first["session_id"]).audio_offset_ms == 1500


def test_failed_commit_does_not_acknowledge_and_retry_adopts_audio(scene, monkeypatch):
    first, _, _, _ = scene

    def failed_commit():
        raise RuntimeError("synthetic database failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(db.session, "commit", failed_commit)
        with pytest.raises(RuntimeError, match="synthetic database failure"):
            phrases.store_phrase(**first)
    assert db.session.get(live.ClinicLiveSession, first["session_id"]).ack_seq == 0
    assert phrases.ClinicLivePhrase.query.count() == 0
    assert len(list(first["store"].root.glob("clinic/originals/v1/*"))) == 1
    receipt = phrases.store_phrase(**first)
    assert receipt.status == "server_saved"
    assert phrases.ClinicLivePhrase.query.count() == 1


def test_retry_requires_the_durable_audio_object(scene):
    first, _, _, _ = scene
    receipt = phrases.store_phrase(**first)
    path = first["store"].file_path(receipt.audio_storage_key)
    missing = path.with_name(path.name + ".unavailable")
    path.rename(missing)
    try:
        with pytest.raises(phrases.PhraseError, match="storage_missing"):
            phrases.store_phrase(**first)
    finally:
        missing.rename(path)
    assert phrases.store_phrase(**first) == receipt


def test_storage_failure_rolls_back_session_receipt(scene, monkeypatch):
    first, _, _, _ = scene

    def unavailable(*_args):
        raise OSError("synthetic disk error")

    with monkeypatch.context() as patcher:
        patcher.setattr(first["store"], "_publish", unavailable)
        with pytest.raises(OSError, match="synthetic disk error"):
            phrases.store_phrase(**first)
    assert phrases.ClinicLivePhrase.query.count() == 0
    assert db.session.get(live.ClinicLiveSession, first["session_id"]).ack_seq == 0
    assert not list(first["store"].root.glob("clinic/originals/v1/.partial-*"))
    assert phrases.store_phrase(**first).status == "server_saved"
