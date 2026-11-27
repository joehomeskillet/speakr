"""Append-only evidence supplied by recognition engines, never inferred marks.

Callers provide the real source reference and run ID, including when identical
ASR text reuses a transcript revision. Reads require that exact run and revision
pair. Helpers flush only; the caller owns the transaction and authorization.
"""

import math

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, event, inspect

from src.clinic.append_only_guards import install_append_only_guards
from src.clinic.models import (
    ClinicAudioRevision, ClinicDictation, ClinicTranscriptRevision,
    _created_at, _in_check, _uuid,
)
from src.database import db

MARK_KINDS = ("recognition", "command", "uncertain", "unrecognized")


class RecognitionMarkError(ValueError):
    """Invalid evidence or an attempt to change stored evidence."""


class ClinicRecognitionMark(db.Model):
    __tablename__ = "clinic_recognition_mark"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    audio_revision_id = db.Column(db.String(36), nullable=False)
    transcript_revision_id = db.Column(db.String(36), nullable=False)
    run_id = db.Column(db.String(255), nullable=False)
    start_ms = db.Column(db.BigInteger, nullable=False)
    end_ms = db.Column(db.BigInteger, nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    source = db.Column(db.String(500), nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"]),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
        ),
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id"],
            ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"],
        ),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"),
        CheckConstraint("length(trim(source)) > 0 AND length(trim(run_id)) > 0"),
        _in_check("kind", MARK_KINDS, "ck_clinic_recognition_mark_kind"),
        Index("ix_clinic_recognition_mark_binding", "dictation_id", "audio_revision_id",
              "transcript_revision_id", "run_id"),
    )


def _immutable(_mapper, _connection, _target):
    raise RecognitionMarkError("recognition evidence is append-only")


event.listen(ClinicRecognitionMark, "before_update", _immutable)
event.listen(ClinicRecognitionMark, "before_delete", _immutable)


def create_tables(bind=None):
    engine = bind if bind is not None else db.engine
    ClinicRecognitionMark.__table__.create(engine, checkfirst=True)
    install_append_only_guards(engine)


def add_mark(dictation, audio_revision, transcript_revision, *, run_id,
             start_ms, end_ms, kind, source, confidence=None):
    """Store one explicitly delivered mark, preserving unknown confidence as NULL."""
    for row, model in ((dictation, ClinicDictation), (audio_revision, ClinicAudioRevision),
                       (transcript_revision, ClinicTranscriptRevision)):
        if not isinstance(row, model) or not inspect(row).persistent:
            raise RecognitionMarkError("binding requires persisted dictation and revisions")
    if any(row.dictation_id != dictation.id for row in (audio_revision, transcript_revision)):
        raise RecognitionMarkError("revision belongs to another dictation")
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        raise RecognitionMarkError("times must be integer milliseconds with 0 <= start < end")
    if kind not in MARK_KINDS:
        raise RecognitionMarkError("unknown recognition mark kind")
    for name, value, limit in (("source", source, 500), ("run_id", run_id, 255)):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RecognitionMarkError(name + " must be a nonempty bounded source reference")
    if confidence is not None and (
        type(confidence) not in (int, float) or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise RecognitionMarkError("confidence must be unknown or a finite number in [0, 1]")
    mark = ClinicRecognitionMark(
        dictation_id=dictation.id, audio_revision_id=audio_revision.id,
        transcript_revision_id=transcript_revision.id, run_id=run_id,
        start_ms=start_ms, end_ms=end_ms, kind=kind, source=source, confidence=confidence,
    )
    db.session.add(mark)
    db.session.flush()
    return mark


def marks_for(dictation_id, audio_revision_id, transcript_revision_id, *, run_id):
    """Exact binding only; no fallback to evidence from another run or revision."""
    return db.session.query(ClinicRecognitionMark).filter_by(
        dictation_id=dictation_id, audio_revision_id=audio_revision_id,
        transcript_revision_id=transcript_revision_id, run_id=run_id,
    ).order_by(ClinicRecognitionMark.start_ms, ClinicRecognitionMark.end_ms,
               ClinicRecognitionMark.id).all()
