"""
Fair database-backed job queue for background processing tasks.

This queue ensures:
- Jobs persist across application restarts
- Fair round-robin scheduling between users
- Separate queues for transcription (slow) and summary (fast) jobs
- Limited concurrency to prevent overwhelming external services
- Automatic recovery of orphaned jobs
"""

import os
import json
import threading
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)

from src.clinic.asr_capabilities import CAPABILITY_UNAVAILABLE
from src.clinic.config import clinic_mode_enabled, kis_delivery_enabled
from src.services.error_classification import is_permanent_error, SummaryStepFailed
from src.utils.error_text import describe_error, log_failure, user_error_text

# Configuration
TRANSCRIPTION_WORKERS = int(os.environ.get('JOB_QUEUE_WORKERS', '2'))
SUMMARY_WORKERS = int(os.environ.get('SUMMARY_QUEUE_WORKERS', '2'))
INDEX_WORKERS = int(os.environ.get('INDEX_QUEUE_WORKERS', '1'))
CONVERT_WORKERS = int(os.environ.get('CONVERT_QUEUE_WORKERS', '2'))
MAX_RETRIES = int(os.environ.get('JOB_MAX_RETRIES', '3'))
POLL_INTERVAL = 1.0  # seconds between checking for new jobs

# Job type categories.
#
# 'stitch' (server-side recording-session assembly, #287 c/d) lives in the
# transcription category on purpose: it is the local ffmpeg concat step that
# gates the recording's transcription, and putting it here means the existing
# transcription workers claim it, get_queue_status counts it, and
# get_position_in_queue places it — no separate worker pool or queue needed.
# Every consumer routes via "SUMMARY_JOBS if ... else TRANSCRIPTION_JOBS", so
# membership in this list is the single source of truth for the whole pipeline.
TRANSCRIPTION_JOBS = ['transcribe', 'reprocess_transcription', 'stitch', 'merge']
SUMMARY_JOBS = ['summarize', 'reprocess_summary', 'translate_transcription']
# Clinic follow-ups (src/clinic): only enqueued in clinic mode, so upstream
# behaviour is unchanged. They run on the fast summary workers.
CLINIC_JOBS = ['clinic_finalize', 'clinic_output', 'clinic_notify', 'clinic_alignment']
ASR_TRAINING_JOB = 'asr_training'
DICTATION_SUMMARY_JOB = "clinic_dictation_summary"
SUMMARY_JOBS += [*CLINIC_JOBS, ASR_TRAINING_JOB, DICTATION_SUMMARY_JOB]
# Building a recording's Inquire chunks (embeddings) has its own workers, so a
# slow embedding model never holds up a transcription or a summary (#541).
INDEX_JOBS = ['index_chunks']
# Converting an upload after the upload is answered (#543). FFmpeg uses
# several cores, so this queue has its own, small pool of workers.
CONVERT_JOBS = ['convert']

QUEUES = {'transcription': TRANSCRIPTION_JOBS, 'summary': SUMMARY_JOBS, 'index': INDEX_JOBS,
          'convert': CONVERT_JOBS}

# Dispatch table: job type -> name of the FairJobQueue method that runs it.
# `_process_job` looks the runner up here instead of walking an if/elif
# chain, so adding a job type means adding one row to this table (plus the
# QUEUES/*_JOBS lists and worker routing, which are unrelated concerns).
JOB_RUNNERS = {
    'transcribe': '_run_audio_job',
    'reprocess_transcription': '_run_audio_job',
    'summarize': '_run_summarization',
    DICTATION_SUMMARY_JOB: '_run_summarization',
    'reprocess_summary': '_run_reprocess_summary',
    'stitch': '_run_stitch',
    'merge': '_run_merge',
    'translate_transcription': '_run_translation',
    'index_chunks': '_run_index_chunks',
    'convert': '_run_convert',
    'clinic_finalize': '_run_clinic_job',
    'clinic_output': '_run_clinic_job',
    'clinic_notify': '_run_clinic_job',
    'clinic_alignment': '_run_clinic_job',
    ASR_TRAINING_JOB: '_run_asr_training',
}

# Jobs that run beside a recording's processing: they leave recording.status
# alone and send no webhooks or emails.
BACKGROUND_JOBS = ('translate_transcription', 'index_chunks', *CLINIC_JOBS, ASR_TRAINING_JOB, DICTATION_SUMMARY_JOB)

# Stable operation keys of clinic follow-ups: one job per key and job type,
# also across concurrent retries and after the first job has ended.
CLINIC_OPERATION_PARAMS = ('clinic_release_id', 'clinic_operation_key')
# Written to a clinic_output job before its handler runs; see _run_clinic_job.
CLINIC_DELIVERY_STARTED = 'delivery_started'

_CLINIC_HANDLERS = {}


class ClinicJobError(Exception):
    """A clinic follow-up failed. The message carries the correlation id."""

    permanent = False


class ClinicJobRefused(ClinicJobError):
    """Retrying cannot help (stale state, no handler, refused input)."""

    permanent = True


class ClinicDeliveryUnknown(ClinicJobRefused):
    """An output may or may not have been delivered: never resend automatically.

    The ledger (src/clinic/output_attempts.py) holds the UNKNOWN claim; only
    trusted lookup evidence resolves it, not a queue retry.
    """


def register_clinic_handler(job_type, handler):
    """Register ``handler(params, correlation_id)`` for a clinic job type."""
    if job_type not in CLINIC_JOBS:
        raise ValueError(f"Not a clinic job type: {job_type}")
    _CLINIC_HANDLERS[job_type] = handler


def clinic_notify_handler(can_see):
    """Handler for ``clinic_notify`` jobs: emit the in-app notice after commit.

    ``can_see`` is the caller's real visibility check (user id, workflow row).
    ``emit`` stores one row per recipient and skips stored ones, so a retry
    only delivers the recipients that failed.
    """
    def handle(params, correlation_id):
        from src.clinic import workflow, workflow_notifications

        # Durable per-transition id: the notice dedupe key is built from it, so
        # it must be the same on every retry of this job and differ between
        # two transitions at the same version.
        transition_id = params.get('transition_id') or params.get('clinic_operation_key')
        if not transition_id:
            raise ClinicJobRefused(f"[{correlation_id}] notify job has no transition_id")
        try:
            acceptor_ids = params.get('acceptor_ids') or ()
            if params['event'] == 'acceptance_requested' and _notify_transition_id(params):
                from src.clinic.notify_enqueue import acceptors
                acceptor_ids = set(acceptor_ids).intersection(acceptors(params['dictation_id']))
            result = workflow_notifications.emit(
                params['event'], params['dictation_id'],
                transition_id=transition_id,
                actor_user_id=params['actor_user_id'],
                expected_version=params['expected_version'],
                acceptor_ids=acceptor_ids,
                can_see=can_see, context=params.get('context'))
        except workflow.WorkflowError as error:
            raise ClinicJobRefused(f"[{correlation_id}] {error}") from error
        if result.failed:
            raise ClinicJobError(f"[{correlation_id}] notice not stored for {len(result.failed)} recipient(s)")
    return handle


def clinic_operation_key(params):
    """The stable operation key of a clinic follow-up's params, or None."""
    if isinstance(params, dict):
        for name in CLINIC_OPERATION_PARAMS:
            if params.get(name):
                return name, params[name]
    return None


def _notify_transition_id(params):
    """New workflow notices use their durable transition ID as the operation key."""
    transition_id = params.get('transition_id')
    if transition_id and clinic_operation_key(params) == ('clinic_operation_key', transition_id):
        return transition_id
    return None


class StitchTargetMismatch(Exception):
    """A stitch job's session belongs to another recording (#596).

    The job fails without a retry and without touching the recording row:
    marking that row FAILED could hit another user's recording (#505).
    """


def job_progress_writer(job_id, kind='chunks'):
    """A callback that stores ``(done, total)`` on a running job.

    The writes go through the job's control (src/services/job_control.py):
    on a connection of their own, committed at once, and at most one per
    PROGRESS_WRITE_INTERVAL, so the queue poll sees the progress while the
    job's own session is still open and a fast loop costs no extra commits.
    A failed write is logged and ignored: progress is display only and must
    never fail the job. ``kind`` is the unit: 'chunks' or 'fraction'.
    """
    from src.services import job_control

    control = job_control.current()
    if control.job_id != job_id:
        control = job_control.JobControl(job_id)

    def write(done, total):
        control.report(done, total, kind)

    return write


def is_background(job_type: str) -> bool:
    """Whether ``job_type`` runs beside a recording's processing.

    Background jobs (translation, Inquire chunks) leave the recording's
    status untouched and send no webhooks or emails. Centralising this
    predicate keeps the "is this a background job" decision in one place
    instead of scattered ``in``/``not in`` membership checks.
    """
    return job_type in BACKGROUND_JOBS


def queue_name_for(job_type: str) -> str:
    """Name of the queue whose workers run this job type."""
    for name, job_types in QUEUES.items():
        if job_type in job_types:
            return name
    return 'transcription'


def jobs_for_queue(queue_name: str) -> list:
    """A copy of the job types that run on the named queue.

    Raises KeyError for an unknown queue name.
    """
    return list(QUEUES[queue_name])


class FairJobQueue:
    """
    A database-backed job queue with fair scheduling across users.

    Uses separate queues for transcription and summary jobs to prevent
    slow transcription jobs from blocking fast summary jobs.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern to ensure only one queue exists."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the job queue."""
        if self._initialized:
            return

        self._transcription_workers = []
        self._summary_workers = []
        self._index_workers = []
        self._convert_workers = []
        self._running = False
        self._app = None
        # Lock for claiming jobs (SQLite doesn't support row-level locking)
        self._claim_lock = threading.Lock()
        # Serialise the check-then-insert in enqueue(). The active-job unique
        # index is the cross-process guarantee; this lock keeps same-process
        # callers from racing into it in the first place.
        self._enqueue_lock = threading.Lock()
        # Whether this process won the queue-owner election (see startup.initialize_job_queue)
        self._is_owner = False
        self._lease_thread = None
        self._initialized = True

        logger.info(f"FairJobQueue initialized: {TRANSCRIPTION_WORKERS} transcription workers, {SUMMARY_WORKERS} summary workers")

    def init_app(self, app):
        """Initialize with Flask app for context management."""
        self._app = app

    @contextmanager
    def _app_context(self):
        """Get application context for database operations."""
        if self._app:
            with self._app.app_context():
                yield
        else:
            yield

    def mark_as_owner(self):
        """Mark this process as the elected owner of the queue."""
        self._is_owner = True

    def start(self):
        """Start the worker threads for both queues."""
        if self._running:
            return

        # Only the elected owner runs workers; this also covers the auto-start in enqueue()
        if not self._is_owner:
            logger.debug(
                "Not the job queue owner; no worker threads started in this process"
            )
            return

        self._running = True

        # Start transcription workers
        for i in range(TRANSCRIPTION_WORKERS):
            worker = threading.Thread(
                target=self._worker_loop,
                args=(TRANSCRIPTION_JOBS, 'transcription'),
                name=f"TranscriptionWorker-{i}",
                daemon=True
            )
            worker.start()
            self._transcription_workers.append(worker)

        # Start summary workers
        for i in range(SUMMARY_WORKERS):
            worker = threading.Thread(
                target=self._worker_loop,
                args=(SUMMARY_JOBS, 'summary'),
                name=f"SummaryWorker-{i}",
                daemon=True
            )
            worker.start()
            self._summary_workers.append(worker)

        for i in range(INDEX_WORKERS):
            worker = threading.Thread(
                target=self._worker_loop,
                args=(INDEX_JOBS, 'index'),
                name=f"IndexWorker-{i}",
                daemon=True
            )
            worker.start()
            self._index_workers.append(worker)

        for i in range(CONVERT_WORKERS):
            worker = threading.Thread(
                target=self._worker_loop,
                args=(CONVERT_JOBS, 'convert'),
                name=f"ConvertWorker-{i}",
                daemon=True
            )
            worker.start()
            self._convert_workers.append(worker)

        logger.info(f"Started {TRANSCRIPTION_WORKERS} transcription workers, {SUMMARY_WORKERS} summary workers, "
                    f"{INDEX_WORKERS} index workers and {CONVERT_WORKERS} convert workers")

        if clinic_mode_enabled():
            from src.clinic.job_lease import start_lease_keeper
            start_lease_keeper(self)

    def stop(self):
        """Stop the worker threads gracefully."""
        self._running = False
        self._lease_thread = None
        for worker in self._transcription_workers + self._summary_workers:
            worker.join(timeout=5)
        self._transcription_workers.clear()
        self._summary_workers.clear()
        logger.info("Job queue workers stopped")

    def _worker_loop(self, job_types: List[str], queue_name: str):
        """Main worker loop that processes jobs of specific types."""
        while self._running:
            try:
                job = self._claim_next_job(job_types, queue_name)
                if job:
                    self._process_job(job)
                else:
                    # No jobs available, sleep briefly
                    time.sleep(POLL_INTERVAL)
            except BaseException as e:
                # JobCanceled is a BaseException and is handled inside
                # _process_job; one that still arrives here must not end
                # the worker thread.
                if not isinstance(e, Exception) and type(e).__name__ != 'JobCanceled':
                    raise
                logger.error(f"{queue_name.capitalize()} worker error: {e}", exc_info=True)
                time.sleep(POLL_INTERVAL)

    def _claim_next_job(self, job_types: List[str], queue_name: str):
        """
        Claim the next job of specified types, with users taking turns.

        Args:
            job_types: List of job types this worker handles
            queue_name: Name of the queue ('transcription' or 'summary')

        Returns the claimed job or None if no jobs available.
        """
        # Use lock to prevent race conditions (SQLite doesn't support row-level locking)
        with self._claim_lock:
            with self._app_context():
                from src.database import db
                from src.models import ProcessingJob

                try:
                    # A clinic follow-up without a registered handler stays
                    # queued (not failed) until register_clinic_handler runs.
                    job_types = [t for t in job_types if t not in CLINIC_JOBS or t in _CLINIC_HANDLERS]
                    if ASR_TRAINING_JOB in job_types and not clinic_mode_enabled():
                        job_types.remove(ASR_TRAINING_JOB)
                    order = self.turn_order(job_types)
                    candidate_job = order[0] if order else None

                    if candidate_job:
                        # Atomically claim the job - only succeeds if status is still 'queued'
                        # This prevents race conditions when multiple workers try to claim the same job
                        from sqlalchemy import update
                        claim_time = datetime.utcnow()
                        conditions = [ProcessingJob.id == candidate_job.id,
                                      ProcessingJob.status == 'queued']  # Critical: only claim if still queued
                        # A retried job reports its progress from 0 again.
                        values = dict(status='processing', started_at=claim_time,
                                      progress_done=None, progress_total=None,
                                      progress_kind=None, stage=None, stage_detail=None)
                        if clinic_mode_enabled():
                            # Lease: no second process can claim a row that is held.
                            from src.clinic.job_lease import claim_values
                            conditions.append(ProcessingJob.claimed_by.is_(None))
                            values.update(claim_values(claim_time))
                        result = db.session.execute(
                            update(ProcessingJob).where(*conditions).values(**values)
                        )

                        if result.rowcount == 0:
                            # Job was already claimed by another worker - this is expected with multiple workers
                            logger.debug(f"[{queue_name.upper()}] Job {candidate_job.id} already claimed by another worker")
                            db.session.rollback()
                            return None

                        # Also update Recording.status to reflect active processing
                        from src.models import Recording
                        recording = db.session.get(Recording, candidate_job.recording_id)
                        # A conversion leaves the recording QUEUED: its
                        # transcription is queued next, and the status should
                        # not go from PROCESSING back to QUEUED (#543).
                        # Training intents also leave the recording alone.
                        if (recording and recording.status == 'QUEUED'
                                and candidate_job.job_type not in CONVERT_JOBS
                                and candidate_job.job_type not in (ASR_TRAINING_JOB, DICTATION_SUMMARY_JOB)):
                            recording.status = 'PROCESSING'

                        db.session.commit()

                        # Refresh the job object to get updated values
                        db.session.refresh(candidate_job)

                        wait_time = (claim_time - candidate_job.created_at).total_seconds()
                        logger.info(f"[{queue_name.upper()}] Claimed job {candidate_job.id} (type={candidate_job.job_type}) for user {candidate_job.user_id}, recording {candidate_job.recording_id} (waited {wait_time:.1f}s)")
                        return candidate_job

                    return None

                except Exception as e:
                    logger.error(f"Error claiming {queue_name} job: {e}", exc_info=True)
                    db.session.rollback()
                    return None

    def turn_order(self, job_types: List[str]):
        """The queued jobs of these types, in the order workers will claim them.

        Users take turns, one job each: the user whose last job of these types
        started longest ago goes next, and a user without one goes first. Ties
        keep the order of each user's oldest queued job, and each user's jobs
        run oldest first. The last start comes from the jobs table, so every
        process computes the same order and a restart keeps it.
        """
        from src.database import db
        from src.models import ProcessingJob

        queued = ProcessingJob.query.filter(
            ProcessingJob.status == 'queued',
            ProcessingJob.job_type.in_(job_types)
        ).order_by(ProcessingJob.created_at, ProcessingJob.id).all()
        if not queued:
            return []

        waiting = {}
        for job in queued:
            waiting.setdefault(job.user_id, []).append(job)

        last_started = dict(db.session.query(
            ProcessingJob.user_id, db.func.max(ProcessingJob.started_at)
        ).filter(
            ProcessingJob.user_id.in_(list(waiting)),
            ProcessingJob.job_type.in_(job_types),
            ProcessingJob.started_at.isnot(None)
        ).group_by(ProcessingJob.user_id).all())

        # (round, last start, place in line): a user moves to the next round
        # after each job handed out below.
        keys = {
            uid: (0, last_started.get(uid) or datetime.min, place)
            for place, uid in enumerate(waiting)
        }
        order = []
        for round_number in range(1, len(queued) + 1):
            uid = min((u for u in waiting if waiting[u]), key=keys.get)
            order.append(waiting[uid].pop(0))
            keys[uid] = (round_number, datetime.min, keys[uid][2])
        return order

    def is_background(self, job_type: str) -> bool:
        """Whether ``job_type`` is a background job (see module helper)."""
        return is_background(job_type)

    def jobs_for_queue(self, queue_name: str) -> list:
        """Job types that run on the named queue (see module helper)."""
        return jobs_for_queue(queue_name)

    def _is_permanent_error(self, error_str: str) -> bool:
        """Delegate to src.services.error_classification.is_permanent_error.

        Kept as a method for backwards compatibility with callers and tests
        that invoke job_queue._is_permanent_error directly.
        """
        return is_permanent_error(error_str)

    def _process_job(self, job):
        """Process a single job by dispatching to the appropriate task function.

        The job's control is bound to this thread while it runs, so the code
        of the job reports progress and sees a cancel without extra
        arguments (src/services/job_control.py).
        """
        from src.services import job_control

        from src.utils.log_filters import recording_scope

        control = job_control.JobControl(job.id)
        # Every file label of the job ends with the id of its recording (#620).
        with job_control.running(control), recording_scope(job.recording_id):
            self._run_claimed_job(job, control)

    def _run_claimed_job(self, job, control):
        from src.services import job_control

        job_id = job.id
        job_type = job.job_type
        recording_id = job.recording_id
        params_str = job.params
        is_new_upload = job.is_new_upload
        # Also identify the attempt if parameter parsing fails before dispatch.
        attempt = (job.status, job.retry_count, job.started_at, job.created_at)

        with self._app_context():
            from src.database import db
            from src.models import ProcessingJob, Recording
            from flask import current_app

            loaded_created_at = None
            try:
                # Parse job parameters
                params = json.loads(params_str) if params_str else {}

                # Re-fetch the job in this session context to ensure it's attached
                job = db.session.get(ProcessingJob, job_id)
                if not job:
                    logger.error(f"Job {job_id} not found when trying to process")
                    return
                # Freeze the running attempt before dispatch. Refresh/rollback
                # can later put a replacement into the same Python object.
                attempt = (job.status, job.retry_count, job.started_at, job.created_at)

                # Get recording
                recording = db.session.get(Recording, recording_id)
                if not recording:
                    raise ValueError(f"Recording {recording_id} not found")
                # Tells this recording from a later upload that received its
                # id after a delete (SQLite reuses the highest id, #597).
                loaded_created_at = recording.created_at

                # Webhook fan-out for "started" events (#275) is now
                # emitted INSIDE each job handler after the
                # file-existence and recording-validity checks, so
                # subscribers don't see misleading started→failed
                # sequences when the audio file is missing on disk.
                # (Previously fired here, immediately after claim.)

                # Dispatch based on job type (registry lookup).
                runner_name = JOB_RUNNERS.get(job_type)
                if runner_name is None:
                    raise ValueError(f"Unknown job type: {job_type}")
                # A cancel that arrived between the claim and this point.
                control.check_cancel()
                runner_outcome = getattr(self, runner_name)(job, recording, params, job_type=job_type)

                # Mark as completed - re-fetch to ensure we have latest state
                job = self._current_attempt(job_id, attempt)
                if job is None:
                    # Deleting a recording deletes its jobs: the id may now
                    # be a new upload's, so nothing is reported (#597).
                    logger.info(f"Job {job_id} is no longer current; no notices sent")
                    return
                # A transcription that ended FAILED without raising (no text,
                # budget used up) must not end as a successful job: the job
                # outcome mirrors the recording's failure.
                terminal_error = None
                if not is_background(job_type):
                    try:
                        terminal_error = self._terminal_failure_error(job_type, recording_id, runner_outcome)
                    except Exception as e:
                        logger.warning(f"Outcome check on job {job_id} failed: {e}")
                # Clinic mode only; upstream keeps the job 'completed'.
                from src.clinic.config import clinic_mode_enabled
                job_fails = bool(terminal_error) and clinic_mode_enabled()
                # A read/check alone races with recovery on another connection.
                # Hold the matching row through all finalization writes/commit.
                job = self._current_attempt(job_id, attempt, lock=True)
                if job is None:
                    return
                job.status = 'failed' if job_fails else 'completed'
                if job_fails:
                    job.error_message = terminal_error
                job.completed_at = datetime.utcnow()
                # A cancel that arrived after the last safe point came too
                # late: the job is complete. The state kept for a cancel is
                # a copy of the old transcript and is not kept any longer.
                job.cancel_requested_at = None
                job.restore_state = None
                # Normally cleared by the connector with the result; a task
                # still saved here belongs to an attempt that was abandoned.
                self._release_remote_task(job)
                db.session.commit()
                if job_fails:
                    logger.info(f"Job {job_id} ended failed: {terminal_error}")
                else:
                    logger.info(f"Job {job_id} completed successfully")

                # Webhook fan-out (#275). One emit per job-type completion;
                # subscribers map these to user-visible lifecycle events.
                # Best-effort: if the webhook subsystem throws, the job is
                # still marked completed. Background jobs (translation,
                # Inquire chunks) send no webhooks or emails.

                if terminal_error:
                    # The transcription ended FAILED without raising: it
                    # returned no text (#406) or the transcription budget
                    # was used up. The job ran to its end, so the notices
                    # are those of a failed transcription, sent once (#595
                    # left both silent).
                    try:
                        self._emit_failure_webhook(job_type, recording_id, terminal_error)
                    except Exception as e:
                        logger.warning(f"Webhook emit on job {job_id} terminal failure failed: {e}")
                    try:
                        self._emit_failure_email(job_type, recording_id, terminal_error)
                    except Exception as e:
                        logger.warning(f"Failure email for job {job_id} failed: {e}")
                elif not is_background(job_type):
                    try:
                        self._emit_completion_webhook(job_type, recording_id)
                    except Exception as e:
                        logger.warning(f"Webhook emit on job {job_id} completion failed: {e}")

                    try:
                        self._emit_completion_email(job_type, recording_id)
                    except Exception as e:
                        logger.warning(f"Completion email for job {job_id} failed: {e}")

            except job_control.JobCanceled:
                # The owner canceled the job and its code stopped at a safe
                # point. Not a failure: no retry, no webhook, no email.
                self._finish_canceled(job_id, recording_id, loaded_created_at)

            except Exception as e:
                error_str = str(e)
                # A failed flush (for example on a recording deleted while
                # the job ran) leaves the session unusable until it is rolled
                # back; without this every query below raised
                # PendingRollbackError (#432).
                try:
                    db.session.rollback()
                except Exception:
                    pass

                job = self._current_attempt(job_id, attempt, lock=True)
                if job is None:
                    return
                from src.tasks.processing import RecordingDeletedDuringJob
                current = db.session.get(Recording, recording_id)
                if (current is None or isinstance(e, RecordingDeletedDuringJob)
                        or (loaded_created_at is not None and current.created_at != loaded_created_at)):
                    # Deleted while the job ran, or the id now belongs to a
                    # new upload (#597): nothing to retry or report, and the
                    # row with this id is not this job's to write.
                    logger.info(f"Job {job_id}: recording {recording_id} was deleted while the job ran; job dropped")
                    job = db.session.get(ProcessingJob, job_id)
                    if job:
                        job.status = 'failed'
                        job.error_message = 'Recording deleted while the job ran'
                        job.completed_at = datetime.utcnow()
                        self._release_remote_task(job)
                        db.session.commit()
                    return

                if control.cancel_requested():
                    # The job failed while a cancel was waiting for its next
                    # safe point: the cancel decides, and no failure is reported.
                    self._finish_canceled(job_id, recording_id, loaded_created_at)
                    return

                if isinstance(e, StitchTargetMismatch):
                    # The same session and recording would mismatch again, so
                    # no retry; the recording row is not this job's (#596).
                    job = db.session.get(ProcessingJob, job_id)
                    if job:
                        job.status = 'failed'
                        job.error_message = error_str
                        job.completed_at = datetime.utcnow()
                        db.session.commit()
                    logger.error(f"Job {job_id} failed: {error_str}")
                    return

                # The error text of a provider can quote the transcript, the
                # prompt or the filename: metadata at ERROR, the text and the
                # traceback at DEBUG.
                log_failure(logger, logging.ERROR, f"Job {job_id} ({job_type}) for recording {recording_id} failed",
                            e, traceback=True)

                # Check if this is a permanent error that shouldn't be retried
                is_permanent_error = getattr(e, 'permanent', False) or self._is_permanent_error(error_str)

                # From here on error_str is what is stored, shown, and sent by
                # webhook and email: the provider's short message, without
                # what it repeats of the request (src/utils/error_text.py).
                try:
                    _params = params if isinstance(locals().get('params'), dict) else {}
                    # Keep the fixed training refusal code even if it occurs in the recording text.
                    if (job_type, error_str) != (ASR_TRAINING_JOB, CAPABILITY_UNAVAILABLE):
                        error_str = user_error_text(
                            e, current,
                            extra_sources=[v for v in (_params.get('hotwords'), _params.get('initial_prompt'),
                                                       _params.get('custom_prompt')) if isinstance(v, str)],
                            limit=1000)
                except Exception as scrub_error:
                    logger.warning(f"Job {job_id}: error text could not be shortened: {describe_error(scrub_error)}")
                    error_str = f"{type(e).__name__} (the error text is in the log at DEBUG)"

                # Re-fetch job to update it
                job = db.session.get(ProcessingJob, job_id)
                if job:
                    job.error_message = error_str
                    job.retry_count += 1

                    # Only retry if: not a permanent error AND under retry limit
                    if not is_permanent_error and job.retry_count < MAX_RETRIES:
                        # Re-queue for retry
                        job.status = 'queued'
                        job.started_at = None
                        logger.info(f"Job {job_id} re-queued for retry ({job.retry_count}/{MAX_RETRIES})")
                        if isinstance(e, SummaryStepFailed):
                            # The summary step set FAILED before the queue
                            # decided to retry; the summary is still pending.
                            recording = db.session.get(Recording, recording_id)
                            if recording:
                                recording.status = 'SUMMARIZING'
                    else:
                        job.status = 'failed'
                        job.completed_at = datetime.utcnow()
                        job.restore_state = None
                        # No attempt is left to poll a job still saved from
                        # this one, so it is removed from the ASR service.
                        self._release_remote_task(job)
                        recording = db.session.get(Recording, recording_id)

                        if is_permanent_error:
                            logger.info(f"Job {job_id} failed with permanent error (no retry): {describe_error(e)}")

                        # Always keep recordings with FAILED status so users can see the error
                        # and reprocess later (e.g., when ASR server recovers)
                        if recording and not is_background(job_type):
                            # Keep the recording with FAILED status so user can see the error and fix settings
                            recording.status = 'FAILED'
                            # Format the error for nice display. A failed summary
                            # job keeps the transcript and shows the error as the
                            # summary; it used to overwrite a good transcript (#412).
                            from src.utils.error_formatting import format_error_for_storage
                            if isinstance(e, SummaryStepFailed):
                                # The summary step stored its own error text
                                # and error_message (#583); keep them.
                                if e.stored_summary:
                                    recording.summary = e.stored_summary
                            elif job_type in ('summarize', 'reprocess_summary'):
                                recording.summary = format_error_for_storage(error_str)
                            else:
                                recording.transcription = format_error_for_storage(error_str)
                            if not isinstance(e, SummaryStepFailed):
                                # The v1 API reports error_message for FAILED
                                # recordings; this path never set it (#583).
                                try:
                                    from src.utils.error_formatting import sanitize_error_message
                                    recording.error_message = sanitize_error_message(error_str)
                                except Exception:
                                    recording.error_message = error_str[:1000]

                        if is_permanent_error:
                            logger.error(f"Job {job_id} failed permanently (non-retryable error)")
                        else:
                            logger.error(f"Job {job_id} failed permanently after {MAX_RETRIES} retries")

                        # Webhook fan-out for permanent failure (#275). The
                        # retryable-failure case does not emit because the
                        # subscriber will see the eventual success or
                        # permanent failure that follows the retry loop.
                        # Background jobs send no webhooks or emails.
                        if not is_background(job_type):
                            try:
                                self._emit_failure_webhook(job_type, recording_id, error_str)
                            except Exception as e:
                                logger.warning(f"Webhook emit on job {job_id} failure failed: {e}")

                        if not is_background(job_type):
                            try:
                                self._emit_failure_email(job_type, recording_id, error_str)
                            except Exception as e:
                                logger.warning(f"Failure email for job {job_id} failed: {e}")

                    db.session.commit()

    @staticmethod
    def _current_attempt(job_id, attempt, *, lock=False):
        """Read or lock only the caller's attempt; discard stale pending writes.

        The conditional no-op UPDATE serializes finalization with recovery on
        SQLite as well as databases with row locks. No lock spans the runner.
        Direct callers that run queued jobs retain their existing semantics.
        """
        from src.database import db
        from src.models import ProcessingJob

        status, retry_count, started_at, created_at = attempt
        query = ProcessingJob.query.filter_by(
            id=job_id, status=status, retry_count=retry_count,
            started_at=started_at, created_at=created_at)
        with db.session.no_autoflush:
            if lock:
                matched = query.update(
                    {ProcessingJob.status: ProcessingJob.status}, synchronize_session=False)
            else:
                matched = query.with_entities(ProcessingJob.id).first() is not None
        if not matched:
            db.session.rollback()
            logger.info(f"Job {job_id}: attempt superseded or removed; outcome ignored")
            return None
        if lock:
            # Preserve pending writes by this runner, but refresh fields another
            # connection may have changed (cancel marks or provider task state).
            db.session.flush()
            return db.session.get(ProcessingJob, job_id, populate_existing=True)
        return db.session.get(ProcessingJob, job_id)

    @staticmethod
    def _finish_canceled(job_id, recording_id, loaded_created_at=None):
        """Remove a canceled job and settle its recording (src/services/job_cancel.py)."""
        try:
            from src.services.job_cancel import finish_canceled_job
            finish_canceled_job(job_id, recording_id, loaded_created_at)
        except Exception as e:
            logger.error(f"Could not finish the cancel of job {job_id}: {describe_error(e)}", exc_info=True)
            try:
                from src.database import db
                db.session.rollback()
            except Exception:
                pass

    @staticmethod
    def _release_remote_task(job):
        """Best-effort removal of the ASR service job saved on a job that has ended."""
        if not job.provider_task:
            return
        try:
            from src.services.transcription.provider_tasks import release_remote_task
            release_remote_task(job)
        except Exception as e:
            logger.warning(f"Removing the ASR service job of job {job.id} failed: {e}")

    def _run_audio_job(self, job, recording, params, job_type='transcribe'):
        """Run a transcription (fresh or reprocess) task. Status updates handled by task function.

        Returns the error message of a failure without retry (an empty
        transcript, an exhausted transcription budget), else None.
        """
        from src.tasks.processing import transcribe_audio_task
        from flask import current_app
        from src.services.storage import get_storage_service

        storage = get_storage_service()
        audio_path = (recording.audio_path or '').strip()
        label = 'transcription' if job_type == 'transcribe' else 'reprocess transcription'
        if not audio_path:
            logger.error(f"Cannot run {label} job for recording {recording.id}: missing audio_path")
            raise ValueError(f"Missing audio_path for recording {recording.id}")
        with storage.materialize(audio_path) as materialized:
            filepath = materialized.local_path
            filename_for_asr = recording.original_filename or os.path.basename(filepath)

            # Emit recording.transcription.started AFTER the file is on disk
            # (materialized from storage) so subscribers don't see misleading
            # started→failed events for jobs that abort immediately. Best-effort;
            # emit errors don't block the actual work. Kept INSIDE the materialize
            # block so the temp file still exists when transcription runs.
            if filepath and os.path.exists(filepath):
                try:
                    self._emit_started_webhook(job_type, recording.id)
                except Exception as e:
                    logger.warning(f"Webhook emit ({job_type} started) failed: {e}")

            return transcribe_audio_task(
                current_app._get_current_object().app_context(),
                recording.id,
                filepath,
                filename_for_asr,
                datetime.utcnow(),
                language=params.get('language'),
                min_speakers=params.get('min_speakers'),
                max_speakers=params.get('max_speakers'),
                diarize=params.get('diarize'),
                tag_id=params.get('tag_id'),
                hotwords=params.get('hotwords'),
                initial_prompt=params.get('initial_prompt'),
                transcription_model=params.get('transcription_model'),
            )

    def _run_summarization(self, job, recording, params, job_type=None):
        """Run summarization-only task. Status updates handled by task function."""
        from src.tasks.processing import generate_summary_only_task
        from flask import current_app

        if job_type == DICTATION_SUMMARY_JOB:
            from src.clinic.dictation_summary import run_summary
            return run_summary(job, params, self._current_attempt)

        if params.get('follow_up'):
            # Title and summary queued by a transcription job (src/clinic/follow_up.py).
            from src.clinic.follow_up import run_follow_up
            return run_follow_up(current_app._get_current_object().app_context(), recording.id, params)

        generate_summary_only_task(
            current_app._get_current_object().app_context(),
            recording.id,
            custom_prompt_override=params.get('custom_prompt'),
            custom_prompt_append=params.get('custom_prompt_append', False),
            user_id=params.get('user_id')
        )

    def _run_reprocess_summary(self, job, recording, params, job_type=None):
        """Run summary reprocessing task. Status updates handled by task function."""
        from src.tasks.processing import generate_summary_only_task
        from flask import current_app

        generate_summary_only_task(
            current_app._get_current_object().app_context(),
            recording.id,
            custom_prompt_override=params.get('custom_prompt'),
            custom_prompt_append=params.get('custom_prompt_append', False),
            user_id=params.get('user_id')
        )

    def _run_stitch(self, job, recording, params, job_type=None):
        """Stitch in-progress recording-session chunks into a single file (#287 c/d).

        On success the recording row is updated in place (audio_path,
        file_size, status='PENDING') and a follow-up ``transcribe`` job is
        enqueued. On failure the recording is flipped to FAILED with the
        ffmpeg error message visible to the user.
        """
        from src.services.recording_stitch import (
            stitch_recording_session,
            kickoff_transcription_for_stitched,
            StitchError,
        )

        session_id = (params or {}).get('session_id')
        if not session_id:
            raise ValueError("stitch job missing required 'session_id' param")

        # The session must point at this recording, and both must belong to
        # the same user. Otherwise the job fails without touching the
        # recording: a plain error would mark another user's recording FAILED
        # (#505), and returning stored the job as completed (#596). A missing
        # session is left to stitch_recording_session, as before.
        from src.models import RecordingSession
        from src.database import db
        sess = db.session.get(RecordingSession, session_id)
        if sess is not None and (sess.finalized_recording_id != recording.id
                                 or sess.user_id != recording.user_id):
            logger.error(f"Stitch job {job.id}: session {session_id} does not belong to "
                         f"recording {recording.id}; job dropped")
            raise StitchTargetMismatch(
                f"Recording session {session_id} does not belong to recording {recording.id}")

        try:
            recording_id, final_path, metadata = stitch_recording_session(session_id)
        except StitchError as e:
            # Mark the recording FAILED so the user sees the error in the UI.
            # The session row was already flipped to 'failed' by the worker,
            # except in cases where stitch raised before touching it; do both
            # defensively.
            from src.models import RecordingSession
            recording.status = 'FAILED'
            recording.transcription = f"Processing failed: {e}"
            sess = db.session.get(RecordingSession, session_id)
            if sess and sess.status not in ('finalized', 'aborted', 'expired'):
                sess.status = 'failed'
                sess.error_message = str(e)
            db.session.commit()
            raise

        kickoff_transcription_for_stitched(
            recording_id=recording_id,
            user_id=recording.user_id,
            metadata=metadata,
        )

    def _run_merge(self, job, recording, params, job_type=None):
        """Concatenate source recordings' audio into this recording (#323).

        The heavy ffmpeg concat runs here, off the request path, so hours-long
        merges do not time out the HTTP request. On success the recording's
        audio_path is set and a follow-up ``transcribe`` job is enqueued; on
        failure the recording is flipped to FAILED with the error visible.
        """
        from src.services.recording_merge import run_merge_job, MergeError
        from src.database import db

        try:
            run_merge_job(recording, params)
        except MergeError as e:
            recording.status = 'FAILED'
            recording.error_message = str(e)
            recording.transcription = f"Processing failed: {e}"
            db.session.commit()
            raise

    def _run_translation(self, job, recording, params, job_type=None):
        """Translate a recording's transcription (summary worker pool)."""
        from flask import current_app
        from src.tasks.processing import translate_transcription_task

        params = params or {}
        translate_transcription_task(
            current_app._get_current_object().app_context(),
            recording.id,
            user_id=params.get('user_id', job.user_id),
            target_language=params.get('target_language'),
            params=params,
            progress=self._translation_progress(job.id),
        )

    @staticmethod
    def _translation_progress(job_id):
        """Chunks finished of chunks in total; between two chunks is a safe point for a cancel."""
        from src.services import job_control

        write = job_progress_writer(job_id)

        def progress(done, total):
            write(done, total)
            job_control.current().check_cancel()

        return progress

    def _run_asr_training(self, job, recording, params, job_type=None):
        """Refuse training intents until a verified local runner is wired."""
        raise ClinicJobRefused(CAPABILITY_UNAVAILABLE)

    def _run_clinic_job(self, job, recording, params, job_type=None):
        """Run a clinic follow-up through its registered handler (src/clinic).

        Failures carry a correlation id (operation key and job id). A
        clinic_output job is never retried or recovered into a second
        delivery: the marker is committed before the handler runs, so a
        retry, a crash recovery or a failure after the adapter call ends as
        UNKNOWN for the output ledger instead of a resend.
        """
        from src.database import db

        correlation_id = f"{(clinic_operation_key(params) or ('', 'none'))[1]}:{job.id}"
        if not clinic_mode_enabled():
            raise ClinicJobRefused(f"[{correlation_id}] clinic mode is off")
        handler = _CLINIC_HANDLERS.get(job_type)
        if handler is None:
            raise ClinicJobRefused(f"[{correlation_id}] no handler for {job_type}")
        output = job_type == 'clinic_output'
        if output:
            if params.get(CLINIC_DELIVERY_STARTED):
                raise ClinicDeliveryUnknown(f"[{correlation_id}] delivery state is UNKNOWN; reconcile, do not resend")
            if not kis_delivery_enabled():
                raise ClinicJobRefused(f"[{correlation_id}] KIS delivery is disabled; nothing was sent")
            job.params = json.dumps({**params, CLINIC_DELIVERY_STARTED: True})
            db.session.commit()
        try:
            outcome = handler(params, correlation_id)
        except ClinicJobError:
            raise
        except Exception as error:
            logger.error(f"Clinic job {correlation_id} ({job_type}) failed: {describe_error(error)}")
            cls = ClinicDeliveryUnknown if output else ClinicJobError
            raise cls(f"[{correlation_id}] {type(error).__name__}") from error
        if output and outcome == 'UNKNOWN':
            raise ClinicDeliveryUnknown(f"[{correlation_id}] adapter outcome UNKNOWN; reconcile, do not resend")

    def _run_convert(self, job, recording, params, job_type=None):
        """Convert an upload, then queue its transcription (convert worker pool)."""
        from src.services.upload_conversion import convert_uploaded_recording

        convert_uploaded_recording(recording.id, params or {})

    # Builds in a row for one job while the transcript keeps changing.
    MAX_INDEX_PASSES = 10

    def _run_index_chunks(self, job, recording, params, job_type=None):
        """Rebuild the recording's Inquire chunks (index worker pool).

        An edit queued while this job runs is merged into it (one active job
        per recording and type), so the chunks are built again when the
        transcript changed during a build.
        """
        from src.database import db
        from src.models import Recording
        from src.services.embeddings import process_recording_chunks

        recording_id = recording.id
        for _ in range(self.MAX_INDEX_PASSES):
            db.session.expire_all()
            recording = db.session.get(Recording, recording_id)
            if recording is None:
                return
            text = recording.transcription
            if not (text or '').strip():
                return
            # A failure keeps the old chunks; raising lets the job be retried.
            if not process_recording_chunks(recording_id):
                raise RuntimeError(f"Could not build the Inquire chunks for recording {recording_id}")
            db.session.expire_all()
            recording = db.session.get(Recording, recording_id)
            if recording is None or recording.transcription == text:
                return
            logger.info(f"Transcript of recording {recording_id} changed while its chunks were built; building again")
        logger.warning(f"Transcript of recording {recording_id} kept changing; its chunks may be behind the last edit")

    def enqueue(
        self,
        user_id: int,
        recording_id: int,
        job_type: str,
        params: Dict[str, Any] = None,
        is_new_upload: bool = False,
        restore_state: Dict[str, Any] = None
    ) -> int:
        """
        Add a job to the database queue.

        Args:
            user_id: ID of the user who owns this job
            recording_id: ID of the recording to process
            job_type: Type of job (transcribe, summarize, reprocess_transcription, reprocess_summary)
            params: Optional parameters for the job
            is_new_upload: True if this is a new file upload (for cleanup on failure)
            restore_state: What a reprocess cleared before it queued this job
                (job_cancel.snapshot_for_reprocess), put back by a cancel

        Returns:
            The created job ID
        """
        with self._app_context():
            from src.database import db
            from src.models import ProcessingJob, Recording
            from sqlalchemy.exc import IntegrityError

            # Take this session's connection before the lock (D564). A
            # thread that holds the lock must never wait for the pool: with
            # every connection checked out by threads queued on the lock,
            # that wait could only end in the 30 s pool timeout.
            db.session.connection()

            with self._enqueue_lock:
                operation = clinic_operation_key(params) if clinic_mode_enabled() else None
                transition_id = _notify_transition_id(params) if job_type == 'clinic_notify' and operation else None
                jobs = ProcessingJob.query.filter_by(recording_id=recording_id, job_type=job_type)
                if transition_id:
                    existing = jobs.filter_by(notify_transition_id=transition_id).first()
                    if existing:
                        return existing.id
                if operation:
                    # One job per operation key and type, even after the first
                    # one ended (a retry must not schedule the work again).
                    older_jobs = jobs.filter_by(notify_transition_id=None) if transition_id else jobs
                    for row in older_jobs.order_by(ProcessingJob.id):
                        try:
                            stored = json.loads(row.params) if row.params else {}
                        except ValueError:
                            continue
                        if clinic_operation_key(stored) == operation:
                            return row.id

                # Check for existing active job of the SAME TYPE for this recording
                # Allow different job types to coexist (e.g., transcribe and summarize)
                active = jobs.filter_by(notify_transition_id=None).filter(
                    ProcessingJob.status.in_(['queued', 'processing']))
                existing = None if transition_id else active.first()

                if existing and operation:
                    # An active job of another operation is not this one's job.
                    raise ClinicJobError(
                        f"Active {job_type} job {existing.id} belongs to another operation; retry later")
                if existing:
                    logger.warning(f"Job of type {job_type} already exists for recording {recording_id}: {existing.id}")
                    return existing.id

                if (job_type in ('transcribe', 'reprocess_transcription') and clinic_mode_enabled()
                        and (params or {}).get('diarize') is None):
                    from src.clinic import recording_type, web_ingest

                    dictation = web_ingest.dictation_for_recording(recording_id)
                    if dictation is not None and dictation.configured_recording_type is not None:
                        params = {**(params or {}), **recording_type.transcription_params(dictation.recording_type)}

                # Create new job
                job = ProcessingJob(
                    user_id=user_id,
                    recording_id=recording_id,
                    job_type=job_type,
                    notify_transition_id=transition_id,
                    params=json.dumps(params) if params else None,
                    is_new_upload=is_new_upload,
                    restore_state=json.dumps(restore_state) if restore_state else None
                )
                # Read the recording before adding the job: a lookup would otherwise
                # autoflush the insert outside the IntegrityError handler below.
                recording = db.session.get(Recording, recording_id)
                if recording:
                    if is_background(job_type):
                        # Leave recording status untouched.
                        pass
                    elif job_type in SUMMARY_JOBS:
                        recording.status = 'SUMMARIZING'
                    else:
                        recording.status = 'QUEUED'

                db.session.add(job)
                try:
                    db.session.commit()
                except IntegrityError:
                    # Another process inserted an active job between our check
                    # and this commit. The partial unique index on
                    # (recording_id, job_type) rejected ours: answer with the
                    # winner so callers still see one job id per recording and
                    # type.
                    db.session.rollback()
                    if transition_id:
                        winner = jobs.filter_by(notify_transition_id=transition_id).first()
                        if winner:
                            return winner.id
                        raise
                    existing = active.first()
                    if existing and operation:
                        try:
                            won = clinic_operation_key(json.loads(existing.params or '{}'))
                        except ValueError:
                            won = None
                        if won == operation:
                            return existing.id
                        raise ClinicJobError(
                            f"Active {job_type} job {existing.id} belongs to another operation; retry later")
                    if existing:
                        logger.info(
                            f"Job of type {job_type} was created concurrently for recording "
                            f"{recording_id}: {existing.id}"
                        )
                        return existing.id
                    raise

                # Auto-start workers if not running (no-op unless this process owns the queue)
                if not self._running:
                    self.start()

                queue_name = queue_name_for(job_type)
                logger.info(f"Enqueued {queue_name} job {job.id} (type={job_type}) for user {user_id}, recording {recording_id}")
                return job.id

    def recover_orphaned_jobs(self):
        """
        Recover jobs that were processing when the app crashed.
        Call this on startup to reset orphaned jobs back to queued.
        """
        if clinic_mode_enabled():
            from src.clinic.job_lease import recover_stale_jobs
            recover_stale_jobs(self)
            return

        with self._app_context():
            from src.database import db
            from src.models import ProcessingJob

            # Canceled, and the worker stopped with the process before its
            # next safe point: the cancel is finished here, not run again.
            canceled = [(job.id, job.recording_id) for job in ProcessingJob.query.filter(
                ProcessingJob.status == 'processing',
                ProcessingJob.cancel_requested_at.isnot(None)
            ).all()]
            for job_id, recording_id in canceled:
                self._finish_canceled(job_id, recording_id)

            orphaned = ProcessingJob.query.filter(
                ProcessingJob.status == 'processing'
            ).all()

            for job in orphaned:
                job.status = 'queued'
                job.started_at = None
                queue_name = queue_name_for(job.job_type)
                logger.info(f"Recovered orphaned {queue_name} job {job.id} for recording {job.recording_id}")

            if orphaned:
                db.session.commit()
                logger.info(f"Recovered {len(orphaned)} orphaned jobs")

    def get_queue_status(self) -> Dict[str, Any]:
        """Get the current status of each queue."""
        with self._app_context():
            from src.models import ProcessingJob

            transcription_queued = ProcessingJob.query.filter(
                ProcessingJob.status == 'queued',
                ProcessingJob.job_type.in_(TRANSCRIPTION_JOBS)
            ).count()
            transcription_processing = ProcessingJob.query.filter(
                ProcessingJob.status == 'processing',
                ProcessingJob.job_type.in_(TRANSCRIPTION_JOBS)
            ).count()

            summary_queued = ProcessingJob.query.filter(
                ProcessingJob.status == 'queued',
                ProcessingJob.job_type.in_(SUMMARY_JOBS)
            ).count()
            summary_processing = ProcessingJob.query.filter(
                ProcessingJob.status == 'processing',
                ProcessingJob.job_type.in_(SUMMARY_JOBS)
            ).count()

            return {
                "transcription_queue": {
                    "queued": transcription_queued,
                    "processing": transcription_processing,
                    "workers": TRANSCRIPTION_WORKERS
                },
                "summary_queue": {
                    "queued": summary_queued,
                    "processing": summary_processing,
                    "workers": SUMMARY_WORKERS
                },
                "convert_queue": {
                    "queued": ProcessingJob.query.filter(
                        ProcessingJob.status == 'queued',
                        ProcessingJob.job_type.in_(CONVERT_JOBS)
                    ).count(),
                    "processing": ProcessingJob.query.filter(
                        ProcessingJob.status == 'processing',
                        ProcessingJob.job_type.in_(CONVERT_JOBS)
                    ).count(),
                    "workers": CONVERT_WORKERS
                },
                "index_queue": {
                    "queued": ProcessingJob.query.filter(
                        ProcessingJob.status == 'queued',
                        ProcessingJob.job_type.in_(jobs_for_queue('index'))
                    ).count(),
                    "processing": ProcessingJob.query.filter(
                        ProcessingJob.status == 'processing',
                        ProcessingJob.job_type.in_(jobs_for_queue('index'))
                    ).count(),
                    "workers": INDEX_WORKERS
                },
                "is_running": self._running
            }

    def get_position_in_queue(self, recording_id: int) -> Optional[int]:
        """Place of a recording's queued job among the queued jobs of its queue (1 = next).

        Background jobs (translation, Inquire chunks) are not counted as the
        recording's job.
        """
        with self._app_context():
            from src.models import ProcessingJob

            job = ProcessingJob.query.filter(
                ProcessingJob.recording_id == recording_id,
                ProcessingJob.status == 'queued',
                ProcessingJob.job_type.notin_(BACKGROUND_JOBS)
            ).first()

            if not job:
                return None

            order = self.turn_order(jobs_for_queue(queue_name_for(job.job_type)))
            return next(i for i, queued in enumerate(order, 1) if queued.id == job.id)

    # Names of the queues as the API reports a recording's stage.
    STAGES = {'convert': 'conversion', 'transcription': 'transcription', 'summary': 'summary'}

    def get_stage(self, recording_id: int) -> Optional[str]:
        """The stage of a recording's waiting or running job: 'conversion',
        'transcription' or 'summary'; None when it has none.

        Background jobs (translation, Inquire chunks) are not the recording's
        stage.
        """
        job = self.get_active_job(recording_id)
        return self.STAGES.get(queue_name_for(job.job_type)) if job else None

    def get_active_job(self, recording_id: int):
        """The recording's waiting or running job, the newest when it has
        several; None when it has none. Background jobs (translation,
        Inquire chunks) are not counted."""
        with self._app_context():
            from src.models import ProcessingJob

            return ProcessingJob.query.filter(
                ProcessingJob.recording_id == recording_id,
                ProcessingJob.status.in_(['queued', 'processing']),
                ProcessingJob.job_type.notin_(BACKGROUND_JOBS)
            ).order_by(ProcessingJob.id.desc()).first()

    def get_job_for_recording(self, recording_id: int):
        """Get the active job for a recording."""
        with self._app_context():
            from src.models import ProcessingJob

            return ProcessingJob.query.filter(
                ProcessingJob.recording_id == recording_id,
                ProcessingJob.status.in_(['queued', 'processing'])
            ).first()

    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove completed/failed jobs older than max_age_hours."""
        with self._app_context():
            from src.database import db
            from src.models import ProcessingJob
            from datetime import timedelta

            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

            # An ASR service job that could not be removed when its
            # processing job ended gets a last attempt before the row goes.
            for job in ProcessingJob.query.filter(
                    ProcessingJob.status.in_(['completed', 'failed']),
                    ProcessingJob.completed_at < cutoff,
                    ProcessingJob.provider_task.isnot(None)).all():
                self._release_remote_task(job)

            deleted = ProcessingJob.query.filter(
                ProcessingJob.status.in_(['completed', 'failed']),
                ProcessingJob.completed_at < cutoff
            ).delete(synchronize_session=False)

            if deleted:
                db.session.commit()
                logger.info(f"Cleaned up {deleted} old jobs")


    # --- Webhook emission helpers (#275) ---------------------------------

    # recording.summary.completed is emitted by the summary step itself
    # (processing.generate_summary_only_task, #412): a summary job can end with
    # a FAILED summary, and the first summary of an upload runs inside the
    # transcribe job, so the job type alone cannot say whether a summary exists.
    _COMPLETION_EVENT_MAP = {
        'transcribe': 'recording.transcription.completed',
        'reprocess_transcription': 'recording.transcription.completed',
    }

    _FAILURE_EVENT_MAP = {
        'transcribe': 'recording.transcription.failed',
        'summarize': 'recording.summary.failed',
        'reprocess_transcription': 'recording.transcription.failed',
        'reprocess_summary': 'recording.summary.failed',
        # A permanent stitch failure means the recording will never reach
        # transcription, so subscribers should hear about it under the
        # transcription.failed event (the follow-up transcribe job that would
        # normally emit this is never enqueued on a stitch failure, so there
        # is no double-emit). Stitch deliberately has NO started/completed
        # mapping: those fire from the real transcribe job after a successful
        # stitch.
        'stitch': 'recording.transcription.failed',
        # Likewise for an upload whose conversion failed (#543), and for a
        # merge or multi-file join whose audio was never assembled (#592).
        'convert': 'recording.transcription.failed',
        'merge': 'recording.transcription.failed',
    }

    # No `summary.started` in the vocabulary today; if one is added later,
    # extend this map and the dispatcher will pick it up automatically.
    _STARTED_EVENT_MAP = {
        'transcribe': 'recording.transcription.started',
        'reprocess_transcription': 'recording.transcription.started',
    }

    def _emit_started_webhook(self, job_type: str, recording_id: int):
        event_type = self._STARTED_EVENT_MAP.get(job_type)
        if not event_type:
            return
        with self._app_context():
            from src.database import db
            from src.models import Recording
            from src.services.webhook_dispatch import emit_webhook_event
            recording = db.session.get(Recording, recording_id)
            if not recording:
                return
            emit_webhook_event(
                user_id=recording.user_id,
                event_type=event_type,
                data={
                    'recording_id': recording.id,
                    'title': recording.title,
                },
            )

    def _terminal_failure_error(self, job_type: str, recording_id: int, runner_outcome=None):
        """The error of a transcription that ended FAILED without raising, else None.

        The transcription step marks a recording FAILED and returns the
        message, without raising, when a retry cannot change the outcome: the
        service returned no text (#406, EMPTY_TRANSCRIPT_MESSAGE) or the
        transcription budget was used up (the translated budget message,
        returned as runner_outcome). The job completes. A recording that is
        FAILED for another reason (its automatic summary failed, #595) keeps
        its own notices.
        """
        if job_type not in self._COMPLETION_EVENT_MAP:
            return None
        with self._app_context():
            from src.database import db
            from src.models import Recording
            from src.tasks.processing import EMPTY_TRANSCRIPT_MESSAGE
            recording = db.session.get(Recording, recording_id)
            if recording is None or recording.status != 'FAILED' or not recording.error_message:
                return None
            terminal = {EMPTY_TRANSCRIPT_MESSAGE}
            if isinstance(runner_outcome, str):
                terminal.add(runner_outcome)
            if recording.error_message in terminal:
                return recording.error_message
            return None

    def _emit_completion_webhook(self, job_type: str, recording_id: int):
        event_type = self._COMPLETION_EVENT_MAP.get(job_type)
        if not event_type:
            return
        with self._app_context():
            from src.database import db
            from src.models import Recording
            from src.services.webhook_dispatch import emit_webhook_event
            recording = db.session.get(Recording, recording_id)
            if not recording:
                return
            if getattr(recording, 'status', None) == 'FAILED':
                # The transcription job returned, but the recording ended
                # FAILED: its auto-summary failed (recording.summary.failed
                # was already sent, #595) or the transcript came back empty
                # (recording.transcription.failed is sent instead, #406).
                return
            # Both of these were documented from #275 and neither was ever
            # sent. audio_duration_seconds read `recording.audio_duration`,
            # which is not a column, and `language` read a per-recording
            # language that did not exist either. getattr's default made both
            # None and the None-filter below then dropped the keys.
            #
            # The language is now recorded at transcription time from what the
            # service reports, so it describes this audio rather than the
            # user's current preference. It stays absent for recordings
            # transcribed before that column existed, and for backends that
            # report no language.
            data = {
                'recording_id': recording.id,
                'title': recording.title,
                'language': recording.transcription_language,
                'audio_duration_seconds': recording.audio_duration_seconds,
                'transcription_duration_seconds': recording.transcription_duration_seconds,
                'summarization_duration_seconds': recording.summarization_duration_seconds,
            }
            emit_webhook_event(
                user_id=recording.user_id,
                event_type=event_type,
                data={k: v for k, v in data.items() if v is not None},
            )

    # Only transcription reaches the inbox. A summary finishing is not an
    # event a user waits on the way they wait on a transcript, and mailing
    # both would double the volume for no extra signal.
    _EMAIL_JOB_TYPES = ('transcribe', 'reprocess_transcription')

    def _load_recording_and_owner(self, recording_id: int):
        from src.database import db
        from src.models import Recording, User
        recording = db.session.get(Recording, recording_id)
        if not recording:
            return None, None
        return recording, db.session.get(User, recording.user_id)

    def _emit_completion_email(self, job_type: str, recording_id: int):
        if job_type not in self._EMAIL_JOB_TYPES:
            return
        with self._app_context():
            from src.services.email import send_transcription_complete_email
            recording, owner = self._load_recording_and_owner(recording_id)
            if not recording or not owner:
                return
            if getattr(recording, 'status', None) == 'FAILED':
                # "Transcription ready" would contradict the library (#595).
                return
            if send_transcription_complete_email(owner, recording):
                logger.info(f"Sent completion email for recording {recording_id} to user {owner.id}")

    def _emit_failure_email(self, job_type: str, recording_id: int, error: str):
        # A failed conversion (#543) or merge (#592) means the transcript
        # will never come.
        if job_type not in self._EMAIL_JOB_TYPES + ('convert', 'merge'):
            return
        with self._app_context():
            from src.services.email import send_transcription_failed_email
            recording, owner = self._load_recording_and_owner(recording_id)
            if not recording or not owner:
                return
            if send_transcription_failed_email(owner, recording, error):
                logger.info(f"Sent failure email for recording {recording_id} to user {owner.id}")

    def _emit_failure_webhook(self, job_type: str, recording_id: int, error: str):
        event_type = self._FAILURE_EVENT_MAP.get(job_type)
        if not event_type:
            return
        with self._app_context():
            from src.database import db
            from src.models import Recording
            from src.services.webhook_dispatch import emit_webhook_event
            recording = db.session.get(Recording, recording_id)
            if not recording:
                return
            emit_webhook_event(
                user_id=recording.user_id,
                event_type=event_type,
                data={
                    'recording_id': recording.id,
                    'title': recording.title,
                    'error': (error or '')[:500],
                },
            )


# Global job queue instance
job_queue = FairJobQueue()
