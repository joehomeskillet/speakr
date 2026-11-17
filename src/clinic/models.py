"""Clinic dictation tables on the existing Speakr database.

Additive models only. ``user`` and ``recording`` stay the upstream tables;
these rows point at ``user.id`` and ``recording.id`` and do not copy them.
New primary keys are UUID strings so the same declarations work on SQLite
and PostgreSQL. A release cannot cite a revision from another dictation:
the foreign key targets ``(dictation_id, id)``, not ``id`` alone.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, text

from src.database import db


def new_id() -> str:
    return str(uuid.uuid4())


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


class ClinicOriginal(db.Model):
    """Immutable stored original. Bytes live under storage_key; this row is the receipt."""

    __tablename__ = "clinic_original"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    content_hash = db.Column(db.String(128), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    storage_key = db.Column(db.String(500), nullable=False)
    receipt = db.Column(db.Text, nullable=False)

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

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_transcript_dictation"
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

    __table_args__ = (
        # Same key and target cannot carry a second payload. A different target
        # is a different delivery. Same key with a different payload conflicts.
        UniqueConstraint(
            "idempotency_key", "target", name="uq_clinic_delivery_idempotency"
        ),
        _in_check("state", DELIVERY_STATES, "ck_clinic_delivery_state"),
    )


_ORIGINAL_TRIGGER = "clinic_original_immutable"
_LEASE_TRIGGER = "clinic_lease_generation_monotone"


def install_clinic_guards(engine) -> None:
    """Reject in-place original edits and a non-increasing lease generation.

    Idempotent. SQLite and PostgreSQL need different trigger DDL; other
    engines keep the tables without these two guards.
    """
    if engine.name == "sqlite":
        statements = (
            "DROP TRIGGER IF EXISTS " + _ORIGINAL_TRIGGER,
            (
                "CREATE TRIGGER " + _ORIGINAL_TRIGGER + " "
                "BEFORE UPDATE ON clinic_original "
                "FOR EACH ROW "
                "BEGIN "
                "SELECT RAISE(ABORT, 'clinic original is immutable'); "
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
            "DROP TRIGGER IF EXISTS " + _ORIGINAL_TRIGGER + " ON clinic_original",
            """
            CREATE TRIGGER clinic_original_immutable
            BEFORE UPDATE ON clinic_original
            FOR EACH ROW EXECUTE PROCEDURE clinic_original_immutable()
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
