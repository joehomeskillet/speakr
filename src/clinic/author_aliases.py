"""Authorized, provenance-bearing device and author aliases for import resolution."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, tuple_

from src.database import db
from src.models import User

from .agent_channel import AgentChannelError, transactional, utcnow
from .author_resolution import CONFLICT, UNRESOLVED, Resolution, normalise_alias, resolve_author
from .identity import ClinicAccountPolicy
from .import_source_config import _may_manage


class ClinicAuthorAlias(db.Model):
    """Append-only through this service; duplicate codes retain all candidates."""

    __tablename__ = "clinic_author_alias"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = db.Column(db.String(16), nullable=False)
    code = db.Column(db.String(255), nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    provenance = db.Column(db.String(1024), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("kind IN ('device', 'author')", name="ck_clinic_author_alias_kind"),
        db.Index("ix_clinic_author_alias_lookup", "kind", "code"),
    )


def create_tables(bind=None):
    db.metadata.create_all(
        bind=bind or db.engine, tables=[ClinicAuthorAlias.__table__], checkfirst=True,
    )


def _code(kind, value):
    if kind not in ("device", "author"):
        raise AgentChannelError("invalid", "alias kind")
    normalized = normalise_alias(value) if kind == "author" else value.strip().upper() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in normalized):
        raise AgentChannelError("invalid", "alias code")
    return normalized


@transactional
def add_author_alias(*, actor_user_id, kind, code, author_user_id, provenance, may_assign_author=None):
    """Record an approved mapping; caller supplies an authenticated actor and policy callback.

    Like import-source configuration, foreign authors require explicit assignment
    authority even for administrators. Conflicting entries are retained for clarification.
    """
    _may_manage(actor_user_id)
    code = _code(kind, code)
    if type(author_user_id) is not int or author_user_id <= 0 or db.session.get(User, author_user_id) is None:
        raise AgentChannelError("invalid", "author_user_id")
    if author_user_id != actor_user_id and (
        may_assign_author is None or not may_assign_author(actor_user_id, author_user_id)
    ):
        raise AgentChannelError("forbidden", "user may not route to this author")
    if (not isinstance(provenance, str) or not provenance.strip() or len(provenance) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in provenance)):
        raise AgentChannelError("invalid", "provenance required")
    row = ClinicAuthorAlias(
        kind=kind, code=code, author_user_id=author_user_id,
        created_by_user_id=actor_user_id, provenance=provenance.strip(),
    )
    db.session.add(row)
    db.session.commit()
    return row


def resolve_stored_author(*, device_id=None, embedded_author=None):
    """Server-side lookup. Ambiguous evidence never selects an author or wins a tie."""
    keys = []
    for kind, value in (("device", device_id), ("author", embedded_author)):
        if value is not None and value != "" and (not isinstance(value, str) or value.strip()):
            keys.append((kind, _code(kind, value)))
    if not keys:
        return Resolution(UNRESOLVED, None, None, "no_input")
    rows = ClinicAuthorAlias.query.filter(
        tuple_(ClinicAuthorAlias.kind, ClinicAuthorAlias.code).in_(keys),
    ).all()
    candidates = {row.author_user_id for row in rows}
    if len(candidates) > 1:
        return Resolution(CONFLICT, None, None, "ambiguous_alias")
    for user_id in candidates:
        account = db.session.get(ClinicAccountPolicy, user_id, populate_existing=True)
        if db.session.get(User, user_id) is None or (account is not None and account.status != "active"):
            return Resolution(UNRESOLVED, None, None, "inactive_author")
    return resolve_author(
        device_id=device_id, embedded_author=embedded_author,
        device_map={row.code: row.author_user_id for row in rows if row.kind == "device"},
        alias_map={row.code: {row.author_user_id} for row in rows if row.kind == "author"},
    )
