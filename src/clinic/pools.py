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
OWN_ACTIONS = ("view", "play")  # safe own default; other actions need a grant
STAFF_LINK_ACTIONS = ("view",)  # derived from a route plus a current membership
SCOPES = ("own", "pool", "all")
EFFECTS = ("allow", "deny")
RESOURCE_KINDS = ("vocabulary", "template")
_ACTION_SET = frozenset(ACTIONS)
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


class ClinicStaffPool(db.Model):
    __tablename__ = "clinic_staff_pool"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    group_id = db.Column(
        db.Integer, db.ForeignKey("group.id"), nullable=False, unique=True
    )
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts()
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
    ClinicPoolHistory,
)


def create_tables(bind=None) -> None:
    """Create this module's tables if they are missing. Does not touch other models."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in POOL_TABLES],
        checkfirst=True,
    )


def _actor(actor_id):
    if db.session.get(User, actor_id) is None:
        raise PoolError("unknown_actor")
    return actor_id


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
    _history(actor_id, "pool", row.id, "created", {"kind": "author"})
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
    _history(actor_id, "pool", row.id, "created", {"kind": "staff", "group_id": group.id})
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


def _grant(user_id, effect, scope, *, actor_id, pool_id, actions):
    actor_id = _actor(actor_id)
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
    _history(
        actor_id, "grant", row.id, "added",
        {
            "user_id": user_id,
            "effect": effect,
            "scope": scope,
            "pool_id": pool_id,
            "actions": stored,
        },
    )
    _commit()
    return row


def allow(user_id: int, scope: str, *, actor_id: int, pool_id: str | None = None, actions=None) -> ClinicVisibilityGrant:
    """Grant actions. ``actions`` None means all of ``ACTIONS``."""
    return _grant(user_id, "allow", scope, actor_id=actor_id, pool_id=pool_id, actions=actions)


def deny(user_id: int, scope: str, *, actor_id: int, pool_id: str | None = None, actions=None) -> ClinicVisibilityGrant:
    """Deny actions. A matching deny wins over own, grants and staff links."""
    return _grant(user_id, "deny", scope, actor_id=actor_id, pool_id=pool_id, actions=actions)


def _actions_of(grant):
    if not grant.actions:
        return ACTIONS
    have = set(grant.actions.split(","))
    return tuple(action for action in ACTIONS if action in have)


def _covers(grant, user_id, author_id, pool_id):
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
    denies = []
    denied = set()
    for grant in grants:
        if not _covers(grant, user_id, author_id, pool_id):
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
    for action in ACTIONS:
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
