"""Bind explicit ASR repeats to their source and consume each result once.

Bindings and result receipts live on the existing ProcessingJob. No source
job or transcript revision is changed. Helpers use the caller's transaction.
"""

import json

from src.clinic.audio_revisions import current_audio_revision
from src.clinic.revisions import current_revision
from src.clinic.web_ingest import dictation_for_recording
from src.database import db
from src.models import ProcessingJob, Recording

REPEAT_PARAM = "clinic_asr_repeat"
ASR_JOBS = ("transcribe", "reprocess_transcription")
TRANSCRIPT_FIELDS = (
    "transcription", "transcription_language", "speaker_embeddings", "speaker_embeddings_space_id",
)


def bind_repeat(recording, params):
    """Called only by the explicit reprocess ingress, after its eligibility checks."""
    dictation = dictation_for_recording(recording.id)
    if dictation is None:
        return False
    db.session.refresh(dictation, with_for_update=True)
    head = current_revision(dictation)
    audio = current_audio_revision(dictation)
    source = ProcessingJob.query.filter(
        ProcessingJob.recording_id == recording.id, ProcessingJob.job_type.in_(ASR_JOBS),
    ).order_by(ProcessingJob.id.desc()).first()
    params[REPEAT_PARAM] = {
        "dictation_id": dictation.id,
        "recording_created_at": recording.created_at.isoformat(),
        "transcript_revision_id": head.id if head else None,
        "audio_revision_id": audio.id if audio else None,
        "source_job_id": source.id if source else None,
        "source_attempt": source.retry_count if source else None,
    }
    return True


def lock_projection(recording):
    """Read the latest projection after ASR returns, before writing any result.

    The dictation lock serializes this result with human corrections and other
    ASR results. It is never held across the external recognition call.
    """
    with db.session.no_autoflush:
        dictation = dictation_for_recording(recording.id)
        if dictation is not None:
            db.session.refresh(dictation, with_for_update=True)
        db.session.refresh(recording, attribute_names=list(TRANSCRIPT_FIELDS), with_for_update=True)
    return {field: getattr(recording, field) for field in TRANSCRIPT_FIELDS}


def attempt_identity(job_id):
    """Snapshot the claimed attempt before the connector releases the transaction."""
    if job_id is None:
        return None
    row = db.session.query(ProcessingJob.retry_count, ProcessingJob.started_at).filter_by(id=job_id).first()
    return tuple(row) if row else None


def repeat_status(job_id, recording, dictation, head, attempt):
    """Return (status, job, params): unbound, duplicate, stale, or eligible."""
    job = db.session.get(ProcessingJob, job_id, populate_existing=True) if job_id else None
    params = json.loads(job.params or "{}") if job else {}
    binding = params.get(REPEAT_PARAM)
    if not isinstance(binding, dict):
        return "unbound", job, params
    if binding.get("receipt") is not None:
        return "duplicate", job, params
    if attempt != (job.retry_count, job.started_at):
        # An old worker must not consume the replacement worker's receipt.
        return "stale_attempt", job, params
    audio = current_audio_revision(dictation)
    source_id = binding.get("source_job_id")
    source = db.session.get(ProcessingJob, source_id) if source_id else None
    newer = ProcessingJob.query.filter(
        ProcessingJob.recording_id == recording.id, ProcessingJob.job_type.in_(ASR_JOBS),
        ProcessingJob.id > job.id,
    ).first()
    valid = (
        job.job_type == "reprocess_transcription" and job.status == "processing"
        and job.recording_id == recording.id and job.cancel_requested_at is None
        and binding.get("dictation_id") == dictation.id
        and binding.get("recording_created_at") == recording.created_at.isoformat()
        and binding.get("audio_revision_id") == (audio.id if audio else None)
        and (source_id is None or source is not None
             and source.recording_id == recording.id
             and source.retry_count == binding.get("source_attempt"))
        and newer is None
        and binding.get("transcript_revision_id") == (head.id if head else None)
    )
    return ("eligible" if valid else "stale"), job, params


def receipt(job, params, outcome, revision):
    """Consume the attempt atomically with the revision and projection."""
    binding = params[REPEAT_PARAM]
    if "receipt" not in binding:
        binding["receipt"] = {
            "job_id": job.id, "attempt": job.retry_count, "outcome": outcome,
            "transcript_revision_id": revision.id if revision else None,
        }
        job.params = json.dumps(params, sort_keys=True, ensure_ascii=False)


def mirror_projection(dictation, recording):
    """The upload source and the worklist anchor expose the same current text."""
    if dictation.recording_id != recording.id:
        anchor = db.session.get(Recording, dictation.recording_id,
                                populate_existing=True, with_for_update=True)
        for field in TRANSCRIPT_FIELDS:
            setattr(anchor, field, getattr(recording, field))
