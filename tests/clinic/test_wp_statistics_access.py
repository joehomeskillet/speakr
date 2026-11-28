"""Statistics-account scope. Synthetic rows only, on a real SQLite file.

A statistics account reads aggregates solely inside an explicit author or
author-pool grant. Counts come from ``statistics.summarize``; dictations
outside the grant are not part of that call.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.app import app
from src.clinic.models import ClinicDictation
from src.clinic.policy import RecordingFacts, build_subject
from src.clinic.statistics import StatisticsError, StatisticsQuery, summarize
from src.clinic.statistics_access import (
    StatisticsAccessError,
    grant_aggregate,
    read_aggregates,
    recording_access,
    revoke_aggregate,
    version_of,
)
from src.database import db
from src.models import Recording, User

POOL_B = "pool-b"
WHEN = datetime(2026, 3, 2, 8, tzinfo=timezone.utc)
DENIED = ("audio", "transcript", "export", "identity_admin", "all")


@pytest.fixture
def stats_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    engine = create_engine("sqlite:///" + (tmp_path / "statistics-access.db").as_posix())

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    with app.app_context():
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


def _dictation(session, author, seconds, pool_id=None):
    recording = Recording(
        user_id=author.id, title="d", status="PENDING", audio_duration_seconds=seconds,
    )
    session.add(recording)
    session.flush()
    row = ClinicDictation(
        recording_id=recording.id,
        author_user_id=author.id,
        created_at=WHEN,
        pool_id=pool_id,
    )
    session.add(row)
    session.flush()
    return row


def _world(session):
    """Author A (10s), author B in pool-b (7s), author C with no length."""
    people = {
        "stats": _user(session, "stats"),
        "a": _user(session, "authora"),
        "b": _user(session, "authorb"),
        "c": _user(session, "authorc"),
    }
    _dictation(session, people["a"], 10)
    _dictation(session, people["b"], 7, pool_id=POOL_B)
    _dictation(session, people["c"], None)
    session.commit()
    return people


def _read(session, user_id, query=None, **kwargs):
    return read_aggregates(
        session,
        user_id,
        StatisticsQuery() if query is None else query,
        role=kwargs.pop("role", "statistics"),
        **kwargs,
    )


def _only(result):
    assert len(result.buckets) == 1
    return result.buckets[0]


def test_granted_scope_hides_other_dictations(stats_db):
    """Author grant, then pool grant. Excluded rows stay out of both counters."""
    people = _world(stats_db)
    stats_id = people["stats"].id
    author = grant_aggregate(stats_id, "author", author_ids=[people["a"].id], session=stats_db)
    created = _only(_read(stats_db, stats_id))
    assert (created.count, created.total_seconds, created.incomplete) == (1, 10.0, False)
    assert created.known_duration_count == 1
    completed = _read(stats_db, stats_id, StatisticsQuery(time_basis="completed"))
    assert completed.excluded_missing_timestamp == 1
    raw = _only(summarize(stats_db, StatisticsQuery()))
    assert raw.count == 3 and raw.incomplete is True

    pooled = grant_aggregate(
        stats_id, "author_pool", pool_ids=[POOL_B], expected_version=author.version, session=stats_db,
    )
    both = _only(_read(stats_db, stats_id))
    assert (both.count, both.total_seconds, both.average_seconds) == (2, 17.0, 8.5)
    assert both.incomplete is False
    by_author = _read(stats_db, stats_id, StatisticsQuery(group_by=("author",)))
    found = {bucket.author_user_id: bucket.count for bucket in by_author.buckets}
    assert found == {people["a"].id: 1, people["b"].id: 1}
    assert people["c"].id not in found
    completed = _read(stats_db, stats_id, StatisticsQuery(time_basis="completed"))
    assert completed.excluded_missing_timestamp == 2
    raw_completed = summarize(stats_db, StatisticsQuery(time_basis="completed"))
    assert raw_completed.excluded_missing_timestamp == 3
    assert pooled.version == 2


def test_statistics_role_does_not_grant_other_capabilities(stats_db):
    people = _world(stats_db)
    stats_id = people["stats"].id
    grant_aggregate(stats_id, "author", author_ids=[people["a"].id], session=stats_db)
    for capability in DENIED:
        with pytest.raises(StatisticsAccessError) as caught:
            _read(stats_db, stats_id, capability=capability)
        assert caught.value.code == "capability_denied"
    for role in ("admin", "author", "typist"):
        with pytest.raises(StatisticsAccessError) as caught:
            _read(stats_db, stats_id, role=role)
        assert caught.value.code == "not_statistics"
    foreign = RecordingFacts(recording_id=1, owner_id=people["a"].id)
    for action in ("export", "play", "download"):
        decision = recording_access(stats_id, action, foreign)
        assert decision.allowed is False and decision.reason == "no_grant"
    own_export = recording_access(stats_id, "export", RecordingFacts(2, stats_id))
    assert own_export.allowed is False and own_export.reason == "action_not_granted"
    audio = recording_access(stats_id, "audio", foreign)
    assert audio.allowed is False and audio.reason == "unknown_action"
    assert build_subject(stats_id).grants == ()
    assert _only(_read(stats_db, stats_id)).count == 1


def test_rejected_scope_does_not_stick_and_a_failed_query_retries(stats_db):
    people = _world(stats_db)
    stats_id = people["stats"].id
    with pytest.raises(StatisticsAccessError) as caught:
        grant_aggregate(stats_id, " all ", author_ids=[people["a"].id], session=stats_db)
    assert caught.value.code == "all_scope_forbidden"
    with pytest.raises(StatisticsAccessError) as caught:
        grant_aggregate(stats_id, "own", author_ids=[people["a"].id], session=stats_db)
    assert caught.value.code == "scope_not_grantable"
    with pytest.raises(StatisticsAccessError) as caught:
        grant_aggregate(stats_id, "author", session=stats_db)
    assert caught.value.code == "empty_scope"
    with pytest.raises(StatisticsAccessError) as caught:
        grant_aggregate(stats_id, "author", author_ids=[True], session=stats_db)
    assert caught.value.code == "author_id"
    assert version_of(stats_id, session=stats_db) == 0

    grant = grant_aggregate(
        stats_id, "author", author_ids=[people["a"].id], expected_version=0, session=stats_db,
    )
    assert grant.version == 1
    start = datetime(2026, 3, 5, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, tzinfo=timezone.utc)
    with pytest.raises(StatisticsError) as caught:
        _read(stats_db, stats_id, StatisticsQuery(start=start, end=end))
    assert caught.value.code == "window"
    assert version_of(stats_id, session=stats_db) == 1
    assert _only(_read(stats_db, stats_id)).count == 1
    with pytest.raises(StatisticsAccessError) as caught:
        revoke_aggregate(stats_id, "missing", expected_version=1, session=stats_db)
    assert caught.value.code == "unknown_grant"
    assert version_of(stats_id, session=stats_db) == 1
    assert revoke_aggregate(stats_id, grant.id, expected_version=1, session=stats_db) == 2


def test_stale_version_keeps_access_until_a_current_revoke(stats_db):
    people = _world(stats_db)
    stats_id = people["stats"].id
    author = grant_aggregate(stats_id, "author", author_ids=[people["a"].id], session=stats_db)
    pooled = grant_aggregate(
        stats_id, "author_pool", pool_ids=[POOL_B], expected_version=author.version, session=stats_db,
    )
    with pytest.raises(StatisticsAccessError) as caught:
        revoke_aggregate(stats_id, author.id, expected_version=author.version, session=stats_db)
    assert caught.value.code == "stale_version"
    assert _only(_read(stats_db, stats_id, expected_version=pooled.version)).count == 2
    with pytest.raises(StatisticsAccessError) as caught:
        _read(stats_db, stats_id, expected_version=author.version)
    assert caught.value.code == "stale_version"
    current = revoke_aggregate(stats_id, pooled.id, expected_version=pooled.version, session=stats_db)
    assert _only(_read(stats_db, stats_id)).count == 1
    current = revoke_aggregate(stats_id, author.id, expected_version=current, session=stats_db)
    with pytest.raises(StatisticsAccessError) as caught:
        _read(stats_db, stats_id)
    assert caught.value.code == "no_grant"
    with pytest.raises(StatisticsAccessError) as caught:
        _read(stats_db, stats_id, expected_version=pooled.version)
    assert caught.value.code == "stale_version"
    assert version_of(stats_id, session=stats_db) == current


def test_operations_are_inert_outside_clinic_mode(monkeypatch, stats_db):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    with pytest.raises(StatisticsAccessError) as caught:
        grant_aggregate(4, "author", author_ids=[4])
    assert caught.value.code == "clinic_mode_off"
    with pytest.raises(StatisticsAccessError) as caught:
        recording_access(4, "export", RecordingFacts(1, 9))
    assert caught.value.code == "clinic_mode_off"
    monkeypatch.setenv("CLINIC_MODE", "1")
    assert version_of(4, session=stats_db) == 0
