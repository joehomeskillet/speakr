"""Bind an opaque, server-verified KIS grant to one dictation."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import event, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from src.database import db

from .config import clinic_mode_enabled
from .kis import PRODUCTION_PROFILE, AdapterTimeout, KisAdapter, payload_hash
from .models import ClinicContextRevision, ClinicDictation
from .release import invalidate_on_change

CASE_REFERENCE = re.compile(r'SYN-[0-9]{4,8}')


class ContextGrantError(ValueError):
    """Stable refusal code without grant or patient identifiers."""


@dataclass(frozen=True, repr=False)
class VerifiedContextGrant:
    """Facts from authenticated KIS evidence, with an explicitly mapped User.id.

    The server resolver verifies signature or backchannel evidence and binds
    the grant to its target dictation. HTTP input must never construct this.
    """

    issuer: str
    audience: str
    user_id: int
    dictation_id: str
    source_system: str
    subject_reference: str
    tenant_reference: str
    case_reference: str
    patient_reference: str
    purpose: str
    nonce: str
    expires_at: datetime


class ClinicContextGrantUse(db.Model):  # type: ignore[name-defined]
    """Provenance and durable nonce claim for a successfully bound revision."""

    __tablename__ = "clinic_context_grant_use"

    revision_id = db.Column(db.String(36), db.ForeignKey("clinic_context_revision.id"),
                            primary_key=True)
    nonce_digest = db.Column(db.String(64), nullable=False, unique=True)
    issuer = db.Column(db.Text, nullable=False)
    audience = db.Column(db.Text, nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject_reference = db.Column(db.String(256), nullable=False)
    purpose = db.Column(db.String(16), nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=False)


def _immutable(_mapper, _connection, _row) -> None:
    raise ContextGrantError("context_is_append_only")


for _model in (ClinicContextRevision, ClinicContextGrantUse):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)


def _text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and bool(value.strip()) and "\x00" not in value)


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def bind_context_grant(
    grant: str, *, dictation_id: str, actor_user_id: int, tenant: str,
    expected_version: int, issuer: str, audience: str, now: datetime,
    resolve: Callable[[str, int], VerifiedContextGrant | None], adapter: KisAdapter,
    authorize_binding: Callable[[ClinicDictation, int], bool],
    purpose: str = "bind_existing",
) -> ClinicContextRevision:
    """Append and invalidate in one savepoint; the caller commits.

    Resolve and authorize_binding are server dependencies. The latter checks
    current dictation action, holder and applicable lease permissions. The
    adapter must check the exact case/patient tuple and this actor's case rights.
    """
    if (not clinic_mode_enabled() or not adapter.enabled or (adapter.is_mock
            and os.getenv("CLINIC_PROFILE", "").strip().lower() == PRODUCTION_PROFILE)):
        raise ContextGrantError("context_capability_unavailable")
    if (not _text(grant, 2048) or not _aware(now) or not issuer or not audience
            or not _text(tenant, 128) or not _text(dictation_id, 36)
            or type(actor_user_id) is not int or actor_user_id < 1
            or type(expected_version) is not int or expected_version < 0
            or purpose not in ("new_dictation", "bind_existing")):
        raise ContextGrantError("invalid_grant_input")
    evidence = resolve(grant, actor_user_id)
    if (not isinstance(evidence, VerifiedContextGrant)
            or (evidence.issuer, evidence.audience) != (issuer, audience)
            or type(evidence.user_id) is not int or evidence.user_id != actor_user_id
            or (evidence.dictation_id, evidence.tenant_reference, evidence.purpose)
            != (dictation_id, tenant, purpose)
            or not _text(evidence.nonce, 128) or not _text(evidence.subject_reference, 256)
            or not _aware(evidence.expires_at)
            or not timedelta(0) < evidence.expires_at - now <= timedelta(seconds=60)):
        raise ContextGrantError("invalid_grant")

    context = dict(source_system=evidence.source_system, tenant_scope=tenant,
                   case_ref=evidence.case_reference, patient_ref=evidence.patient_reference)
    # The current persistence model accepts at most 120 characters per field.
    if not all(_text(value, 120) for value in context.values()):
        raise ContextGrantError("invalid_context_reference")
    digest = payload_hash({"issuer": issuer, "nonce": evidence.nonce})
    used = select(ClinicContextGrantUse.revision_id).filter_by(nonce_digest=digest)
    if db.session.scalar(used) is not None:
        raise ContextGrantError("grant_replayed")

    dictation = db.session.get(ClinicDictation, dictation_id, populate_existing=True)
    if dictation is None or authorize_binding(dictation, actor_user_id) is not True:
        raise ContextGrantError("binding_denied")
    if dictation.version_counter != expected_version:
        raise ContextGrantError("stale_version")
    try:
        checked = adapter.check_context(dict(
            context, actor_user_id=actor_user_id,
            subject_reference=evidence.subject_reference, purpose=purpose,
        ))
    except AdapterTimeout:
        raise ContextGrantError("context_capability_unavailable") from None
    if checked.ok is not True:
        raise ContextGrantError("case_unresolved")

    # SQLite legacy transaction mode does not start a transaction on SELECT.
    connection = db.session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if driver is not None and not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
    try:
        with db.session.begin_nested():
            advance = update(ClinicDictation).where(
                ClinicDictation.id == dictation_id,
                ClinicDictation.version_counter == expected_version,
            ).values(version_counter=ClinicDictation.version_counter + 1)
            changed = cast(CursorResult, db.session.execute(advance))
            if changed.rowcount != 1:
                raise ContextGrantError("stale_version")
            child = aliased(ClinicContextRevision)
            row = ClinicContextRevision
            heads = db.session.scalars(select(row.id).where(
                row.dictation_id == dictation_id,
                ~select(child.id).where(child.dictation_id == dictation_id,
                                        child.parent_revision_id == row.id).exists(),
            ).limit(2)).all()
            if len(heads) > 1 or (purpose == "new_dictation" and (heads or expected_version)):
                raise ContextGrantError("context_history_conflict")
            revision = row(dictation_id=dictation_id, parent_revision_id=heads[0] if heads else None,
                           **context)
            db.session.add(revision)
            db.session.flush()
            db.session.add(ClinicContextGrantUse(
                revision_id=revision.id, nonce_digest=digest, issuer=issuer, audience=audience,
                actor_user_id=actor_user_id, subject_reference=evidence.subject_reference,
                purpose=purpose, verified_at=now,
            ))
            invalidate_on_change(dictation, now, reason="context_changed")
    except IntegrityError:
        if db.session.scalar(used) is not None:
            raise ContextGrantError("grant_replayed") from None
        raise
    return revision
