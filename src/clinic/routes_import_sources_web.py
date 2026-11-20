"""Web routing for existing locally approved sources. No device or filesystem commands."""
import hashlib
import json
from uuid import UUID

from flask import jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from src.clinic import import_source_config as sources
from src.clinic.agent_channel import AgentChannelError
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.database import db
from src.models import User
from src.services import audit


def _visible(config):
    return {key: config[key] for key in (
        "id", "source_kind", "source_ref", "target_author_user_id", "target_pool_id", "policy_version"
    )}


def _targets():
    authors = db.session.query(User.id, User.name, User.username).join(
        ClinicAccountPolicy, ClinicAccountPolicy.user_id == User.id).filter(ClinicAccountPolicy.status == "active")
    if not current_user.is_admin:
        authors = authors.filter(User.id == current_user.id)
    people = [{"id": uid, "name": name or login} for uid, name, login in authors.order_by(User.username)]
    pools = db.session.query(ClinicAuthorPool.id, ClinicAuthorPool.name, ClinicAuthorPoolMember.user_id).join(
        ClinicAuthorPoolMember, ClinicAuthorPoolMember.pool_id == ClinicAuthorPool.id)
    if not current_user.is_admin:
        pools = pools.filter(ClinicAuthorPoolMember.user_id == current_user.id)
    return people, [{"id": pid, "name": name, "author_id": uid} for pid, name, uid in pools.order_by(ClinicAuthorPool.name)]


def _source_error(error):
    if error.code == "not_found":
        return jsonify(status=404, code="NOT_FOUND", title="Quelle nicht gefunden"), 404
    return _problem({"forbidden": "FORBIDDEN", "conflict": "REVISION_CONFLICT"}.get(error.code, "INVALID_PAYLOAD"))


def list_sources():
    actor = current_user.id
    try:
        sources._may_manage(actor)
        items = []
        for config in sources.list_source_configs(actor_user_id=actor):
            try:
                sources._clean_root(config["approved_root"])
            except AgentChannelError:
                continue
            items.append(_visible(config))
    except AgentChannelError as error:
        return _source_error(error)
    authors, pools = _targets() if items else ([], [])
    return jsonify(items=items, authors=authors, pools=pools)


def update_source(config_id):
    actor = current_user.id
    try:
        sources._may_manage(actor)
        config = sources.get_source_config(actor_user_id=actor, config_id=config_id)
        sources._clean_root(config["approved_root"])
    except AgentChannelError as error:
        return _source_error(error)
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
    except ValidationError:
        return _problem("FORBIDDEN")
    key = request.headers.get("Idempotency-Key", "")
    try:
        UUID(key)
    except ValueError:
        return _problem("INVALID_PAYLOAD")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"expected_version", "target_author_user_id", "target_pool_id"}:
        return _problem("INVALID_PAYLOAD")
    version, author, pool = body["expected_version"], body["target_author_user_id"], body["target_pool_id"]
    if (type(version) is not int or version < 1 or type(author) is not int or author < 1 or
            (pool is not None and (not isinstance(pool, str) or not 1 <= len(pool) <= 36))):
        return _problem("INVALID_PAYLOAD")
    if author != actor and not current_user.is_admin:
        return _problem("FORBIDDEN")
    active = db.session.get(ClinicAccountPolicy, author)
    if active is None or active.status != "active":
        return _problem("INVALID_PAYLOAD")
    if pool is not None and not ClinicAuthorPoolMember.query.filter_by(pool_id=pool, user_id=author).first():
        return _problem("INVALID_PAYLOAD")
    scope = (actor, "route_import_source", config_id, key)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def write():
        updated = sources.update_source_config(actor_user_id=actor, config_id=config_id, expected_version=version,
            target_author_user_id=author, target_pool_id=pool, may_assign_author=lambda *_: current_user.is_admin,
            commit=False)
        event = audit.record_in_tx("admin.setting.changed", actor_user_id=actor, target_user_id=author, force=True,
                                   details={"key": "clinic_import_routing:" + config_id})
        if event is None:
            raise CommandProblem("DEPENDENCY_UNAVAILABLE")
        return _visible(updated)

    try:
        try:
            result = run_once(scope, digest, write)
        except AgentChannelError as error:
            # A concurrent identical PUT may have committed its receipt while our version check waited.
            if error.code != "conflict" or db.session.get(ClinicParityReceipt, scope_key(scope)) is None:
                raise
            result = run_once(scope, digest, write)
        return jsonify(result)
    except CommandProblem as error:
        return _problem(str(error))
    except AgentChannelError as error:
        return _source_error(error)
