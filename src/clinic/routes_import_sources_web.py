"""Web configuration of locally approved import sources."""
import hashlib
import json
from uuid import UUID

from flask import jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.exc import IntegrityError
from wtforms.validators import ValidationError

from src.clinic import import_source_config as sources
from src.clinic.agent_channel import AgentChannelError
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.clinic.workflow import PRIORITY_CODES
from src.database import db
from src.models import User
from src.services import audit


_FOLDER_FIELDS = {"approved_root", "file_pattern", "priority", "language_id", "asr_profile_id"}
_TARGET_FIELDS = {"target_author_user_id", "target_pool_id"}
_FLAGS = {"autosend_enabled", "eol_filter_enabled"}


def _visible(config):
    result = {key: config[key] for key in (
        "id", "source_kind", "source_ref", "target_author_user_id", "target_pool_id", "policy_version",
        "autosend_enabled", "eol_filter_enabled",
    )}
    if config["source_kind"] == "folder":
        result.update((key, config[key]) for key in _FOLDER_FIELDS)
    return result


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


def _check_csrf():
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
    except ValidationError:
        raise CommandProblem("FORBIDDEN") from None


def _owned_source(actor, config_id):
    if not isinstance(config_id, str) or not 1 <= len(config_id) <= 36 or "\x00" in config_id:
        raise CommandProblem("INVALID_PAYLOAD")
    config = sources.get_source_config(actor_user_id=actor, config_id=config_id)
    sources._clean_root(config["approved_root"])
    return config


def _validate_options(body):
    for name, limit in (("approved_root", 1024), ("file_pattern", 255)):
        if name in body and (not isinstance(body[name], str) or not 1 <= len(body[name]) <= limit):
            raise CommandProblem("INVALID_PAYLOAD")
    if "priority" in body and (type(body["priority"]) is not int or body["priority"] not in PRIORITY_CODES):
        raise CommandProblem("INVALID_PAYLOAD")
    if "language_id" in body and body["language_id"] is None:
        raise CommandProblem("INVALID_PAYLOAD")
    profile = body.get("asr_profile_id")
    if profile is not None and (not isinstance(profile, str) or not 1 <= len(profile) <= 36):
        raise CommandProblem("INVALID_PAYLOAD")
    if any(type(body[name]) is not bool for name in _FLAGS & body.keys()):
        raise CommandProblem("INVALID_PAYLOAD")


def _validate_target(actor, author, pool):
    if (type(author) is not int or not 1 <= author < 2**31 or
            (pool is not None and (not isinstance(pool, str) or not 1 <= len(pool) <= 36 or "\x00" in pool))):
        raise CommandProblem("INVALID_PAYLOAD")
    if author != actor and not current_user.is_admin:
        raise CommandProblem("FORBIDDEN")
    active = db.session.get(ClinicAccountPolicy, author)
    if active is None or active.status != "active":
        raise CommandProblem("INVALID_PAYLOAD")
    if pool is not None and not ClinicAuthorPoolMember.query.filter_by(pool_id=pool, user_id=author).first():
        raise CommandProblem("INVALID_PAYLOAD")


def _audit_change(actor, config_id, author, action="routing"):
    event = audit.record_in_tx("admin.setting.changed", actor_user_id=actor, target_user_id=author, force=True,
                               details={"key": f"clinic_import_{action}:{config_id}"})
    if event is None:
        raise CommandProblem("DEPENDENCY_UNAVAILABLE")


def create_source():
    actor = current_user.id
    try:
        sources._may_manage(actor)
        _check_csrf()
        body = request.get_json(silent=True)
        allowed = _FOLDER_FIELDS | _TARGET_FIELDS | _FLAGS | {"source_kind", "source_ref"}
        if (not isinstance(body, dict) or body.keys() - allowed
                or {"source_ref", "approved_root"} - body.keys() or body.get("source_kind", "folder") != "folder"):
            return _problem("INVALID_PAYLOAD")
        body.setdefault("source_kind", "folder")
        body.setdefault("target_author_user_id", actor)
        _validate_options(body)
        _validate_target(actor, body["target_author_user_id"], body.get("target_pool_id"))
        try:
            config = sources.create_source_config(
                actor_user_id=actor, commit=False, may_assign_author=lambda *_: current_user.is_admin, **body,
            )
        except IntegrityError:
            if sources.ClinicImportSourceConfig.query.filter_by(
                source_kind="folder", source_ref=body["source_ref"],
            ).first():
                return _problem("REVISION_CONFLICT")
            raise
        _audit_change(actor, config["id"], config["target_author_user_id"], "create")
        db.session.commit()
        return jsonify(_visible(config)), 201
    except CommandProblem as error:
        db.session.rollback()
        return _problem(str(error))
    except AgentChannelError as error:
        return _source_error(error)


def delete_source(config_id=None):
    actor = current_user.id
    try:
        sources._may_manage(actor)
        _check_csrf()
        if config_id is None:
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or body.keys() != {"config_id"}:
                return _problem("INVALID_PAYLOAD")
            config_id = body["config_id"]
        config = _owned_source(actor, config_id)
        if config["source_kind"] != "folder":
            return _problem("INVALID_PAYLOAD")
        sources.delete_source_config(
            actor_user_id=actor, config_id=config_id, expected_version=config["policy_version"], commit=False,
        )
        _audit_change(actor, config_id, config["target_author_user_id"], "delete")
        db.session.commit()
        return "", 204
    except CommandProblem as error:
        db.session.rollback()
        return _problem(str(error))
    except AgentChannelError as error:
        return _source_error(error)


def update_source(config_id):
    actor = current_user.id
    try:
        sources._may_manage(actor)
        config = _owned_source(actor, config_id)
        _check_csrf()
    except AgentChannelError as error:
        return _source_error(error)
    except CommandProblem as error:
        return _problem(str(error))
    key = request.headers.get("Idempotency-Key", "")
    try:
        UUID(key)
    except ValueError:
        return _problem("INVALID_PAYLOAD")
    body = request.get_json(silent=True)
    allowed = {"expected_version"} | _TARGET_FIELDS | _FLAGS
    if config["source_kind"] == "folder":
        allowed |= _FOLDER_FIELDS
    if not isinstance(body, dict) or not set(body) <= allowed or "expected_version" not in body:
        return _problem("INVALID_PAYLOAD")
    target_fields = _TARGET_FIELDS & body.keys()
    changes = {name: value for name, value in body.items() if name != "expected_version"}
    if not changes or (target_fields and target_fields != _TARGET_FIELDS):
        return _problem("INVALID_PAYLOAD")
    version = body["expected_version"]
    if type(version) is not int or not 1 <= version < 2**31 - 1:
        return _problem("INVALID_PAYLOAD")
    try:
        _validate_options(changes)
        if target_fields:
            _validate_target(actor, changes["target_author_user_id"], changes["target_pool_id"])
    except CommandProblem as error:
        return _problem(str(error))
    if "asr_profile_id" in changes and changes["asr_profile_id"] is None:
        changes["asr_profile_id"] = ""
    scope = (actor, "route_import_source", config_id, key)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def write():
        updated = sources.update_source_config(
            actor_user_id=actor, config_id=config_id, expected_version=version, commit=False,
            may_assign_author=lambda *_: current_user.is_admin, **changes)
        _audit_change(actor, config_id, updated["target_author_user_id"])
        return _visible(updated)

    try:
        try:
            result = run_once(scope, digest, write)
        except AgentChannelError as error:
            # A concurrent identical PUT may have committed its receipt while our version check waited.
            if error.code != "conflict" or db.session.get(ClinicParityReceipt, scope_key(scope)) is None:
                raise
            result = run_once(scope, digest, write)
        if "approved_root" in result:
            sources._clean_root(result["approved_root"])
        return jsonify(result)
    except CommandProblem as error:
        return _problem(str(error))
    except AgentChannelError as error:
        return _source_error(error)
