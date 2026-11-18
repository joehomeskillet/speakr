"""Dictation statistics on a real SQLite file. No database mocks.

Lengths and counts are asserted against the rows this file inserts.
The window is half-open and period labels are Europe/Zurich calendar dates.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.app import app
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicReviewLease,
    ClinicTranscriptRevision,
)
from src.clinic.release import ClinicReleaseSupersession, ClinicWorkStateTransfer
from src.clinic.statistics import StatisticsError, StatisticsQuery, summarize
from src.database import db
from src.models import Recording, User

ZONE = ZoneInfo("Europe/Zurich")


def _at(year, month, day, hour=0, minute=0, second=0, microsecond=0):
    """UTC instant for a Europe/Zurich civil time."""
    local = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=ZONE)
    return local.astimezone(timezone.utc)


@pytest.fixture
def stats_db(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "statistics.db").as_posix())

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    with app.app_context():
        # Full schema: user and recording reference other tables, and SQLite
        # checks those names as soon as foreign keys are on.
        db.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


def _user(session, name):
    user = User(username=name, email=f"{name}@example.invalid", password="x")
    session.add(user)
    session.flush()
    return user


def _dictation(session, author, when, seconds):
    recording = Recording(
        user_id=author.id, title="d", status="PENDING", audio_duration_seconds=seconds,
    )
    session.add(recording)
    session.flush()
    row = ClinicDictation(
        recording_id=recording.id, author_user_id=author.id, created_at=when,
    )
    session.add(row)
    session.flush()
    return row


def _release(session, dictation, actor, when):
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="o/" + dictation.id, receipt="stored", origin_ref="test",
    )
    session.add(original)
    session.flush()
    audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
    transcript = ClinicTranscriptRevision(
        dictation_id=dictation.id, original_version_id=original.id, kind="ASR",
        model_ref="stub:1", body="text", normalised_text_hash="t" * 64,
    )
    context = ClinicContextRevision(dictation_id=dictation.id)
    session.add_all([audio, transcript, context])
    session.flush()
    release = ClinicRelease(
        dictation_id=dictation.id, audio_revision_id=audio.id,
        transcript_revision_id=transcript.id, context_revision_id=context.id,
        text_hash=transcript.normalised_text_hash, actor_user_id=actor.id,
        document_type="report", release_type="clinical",
        authorization_decision='{"allowed": true}', created_at=when,
    )
    session.add(release)
    session.flush()
    return release, original


def _follow(session, dictation, transcript_id, original_id):
    child = ClinicTranscriptRevision(
        dictation_id=dictation.id, original_version_id=original_id,
        parent_revision_id=transcript_id, kind="ASR", model_ref="stub:1",
        body="later", normalised_text_hash="u" * 64,
    )
    session.add(child)
    session.flush()


def _lease(session, dictation, holder, expires):
    session.add(ClinicReviewLease(
        dictation_id=dictation.id, holder_user_id=holder.id,
        expires_at=expires, generation=1,
    ))
    session.flush()


def _transfer(session, dictation, source, target, when):
    session.add(ClinicWorkStateTransfer(
        dictation_id=dictation.id, from_user_id=source.id, to_user_id=target.id,
        transferred_at=when,
    ))
    session.flush()


def _supersede(session, release, when):
    session.add(ClinicReleaseSupersession(
        release_id=release.id, reason="revision_changed", superseded_at=when,
    ))
    session.flush()


def _one(result, **keys):
    found = [
        bucket for bucket in result.buckets
        if all(getattr(bucket, field) == value for field, value in keys.items())
    ]
    assert len(found) == 1
    return found[0]


def test_groups_count_and_length_by_author_status_period_and_typist(stats_db):
    """Author × status × day, plus typist, with one unknown length."""
    author_a = _user(stats_db, "authora")
    author_b = _user(stats_db, "authorb")
    typist = _user(stats_db, "typist")
    start = _at(2026, 3, 2)
    end = _at(2026, 3, 5)
    # Two open dictations on 2 March, 10s and 30s, plus one more at the start instant.
    _dictation(stats_db, author_a, _at(2026, 3, 2, 8), 10)
    _dictation(stats_db, author_a, _at(2026, 3, 2, 18), 30)
    _dictation(stats_db, author_a, start, 5)
    held = _dictation(stats_db, author_a, _at(2026, 3, 2, 12), 20)
    _lease(stats_db, held, typist, _at(2026, 3, 11))
    done = _dictation(stats_db, author_a, _at(2026, 3, 3, 9), 40)
    _transfer(stats_db, done, author_a, typist, _at(2026, 3, 3, 10))
    _release(stats_db, done, author_a, _at(2026, 3, 3, 15))
    unknown = _dictation(stats_db, author_b, _at(2026, 3, 3, 11), None)
    _transfer(stats_db, unknown, author_b, typist, _at(2026, 3, 3, 12))
    _release(stats_db, unknown, author_b, _at(2026, 3, 3, 16))
    # Exactly at end: excluded. One hour later in Zurich: also excluded.
    _dictation(stats_db, author_a, end, 99)
    _dictation(stats_db, author_b, _at(2026, 3, 5, 0, 30), 1)
    stats_db.commit()

    query = StatisticsQuery(
        start=start, end=end, grain="day",
        group_by=("author", "status", "period"), now=_at(2026, 3, 10, 12),
    )
    result = summarize(stats_db, query)
    assert result.excluded_missing_timestamp == 0
    assert result.time_basis == "created"
    assert result.zone == "Europe/Zurich"
    assert result.window == "[start, end)"

    open_day = _one(
        result, author_user_id=author_a.id, status="open", period_start="2026-03-02",
    )
    assert (open_day.count, open_day.total_seconds, open_day.average_seconds) == (3, 45.0, 15.0)
    assert open_day.known_duration_count == 3 and open_day.incomplete is False

    review = _one(
        result, author_user_id=author_a.id, status="in_review", period_start="2026-03-02",
    )
    assert (review.count, review.total_seconds, review.average_seconds) == (1, 20.0, 20.0)

    released_a = _one(
        result, author_user_id=author_a.id, status="released", period_start="2026-03-03",
    )
    assert (released_a.count, released_a.total_seconds) == (1, 40.0)

    released_b = _one(
        result, author_user_id=author_b.id, status="released", period_start="2026-03-03",
    )
    assert released_b.count == 1
    assert released_b.total_seconds is None
    assert released_b.average_seconds is None
    assert released_b.known_duration_count == 0
    assert released_b.incomplete is True

    by_typist = summarize(stats_db, StatisticsQuery(
        start=start, end=end, group_by=("typist",), now=_at(2026, 3, 10, 12),
    ))
    assigned = _one(by_typist, typist_user_id=typist.id)
    # 20 + 40 known, one unknown. Average is 30, not 20.
    assert (assigned.count, assigned.known_duration_count, assigned.total_seconds) == (3, 2, 60.0)
    assert assigned.average_seconds == 30.0 and assigned.incomplete is True
    unassigned = _one(by_typist, typist_user_id=None)
    assert (unassigned.count, unassigned.total_seconds, unassigned.average_seconds) == (3, 45.0, 15.0)

    by_day = summarize(stats_db, StatisticsQuery(
        start=start, end=end, grain="day", group_by=("period",), now=_at(2026, 3, 10, 12),
    ))
    assert [bucket.period_start for bucket in by_day.buckets] == [
        "2026-03-02", "2026-03-03", "2026-03-04",
    ]
    assert (by_day.buckets[0].count, by_day.buckets[0].total_seconds) == (4, 65.0)
    assert by_day.buckets[0].average_seconds == 16.25
    # 40 known plus one missing length: average stays 40.
    assert by_day.buckets[1].count == 2
    assert by_day.buckets[1].total_seconds == 40.0
    assert by_day.buckets[1].average_seconds == 40.0
    assert by_day.buckets[1].incomplete is True
    assert by_day.buckets[2].count == 0
    assert by_day.buckets[2].total_seconds == 0.0
    assert by_day.buckets[2].incomplete is False

    body = result.as_dict()
    json.dumps(body)
    assert body["group_by"] == ["author", "status", "period"]
    assert body["start"].endswith("+00:00")
    assert "author_user_id" in body["buckets"][0]
    assert "typist_user_id" not in body["buckets"][0]


def test_window_is_half_open_for_creation_and_completion(stats_db):
    author = _user(stats_db, "boundary")
    start = _at(2026, 3, 2)
    end = _at(2026, 3, 3)
    _dictation(stats_db, author, start, 10)  # included on created
    _dictation(stats_db, author, end, 99)  # excluded on created
    _dictation(stats_db, author, end - timedelta(microseconds=1), 7)  # included
    early = _dictation(stats_db, author, _at(2026, 3, 1, 8), 3)
    _release(stats_db, early, author, start)  # completion on the start instant
    late = _dictation(stats_db, author, start, 4)
    _release(stats_db, late, author, end)  # completion exactly at end
    stats_db.commit()

    created = summarize(stats_db, StatisticsQuery(start=start, end=end, now=end))
    assert created.buckets[0].count == 3
    assert created.buckets[0].total_seconds == 21.0
    assert created.excluded_missing_timestamp == 0

    completed = summarize(stats_db, StatisticsQuery(
        time_basis="completed", start=start, end=end, now=end,
    ))
    assert completed.buckets[0].count == 1
    assert completed.buckets[0].total_seconds == 3.0
    # Three dictations have no active release. The one completed at `end` has
    # a timestamp and is outside the window, so it is not "missing".
    assert completed.excluded_missing_timestamp == 3


def test_empty_periods_and_empty_total(stats_db):
    author = _user(stats_db, "empty")
    _dictation(stats_db, author, _at(2026, 3, 3, 9), 8)
    _dictation(stats_db, author, _at(2026, 6, 1), -1)  # unusable, outside the March window
    _dictation(stats_db, author, _at(2026, 6, 2), None)
    stats_db.commit()

    days = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 3, 2), end=_at(2026, 3, 5), grain="day", group_by=("period",),
        now=_at(2026, 3, 10),
    ))
    assert [(b.period_start, b.count, b.total_seconds) for b in days.buckets] == [
        ("2026-03-02", 0, 0.0),
        ("2026-03-03", 1, 8.0),
        ("2026-03-04", 0, 0.0),
    ]

    quiet = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 4, 1), end=_at(2026, 4, 3), grain="day", group_by=("period",),
        now=_at(2026, 5, 1),
    ))
    assert [b.count for b in quiet.buckets] == [0, 0]
    assert quiet.excluded_missing_timestamp == 0

    weeks = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 3, 2), end=_at(2026, 3, 16), grain="week", group_by=("period",),
        now=_at(2026, 3, 20),
    ))
    assert [b.period_start for b in weeks.buckets] == ["2026-03-02", "2026-03-09"]
    assert weeks.buckets[0].count == 1 and weeks.buckets[1].count == 0

    months = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 1, 15), end=_at(2026, 3, 1), grain="month", group_by=("period",),
        now=_at(2026, 4, 1),
    ))
    assert [b.period_start for b in months.buckets] == ["2026-01-01", "2026-02-01"]
    assert [b.count for b in months.buckets] == [0, 0]

    nothing = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 4, 1), end=_at(2026, 4, 2), now=_at(2026, 5, 1),
    ))
    assert nothing.buckets[0].count == 0
    assert nothing.buckets[0].total_seconds == 0.0
    assert nothing.as_dict()["buckets"] == [{
        "count": 0,
        "total_seconds": 0.0,
        "average_seconds": None,
        "known_duration_count": 0,
        "incomplete": False,
    }]

    bad = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 6, 1), end=_at(2026, 6, 3), now=_at(2026, 6, 4),
    ))
    assert bad.buckets[0].count == 2
    assert bad.buckets[0].total_seconds is None
    assert bad.buckets[0].average_seconds is None
    assert bad.buckets[0].incomplete is True


def test_period_buckets_use_zurich(stats_db):
    author = _user(stats_db, "zurich")
    # 22:30 UTC is still Sunday in Zurich; 23:30 UTC is already Monday.
    _dictation(stats_db, author, datetime(2026, 3, 1, 22, 30, tzinfo=timezone.utc), 1)
    _dictation(stats_db, author, datetime(2026, 3, 1, 23, 30, tzinfo=timezone.utc), 1)
    # 22:00 UTC is 31 January in Zurich; 23:30 UTC is 1 February.
    _dictation(stats_db, author, datetime(2026, 1, 31, 22, 0, tzinfo=timezone.utc), 1)
    _dictation(stats_db, author, datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc), 1)
    stats_db.commit()
    now = _at(2026, 4, 1)

    days = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 3, 1), end=_at(2026, 3, 3), grain="day", group_by=("period",), now=now,
    ))
    assert [(b.period_start, b.count) for b in days.buckets] == [
        ("2026-03-01", 1), ("2026-03-02", 1),
    ]

    weeks = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 2, 23), end=_at(2026, 3, 9), grain="week", group_by=("period",), now=now,
    ))
    assert [(b.period_start, b.count) for b in weeks.buckets] == [
        ("2026-02-23", 1), ("2026-03-02", 1),
    ]

    months = summarize(stats_db, StatisticsQuery(
        start=_at(2026, 1, 1), end=_at(2026, 3, 1), grain="month", group_by=("period",), now=now,
    ))
    assert [(b.period_start, b.count) for b in months.buckets] == [
        ("2026-01-01", 1), ("2026-02-01", 1),
    ]


def test_status_and_typist_follow_lease_transfer_and_active_release(stats_db):
    author = _user(stats_db, "doctor")
    holder = _user(stats_db, "holder")
    holder_2 = _user(stats_db, "holder2")
    typist = _user(stats_db, "writer")
    now = _at(2026, 3, 10, 12)

    _dictation(stats_db, author, _at(2026, 3, 2, 9), 10)
    expired = _dictation(stats_db, author, _at(2026, 3, 2, 10), 10)
    _lease(stats_db, expired, holder, now)  # expiry equal to now is over
    live = _dictation(stats_db, author, _at(2026, 3, 2, 11), 10)
    _lease(stats_db, live, holder, now + timedelta(hours=1))
    handed = _dictation(stats_db, author, _at(2026, 3, 3, 9), 10)
    _lease(stats_db, handed, holder, now - timedelta(hours=1))
    _transfer(stats_db, handed, author, typist, _at(2026, 3, 3, 8))
    taken = _dictation(stats_db, author, _at(2026, 3, 3, 10), 10)
    _transfer(stats_db, taken, author, typist, _at(2026, 3, 3, 8))
    _lease(stats_db, taken, holder_2, now + timedelta(hours=1))

    finished = _dictation(stats_db, author, _at(2026, 3, 4, 9), 10)
    _transfer(stats_db, finished, author, typist, _at(2026, 3, 4, 8))
    _release(stats_db, finished, author, _at(2026, 3, 4, 10))

    stale = _dictation(stats_db, author, _at(2026, 3, 5, 9), 10)
    release, original = _release(stats_db, stale, author, _at(2026, 3, 5, 10))
    transcript_id = stats_db.query(ClinicTranscriptRevision.id).filter_by(
        dictation_id=stale.id,
    ).scalar()
    _follow(stats_db, stale, transcript_id, original.id)

    superseded = _dictation(stats_db, author, _at(2026, 3, 6, 9), 10)
    old = _release(stats_db, superseded, author, _at(2026, 3, 6, 10))[0]
    _supersede(stats_db, old, _at(2026, 3, 6, 11))

    again = _dictation(stats_db, author, _at(2026, 3, 1, 9), 10)
    first = _release(stats_db, again, author, _at(2026, 3, 1, 12))[0]
    _supersede(stats_db, first, _at(2026, 3, 7, 9))
    _release(stats_db, again, author, _at(2026, 3, 8, 15))
    stats_db.commit()

    created = summarize(stats_db, StatisticsQuery(group_by=("status", "typist"), now=now))
    assert _one(created, status="open", typist_user_id=None).count == 4
    assert _one(created, status="open", typist_user_id=typist.id).count == 1
    assert _one(created, status="in_review", typist_user_id=holder.id).count == 1
    assert _one(created, status="in_review", typist_user_id=holder_2.id).count == 1
    assert _one(created, status="released", typist_user_id=typist.id).count == 1
    assert _one(created, status="released", typist_user_id=None).count == 1
    assert all(bucket.total_seconds == bucket.count * 10 for bucket in created.buckets)

    # Completion is the active release on 8 March, not the superseded one on 1 March
    # and not the dictation's creation the same day.
    completed = summarize(stats_db, StatisticsQuery(
        time_basis="completed", start=_at(2026, 3, 8), end=_at(2026, 3, 9),
        grain="day", group_by=("period",), now=now,
    ))
    assert [(b.period_start, b.count) for b in completed.buckets] == [("2026-03-08", 1)]
    assert completed.buckets[0].total_seconds == 10.0


@pytest.mark.parametrize("query,code", [
    (StatisticsQuery(time_basis="processing"), "time_basis"),
    (StatisticsQuery(start=datetime(2026, 3, 2)), "naive_datetime"),
    (StatisticsQuery(group_by=("nope",)), "group_by"),
    (StatisticsQuery(group_by=("status", "status")), "group_by"),
    (StatisticsQuery(grain="day"), "grain"),
    (StatisticsQuery(group_by=("period",)), "grain"),
    (StatisticsQuery(group_by=("period",), grain="day"), "period_bounds"),
    (StatisticsQuery(
        start=datetime(2026, 3, 2, tzinfo=timezone.utc),
        end=datetime(2026, 3, 2, tzinfo=timezone.utc),
    ), "window"),
])
def test_rejects_bad_query(query, code):
    with pytest.raises(StatisticsError) as raised:
        summarize(None, query)
    assert raised.value.code == code


def test_refuses_a_daily_range_past_the_cap(stats_db):
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(StatisticsError) as raised:
        summarize(stats_db, StatisticsQuery(
            start=start, end=start + timedelta(days=4001),
            grain="day", group_by=("period",),
        ))
    assert raised.value.code == "range_too_wide"
