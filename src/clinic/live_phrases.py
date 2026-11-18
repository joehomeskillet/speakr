"""Durable final live phrases. The caller supplies an IngestService storage root.

The audio is fsynced before the phrase and session receipt commit together.
If the database fails, a retry can adopt the already published audio object.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from src.clinic import audio_revisions, ingest, live_session, revisions
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicTranscriptRevision, _created_at, _uuid
from src.database import db


class PhraseError(ValueError):
    """Invalid, conflicting, or stale final phrase (``reason`` is stable)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ClinicLivePhrase(db.Model):
    """One immutable envelope per session sequence, with its durable audio key."""

    __tablename__ = "clinic_live_phrase"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    session_id = db.Column(db.String(36), nullable=False)
    generation = db.Column(db.Integer, nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    event_id = db.Column(db.String(128), nullable=False)
    audio_revision_id = db.Column(db.String(36), nullable=False)
    audio_start_ms = db.Column(db.Integer, nullable=False)
    audio_end_ms = db.Column(db.Integer, nullable=False)
    audio_sha256 = db.Column(db.String(64), nullable=False)
    audio_size = db.Column(db.BigInteger, nullable=False)
    audio_storage_key = db.Column(db.String(500), nullable=False)
    transcript_revision_id = db.Column(db.String(36), nullable=False)
    text_hash = db.Column(db.String(128), nullable=False)
    envelope_hash = db.Column(db.String(64), nullable=False)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"]),
        ForeignKeyConstraint(["session_id"], ["clinic_live_session.id"]),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
        ),
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id", "text_hash"],
            ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id",
             "clinic_transcript_revision.normalised_text_hash"],
        ),
        UniqueConstraint("session_id", "sequence", name="uq_clinic_live_phrase_seq"),
        UniqueConstraint("session_id", "event_id", name="uq_clinic_live_phrase_event"),
        CheckConstraint("generation > 0 AND sequence > 0 AND audio_start_ms >= 0 "
                        "AND audio_end_ms > audio_start_ms AND audio_size > 0",
                        name="ck_clinic_live_phrase_range"),
    )


@dataclass(frozen=True)
class PhraseReceipt:
    phrase_id: str
    session_id: str
    generation: int
    sequence: int
    event_id: str
    audio_storage_key: str
    audio_sha256: str
    text_hash: str
    status: str = "server_saved"


def create_tables(bind=None) -> None:
    """Schema entry point for the integrator's model registration/migration."""
    ClinicLivePhrase.__table__.create(bind=bind or db.engine, checkfirst=True)


def store_phrase(
    store: ingest.IngestService, *, dictation_id: str, session_id: str,
    user_id: int, client_id: str | None, now: datetime, generation: int,
    sequence: int, event_id: str, audio_start_ms: int, audio_end_ms: int,
    audio: bytes, audio_revision_id: str, transcript_revision_id: str,
    text_hash: str,
) -> PhraseReceipt:
    """Save a final phrase, committing the audio metadata and live receipt.

    Callers must authenticate and authorize the dictation at the HTTP boundary.
    This service also fences writes against the current live holder.
    """
    for name, value, minimum in (
        ("generation", generation, 1), ("sequence", sequence, 1),
        ("audio_start_ms", audio_start_ms, 0), ("audio_end_ms", audio_end_ms, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise PhraseError("invalid_" + name)
    if audio_end_ms <= audio_start_ms or not isinstance(audio, bytes) or not audio:
        raise PhraseError("invalid_audio")
    if not isinstance(event_id, str) or not 0 < len(event_id) <= 128 or event_id.strip() != event_id:
        raise PhraseError("invalid_event_id")
    for value in (dictation_id, session_id, audio_revision_id, transcript_revision_id, text_hash):
        if not isinstance(value, str) or not value:
            raise PhraseError("invalid_envelope")
    ingest._checked_size(len(audio))
    audio_hash = hashlib.sha256(audio).hexdigest()
    fields = [dictation_id, session_id, generation, sequence, event_id,
              audio_revision_id, audio_start_ms, audio_end_ms, audio_hash,
              len(audio), transcript_revision_id, text_hash]
    envelope_hash = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
    key = f"live:{session_id}:{sequence}"
    storage_key = ingest.storage_key_for(key)

    try:
        session = live_session.heartbeat(session_id, user_id, now)
        if session.dictation_id != dictation_id or (
            session.client_id is not None and session.client_id != client_id
        ):
            raise live_session.LiveSessionError(live_session.NOT_HOLDER, session)

        existing = ClinicLivePhrase.query.filter_by(session_id=session_id, sequence=sequence).one_or_none()
        if existing is not None:
            if not hmac.compare_digest(existing.envelope_hash, envelope_hash):
                raise PhraseError("sequence_conflict")
            if store.stored_path(idempotency_key=key, declared_sha256=audio_hash,
                                 declared_size=len(audio)) is None:
                raise PhraseError("storage_missing")
            receipt = _receipt(existing)
            db.session.commit()
            return receipt

        previous = (ClinicLivePhrase.query.filter_by(session_id=session_id)
                    .order_by(ClinicLivePhrase.sequence.desc()).first())
        if (generation < (previous.generation if previous else 1)
                or sequence != session.ack_seq + 1
                or audio_start_ms != session.audio_offset_ms):
            raise PhraseError("stale_sequence")
        if ClinicLivePhrase.query.filter_by(
            session_id=session_id, event_id=event_id
        ).first() is not None:
            raise PhraseError("event_conflict")
        dictation = db.session.get(ClinicDictation, dictation_id)
        revision = db.session.get(ClinicTranscriptRevision, transcript_revision_id)
        audio_revision = db.session.get(ClinicAudioRevision, audio_revision_id)
        if dictation is None:
            raise PhraseError("stale_version")
        head = revisions.current_revision(dictation)
        current_audio = audio_revisions.current_audio_revision(dictation)
        if (revision is None or revision.dictation_id != dictation_id
                or not hmac.compare_digest(revision.normalised_text_hash, text_hash)
                or head is None or head.id != revision.id
                or audio_revision is None or audio_revision.dictation_id != dictation_id
                or current_audio is None or current_audio.id != audio_revision_id):
            raise PhraseError("stale_version")

        path = store.file_path(storage_key)
        spool, digest = ingest._spool(BytesIO(audio), path.parent, len(audio))
        try:
            if not hmac.compare_digest(digest, audio_hash):
                raise PhraseError("audio_hash_mismatch")
            store._publish(spool, path, audio_hash)
        finally:
            spool.unlink(missing_ok=True)
        phrase = ClinicLivePhrase(
            dictation_id=dictation_id, session_id=session_id, generation=generation,
            sequence=sequence, event_id=event_id, audio_revision_id=audio_revision_id,
            audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms,
            audio_sha256=audio_hash, audio_size=len(audio), audio_storage_key=storage_key,
            transcript_revision_id=transcript_revision_id, text_hash=text_hash,
            envelope_hash=envelope_hash,
        )
        db.session.add(phrase)
        live_session.acknowledge(session_id, user_id, now, sequence, audio_end_ms, transcript_revision_id)
        db.session.commit()
        return _receipt(phrase)
    except Exception:
        db.session.rollback()
        raise


def _receipt(row: ClinicLivePhrase) -> PhraseReceipt:
    return PhraseReceipt(row.id, row.session_id, row.generation, row.sequence,
                         row.event_id, row.audio_storage_key, row.audio_sha256, row.text_hash)
