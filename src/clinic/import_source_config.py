"""Persist approved import-source and autosend configuration.

Each approved source (device root or dropfolder) carries server-authorized
author routing, autosend settings and the EOL filter. The server owns the
policy version; clients cannot forge versions or grant themselves source
deletion rights. Path safety is enforced here; callers supply the
authenticated user id.
"""

from __future__ import annotations

import fnmatch
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint

from src.database import db
from src.models import User

from .agent_channel import AgentChannelError, aware, transactional, utcnow
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
    """Create this module's tables if they are missing."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[m.__table__ for m in CONFIG_TABLES],
        checkfirst=True,
    )


def _clean_ref(value, what):
    if not isinstance(value, str) or not _REF.match(value):
        raise AgentChannelError("invalid", what)
    return value


def _clean_root(value):
    root = str(value or "").strip().replace("\\", "/")
    if not root or _CONTROL.search(root) or ".." in root.split("/"):
        raise AgentChannelError("invalid", "approved_root")
    return root.rstrip("/") or "/"


def _clean_pattern(value):
    pattern = str(value or "*").strip()
    if not pattern or _CONTROL.search(pattern):
        raise AgentChannelError("invalid", "file_pattern")
    return pattern


def _may_manage(actor_user_id):
    if db.session.get(User, actor_user_id) is None:
        raise AgentChannelError("forbidden", "unknown user")


def _view(row):
    return {
        "id": row.id,
        "source_kind": row.source_kind,
        "source_ref": row.source_ref,
        "approved_root": row.approved_root,
        "target_author_user_id": row.target_author_user_id,
        "target_pool_id": row.target_pool_id,
        "autosend_enabled": row.autosend_enabled,
        "eol_filter_enabled": row.eol_filter_enabled,
        "file_pattern": row.file_pattern,
        "priority": row.priority,
        "language_id": row.language_id,
        "asr_profile_id": row.asr_profile_id,
        "policy_version": row.policy_version,
        "allow_source_delete": row.allow_source_delete,
        "created_at": aware(row.created_at).isoformat(),
        "updated_at": aware(row.updated_at).isoformat(),
    }


@transactional
def create_source_config(
    *, actor_user_id, source_kind, source_ref, approved_root, target_author_user_id,
    target_pool_id=None, autosend_enabled=False, eol_filter_enabled=True, file_pattern="*",
    priority=None, language_id=None, asr_profile_id=None, allow_source_delete=False,
    may_assign_author: Optional[Callable[[int, int], bool]] = None,
    may_manage_deletion: Optional[Callable[[int], bool]] = None, now=None,
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
    row = ClinicImportSourceConfig(
        source_kind=source_kind, source_ref=source_ref, approved_root=approved_root,
        target_author_user_id=target_author_user_id, target_pool_id=target_pool_id,
        autosend_enabled=bool(autosend_enabled), eol_filter_enabled=bool(eol_filter_enabled),
        file_pattern=_clean_pattern(file_pattern), priority=priority, language_id=language_id,
        asr_profile_id=_clean_ref(asr_profile_id, "asr_profile_id") if asr_profile_id else None,
        policy_version=1, allow_source_delete=bool(allow_source_delete),
        created_by_user_id=actor_user_id, created_at=now, updated_at=now,
    )
    db.session.add(row)
    db.session.commit()
    return _view(row)


@transactional
def update_source_config(
    *, actor_user_id, config_id, expected_version, approved_root=None,
    target_author_user_id=None, target_pool_id=..., autosend_enabled=None,
    eol_filter_enabled=None, file_pattern=None, priority=None, language_id=None,
    asr_profile_id=None, allow_source_delete=None,
    may_assign_author: Optional[Callable[[int, int], bool]] = None,
    may_manage_deletion: Optional[Callable[[int], bool]] = None, now=None,
):
    """Authorized update. ``expected_version`` must match the server policy version."""
    now = aware(now) or _now()
    _may_manage(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    if row.policy_version != expected_version:
        raise AgentChannelError("conflict", "stale policy version")
    if approved_root is not None:
        row.approved_root = _clean_root(approved_root)
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
    row.policy_version += 1
    row.updated_at = now
    db.session.commit()
    return _view(row)


@transactional
def delete_source_config(*, actor_user_id, config_id):
    """Server-controlled removal. The caller must authorize outside this layer."""
    _may_manage(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    if row.allow_source_delete:
        raise AgentChannelError("forbidden", "source deletion is enabled; disable before removing config")
    db.session.delete(row)
    db.session.commit()


def get_source_config(*, actor_user_id, config_id):
    _may_manage(actor_user_id)
    row = db.session.get(ClinicImportSourceConfig, config_id)
    if row is None or row.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import source config")
    return _view(row)


def list_source_configs(*, actor_user_id):
    _may_manage(actor_user_id)
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
    if config.source_kind == "folder":
        root = config.approved_root.replace("\\", "/").rstrip("/")
        if not root.startswith("/"):
            raise AgentChannelError("invalid", "approved_root must be absolute for folder sources")
        combined = (root + "/" + raw).split("/")
        root_parts = root.split("/")
        # Any escaping step would already have been rejected above; this guards
        # against future normalization that might collapse symlinks or prefixes.
        if combined[: len(root_parts)] != root_parts:
            raise AgentChannelError("forbidden", "source outside approved root")


def resolve_source_config(source_kind, source_ref, relative_path):
    """Return the matching config view, or None if no config matches.

    Raises AgentChannelError for traversal or a path outside the approved root.
    """
    if source_kind not in SOURCE_KINDS:
        raise AgentChannelError("invalid", "source_kind")
    source_ref = _clean_ref(source_ref, "source_ref")
    row = ClinicImportSourceConfig.query.filter_by(source_kind=source_kind, source_ref=source_ref).first()
    if row is None:
        return None
    _validate_path(row, relative_path)
    name = norm_path(relative_path).split("/")[-1].lower()
    if not fnmatch.fnmatch(name, row.file_pattern.lower()):
        return None
    return _view(row)


def norm_path(value):
    return str(value or "").replace("\\", "/").strip("/")
