"""HTTP surface for the user's own voice profile consent (OWN-SPK-001, R-034).

A thin layer over ``voice_consent``. The actor is always ``current_user``; the
subject of every operation is that same internal user id. A body never names an
actor, user, patient or role, so neither an administrator nor a client can
consent on behalf of someone else. Only the user's own voice is covered, never
a patient's. Writes need the browser session, a CSRF token and an
``Idempotency-Key``. Responses carry status and history, never embedding bytes.

Routes (import-web contract): ``GET /api/clinic/v1/voice-consent``,
``POST /api/clinic/v1/voice-consent/grant`` and ``.../revoke``.

Retries: a repeated request whose ``expected_consent_id`` is the version just
before the row it already created returns that result again instead of a
conflict. Mounting ``blueprint`` in the app is left to the consumer package
(w199-routes-import).
"""

from __future__ import annotations

import hashlib
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import voice_consent as vc
from src.clinic.config import clinic_mode_enabled

PREFIX = "/api/clinic/v1/voice-consent"
_MAX_STATEMENT = 20000
# Identity and authority come from the session, never from the request.
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "actor_user_id", "user", "user_id", "subject", "subject_id",
    "patient", "patient_id", "author_user_id", "role", "roles", "is_admin", "created_by",
    "on_behalf_of", "target_user_id",
})
_STATUS = {
    "unauthenticated": 401,
    "unauthorized": 403, "not_self": 403, "unknown_user": 403, "invalid_user_id": 403,
    "forbidden_field": 403,
    "stale_consent": 409, "already_granted": 409, "no_active_consent": 409,
    "invalid_payload": 422, "statement_required": 422, "statement_too_long": 422,
    "idempotency_key_required": 400,
    "unsupported_consent_transaction": 503, "append_only": 503, "consent_unavailable": 503,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           409: "Versionskonflikt", 422: "Eingabe nicht zulässig", 503: "Dienst nicht verfügbar"}

blueprint = Blueprint("clinic_voice_consent", __name__)


def _problem(code: str, status: int | None = None):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code)
    response.mimetype = "application/problem+json"
    return response, status


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
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 255:
            return _problem("idempotency_key_required")


@blueprint.errorhandler(CSRFError)
def _csrf_error(_error):
    return _problem("unauthorized")


def _view(user_id: int) -> dict[str, Any]:
    state = vc.consent_state(user_id)
    history = vc.consent_history(user_id)
    return {
        "state": state.state,
        "latest_consent_id": history[-1]["id"] if history else 0,
        "embedding_count": vc.embedding_count(user_id),
        "granted_at": state.granted_at.isoformat() if state.granted_at else None,
        "revoked_at": state.revoked_at.isoformat() if state.revoked_at else None,
    }


def _history_json(user_id: int) -> list[dict[str, Any]]:
    return [
        {"id": r["id"], "status": r["status"], "statement_hash": r["statement_hash"],
         "purpose": r["purpose"], "created_at": r["created_at"].isoformat()}
        for r in vc.consent_history(user_id)
    ]


def _body(required: dict[str, type]) -> dict[str, Any] | tuple[Any, int]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _problem("invalid_payload")
    if _FORBIDDEN_KEYS & body.keys():
        return _problem("forbidden_field")
    if body.keys() != required.keys():
        return _problem("invalid_payload")
    for key, kind in required.items():
        if type(body[key]) is not kind:
            return _problem("invalid_payload")
    return body


def _replayed(user_id: int, expected: int, status: str, statement_hash: str | None = None) -> bool:
    """True when the request already produced the latest row (a retry of the same call)."""
    rows = vc.consent_history(user_id)
    if not rows or rows[-1]["id"] != expected + 1 or rows[-1]["status"] != status:
        return False
    return statement_hash is None or rows[-1]["statement_hash"] == statement_hash


@blueprint.get(PREFIX)
def get_consent():
    user_id = current_user.id
    return jsonify(**_view(user_id), history=_history_json(user_id))


@blueprint.post(PREFIX + "/grant")
def grant():
    body = _body({"expected_consent_id": int, "statement_text": str})
    if isinstance(body, tuple):
        return body
    user_id, expected, text = current_user.id, body["expected_consent_id"], body["statement_text"]
    if expected < 0 or len(text) > _MAX_STATEMENT:
        return _problem("invalid_payload" if expected < 0 else "statement_too_long")
    try:
        vc.grant_consent(actor_user_id=user_id, user_id=user_id, statement_text=text,
                         expected_consent_id=expected)
    except vc.VoiceConsentError as error:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if error.code == "stale_consent" and _replayed(user_id, expected, "granted", digest):
            return jsonify(**_view(user_id)), 201
        return _problem(error.code if error.code in _STATUS else "consent_unavailable")
    return jsonify(**_view(user_id)), 201


@blueprint.post(PREFIX + "/revoke")
def revoke():
    body = _body({"expected_consent_id": int})
    if isinstance(body, tuple):
        return body
    user_id, expected = current_user.id, body["expected_consent_id"]
    if expected < 1:
        return _problem("invalid_payload")
    try:
        vc.revoke_consent(actor_user_id=user_id, user_id=user_id, expected_consent_id=expected)
    except vc.VoiceConsentError as error:
        if error.code == "stale_consent" and _replayed(user_id, expected, "revoked"):
            return jsonify(**_view(user_id)), 200
        return _problem(error.code if error.code in _STATUS else "consent_unavailable")
    return jsonify(**_view(user_id)), 200
