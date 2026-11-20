"""Project the recognition outcome onto the dictation's preparation step."""

from sqlalchemy import event
from sqlalchemy.orm import object_session

from src.clinic.config import clinic_mode_enabled
from src.models import ProcessingJob, Recording

_ASR_JOBS = ('transcribe', 'reprocess_transcription', 'convert', 'stitch', 'merge')


def _recording_ids(session, dictation):
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES
    from src.clinic.models import ClinicOriginal

    ids = {dictation.recording_id}
    refs = session.query(ClinicOriginal.origin_ref).filter_by(dictation_id=dictation.id)
    for (ref,) in refs:
        if ref.startswith(ORIGIN_REF_PREFIXES):
            parts = ref.split(':', 3)
            if len(parts) == 4 and parts[2].isascii() and parts[2].isdigit():
                number = int(parts[2])
                if 0 < number <= 2**63 - 1:
                    ids.add(number)
    return ids


def asr_outcome(dictation):
    from src.clinic.models import ClinicTranscriptRevision
    from src.clinic.revisions import KIND_ASR

    session = object_session(dictation)
    # A late ASR tip also counts when a human revision is the visible head.
    if session.query(ClinicTranscriptRevision.id).filter_by(
        dictation_id=dictation.id, kind=KIND_ASR
    ).first():
        return 'done'
    anchor = session.get(Recording, dictation.recording_id)
    recordings = session.query(Recording.id).filter(
        Recording.id.in_(_recording_ids(session, dictation)), Recording.user_id == anchor.user_id
    )
    jobs = session.query(ProcessingJob.status).filter(
        ProcessingJob.recording_id.in_(recordings),
        ProcessingJob.job_type.in_(_ASR_JOBS),
    )
    states = {status for (status,) in jobs}
    if states.intersection(('queued', 'processing')):
        return 'running'
    if 'failed' in states or recordings.filter(Recording.status == 'FAILED').first():
        return 'failed'
    return 'none'


def settle(session, dictation_id):
    """Advance a prepared dictation in the caller's transaction."""
    from src.clinic import workflow
    from src.clinic.models import ClinicDictation

    row = session.get(workflow.ClinicWorkflowDictation, dictation_id,
                      populate_existing=True, with_for_update=True)
    if row is None or row.status not in (workflow.PREP_ONLINE, workflow.PREP_OFFLINE):
        return row
    outcome = asr_outcome(session.get(ClinicDictation, dictation_id))
    if outcome == 'running':
        return row
    trigger = 'recognition_done' if outcome == 'done' else 'skip_recognition'
    return workflow.transition(session, dictation_id, trigger)


@event.listens_for(ProcessingJob.status, 'set')
def _on_job_status(job, status, old_status, initiator):
    """Settle in the queue's transaction, once it has decided against retry."""
    if status != 'failed' or job.job_type not in _ASR_JOBS or not clinic_mode_enabled():
        return
    session = object_session(job)
    if session is None:
        return

    @event.listens_for(session, 'before_commit', once=True)
    def settle_failure(session):
        from src.clinic.web_ingest import dictation_for_recording

        if job not in session or job in session.deleted or job.status != 'failed':
            return
        session.flush()
        recording = session.get(Recording, job.recording_id)
        if recording is None or recording.user_id != job.user_id or recording.created_at > job.created_at:
            return
        dictation = dictation_for_recording(job.recording_id, session=session)
        if dictation is not None:
            settle(session, dictation.id)


def mirror_recording(dictation, recording):
    """Keep the list's separate anchor current without replacing an edited title."""
    if dictation.recording_id != recording.id:
        anchor = object_session(dictation).get(Recording, dictation.recording_id,
                                               populate_existing=True, with_for_update=True)
        anchor.status = recording.status
        anchor.audio_duration_seconds = recording.audio_duration_seconds
        anchor.meeting_date = recording.meeting_date
        if not anchor.title:
            anchor.title = recording.title
