"""Durable store for author and author-pool aggregate statistics grants.

Each statistics actor (``user_id``) has a monotonic grant-set version in
``clinic_statistics_grant_account`` and explicit scoped grants in
``clinic_statistics_grant``. Unrestricted scopes (such as ``all``) and unknown
scopes are rejected before write and by database check constraints. Revocations
bump the actor version and mark the grant revoked so subsequent sessions keep
both the revocation and the new version.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database import db
from src.models import User

GRANTABLE_SCOPES = frozenset({"author", "author_pool"})


class StatisticsGrantError(ValueError):
    """The grant read or change was rejected. ``code`` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AggregateGrant:
    """One active explicit aggregate grant and the actor version that created it."""

    id: str
    user_id: int
    scope: str
    author_ids: frozenset[int]
    pool_ids: frozenset[str]
    version: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class ClinicStatisticsGrantAccount(db.Model):
    """Per-actor monotonic grant-set version for optimistic concurrency."""

    __tablename__ = "clinic_statistics_grant_account"

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("version >= 0", name="ck_clinic_statistics_grant_account_version"),
    )


class ClinicStatisticsGrant(db.Model):
    """One persisted author or author-pool aggregate grant for a statistics actor."""

    __tablename__ = "clinic_statistics_grant"

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    scope = db.Column(db.String(24), nullable=False)
    author_ids_json = db.Column(db.Text, nullable=False, default="[]")
    pool_ids_json = db.Column(db.Text, nullable=False, default="[]")
    version = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_version = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("scope IN ('author', 'author_pool')", name="ck_clinic_statistics_grant_scope"),
        CheckConstraint("version >= 1", name="ck_clinic_statistics_grant_version"),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_version IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_version IS NOT NULL AND revoked_version > version)",
            name="ck_clinic_statistics_grant_revocation",
        ),
    )


GRANT_TABLES = (ClinicStatisticsGrantAccount, ClinicStatisticsGrant)


def create_tables(bind=None) -> None:
    """Create the statistics grant tables if missing."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in GRANT_TABLES],
        checkfirst=True,
    )


def _session(session: Session | None) -> Session:
    return db.session if session is None else session


def _user_id(user_id: object) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise StatisticsGrantError("user_id")
    return user_id


def _members(values: object, code: str) -> frozenset:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise StatisticsGrantError(code)
    found = []
    for value in values:
        if code == "author_id":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise StatisticsGrantError(code)
        elif not isinstance(value, str) or not value or value != value.strip():
            raise StatisticsGrantError(code)
        found.append(value)
    if len(found) != len(set(found)):
        raise StatisticsGrantError(code)
    return frozenset(found)


def _scope(
    scope: object,
    author_ids: Iterable[int],
    pool_ids: Iterable[str],
) -> tuple[str, frozenset[int], frozenset[str]]:
    if not isinstance(scope, str):
        raise StatisticsGrantError("unknown_scope")
    name = scope.strip().lower()
    if name == "all":
        raise StatisticsGrantError("all_scope_forbidden")
    if name not in GRANTABLE_SCOPES:
        raise StatisticsGrantError("scope_not_grantable")
    authors = _members(author_ids, "author_id")
    pools = _members(pool_ids, "pool_id")
    if name == "author" and pools:
        raise StatisticsGrantError("scope_mismatch")
    if name == "author_pool" and authors:
        raise StatisticsGrantError("scope_mismatch")
    if name == "author" and not authors:
        raise StatisticsGrantError("empty_scope")
    if name == "author_pool" and not pools:
        raise StatisticsGrantError("empty_scope")
    return name, authors, pools


def _check_version(current: int, expected: int | None) -> None:
    if expected is None:
        return
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise StatisticsGrantError("version")
    if expected != current:
        raise StatisticsGrantError("stale_version")


def _current_version(sess: Session, user_id: int) -> int:
    row = sess.execute(
        select(ClinicStatisticsGrantAccount.version).where(
            ClinicStatisticsGrantAccount.user_id == user_id
        )
    ).first()
    return 0 if row is None else int(row[0])


def _bump_version(sess: Session, user_id: int, current: int) -> int:
    new_version = current + 1
    if current == 0:
        sess.add(ClinicStatisticsGrantAccount(user_id=user_id, version=new_version))
        sess.flush()
        return new_version
    result = sess.execute(
        update(ClinicStatisticsGrantAccount)
        .where(
            ClinicStatisticsGrantAccount.user_id == user_id,
            ClinicStatisticsGrantAccount.version == current,
        )
        .values(version=new_version)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise StatisticsGrantError("stale_version")
    return new_version


def version_of(user_id: int, *, session: Session | None = None) -> int:
    """Return the persisted grant-set version for ``user_id`` (0 when none)."""
    uid = _user_id(user_id)
    return _current_version(_session(session), uid)


def grants_for(
    user_id: int,
    *,
    expected_version: int | None = None,
    session: Session | None = None,
) -> tuple[AggregateGrant, ...]:
    """Return all active aggregate grants for ``user_id`` in version order."""
    uid = _user_id(user_id)
    sess = _session(session)
    current = _current_version(sess, uid)
    _check_version(current, expected_version)
    rows = sess.execute(
        select(
            ClinicStatisticsGrant.id,
            ClinicStatisticsGrant.user_id,
            ClinicStatisticsGrant.scope,
            ClinicStatisticsGrant.author_ids_json,
            ClinicStatisticsGrant.pool_ids_json,
            ClinicStatisticsGrant.version,
        )
        .where(
            ClinicStatisticsGrant.user_id == uid,
            ClinicStatisticsGrant.revoked_at.is_(None),
        )
        .order_by(ClinicStatisticsGrant.version, ClinicStatisticsGrant.id)
    ).all()
    return tuple(
        AggregateGrant(
            id=row.id,
            user_id=row.user_id,
            scope=row.scope,
            author_ids=frozenset(json.loads(row.author_ids_json)),
            pool_ids=frozenset(json.loads(row.pool_ids_json)),
            version=row.version,
        )
        for row in rows
    )


def grant_aggregate(
    user_id: int,
    scope: str,
    *,
    author_ids: Iterable[int] = (),
    pool_ids: Iterable[str] = (),
    expected_version: int | None = None,
    session: Session | None = None,
) -> AggregateGrant:
    """Persist one scoped aggregate grant for ``user_id`` and bump its version."""
    uid = _user_id(user_id)
    scope_name, authors, pools = _scope(scope, author_ids, pool_ids)
    sess = _session(session)
    if sess.execute(select(User.id).where(User.id == uid)).first() is None:
        raise StatisticsGrantError("unknown_user")
    current = _current_version(sess, uid)
    _check_version(current, expected_version)
    grant_id = _new_id()
    try:
        new_version = _bump_version(sess, uid, current)
        sess.add(
            ClinicStatisticsGrant(
                id=grant_id,
                user_id=uid,
                scope=scope_name,
                author_ids_json=json.dumps(sorted(authors)),
                pool_ids_json=json.dumps(sorted(pools)),
                version=new_version,
                created_at=_utcnow(),
            )
        )
        sess.commit()
    except IntegrityError as exc:
        sess.rollback()
        raise StatisticsGrantError("stale_version") from exc
    except BaseException:
        sess.rollback()
        raise
    return AggregateGrant(grant_id, uid, scope_name, authors, pools, new_version)


def revoke_aggregate(
    user_id: int,
    grant_id: str,
    *,
    expected_version: int | None = None,
    session: Session | None = None,
) -> int:
    """Persist revocation of ``grant_id`` for ``user_id`` and bump its version."""
    uid = _user_id(user_id)
    if not isinstance(grant_id, str) or not grant_id:
        raise StatisticsGrantError("unknown_grant")
    sess = _session(session)
    current = _current_version(sess, uid)
    _check_version(current, expected_version)
    active = sess.execute(
        select(ClinicStatisticsGrant.id).where(
            ClinicStatisticsGrant.id == grant_id,
            ClinicStatisticsGrant.user_id == uid,
            ClinicStatisticsGrant.revoked_at.is_(None),
        )
    ).first()
    if active is None:
        raise StatisticsGrantError("unknown_grant")
    try:
        new_version = _bump_version(sess, uid, current)
        updated = sess.execute(
            update(ClinicStatisticsGrant)
            .where(
                ClinicStatisticsGrant.id == grant_id,
                ClinicStatisticsGrant.user_id == uid,
                ClinicStatisticsGrant.revoked_at.is_(None),
            )
            .values(revoked_at=_utcnow(), revoked_version=new_version)
            .execution_options(synchronize_session=False)
        )
        if updated.rowcount != 1:
            raise StatisticsGrantError("unknown_grant")
        sess.commit()
    except BaseException:
        sess.rollback()
        raise
    return new_version


def clear_grants(*, session: Session | None = None) -> None:
    """Remove every stored grant and actor version row (test helper)."""
    sess = _session(session)
    sess.query(ClinicStatisticsGrant).delete(synchronize_session=False)
    sess.query(ClinicStatisticsGrantAccount).delete(synchronize_session=False)
    sess.commit()
