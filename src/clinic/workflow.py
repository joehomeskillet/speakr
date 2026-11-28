"""Dictation workflow: states, work lists, priority and deadline.

Queue states use the clinic labels. Preparation is two states, offline and
online, because a sent device dictation waits in the offline group until
recognition or a skip moves it on. Opening a writable dictation stores the
queue state it left; closing restores that state. Rejecting an acceptance
returns the dictation to correction. Finishing while it is open follows the
same rule as finishing from the queue, since the case window is already the
open state.

Priority is only "Bei Gelegenheit", "Normal" and "Dringend" (0, 1, 128).
A deadline is a separate date. The clock time is optional; without it the
deadline is 23:59:59 Europe/Zurich on that date. An unknown imported
priority becomes Normal and sets ``priority_unrecognized``. The original
token is not kept.

This module does not check pool grants and does not take the review lease.
A later wave wires routes, rights and the lease together.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import CheckConstraint, or_
from sqlalchemy.orm import Session

from src.database import db

CLINIC_ZONE = "Europe/Zurich"
_ZONE = ZoneInfo(CLINIC_ZONE)
END_OF_DAY = time(23, 59, 59)

PRIORITY_WHEN_CONVENIENT = 0
PRIORITY_NORMAL = 1
PRIORITY_URGENT = 128
PRIORITY_CODES = (
    PRIORITY_WHEN_CONVENIENT,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
)

ROLE_AUTHOR = "author"
ROLE_TYPIST = "typist"
_ROLES = (ROLE_AUTHOR, ROLE_TYPIST)

TASK = "aufgabe"
DRAFT = "neu_angelegt"
PREP_OFFLINE = "vorbereitung_offline"
PREP_ONLINE = "vorbereitung_online"
TO_WRITE = "zu_schreiben"
TO_CORRECT = "zu_korrigieren"
IN_PROGRESS = "in_bearbeitung"
TO_ACCEPT = "abzunehmen"
ACCEPTED = "abgenommen"
DONE = "fertiggestellt"
TO_ARCHIVE = "zu_archivieren"
TO_DELETE = "zu_loeschen"

STATUS_ORDER = (
    TASK,
    DRAFT,
    PREP_OFFLINE,
    PREP_ONLINE,
    TO_WRITE,
    TO_CORRECT,
    IN_PROGRESS,
    TO_ACCEPT,
    ACCEPTED,
    DONE,
    TO_ARCHIVE,
    TO_DELETE,
)
_QUEUE = (TO_WRITE, TO_CORRECT)
_PREP = (PREP_OFFLINE, PREP_ONLINE)

LABELS = {
    TASK: "Aufgabe",
    DRAFT: "Neu angelegt",
    PREP_OFFLINE: "In Vorbereitung [Offline]",
    PREP_ONLINE: "In Vorbereitung [Online]",
    TO_WRITE: "Zu schreiben",
    TO_CORRECT: "Zu korrigieren",
    IN_PROGRESS: "In Bearbeitung",
    TO_ACCEPT: "Abzunehmen",
    ACCEPTED: "Abgenommen",
    DONE: "Fertiggestellt",
    TO_ARCHIVE: "Zu archivieren",
    TO_DELETE: "Zu Löschen",
}

# Tokens for targets that depend on the row, not only on the pair.
_RESUME = "resume"
_BRANCH = "branch"

# (current status or None for a new dictation, trigger) -> next status or token.
TRANSITIONS: dict[tuple[str | None, str], str] = {
    (None, "plan"): TASK,
    (None, "create"): DRAFT,
    (DRAFT, "send_offline"): PREP_OFFLINE,
    (DRAFT, "send_online"): PREP_ONLINE,
}
for _prep in _PREP:
    TRANSITIONS[(_prep, "skip_recognition")] = TO_WRITE
    TRANSITIONS[(_prep, "recognition_done")] = TO_CORRECT
for _queue in _QUEUE:
    TRANSITIONS[(_queue, "open")] = IN_PROGRESS
    TRANSITIONS[(_queue, "complete")] = _BRANCH
TRANSITIONS.update(
    {
        (IN_PROGRESS, "close"): _RESUME,
        (IN_PROGRESS, "complete"): _BRANCH,
        (TO_ACCEPT, "accept"): ACCEPTED,
        (TO_ACCEPT, "reject"): TO_CORRECT,
        (ACCEPTED, "complete"): DONE,
        (DONE, "reopen"): TO_CORRECT,
    }
)
for _status in STATUS_ORDER:
    if _status != TO_ARCHIVE:
        TRANSITIONS[(_status, "report_archive")] = TO_ARCHIVE
    if _status != TO_DELETE:
        TRANSITIONS[(_status, "report_delete")] = TO_DELETE

TRIGGERS = tuple(sorted({trigger for _status, trigger in TRANSITIONS}))

_PRIORITY_TEXT = {
    "0": PRIORITY_WHEN_CONVENIENT,
    "idle": PRIORITY_WHEN_CONVENIENT,
    "bei gelegenheit": PRIORITY_WHEN_CONVENIENT,
    "1": PRIORITY_NORMAL,
    "normal": PRIORITY_NORMAL,
    "128": PRIORITY_URGENT,
    "urgent": PRIORITY_URGENT,
    "dringend": PRIORITY_URGENT,
}


class WorkflowError(Exception):
    """A workflow call cannot be applied. ``code`` is a stable token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WorkflowTransitionError(WorkflowError):
    """The status and trigger pair is not in the transition table."""

    def __init__(self, status: str | None, trigger: str):
        super().__init__("illegal_transition")
        self.status = status
        self.trigger = trigger


class _UtcDateTime(db.TypeDecorator):
    """UTC timestamp. Naive values are rejected on write."""

    impl = db.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise WorkflowError("naive_datetime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    listed = ", ".join("'" + value + "'" for value in values)
    return CheckConstraint(column + " IN (" + listed + ")", name=name)


class ClinicWorkflowDictation(db.Model):
    """One dictation on the workflow work list. No foreign keys yet."""

    __tablename__ = "clinic_workflow_dictation"

    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    author_user_id = db.Column(db.Integer, nullable=False, index=True)
    pool_id = db.Column(db.String(36), nullable=False, index=True)
    assignee_user_id = db.Column(db.Integer, nullable=True, index=True)
    assigned_at = db.Column(_UtcDateTime(), nullable=True)
    status = db.Column(db.String(32), nullable=False)
    # Queue state to restore when the open dictation is closed.
    resume_status = db.Column(db.String(32), nullable=True)
    acceptance_required = db.Column(db.Boolean, nullable=False, default=False)
    priority = db.Column(db.Integer, nullable=False, default=PRIORITY_NORMAL)
    priority_unrecognized = db.Column(db.Boolean, nullable=False, default=False)
    audio_seconds = db.Column(db.Integer, nullable=False, default=0)
    deadline_date = db.Column(db.Date, nullable=True)
    deadline_time = db.Column(db.Time, nullable=True)
    deadline_at = db.Column(_UtcDateTime(), nullable=True)
    created_at = db.Column(_UtcDateTime(), nullable=False, default=_utcnow)
    # Resolved shipping target at submit. Not an assignee and not a visibility grant.
    routing_target_json = db.Column(db.Text, nullable=True)

    __table_args__ = (
        _in_check("status", STATUS_ORDER, "ck_clinic_workflow_status"),
        CheckConstraint("priority IN (0, 1, 128)", name="ck_clinic_workflow_priority"),
        CheckConstraint("audio_seconds >= 0", name="ck_clinic_workflow_audio"),
        CheckConstraint(
            # IS NOT NULL keeps the IN-list from becoming NULL, which SQLite
            # would treat as a satisfied CHECK.
            "(status = 'in_bearbeitung' AND resume_status IS NOT NULL"
            " AND resume_status IN ('zu_schreiben', 'zu_korrigieren'))"
            " OR (status <> 'in_bearbeitung' AND resume_status IS NULL)",
            name="ck_clinic_workflow_resume",
        ),
        CheckConstraint(
            "(deadline_date IS NULL AND deadline_time IS NULL AND deadline_at IS NULL)"
            " OR (deadline_date IS NOT NULL AND deadline_at IS NOT NULL)",
            name="ck_clinic_workflow_deadline",
        ),
    )


WORKFLOW_TABLES = (ClinicWorkflowDictation,)


def create_tables(bind=None) -> None:
    """Create missing tables and upgrade legacy assignment timestamps."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in WORKFLOW_TABLES],
        checkfirst=True,
    )
    from . import migration_assignment_stamp

    bound = bind or db.engine
    migration_assignment_stamp.upgrade(bound)
    _ensure_routing_column(bound)


def _ensure_routing_column(bind) -> None:
    """Add the routing snapshot column. Existing rows stay unassigned and untargeted."""
    from sqlalchemy import inspect, text
    from sqlalchemy.engine import Engine

    inspector = inspect(bind)
    table = ClinicWorkflowDictation.__tablename__
    if not inspector.has_table(table):
        return
    if any(column["name"] == "routing_target_json" for column in inspector.get_columns(table)):
        return
    statement = "ALTER TABLE clinic_workflow_dictation ADD COLUMN routing_target_json TEXT"
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            connection.execute(text(statement))
    else:
        bind.execute(text(statement))


def normalize_priority(raw) -> tuple[int, bool]:
    """Map an imported priority to ``(code, unrecognized)``.

    ``None`` is the default Normal and is not a flag. Anything else that is
    not one of the three levels becomes Normal and sets the flag.
    """
    if raw is None:
        return PRIORITY_NORMAL, False
    if isinstance(raw, bool):
        return PRIORITY_NORMAL, True
    if isinstance(raw, int):
        if raw in PRIORITY_CODES:
            return raw, False
        return PRIORITY_NORMAL, True
    if isinstance(raw, str):
        key = " ".join(raw.strip().casefold().split())
        code = _PRIORITY_TEXT.get(key)
        if code is not None:
            return code, False
    return PRIORITY_NORMAL, True


def resolve_deadline(on_date, at_time) -> datetime | None:
    """UTC instant for a deadline. A missing clock time means end of that day."""
    if on_date is None and at_time is None:
        return None
    if not isinstance(on_date, date) or isinstance(on_date, datetime):
        raise WorkflowError("deadline_date_required")
    if at_time is None:
        clock = END_OF_DAY
    elif isinstance(at_time, time) and at_time.tzinfo is None:
        clock = at_time
    else:
        raise WorkflowError("invalid_deadline_time")
    local = datetime.combine(on_date, clock, tzinfo=_ZONE)
    return local.astimezone(timezone.utc)


def start(
    session: Session,
    trigger: str,
    *,
    author_user_id: int,
    pool_id: str,
    audio_seconds: int = 0,
    assignee_user_id: int | None = None,
    priority_raw=None,
    acceptance_required: bool = False,
    deadline_on: date | None = None,
    deadline_clock: time | None = None,
) -> ClinicWorkflowDictation:
    """Insert a dictation in ``Aufgabe`` (``plan``) or ``Neu angelegt`` (``create``)."""
    spec = TRANSITIONS.get((None, trigger))
    if spec is None or spec in (_RESUME, _BRANCH):
        raise WorkflowTransitionError(None, trigger)
    row = ClinicWorkflowDictation(
        author_user_id=_user_id(author_user_id),
        pool_id=_pool_id(pool_id),
        assignee_user_id=_optional_user(assignee_user_id),
        status=spec,
        resume_status=None,
        acceptance_required=_flag(acceptance_required, "invalid_acceptance"),
        audio_seconds=_audio(audio_seconds),
        priority=PRIORITY_NORMAL,
        priority_unrecognized=False,
    )
    _write_priority(row, priority_raw)
    _write_deadline(row, deadline_on, deadline_clock)
    session.add(row)
    session.flush()
    return row


def transition(session: Session, dictation_id: str, trigger: str) -> ClinicWorkflowDictation:
    """Move a dictation. An unknown pair raises ``WorkflowTransitionError``."""
    row = _require(session, dictation_id)
    nxt = _destination(row.status, trigger, row.acceptance_required, row.resume_status)
    if nxt is None:
        raise WorkflowTransitionError(row.status, trigger)
    row.status, row.resume_status = nxt
    session.flush()
    return row


def resubmit_for_acceptance(session: Session, dictation_id: str) -> ClinicWorkflowDictation | None:
    """Wiedervorlage: an accepted dictation whose text head moved goes back to ``Abzunehmen``.

    Kept apart from ``TRANSITIONS`` (the user-triggered table, pinned by test_workflow.py):
    this move is made by the save path, never requested by a caller. Returns the moved row,
    or None when the dictation is not in ``Abgenommen`` (nothing to resubmit).
    """
    row = _require(session, dictation_id)
    if row.status != ACCEPTED:
        return None
    row.status, row.resume_status = TO_ACCEPT, None
    session.flush()
    return row


def set_priority(session: Session, dictation_id: str, raw) -> ClinicWorkflowDictation:
    """Store a priority. Unknown raw values become Normal and set the flag."""
    row = _require(session, dictation_id)
    _write_priority(row, raw)
    session.flush()
    return row


def set_deadline(session: Session, dictation_id: str, on_date, at_time) -> ClinicWorkflowDictation:
    """Set or clear the deadline. Pass both arguments as ``None`` to clear it."""
    row = _require(session, dictation_id)
    _write_deadline(row, on_date, at_time)
    session.flush()
    return row


def set_acceptance(session: Session, dictation_id: str, required: bool) -> ClinicWorkflowDictation:
    """Record whether finish must stop for acceptance. Does not change status."""
    row = _require(session, dictation_id)
    row.acceptance_required = _flag(required, "invalid_acceptance")
    session.flush()
    return row


@dataclass(frozen=True)
class WorkGroup:
    """One status group on a work list."""

    status: str
    label: str
    count: int
    total_audio_seconds: int
    finished: bool
    dictation_ids: tuple[str, ...]


def worklist(session: Session, *, role: str, user_id: int, pool_id: str) -> list[WorkGroup]:
    """Groups for one role in one pool, with count and total audio length.

    An author sees only their own dictations in the pool. A typist sees
    dictations in the pool that are unassigned or assigned to them.
    ``finished`` is true only for ``Fertiggestellt``.
    """
    if role not in _ROLES:
        raise WorkflowError("unknown_role")
    user = _user_id(user_id)
    pool = _pool_id(pool_id)
    query = session.query(ClinicWorkflowDictation).filter(ClinicWorkflowDictation.pool_id == pool)
    if role == ROLE_AUTHOR:
        query = query.filter(ClinicWorkflowDictation.author_user_id == user)
    else:
        query = query.filter(
            or_(
                ClinicWorkflowDictation.assignee_user_id.is_(None),
                ClinicWorkflowDictation.assignee_user_id == user,
            )
        )
    buckets: dict[str, list[ClinicWorkflowDictation]] = {}
    for row in query.all():
        buckets.setdefault(row.status, []).append(row)
    groups = []
    for status in STATUS_ORDER:
        items = buckets.get(status)
        if not items:
            continue
        ordered = sorted(items, key=lambda item: (item.created_at, item.id))
        groups.append(
            WorkGroup(
                status=status,
                label=LABELS[status],
                count=len(ordered),
                total_audio_seconds=sum(item.audio_seconds for item in ordered),
                finished=status == DONE,
                dictation_ids=tuple(item.id for item in ordered),
            )
        )
    return groups


def _destination(status, trigger, acceptance_required, resume_status):
    spec = TRANSITIONS.get((status, trigger))
    if spec is None:
        return None
    if spec == _RESUME:
        if resume_status not in _QUEUE:
            return None
        return resume_status, None
    if spec == _BRANCH:
        return (TO_ACCEPT if acceptance_required else DONE), None
    if trigger == "open":
        return IN_PROGRESS, status
    return spec, None


def _write_priority(row, raw) -> None:
    code, flagged = normalize_priority(raw)
    row.priority = code
    row.priority_unrecognized = flagged


def _write_deadline(row, on_date, at_time) -> None:
    instant = resolve_deadline(on_date, at_time)
    if instant is None:
        row.deadline_date = None
        row.deadline_time = None
        row.deadline_at = None
        return
    row.deadline_date = on_date
    row.deadline_time = at_time
    row.deadline_at = instant


def _require(session: Session, dictation_id: str) -> ClinicWorkflowDictation:
    if not isinstance(dictation_id, str):
        raise WorkflowError("not_found")
    row = session.get(ClinicWorkflowDictation, dictation_id)
    if row is None:
        raise WorkflowError("not_found")
    return row


def _user_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowError("invalid_user")
    return value


def _optional_user(value):
    if value is None:
        return None
    return _user_id(value)


def _pool_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise WorkflowError("invalid_pool")
    if len(value) > 36:
        raise WorkflowError("invalid_pool")
    return value


def _audio(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkflowError("invalid_audio_length")
    return value


def _flag(value: bool, code: str) -> bool:
    if not isinstance(value, bool):
        raise WorkflowError(code)
    return value


_ROUTING_MODES = frozenset(("all_eligible", "person", "staff_pool", "self_correction"))
_ROUTING_LABELS = {
    "all_eligible": "Alle berechtigten", "person": "Person",
    "staff_pool": "Schreibdienst", "self_correction": "Selbstkorrektur",
}


@dataclass(frozen=True)
class RoutingTarget:
    """Submit-time shipping target. A later default edit does not change it."""

    mode: str
    target_ids: tuple[str, ...]
    recipient_user_ids: tuple[int, ...]
    default_version: int


def store_routing_target(
    session: Session, dictation_id: str, *, actor_id: int,
    override: dict | None = None, expected_version: int | None = None,
) -> RoutingTarget:
    """Store the resolved target. Does not assign a typist or grant visibility."""
    row = _require(session, dictation_id)
    from src.clinic import recipient_defaults as defaults

    try:
        sent = defaults.submit(
            row.author_user_id, row.pool_id, actor_id=actor_id,
            override=override, expected_version=expected_version,
        )
    except defaults.RecipientDefaultError as error:
        raise WorkflowError(error.code) from error
    target = RoutingTarget(sent.mode, sent.target_ids, sent.recipient_user_ids, sent.default_version)
    row.routing_target_json = json.dumps({
        "mode": target.mode, "target_ids": list(target.target_ids),
        "recipient_user_ids": list(target.recipient_user_ids), "default_version": target.default_version,
    }, separators=(",", ":"))
    session.flush()
    return target


def read_routing_target(row: ClinicWorkflowDictation) -> RoutingTarget | None:
    raw = row.routing_target_json
    if raw is None:
        return None
    data = json.loads(raw)
    mode, ids, recipients, version = (
        data.get("mode"), data.get("target_ids"), data.get("recipient_user_ids"), data.get("default_version"),
    )
    if (mode not in _ROUTING_MODES or type(version) is not int
            or not isinstance(ids, list) or not isinstance(recipients, list)):
        raise WorkflowError("invalid_routing_target")
    return RoutingTarget(mode, tuple(ids), tuple(recipients), version)


def routing_public(row: ClinicWorkflowDictation | None) -> dict | None:
    target = None if row is None else read_routing_target(row)
    if target is None:
        return None
    name = _ROUTING_LABELS[target.mode]
    if target.mode in ("person", "staff_pool") and target.target_ids:
        name = f"{name} {target.target_ids[0]}"
    return {
        "mode": target.mode, "target_ids": list(target.target_ids),
        "recipient_user_ids": list(target.recipient_user_ids), "default_version": target.default_version,
        "label": f"{name} (Default v{target.default_version})",
    }
