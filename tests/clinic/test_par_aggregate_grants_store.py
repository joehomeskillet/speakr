"""PAR-ADM-008 / PAR-OPS-005: durable aggregate grant store keyed by actor and version.

Synthetic accounts only on a real SQLite file. Verifies unrestricted-scope
rejection, actor isolation, optimistic version locking, and cross-session
persistence of grants and revocations.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.app import app
from src.clinic.statistics_grants import (
    ClinicStatisticsGrant,
    ClinicStatisticsGrantAccount,
    StatisticsGrantError,
    clear_grants,
    create_tables,
    grant_aggregate,
    grants_for,
    revoke_aggregate,
    version_of,
)
from src.database import db
from src.models import User


@pytest.fixture
def grant_db(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "statistics-grants.db").as_posix())

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    with app.app_context():
        db.metadata.create_all(bind=engine)
        create_tables(bind=engine)
        Session = sessionmaker(bind=engine)
        try:
            yield Session
        finally:
            engine.dispose()


def _account(session, name: str) -> int:
    user = User(username=name, email=f"{name}@example.invalid", password="x")
    session.add(user)
    session.commit()
    return int(user.id)


def test_rejects_unrestricted_and_invalid_scopes_without_bumping_version(grant_db):
    s1 = grant_db()
    try:
        actor_id = _account(s1, "stats_actor")
        author_id = _account(s1, "author_one")

        for bad_scope in ("all", " all ", "ALL", "own", "global", "*", "", None):
            with pytest.raises(StatisticsGrantError) as caught:
                grant_aggregate(actor_id, bad_scope, author_ids=[author_id], session=s1)
            expected = "unknown_scope" if bad_scope is None else (
                "all_scope_forbidden" if str(bad_scope).strip().lower() == "all" else "scope_not_grantable"
            )
            assert caught.value.code == expected

        with pytest.raises(StatisticsGrantError) as caught:
            grant_aggregate(actor_id, "author", pool_ids=["pool-1"], session=s1)
        assert caught.value.code == "scope_mismatch"

        with pytest.raises(StatisticsGrantError) as caught:
            grant_aggregate(actor_id, "author_pool", author_ids=[author_id], session=s1)
        assert caught.value.code == "scope_mismatch"

        with pytest.raises(StatisticsGrantError) as caught:
            grant_aggregate(actor_id, "author", session=s1)
        assert caught.value.code == "empty_scope"

        with pytest.raises(StatisticsGrantError) as caught:
            grant_aggregate(actor_id, "author_pool", pool_ids=["  "], session=s1)
        assert caught.value.code == "pool_id"

        assert version_of(actor_id, session=s1) == 0
        assert grants_for(actor_id, session=s1) == ()

        # Database check constraint independently rejects unrestricted scope.
        s1.add(ClinicStatisticsGrantAccount(user_id=actor_id, version=1))
        s1.add(
            ClinicStatisticsGrant(
                user_id=actor_id,
                scope="all",
                author_ids_json="[]",
                pool_ids_json="[]",
                version=1,
                created_at=datetime.now(timezone.utc),
            )
        )
        with pytest.raises(IntegrityError):
            s1.commit()
        s1.rollback()
    finally:
        s1.close()

    s2 = grant_db()
    try:
        assert version_of(actor_id, session=s2) == 0
        assert grants_for(actor_id, session=s2) == ()
    finally:
        s2.close()


def test_persists_scoped_grants_and_revocation_across_sessions(grant_db):
    s1 = grant_db()
    try:
        actor_id = _account(s1, "stats_persist")
        author_a_id = _account(s1, "author_a")
        author_b_id = _account(s1, "author_b")

        g1 = grant_aggregate(
            actor_id, "author", author_ids=[author_a_id, author_b_id],
            expected_version=0, session=s1,
        )
        g2 = grant_aggregate(
            actor_id, "author_pool", pool_ids=["pool-north"],
            expected_version=g1.version, session=s1,
        )
        assert g1.version == 1
        assert g2.version == 2

        with pytest.raises(StatisticsGrantError) as stale_grant:
            grant_aggregate(
                actor_id, "author", author_ids=[author_a_id],
                expected_version=g1.version, session=s1,
            )
        assert stale_grant.value.code == "stale_version"
        assert version_of(actor_id, session=s1) == 2

        revoked_version = revoke_aggregate(
            actor_id, g1.id, expected_version=g2.version, session=s1,
        )
        assert revoked_version == 3
    finally:
        s1.close()

    # Reopen in a fresh DB session: revocation and version survive.
    s2 = grant_db()
    try:
        assert version_of(actor_id, session=s2) == 3
        active = grants_for(actor_id, expected_version=3, session=s2)
        assert len(active) == 1
        assert active[0].id == g2.id
        assert active[0].scope == "author_pool"
        assert active[0].pool_ids == frozenset({"pool-north"})
        assert active[0].author_ids == frozenset()

        # Stale version and re-revoking an already-revoked grant are rejected.
        with pytest.raises(StatisticsGrantError) as stale:
            grants_for(actor_id, expected_version=2, session=s2)
        assert stale.value.code == "stale_version"

        with pytest.raises(StatisticsGrantError) as repeat_revoke:
            revoke_aggregate(actor_id, g1.id, expected_version=3, session=s2)
        assert repeat_revoke.value.code == "unknown_grant"
        assert version_of(actor_id, session=s2) == 3

        # Revoking the last grant leaves no active grants while keeping version 4.
        assert revoke_aggregate(actor_id, g2.id, expected_version=3, session=s2) == 4
        stored_rows = (
            s2.query(ClinicStatisticsGrant)
            .filter_by(user_id=actor_id)
            .order_by(ClinicStatisticsGrant.version)
            .all()
        )
        assert [(r.id, r.revoked_version) for r in stored_rows] == [(g1.id, 3), (g2.id, 4)]
        assert all(r.revoked_at is not None for r in stored_rows)
    finally:
        s2.close()

    s3 = grant_db()
    try:
        assert version_of(actor_id, session=s3) == 4
        assert grants_for(actor_id, session=s3) == ()
    finally:
        s3.close()


def test_reopen_two_synthetic_accounts_in_fresh_session_and_compare_grants(grant_db):
    """Staging check equivalent: two synthetic accounts compared in a fresh DB session."""
    s1 = grant_db()
    try:
        acct_one_id = _account(s1, "stats_one")
        acct_two_id = _account(s1, "stats_two")
        author_x_id = _account(s1, "author_x")
        author_y_id = _account(s1, "author_y")

        grant_one_a = grant_aggregate(acct_one_id, "author", author_ids=[author_x_id], session=s1)
        grant_one_b = grant_aggregate(acct_one_id, "author", author_ids=[author_y_id], session=s1)
        grant_two = grant_aggregate(acct_two_id, "author_pool", pool_ids=["pool-cardio"], session=s1)
        revoke_aggregate(acct_one_id, grant_one_a.id, expected_version=grant_one_b.version, session=s1)

        # Cross-actor revocation is rejected and does not touch either account's version.
        with pytest.raises(StatisticsGrantError) as foreign_revoke:
            revoke_aggregate(acct_two_id, grant_one_b.id, session=s1)
        assert foreign_revoke.value.code == "unknown_grant"
    finally:
        s1.close()

    fresh = grant_db()
    try:
        reopened_one = fresh.get(User, acct_one_id)
        reopened_two = fresh.get(User, acct_two_id)
        assert reopened_one is not None and reopened_two is not None

        grants_one = grants_for(reopened_one.id, session=fresh)
        grants_two = grants_for(reopened_two.id, session=fresh)

        assert version_of(reopened_one.id, session=fresh) == 3
        assert version_of(reopened_two.id, session=fresh) == 1

        assert len(grants_one) == 1
        assert grants_one[0].id == grant_one_b.id
        assert grants_one[0].scope == "author"
        assert grants_one[0].author_ids == frozenset({author_y_id})
        assert grants_one[0].pool_ids == frozenset()

        assert len(grants_two) == 1
        assert grants_two[0].id == grant_two.id
        assert grants_two[0].scope == "author_pool"
        assert grants_two[0].pool_ids == frozenset({"pool-cardio"})
        assert grants_two[0].author_ids == frozenset()

        assert grants_one != grants_two
    finally:
        fresh.close()


def test_clear_grants_resets_persisted_state(grant_db):
    s1 = grant_db()
    try:
        actor_id = _account(s1, "stats_clear")
        author_id = _account(s1, "author_clear")
        grant_aggregate(actor_id, "author", author_ids=[author_id], session=s1)
        assert version_of(actor_id, session=s1) == 1
        clear_grants(session=s1)
        assert version_of(actor_id, session=s1) == 0
        assert grants_for(actor_id, session=s1) == ()
    finally:
        s1.close()


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"author_ids": [True]}, "author_id"),
    ({"author_ids": [0]}, "author_id"),
    ({"author_ids": [1, 1]}, "author_id"),
    ({"author_ids": "1"}, "author_id"),
    ({"author_ids": None}, "author_id"),
    ({"scope": "author_pool", "pool_ids": ["pool", "pool"]}, "pool_id"),
    ({"scope": "author_pool", "pool_ids": []}, "empty_scope"),
    ({"expected_version": True}, "version"),
    ({"expected_version": -1}, "version"),
])
def test_invalid_grant_does_not_write(grant_db, kwargs, code):
    with grant_db() as session:
        actor_id = _account(session, "invalid_grant")
        arguments = {"scope": "author", "author_ids": [actor_id]}
        if kwargs.get("scope") == "author_pool":
            arguments["author_ids"] = ()
        arguments.update(kwargs)
        with pytest.raises(StatisticsGrantError) as caught:
            grant_aggregate(actor_id, **arguments, session=session)
        assert caught.value.code == code
    with grant_db() as fresh:
        assert version_of(actor_id, session=fresh) == 0
        assert grants_for(actor_id, session=fresh) == ()


def test_old_session_observes_revocation_and_rejects_stale_write(grant_db):
    with grant_db() as reader, grant_db() as writer:
        actor_id = _account(writer, "concurrent_actor")
        grant = grant_aggregate(actor_id, "author", author_ids=[actor_id], session=writer)
        assert grants_for(actor_id, expected_version=1, session=reader) == (grant,)
        assert revoke_aggregate(actor_id, grant.id, expected_version=1, session=writer) == 2

        with pytest.raises(StatisticsGrantError) as stale_read:
            grants_for(actor_id, expected_version=1, session=reader)
        assert stale_read.value.code == "stale_version"
        with pytest.raises(StatisticsGrantError) as stale_write:
            grant_aggregate(
                actor_id, "author", author_ids=[actor_id], expected_version=1, session=reader,
            )
        assert stale_write.value.code == "stale_version"
        assert version_of(actor_id, session=reader) == 2
        assert grants_for(actor_id, session=reader) == ()
    with grant_db() as fresh:
        assert version_of(actor_id, session=fresh) == 2
        assert grants_for(actor_id, session=fresh) == ()
