"""Browser-session administration of import-agent machine identities."""

import json

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, ValidationError, validate_csrf
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.clinic import agent_channel as channel
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.services import audit

PREFIX = "/api/clinic/v1/admin/agents"
MAX_BODY_BYTES = 16 * 1024
_ENROLL_FIELDS = {"display_name", "allowed_sources", "credential_fingerprint"}
_ERROR_STATUS = {"invalid": 400, "forbidden": 403, "not_found": 404}


def _error(status, code):
    return jsonify(error=code.replace("_", " "), code=code), status


def _body():
    request.max_content_length = min(request.max_content_length or MAX_BODY_BYTES, MAX_BODY_BYTES)
    if request.is_json and len(request.get_data()) > MAX_BODY_BYTES:
        abort(413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise channel.AgentChannelError("invalid")
    return body


def _may_manage(actor, action):
    return (action in {"register_agent", "manage_agent"} and _is_active_site_admin(db.session, current_user)
            and current_user.id == actor)


def create_blueprint(master_secret: bytes) -> Blueprint:
    bp = Blueprint("clinic_agent_admin", __name__, url_prefix=PREFIX)
    policy = channel.ChannelPolicy(master_secret=master_secret, authorise=_may_manage)

    @bp.before_request
    def require_admin():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return _error(401, "unauthenticated")
        if not _is_active_site_admin(db.session, current_user):
            return _error(403, "forbidden")
        if request.method in {"POST", "PATCH"} and current_app.config.get("WTF_CSRF_ENABLED", True):
            try:
                validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
            except ValidationError:
                return _error(403, "csrf_failed")
        return None

    @bp.after_request
    def prevent_caching(response):
        response.cache_control.no_store = True
        return response

    @bp.errorhandler(413)
    def too_large(_exc):
        return _error(413, "payload_too_large")

    @bp.errorhandler(CSRFError)
    def csrf_failed(_exc):
        return _error(403, "csrf_failed")

    @bp.errorhandler(channel.AgentChannelError)
    def channel_error(exc):
        return _error(_ERROR_STATUS.get(exc.code, 400), exc.code)

    @bp.errorhandler(SQLAlchemyError)
    def database_error(_exc):
        db.session.rollback()
        current_app.logger.error("Import agent administration: database error")
        return _error(503, "dependency_unavailable")

    @bp.get("")
    def list_agents():
        agents = channel.ClinicAgent.query.order_by(channel.ClinicAgent.display_name, channel.ClinicAgent.id)
        items = [{"agent_id": row.id, "display_name": row.display_name, "enabled": row.enabled,
                  "allowed_sources": json.loads(row.allowed_sources)} for row in agents]
        return jsonify(items=items, count=len(items))

    @bp.post("")
    def enroll():
        if not policy.master_secret:
            return _error(503, "dependency_unavailable")
        body = _body()
        if body.keys() != _ENROLL_FIELDS:
            return _error(400, "invalid")
        name, sources, fingerprint = body["display_name"], body["allowed_sources"], body["credential_fingerprint"]
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not name.strip().isprintable()
                or not isinstance(sources, list) or not isinstance(fingerprint, str)):
            return _error(400, "invalid")
        try:
            result = channel.register_agent(policy, actor_user_id=current_user.id, **body)
        except IntegrityError:
            return _error(409, "credential_already_registered")
        return jsonify(result), 201

    @bp.patch("/<uuid:agent_id>")
    def set_enabled(agent_id):
        body = _body()
        if body.keys() != {"enabled"} or type(body["enabled"]) is not bool:
            return _error(400, "invalid")
        agent_id = str(agent_id)
        channel.set_agent_enabled(policy, actor_user_id=current_user.id, agent_id=agent_id, enabled=body["enabled"])
        return jsonify(agent_id=agent_id, enabled=body["enabled"])

    @bp.post("/<uuid:agent_id>/rotate-key")
    def rotate_key(agent_id):
        if not policy.master_secret:
            return _error(503, "dependency_unavailable")
        agent_id = str(agent_id)
        actor_id = current_user.id
        result = channel.rotate_signing_key(policy, actor_user_id=actor_id, agent_id=agent_id, commit=False)
        event = audit.record_in_tx("admin.setting.changed", actor_kind="session", actor_user_id=actor_id, force=True,
                                   details={"key": f"clinic_agent:{agent_id}:intent_signing_key"})
        if event is None:
            db.session.rollback()
            return _error(503, "dependency_unavailable")
        db.session.commit()
        return jsonify(result)

    return bp
