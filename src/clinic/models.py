"""Clinic dictation tables on the existing Speakr database.

Additive models only. ``user`` and ``recording`` stay the upstream tables;
these rows point at ``user.id`` and ``recording.id`` and do not copy them.
New primary keys are UUID strings so the same declarations work on SQLite
and PostgreSQL. A release cannot cite a revision from another dictation:
the foreign key targets ``(dictation_id, id)``, not ``id`` alone.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, func, text

from src.database import db


def new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _created_at():
    """timestamptz, UTC. The UI converts to Europe/Zurich."""
    return db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )


TRANSCRIPT_KINDS = ("ASR", "HUMAN")
DELIVERY_STATES = ("PENDING", "SENT", "ACKNOWLEDGED", "FAILED", "UNKNOWN")


def _uuid():
    return db.Column(db.String(36), primary_key=True, default=new_id)


def _in_check(column, values, name):
    listed = ", ".join("'" + value + "'" for value in values)
    return CheckConstraint(column + " IN (" + listed + ")", name=name)


class ClinicDictation(db.Model):
    """One clinical dictation, bound to one existing recording and its author."""

    __tablename__ = "clinic_dictation"

    id = _uuid()
    recording_id = db.Column(
        db.Integer, db.ForeignKey("recording.id"), nullable=False, unique=True
    )
    author_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    version_counter = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    # clinic_pool is a separate work package; until it exists this is a plain
    # UUID reference without a foreign key.
    pool_id = db.Column(db.String(36), nullable=True, index=True)
    created_at = _created_at()


class ClinicOriginal(db.Model):
    """Immutable stored original. Bytes live under storage_key; this row is the receipt."""

    __tablename__ = "clinic_original_version"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    content_hash = db.Column(db.String(128), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    storage_key = db.Column(db.String(500), nullable=False)
    receipt = db.Column(db.Text, nullable=False)
    origin_ref = db.Column(db.String(255), nullable=False)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_original_dictation"
        ),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_original_dictation"),
    )


class ClinicAudioRevision(db.Model):
    """Non-destructive audio edit. The original row is never the edited audio."""

    __tablename__ = "clinic_audio_revision"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    parent_revision_id = db.Column(db.String(36), nullable=True)
    content_hash = db.Column(db.String(128), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_dictation"
        ),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_audio_dictation"),
        ForeignKeyConstraint(
            ["dictation_id", "parent_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
            name="fk_clinic_audio_parent_same_dictation",
        ),
    )


class ClinicTranscriptRevision(db.Model):
    """ASR or human text. A later result is a new row, not an overwrite of a correction."""

    __tablename__ = "clinic_transcript_revision"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    parent_revision_id = db.Column(db.String(36), nullable=True)
    kind = db.Column(db.String(16), nullable=False)
    body = db.Column(db.Text, nullable=False)
    normalised_text_hash = db.Column(db.String(128), nullable=False)
    original_version_id = db.Column(db.String(36), nullable=False)
    # Modellnachweis. Required for ASR rows, empty for human corrections.
    model_ref = db.Column(db.String(255), nullable=True)
    # Who saved the row and when. NULL on rows written before these columns
    # existed; never backfilled from the uploader or the dictating person.
    revision_author_user_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_transcript_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "original_version_id"],
            ["clinic_original_version.dictation_id", "clinic_original_version.id"],
            name="fk_clinic_transcript_original_same_dictation",
        ),
        CheckConstraint(
            "kind <> 'ASR' OR model_ref IS NOT NULL", name="ck_clinic_transcript_asr_model"
        ),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_transcript_dictation"),
        UniqueConstraint(
            "dictation_id", "id", "normalised_text_hash",
            name="uq_clinic_transcript_dictation_hash",
        ),
        ForeignKeyConstraint(
            ["dictation_id", "parent_revision_id"],
            ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"],
            name="fk_clinic_transcript_parent_same_dictation",
        ),
        _in_check("kind", TRANSCRIPT_KINDS, "ck_clinic_transcript_kind"),
    )


class ClinicContextRevision(db.Model):
    """Checked case context. Case numbers only match inside source and tenant."""

    __tablename__ = "clinic_context_revision"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    parent_revision_id = db.Column(db.String(36), nullable=True)
    source_system = db.Column(db.String(120), nullable=True)
    tenant_scope = db.Column(db.String(120), nullable=True)
    case_ref = db.Column(db.String(120), nullable=True)
    patient_ref = db.Column(db.String(120), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_context_dictation"
        ),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_context_dictation"),
        ForeignKeyConstraint(
            ["dictation_id", "parent_revision_id"],
            ["clinic_context_revision.dictation_id", "clinic_context_revision.id"],
            name="fk_clinic_context_parent_same_dictation",
        ),
    )


class ClinicReviewLease(db.Model):
    """One holder per dictation. generation is a fencing token and only moves forward."""

    __tablename__ = "clinic_review_lease"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, unique=True)
    holder_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    generation = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_lease_dictation"
        ),
        CheckConstraint("generation >= 1", name="ck_clinic_lease_generation"),
    )


class ClinicRelease(db.Model):
    """One checked state: audio, text and context of this dictation, plus the text hash."""

    __tablename__ = "clinic_release"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    audio_revision_id = db.Column(db.String(36), nullable=False)
    transcript_revision_id = db.Column(db.String(36), nullable=False)
    context_revision_id = db.Column(db.String(36), nullable=False)
    text_hash = db.Column(db.String(128), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    document_type = db.Column(db.String(80), nullable=False)
    release_type = db.Column(db.String(80), nullable=False)
    # Record of the permission decision taken at release time (JSON text).
    authorization_decision = db.Column(db.Text, nullable=False)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_release_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
            name="fk_clinic_release_audio_same_dictation",
        ),
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id", "text_hash"],
            [
                "clinic_transcript_revision.dictation_id",
                "clinic_transcript_revision.id",
                "clinic_transcript_revision.normalised_text_hash",
            ],
            name="fk_clinic_release_transcript_same_dictation",
        ),
        ForeignKeyConstraint(
            ["dictation_id", "context_revision_id"],
            ["clinic_context_revision.dictation_id", "clinic_context_revision.id"],
            name="fk_clinic_release_context_same_dictation",
        ),
    )


class ClinicDelivery(db.Model):
    """Dispatch ledger. UNKNOWN is its own state and is not a failed retry."""

    __tablename__ = "clinic_delivery"

    id = _uuid()
    release_id = db.Column(db.String(36), db.ForeignKey("clinic_release.id"), nullable=False)
    target = db.Column(db.String(80), nullable=False)
    idempotency_key = db.Column(db.String(255), nullable=False)
    payload_hash = db.Column(db.String(128), nullable=False)
    state = db.Column(db.String(20), nullable=False)
    # Receipt id from the receiving system. Empty until it confirms.
    external_document_id = db.Column(db.String(255), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        # Same key and target cannot carry a second payload. A different target
        # is a different delivery. Same key with a different payload conflicts.
        UniqueConstraint(
            "idempotency_key", "target", name="uq_clinic_delivery_idempotency"
        ),
        _in_check("state", DELIVERY_STATES, "ck_clinic_delivery_state"),
    )


class ClinicArchiveEvent(db.Model):
    """Durable archive evidence, independent of account/recording deletion.

    The unique revision prevents two stale writers from committing evidence
    for the same transition. The timestamp distinguishes reused SQLite ids.
    No foreign keys or cascades may remove this evidence.
    """

    __tablename__ = "clinic_archive_event"

    id = _uuid()
    recording_id = db.Column(db.Integer, nullable=False, index=True)
    recording_created_at = db.Column(db.DateTime, nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    audit_event_id = db.Column(db.BigInteger, nullable=False)
    event = db.Column(db.String(48), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    actor_kind = db.Column(db.String(12), nullable=False)
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_token_id = db.Column(db.Integer, nullable=True)
    recording_owner_id = db.Column(db.Integer, nullable=True)
    target_user_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint('recording_id', 'recording_created_at', 'revision',
                         name='uq_clinic_archive_revision'),
        CheckConstraint('revision > 0', name='ck_clinic_archive_revision'),
        _in_check('event', ('recording.archived', 'recording.unarchived'),
                  'ck_clinic_archive_event'),
    )


_ORIGINAL_TRIGGER = "clinic_original_immutable"
_ORIGINAL_DELETE_TRIGGER = "clinic_original_no_delete"
_LEASE_TRIGGER = "clinic_lease_generation_monotone"
_TRANSCRIPT_TRIGGER = "clinic_transcript_immutable"
_TRANSCRIPT_DELETE_TRIGGER = "clinic_transcript_no_delete"


def install_clinic_guards(engine) -> None:
    """Reject original and transcript revision UPDATE and DELETE and a non-increasing lease generation.

    Idempotent. SQLite and PostgreSQL need different trigger DDL; other
    engines keep the tables without these two guards.
    """
    if engine.name == "sqlite":
        statements = (
            "DROP TRIGGER IF EXISTS " + _ORIGINAL_TRIGGER,
            (
                "CREATE TRIGGER " + _ORIGINAL_TRIGGER + " "
                "BEFORE UPDATE ON clinic_original_version "
                "FOR EACH ROW "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic original is immutable'); "
                "END"
            ),
            "DROP TRIGGER IF EXISTS " + _ORIGINAL_DELETE_TRIGGER,
            (
                "CREATE TRIGGER " + _ORIGINAL_DELETE_TRIGGER + " "
                "BEFORE DELETE ON clinic_original_version "
                "FOR EACH ROW "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic original cannot be deleted'); "
                "END"
            ),
            "DROP TRIGGER IF EXISTS " + _TRANSCRIPT_TRIGGER,
            (
                "CREATE TRIGGER " + _TRANSCRIPT_TRIGGER + " "
                "BEFORE UPDATE ON clinic_transcript_revision "
                "FOR EACH ROW "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic transcript revision is immutable'); "
                "END"
            ),
            "DROP TRIGGER IF EXISTS " + _TRANSCRIPT_DELETE_TRIGGER,
            (
                "CREATE TRIGGER " + _TRANSCRIPT_DELETE_TRIGGER + " "
                "BEFORE DELETE ON clinic_transcript_revision "
                "FOR EACH ROW "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic transcript revision cannot be deleted'); "
                "END"
            ),
            "DROP TRIGGER IF EXISTS " + _LEASE_TRIGGER,
            (
                "CREATE TRIGGER " + _LEASE_TRIGGER + " "
                "BEFORE UPDATE OF generation ON clinic_review_lease "
                "FOR EACH ROW "
                "WHEN NEW.generation <= OLD.generation "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic review lease generation must increase'); "
                "END"
            ),
        )
    elif engine.name == "postgresql":
        statements = (
            """
            CREATE OR REPLACE FUNCTION clinic_original_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'clinic original is immutable'
                    USING ERRCODE = 'check_violation';
            END;
            $$
            """,
            "DROP TRIGGER IF EXISTS " + _ORIGINAL_TRIGGER + " ON clinic_original_version",
            """
            CREATE TRIGGER clinic_original_immutable
            BEFORE UPDATE OR DELETE ON clinic_original_version
            FOR EACH ROW EXECUTE PROCEDURE clinic_original_immutable()
            """,
            """
            CREATE OR REPLACE FUNCTION clinic_transcript_immutable()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'clinic transcript revision is immutable'
                    USING ERRCODE = 'check_violation';
            END;
            $$
            """,
            "DROP TRIGGER IF EXISTS " + _TRANSCRIPT_TRIGGER + " ON clinic_transcript_revision",
            """
            CREATE TRIGGER clinic_transcript_immutable
            BEFORE UPDATE OR DELETE ON clinic_transcript_revision
            FOR EACH ROW EXECUTE PROCEDURE clinic_transcript_immutable()
            """,
            """
            CREATE OR REPLACE FUNCTION clinic_lease_generation_monotone()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.generation <= OLD.generation THEN
                    RAISE EXCEPTION 'clinic review lease generation must increase'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
            "DROP TRIGGER IF EXISTS " + _LEASE_TRIGGER + " ON clinic_review_lease",
            """
            CREATE TRIGGER clinic_lease_generation_monotone
            BEFORE UPDATE OF generation ON clinic_review_lease
            FOR EACH ROW EXECUTE PROCEDURE clinic_lease_generation_monotone()
            """,
        )
    else:
        return

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    from src.clinic.archive import install_archive_guards
    install_archive_guards(engine)
