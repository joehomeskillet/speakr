"""Title and summary as a follow-up job on the summary queue (CLINIC_MODE only).

A transcription job ends with its transcript. The title and the summary, which
need the LLM, run as a ``summarize`` job on the summary workers, so a hung or
failing LLM can never occupy a transcription worker. For linked clinic dictations
this job only titles the source; their summary is bound to the first ASR revision.
For unlinked recordings, if the summary step fails
the recording keeps its transcript and shows the summary as failed; the job
queue retries it and a summary reprocess recovers it (SummaryStepFailed).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PARAM = 'follow_up'


def enqueue_follow_up(recording, will_auto_summarize: bool, *, dictation=None) -> int:
    from src.services.job_queue import job_queue

    job_id = job_queue.enqueue(
        user_id=recording.user_id,
        recording_id=recording.id,
        job_type='summarize',
        params={PARAM: True, 'summarize': bool(will_auto_summarize and dictation is None)},
    )
    logger.info(f'Title/summary of recording {recording.id} queued as job {job_id}')
    return job_id


def run_follow_up(app_context, recording_id: int, params: dict) -> None:
    """The title, then the summary when it is wanted. Raises SummaryStepFailed on a failed summary."""
    from src.clinic.config import clinic_mode_enabled
    from src.tasks.processing import generate_summary_only_task, generate_title_task

    summarize = bool(params.get('summarize', True))
    if summarize and clinic_mode_enabled():
        # A pre-upgrade follow-up may still request a stock summary on the ASR source.
        from src.clinic.web_ingest import dictation_for_recording
        with app_context:
            summarize = dictation_for_recording(recording_id) is None
    generate_title_task(app_context, recording_id, will_auto_summarize=summarize)
    if summarize:
        generate_summary_only_task(app_context, recording_id)
