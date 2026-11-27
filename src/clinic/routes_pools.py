"""HTTP surface for author and staff pool membership (PAR-ADM-002, PAR-ADM-003).

A thin layer over ``pools``. The actor is always ``current_user``; a body never
names an actor or role. Writes need an active admin or the ``manage_pools`` grant
(``pools._actor``) and carry ``expected_revision``; a stale revision gives 409.
The revision is the number of pool history rows: every committed change appends
one, so it only grows. Author-resource pools and staff pools stay separate kinds.

Mounting ``blueprint`` in the app is left to the consumer packages (w196, w084).

shortcut: a save applies its membership changes one service call at a time, each
committed with its own history row; a failure midway leaves the earlier changes.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import pools
from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.models import GroupMembership

PREFIX = "/api/clinic/v1/pools"
KINDS = ("author", "staff")
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "role", "roles", "is_admin", "created_by",
})
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "forbidden": 403, "unknown_actor": 403,
    "not_author_pool": 404, "not_staff_pool": 404, "unknown_kind": 404,
    "stale_revision": 409, "name_taken": 409, "conflict": 409,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Versionskonflikt"}


def revision() -> int:
    """Current pool revision: the count of append-only history rows."""
    return db.session.query(pools.ClinicPoolHistory).count()


def _members(kind: str, pool) -> list[int]:
    if kind == "author":
        rows = db.session.query(pools.ClinicAuthorPoolMember.user_id).filter_by(pool_id=pool.id)
    else:
        rows = db.session.query(GroupMembership.user_id).filter_by(group_id=pool.group_id)
    return sorted(row[0] for row in rows)


def _is_manager(user_id: int) -> bool:
    try:
        pools._actor(user_id)
    except pools.PoolError:
        return False
    return True


def list_state(user_id: int) -> dict[str, Any]:
    """Pools for ``user_id``. Managers see all members; others only pools they are in, with themselves."""
    manager = _is_manager(user_id)
    out: dict[str, Any] = {"revision": revision(), "author_pools": [], "staff_pools": []}
    routed: dict[str, list[str]] = {}
    if manager:
        for author_id, staff_id in db.session.query(
                pools.ClinicPoolRoute.author_pool_id, pools.ClinicPoolRoute.staff_pool_id
        ).order_by(pools.ClinicPoolRoute.id):
            routed.setdefault(author_id, []).append(staff_id)
    for kind, model in (("author", pools.ClinicAuthorPool), ("staff", pools.ClinicStaffPool)):
        order = model.name if kind == "author" else model.id
        for pool in db.session.query(model).order_by(order).all():
            members = _members(kind, pool)
            if not manager:
                if user_id not in members:
                    continue
                members = [user_id]
            state = {"id": pool.id, "name": pool.name, "member_user_ids": members}
            if kind == "author" and manager:
                state["staff_pool_ids"] = routed.get(pool.id, [])
            if kind == "staff":
                state["members"] = [member for member in pools.staff_member_roles(pool.id)
                                    if manager or member["user_id"] == user_id]
            out[f"{kind}_pools"].append(state)
    return out


def save_pool(kind: str, pool_id: str, name: Any, member_ids: Any, expected_revision: Any, *, actor_id: int) -> None:
    """Set name and full membership of one pool. Raises ``pools.PoolError``."""
    actor_id = pools._actor(actor_id)
    if kind not in KINDS:
        raise pools.PoolError("unknown_kind")
    if type(expected_revision) is not int or expected_revision < 0:
        raise pools.PoolError("invalid_revision")
    if not isinstance(member_ids, list) or len(set(member_ids)) != len(member_ids) or any(
            type(m) is not int or m < 1 for m in member_ids):
        raise pools.PoolError("invalid_members")
    pool = pools._author_pool(pool_id) if kind == "author" else pools._staff_pool(pool_id)
    if expected_revision != revision():
        raise pools.PoolError("stale_revision")
    name = pools._name(name)
    for user_id in member_ids:
        pools._known_user(user_id)
    current = set(_members(kind, pool))
    wanted = set(member_ids)
    if name != pool.name:
        _rename(kind, pool, name, actor_id)
    add, remove = (pools.add_author_member, pools.remove_author_member) if kind == "author" else (
        pools.add_staff_member, pools.remove_staff_member)
    for user_id in sorted(wanted - current):
        add(pool.id, user_id, actor_id=actor_id)
    for user_id in sorted(current - wanted):
        remove(pool.id, user_id, actor_id=actor_id)


def _rename(kind: str, pool, name: str, actor_id: int) -> None:
    if kind == "author":
        if db.session.query(pools.ClinicAuthorPool.id).filter_by(name=name).first():
            raise pools.PoolError("name_taken")
        pool.name = name
    else:
        if db.session.query(pools.Group.id).filter_by(name=name).first():
            raise pools.PoolError("name_taken")
        pool.group.name = name
    pools._history(actor_id, "pool", pool.id, "renamed", {"kind": kind, "name": name})
    pools._commit()


def _problem(code: str, status: int | None = None):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code)
    response.mimetype = "application/problem+json"
    return response, status


blueprint = Blueprint("clinic_pools", __name__)


@blueprint.before_request
def _guard():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
        return _problem("unauthenticated")
    if request.method != "GET":
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except ValidationError:
            return _problem("unauthorized")


blueprint.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))


@blueprint.get(PREFIX)
def list_pools():
    return jsonify(list_state(current_user.id))


@blueprint.put(PREFIX + "/<kind>/<pool_id>")
def put_pool(kind: str, pool_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys() or body.keys() - {
            "expected_revision", "name", "member_user_ids"}:
        return _problem("invalid_payload")
    try:
        save_pool(kind, pool_id, body.get("name"), body.get("member_user_ids"),
                  body.get("expected_revision"), actor_id=current_user.id)
    except pools.PoolError as error:
        return _problem(error.code)
    return jsonify(list_state(current_user.id))
