"""Author resource pools and staff pools. Two tables, so kind cannot change.

``clinic_author_pool`` holds authors and shared vocabularies/templates.
``clinic_staff_pool`` is one existing ``group``; typists are group members.
Rights use ids only. ``ClinicDictation.pool_id`` is an author-pool id with no
foreign key here (``models.py`` is another package). ``policy_provider`` is
``grants_for``. ``User.is_admin`` is not read.

shortcut: a group membership inserted outside ``add_staff_member`` grants the
same view and writes no history. History is append-only in the ORM only.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint, event
from sqlalchemy.exc import IntegrityError

from src.database import db
from src.models import Group, GroupMembership, User

logger = logging.getLogger(__name__)

ACTIONS = ("view", "play", "edit", "export", "release")
# Separately grantable actions (PAR-ADM-005). A grant without ``actions`` allows
# only ``ACTIONS``; these must be named. A deny without ``actions`` blocks all.
SEPARATE_ACTIONS = ("delete", "archive", "rename", "priority", "audio_export", "reassign", "reopen")
GRANTABLE_ACTIONS = ACTIONS + SEPARATE_ACTIONS
OWN_ACTIONS = ("view", "play")  # safe own default; other actions need a grant
STAFF_LINK_ACTIONS = ("view",)  # derived from a route plus a current membership
SCOPES = ("own", "pool", "all")
EFFECTS = ("allow", "deny")
RESOURCE_KINDS = ("vocabulary", "template")
_ACTION_SET = frozenset(GRANTABLE_ACTIONS)
_SCOPE_RANK = {"own": 3, "pool": 2, "all": 1}
_NAME_MAX = 100


class PoolError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class AwareDateTime(db.TypeDecorator):
    """UTC timestamp. Naive values are rejected on write and stamped UTC on read."""

    impl = db.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise PoolError("naive_datetime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


_last_stamp = None


def _utcnow():
    """UTC, and strictly later than the previous stamp so history order holds."""
    global _last_stamp
    now = datetime.now(timezone.utc)
    if _last_stamp is not None and now <= _last_stamp:
        now = _last_stamp + timedelta(microseconds=1)
    _last_stamp = now
    return now


def _new_id():
    return str(uuid.uuid4())


def _ts():
    return db.Column(AwareDateTime(), nullable=False, default=_utcnow)


class ClinicAuthorPool(db.Model):
    __tablename__ = "clinic_author_pool"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    name = db.Column(db.String(_NAME_MAX), nullable=False, unique=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    # v8-29: NULL active means active. Deactivated pools stay referenced in history.
    active = db.Column(db.Boolean, nullable=True)
    valid_from = db.Column(AwareDateTime(), nullable=True)
    valid_to = db.Column(AwareDateTime(), nullable=True)


class ClinicStaffPool(db.Model):
    __tablename__ = "clinic_staff_pool"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    group_id = db.Column(
        db.Integer, db.ForeignKey("group.id"), nullable=False, unique=True
    )
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    active = db.Column(db.Boolean, nullable=True)
    valid_from = db.Column(AwareDateTime(), nullable=True)
    valid_to = db.Column(AwareDateTime(), nullable=True)
    group = db.relationship("Group", foreign_keys=[group_id])

    @property
    def name(self):
        return self.group.name if self.group is not None else None


class ClinicAuthorPoolMember(db.Model):
    __tablename__ = "clinic_author_pool_member"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    pool_id = db.Column(
        db.String(36), db.ForeignKey("clinic_author_pool.id"), nullable=False, index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    __table_args__ = (
        UniqueConstraint("pool_id", "user_id", name="uq_clinic_author_pool_member"),
    )


class ClinicPoolRoute(db.Model):
    __tablename__ = "clinic_pool_route"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    author_pool_id = db.Column(
        db.String(36), db.ForeignKey("clinic_author_pool.id"), nullable=False, index=True
    )
    staff_pool_id = db.Column(
        db.String(36), db.ForeignKey("clinic_staff_pool.id"), nullable=False, index=True
    )
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    __table_args__ = (
        UniqueConstraint(
            "author_pool_id", "staff_pool_id", name="uq_clinic_pool_route"
        ),
    )


class ClinicPoolResource(db.Model):
    __tablename__ = "clinic_pool_resource"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    author_pool_id = db.Column(
        db.String(36), db.ForeignKey("clinic_author_pool.id"), nullable=False, index=True
    )
    kind = db.Column(db.String(16), nullable=False)
    name = db.Column(db.String(_NAME_MAX), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    __table_args__ = (
        UniqueConstraint(
            "author_pool_id", "kind", "name", name="uq_clinic_pool_resource"
        ),
        CheckConstraint(
            "kind IN ('vocabulary','template')", name="ck_clinic_pool_resource_kind"
        ),
    )


class ClinicVisibilityGrant(db.Model):
    """``actions`` NULL means every action. ``pool_id`` is set only for scope pool."""

    __tablename__ = "clinic_visibility_grant"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    effect = db.Column(db.String(8), nullable=False)
    scope = db.Column(db.String(8), nullable=False)
    pool_id = db.Column(
        db.String(36), db.ForeignKey("clinic_author_pool.id"), nullable=True, index=True
    )
    actions = db.Column(db.String(64), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
    __table_args__ = (
        CheckConstraint("effect IN ('allow','deny')", name="ck_clinic_grant_effect"),
        CheckConstraint("scope IN ('own','pool','all')", name="ck_clinic_grant_scope"),
        CheckConstraint(
            "(scope = 'pool' AND pool_id IS NOT NULL) OR (scope <> 'pool' AND pool_id IS NULL)",
            name="ck_clinic_grant_pool",
        ),
    )


class ClinicGrantAuthor(db.Model):
    """Author user ids a grant is limited to. A grant without rows covers every author of its scope."""

    __tablename__ = "clinic_visibility_grant_author"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    grant_id = db.Column(
        db.String(36), db.ForeignKey("clinic_visibility_grant.id"), nullable=False, index=True
    )
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (
        UniqueConstraint("grant_id", "author_user_id", name="uq_clinic_grant_author"),
    )


class ClinicGrantVersion(db.Model):
    """Stored counter for 409 stale checks; one row, bumped on every allow, deny and revoke."""

    __tablename__ = "clinic_grant_version"
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=0)


class ClinicPoolHistory(db.Model):
    __tablename__ = "clinic_pool_history"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    happened_at = _ts()
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    entity = db.Column(db.String(16), nullable=False)
    entity_id = db.Column(db.String(36), nullable=False)
    change = db.Column(db.String(16), nullable=False)
    detail = db.Column(db.Text, nullable=False)


@event.listens_for(ClinicPoolHistory, "before_update")
@event.listens_for(ClinicPoolHistory, "before_delete")
def _reject_history_mutation(_mapper, _connection, _target):
    raise PoolError("history_immutable")


POOL_TABLES = (
    ClinicAuthorPool,
    ClinicStaffPool,
    ClinicAuthorPoolMember,
    ClinicPoolRoute,
    ClinicPoolResource,
    ClinicVisibilityGrant,
    ClinicGrantAuthor,
    ClinicGrantVersion,
    ClinicPoolHistory,
)


def create_tables(bind=None) -> None:
    """Create this module's tables if they are missing. Does not touch other models."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in POOL_TABLES],
        checkfirst=True,
    )
    from . import migration_pool_admin  # adds the v8-29 columns to tables that predate them

    migration_pool_admin.upgrade(bind or db.engine)


def _actor(actor_id, action="manage_pools"):
    """Authorise a mutation: active site admin or an explicit central-policy grant of ``action``."""
    from dataclasses import replace

    from . import policy
    from .config import clinic_mode_enabled
    from .identity import ClinicAccountPolicy

    user = db.session.get(User, actor_id, populate_existing=True) if type(actor_id) is int else None
    if user is None:
        raise PoolError("unknown_actor")
    if not clinic_mode_enabled():
        return actor_id  # upstream behaviour is unchanged with clinic mode off
    account = db.session.get(ClinicAccountPolicy, user.id, populate_existing=True)
    if account is not None and account.status != "active":
        raise PoolError("forbidden")
    if user.is_admin:
        return actor_id
    # Project explicit grants of this action onto a grant-only action on a synthetic
    # actor-owned resource, so central scope and deny rules still apply.
    subject = policy.build_subject(user.id)
    grants = tuple(
        replace(grant, actions=frozenset({"release"})) for grant in subject.grants
        if action in grant.actions
    )
    if grants and policy.decide(
        replace(subject, grants=grants), "release", policy.RecordingFacts(recording_id=0, owner_id=user.id),
    ).allowed:
        return actor_id
    raise PoolError("forbidden")


def _known_user(user_id):
    if db.session.get(User, user_id) is None:
        raise PoolError("unknown_user")
    return user_id


def _name(name):
    if not isinstance(name, str) or name != name.strip() or not name or len(name) > _NAME_MAX:
        raise PoolError("invalid_name")
    return name


def _author_pool(pool_id):
    row = db.session.get(ClinicAuthorPool, pool_id)
    if row is None:
        raise PoolError("not_author_pool")
    return row


def _staff_pool(pool_id):
    row = db.session.get(ClinicStaffPool, pool_id)
    if row is None:
        raise PoolError("not_staff_pool")
    return row


def _history(actor_id, entity, entity_id, change, detail):
    row = ClinicPoolHistory(
        happened_at=_utcnow(),
        actor_user_id=actor_id,
        entity=entity,
        entity_id=str(entity_id),
        change=change,
        detail=json.dumps(detail, sort_keys=True),
    )
    db.session.add(row)
    logger.info("clinic pool: %s %s %s by %s", change, entity, entity_id, actor_id)
    return row


def _commit():
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise PoolError("conflict") from None


def create_author_pool(name: str, *, actor_id: int) -> ClinicAuthorPool:
    actor_id = _actor(actor_id)
    name = _name(name)
    if db.session.query(ClinicAuthorPool.id).filter_by(name=name).first():
        raise PoolError("name_taken")
    row = ClinicAuthorPool(name=name, created_by=actor_id)
    db.session.add(row)
    db.session.flush()
    _history(
        actor_id, "pool", row.id, "created",
        {"kind": "author", "old": None, "new": {"name": name, "active": True}},
    )
    _commit()
    return row


def create_staff_pool(name: str, *, actor_id: int) -> ClinicStaffPool:
    actor_id = _actor(actor_id)
    name = _name(name)
    if db.session.query(Group.id).filter_by(name=name).first():
        raise PoolError("name_taken")
    group = Group(name=name, created_by=actor_id)
    db.session.add(group)
    db.session.flush()
    row = ClinicStaffPool(group_id=group.id, created_by=actor_id)
    db.session.add(row)
    db.session.flush()
    _history(
        actor_id, "pool", row.id, "created",
        {"kind": "staff", "group_id": group.id, "old": None, "new": {"name": name, "active": True}},
    )
    _commit()
    return row


def add_author_member(pool_id: str, user_id: int, *, actor_id: int) -> ClinicAuthorPoolMember:
    actor_id = _actor(actor_id)
    pool = _author_pool(pool_id)
    user_id = _known_user(user_id)
    if ClinicAuthorPoolMember.query.filter_by(pool_id=pool.id, user_id=user_id).first():
        raise PoolError("already_member")
    row = ClinicAuthorPoolMember(pool_id=pool.id, user_id=user_id, created_by=actor_id)
    db.session.add(row)
    db.session.flush()
    _history(
        actor_id, "membership", row.id, "added",
        {"kind": "author", "pool_id": pool.id, "user_id": user_id},
    )
    _commit()
    return row


def remove_author_member(pool_id: str, user_id: int, *, actor_id: int) -> None:
    actor_id = _actor(actor_id)
    pool = _author_pool(pool_id)
    row = ClinicAuthorPoolMember.query.filter_by(pool_id=pool.id, user_id=user_id).first()
    if row is None:
        raise PoolError("not_a_member")
    detail = {"kind": "author", "pool_id": pool.id, "user_id": user_id}
    member_id = row.id
    db.session.delete(row)
    _history(actor_id, "membership", member_id, "removed", detail)
    _commit()


def add_staff_member(pool_id: str, user_id: int, *, actor_id: int) -> None:
    actor_id = _actor(actor_id)
    pool = _staff_pool(pool_id)
    user_id = _known_user(user_id)
    if GroupMembership.query.filter_by(group_id=pool.group_id, user_id=user_id).first():
        raise PoolError("already_member")
    db.session.add(GroupMembership(group_id=pool.group_id, user_id=user_id, role="member"))
    db.session.flush()
    _history(
        actor_id, "membership", pool.id, "added",
        {"kind": "staff", "pool_id": pool.id, "user_id": user_id},
    )
    _commit()


def remove_staff_member(pool_id: str, user_id: int, *, actor_id: int) -> None:
    actor_id = _actor(actor_id)
    pool = _staff_pool(pool_id)
    row = GroupMembership.query.filter_by(group_id=pool.group_id, user_id=user_id).first()
    if row is None:
        raise PoolError("not_a_member")
    db.session.delete(row)
    _history(
        actor_id, "membership", pool.id, "removed",
        {"kind": "staff", "pool_id": pool.id, "user_id": user_id},
    )
    _commit()


def link_staff_pool(author_pool_id: str, staff_pool_id: str, *, actor_id: int) -> ClinicPoolRoute:
    actor_id = _actor(actor_id)
    author = _author_pool(author_pool_id)
    staff = _staff_pool(staff_pool_id)
    ensure_routable(author)
    ensure_routable(staff)
    if ClinicPoolRoute.query.filter_by(
        author_pool_id=author.id, staff_pool_id=staff.id
    ).first():
        raise PoolError("already_linked")
    row = ClinicPoolRoute(
        author_pool_id=author.id, staff_pool_id=staff.id, created_by=actor_id
    )
    db.session.add(row)
    db.session.flush()
    _history(
        actor_id, "route", row.id, "added",
        {"author_pool_id": author.id, "staff_pool_id": staff.id},
    )
    _commit()
    return row


POOL_KINDS = ("author", "staff")


def _pool(kind, pool_id):
    if kind not in POOL_KINDS:
        raise PoolError("unknown_kind")
    return _author_pool(pool_id) if kind == "author" else _staff_pool(pool_id)


def is_routable(pool, now=None) -> bool:
    """True when the pool is not deactivated and ``now`` lies inside its validity window."""
    if pool.active is False:
        return False
    now = now or datetime.now(timezone.utc)
    if pool.valid_from is not None and now < pool.valid_from:
        return False
    return pool.valid_to is None or now < pool.valid_to


def ensure_routable(pool, now=None) -> None:
    """Refuse new routing to a deactivated or out-of-validity pool. History and reads are unaffected.

    Limitation: a pool whose ``valid_from`` lies in the future is not routable yet, so a link to or
    from it cannot be prepared ahead of the planned start date (P10 does not define this case).
    """
    if not is_routable(pool, now):
        raise PoolError("pool_inactive")


def routable_staff_pool_ids(author_pool_id: str, now=None) -> list[str]:
    """Ids of the staff pools linked to the author pool that are routable now (active, inside validity).

    For consumers that pick recipients of NEW dictations. Existing dictations, history and read
    rights are unaffected by this filter.
    """
    rows = (
        db.session.query(ClinicStaffPool)
        .join(ClinicPoolRoute, ClinicPoolRoute.staff_pool_id == ClinicStaffPool.id)
        .filter(ClinicPoolRoute.author_pool_id == author_pool_id)
        .order_by(ClinicStaffPool.id)
    )
    return [pool.id for pool in rows if is_routable(pool, now)]


def _stamp(value):
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def pool_state(kind: str, pool) -> dict:
    return {
        "kind": kind,
        "id": pool.id,
        "name": pool.name,
        "active": pool.active is not False,
        "valid_from": _stamp(pool.valid_from),
        "valid_to": _stamp(pool.valid_to),
        "routable": is_routable(pool),
    }


def rename_pool(kind: str, pool_id: str, name: str, *, actor_id: int) -> dict:
    actor_id = _actor(actor_id)
    pool = _pool(kind, pool_id)
    name = _name(name)
    old = pool.name
    if name != old:
        taken = (
            db.session.query(ClinicAuthorPool.id).filter_by(name=name)
            if kind == "author" else db.session.query(Group.id).filter_by(name=name)
        )
        if taken.first():
            raise PoolError("name_taken")
        if kind == "author":
            pool.name = name
        else:
            pool.group.name = name
        _history(actor_id, "pool", pool.id, "renamed", {"kind": kind, "old": old, "new": name})
        _commit()
    return pool_state(kind, pool)


def set_pool_active(kind: str, pool_id: str, active: bool, *, actor_id: int) -> dict:
    """Deactivate or reactivate. Nothing is deleted; dictations and history stay readable."""
    actor_id = _actor(actor_id)
    pool = _pool(kind, pool_id)
    if type(active) is not bool:
        raise PoolError("invalid_active")
    old = pool.active is not False
    if old != active:
        pool.active = active
        _history(
            actor_id, "pool", pool.id, "reactivated" if active else "deactivated",
            {"kind": kind, "old": old, "new": active},
        )
        _commit()
    return pool_state(kind, pool)


def set_pool_validity(kind: str, pool_id: str, valid_from, valid_to, *, actor_id: int) -> dict:
    """Set the validity window; ``None`` leaves a side open. Datetimes must be timezone-aware."""
    actor_id = _actor(actor_id)
    pool = _pool(kind, pool_id)
    for value in (valid_from, valid_to):
        if value is not None and not isinstance(value, datetime):
            raise PoolError("invalid_validity")
        if value is not None and value.tzinfo is None:
            raise PoolError("naive_datetime")
    if valid_from is not None and valid_to is not None and valid_from >= valid_to:
        raise PoolError("invalid_validity")
    old = {"valid_from": _stamp(pool.valid_from), "valid_to": _stamp(pool.valid_to)}
    new = {"valid_from": _stamp(valid_from), "valid_to": _stamp(valid_to)}
    if old != new:
        pool.valid_from, pool.valid_to = valid_from, valid_to
        _history(actor_id, "pool", pool.id, "validity_set", {"kind": kind, "old": old, "new": new})
        _commit()
    return pool_state(kind, pool)


def usage_preview(kind: str, pool_id: str) -> dict:
    """Counts only: open dictations, members and links. No names, no patient data."""
    from .workflow import DONE, TO_ARCHIVE, TO_DELETE, ClinicWorkflowDictation

    pool = _pool(kind, pool_id)
    if kind == "author":
        author_ids = [pool.id]
        members = ClinicAuthorPoolMember.query.filter_by(pool_id=pool.id).count()
        links = ClinicPoolRoute.query.filter_by(author_pool_id=pool.id).count()
    else:
        author_ids = [
            row[0] for row in db.session.query(ClinicPoolRoute.author_pool_id).filter_by(staff_pool_id=pool.id)
        ]
        members = GroupMembership.query.filter_by(group_id=pool.group_id).count()
        links = len(author_ids)
    open_count = (
        ClinicWorkflowDictation.query.filter(
            ClinicWorkflowDictation.pool_id.in_(author_ids),
            ClinicWorkflowDictation.status.notin_((DONE, TO_ARCHIVE, TO_DELETE)),
        ).count()
        if author_ids else 0
    )
    return {"kind": kind, "pool_id": pool.id, "open_dictations": open_count, "members": members, "links": links}


def add_resource(author_pool_id: str, kind: str, name: str, *, actor_id: int) -> ClinicPoolResource:
    actor_id = _actor(actor_id)
    pool = _author_pool(author_pool_id)
    if kind not in RESOURCE_KINDS:
        raise PoolError("unknown_kind")
    name = _name(name)
    row = ClinicPoolResource(
        author_pool_id=pool.id, kind=kind, name=name, created_by=actor_id
    )
    db.session.add(row)
    db.session.flush()
    _history(
        actor_id, "resource", row.id, "added",
        {"author_pool_id": pool.id, "kind": kind},
    )
    _commit()
    return row


def resources_for(user_id: int) -> list[dict]:
    """Resources of author pools this user belongs to. Staff membership does not count."""
    rows = (
        ClinicPoolResource.query.join(
            ClinicAuthorPoolMember,
            ClinicAuthorPoolMember.pool_id == ClinicPoolResource.author_pool_id,
        )
        .filter(ClinicAuthorPoolMember.user_id == user_id)
        .order_by(ClinicPoolResource.id)
        .all()
    )
    return [
        {
            "id": row.id,
            "author_pool_id": row.author_pool_id,
            "kind": row.kind,
            "name": row.name,
        }
        for row in rows
    ]


def _normalise_actions(actions):
    if actions is None:
        return None
    if isinstance(actions, str) or not hasattr(actions, "__iter__"):
        raise PoolError("actions_type")
    picked = []
    for action in actions:
        if action not in _ACTION_SET or action in picked:
            raise PoolError("unknown_action" if action not in _ACTION_SET else "duplicate_action")
        picked.append(action)
    if not picked:
        raise PoolError("actions_empty")
    picked.sort()
    return ",".join(picked)


def _bump_version() -> int:
    row = db.session.get(ClinicGrantVersion, 1)
    if row is None:
        row = ClinicGrantVersion(id=1, version=0)
        db.session.add(row)
    row.version = row.version + 1
    db.session.flush()
    return row.version


def grant_version() -> int:
    """Current stored grant version (0 before any change)."""
    row = db.session.get(ClinicGrantVersion, 1, populate_existing=True)
    return 0 if row is None else row.version


def _authors(authors, scope):
    if authors is None:
        return ()
    if isinstance(authors, (str, bytes)) or not hasattr(authors, "__iter__"):
        raise PoolError("authors_type")
    if any(type(a) is not int for a in authors):
        raise PoolError("authors_type")
    picked = sorted({_known_user(a) for a in authors})
    if picked and scope == "own":
        raise PoolError("authors_forbidden")
    return tuple(picked)


def _add_grant(user_id, effect, scope, *, actor_id, pool_id, actions, authors=None):
    """Stage one grant, its author rows, history and a version bump. Does not commit."""
    actor_id = _actor(actor_id, "manage_grants")
    user_id = _known_user(user_id)
    if scope not in SCOPES:
        raise PoolError("unknown_scope")
    if effect not in EFFECTS:
        raise PoolError("unknown_effect")
    if scope == "pool":
        pool_id = _author_pool(pool_id).id
    elif pool_id is not None:
        raise PoolError("pool_forbidden")
    else:
        pool_id = None
    stored = _normalise_actions(actions)
    author_ids = _authors(authors, scope)
    row = ClinicVisibilityGrant(
        user_id=user_id,
        effect=effect,
        scope=scope,
        pool_id=pool_id,
        actions=stored,
        created_by=actor_id,
    )
    db.session.add(row)
    db.session.flush()
    for author_id in author_ids:
        db.session.add(ClinicGrantAuthor(grant_id=row.id, author_user_id=author_id))
    detail = {
        "user_id": user_id,
        "effect": effect,
        "scope": scope,
        "pool_id": pool_id,
        "actions": stored,
    }
    if author_ids:
        detail["author_user_ids"] = list(author_ids)
    _history(actor_id, "grant", row.id, "added", detail)
    _bump_version()
    return row


def _grant(user_id, effect, scope, *, actor_id, pool_id, actions):
    row = _add_grant(user_id, effect, scope, actor_id=actor_id, pool_id=pool_id, actions=actions)
    _commit()
    return row


def allow(user_id: int, scope: str, *, actor_id: int, pool_id: str | None = None, actions=None) -> ClinicVisibilityGrant:
    """Grant actions. ``actions`` None means all of ``ACTIONS`` (not ``SEPARATE_ACTIONS``)."""
    return _grant(user_id, "allow", scope, actor_id=actor_id, pool_id=pool_id, actions=actions)


def deny(user_id: int, scope: str, *, actor_id: int, pool_id: str | None = None, actions=None) -> ClinicVisibilityGrant:
    """Deny actions. A matching deny wins over own, grants and staff links."""
    return _grant(user_id, "deny", scope, actor_id=actor_id, pool_id=pool_id, actions=actions)


def set_author_scope(
    user_id: int, *, actor_id: int, explicit_users=(), deny_user_ids=(),
    pool_id: str | None = None, actions=None,
) -> list[ClinicVisibilityGrant]:
    """Per-author scope on top of all authors (or one pool when ``pool_id`` is given).

    ``explicit_users`` get an allow limited to exactly those authors; ``deny_user_ids``
    get a deny for those authors, which wins over every allow ("pool A except Beta").
    Both rows and the version bump commit together.
    """
    scope = "all" if pool_id is None else "pool"
    explicit, denied = tuple(explicit_users), tuple(deny_user_ids)
    if not explicit and not denied:
        raise PoolError("authors_empty")
    rows = []
    for effect, authors in (("allow", explicit), ("deny", denied)):
        if authors:
            rows.append(_add_grant(
                user_id, effect, scope, actor_id=actor_id, pool_id=pool_id,
                actions=actions, authors=authors,
            ))
    _commit()
    return rows


def revoke(grant_id: str, *, actor_id: int) -> None:
    """Remove an allow or deny. Writes a history row and bumps the stored version."""
    actor_id = _actor(actor_id, "manage_grants")
    row = db.session.get(ClinicVisibilityGrant, grant_id)
    if row is None:
        raise PoolError("unknown_grant")
    author_rows = ClinicGrantAuthor.query.filter_by(grant_id=row.id).order_by(ClinicGrantAuthor.author_user_id).all()
    detail = {
        "user_id": row.user_id,
        "effect": row.effect,
        "scope": row.scope,
        "pool_id": row.pool_id,
        "actions": row.actions,
    }
    if author_rows:
        detail["author_user_ids"] = [a.author_user_id for a in author_rows]
    for author in author_rows:
        db.session.delete(author)
    db.session.delete(row)
    _history(actor_id, "grant", grant_id, "revoked", detail)
    _bump_version()
    _commit()


def _actions_of(grant):
    if not grant.actions:
        return GRANTABLE_ACTIONS if grant.effect == "deny" else ACTIONS
    have = set(grant.actions.split(","))
    return tuple(action for action in GRANTABLE_ACTIONS if action in have)


def _covers(grant, user_id, author_id, pool_id, authors=frozenset()):
    if authors and author_id not in authors:
        return False
    if grant.scope == "all":
        return True
    if grant.scope == "own":
        return user_id == author_id
    if grant.scope == "pool":
        return pool_id is not None and grant.pool_id == pool_id
    return False


def _staff_link_id(user_id, author_pool_id):
    row = (
        db.session.query(ClinicPoolRoute.id)
        .join(ClinicStaffPool, ClinicStaffPool.id == ClinicPoolRoute.staff_pool_id)
        .join(GroupMembership, GroupMembership.group_id == ClinicStaffPool.group_id)
        .filter(
            ClinicPoolRoute.author_pool_id == author_pool_id,
            GroupMembership.user_id == user_id,
        )
        .order_by(ClinicPoolRoute.id)
        .first()
    )
    return None if row is None else row[0]


def ui_role(user_id) -> str:
    """UI hint for the clinic frontend: ``admin``, ``typist`` or ``author``.

    Display only. It never grants access; the server narrows rows and checks
    permissions on its own. Unknown or inactive users get ``author``. A user who
    is both author and typist gets ``typist``.
    """
    from src.clinic.admin_dictation_access import _is_active_site_admin  # lazy: avoids an import cycle
    from src.clinic.identity import ClinicAccountPolicy

    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return "author"
    if user is None:
        return "author"
    account = db.session.get(ClinicAccountPolicy, user.id)
    if account is None or account.status != "active":
        return "author"
    if _is_active_site_admin(db.session, user):
        return "admin"
    is_typist = (
        db.session.query(ClinicStaffPool.id)
        .join(GroupMembership, GroupMembership.group_id == ClinicStaffPool.group_id)
        .filter(GroupMembership.user_id == user.id)
        .first()
    )
    return "typist" if is_typist else "author"


def grants_for(user_id: int, dictation) -> dict:
    """Allowed actions plus matching denies. Most specific reason wins; denies win.

    Reasons are ``own``, ``pool`` (grant_id), ``all`` (grant_id), ``staff_link``
    (link_id). An administrator flag is not consulted.
    """
    if dictation is None or not hasattr(dictation, "author_user_id"):
        raise PoolError("dictation_required")
    author_id = dictation.author_user_id
    pool_id = dictation.pool_id
    chosen = {}

    def consider(action, reason, rank):
        current = chosen.get(action)
        if current is None or rank > current[0]:
            chosen[action] = (rank, reason)

    if user_id == author_id:
        for action in OWN_ACTIONS:
            consider(action, {"reason": "own"}, _SCOPE_RANK["own"])

    grants = (
        ClinicVisibilityGrant.query.filter_by(user_id=user_id)
        .order_by(ClinicVisibilityGrant.id)
        .all()
    )
    limits = {}
    if grants:
        for link in ClinicGrantAuthor.query.filter(
            ClinicGrantAuthor.grant_id.in_([g.id for g in grants])
        ):
            limits.setdefault(link.grant_id, set()).add(link.author_user_id)
    denies = []
    denied = set()
    for grant in grants:
        if not _covers(grant, user_id, author_id, pool_id, limits.get(grant.id, frozenset())):
            continue
        actions = _actions_of(grant)
        if grant.effect == "deny":
            denied.update(actions)
            denies.append({
                "grant_id": grant.id,
                "scope": grant.scope,
                "pool_id": grant.pool_id,
                "actions": list(actions),
            })
            continue
        reason = {"reason": grant.scope, "grant_id": grant.id}
        for action in actions:
            consider(action, reason, _SCOPE_RANK[grant.scope])

    if pool_id is not None:
        link_id = _staff_link_id(user_id, pool_id)
        if link_id is not None:
            for action in STAFF_LINK_ACTIONS:
                consider(action, {"reason": "staff_link", "link_id": link_id}, 0)

    allowed = {}
    for action in GRANTABLE_ACTIONS:
        if action in chosen and action not in denied:
            allowed[action] = chosen[action][1]
    return {"allowed": allowed, "denies": denies}


def history() -> list[dict]:
    rows = ClinicPoolHistory.query.order_by(
        ClinicPoolHistory.happened_at, ClinicPoolHistory.id
    ).all()
    return [
        {
            "id": row.id,
            "happened_at": row.happened_at,
            "actor_user_id": row.actor_user_id,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "change": row.change,
            "detail": json.loads(row.detail),
        }
        for row in rows
    ]


policy_provider = grants_for  # central policy imports this; no policy module yet
