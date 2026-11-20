"""Exclusive, time-limited write lease for one clinic dictation.

Pure session functions. No Flask routes and no clock: every call takes ``now``.
A successful first acquire starts generation 1. Takeover after release or
expiry keeps the same row and moves generation forward by one. The database
trigger rejects a generation that does not increase. Heartbeat and release
keep the generation and succeed only for the current holder.

``LeaseConflict`` is the signal a later route maps to HTTP 409. A stale
generation, an expired lease or a different holder cannot extend or clear
the current grant, so a late save from an old editor has no lease to write
under.

SQLite has no row locks. Concurrent acquires insert under the unique
``dictation_id`` constraint and a savepoint, then the loser reads the winner.
PostgreSQL (and MySQL) also take ``SELECT ... FOR UPDATE`` on the dictation
and the lease, in that order.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import set_committed_value

from src.clinic.models import ClinicDictation, ClinicReviewLease
from src.database import db
from src.models.audit import AuditEvent
from src.services import audit

STALE_GENERATION = "stale_generation"
EXPIRED = "expired"
FOREIGN_HOLDER = "foreign_holder"
NOT_FOUND = "not_found"

# The audit vocabulary has no lease event yet. Lease operations use the existing
# change event; the first entry of ``fields`` names the operation. Never transcript text.
AUDIT_EVENT = "recording.edited"

_ROW_LOCK_DIALECTS = {"postgresql", "mysql", "mariadb"}
_DEFAULTS = Path(__file__).resolve().parents[2] / "clinic-spec" / "config" / "dev.defaults.json"
_cached_defaults: tuple[float, float] | None = None


class LeaseConflict(Exception):
    """The caller does not hold the current write lease.

    ``reason`` is ``stale_generation``, ``expired``, ``foreign_holder`` or
    ``not_found``. ``lease`` is the current row when one exists. Routes map
    this exception to HTTP 409.
    """

    def __init__(self, reason: str, lease: ClinicReviewLease | None = None):
        self.reason = reason
        self.lease = lease
        # Generation of the grant that now holds the dictation, when one exists.
        self.generation = lease.generation if lease is not None else None
        super().__init__(reason)


def review_intervals(
    duration: float | None = None,
    heartbeat_interval: float | None = None,
) -> tuple[float, float]:
    """Lease length and heartbeat cadence, in seconds.

    Both come from ``clinic-spec/config/dev.defaults.json`` unless the
    argument is set. The lease length is how long a grant or heartbeat
    stays valid. The cadence is the client's renew hint; it does not
    shorten the lease.
    """
    lease_seconds, heartbeat_seconds = _dev_defaults()
    if duration is not None:
        lease_seconds = _positive(duration, "duration")
    if heartbeat_interval is not None:
        heartbeat_seconds = _positive(heartbeat_interval, "heartbeat_interval")
    return lease_seconds, heartbeat_seconds


def acquire(
    dictation_id: str,
    user_id: int,
    now: datetime,
    duration: float | None = None,
    heartbeat_interval: float | None = None,
) -> ClinicReviewLease:
    """Grant the exclusive write lease, or refresh it when this user still holds it.

    An active lease held by someone else raises ``LeaseConflict``. An
    expired or released lease is taken over and its generation increases
    by one. A retry by the current holder keeps the generation, so the
    token the holder already has stays valid.
    """
    now = _require_now(now)
    seconds, _heartbeat = review_intervals(duration, heartbeat_interval)
    expires = now + timedelta(seconds=seconds)
    if _lock_dictation(dictation_id) is None:
        raise LeaseConflict(NOT_FOUND)

    last = None
    for _attempt in range(2):
        lease = _lock_lease(dictation_id)
        last = lease
        if lease is None:
            created = _insert(dictation_id, user_id, expires)
            if created is not None:
                return created
            if _lock_dictation(dictation_id) is None:
                raise LeaseConflict(NOT_FOUND)
            continue
        _stamp(lease)
        if lease.expires_at > now:
            if lease.holder_user_id != user_id:
                raise LeaseConflict(FOREIGN_HOLDER, lease)
            lease.expires_at = expires
            db.session.flush()
            return lease
        if _take_over(lease, user_id, expires):
            return lease

    if last is not None and _expiry(last) > now and last.holder_user_id == user_id:
        return _stamp(last)
    raise LeaseConflict(FOREIGN_HOLDER, last)


def takeover(
    dictation_id: str,
    user_id: int,
    now: datetime,
    duration: float | None = None,
    heartbeat_interval: float | None = None,
) -> tuple[ClinicReviewLease, tuple[int, int] | None]:
    """Move the lease to ``user_id`` now, even while another holder is still active.

    The generation increases by one, so the previous holder's next save or
    renew fails with a stale generation. Returns the lease and
    ``(previous_holder_user_id, previous_generation)``, or ``None`` when no
    lease existed. A holder who already has the active lease keeps it unchanged.
    The caller decides whether the actor may take over.
    """
    now = _require_now(now)
    seconds, _heartbeat = review_intervals(duration, heartbeat_interval)
    expires = now + timedelta(seconds=seconds)
    if _lock_dictation(dictation_id) is None:
        raise LeaseConflict(NOT_FOUND)
    row = _lock_lease(dictation_id)
    if row is None:
        created = _insert(dictation_id, user_id, expires)
        if created is not None:
            return created, None
        row = _lock_lease(dictation_id)
        if row is None:
            raise LeaseConflict(NOT_FOUND)
    _stamp(row)
    previous = (row.holder_user_id, row.generation)
    if row.holder_user_id == user_id and row.expires_at > now:
        row.expires_at = expires
        db.session.flush()
        return row, previous
    if not _take_over(row, user_id, expires):
        raise LeaseConflict(STALE_GENERATION, row)
    return row, previous


def current(dictation_id: str) -> ClinicReviewLease | None:
    """The stored lease of this dictation (expired or not), or None. Read only."""
    row = db.session.query(ClinicReviewLease).filter(ClinicReviewLease.dictation_id == dictation_id).one_or_none()
    return _stamp(row) if row is not None else None


def audit_operation(
    operation: str,
    row: ClinicReviewLease,
    actor_user_id: int,
    dictation: ClinicDictation,
    *,
    previous_holder: int | None = None,
    actor_kind: str = "session",
) -> None:
    """Add one audit row for a lease operation to the caller's transaction.

    Carries operation, actor, dictation id and generation only. The caller
    commits, so the row exists exactly when the operation took effect.
    """
    fields = [f"lease.{operation}", f"dictation:{row.dictation_id}", f"generation:{row.generation}"]
    if previous_holder is not None:
        fields.append(f"from:{previous_holder}")
    audit.record_in_tx(
        AUDIT_EVENT, recording_id=dictation.recording_id, actor_kind=actor_kind,
        actor_user_id=actor_user_id, target_user_id=previous_holder, details={"fields": fields},
    )


def audit_lapse(dictation: ClinicDictation, now: datetime) -> bool:
    """Audit an expired grant once, when it is first seen.

    Expiry has no event of its own, so it is recorded when the lapsed lease is
    next looked at by an acquire or takeover. A grant that ended by release, or
    was already recorded as expired, writes nothing. Returns True on a new row.
    """
    now = _require_now(now)
    row = _lock_lease(dictation.id)
    if row is None:
        return False
    _stamp(row)
    if row.expires_at > now:
        return False
    marker, generation = f"dictation:{dictation.id}", f"generation:{row.generation}"
    rows = (
        db.session.query(AuditEvent)
        .filter(AuditEvent.event == AUDIT_EVENT, AuditEvent.recording_id == dictation.recording_id)
        .order_by(AuditEvent.id.desc())
        .limit(200)
    )
    for event in rows:
        fields = (event.details or {}).get("fields") or []
        if marker in fields and generation in fields and fields[0] in ("lease.release", "lease.expire"):
            return False
    audit_operation("expire", row, row.holder_user_id, dictation, actor_kind="system")
    return True


def heartbeat(
    lease: ClinicReviewLease,
    generation: int,
    now: datetime,
    duration: float | None = None,
    heartbeat_interval: float | None = None,
    *,
    user_id: int | None = None,
) -> ClinicReviewLease:
    """Slide the expiry forward for this holder and generation only.

    An expired lease is not revived. The holder must acquire again, which
    starts a new generation. ``user_id`` defaults to ``lease.holder_user_id``;
    a route passes the signed-in user so a foreign caller cannot renew.
    """
    now = _require_now(now)
    seconds, _heartbeat = review_intervals(duration, heartbeat_interval)
    holder = lease.holder_user_id if user_id is None else user_id
    current = _require_current(lease, holder, generation, now)
    current.expires_at = now + timedelta(seconds=seconds)
    db.session.flush()
    return current


def release(
    lease: ClinicReviewLease,
    generation: int,
    now: datetime,
    *,
    user_id: int | None = None,
) -> ClinicReviewLease:
    """End this holder's active grant without moving the generation.

    The row stays, with ``expires_at`` set to ``now``, so the same instant
    is already expired and the next acquire starts a new generation.
    A stale generation or another holder leaves the row unchanged.
    """
    now = _require_now(now)
    holder = lease.holder_user_id if user_id is None else user_id
    current = _require_current(lease, holder, generation, now)
    current.expires_at = now
    db.session.flush()
    return current


def assert_current(
    dictation_id: str,
    user_id: int,
    generation: int,
    now: datetime,
) -> ClinicReviewLease:
    """Return the live lease, or raise when a write must not proceed.

    Checked in order: stale generation, then expiry, then holder. Any of
    the three is a ``LeaseConflict``.
    """
    now = _require_now(now)
    _lock_dictation(dictation_id)
    return _check(_lock_lease(dictation_id), user_id, generation, now)


def _require_current(
    lease: ClinicReviewLease | None,
    user_id: int,
    generation: int,
    now: datetime,
) -> ClinicReviewLease:
    if lease is None or getattr(lease, "dictation_id", None) is None:
        raise LeaseConflict(EXPIRED)
    _lock_dictation(lease.dictation_id)
    current = _check(_lock_lease(lease.dictation_id), user_id, generation, now)
    if current.id != lease.id:
        raise LeaseConflict(STALE_GENERATION, current)
    return current


def _check(
    lease: ClinicReviewLease | None,
    user_id: int,
    generation: int,
    now: datetime,
) -> ClinicReviewLease:
    if lease is None:
        raise LeaseConflict(EXPIRED)
    _stamp(lease)
    if lease.generation != generation:
        raise LeaseConflict(STALE_GENERATION, lease)
    if lease.expires_at <= now:
        raise LeaseConflict(EXPIRED, lease)
    if lease.holder_user_id != user_id:
        raise LeaseConflict(FOREIGN_HOLDER, lease)
    return lease


def _insert(dictation_id: str, user_id: int, expires_at: datetime) -> ClinicReviewLease | None:
    lease = ClinicReviewLease(
        dictation_id=dictation_id,
        holder_user_id=user_id,
        expires_at=expires_at,
        generation=1,
    )
    try:
        with db.session.begin_nested():
            db.session.add(lease)
            db.session.flush()
    except IntegrityError:
        if lease in db.session:
            db.session.expunge(lease)
        return None
    return lease


def _take_over(lease: ClinicReviewLease, user_id: int, expires_at: datetime) -> bool:
    """Move an expired row to ``generation + 1`` only if that generation is still current."""
    previous = lease.generation
    lease_id = lease.id
    try:
        with db.session.begin_nested():
            updated = db.session.query(ClinicReviewLease).filter(
                ClinicReviewLease.id == lease_id,
                ClinicReviewLease.generation == previous,
            ).update(
                {
                    ClinicReviewLease.holder_user_id: user_id,
                    ClinicReviewLease.expires_at: expires_at,
                    ClinicReviewLease.generation: previous + 1,
                },
                synchronize_session=False,
            )
    except IntegrityError:
        db.session.expire(lease)
        return False
    if updated != 1:
        db.session.expire(lease)
        return False
    db.session.refresh(lease)
    _stamp(lease)
    return lease.generation == previous + 1 and lease.holder_user_id == user_id


def _lock_dictation(dictation_id: str) -> ClinicDictation | None:
    return _lock(ClinicDictation, ClinicDictation.id, dictation_id)


def _lock_lease(dictation_id: str) -> ClinicReviewLease | None:
    return _lock(ClinicReviewLease, ClinicReviewLease.dictation_id, dictation_id)


def _lock(model, column, value):
    query = db.session.query(model).filter(column == value)
    if db.session.get_bind().dialect.name in _ROW_LOCK_DIALECTS:
        query = query.with_for_update()
    return query.one_or_none()


def _stamp(lease: ClinicReviewLease) -> ClinicReviewLease:
    """Make ``expires_at`` UTC-aware without marking the row dirty.

    SQLite hands back a naive UTC value. PostgreSQL hands back an aware one.
    """
    expires = _as_utc(lease.expires_at)
    if lease.expires_at.tzinfo is None or lease.expires_at != expires:
        set_committed_value(lease, "expires_at", expires)
    return lease


def _expiry(lease: ClinicReviewLease) -> datetime:
    return _as_utc(lease.expires_at)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _positive(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be a positive number of seconds")
    return float(value)


def _dev_defaults() -> tuple[float, float]:
    global _cached_defaults
    if _cached_defaults is None:
        with _DEFAULTS.open(encoding="utf-8") as handle:
            review = json.load(handle)["review"]
        _cached_defaults = (
            _positive(review["lease_seconds"], "lease_seconds"),
            _positive(review["heartbeat_seconds"], "heartbeat_seconds"),
        )
    return _cached_defaults
