"""Read and change explicit action grants and deny exceptions (PAR-ADM-004/005/008).

Thin HTTP layer over ``src.clinic.pools``: grants live in
``ClinicVisibilityGrant`` and every change is audited by the pool service in
the append-only ``ClinicPoolHistory``. ``pool_policy.decide`` reads grants
live, so a change applies to the next protected request.

Rules:

* The actor always comes from the session. The body never names an actor or
  role.
* Only an active site administrator may change grants. Identity admins,
  statistics accounts and every other user are refused, so none of them can
  give themselves clinical access.
* Reading a grant list needs an active site or identity admin, or the user's
  own id (identity-web contract, ``getClinicUserGrants``).
* A change names its actions explicitly (no implicit "all actions") and must
  send the revision it last read. A stale or replayed change gets 409.
* A refused request writes nothing.

All users share the pool service's stored grant revision.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy import text
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic import pools
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.database import db
from src.models import User

logger = logging.getLogger(__name__)

PATH = "/api/clinic/v1/identity/users/<int:user_id>/grants"
WRITE_KEYS = frozenset({"expected_revision", "effect", "scope", "pool_id", "actions"})


def grant_list(user_id: int, *, include_ids: bool = False) -> dict[str, Any]:
    """GrantList of the identity-web contract."""
    revision = pools.grant_version()
    authors = defaultdict(list)
    if include_ids:
        restrictions = (db.session.query(pools.ClinicGrantAuthor.grant_id, User.username)
                        .join(User, User.id == pools.ClinicGrantAuthor.author_user_id)
                        .join(pools.ClinicVisibilityGrant,
                              pools.ClinicVisibilityGrant.id == pools.ClinicGrantAuthor.grant_id)
                        .filter(pools.ClinicVisibilityGrant.user_id == user_id)
                        .order_by(User.username))
        for grant_id, username in restrictions:
            authors[grant_id].append(username)
    rows = (
        pools.ClinicVisibilityGrant.query.filter_by(user_id=user_id)
        .order_by(pools.ClinicVisibilityGrant.created_at, pools.ClinicVisibilityGrant.id)
        .all()
    )
    items = []
    for row in rows:
        item = {"effect": row.effect, "scope": row.scope, "actions": list(pools._actions_of(row))}
        if include_ids:
            item["id"] = row.id
            if row.id in authors:
                item["author_names"] = authors[row.id]
        if row.pool_id is not None:
            item["pool_id"] = row.pool_id
        items.append(item)
    return {"user_id": user_id, "revision": revision, "items": items}


def _is_active_identity_admin(user: User) -> bool:
    account = db.session.get(ClinicAccountPolicy, user.id)
    return account is not None and account.status == "active" and account.is_identity_admin


def _parse_change(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or body.keys() - WRITE_KEYS:
        raise CommandProblem("INVALID_PAYLOAD")
    expected = body.get("expected_revision")
    actions = body.get("actions")
    if (type(expected) is not int or expected < 0 or body.get("effect") not in pools.EFFECTS
            or not isinstance(body.get("scope"), str) or not isinstance(body.get("pool_id"), (str, type(None)))
            or not isinstance(actions, list) or not actions or not all(isinstance(a, str) for a in actions)):
        raise CommandProblem("INVALID_PAYLOAD")
    if body["scope"] == "pool" and body.get("pool_id") is None:
        raise CommandProblem("INVALID_PAYLOAD")
    return body


class GrantsApi:
    """Mount ``blueprint`` once; registration in app.py belongs to a consumer package."""

    def __init__(self):
        self.blueprint = Blueprint("clinic_grants", __name__)
        self.blueprint.add_url_rule(PATH, "read", self._read, methods=["GET"])
        self.blueprint.add_url_rule(PATH, "change", self._change, methods=["POST"])
        self.blueprint.add_url_rule(PATH + "/<uuid:grant_id>", "change", self._change, methods=["DELETE"])
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))

    @staticmethod
    def _actor():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return None
        return current_user._get_current_object()

    def _read(self, user_id: int):
        actor = self._actor()
        if actor is None:
            return _problem("UNAUTHORIZED")
        if not (actor.id == user_id or _is_active_site_admin(db.session, actor)
                or _is_active_identity_admin(actor)):
            return _problem("FORBIDDEN")
        if db.session.get(User, user_id) is None:
            return _not_found()
        edit_entries = request.args.get("entries") == "1" and _is_active_site_admin(db.session, actor)
        return jsonify(grant_list(user_id, include_ids=edit_entries))

    def _change(self, user_id: int, grant_id=None):
        actor = self._actor()
        if actor is None:
            return _problem("UNAUTHORIZED")
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except CsrfValidationError:
            return _problem("FORBIDDEN")
        if not _is_active_site_admin(db.session, actor):
            logger.warning("clinic grants: change refused for actor %s on user %s", actor.id, user_id)
            return _problem("FORBIDDEN")
        try:
            body = request.get_json(silent=True)
            if grant_id is None:
                body = _parse_change(body)
            elif (not isinstance(body, dict) or body.keys() != {"expected_revision"}
                  or type(body["expected_revision"]) is not int or body["expected_revision"] < 0):
                raise CommandProblem("INVALID_PAYLOAD")
            if db.session.get(User, user_id) is None:
                db.session.rollback()
                return _not_found()
            # Lock the shared counter through the service commit, including the first write.
            db.session.execute(text(
                "INSERT INTO clinic_grant_version (id, version) VALUES (1, 0) "
                "ON CONFLICT (id) DO UPDATE SET version = clinic_grant_version.version"
            ))
            if pools.grant_version() != body["expected_revision"]:
                raise CommandProblem("REVISION_CONFLICT")
            if grant_id is not None:
                row = db.session.get(pools.ClinicVisibilityGrant, str(grant_id))
                if row is None or row.user_id != user_id:
                    db.session.rollback()
                    return _not_found()
                pools.revoke(row.id, actor_id=actor.id)
            else:
                change = pools.allow if body["effect"] == "allow" else pools.deny
                change(user_id, body.get("scope"), actor_id=actor.id,
                       pool_id=body.get("pool_id"), actions=body["actions"])
        except CommandProblem as error:
            db.session.rollback()
            return _problem(str(error))
        except pools.PoolError as error:
            db.session.rollback()
            return _problem("REVISION_CONFLICT" if error.code == "conflict" else "INVALID_PAYLOAD")
        except Exception:
            db.session.rollback()
            logger.exception("clinic grants: change failed for user %s", user_id)
            return _problem("DEPENDENCY_UNAVAILABLE")
        return jsonify(grant_list(user_id))


def _not_found():
    response = jsonify(type="about:blank", title="Nicht gefunden", status=404, code="NOT_FOUND")
    response.mimetype = "application/problem+json"
    return response, 404
