"""Project the recognition outcome onto the dictation's preparation step."""

from sqlalchemy import case, event, func
from sqlalchemy.orm import load_only, object_session

from src.clinic.config import clinic_mode_enabled
from src.models import ProcessingJob, Recording

_ASR_JOBS = ('transcribe', 'reprocess_transcription', 'convert', 'stitch', 'merge')

# Narrow ASR filler: whitespace, sentence separators and quotation marks only.
# Keep medical/math signs (including +, ?, §, -, /) and all letters/numbers.
# Explicit whitespace keeps SQLite/Python and PostgreSQL independent of locale.
_ASR_CONTENT_PATTERN = '[^' + (
    '\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680'
    '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
    '\u2028\u2029\u202f\u205f\u3000'
    '.,!;:…。｡、､，！：；،؛۔\'"“”„«»‹›‘’‚'
) + ']'


def asr_outcomes(dictations):
    """Detailed outcomes in batches; no per-dictation original or job scans."""
    from collections import defaultdict
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES
    from src.clinic.models import ClinicOriginal, ClinicTranscriptRevision
    from src.clinic.revisions import KIND_ASR

    items = list(dictations)
    outcomes = {}
    for start in range(0, len(items), 500):
        part = items[start:start + 500]
        session = object_session(part[0])
        ids = [d.id for d in part]
        links = {d.id: {d.recording_id} for d in part}
        for did, ref in session.query(ClinicOriginal.dictation_id, ClinicOriginal.origin_ref).filter(
                ClinicOriginal.dictation_id.in_(ids)):
            if ref and ref.startswith(ORIGIN_REF_PREFIXES):
                fields = ref.split(':', 3)
                if len(fields) == 4 and fields[2].isascii() and fields[2].isdigit():
                    number = int(fields[2])
                    if 0 < number <= 2**63 - 1:
                        links[did].add(number)
        recording_ids = set().union(*links.values())
        recordings = {r.id: r for r in session.query(Recording).options(
            load_only(Recording.id, Recording.user_id, Recording.status)
        ).filter(Recording.id.in_(recording_ids))}
        states = defaultdict(set)
        for rid, state in session.query(ProcessingJob.recording_id, ProcessingJob.status).filter(
                ProcessingJob.recording_id.in_(recording_ids), ProcessingJob.job_type.in_(_ASR_JOBS)):
            states[rid].add(state)
        # A late ASR tip also counts when a human revision is the visible head.
        # Classify both incoming and historical immutable receipts in the DB.
        # Return one flag per dictation, never transcript bodies or per-row binds.
        # A prior meaningful ASR still counts after a later filler-only result.
        done = dict(session.query(ClinicTranscriptRevision.dictation_id, func.max(case(
            (ClinicTranscriptRevision.body.regexp_match(_ASR_CONTENT_PATTERN), 1), else_=0
        ))).filter(ClinicTranscriptRevision.dictation_id.in_(ids),
                   ClinicTranscriptRevision.kind == KIND_ASR
        ).group_by(ClinicTranscriptRevision.dictation_id).all())
        for d in part:
            anchor = recordings.get(d.recording_id)
            linked = [recordings[rid] for rid in links[d.id] if rid in recordings and anchor is not None
                      and recordings[rid].user_id == anchor.user_id]
            job_states = set().union(*(states[r.id] for r in linked))
            if done.get(d.id):
                outcome = 'completed'
            elif 'processing' in job_states:
                outcome = 'processing'
            elif 'queued' in job_states:
                outcome = 'queued'
            elif 'failed' in job_states or any(r.status == 'FAILED' for r in linked):
                outcome = 'failed'
            elif d.id in done:
                outcome = 'skipped'
            else:
                outcome = 'none'
            outcomes[d.id] = outcome
    return outcomes


def asr_outcome(dictation, *, outcomes=None, detailed=False):
    """Preserve workflow semantics; list consumers may request detailed states."""
    state = (asr_outcomes([dictation]) if outcomes is None else outcomes)[dictation.id]
    if detailed:
        return state
    return {'completed': 'done', 'queued': 'running', 'processing': 'running',
            'skipped': 'none'}.get(state, state)


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
    if status not in ('completed', 'failed') or job.job_type not in _ASR_JOBS or not clinic_mode_enabled():
        return
    if status == 'completed' and job.job_type not in ('transcribe', 'reprocess_transcription'):
        return
    session = object_session(job)
    if session is None:
        return

    @event.listens_for(session, 'before_commit', once=True)
    def settle_terminal(session):
        from src.clinic.web_ingest import dictation_for_recording

        if job not in session or job in session.deleted or job.status != status:
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
