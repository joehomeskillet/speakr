"""Job claim and orphan recovery across processes (CLINIC_MODE).

Staging showed one job recovered and claimed by three app processes within
20 seconds. The recovery sweep must run only in the queue owner, treat a job as
orphaned only when its lease ran out, and let exactly one sweep win a job.
"""
import threading
from datetime import datetime, timedelta

import pytest

from src.app import app, db
from src.clinic import job_lease
from src.models import ProcessingJob
from src.services.job_queue import TRANSCRIPTION_JOBS, job_queue


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(job_queue, '_is_owner', True)
    job_queue.init_app(app)
    return job_queue


@pytest.fixture
def one_job(world):
    """job_a only: the other queued job of the world fixture is parked."""
    with app.app_context():
        db.session.get(ProcessingJob, world['job_b']).status = 'failed'
        db.session.commit()
    return world['job_a']


def _set(job_id, **values):
    with app.app_context():
        job = db.session.get(ProcessingJob, job_id)
        for key, value in values.items():
            setattr(job, key, value)
        db.session.commit()


def _row(job_id):
    with app.app_context():
        job = db.session.get(ProcessingJob, job_id)
        return job.status, job.claimed_by, job.heartbeat_at


def _stale(job_id, by='other-host:1:dead'):
    _set(job_id, status='processing', claimed_by=by,
         heartbeat_at=datetime.utcnow() - timedelta(seconds=job_lease.LEASE_SECONDS + 60))


def test_claim_is_exclusive_across_processes(clinic_on, owner, one_job, monkeypatch):
    first = owner._claim_next_job(TRANSCRIPTION_JOBS, 'transcription')
    assert first is not None and first.id == one_job
    assert _row(one_job)[1] == job_lease.PROCESS_ID

    monkeypatch.setattr(job_lease, 'PROCESS_ID', 'other-host:2:beef')
    assert owner._claim_next_job(TRANSCRIPTION_JOBS, 'transcription') is None
    assert _row(one_job)[1] != 'other-host:2:beef'


def test_claim_refuses_a_row_that_is_still_held(clinic_on, owner, one_job):
    _set(one_job, claimed_by='someone:1:aaaa')
    assert owner._claim_next_job(TRANSCRIPTION_JOBS, 'transcription') is None
    assert _row(one_job)[0] == 'queued'


def test_two_concurrent_sweeps_recover_a_stale_job_once(clinic_on, owner, one_job):
    _stale(one_job)
    barrier = threading.Barrier(2)
    counts = []

    def sweep():
        barrier.wait()
        counts.append(job_lease.recover_stale_jobs(owner))

    threads = [threading.Thread(target=sweep) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(counts) == [0, 1]
    assert _row(one_job)[:2] == ('queued', None)


def test_a_live_lease_is_not_orphaned(clinic_on, owner, one_job):
    _set(one_job, status='processing', claimed_by='sibling:3:cafe', heartbeat_at=datetime.utcnow())
    owner.recover_orphaned_jobs()
    assert _row(one_job)[0] == 'processing'


def test_a_non_owner_sweep_does_nothing(clinic_on, monkeypatch, one_job):
    monkeypatch.setattr(job_queue, '_is_owner', False)
    job_queue.init_app(app)
    _stale(one_job)
    job_queue.recover_orphaned_jobs()
    assert _row(one_job)[0] == 'processing'


def test_a_stale_lease_is_recovered_once_by_the_startup_sweep(clinic_on, owner, one_job):
    _stale(one_job)
    owner.recover_orphaned_jobs()
    assert _row(one_job)[:2] == ('queued', None)
    assert job_lease.recover_stale_jobs(owner) == 0


def test_heartbeat_renews_only_this_process_leases(clinic_on, owner, one_job):
    old = datetime.utcnow() - timedelta(seconds=300)
    _set(one_job, status='processing', claimed_by=job_lease.PROCESS_ID, heartbeat_at=old)
    assert job_lease.beat(owner) >= 1
    assert _row(one_job)[2] > old
    _set(one_job, claimed_by='sibling:3:cafe', heartbeat_at=old)
    job_lease.beat(owner)
    assert _row(one_job)[2] == old
