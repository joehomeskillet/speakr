"""HTTP surface for category sets and required submission fields (PAR-CFG-003).

A thin layer over ``presets`` (``ClinicCategorySet``, ``validate_category_requirement``).
The actor is always ``current_user``; a body never names an actor, user or role.
Visible sets are system sets, sets of the caller's current author pools and the
caller's own user sets, read on every request. A submission check is read-only:
it validates every visible required set before the caller changes anything, so a
stale or omitted mandatory category leaves the workflow untouched.

Active site administrators create and replace sets with POST and PUT on
``PREFIX`` (the required flag included). The send route calls
``check_submission`` before it changes a dictation and copies the chosen
category labels into ``Recording.notes``.
"""

from __future__ import annotations

import json
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
_WRITE_KEYS = frozenset({"name", "categories", "required", "is_required", "scope", "scope_id", "target_field"})
_SCOPES = frozenset({"system", "pool", "user"})
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


def selection_notes(user_id: int, selections: list) -> str:
    """Canonical labels satisfied by ``selections``, one per line, for Recording.notes."""
    visible = {row.id: row for row in visible_sets(user_id)}
    lines: list[str] = []
    for item in selections:
        if not isinstance(item, dict):
            continue
        row = visible.get(item.get("set_id"))
        value = item.get("value")
        if row is None or not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        lines.extend(cat for cat in row.categories if cat.lower() in cleaned)
    return "\n".join(lines)


def _active_admin() -> bool:
    from src.clinic.admin_dictation_access import _is_active_site_admin
    return _is_active_site_admin(db.session, current_user)


def _read_body(allowed: frozenset[str]) -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or (_FORBIDDEN_KEYS & body.keys()) or (set(body) - allowed):
        raise CategoryError("invalid_payload")
    return body


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise CategoryError("invalid_payload")
    name = value.strip()
    if not name or len(name) > 128:
        raise CategoryError("invalid_payload")
    return name


def _category_names(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise CategoryError("invalid_payload")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise CategoryError("invalid_payload")
        name = item.strip()
        if not name or len(name) > 128:
            raise CategoryError("invalid_payload")
        names.append(name)
    if len(set(names)) != len(names):
        raise CategoryError("invalid_payload")
    return names


def _required_flag(body: dict) -> bool:
    if "required" in body and "is_required" in body and body["required"] is not body["is_required"]:
        raise CategoryError("invalid_payload")
    flag = body["required"] if "required" in body else body["is_required"]
    if type(flag) is not bool:
        raise CategoryError("invalid_payload")
    return flag


def _scope(body: dict, current: presets.ClinicCategorySet | None) -> tuple[str, str]:
    scope = body.get("scope", current.scope if current is not None else "system")
    if scope not in _SCOPES:
        raise CategoryError("invalid_payload")
    if scope == "system":
        if "scope_id" in body and body["scope_id"] not in (None, presets.SYSTEM_SCOPE_ID):
            raise CategoryError("invalid_payload")
        return scope, presets.SYSTEM_SCOPE_ID
    if "scope_id" in body:
        scope_id = body["scope_id"]
    elif current is not None and current.scope == scope:
        scope_id = current.scope_id
    else:
        scope_id = None
    if not isinstance(scope_id, str) or not scope_id.strip() or len(scope_id.strip()) > 36:
        raise CategoryError("invalid_payload")
    return scope, scope_id.strip()


def _target(body: dict, current: str | None) -> str:
    if "target_field" not in body:
        return current or "notes"
    if body["target_field"] != "notes":
        raise CategoryError("invalid_payload")
    return "notes"


def _parsed(body: dict, current: presets.ClinicCategorySet | None) -> dict[str, Any]:
    if "name" in body:
        name = _name(body["name"])
    elif current is not None:
        name = current.name
    else:
        raise CategoryError("invalid_payload")
    categories = _category_names(body["categories"]) if "categories" in body else (
        list(current.categories) if current is not None else None)
    if categories is None:
        raise CategoryError("invalid_payload")
    if "required" in body or "is_required" in body:
        required = _required_flag(body)
    elif current is not None:
        required = bool(current.is_required)
    else:
        required = False
    scope, scope_id = _scope(body, current)
    return {"name": name, "categories": categories, "is_required": required, "scope": scope,
            "scope_id": scope_id, "target_field": _target(body, current.target_field if current else None)}


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
            validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
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


def _refuse_unless_admin():
    if not _active_admin():
        return _problem("unauthorized")
    return None


@blueprint.post(PREFIX)
def create_category_set():
    refused = _refuse_unless_admin()
    if refused is not None:
        return refused
    try:
        body = _read_body(_WRITE_KEYS)
        if "name" not in body or "categories" not in body:
            raise CategoryError("invalid_payload")
        parsed = _parsed(body, None)
        row = presets.save_category_set(
            parsed["name"], parsed["categories"], is_required=parsed["is_required"],
            scope=parsed["scope"], scope_id=parsed["scope_id"], target_field=parsed["target_field"])
        db.session.commit()
    except CategoryError as error:
        db.session.rollback()
        return _problem(error.code, **error.extra)
    return jsonify(set_json(row)), 201


@blueprint.put(PREFIX)
def update_category_set():
    refused = _refuse_unless_admin()
    if refused is not None:
        return refused
    try:
        body = _read_body(_WRITE_KEYS | {"id", "version"})
        set_id, version = body.get("id"), body.get("version")
        if not isinstance(set_id, str) or not set_id or type(version) is not int:
            raise CategoryError("invalid_payload")
        row = (db.session.query(presets.ClinicCategorySet).filter_by(id=set_id)
               .with_for_update().one_or_none())
        if row is None or not row.is_active:
            raise CategoryError("unknown_category_set")
        if version != row.version:
            raise CategoryError("stale_version", set_id=set_id, current_version=row.version)
        if not (_WRITE_KEYS & body.keys()):
            raise CategoryError("invalid_payload")
        parsed = _parsed(body, row)
        row.name = parsed["name"]
        row.categories_json = json.dumps(parsed["categories"])
        row.is_required = parsed["is_required"]
        row.scope = parsed["scope"]
        row.scope_id = parsed["scope_id"]
        row.target_field = parsed["target_field"]
        row.version += 1
        db.session.commit()
    except CategoryError as error:
        db.session.rollback()
        return _problem(error.code, **error.extra)
    return jsonify(set_json(row))
