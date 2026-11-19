"""Synthetic reconnect: durable position, tail replay, stale rejection, takeover."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import audio_revisions, live_phrases as phrases, live_reconnect as rc
from src.clinic import live_session as live, revisions
from src.clinic.ingest import IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def scene(tmp_path):
    with app.app_context():
        live.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        phrases.create_tables()
        suffix = uuid.uuid4().hex[:10]
        owner = User(username="rc" + suffix, email=suffix + "@example.invalid", password="x")
        other = User(username="rco" + suffix, email="o" + suffix + "@example.invalid", password="x")
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
        audio_revision = audio_revisions.init_audio_revision(dictation, original, 5000)
        transcript = revisions.add_asr_revision(dictation, original, "Eine Phrase", "local-test")
        session = live.start(dictation.id, owner.id, NOW, client_id="tab-a")
        first = dict(store=IngestService(tmp_path), dictation_id=dictation.id,
                     session_id=session.id, user_id=owner.id, client_id="tab-a",
                     now=NOW + timedelta(seconds=1), generation=1, sequence=1,
                     event_id="event-1", audio_start_ms=0, audio_end_ms=1000,
                     audio=b"synthetic-audio-1", audio_revision_id=audio_revision.id,
                     transcript_revision_id=transcript.id,
                     text_hash=transcript.normalised_text_hash)
        db.session.commit()
        yield first, owner.id, other.id, dictation.id
        db.session.rollback()
        db.session.remove()
        phrases.ClinicLivePhrase.__table__.drop(db.engine, checkfirst=True)


def _phrase(first, n):
    return dict(first, sequence=n, event_id=f"event-{n}", audio_start_ms=(n - 1) * 1000,
                audio_end_ms=n * 1000, audio=b"synthetic-audio-%d" % n,
                now=NOW + timedelta(seconds=n))


def _store(first, count):
    for n in range(1, count + 1):
        phrases.store_phrase(**_phrase(first, n))


def test_returns_last_committed_position_and_missing_tail_in_order(scene):
    first, user, _, dictation_id = scene
    _store(first, 3)
    state = rc.reconnect(dictation_id, user, LATER, "tab-a", 1, 1)
    db.session.commit()
    assert (state.sequence, state.audio_offset_ms, state.text_ref) == (
        3, 3000, first["transcript_revision_id"])
    assert [p.sequence for p in state.tail] == [2, 3]
    assert state.tail[0].event_id == "event-2"
    caught_up = rc.reconnect(dictation_id, user, LATER + timedelta(seconds=1), "tab-a", 1, 3)
    assert caught_up.tail == () and caught_up.sequence == 3


def test_disconnect_before_commit_loses_nothing_and_retry_does_not_duplicate(scene):
    first, user, _, dictation_id = scene
    _store(first, 1)
    state = rc.reconnect(dictation_id, user, LATER, "tab-a", 1, 1)
    db.session.commit()
    assert state.sequence == 1 and state.tail == ()
    # The un-acked phrase 2 is resent by the client; resending phrase 1 is a no-op.
    receipt = phrases.store_phrase(**dict(_phrase(first, 2), now=LATER + timedelta(seconds=1)))
    assert phrases.store_phrase(**dict(_phrase(first, 1), now=LATER + timedelta(seconds=2))).sequence == 1
    assert receipt.sequence == 2
    assert phrases.ClinicLivePhrase.query.count() == 2


def test_disconnect_after_commit_replays_phrase_client_never_saw_ack_for(scene):
    first, user, _, dictation_id = scene
    _store(first, 2)  # server committed 2, client only saw ack 1 before the break
    state = rc.reconnect(dictation_id, user, LATER, "tab-a", 1, 1)
    assert [p.sequence for p in state.tail] == [2]
    assert phrases.ClinicLivePhrase.query.count() == 2


def test_process_restart_reads_only_durable_state(scene):
    first, user, _, dictation_id = scene
    _store(first, 2)
    db.session.remove()
    state = rc.reconnect(dictation_id, user, LATER, "tab-a", 1, 0)
    assert [p.sequence for p in state.tail] == [1, 2]


def test_tab_takeover_fences_old_tab(scene):
    first, user, _, dictation_id = scene
    _store(first, 1)
    state = rc.reconnect(dictation_id, user, LATER, "tab-b", 1, 1)
    db.session.commit()
    assert db.session.get(live.ClinicLiveSession, state.session_id).client_id == "tab-b"
    with pytest.raises(live.LiveSessionError) as error:
        phrases.store_phrase(**dict(_phrase(first, 2), now=LATER + timedelta(seconds=1)))
    assert error.value.reason == live.NOT_HOLDER
    new_tab = dict(_phrase(first, 2), client_id="tab-b", now=LATER + timedelta(seconds=1))
    assert phrases.store_phrase(**new_tab).sequence == 2
    assert phrases.ClinicLivePhrase.query.count() == 2


def test_stale_generation_rejected_and_session_unchanged(scene):
    first, user, _, dictation_id = scene
    _store(first, 1)
    phrases.store_phrase(**dict(_phrase(first, 2), generation=2))
    with pytest.raises(rc.ReconnectError) as error:
        rc.reconnect(dictation_id, user, LATER, "tab-b", 1, 2)
    assert error.value.reason == rc.STALE_GENERATION
    assert db.session.get(live.ClinicLiveSession, first["session_id"]).client_id == "tab-a"
    assert rc.reconnect(dictation_id, user, LATER, "tab-a", 2, 2).generation == 2


def test_ack_beyond_durable_storage_rejected(scene):
    first, user, _, dictation_id = scene
    _store(first, 1)
    with pytest.raises(rc.ReconnectError) as error:
        rc.reconnect(dictation_id, user, LATER, "tab-b", 1, 2)
    assert error.value.reason == rc.ACK_BEYOND_DURABLE
    assert db.session.get(live.ClinicLiveSession, first["session_id"]).client_id == "tab-a"


def test_other_user_and_missing_session_and_bad_input(scene):
    first, user, other, dictation_id = scene
    with pytest.raises(live.LiveSessionError) as error:
        rc.reconnect(dictation_id, other, NOW + timedelta(seconds=2), "x", 1, 0)
    assert error.value.reason == live.ACTIVE_SESSION
    with pytest.raises(live.LiveSessionError) as error:
        rc.reconnect(dictation_id, other, LATER, "x", 1, 0)
    assert error.value.reason == live.NO_SESSION
    live.end(first["session_id"], user, LATER)
    db.session.commit()
    with pytest.raises(live.LiveSessionError) as error:
        rc.reconnect(dictation_id, user, LATER, "tab-a", 1, 0)
    assert error.value.reason == live.NO_SESSION
    for bad in ({"client_generation": 0}, {"client_ack_seq": -1}, {"client_ack_seq": True}):
        with pytest.raises(ValueError):
            rc.reconnect(dictation_id, user, LATER, "tab-a", **bad)
