"""Voice profile consent: opt-in, logged, revocable. Embeddings only, users only.

Owner decision 2026-11-18. A voice profile of a user (purpose ``author_recognition``)
may exist only while that user's own consent is active.

* ``ClinicVoiceConsent`` is append-only. A grant and a revocation are each a new
  row (who, when, purpose, SHA-256 of the declaration text shown, status). Rows
  are never updated or deleted; the ORM refuses it. The latest row decides.
* Revocation deletes every embedding of the user in all embedding spaces in the
  same transaction as the revocation row and writes an audit row with the number
  of deleted vectors. The consent history stays.
* ``enrol`` stores one embedding and refuses without active consent. The API takes
  only ``user_id``: there is no way to name a patient or any other identity.
* Only embeddings are stored, no audio. Reads of the vectors belong to admin and
  service code; this module exposes no read of vector bytes.

Scope of the deletion: the vectors enrolled here, in every embedding space.
Address-book ``Speaker`` profiles and ``SpeakerVoiceSample`` rows describe other
people and are not touched.

No Flask routes live here. Callers supply the signed-in user id.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, event

from src.database import db
from src.models import User
from src.models.voice import VoiceEmbeddingSpace

PURPOSE = "author_recognition"
STATUSES = ("granted", "revoked")
AUDIT_ACTIONS = ("enrolled", "embeddings_deleted")
_FLOAT32 = 4
_MAX_DIM = 8192
_MAX_STATEMENT = 20000


class VoiceConsentError(ValueError):
    """The operation was rejected before it changed state."""

    def __init__(self, code: str, message: Optional[str] = None):
        self.code = code
        super().__init__(message or code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClinicVoiceConsent(db.Model):
    """Append-only consent history. One row per grant or revocation."""

    __tablename__ = "clinic_voice_consent"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    purpose = db.Column(db.String(32), nullable=False, default=PURPOSE)
    status = db.Column(db.String(16), nullable=False)
    statement_hash = db.Column(db.String(64), nullable=True)  # SHA-256 of the declaration shown
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    __table_args__ = (
        CheckConstraint("status IN ('granted','revoked')", name="ck_clinic_voice_consent_status"),
        CheckConstraint("purpose IN ('author_recognition')", name="ck_clinic_voice_consent_purpose"),
    )


class ClinicVoiceEmbedding(db.Model):
    """One enrolled voice embedding of a user. Vector only, never audio."""

    __tablename__ = "clinic_voice_embedding"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False, index=True)
    space_id = db.Column(db.Integer, nullable=True)  # VoiceEmbeddingSpace.id; NULL: legacy space
    dimension = db.Column(db.Integer, nullable=False)
    embedding = db.Column(db.LargeBinary, nullable=False)  # float32
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)


class ClinicVoiceConsentAudit(db.Model):
    """Append-only audit of enrolments and consent-driven deletions."""

    __tablename__ = "clinic_voice_consent_audit"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False, index=True)
    action = db.Column(db.String(24), nullable=False)
    vector_count = db.Column(db.Integer, nullable=False)
    consent_id = db.Column(db.Integer, nullable=True)  # the revocation row for embeddings_deleted
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    __table_args__ = (
        CheckConstraint("action IN ('enrolled','embeddings_deleted')", name="ck_clinic_voice_audit_action"),
    )


VOICE_CONSENT_TABLES = (ClinicVoiceConsent, ClinicVoiceEmbedding, ClinicVoiceConsentAudit)


def _refuse_change(_mapper, _connection, target):
    raise VoiceConsentError("append_only", type(target).__name__ + " is append-only")


for _model in (ClinicVoiceConsent, ClinicVoiceConsentAudit):
    event.listen(_model, "before_update", _refuse_change)
    event.listen(_model, "before_delete", _refuse_change)


def create_tables(bind=None):
    db.metadata.create_all(
        bind=bind or db.engine, tables=[m.__table__ for m in VOICE_CONSENT_TABLES], checkfirst=True
    )


@dataclass(frozen=True)
class EmbeddingRef:
    """A user's embedding: float32 bytes, its dimension and the embedding space it lives in."""

    vector: bytes
    dimension: int
    space_id: Optional[int] = None


@dataclass(frozen=True)
class ConsentState:
    state: str  # "none" | "granted" | "revoked"
    granted_at: Optional[datetime]
    revoked_at: Optional[datetime]


def _check_user_id(user_id) -> None:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise VoiceConsentError("invalid_user_id", "user_id must be a positive int")
    if db.session.get(User, user_id) is None:
        raise VoiceConsentError("unknown_user", "no such user")


def _latest(user_id: int) -> Optional[ClinicVoiceConsent]:
    return (
        ClinicVoiceConsent.query.filter_by(user_id=user_id, purpose=PURPOSE)
        .order_by(ClinicVoiceConsent.id.desc())
        .first()
    )


def consent_state(user_id: int) -> ConsentState:
    """Current state with the time of the latest grant and the latest revocation."""
    _check_user_id(user_id)
    latest = _latest(user_id)
    if latest is None:
        return ConsentState("none", None, None)
    rows = ClinicVoiceConsent.query.filter_by(user_id=user_id, purpose=PURPOSE).order_by(ClinicVoiceConsent.id).all()
    granted = [r.created_at for r in rows if r.status == "granted"]
    revoked = [r.created_at for r in rows if r.status == "revoked"]
    return ConsentState(
        latest.status, granted[-1] if granted else None, revoked[-1] if revoked else None
    )


def has_active_consent(user_id: int) -> bool:
    return consent_state(user_id).state == "granted"


def consent_history(user_id: int) -> list:
    """Every grant and revocation of the user, oldest first. Includes the statement hash, never the text."""
    _check_user_id(user_id)
    rows = ClinicVoiceConsent.query.filter_by(user_id=user_id).order_by(ClinicVoiceConsent.id).all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "actor_user_id": r.actor_user_id,
            "purpose": r.purpose,
            "status": r.status,
            "statement_hash": r.statement_hash,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def audit_entries(user_id: int) -> list:
    _check_user_id(user_id)
    rows = ClinicVoiceConsentAudit.query.filter_by(user_id=user_id).order_by(ClinicVoiceConsentAudit.id).all()
    return [
        {"action": r.action, "vector_count": r.vector_count, "consent_id": r.consent_id, "created_at": r.created_at}
        for r in rows
    ]


def _own_action(actor_user_id, user_id) -> None:
    _check_user_id(user_id)
    if actor_user_id != user_id:
        # Consent is the user's own act. Admins and services cannot give it for someone.
        raise VoiceConsentError("not_self", "consent can only be given or revoked by the user")


def grant_consent(*, actor_user_id: int, user_id: int, statement_text: str) -> ClinicVoiceConsent:
    """Record the user's opt-in. ``statement_text`` is the declaration shown; only its hash is kept."""
    _own_action(actor_user_id, user_id)
    if not isinstance(statement_text, str) or not statement_text.strip():
        raise VoiceConsentError("statement_required", "the declaration text is required")
    if len(statement_text) > _MAX_STATEMENT:
        raise VoiceConsentError("statement_too_long")
    if has_active_consent(user_id):
        raise VoiceConsentError("already_granted")
    row = ClinicVoiceConsent(
        user_id=user_id,
        actor_user_id=actor_user_id,
        purpose=PURPOSE,
        status="granted",
        statement_hash=hashlib.sha256(statement_text.encode("utf-8")).hexdigest(),
    )
    db.session.add(row)
    db.session.commit()
    return row


def revoke_consent(*, actor_user_id: int, user_id: int) -> int:
    """Record the revocation and delete all embeddings of the user. Returns the number deleted.

    One transaction: the revocation row, the deletion and the audit row commit together
    or not at all.
    """
    _own_action(actor_user_id, user_id)
    if not has_active_consent(user_id):
        raise VoiceConsentError("no_active_consent")
    try:
        row = ClinicVoiceConsent(
            user_id=user_id, actor_user_id=actor_user_id, purpose=PURPOSE, status="revoked"
        )
        db.session.add(row)
        db.session.flush()
        deleted = ClinicVoiceEmbedding.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.add(
            ClinicVoiceConsentAudit(
                user_id=user_id, action="embeddings_deleted", vector_count=deleted, consent_id=row.id
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return deleted


def _check_ref(ref) -> None:
    if not isinstance(ref, EmbeddingRef):
        raise VoiceConsentError("invalid_embedding", "embedding_ref must be an EmbeddingRef")
    dim = ref.dimension
    if isinstance(dim, bool) or not isinstance(dim, int) or not 1 <= dim <= _MAX_DIM:
        raise VoiceConsentError("invalid_embedding", "dimension out of range")
    if not isinstance(ref.vector, (bytes, bytearray)) or len(ref.vector) != dim * _FLOAT32:
        raise VoiceConsentError("invalid_embedding", "vector must be dimension float32 values")
    if ref.space_id is not None:
        if isinstance(ref.space_id, bool) or not isinstance(ref.space_id, int):
            raise VoiceConsentError("invalid_space", "space_id must be an int or None")
        if db.session.get(VoiceEmbeddingSpace, ref.space_id) is None:
            raise VoiceConsentError("invalid_space", "unknown embedding space")


def enrol(user_id: int, embedding_ref: EmbeddingRef) -> ClinicVoiceEmbedding:
    """Store one embedding of the user. Refused without that user's active consent."""
    _check_user_id(user_id)
    if not has_active_consent(user_id):
        raise VoiceConsentError("consent_required", "no active voice profile consent")
    _check_ref(embedding_ref)
    row = ClinicVoiceEmbedding(
        user_id=user_id,
        space_id=embedding_ref.space_id,
        dimension=embedding_ref.dimension,
        embedding=bytes(embedding_ref.vector),
    )
    db.session.add(row)
    db.session.add(ClinicVoiceConsentAudit(user_id=user_id, action="enrolled", vector_count=1))
    db.session.commit()
    return row


def embedding_count(user_id: int) -> int:
    """Number of enrolled vectors of the user, for status display. No vector bytes."""
    _check_user_id(user_id)
    return ClinicVoiceEmbedding.query.filter_by(user_id=user_id).count()
