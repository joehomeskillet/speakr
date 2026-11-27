"""Append-only evidence supplied by recognition engines, never inferred marks.

Callers provide the real source reference and run ID, including when identical
ASR text reuses a transcript revision. Reads require that exact run and revision
pair. Helpers flush only; the caller owns the transaction and authorization.
"""

import math

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, event, inspect

from src.clinic.append_only_guards import install_append_only_guards
from src.clinic.audio_revisions import ClinicAudioRevisionSnapshot, current_audio_revision
from src.clinic.models import (
    ClinicAudioRevision, ClinicDictation, ClinicTranscriptRevision,
    _created_at, _in_check, _uuid,
)
from src.clinic.recognition_marks import recognition_marks
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


class ClinicCurrentRecognitionRun(db.Model):
    __tablename__ = "clinic_current_recognition_run"

    dictation_id = db.Column(db.String(36), db.ForeignKey("clinic_dictation.id"), primary_key=True)
    audio_revision_id = db.Column(db.String(36), primary_key=True)
    transcript_revision_id = db.Column(db.String(36), primary_key=True)
    run_id = db.Column(db.String(255), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["dictation_id", "audio_revision_id"],
                             ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"]),
        ForeignKeyConstraint(["dictation_id", "transcript_revision_id"],
                             ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"]),
    )


def _immutable(_mapper, _connection, _target):
    raise RecognitionMarkError("recognition evidence is append-only")


event.listen(ClinicRecognitionMark, "before_update", _immutable)
event.listen(ClinicRecognitionMark, "before_delete", _immutable)


def create_tables(bind=None):
    engine = bind if bind is not None else db.engine
    ClinicRecognitionMark.__table__.create(engine, checkfirst=True)
    ClinicCurrentRecognitionRun.__table__.create(engine, checkfirst=True)
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


def current_marks(dictation_id, audio_revision_id, transcript_revision_id):
    """Read one exact run. Legacy evidence is usable when its run is unambiguous."""
    binding = (dictation_id, audio_revision_id, transcript_revision_id)
    current = db.session.get(ClinicCurrentRecognitionRun, binding)
    if current is not None:
        return marks_for(*binding, run_id=current.run_id)
    runs = db.session.query(ClinicRecognitionMark.run_id).filter_by(
        dictation_id=dictation_id, audio_revision_id=audio_revision_id,
        transcript_revision_id=transcript_revision_id,
    ).distinct().limit(2).all()
    return marks_for(*binding, run_id=runs[0].run_id) if len(runs) == 1 else []


def store_asr_marks(dictation, audio, revision, *, run_id, model_ref, segments, duration_ms):
    """Write measured spans and select the accepted run, even when it has no marks."""
    current = db.session.get(ClinicCurrentRecognitionRun, (dictation.id, audio.id, revision.id))
    if current is not None and current.run_id == run_id:
        return
    report = recognition_marks(segments)
    for mark in report.marks:
        if mark.end_ms > duration_ms:
            continue
        if mark.source == "word":
            metric, confidence = "probability", mark.value
        else:
            metric = "no_speech_prob" if mark.kind == "no_speech" else "avg_logprob"
            confidence = None
        add_mark(dictation, audio, revision, run_id=run_id, start_ms=mark.start_ms, end_ms=mark.end_ms,
                 kind="uncertain", source=f"{model_ref}/{mark.source}/{metric}", confidence=confidence)
    if current is None:
        db.session.add(ClinicCurrentRecognitionRun(
            dictation_id=dictation.id, audio_revision_id=audio.id,
            transcript_revision_id=revision.id, run_id=run_id,
        ))
    else:
        current.run_id = run_id


def store_result(dictation, original, revision, *, run_id, model_ref, segments, verified_repeat_audio):
    """Bind original input to its root revision, or use the repeat's verified EDL."""
    audio = current_audio_revision(dictation)
    if audio is None or (not verified_repeat_audio and audio.parent_revision_id is not None):
        return
    snapshot = ClinicAudioRevisionSnapshot.query.filter_by(
        dictation_id=dictation.id, audio_revision_id=audio.id, original_id=original.id).one_or_none()
    if snapshot is None:
        return
    measurements = (segment if isinstance(segment, dict) else vars(segment) for segment in segments or ())
    store_asr_marks(dictation, audio, revision, run_id=run_id, model_ref=model_ref,
                    segments=measurements, duration_ms=snapshot.total_duration_ms)
