"""Web configuration of locally approved import sources."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from flask import jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.exc import IntegrityError
from wtforms.validators import ValidationError

from src.clinic import import_source_config as sources
from src.clinic import policy
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.agent_channel import AgentChannelError
from src.clinic.backup_retention_plan import BackupEntry, plan_cleanup
from src.clinic.config import feature_locked
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.clinic.workflow import PRIORITY_CODES
from src.database import db
from src.models import User
from src.services import audit


_BACKUP_FIELDS = {
    "backup_enabled": "backup_enabled",
    "backup_folder": "backup_root",
    "backup_filename_template": "backup_name",
    "backup_retention_days": "backup_retention_days",
}


def _visible(config):
    visible = {key: config[key] for key in (
        "id", "source_kind", "source_ref", "target_author_user_id", "target_pool_id", "policy_version",
        *(_FOLDER_FIELDS if config["source_kind"] == "folder" else ()),
        "autosend_enabled", "eol_filter_enabled", "allow_source_delete",
    )}
    visible.update({field: config[key] for field, key in _BACKUP_FIELDS.items()})
    return visible


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


_FOLDER_FIELDS = {"approved_root", "file_pattern", "priority", "language_id", "asr_profile_id"}
_TARGET_FIELDS = {"target_author_user_id", "target_pool_id"}
_FLAGS = {"autosend_enabled", "eol_filter_enabled"}


def _source_error(error):
    if error.code == "not_found":
        return jsonify(status=404, code="NOT_FOUND", title="Quelle nicht gefunden"), 404
    return _problem({"forbidden": "FORBIDDEN", "conflict": "REVISION_CONFLICT"}.get(error.code, "INVALID_PAYLOAD"))


def _check_csrf():
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
    except ValidationError:
        raise CommandProblem("FORBIDDEN") from None


def _valid_source_id(config_id):
    return isinstance(config_id, str) and 1 <= len(config_id) <= 36 and "\x00" not in config_id


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
        if not _valid_source_id(config_id):
            return _problem("INVALID_PAYLOAD")
        config = sources.get_source_config(actor_user_id=actor, config_id=config_id)
        sources._clean_root(config["approved_root"])
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


def _owned_source(actor, config_id):
    sources._may_manage(actor)
    config = sources.get_source_config(actor_user_id=actor, config_id=config_id)
    sources._clean_root(config["approved_root"])
    return config


def get_source(config_id):
    try:
        return jsonify(_visible(_owned_source(current_user.id, config_id)))
    except AgentChannelError as error:
        return _source_error(error)


def may_manage_source_deletion(actor, config):
    if _is_active_site_admin(db.session, current_user):
        return True
    subject = policy.build_subject(actor)
    grants = tuple(replace(grant, actions=frozenset({"release"})) for grant in subject.grants
                   if "clinic.import.cleanup" in grant.actions)
    pool_id = config["target_pool_id"]
    facts = policy.RecordingFacts(0, config["target_author_user_id"], frozenset({pool_id}) if pool_id else frozenset())
    return bool(grants) and policy.decide(replace(subject, grants=grants), "release", facts).allowed


def update_source(config_id):
    if not _valid_source_id(config_id):
        return _problem("INVALID_PAYLOAD")
    actor = current_user.id
    try:
        config = _owned_source(actor, config_id)
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
    folder_changes = {}
    if isinstance(body, dict) and config["source_kind"] == "folder":
        folder_changes = {name: body[name] for name in _FOLDER_FIELDS & body.keys()}
        body = {name: value for name, value in body.items() if name not in folder_changes}
    allowed = {"expected_version", "target_author_user_id", "target_pool_id",
               "autosend_enabled", "eol_filter_enabled", "allow_source_delete", *_BACKUP_FIELDS}
    if not isinstance(body, dict) or not set(body) <= allowed or "expected_version" not in body:
        return _problem("INVALID_PAYLOAD")
    target_fields = _TARGET_FIELDS & body.keys()
    if target_fields and target_fields != _TARGET_FIELDS:
        return _problem("INVALID_PAYLOAD")
    routing = bool(target_fields or folder_changes)
    flags = (_FLAGS | {"allow_source_delete"}) & body.keys()
    backup = {key: body[field] for field, key in _BACKUP_FIELDS.items() if field in body}
    if not routing and not flags and not backup:
        return _problem("INVALID_PAYLOAD")
    if backup.get("backup_name", "") is None:
        backup["backup_name"] = ""
    version = body["expected_version"]
    if (type(version) is not int or version < 1
            or any(type(body[name]) is not bool for name in flags)
            or type(body.get("backup_enabled", False)) is not bool):
        return _problem("INVALID_PAYLOAD")
    if version >= 2**31 - 1:
        return _problem("INVALID_PAYLOAD")
    try:
        _validate_options(folder_changes)
        if target_fields:
            _validate_target(actor, body["target_author_user_id"], body["target_pool_id"])
    except CommandProblem as error:
        return _problem(str(error))
    scope_config = config | {name: body[name] for name in target_fields}
    if "allow_source_delete" in body and (
        not may_manage_source_deletion(actor, scope_config)
        or (body["allow_source_delete"] and feature_locked("device_delete_runtime_enabled"))
    ):
        return _problem("FORBIDDEN")
    scope = (actor, "route_import_source", config_id, key)
    digest = hashlib.sha256(json.dumps(body | folder_changes, sort_keys=True).encode()).hexdigest()
    if "asr_profile_id" in folder_changes and folder_changes["asr_profile_id"] is None:
        folder_changes["asr_profile_id"] = ""

    def write():
        changes = dict(backup)
        if routing:
            changes.update((name, body[name]) for name in target_fields)
            changes.update(folder_changes)
        for name in flags:
            changes[name] = body[name]
        updated = sources.update_source_config(
            actor_user_id=actor, config_id=config_id, expected_version=version, commit=False,
            may_assign_author=lambda *_: current_user.is_admin,
            may_manage_deletion=lambda uid: not feature_locked("device_delete_runtime_enabled")
            and may_manage_source_deletion(uid, scope_config),
            **changes,
        )
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


def retention_preview(config_id):
    if not current_user.is_admin:
        return _problem("FORBIDDEN")
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
    except ValidationError:
        return _problem("FORBIDDEN")
    try:
        config = _owned_source(current_user.id, config_id)
        sources._clean_backup_root(config["backup_root"], config["approved_root"])
    except AgentChannelError as error:
        return _source_error(error)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.keys() != {"entries"}:
        return _problem("INVALID_PAYLOAD")
    raw = body["entries"]
    if not isinstance(raw, list) or len(raw) > 1000:
        return _problem("INVALID_PAYLOAD")

    entries = []
    try:
        for item in raw:
            if (not isinstance(item, dict) or not item.keys() <= {"path", "moved_at", "hold"}
                    or not isinstance(item.get("path"), str) or len(item["path"]) > 1024
                    or type(item.get("hold", False)) is not bool):
                return _problem("INVALID_PAYLOAD")
            moved_at = datetime.fromisoformat(item.get("moved_at"))
            if moved_at.utcoffset() is None:
                return _problem("INVALID_PAYLOAD")
            entries.append(BackupEntry(item["path"], moved_at, hold=item.get("hold", False)))
        days = config["backup_retention_days"]
        enabled = config["backup_enabled"]
        plan = plan_cleanup(entries, datetime.now(timezone.utc), days) if enabled and days is not None else None
    except (TypeError, ValueError, OverflowError):
        return _problem("INVALID_PAYLOAD")
    counts = None if plan is None else {
        "expired": len(plan.expired), "kept": len(plan.kept), "held": len(plan.held), "invalid": len(plan.invalid),
    }
    return jsonify(backup_enabled=enabled, backup_retention_days=days, plan=counts)
