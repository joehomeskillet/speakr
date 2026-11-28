"""Live dictation session state, speech lock and autosave receipts (P06, service only).

One live session belongs to one dictation and one holder (the person who
dictates). States: ``RECORDING``, ``PAUSED``, ``ENDED``. Only one session per
dictation is open (not ``ENDED``) at any time. The database enforces this with
the unique ``active_key`` column, which holds the dictation id while the
session is open and NULL once it has ended. A second start raises
``LiveSessionError(ACTIVE_SESSION)``.

Speech lock: while a session is open and its lock has not expired, the text
area of that dictation is closed for every other editor. ``assert_edit_allowed``
is the check a later editor route calls before it accepts a change. The lock
is a lease on inactivity. Each holder call (heartbeat, pause, continue,
acknowledge) moves ``lock_expires_at`` to ``now + timeout``. The default is
``DEFAULT_LOCK_TIMEOUT_SECONDS`` (60 s): twelve times the 5 s autosave
cadence in ``clinic-spec/config/dev.defaults.json``, so a short network gap does
not drop the lock, and a crashed client frees the field within a minute. A
call may pass a different timeout.

Autosave receipts: the client sends a receipt with a rising ``seq``, the
audio offset in milliseconds and an opaque reference to the text state
(for example a transcript revision id). The row keeps only the latest receipt.
After a crash or break the holder calls ``recover``. It reopens the lapsed
or expired session and returns the last receipt as ``ResumePoint``.
Everything after that receipt was never acknowledged and is the client's to
resend. A receipt with a lower ``seq`` or an offset that moves backwards is
rejected.

No Flask routes and no clock: every call takes ``now``. Callers commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, inspect, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import set_committed_value

from src.clinic.config import clinic_mode_enabled
from src.clinic.lease import _as_utc, _lock_dictation, _positive, _require_now
from src.clinic.models import _created_at, _in_check, _uuid
from src.database import db
from src.models.user import User

RECORDING = "RECORDING"
PAUSED = "PAUSED"
ENDED = "ENDED"
LIVE_STATES = (RECORDING, PAUSED, ENDED)

END_FINISHED = "finished"
END_EXPIRED = "expired"
END_REASONS = (END_FINISHED, END_EXPIRED)

ACTIVE_SESSION = "active_session"
SPEECH_LOCK_HELD = "speech_lock_held"
EXPIRED = "expired"
NOT_FOUND = "not_found"
NOT_HOLDER = "not_holder"
ENDED_SESSION = "ended"
STALE_RECEIPT = "stale_receipt"
NO_SESSION = "no_session"

DEFAULT_LOCK_TIMEOUT_SECONDS = 60.0

_STAMPED = ("started_at", "ended_at", "last_activity_at", "lock_expires_at", "acked_at")


class LiveSessionError(Exception):
    """A live-session rule was violated. A later route maps this to HTTP 409.

    ``reason`` is one of ``active_session``, ``speech_lock_held``, ``expired``,
    ``not_found``, ``not_holder``, ``ended``, ``stale_receipt`` or
    ``no_session``. ``session`` is the current row when one exists.
    """

    def __init__(self, reason: str, session: "ClinicLiveSession | None" = None):
        self.reason = reason
        self.session = session
        super().__init__(reason)


class ClinicLiveSession(db.Model):
    """One live dictation session. ``active_key`` is the dictation id while open, else NULL."""

    __tablename__ = "clinic_live_session"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    client_id = db.Column(db.String(128), nullable=True)
    holder_generation = db.Column(db.Integer, nullable=True)
    state = db.Column(db.String(16), nullable=False)
    end_reason = db.Column(db.String(16), nullable=True)
    active_key = db.Column(db.String(36), nullable=True, unique=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_activity_at = db.Column(db.DateTime(timezone=True), nullable=False)
    lock_expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ack_seq = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    audio_offset_ms = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    text_ref = db.Column(db.String(128), nullable=True)
    acked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_live_dictation"
        ),
        _in_check("state", LIVE_STATES, "ck_clinic_live_state"),
        CheckConstraint(
            "(state = 'ENDED' AND active_key IS NULL) OR "
            "(state <> 'ENDED' AND active_key IS NOT NULL)",
            name="ck_clinic_live_single_active",
        ),
        CheckConstraint("ack_seq >= 0 AND audio_offset_ms >= 0", name="ck_clinic_live_receipt"),
    )


@dataclass(frozen=True)
class ResumePoint:
    """Where to continue after a break: the last acknowledged receipt."""

    session_id: str
    ack_seq: int
    audio_offset_ms: int
    text_ref: str | None


def create_tables(bind=None) -> None:
    """Add the nullable fence to existing session tables without changing old receipts."""
    bind = bind or db.engine
    with bind.begin() as connection:
        if "holder_generation" in {
            column["name"] for column in inspect(connection).get_columns(ClinicLiveSession.__tablename__)
        }:
            return
        if bind.dialect.name == "postgresql":
            connection.execute(text(
                "ALTER TABLE clinic_live_session ADD COLUMN IF NOT EXISTS holder_generation INTEGER"))
        else:
            connection.execute(text("ALTER TABLE clinic_live_session ADD COLUMN holder_generation INTEGER"))


def lock_author(user_id: int) -> None:
    """Serialize acquisition across drafts, before taking any dictation row lock."""
    if db.session.get_bind().dialect.name == "sqlite":
        db.session.execute(update(User).where(User.id == user_id).values(username=User.username))
    else:
        db.session.query(User.id).filter_by(id=user_id).with_for_update(key_share=True).one_or_none()


def author_holders(user_id: int, now: datetime | None = None) -> list[ClinicLiveSession]:
    """Read recordings and unreleased paused fences under the author lock."""
    row = ClinicLiveSession
    query = db.session.query(row).filter(
        row.author_user_id == user_id,
        (row.state == RECORDING) | ((row.state == PAUSED)
                                  & row.holder_generation.isnot(None) & row.client_id.isnot(None)),
    )
    if now is not None:
        query = query.filter(row.lock_expires_at > now)
    return query.populate_existing().all()


def assert_no_other_holder(dictation_id: str, user_id: int, now: datetime) -> None:
    if not clinic_mode_enabled():
        return
    for row in author_holders(user_id, now):
        if row.dictation_id != dictation_id:
            raise LiveSessionError(ACTIVE_SESSION, row)


def check_fence(session: ClinicLiveSession, client_id: str | None, generation: int | None) -> None:
    if session.holder_generation is not None and (
        session.client_id is None or session.client_id != client_id
        or type(generation) is not int or session.holder_generation != generation
    ):
        raise LiveSessionError(NOT_HOLDER, session)


def revoke_holder(session: ClinicLiveSession) -> None:
    session.state = PAUSED
    session.client_id = None
    if session.holder_generation is None:
        session.holder_generation = 0


def lock_timeout(timeout: float | None = None) -> float:
    """Inactivity timeout of the speech lock in seconds."""
    if timeout is None:
        return DEFAULT_LOCK_TIMEOUT_SECONDS
    return _positive(timeout, "timeout")


def start(
    dictation_id: str,
    user_id: int,
    now: datetime,
    client_id: str | None = None,
    timeout: float | None = None,
) -> ClinicLiveSession:
    """Open a RECORDING session. A still-valid open session blocks it.

    An open session whose lock has expired is ended with reason ``expired``
    and the new session takes over, whoever held the old one.
    """
    now = _require_now(now)
    seconds = lock_timeout(timeout)
    lock_author(user_id)
    if _lock_dictation(dictation_id) is None:
        raise LiveSessionError(NOT_FOUND)
    current = _open(dictation_id)
    if current is not None:
        if _expiry(current) > now:
            raise LiveSessionError(ACTIVE_SESSION, current)
    assert_no_other_holder(dictation_id, user_id, now)
    if current is not None:
        _close(current, now, END_EXPIRED)
    session = ClinicLiveSession(
        dictation_id=dictation_id,
        author_user_id=user_id,
        client_id=client_id,
        state=RECORDING,
        active_key=dictation_id,
        started_at=now,
        last_activity_at=now,
        lock_expires_at=now + timedelta(seconds=seconds),
    )
    try:
        with db.session.begin_nested():
            db.session.add(session)
            db.session.flush()
    except IntegrityError:
        if session in db.session:
            db.session.expunge(session)
        raise LiveSessionError(ACTIVE_SESSION, _open(dictation_id)) from None
    return session


def heartbeat(
    session_id: str, user_id: int, now: datetime, timeout: float | None = None,
    *, client_id: str | None = None, generation: int | None = None,
) -> ClinicLiveSession:
    """Renew the speech lock. Holder only, and only before the lock expired."""
    session = _holder_session(session_id, user_id, now, client_id, generation)
    assert_no_other_holder(session.dictation_id, user_id, now)
    return _touch(session, now, timeout)


def pause(
    session_id: str, user_id: int, now: datetime, timeout: float | None = None,
    *, client_id: str | None = None, generation: int | None = None, release: bool = False,
) -> ClinicLiveSession:
    """Pause dictation, optionally relinquishing the current tab's fence."""
    session = _holder_session(session_id, user_id, now, client_id, generation)
    if release:
        if session.holder_generation is None and (client_id is None or session.client_id != client_id):
            raise LiveSessionError(NOT_HOLDER, session)
        revoke_holder(session)
    else:
        session.state = PAUSED
    return _touch(session, now, timeout)


def continue_recording(
    session_id: str, user_id: int, now: datetime, timeout: float | None = None,
    *, client_id: str | None = None, generation: int | None = None,
) -> ClinicLiveSession:
    """Continue after ``pause``."""
    session = _holder_session(session_id, user_id, now, client_id, generation)
    assert_no_other_holder(session.dictation_id, user_id, now)
    session.state = RECORDING
    return _touch(session, now, timeout)


def acknowledge(
    session_id: str,
    user_id: int,
    now: datetime,
    seq: int,
    audio_offset_ms: int,
    text_ref: str | None = None,
    timeout: float | None = None,
    *, client_id: str | None = None, generation: int | None = None,
) -> ClinicLiveSession:
    """Store the autosave receipt and renew the lock.

    ``seq`` must rise. Repeating the stored ``seq`` with the same values is
    accepted and changes nothing but the lock. A lower ``seq``, a different
    payload under the stored ``seq`` or an offset below the stored one raises
    ``stale_receipt``.
    """
    for label, value in (("seq", seq), ("audio_offset_ms", audio_offset_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(label + " must be a non-negative integer")
    session = _holder_session(session_id, user_id, now, client_id, generation)
    assert_no_other_holder(session.dictation_id, user_id, now)
    now = _require_now(now)
    same = (
        seq == session.ack_seq
        and audio_offset_ms == session.audio_offset_ms
        and text_ref == session.text_ref
    )
    if not same:
        if seq <= session.ack_seq or audio_offset_ms < session.audio_offset_ms:
            raise LiveSessionError(STALE_RECEIPT, session)
        session.ack_seq = seq
        session.audio_offset_ms = audio_offset_ms
        session.text_ref = text_ref
        session.acked_at = now
    return _touch(session, now, timeout)


def end(
    session_id: str, user_id: int, now: datetime,
    *, client_id: str | None = None, generation: int | None = None,
) -> ClinicLiveSession:
    """Finish the session and free the speech lock. Holder only; repeating it is a no-op.

    A holder whose lock has lapsed, but whom nobody replaced, may still end it.
    """
    now = _require_now(now)
    lock_author(user_id)
    session = _load(session_id)
    if session.author_user_id != user_id:
        raise LiveSessionError(NOT_HOLDER, session)
    check_fence(session, client_id, generation)
    if session.state != ENDED:
        _close(session, now, END_FINISHED)
    return session


def recover(
    dictation_id: str,
    user_id: int,
    now: datetime,
    client_id: str | None = None,
    timeout: float | None = None,
    *, generation: int | None = None,
) -> tuple[ClinicLiveSession, ResumePoint]:
    """Reopen this holder's interrupted session and return the last receipt.

    Works for a session whose lock lapsed but is still open, and for one that
    was ended with reason ``expired`` when nobody has taken the dictation
    since. A session the holder ended on purpose is not recovered. An open,
    unexpired session of someone else raises ``active_session``. With nothing
    to recover it raises ``no_session``.
    """
    now = _require_now(now)
    seconds = lock_timeout(timeout)
    lock_author(user_id)
    if _lock_dictation(dictation_id) is None:
        raise LiveSessionError(NOT_FOUND)
    current = _open(dictation_id)
    session: ClinicLiveSession | None
    if current is not None:
        if current.author_user_id != user_id and _expiry(current) > now:
            raise LiveSessionError(ACTIVE_SESSION, current)
        if current.author_user_id != user_id:
            raise LiveSessionError(NO_SESSION, current)
        session = current
    else:
        session = (
            db.session.query(ClinicLiveSession)
            .filter_by(dictation_id=dictation_id)
            .order_by(ClinicLiveSession.started_at.desc(), ClinicLiveSession.created_at.desc())
            .first()
        )
        if (
            session is None
            or session.author_user_id != user_id
            or session.end_reason != END_EXPIRED
        ):
            raise LiveSessionError(NO_SESSION, session)
    reclaim = (client_id is not None and type(generation) is int and generation > 0
               and (_expiry(session) <= now or (session.state == PAUSED and session.client_id is None))
               and (session.client_id != client_id or session.holder_generation is None))
    if not reclaim:
        check_fence(session, client_id, generation)
    assert_no_other_holder(dictation_id, user_id, now)
    if reclaim:
        session.holder_generation = max(session.holder_generation or 0, 1) + 1
    if current is None:
        session.end_reason = None
        session.ended_at = None
        session.active_key = dictation_id
    _stamp(session)
    session.state = PAUSED
    if client_id is not None:
        session.client_id = client_id
    _touch(session, now, seconds)
    return session, ResumePoint(session.id, session.ack_seq, session.audio_offset_ms, session.text_ref)


def assert_edit_allowed(dictation_id: str, user_id: int, now: datetime) -> None:
    """Raise ``speech_lock_held`` when another person's live session holds the text area."""
    now = _require_now(now)
    current = _open(dictation_id)
    if current is None or _expiry(current) <= now:
        return
    if current.author_user_id != user_id:
        raise LiveSessionError(SPEECH_LOCK_HELD, current)


def active_session(dictation_id: str, now: datetime) -> ClinicLiveSession | None:
    """The open session whose speech lock is still valid, else None."""
    now = _require_now(now)
    current = _open(dictation_id)
    if current is None or _expiry(current) <= now:
        return None
    return current


def _open(dictation_id: str) -> ClinicLiveSession | None:
    row = (
        db.session.query(ClinicLiveSession)
        .filter(
            ClinicLiveSession.dictation_id == dictation_id,
            ClinicLiveSession.state != ENDED,
        )
        .populate_existing()
        .one_or_none()
    )
    return _stamp(row) if row is not None else None


def _load(session_id: str) -> ClinicLiveSession:
    """Fetch by id after taking the dictation lock (PostgreSQL row lock; no-op on SQLite)."""
    row = db.session.get(ClinicLiveSession, session_id)
    if row is None:
        raise LiveSessionError(NOT_FOUND)
    _lock_dictation(row.dictation_id)
    db.session.refresh(row)
    return _stamp(row)


def _holder_session(
    session_id: str, user_id: int, now: datetime, client_id: str | None, generation: int | None,
) -> ClinicLiveSession:
    now = _require_now(now)
    lock_author(user_id)
    session = _load(session_id)
    if session.author_user_id != user_id:
        raise LiveSessionError(NOT_HOLDER, session)
    if session.state == ENDED:
        raise LiveSessionError(ENDED_SESSION, session)
    if _expiry(session) <= now:
        raise LiveSessionError(EXPIRED, session)
    check_fence(session, client_id, generation)
    return session


def _touch(session: ClinicLiveSession, now: datetime, timeout: float | None) -> ClinicLiveSession:
    now = _require_now(now)
    session.last_activity_at = now
    session.lock_expires_at = now + timedelta(seconds=lock_timeout(timeout))
    db.session.flush()
    return session


def _close(session: ClinicLiveSession, now: datetime, reason: str) -> None:
    session.state = ENDED
    session.end_reason = reason
    session.ended_at = now
    session.active_key = None
    db.session.flush()


def _expiry(session: ClinicLiveSession) -> datetime:
    return _as_utc(session.lock_expires_at)


def _stamp(session: ClinicLiveSession) -> ClinicLiveSession:
    """Make loaded timestamps UTC-aware without marking the row dirty (SQLite returns naive)."""
    for name in _STAMPED:
        value = getattr(session, name)
        if value is not None and value.tzinfo is None:
            set_committed_value(session, name, _as_utc(value))
    return session
