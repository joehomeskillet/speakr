"""Persist approved import-source and autosend configuration.

Each approved source (device root or dropfolder) carries server-authorized
author routing, autosend settings, the EOL filter and optional backup name,
retention and cleanup. The server owns the policy version; clients cannot
forge versions or grant themselves source deletion rights. Path safety is
enforced here; callers supply the authenticated user id. A backup name,
retention period or cleanup requires an approved backup root.
"""

from __future__ import annotations

import fnmatch
import os
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Callable, Optional

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint

from src.database import db
from src.models import User

from . import policy
from .agent_channel import AgentChannelError, aware, transactional, utcnow
from .identity import ClinicAccountPolicy
from .import_centre import resolve_language
from .workflow import normalize_priority

SOURCE_KINDS = ("device", "folder")
_REF = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_CONTROL = re.compile(r"[\x00-\x1f]")


def _uid():
    return str(uuid.uuid4())


def _now():
    return aware(datetime.now(timezone.utc))


class ClinicImportSourceConfig(db.Model):
    __tablename__ = "clinic_import_source_config"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    source_kind = db.Column(db.String(16), nullable=False)
    source_ref = db.Column(db.String(128), nullable=False)
    approved_root = db.Column(db.String(1024), nullable=False)
    target_author_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    target_pool_id = db.Column(db.String(36), nullable=True)
    autosend_enabled = db.Column(db.Boolean, nullable=False, default=False)
    eol_filter_enabled = db.Column(db.Boolean, nullable=False, default=True)
    file_pattern = db.Column(db.String(255), nullable=False, default="*")
    priority = db.Column(db.Integer, nullable=False, default=1)
    language_id = db.Column(db.Integer, nullable=False, default=1031)
    asr_profile_id = db.Column(db.String(36), nullable=True)
    policy_version = db.Column(db.Integer, nullable=False, default=1)
    allow_source_delete = db.Column(db.Boolean, nullable=False, default=False)
    backup_name = db.Column(db.String(255), nullable=False, default="")
    backup_retention_days = db.Column(db.Integer, nullable=True)
    cleanup_enabled = db.Column(db.Boolean, nullable=False, default=False)
    backup_root = db.Column(db.String(1024), nullable=True)
    created_by_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        CheckConstraint("source_kind IN ('device','folder')", name="ck_clinic_import_source_kind"),
        CheckConstraint("priority IN (0, 1, 128)", name="ck_clinic_import_source_priority"),
        CheckConstraint("policy_version > 0", name="ck_clinic_import_source_version"),
        UniqueConstraint("source_kind", "source_ref", name="uq_clinic_import_source_ref"),
    )


CONFIG_TABLES = (ClinicImportSourceConfig,)


def create_tables(bind=None):
    """Create this module's tables if they are missing, then fill option columns on older copies."""
    bind = bind or db.engine
    db.metadata.create_all(
        bind=bind,
        tables=[m.__table__ for m in CONFIG_TABLES],
        checkfirst=True,
    )
    from . import migration_import_source_options

    migration_import_source_options.upgrade(bind)


def _clean_ref(value, what):
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise AgentChannelError("invalid", what)
    return value


def _root_path(value: str, what="approved_root") -> PurePosixPath | PureWindowsPath:
    root = value.replace("\\", "/")
    if _CONTROL.search(root) or ".." in root.split("/"):
        raise AgentChannelError("invalid", what)
    drive = re.match(r"^[A-Za-z]:", root)
    if root.startswith("//") or drive:
        # Reject Windows device namespaces and names Win32 may reinterpret.
        tail = root[2:] if drive else root
        if (root.startswith(("//?/", "//./")) or re.search(r'[<>"|?*:]', tail)
                or any(p != "." and p.endswith((".", " ")) for p in tail.split("/"))):
            raise AgentChannelError("invalid", what)
        path: PurePosixPath | PureWindowsPath = PureWindowsPath(root)
    else:
        path = PurePosixPath(root)
    if not path.is_absolute():
        raise AgentChannelError("invalid", what)
    return path


def _clean_root(value: str, what="approved_root") -> str:
    if not isinstance(value, str):
        raise AgentChannelError("invalid", what)
    root = _root_path(value, what)
    canonical = root.as_posix()
    if len(canonical) > 1024:
        raise AgentChannelError("invalid", what)
    for entry in os.environ.get("CLINIC_IMPORT_ROOT_ALLOWLIST", "").split(";"):
        try:
            base = _root_path(entry.strip())
        except AgentChannelError:
            continue
        if type(root) is type(base) and root.is_relative_to(base):
            return canonical
    raise AgentChannelError("forbidden", what + " outside allowlist")


def _clean_pattern(value):
    pattern = str(value or "*").strip()
    if not pattern or _CONTROL.search(pattern):
        raise AgentChannelError("invalid", "file_pattern")
    return pattern


_MISSING = object()
_BACKUP_TOKENS = frozenset("ATDSC")  # same letters as output.PLACEHOLDERS
_BACKUP_CHAR = re.compile(r"[A-Za-z0-9 ._()\-]")
_MAX_RETENTION_DAYS = 36500


def _clean_backup_name(value):
    """Allowed backup file name. Empty keeps the source file name."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 255 or value != value.strip() or value in (".", ".."):
        raise AgentChannelError("invalid", "backup_name")
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%":
            if index + 1 >= len(value) or (value[index + 1] != "%" and value[index + 1] not in _BACKUP_TOKENS):
                raise AgentChannelError("invalid", "backup_name")
            index += 2
            continue
        if _BACKUP_CHAR.fullmatch(char) is None:
            raise AgentChannelError("invalid", "backup_name")
        index += 1
    return value


def _clean_retention(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_RETENTION_DAYS:
        raise AgentChannelError("invalid", "backup_retention_days")
    return value


def _overlaps(left: str, right: str) -> bool:
    a, b = _root_path(left), _root_path(right)
    if type(a) is not type(b):
        return False
    return a.is_relative_to(b) or b.is_relative_to(a)


def _clean_backup_root(value, approved_root: str):
    if value is None or value == "":
        return None
    root = _clean_root(value, "backup_root")
    if _overlaps(root, approved_root):
        raise AgentChannelError("invalid", "backup_root overlaps approved_root")
    return root


def _checked_backup(approved_root, backup_root, backup_name, retention, cleanup):
    """A backup name, retention period or cleanup requires an approved backup root."""
    name = _clean_backup_name(backup_name)
    days = _clean_retention(retention)
    enabled = bool(cleanup)
    root = _clean_backup_root(backup_root, approved_root)
    if (enabled or days is not None or name != "") and root is None:
        raise AgentChannelError("invalid", "backup requires an approved root")
    if enabled and days is None:
        raise AgentChannelError("invalid", "backup_retention_days")
    return root, name, days, enabled


def _known_actor(actor_user_id: int) -> User:
    actor = db.session.get(User, actor_user_id, populate_existing=True) if type(actor_user_id) is int else None
    if actor is None:
        raise AgentChannelError("forbidden", "unknown user")
    return actor


def _may_manage(actor_user_id: int) -> None:
    actor = _known_actor(actor_user_id)
    account = db.session.get(ClinicAccountPolicy, actor.id, populate_existing=True)
    if account is not None and account.status != "active":
        raise AgentChannelError("forbidden", "inactive account")
    if actor.is_admin:
        return

    # The evaluator has a closed list of recording actions. Project only explicit
    # import grants onto a grant-only action on a synthetic, actor-owned resource.
    # This preserves central scope and deny rules without changing recording policy.
    subject = policy.build_subject(actor.id)
    grants = tuple(
        replace(grant, actions=frozenset({"release"})) for grant in subject.grants
        if "manage_import_sources" in grant.actions
    )
    if grants and policy.decide(
        replace(subject, grants=grants), "release", policy.RecordingFacts(recording_id=0, owner_id=actor.id),
    ).allowed:
        return
    raise AgentChannelError("forbidden", "manage_import_sources required")


def _view(row):
    return {
        "id": row.id,
        "source_kind": row.source_kind,
        "source_ref": row.source_ref,
        "approved_root": row.approved_root,
        "target_author_user_id": row.target_author_user_id,
        "target_pool_id": row.target_pool_id,
        "autosend_enabled": row.autosend_enabled,
        "eol_filter_enabled": True if row.eol_filter_enabled is None else bool(row.eol_filter_enabled),
        "file_pattern": row.file_pattern or "*",
        "priority": 1 if row.priority is None else row.priority,
        "language_id": 1031 if row.language_id is None else row.language_id,
        "asr_profile_id": row.asr_profile_id,
        "policy_version": row.policy_version,
        "allow_source_delete": row.allow_source_delete,
        "backup_name": row.backup_name or "",
        "backup_retention_days": row.backup_retention_days,
        "cleanup_enabled": False if row.cleanup_enabled is None else bool(row.cleanup_enabled),
        "backup_root": row.backup_root,
        "created_at": aware(row.created_at).isoformat(),
        "updated_at": aware(row.updated_at).isoformat(),
    }


@transactional
def create_source_config(
    *, actor_user_id, source_kind, source_ref, approved_root, target_author_user_id,
    target_pool_id=None, autosend_enabled=False, eol_filter_enabled=True, file_pattern="*",
    priority=None, language_id=None, asr_profile_id=None, allow_source_delete=False,
    backup_name="", backup_retention_days=None, cleanup_enabled=False, backup_root=None,
    may_assign_author: Optional[Callable[[int, int], bool]] = None,
    may_manage_deletion: Optional[Callable[[int], bool]] = None, now=None, commit=True,
):
    """Approve one import source. Source deletion rights require explicit authorization."""
    now = aware(now) or _now()
    _may_manage(actor_user_id)
    if source_kind not in SOURCE_KINDS:
        raise AgentChannelError("invalid", "source_kind")
    source_ref = _clean_ref(source_ref, "source_ref")
    approved_root = _clean_root(approved_root)
    if db.session.get(User, target_author_user_id) is None:
        raise AgentChannelError("invalid", "target_author_user_id")
    if target_author_user_id != actor_user_id:
        if may_assign_author is None or not may_assign_author(actor_user_id, target_author_user_id):
            raise AgentChannelError("forbidden", "user may not route to this author")
    if allow_source_delete:
        if may_manage_deletion is None or not may_manage_deletion(actor_user_id):
            raise AgentChannelError("forbidden", "source deletion rights require authorization")
    priority, _ = normalize_priority(priority)
    language_id = resolve_language(language_id)
    backup_root, backup_name, backup_retention_days, cleanup_enabled = _checked_backup(
        approved_root, backup_root, backup_name, backup_retention_days, cleanup_enabled)
    row = ClinicImportSourceConfig(
        source_kind=source_kind, source_ref=source_ref, approved_root=approved_root,
        target_author_user_id=target_author_user_id, target_pool_id=target_pool_id,
        autosend_enabled=bool(autosend_enabled), eol_filter_enabled=bool(eol_filter_enabled),
        file_pattern=_clean_pattern(file_pattern), priority=priority, language_id=language_id,
        asr_profile_id=_clean_ref(asr_profile_id, "asr_profile_id") if asr_profile_id else None,
        policy_version=1, allow_source_delete=bool(allow_source_delete),
        backup_name=backup_name, backup_retention_days=backup_retention_days,
        cleanup_enabled=cleanup_enabled, backup_root=backup_root,
        created_by_user_id=actor_user_id, created_at=now, updated_at=now,
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return _view(row)


@transactional
def update_source_config(
    *, actor_user_id, config_id, expected_version, approved_root=None,
    target_author_user_id=None, target_pool_id=..., autosend_enabled=None,
    eol_filter_enabled=None, file_pattern=None, priority=None, language_id=None,
    asr_profile_id=None, allow_source_delete=None,
    backup_name=None, backup_retention_days=_MISSING, cleanup_enabled=None, backup_root=_MISSING,
    may_assign_author: Optional[Callable[[int, int], bool]] = None,
    may_manage_deletion: Optional[Callable[[int], bool]] = None, now=None, commit=True,
):
    """Authorized update. ``expected_version`` must match the server policy version."""
    now = aware(now) or _now()
    _may_manage(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    claimed = ClinicImportSourceConfig.query.filter_by(
        id=config_id, created_by_user_id=actor_user_id, policy_version=expected_version,
    ).update({ClinicImportSourceConfig.policy_version: ClinicImportSourceConfig.policy_version + 1},
             synchronize_session=False)
    if claimed != 1:
        raise AgentChannelError("conflict", "stale policy version")
    row.approved_root = _clean_root(row.approved_root if approved_root is None else approved_root)
    if target_author_user_id is not None:
        if db.session.get(User, target_author_user_id) is None:
            raise AgentChannelError("invalid", "target_author_user_id")
        if target_author_user_id != actor_user_id:
            if may_assign_author is None or not may_assign_author(actor_user_id, target_author_user_id):
                raise AgentChannelError("forbidden", "user may not route to this author")
        row.target_author_user_id = target_author_user_id
    if target_pool_id is not ...:
        row.target_pool_id = target_pool_id
    if autosend_enabled is not None:
        row.autosend_enabled = bool(autosend_enabled)
    if eol_filter_enabled is not None:
        row.eol_filter_enabled = bool(eol_filter_enabled)
    if file_pattern is not None:
        row.file_pattern = _clean_pattern(file_pattern)
    if priority is not None:
        row.priority = normalize_priority(priority)[0]
    if language_id is not None:
        row.language_id = resolve_language(language_id)
    if asr_profile_id is not None:
        row.asr_profile_id = _clean_ref(asr_profile_id, "asr_profile_id") if asr_profile_id else None
    if allow_source_delete is not None:
        if allow_source_delete:
            if may_manage_deletion is None or not may_manage_deletion(actor_user_id):
                raise AgentChannelError("forbidden", "source deletion rights require authorization")
        row.allow_source_delete = bool(allow_source_delete)
    next_root = row.backup_root if backup_root is _MISSING else backup_root
    next_name = row.backup_name if backup_name is None else backup_name
    next_days = row.backup_retention_days if backup_retention_days is _MISSING else backup_retention_days
    next_cleanup = row.cleanup_enabled if cleanup_enabled is None else cleanup_enabled
    row.backup_root, row.backup_name, row.backup_retention_days, row.cleanup_enabled = _checked_backup(
        row.approved_root, next_root, next_name, next_days, next_cleanup)
    row.policy_version = expected_version + 1
    row.updated_at = now
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return _view(row)


@transactional
def delete_source_config(*, actor_user_id, config_id, expected_version=None, commit=True):
    """Remove an owned import-source configuration with source management authority."""
    _may_manage(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    if row.allow_source_delete:
        raise AgentChannelError("forbidden", "source deletion is enabled; disable before removing config")
    version = row.policy_version if expected_version is None else expected_version
    removed = ClinicImportSourceConfig.query.filter_by(
        id=config_id, created_by_user_id=actor_user_id, policy_version=version, allow_source_delete=False,
    ).delete(synchronize_session="fetch")
    if removed != 1:
        raise AgentChannelError("conflict", "stale policy version")
    if commit:
        db.session.commit()


def get_source_config(*, actor_user_id, config_id):
    _known_actor(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    return _view(row)


def list_source_configs(*, actor_user_id):
    _known_actor(actor_user_id)
    rows = ClinicImportSourceConfig.query.filter_by(created_by_user_id=actor_user_id).order_by(
        ClinicImportSourceConfig.source_kind, ClinicImportSourceConfig.source_ref)
    return [_view(r) for r in rows]


def _validate_path(config, relative_path):
    """Reject traversal and paths outside the approved root.

    File stability and modification time never stand in for EOL; this check
    validates path safety only.
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise AgentChannelError("invalid", "relative_path")
    raw = relative_path.replace("\\", "/")
    # Absolute paths, drive letters and parent-directory steps are traversal.
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", relative_path):
        raise AgentChannelError("forbidden", "path traversal")
    parts = raw.split("/")
    if any(p == ".." for p in parts):
        raise AgentChannelError("forbidden", "path traversal")
    _clean_root(config.approved_root)


def resolve_source_config(source_kind, source_ref, relative_path):
    """Return the matching config view, or None if no config matches.

    Raises AgentChannelError for traversal or a path outside the approved root.
    """
    if source_kind not in SOURCE_KINDS:
        raise AgentChannelError("invalid", "source_kind")
    source_ref = _clean_ref(source_ref, "source_ref")
    row = ClinicImportSourceConfig.query.filter_by(source_kind=source_kind, source_ref=source_ref).first()
    return _matching_file(row, relative_path) if row is not None else None


def delivery_config(source_ref, relative_path):
    """An inventoried root has no kind. Configured roots must resolve to one matching rule."""
    rows = ClinicImportSourceConfig.query.filter_by(source_ref=source_ref).all()
    if not rows:
        return None
    matches = [config for row in rows if (config := _matching_file(row, relative_path))]
    if len(matches) != 1:
        raise AgentChannelError("conflict", "no unique import source rule")
    return matches[0]


def _matching_file(row, relative_path):
    _validate_path(row, relative_path)
    name = norm_path(relative_path).split("/")[-1].lower()
    if not fnmatch.fnmatch(name, row.file_pattern.lower()):
        return None
    return _view(row)


def norm_path(value):
    return str(value or "").replace("\\", "/").strip("/")
