"""Workflow states, work-list groups, priority import and deadlines.

The database is a SQLite file under tmp_path. Nothing here mocks the engine.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.clinic import workflow as wf

ZONE = ZoneInfo("Europe/Zurich")
STATUSES = (
    wf.TASK,
    wf.DRAFT,
    wf.PREP_OFFLINE,
    wf.PREP_ONLINE,
    wf.TO_WRITE,
    wf.TO_CORRECT,
    wf.IN_PROGRESS,
    wf.TO_ACCEPT,
    wf.ACCEPTED,
    wf.DONE,
    wf.TO_ARCHIVE,
    wf.TO_DELETE,
)
TRIGGERS = (
    "plan",
    "create",
    "send_offline",
    "send_online",
    "skip_recognition",
    "recognition_done",
    "open",
    "close",
    "complete",
    "accept",
    "reject",
    "reopen",
    "report_archive",
    "report_delete",
)
_QUEUE = (wf.TO_WRITE, wf.TO_CORRECT)
_PREP = (wf.PREP_OFFLINE, wf.PREP_ONLINE)

# Pairs whose target does not depend on the row.
_FIXED = {
    (wf.DRAFT, "send_offline"): wf.PREP_OFFLINE,
    (wf.DRAFT, "send_online"): wf.PREP_ONLINE,
    (wf.TO_ACCEPT, "accept"): wf.ACCEPTED,
    (wf.TO_ACCEPT, "reject"): wf.TO_CORRECT,
    (wf.ACCEPTED, "complete"): wf.DONE,
    (wf.DONE, "reopen"): wf.TO_CORRECT,
}
for _prep in _PREP:
    _FIXED[(_prep, "skip_recognition")] = wf.TO_WRITE
    _FIXED[(_prep, "recognition_done")] = wf.TO_CORRECT
for _queue in _QUEUE:
    _FIXED[(_queue, "open")] = wf.IN_PROGRESS


def expected(status, trigger, acceptance, resume):
    """Oracle for one pair. ``None`` means the transition is forbidden."""
    if trigger == "close" and status == wf.IN_PROGRESS:
        return resume if resume in _QUEUE else None
    if trigger == "complete" and status in (*_QUEUE, wf.IN_PROGRESS):
        return wf.TO_ACCEPT if acceptance else wf.DONE
    if trigger == "report_archive" and status != wf.TO_ARCHIVE:
        return wf.TO_ARCHIVE
    if trigger == "report_delete" and status != wf.TO_DELETE:
        return wf.TO_DELETE
    return _FIXED.get((status, trigger))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "workflow.sqlite"
    engine = create_engine("sqlite:///" + path.as_posix())
    wf.create_tables(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session, engine, path
    finally:
        session.close()
        engine.dispose()


def _start(session, trigger="create", **extra):
    kwargs = dict(author_user_id=1, pool_id="pool-a", audio_seconds=0)
    kwargs.update(extra)
    return wf.start(session, trigger, **kwargs)


def arrive(session, status, *, acceptance=False):
    """Walk a legal path to ``status``. Open dictations resume to Zu schreiben."""
    if status == wf.TASK:
        return _start(session, "plan", acceptance_required=acceptance)
    if status == wf.DRAFT:
        return _start(session, "create", acceptance_required=acceptance)
    if status == wf.PREP_OFFLINE:
        row = arrive(session, wf.DRAFT, acceptance=acceptance)
        return wf.transition(session, row.id, "send_offline")
    if status == wf.PREP_ONLINE:
        row = arrive(session, wf.DRAFT, acceptance=acceptance)
        return wf.transition(session, row.id, "send_online")
    if status == wf.TO_WRITE:
        row = arrive(session, wf.PREP_OFFLINE, acceptance=acceptance)
        return wf.transition(session, row.id, "skip_recognition")
    if status == wf.TO_CORRECT:
        row = arrive(session, wf.PREP_OFFLINE, acceptance=acceptance)
        return wf.transition(session, row.id, "recognition_done")
    if status == wf.IN_PROGRESS:
        row = arrive(session, wf.TO_WRITE, acceptance=acceptance)
        return wf.transition(session, row.id, "open")
    if status == wf.TO_ACCEPT:
        row = arrive(session, wf.TO_WRITE, acceptance=True)
        return wf.transition(session, row.id, "complete")
    if status == wf.ACCEPTED:
        row = arrive(session, wf.TO_ACCEPT)
        return wf.transition(session, row.id, "accept")
    if status == wf.DONE:
        row = arrive(session, wf.TO_WRITE, acceptance=False)
        return wf.transition(session, row.id, "complete")
    if status == wf.TO_ARCHIVE:
        row = arrive(session, wf.TASK)
        return wf.transition(session, row.id, "report_archive")
    if status == wf.TO_DELETE:
        row = arrive(session, wf.TASK)
        return wf.transition(session, row.id, "report_delete")
    raise AssertionError(status)


def _reread(engine, ident):
    other = sessionmaker(bind=engine)()
    try:
        return other.get(wf.ClinicWorkflowDictation, ident)
    finally:
        other.close()


def test_sqlite_file_is_a_real_database(db):
    _session, _engine, path = db
    assert path.is_file()
    assert path.read_bytes()[:16] == b"SQLite format 3\x00"


def test_transition_table_allows_documented_moves_and_rejects_the_rest(db):
    session, engine, _path = db
    spec_keys = {(None, "plan"), (None, "create")}
    for status in STATUSES:
        for trigger in TRIGGERS:
            if expected(status, trigger, False, wf.TO_WRITE) is not None:
                spec_keys.add((status, trigger))
            if expected(status, trigger, True, wf.TO_CORRECT) is not None:
                spec_keys.add((status, trigger))
    assert set(wf.TRANSITIONS) == spec_keys
    assert set(wf.TRIGGERS) == set(TRIGGERS)
    assert wf.STATUS_ORDER == STATUSES

    for status in STATUSES:
        for acceptance in (False, True):
            for trigger in TRIGGERS:
                target = expected(status, trigger, acceptance, wf.TO_WRITE)
                row = arrive(session, status, acceptance=acceptance)
                session.commit()
                if target is None:
                    with pytest.raises(wf.WorkflowTransitionError) as raised:
                        wf.transition(session, row.id, trigger)
                    assert raised.value.status == status
                    assert raised.value.trigger == trigger
                    assert raised.value.code == "illegal_transition"
                    session.rollback()
                    stored = _reread(engine, row.id)
                    assert stored.status == status
                    continue
                moved = wf.transition(session, row.id, trigger)
                session.commit()
                stored = _reread(engine, moved.id)
                assert stored.status == target, (status, trigger, acceptance, stored.status)
                if target == wf.IN_PROGRESS:
                    assert stored.resume_status == status
                else:
                    assert stored.resume_status is None


def test_close_restores_the_correction_queue(db):
    session, engine, _path = db
    row = arrive(session, wf.TO_CORRECT)
    wf.transition(session, row.id, "open")
    session.commit()
    wf.transition(session, row.id, "close")
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.status == wf.TO_CORRECT
    assert stored.resume_status is None


def test_offline_and_online_preparation_follow_recognition(db):
    """Device send waits offline, then successful recognition is Zu korrigieren."""
    session, engine, _path = db
    offline = arrive(session, wf.PREP_OFFLINE)
    online = arrive(session, wf.PREP_ONLINE)
    assert offline.status == wf.PREP_OFFLINE
    assert wf.LABELS[offline.status] == "In Vorbereitung [Offline]"
    assert wf.LABELS[online.status] == "In Vorbereitung [Online]"
    wf.transition(session, offline.id, "recognition_done")
    wf.transition(session, online.id, "skip_recognition")
    session.commit()
    assert _reread(engine, offline.id).status == wf.TO_CORRECT
    assert _reread(engine, online.id).status == wf.TO_WRITE


def test_worklist_groups_by_status_with_count_and_audio_length(db):
    session, engine, _path = db
    pool = "pool-a"
    other = "pool-b"

    def add(author, pool_id, status, seconds, assignee=None):
        # One row per dictation. Walking the transitions would leave the
        # earlier states in the same pool and inflate the groups.
        row = _start(
            session,
            author_user_id=author,
            pool_id=pool_id,
            audio_seconds=seconds,
            assignee_user_id=assignee,
        )
        row.status = status
        return row

    add(1, pool, wf.PREP_OFFLINE, 9)
    add(1, pool, wf.TO_WRITE, 4, assignee=99)
    add(1, pool, wf.TO_WRITE, 6, assignee=7)
    add(1, pool, wf.TO_CORRECT, 10)
    add(1, pool, wf.TO_CORRECT, 25)
    add(1, pool, wf.DONE, 8)
    add(2, pool, wf.TO_CORRECT, 100)
    add(1, other, wf.TO_CORRECT, 3)
    session.commit()

    author = wf.worklist(session, role=wf.ROLE_AUTHOR, user_id=1, pool_id=pool)
    assert [(g.status, g.count, g.total_audio_seconds, g.finished, g.label) for g in author] == [
        (wf.PREP_OFFLINE, 1, 9, False, "In Vorbereitung [Offline]"),
        (wf.TO_WRITE, 2, 10, False, "Zu schreiben"),
        (wf.TO_CORRECT, 2, 35, False, "Zu korrigieren"),
        (wf.DONE, 1, 8, True, "Fertiggestellt"),
    ]
    typist = wf.worklist(session, role=wf.ROLE_TYPIST, user_id=7, pool_id=pool)
    assert [(g.status, g.count, g.total_audio_seconds) for g in typist] == [
        (wf.PREP_OFFLINE, 1, 9),
        (wf.TO_WRITE, 1, 6),
        (wf.TO_CORRECT, 3, 135),
        (wf.DONE, 1, 8),
    ]
    assert wf.worklist(session, role=wf.ROLE_AUTHOR, user_id=2, pool_id=pool)[0].total_audio_seconds == 100
    other_pool = wf.worklist(session, role=wf.ROLE_TYPIST, user_id=7, pool_id=other)
    assert [(g.count, g.total_audio_seconds) for g in other_pool] == [(1, 3)]
    assert wf.worklist(session, role=wf.ROLE_AUTHOR, user_id=1, pool_id="missing") == []
    with pytest.raises(wf.WorkflowError) as raised:
        wf.worklist(session, role="admin", user_id=1, pool_id=pool)
    assert raised.value.code == "unknown_role"
    # The committed groups are visible to a second connection.
    assert _reread(engine, author[0].dictation_ids[0]).audio_seconds == 9


def test_unknown_priority_becomes_normal_and_sets_the_flag(db):
    session, engine, _path = db
    known = {
        None: (wf.PRIORITY_NORMAL, False),
        0: (wf.PRIORITY_WHEN_CONVENIENT, False),
        1: (wf.PRIORITY_NORMAL, False),
        128: (wf.PRIORITY_URGENT, False),
        "0": (0, False),
        "1": (1, False),
        "128": (128, False),
        "Idle": (0, False),
        "  normal ": (1, False),
        "UrGeNt": (128, False),
        "Bei Gelegenheit": (0, False),
        "bei  gelegenheit": (0, False),
        "Dringend": (128, False),
    }
    unknown = (2, "hoch", "Frist", True, 1.5, "", "128.0", -1)
    for raw, (code, flagged) in known.items():
        assert wf.normalize_priority(raw) == (code, flagged)
        row = _start(session, priority_raw=raw)
        session.commit()
        stored = _reread(engine, row.id)
        assert stored.priority == code
        assert stored.priority_unrecognized is flagged

    row = _start(session, priority_raw=64)
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.priority == wf.PRIORITY_NORMAL
    assert stored.priority_unrecognized is True
    wf.set_priority(session, row.id, "Dringend")
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.priority == 128
    assert stored.priority_unrecognized is False

    for raw in unknown:
        flagged = _start(session, priority_raw=raw)
        session.commit()
        stored = _reread(engine, flagged.id)
        assert stored.priority == wf.PRIORITY_NORMAL
        assert stored.priority_unrecognized is True


def test_deadline_without_a_clock_is_the_end_of_that_day(db):
    session, engine, _path = db
    row = _start(session)
    session.commit()
    with pytest.raises(wf.WorkflowError) as raised:
        wf.set_deadline(session, row.id, None, time(15, 0))
    assert raised.value.code == "deadline_date_required"
    session.rollback()
    assert _reread(engine, row.id).deadline_at is None

    winter = date(2025, 3, 7)
    summer = date(2025, 7, 7)
    wf.set_deadline(session, row.id, winter, None)
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.deadline_date == winter
    assert stored.deadline_time is None
    assert stored.deadline_at == datetime.combine(
        winter, time(23, 59, 59), tzinfo=ZONE
    ).astimezone(timezone.utc)

    wf.set_deadline(session, row.id, summer, None)
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.deadline_at == datetime.combine(
        summer, time(23, 59, 59), tzinfo=ZONE
    ).astimezone(timezone.utc)

    clock = time(15, 30)
    wf.set_deadline(session, row.id, winter, clock)
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.deadline_time == clock
    assert stored.deadline_at == datetime.combine(winter, clock, tzinfo=ZONE).astimezone(timezone.utc)
    assert stored.deadline_at != datetime.combine(
        winter, time(23, 59, 59), tzinfo=ZONE
    ).astimezone(timezone.utc)

    wf.set_deadline(session, row.id, None, None)
    session.commit()
    stored = _reread(engine, row.id)
    assert stored.deadline_date is None
    assert stored.deadline_time is None
    assert stored.deadline_at is None


def test_database_rejects_a_broken_priority_resume_or_deadline(db):
    session, _engine, _path = db
    row = arrive(session, wf.IN_PROGRESS)
    session.commit()
    row.priority = 5
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    row = session.get(wf.ClinicWorkflowDictation, row.id)
    row.resume_status = None
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    row = session.get(wf.ClinicWorkflowDictation, row.id)
    row.deadline_time = time(8, 0)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_start_rejects_unknown_origin_and_bad_fields(db):
    session, _engine, _path = db
    with pytest.raises(wf.WorkflowTransitionError) as raised:
        _start(session, "send_offline")
    assert raised.value.status is None
    assert raised.value.trigger == "send_offline"
    with pytest.raises(wf.WorkflowError) as raised:
        _start(session, pool_id="  ")
    assert raised.value.code == "invalid_pool"
    with pytest.raises(wf.WorkflowError) as raised:
        _start(session, audio_seconds=-1)
    assert raised.value.code == "invalid_audio_length"
    with pytest.raises(wf.WorkflowError) as raised:
        wf.transition(session, "missing", "open")
    assert raised.value.code == "not_found"
