"""Lease-based claim and recovery for processing jobs (CLINIC_MODE only).

Upstream recovery resets every ``processing`` row to ``queued`` at startup, with
no way to tell a job a crashed process abandoned from one a live sibling is
transcribing right now. On staging that restarted one recording in three
processes within seconds.

Here a claim carries the claiming process (``claimed_by``) and a heartbeat
(``heartbeat_at``). A job is orphaned only when its heartbeat is older than the
lease. Recovery is a conditional UPDATE that matches the row exactly as it was
read, so of any number of concurrent sweeps exactly one wins a given job. The
sweep runs only in the elected queue-owner process.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# One id per interpreter: pid alone repeats across container recreates.
PROCESS_ID = f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20
_RECOVERY_ID = 'recovery'


def claim_values(now: datetime) -> dict:
    """Columns a claim sets in addition to the upstream ones."""
    return {'claimed_by': PROCESS_ID, 'heartbeat_at': now}


def _same(column, value):
    return column.is_(None) if value is None else column == value


def _takeover(db, job, **values) -> bool:
    """Change ``job`` only if it is still exactly as read; True for the one winner."""
    from sqlalchemy import update
    from src.models import ProcessingJob as PJ

    result = db.session.execute(
        update(PJ).where(
            PJ.id == job.id,
            PJ.status == 'processing',
            _same(PJ.claimed_by, job.claimed_by),
            _same(PJ.heartbeat_at, job.heartbeat_at),
        ).values(**values))
    db.session.commit()
    return result.rowcount == 1


def beat(queue, now: datetime | None = None) -> int:
    """Renew the lease of the jobs this process is running."""
    from src.database import db
    from src.models import ProcessingJob as PJ

    with queue._app_context():
        result = db.session.execute(
            PJ.__table__.update().where(
                PJ.status == 'processing', PJ.claimed_by == PROCESS_ID,
            ).values(heartbeat_at=now or datetime.utcnow()))
        db.session.commit()
        return result.rowcount


def recover_stale_jobs(queue, now: datetime | None = None,
                       lease: timedelta = timedelta(seconds=LEASE_SECONDS)) -> int:
    """Requeue jobs whose lease ran out. No-op outside the queue-owner process."""
    if not queue._is_owner:
        logger.debug('Not the job queue owner; orphan recovery skipped in this process')
        return 0

    from sqlalchemy import or_
    from src.clinic.notify_enqueue import recover_pending_notices
    from src.database import db
    from src.models import ProcessingJob as PJ

    cutoff = (now or datetime.utcnow()) - lease
    recovered = 0
    with queue._app_context():
        stale = PJ.query.filter(
            PJ.status == 'processing',
            or_(PJ.heartbeat_at.is_(None), PJ.heartbeat_at < cutoff),
        ).all()
        for job in stale:
            job_id, recording_id = job.id, job.recording_id
            if job.cancel_requested_at is not None:
                # Take it exclusively (the row stays processing), then finish the cancel.
                if _takeover(db, job, claimed_by=_RECOVERY_ID, heartbeat_at=datetime.utcnow()):
                    queue._finish_canceled(job_id, recording_id)
                continue
            queue_name = _queue_name(job.job_type)
            if _takeover(db, job, status='queued', started_at=None,
                         claimed_by=None, heartbeat_at=None):
                recovered += 1
                logger.info(f'Recovered orphaned {queue_name} job {job_id} for recording {recording_id}')
        if recovered:
            logger.info(f'Recovered {recovered} orphaned jobs')
    recover_pending_notices(queue)
    return recovered


def _queue_name(job_type: str) -> str:
    from src.services.job_queue import queue_name_for
    return queue_name_for(job_type)


def start_lease_keeper(queue) -> None:
    """Renew this process's leases and sweep for expired ones, until the queue stops."""
    if getattr(queue, '_lease_thread', None) is not None:
        return

    def loop():
        while queue._running:
            try:
                beat(queue)
                recover_stale_jobs(queue)
            except Exception as exc:
                logger.error(f'Job lease keeper failed: {exc}', exc_info=True)
            threading.Event().wait(HEARTBEAT_SECONDS)

    queue._lease_thread = threading.Thread(target=loop, name='JobLeaseKeeper', daemon=True)
    queue._lease_thread.start()
