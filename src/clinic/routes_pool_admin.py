"""Pool administration API (V8 P10/P11): create, rename, link, deactivate, validity, usage preview.

A thin layer over ``pools``. The actor is always ``current_user``; a body never names an actor or
role. Writes need an active site admin or the ``manage_pools`` grant (``pools._actor``). Every change
is audited by the pool service in the append-only pool history with the old and the new value. A pool
is never deleted: deactivation only refuses new routing, existing dictations and history stay readable.
The usage preview is also open to a lead of the staff pool concerned (W13) and returns counts only.

``register_clinic_identity`` mounts ``blueprint``; its endpoints are AUTH rows in ``route_policy`` because
the handlers decide admin, manager or lead themselves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import pools, worklist_entries
from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.models import GroupMembership

PREFIX = "/api/clinic/v1/pool-admin"
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "role", "roles", "is_admin", "created_by",
})
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "forbidden": 403, "unknown_actor": 403,
    "not_author_pool": 404, "not_staff_pool": 404, "unknown_kind": 404, "not_a_member": 404,
    "name_taken": 409, "conflict": 409, "already_linked": 409, "pool_inactive": 409,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Konflikt"}


def _problem(code: str, status: int | None = None):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code)
    response.mimetype = "application/problem+json"
    return response, status


def _is_manager(user_id: int) -> bool:
    try:
        pools._actor(user_id)
    except pools.PoolError:
        return False
    return True


def _is_lead(user_id: int, kind: str, pool_id: str) -> bool:
    """True for a lead of the staff pool, or of any staff pool the author pool is routed to."""
    from src.clinic.identity import ClinicAccountPolicy

    account = db.session.get(ClinicAccountPolicy, user_id, populate_existing=True)
    if account is not None and account.status != "active":
        return False
    if kind == "author":
        return pool_id in worklist_entries._led_author_pools(user_id)
    pool = db.session.get(pools.ClinicStaffPool, pool_id)
    return pool is not None and db.session.query(GroupMembership.id).filter(
        GroupMembership.user_id == user_id, GroupMembership.role == worklist_entries.LEAD_ROLE,
        GroupMembership.group_id == pool.group_id,
    ).first() is not None


def list_state() -> dict[str, Any]:
    out: dict[str, Any] = {"author_pools": [], "staff_pools": []}
    for kind, model in (("author", pools.ClinicAuthorPool), ("staff", pools.ClinicStaffPool)):
        order = model.name if kind == "author" else model.id
        out[f"{kind}_pools"] = [pools.pool_state(kind, p) for p in db.session.query(model).order_by(order)]
    return out


def _body(allowed: set[str], required: set[str]):
    body = request.get_json(silent=True)
    if (not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys() or body.keys() - allowed
            or required - body.keys()):
        return None
    return body


def _stamp_in(value: Any):
    """None, or an ISO 8601 datetime with an offset. Anything else raises ``PoolError``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise pools.PoolError("invalid_validity")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise pools.PoolError("invalid_validity") from None


blueprint = Blueprint("clinic_pool_admin", __name__)


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


@blueprint.put(PREFIX + "/pools/staff/<pool_id>/members/<int:user_id>/role")
def set_member_role(pool_id: str, user_id: int):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"role"}:
        return _problem("invalid_payload")
    try:
        return jsonify(pools.set_staff_member_role(
            pool_id, user_id, body["role"], actor_id=current_user.id))
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.get(PREFIX + "/pools")
def list_pools():
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    return jsonify(list_state())


@blueprint.post(PREFIX + "/pools")
def create_pool():
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    body = _body({"kind", "name"}, {"kind", "name"})
    if body is None:
        return _problem("invalid_payload")
    try:
        if body["kind"] not in pools.POOL_KINDS:
            raise pools.PoolError("unknown_kind")
        make = pools.create_author_pool if body["kind"] == "author" else pools.create_staff_pool
        pool = make(body["name"], actor_id=current_user.id)
        return jsonify(pools.pool_state(body["kind"], pool)), 201
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.put(PREFIX + "/pools/<kind>/<pool_id>/name")
def rename(kind: str, pool_id: str):
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    body = _body({"name"}, {"name"})
    if body is None:
        return _problem("invalid_payload")
    try:
        return jsonify(pools.rename_pool(kind, pool_id, body["name"], actor_id=current_user.id))
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.put(PREFIX + "/pools/<kind>/<pool_id>/active")
def set_active(kind: str, pool_id: str):
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    body = _body({"active"}, {"active"})
    if body is None:
        return _problem("invalid_payload")
    try:
        return jsonify(pools.set_pool_active(kind, pool_id, body["active"], actor_id=current_user.id))
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.put(PREFIX + "/pools/<kind>/<pool_id>/validity")
def set_validity(kind: str, pool_id: str):
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    body = _body({"valid_from", "valid_to"}, {"valid_from", "valid_to"})
    if body is None:
        return _problem("invalid_payload")
    try:
        state = pools.set_pool_validity(
            kind, pool_id, _stamp_in(body["valid_from"]), _stamp_in(body["valid_to"]),
            actor_id=current_user.id)
        return jsonify(state)
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.post(PREFIX + "/pools/author/<pool_id>/links")
def link(pool_id: str):
    if not _is_manager(current_user.id):
        return _problem("forbidden")
    body = _body({"staff_pool_id"}, {"staff_pool_id"})
    if body is None or not isinstance(body["staff_pool_id"], str):
        return _problem("invalid_payload")
    try:
        route = pools.link_staff_pool(pool_id, body["staff_pool_id"], actor_id=current_user.id)
        return jsonify(id=route.id, author_pool_id=route.author_pool_id,
                       staff_pool_id=route.staff_pool_id), 201
    except pools.PoolError as error:
        return _problem(error.code)


@blueprint.get(PREFIX + "/pools/<kind>/<pool_id>/usage")
def usage(kind: str, pool_id: str):
    if kind not in pools.POOL_KINDS:
        return _problem("unknown_kind")
    if not (_is_manager(current_user.id) or _is_lead(current_user.id, kind, pool_id)):
        return _problem("forbidden")
    try:
        return jsonify(pools.usage_preview(kind, pool_id))
    except pools.PoolError as error:
        return _problem(error.code)
