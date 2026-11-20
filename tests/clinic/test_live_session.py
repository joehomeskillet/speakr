"""Service rules for the clinic live dictation session and speech lock (P06).

Covers the single-open-session invariant, the speech-lock conflict and its
expiry, and recovery from the last autosave receipt after an interruption.
Real database, no mocks. These tests do not cover streaming, the editor UI,
or an HTTP 409 response; those belong to later work packages.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import live_session as ls
from src.clinic.live_session import ClinicLiveSession, LiveSessionError
from src.clinic.models import ClinicDictation
from src.models import Recording, User

T0 = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)


def _sec(n):
    return T0 + timedelta(seconds=n)


@pytest.fixture
def live_db():
    """Use the session-owned clinic schema and enforce foreign keys on SQLite."""
    with app.app_context():
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        db.session.remove()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.engine.dispose()


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="lv" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _dictation(author):
    recording = Recording(user_id=author.id, title="live", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(row)
    db.session.flush()
    return row


def _setup():
    a, b = _user(), _user()
    return a, b, _dictation(a)


def test_only_one_open_session_per_dictation(live_db):
    a, b, dictation = _setup()
    first = ls.start(dictation.id, a.id, T0, client_id="browser-1")
    assert first.state == ls.RECORDING
    assert first.active_key == dictation.id
    assert first.lock_expires_at == T0 + timedelta(seconds=ls.DEFAULT_LOCK_TIMEOUT_SECONDS)

    for who in (a, b):
        with pytest.raises(LiveSessionError) as info:
            ls.start(dictation.id, who.id, _sec(5))
        assert info.value.reason == ls.ACTIVE_SESSION
        assert info.value.session.id == first.id

    # a second dictation is independent
    other = _dictation(a)
    assert ls.start(other.id, b.id, _sec(5)).state == ls.RECORDING

    ended = ls.end(first.id, a.id, _sec(10))
    assert ended.state == ls.ENDED and ended.active_key is None
    assert ended.end_reason == ls.END_FINISHED
    again = ls.start(dictation.id, b.id, _sec(11))
    assert again.id != first.id


def test_database_rejects_a_second_open_row(live_db):
    """The invariant is a table constraint, not only service logic."""
    from sqlalchemy.exc import IntegrityError

    a, _b, dictation = _setup()
    ls.start(dictation.id, a.id, T0)
    rogue = ClinicLiveSession(
        dictation_id=dictation.id, author_user_id=a.id, state=ls.RECORDING,
        active_key=dictation.id, started_at=T0, last_activity_at=T0, lock_expires_at=T0,
    )
    db.session.add(rogue)
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_speech_lock_blocks_others_and_expires_on_inactivity(live_db):
    a, b, dictation = _setup()
    timeout = 30
    session = ls.start(dictation.id, a.id, T0, timeout=timeout)

    ls.assert_edit_allowed(dictation.id, a.id, _sec(1))  # holder may edit
    with pytest.raises(LiveSessionError) as info:
        ls.assert_edit_allowed(dictation.id, b.id, _sec(1))
    assert info.value.reason == ls.SPEECH_LOCK_HELD

    # heartbeat slides the lock: still held at 45 s because of the beat at 20 s
    ls.heartbeat(session.id, a.id, _sec(20), timeout=timeout)
    with pytest.raises(LiveSessionError):
        ls.assert_edit_allowed(dictation.id, b.id, _sec(45))
    assert ls.active_session(dictation.id, _sec(45)).id == session.id

    # pause keeps the lock
    ls.pause(session.id, a.id, _sec(46), timeout=timeout)
    with pytest.raises(LiveSessionError):
        ls.assert_edit_allowed(dictation.id, b.id, _sec(60))

    # no activity: expired exactly at last activity + timeout
    ls.assert_edit_allowed(dictation.id, b.id, _sec(46 + timeout))
    assert ls.active_session(dictation.id, _sec(46 + timeout)) is None

    # the lapsed holder cannot extend it any more
    with pytest.raises(LiveSessionError) as late:
        ls.heartbeat(session.id, a.id, _sec(46 + timeout))
    assert late.value.reason == ls.EXPIRED
    # and a foreign user cannot touch it at all
    with pytest.raises(LiveSessionError) as foreign:
        ls.heartbeat(session.id, b.id, _sec(47))
    assert foreign.value.reason == ls.NOT_HOLDER


def test_takeover_after_expiry_ends_old_session(live_db):
    a, b, dictation = _setup()
    old = ls.start(dictation.id, a.id, T0, timeout=30)
    new = ls.start(dictation.id, b.id, _sec(31), timeout=30)
    db.session.refresh(old)
    assert old.state == ls.ENDED and old.end_reason == ls.END_EXPIRED
    assert new.state == ls.RECORDING and new.author_user_id == b.id

    # the old holder is now locked out of its own session and of recovery
    with pytest.raises(LiveSessionError) as stale:
        ls.acknowledge(old.id, a.id, _sec(32), seq=1, audio_offset_ms=100)
    assert stale.value.reason == ls.ENDED_SESSION
    with pytest.raises(LiveSessionError) as lost:
        ls.recover(dictation.id, a.id, _sec(33))
    assert lost.value.reason == ls.ACTIVE_SESSION


def test_receipts_are_monotonic_and_idempotent(live_db):
    a, _b, dictation = _setup()
    session = ls.start(dictation.id, a.id, T0)
    ls.acknowledge(session.id, a.id, _sec(5), seq=1, audio_offset_ms=5000, text_ref="rev-1")
    ls.acknowledge(session.id, a.id, _sec(6), seq=1, audio_offset_ms=5000, text_ref="rev-1")
    ls.acknowledge(session.id, a.id, _sec(10), seq=2, audio_offset_ms=10000, text_ref="rev-2")
    for bad in (
        dict(seq=1, audio_offset_ms=12000, text_ref="rev-x"),   # seq goes back
        dict(seq=2, audio_offset_ms=10000, text_ref="rev-y"),   # other payload, same seq
        dict(seq=3, audio_offset_ms=9000, text_ref="rev-3"),    # offset goes back
    ):
        with pytest.raises(LiveSessionError) as info:
            ls.acknowledge(session.id, a.id, _sec(11), **bad)
        assert info.value.reason == ls.STALE_RECEIPT
    with pytest.raises(ValueError):
        ls.acknowledge(session.id, a.id, _sec(11), seq=-1, audio_offset_ms=0)
    db.session.refresh(session)
    assert (session.ack_seq, session.audio_offset_ms, session.text_ref) == (2, 10000, "rev-2")


def test_recovery_after_interruption_resumes_at_last_receipt(live_db):
    a, b, dictation = _setup()
    session = ls.start(dictation.id, a.id, T0, client_id="tab-1", timeout=30)
    ls.acknowledge(session.id, a.id, _sec(5), seq=1, audio_offset_ms=5000, text_ref="rev-1", timeout=30)
    ls.acknowledge(session.id, a.id, _sec(10), seq=2, audio_offset_ms=10000, text_ref="rev-2", timeout=30)
    session_id, dict_id, a_id, b_id = session.id, dictation.id, a.id, b.id
    db.session.commit()
    db.session.remove()  # the client and server context vanish: crash

    # case 1: lock lapsed, nobody took over; session is still open
    other_edit_time = _sec(10 + 30)
    ls.assert_edit_allowed(dict_id, b_id, other_edit_time)  # lock is gone
    resumed, point = ls.recover(dict_id, a_id, _sec(60), client_id="tab-2", timeout=30)
    assert resumed.id == session_id
    assert resumed.client_id == "tab-2"
    assert resumed.state == ls.PAUSED
    assert point == ls.ResumePoint(session_id, 2, 10000, "rev-2")
    with pytest.raises(LiveSessionError):
        ls.assert_edit_allowed(dict_id, b_id, _sec(61))  # lock is back

    ls.continue_recording(session_id, a_id, _sec(62), timeout=30)
    ls.acknowledge(session_id, a_id, _sec(65), seq=3, audio_offset_ms=15000, text_ref="rev-3")
    db.session.commit()
    db.session.remove()

    # case 2: another editor's start closed it as expired, then that one finished;
    # recovery is refused because a newer session exists in history
    b_session = ls.start(dict_id, b_id, _sec(200))
    ls.end(b_session.id, b_id, _sec(210))
    with pytest.raises(LiveSessionError) as gone:
        ls.recover(dict_id, a_id, _sec(220))
    assert gone.value.reason == ls.NO_SESSION


def test_recover_reopens_a_session_ended_by_expiry_when_nobody_took_over(live_db):
    a, _b, dictation = _setup()
    session = ls.start(dictation.id, a.id, T0, timeout=30)
    ls.acknowledge(session.id, a.id, _sec(4), seq=1, audio_offset_ms=4000, text_ref="rev-1", timeout=30)
    # a janitor-style closing by expiry (what start() does to a lapsed session)
    ls._close(session, _sec(100), ls.END_EXPIRED)
    db.session.commit()

    resumed, point = ls.recover(dictation.id, a.id, _sec(101), timeout=30)
    assert resumed.id == session.id
    assert resumed.state == ls.PAUSED and resumed.active_key == dictation.id
    assert resumed.end_reason is None and resumed.ended_at is None
    assert (point.ack_seq, point.audio_offset_ms, point.text_ref) == (1, 4000, "rev-1")


def test_deliberately_ended_session_is_not_recovered(live_db):
    a, _b, dictation = _setup()
    session = ls.start(dictation.id, a.id, T0)
    ls.end(session.id, a.id, _sec(5))
    ls.end(session.id, a.id, _sec(6))  # idempotent
    with pytest.raises(LiveSessionError) as info:
        ls.recover(dictation.id, a.id, _sec(7))
    assert info.value.reason == ls.NO_SESSION


def test_unknown_dictation_and_naive_clock(live_db):
    a, _b, dictation = _setup()
    with pytest.raises(LiveSessionError) as info:
        ls.start(str(uuid.uuid4()), a.id, T0)
    assert info.value.reason == ls.NOT_FOUND
    with pytest.raises(ValueError):
        ls.start(dictation.id, a.id, datetime(2026, 11, 18, 8, 0))
    with pytest.raises(ValueError):
        ls.lock_timeout(0)
