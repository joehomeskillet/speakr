"""HTTP surface for category sets and required submission fields (PAR-CFG-003).

A thin layer over ``presets`` (``ClinicCategorySet``, ``validate_category_requirement``).
The actor is always ``current_user``; a body never names an actor, user or role.
Visible sets are system sets, sets of the caller's current author pools and the
caller's own user sets, read on every request. A submission check is read-only:
it validates every visible required set before the caller changes anything, so a
stale or omitted mandatory category leaves the workflow untouched.

Mounting ``blueprint`` in the app and calling ``check_submission`` from the
submit route are left to the consumer packages (w094, w197).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy import or_
from wtforms.validators import ValidationError

from src.clinic import pool_policy, presets
from src.clinic.config import clinic_mode_enabled
from src.database import db

PREFIX = "/api/clinic/v1/categories"
# Identity and authority come from the session, never from the request.
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "unknown_category_set": 404,
    "stale_version": 409, "invalid_payload": 400,
    "missing_category": 422, "invalid_category": 422,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Versionskonflikt", 422: "Kategorie erforderlich"}


class CategoryError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.extra = code, extra


def visible_sets(user_id: int) -> list[presets.ClinicCategorySet]:
    """Active sets the user may use now: system, own author pools, own user sets."""
    cs = presets.ClinicCategorySet
    conds = [(cs.scope == "system") & (cs.scope_id == presets.SYSTEM_SCOPE_ID),
             (cs.scope == "user") & (cs.scope_id == str(user_id))]
    pool_ids = [str(p) for p in pool_policy.author_pool_ids(user_id)]
    if pool_ids:
        conds.append((cs.scope == "pool") & cs.scope_id.in_(pool_ids))
    return db.session.query(cs).filter(cs.is_active.is_(True), or_(*conds)).order_by(cs.name, cs.id).all()


def set_json(row: presets.ClinicCategorySet) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "version": row.version, "categories": row.categories,
            "required": row.is_required, "target_field": row.target_field, "scope": row.scope}


def check_submission(user_id: int, selections: Any) -> None:
    """Raise ``CategoryError`` unless every visible required set is current and satisfied."""
    if not isinstance(selections, list) or not all(isinstance(s, dict) for s in selections):
        raise CategoryError("invalid_payload")
    visible = {row.id: row for row in visible_sets(user_id)}
    chosen: dict[str, Any] = {}
    for item in selections:
        set_id = item.get("set_id")
        if not isinstance(set_id, str) or set_id in chosen:
            raise CategoryError("invalid_payload")
        row = visible.get(set_id)
        if row is None:
            # Gone, deactivated or foreign sets look the same: no existence leak.
            raise CategoryError("unknown_category_set", set_id=set_id)
        if item.get("version") != row.version:
            raise CategoryError("stale_version", set_id=set_id, current_version=row.version)
        chosen[set_id] = item.get("value")
    for set_id, row in visible.items():
        if not row.is_required:
            if set_id in chosen and chosen[set_id]:
                _validate(row, chosen[set_id])
            continue
        if set_id not in chosen:
            raise CategoryError("missing_category", set_id=set_id, field=row.target_field)
        _validate(row, chosen[set_id])


def _validate(row: presets.ClinicCategorySet, value: Any) -> None:
    if not isinstance(value, str):
        raise CategoryError("invalid_payload")
    probe = presets.ClinicCategorySet(
        name=row.name, categories_json=row.categories_json, is_required=True, target_field=row.target_field)
    try:
        presets.validate_category_requirement(probe, value)
    except presets.ValidationError as error:
        code = "missing_category" if not value.strip() else "invalid_category"
        raise CategoryError(code, set_id=row.id, field=error.field)


def _problem(code: str, status: int | None = None, **extra: Any):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


blueprint = Blueprint("clinic_categories", __name__)


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
def list_categories():
    return jsonify(sets=[set_json(row) for row in visible_sets(current_user.id)])


@blueprint.post(PREFIX + "/check")
def check_categories():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        return _problem("invalid_payload")
    try:
        check_submission(current_user.id, body.get("selections"))
    except CategoryError as error:
        return _problem(error.code, **error.extra)
    return jsonify(ok=True)
