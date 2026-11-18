"""Queue-owner re-election (CLINIC_MODE only).

Upstream elects the job-queue owner once at process start, with a file lock that
lives as long as the holder. A process that lost the election never asks again,
so when the owner dies the queue stays dead until a restart. Here every
non-owner web worker re-runs the election at a fixed interval; the lock is gone
with the dead holder, one worker wins, starts the queue threads, and the lease
sweep (job_lease) requeues the jobs the dead owner left in ``processing``.

Takeover time is at most REELECT_SECONDS plus the job lease.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

REELECT_SECONDS = 15
_WATCH_NAME = 'JobQueueOwnerWatch'


def reelect_once(app) -> bool:
    """Run the election again; start the queue here on a win. True once owner."""
    from src.config import startup
    from src.services.job_queue import job_queue
    from src.utils import database

    if job_queue._is_owner:
        return True
    # A lost election is cached for the life of the process; forget it to ask again.
    cached = database._held_singleton_locks.get(startup.JOB_QUEUE_OWNER_LOCK)
    if cached is not None and not cached[0]:
        database._held_singleton_locks.pop(startup.JOB_QUEUE_OWNER_LOCK, None)
    # The election reads db.engine, which needs an app context in this thread.
    with app.app_context():
        startup.start_single_instance(
            app, startup.JOB_QUEUE_OWNER_LOCK, lambda: startup.start_job_queue(app), 'job queue')
    return job_queue._is_owner


def start_reelection_watch(app, interval: float = REELECT_SECONDS):
    """In a non-owner process: retry the election until this process wins."""
    def loop():
        stop = threading.Event()
        while True:
            stop.wait(interval)
            try:
                if reelect_once(app):
                    logger.info('This process took over the job queue')
                    return
            except Exception as exc:
                logger.error(f'Job queue re-election failed: {exc}', exc_info=True)

    thread = threading.Thread(target=loop, name=_WATCH_NAME, daemon=True)
    thread.start()
    return thread
