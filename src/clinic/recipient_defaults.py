"""Versioned recipient defaults and submit-time routing (PAR-ADM-006/007).

Reuses pool routing identities from ``src.clinic.pools``: staff-pool ids,
``ClinicPoolRoute`` links and current ``GroupMembership``. A stored default
never writes grants; content rights stay with ``pools.grants_for``.
An administrative choice grant caps both the personal preference and routing.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint, or_
from sqlalchemy.exc import IntegrityError

from src.clinic import pools
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import GroupMembership, User

MODES = ("all_eligible", "person", "staff_pool", "self_correction")
CHOICE_DEFAULT_ONLY = "default_only"
CHOICE_STAFF_POOLS = "staff_pools_only"
CHOICE_OPEN = "open"
CHOICES = (CHOICE_DEFAULT_ONLY, CHOICE_STAFF_POOLS, CHOICE_OPEN)
_MODE_SET = frozenset(MODES)
_CHOICE_SET = frozenset(CHOICES)


class RecipientDefaultError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class VersionConflict(RecipientDefaultError):
    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__("stale_version")


@dataclass(frozen=True)
class Assignment:
    """Submit-time snapshot. Later default edits do not mutate it."""

    author_user_id: int
    author_pool_id: str | None
    mode: str
    target_ids: tuple[str, ...]
    recipient_user_ids: tuple[int, ...]
    default_version: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class ClinicRecipientDefault(db.Model):
    """Current recipient default of one author. ``version`` guards stale writes."""

    __tablename__ = "clinic_recipient_default"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    author_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    mode = db.Column(db.String(24), nullable=False)
    target_ids_json = db.Column(db.Text, nullable=False, default="[]")
    choice = db.Column(db.String(24), nullable=False, default=CHOICE_OPEN)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    updated_at = db.Column(pools.AwareDateTime(), nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("author_user_id", name="uq_clinic_recipient_default_author"),
        CheckConstraint(
            "mode IN ('all_eligible','person','staff_pool','self_correction')",
            name="ck_clinic_recipient_default_mode",
        ),
        CheckConstraint(
            "choice IN ('default_only','staff_pools_only','open')",
            name="ck_clinic_recipient_default_choice",
        ),
    )

    @property
    def target_ids(self) -> list[str]:
        return json.loads(self.target_ids_json)


class ClinicRecipientChoiceGrant(db.Model):
    """Administrative ceiling on a user's recipient selection."""

    __tablename__ = "clinic_recipient_choice_grant"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    choice = db.Column(db.String(24), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "choice IN ('default_only','staff_pools_only','open')",
            name="ck_clinic_recipient_choice_grant_choice",
        ),
    )


DEFAULT_TABLES = (ClinicRecipientDefault, ClinicRecipientChoiceGrant)


def create_tables(bind=None) -> None:
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in DEFAULT_TABLES],
        checkfirst=True,
    )


def _user_id(value, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecipientDefaultError(code)
    if db.session.get(User, value) is None:
        raise RecipientDefaultError(code)
    return value


def _mode(mode: str) -> str:
    if not isinstance(mode, str) or mode not in _MODE_SET:
        raise RecipientDefaultError("invalid_mode")
    return mode


def _choice(choice: str) -> str:
    if not isinstance(choice, str) or choice not in _CHOICE_SET:
        raise RecipientDefaultError("invalid_choice")
    return choice


def _version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecipientDefaultError("invalid_version")
    return value


def _target_ids(raw, mode: str, author_user_id: int) -> list[str]:
    if raw is None:
        raw = []
    if isinstance(raw, str) or not hasattr(raw, "__iter__"):
        raise RecipientDefaultError("invalid_target")
    ids = list(raw)
    if any(not isinstance(item, str) or not item or len(item) > 256 for item in ids):
        raise RecipientDefaultError("invalid_target")
    if mode in ("all_eligible",) and ids:
        raise RecipientDefaultError("invalid_target")
    if mode == "self_correction":
        if ids not in ([], [str(author_user_id)]):
            raise RecipientDefaultError("invalid_target")
        return []
    if mode == "person":
        if len(ids) != 1:
            raise RecipientDefaultError("invalid_target")
        try:
            uid = int(ids[0])
        except (TypeError, ValueError):
            raise RecipientDefaultError("invalid_target") from None
        _user_id(uid, "unknown_user")
        return [str(uid)]
    if mode == "staff_pool":
        if len(ids) != 1:
            raise RecipientDefaultError("invalid_target")
        if db.session.get(pools.ClinicStaffPool, ids[0]) is None:
            raise RecipientDefaultError("unknown_staff_pool")
        return ids
    return []


def _linked_staff_ids(author_pool_id: str) -> list[str]:
    rows = (
        db.session.query(pools.ClinicPoolRoute.staff_pool_id)
        .filter_by(author_pool_id=author_pool_id)
        .order_by(pools.ClinicPoolRoute.id)
        .all()
    )
    now = _utcnow()
    ids = []
    for (staff_id,) in rows:
        staff = db.session.get(pools.ClinicStaffPool, staff_id)
        # Inactive or out-of-validity staff pools are never a default or an eligible target.
        if staff is not None and pools.is_routable(staff, now):
            ids.append(staff_id)
    return ids


def _member_ids(staff_pool_id: str) -> list[int]:
    pool = db.session.get(pools.ClinicStaffPool, staff_pool_id)
    if pool is None:
        return []
    rows = (
        db.session.query(GroupMembership.user_id)
        .join(User, User.id == GroupMembership.user_id)
        .outerjoin(ClinicAccountPolicy, ClinicAccountPolicy.user_id == User.id)
        .filter(
            GroupMembership.group_id == pool.group_id,
            # Preserve existing pool members without a clinic account policy.
            or_(ClinicAccountPolicy.user_id.is_(None), ClinicAccountPolicy.status == "active"),
        )
        .order_by(GroupMembership.user_id)
        .all()
    )
    return [row[0] for row in rows]


def eligible_user_ids(author_pool_id: str) -> tuple[int, ...]:
    """Active staff members of routable pools linked to this author pool."""
    if db.session.get(pools.ClinicAuthorPool, author_pool_id) is None:
        raise RecipientDefaultError("not_author_pool")
    seen: set[int] = set()
    ordered: list[int] = []
    for staff_id in _linked_staff_ids(author_pool_id):
        for user_id in _member_ids(staff_id):
            if user_id not in seen:
                seen.add(user_id)
                ordered.append(user_id)
    return tuple(ordered)


def get_default(author_user_id: int) -> ClinicRecipientDefault | None:
    _user_id(author_user_id, "unknown_author")
    return ClinicRecipientDefault.query.filter_by(author_user_id=author_user_id).one_or_none()


def get_choice_grant(user_id: int) -> str | None:
    grant = db.session.get(ClinicRecipientChoiceGrant, user_id, populate_existing=True)
    return None if grant is None else grant.choice


def effective_choice(row: ClinicRecipientDefault | None, grant: str | None) -> str:
    personal = CHOICE_OPEN if row is None else row.choice
    return min(personal, grant or CHOICE_OPEN, key=CHOICES.index)


def _check_default_mode(mode: str, grant: str | None) -> None:
    if grant == CHOICE_STAFF_POOLS and mode != "staff_pool":
        raise RecipientDefaultError("person_not_allowed" if mode == "person" else "choice_disabled")


def set_default(
    author_user_id: int,
    mode: str,
    *,
    actor_id: int,
    expected_version: int,
    target_ids: list[str] | None = None,
    choice: str | None = None,
) -> ClinicRecipientDefault:
    """Persist a versioned default. ``expected_version`` is 0 on first write."""
    actor_id = _user_id(actor_id, "unknown_actor")
    author_user_id = _user_id(author_user_id, "unknown_author")
    if actor_id != author_user_id:
        raise RecipientDefaultError("unauthorized")
    mode = _mode(mode)
    grant = get_choice_grant(author_user_id)
    if choice is None and grant is None:
        choice = CHOICE_OPEN
    if choice is not None:
        choice = _choice(choice)
        if grant is not None and CHOICES.index(choice) > CHOICES.index(grant):
            raise RecipientDefaultError("choice_disabled")
    _check_default_mode(mode, grant)
    expected_version = _version(expected_version)
    stored_ids = _target_ids(target_ids, mode, author_user_id)
    row = ClinicRecipientDefault.query.filter_by(author_user_id=author_user_id).one_or_none()
    if row is None:
        if expected_version != 0:
            raise VersionConflict(0)
        row = ClinicRecipientDefault(
            author_user_id=author_user_id, version=0, created_by=actor_id
        )
        db.session.add(row)
    elif expected_version != row.version:
        raise VersionConflict(row.version)
    row.mode = mode
    row.target_ids_json = json.dumps(stored_ids)
    if choice is not None:
        row.choice = choice
    row.version += 1
    row.updated_at = _utcnow()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise RecipientDefaultError("conflict") from None
    return row


def selectable_overrides(author_user_id: int, author_pool_id: str) -> dict:
    """Person and staff-pool ids the author may pick instead of the default."""
    _user_id(author_user_id, "unknown_author")
    eligible = eligible_user_ids(author_pool_id)
    staff_ids = tuple(_linked_staff_ids(author_pool_id))
    row = get_default(author_user_id)
    choice = effective_choice(row, get_choice_grant(author_user_id))
    if choice == CHOICE_DEFAULT_ONLY:
        return {"persons": (), "staff_pools": ()}
    if choice == CHOICE_STAFF_POOLS:
        return {"persons": (), "staff_pools": staff_ids}
    return {"persons": tuple(str(uid) for uid in eligible), "staff_pools": staff_ids}


def _resolve(
    mode: str,
    target_ids: list[str],
    author_user_id: int,
    author_pool_id: str | None,
) -> tuple[int, ...]:
    if mode == "self_correction":
        return (author_user_id,)
    if author_pool_id is None:
        raise RecipientDefaultError("not_author_pool")
    eligible = eligible_user_ids(author_pool_id)
    if mode == "all_eligible":
        return eligible
    if mode == "person":
        uid = int(target_ids[0])
        if uid not in eligible:
            raise RecipientDefaultError("recipient_not_eligible")
        return (uid,)
    staff_id = target_ids[0]
    if staff_id not in _linked_staff_ids(author_pool_id):
        raise RecipientDefaultError("recipient_not_eligible")
    return tuple(_member_ids(staff_id))


def submit(
    author_user_id: int,
    author_pool_id: str | None,
    *,
    actor_id: int,
    override: dict | None = None,
    expected_version: int | None = None,
) -> Assignment:
    """Resolve currently eligible recipients. Revalidates an override."""
    actor_id = _user_id(actor_id, "unknown_actor")
    author_user_id = _user_id(author_user_id, "unknown_author")
    if actor_id != author_user_id:
        raise RecipientDefaultError("unauthorized")
    row = get_default(author_user_id)
    current_version = 0 if row is None else row.version
    if expected_version is not None:
        if _version(expected_version) != current_version:
            raise VersionConflict(current_version)
    mode = "all_eligible" if row is None else row.mode
    target_ids = [] if row is None else list(row.target_ids)
    grant = get_choice_grant(author_user_id)
    choice = effective_choice(row, grant)
    if override:
        if choice == CHOICE_DEFAULT_ONLY:
            raise RecipientDefaultError("choice_disabled")
        over_mode = _mode(override.get("mode"))
        if over_mode == "person" and choice == CHOICE_STAFF_POOLS:
            raise RecipientDefaultError("person_not_allowed")
        if over_mode == "self_correction" and choice != CHOICE_OPEN:
            raise RecipientDefaultError("choice_disabled")
        _check_default_mode(over_mode, grant)
        over_ids = _target_ids(override.get("target_ids"), over_mode, author_user_id)
        mode, target_ids = over_mode, over_ids
    elif row is not None:
        _check_default_mode(mode, grant)
    recipients = _resolve(mode, target_ids, author_user_id, author_pool_id)
    return Assignment(
        author_user_id=author_user_id,
        author_pool_id=author_pool_id,
        mode=mode,
        target_ids=tuple(target_ids),
        recipient_user_ids=recipients,
        default_version=current_version,
    )
