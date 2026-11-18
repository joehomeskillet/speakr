"""One queue owner per host, re-election, and no transaction across ASR (CLINIC_MODE).

Staging ran the queue threads in every gunicorn worker and kept sessions
"idle in transaction" for the length of a chunked ASR call.
"""
import fcntl
import hashlib
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.app import app, db
from src.clinic import job_lease, queue_owner
from src.config import startup
from src.models import ProcessingJob, Recording
from src.services.job_queue import job_queue
import src.tasks.processing as proc
from src.utils import database


@pytest.fixture
def fresh_queue(monkeypatch):
    """The singleton queue with no workers, not owner, not running; restored after."""
    monkeypatch.setattr(job_queue, '_is_owner', False)
    monkeypatch.setattr(job_queue, '_running', False)
    for name in ('_transcription_workers', '_summary_workers', '_index_workers', '_convert_workers'):
        monkeypatch.setattr(job_queue, name, [])
    monkeypatch.setattr(job_queue, '_lease_thread', None)
    # Workers would poll the shared test DB; the thread objects are what is counted.
    monkeypatch.setattr(job_queue, '_worker_loop', lambda *args: None)
    monkeypatch.setattr(job_lease, 'start_lease_keeper', lambda queue: None)
    job_queue.init_app(app)
    return job_queue


def _worker_count(queue):
    return len(queue._transcription_workers) + len(queue._summary_workers)


def test_a_non_owner_process_starts_no_queue_threads(clinic_on, fresh_queue, monkeypatch):
    monkeypatch.setattr('src.services.job_queue.TRANSCRIPTION_WORKERS', 2)
    monkeypatch.setattr('src.services.job_queue.SUMMARY_WORKERS', 2)
    fresh_queue.start()
    assert _worker_count(fresh_queue) == 0
    assert fresh_queue._running is False


def test_the_owner_starts_the_queue_threads(clinic_on, fresh_queue, monkeypatch):
    monkeypatch.setattr('src.services.job_queue.TRANSCRIPTION_WORKERS', 2)
    monkeypatch.setattr('src.services.job_queue.SUMMARY_WORKERS', 2)
    fresh_queue.mark_as_owner()
    fresh_queue.start()
    assert _worker_count(fresh_queue) > 0


# --- re-election ------------------------------------------------------------

@pytest.fixture
def election(monkeypatch, tmp_path, fresh_queue):
    """A private lock directory; `hold()` takes the owner lock like a sibling process."""
    monkeypatch.setattr(database, '_held_singleton_locks', {})
    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(job_queue, 'start', lambda: None)  # no threads; the election is under test
    with app.app_context():
        url = db.engine.url
    digest = hashlib.sha256(f'{url}|{startup.JOB_QUEUE_OWNER_LOCK}'.encode()).hexdigest()[:16]
    path = tmp_path / f'speakr_singleton_{digest}.lock'
    handles = []

    def hold():
        handle = open(path, 'a+')
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handles.append(handle)
        return handle

    yield hold
    for handle in handles:
        handle.close()


def test_a_lost_election_is_retried_and_won_after_the_owner_dies(clinic_on, election):
    dead_owner = election()
    assert queue_owner.reelect_once(app) is False
    assert not job_queue._is_owner

    dead_owner.close()  # the owner process died: its lock is gone
    assert queue_owner.reelect_once(app) is True
    assert job_queue._is_owner


def test_the_watch_thread_takes_over_and_stops(clinic_on, election):
    dead_owner = election()
    watch = queue_owner.start_reelection_watch(app, interval=0.01)
    time.sleep(0.1)
    assert watch.is_alive() and not job_queue._is_owner

    dead_owner.close()
    watch.join(5)
    assert not watch.is_alive()
    assert job_queue._is_owner


def test_the_new_owner_requeues_the_dead_owners_job(clinic_on, election, world):
    with app.app_context():
        db.session.get(ProcessingJob, world['job_b']).status = 'failed'
        job = db.session.get(ProcessingJob, world['job_a'])
        job.status = 'processing'
        job.claimed_by = 'old-host:1:dead'
        job.heartbeat_at = datetime.utcnow() - timedelta(seconds=job_lease.LEASE_SECONDS + 60)
        db.session.commit()

    dead_owner = election()
    assert queue_owner.reelect_once(app) is False
    dead_owner.close()
    assert queue_owner.reelect_once(app) is True  # runs the lease recovery on takeover

    with app.app_context():
        job = db.session.get(ProcessingJob, world['job_a'])
        assert (job.status, job.claimed_by) == ('queued', None)


def test_the_loser_of_the_startup_election_watches_only_in_clinic_mode(monkeypatch, fresh_queue):
    started = []
    monkeypatch.setattr(queue_owner, 'start_reelection_watch', lambda a: started.append(a))
    with patch.object(startup, 'start_single_instance', return_value=False):
        for mode, expected in (('1', [app]), ('', [])):
            started.clear()
            monkeypatch.setenv('CLINIC_MODE', mode)
            startup.run_startup_tasks(app)
            assert started == expected


# --- no transaction across the ASR call -------------------------------------

class _SlowAsr:
    PROVIDER_NAME = 'fake'
    supports_diarization = False
    specifications = MagicMock()

    def __init__(self):
        self.in_transaction = []

    def transcribe(self, request):
        self.in_transaction.append(db.session().in_transaction())
        time.sleep(0.01)
        from src.services.transcription.base import TranscriptionResponse
        return TranscriptionResponse(text='part', provider='fake', model='fake')


def _chunked_run(tmp_path, recording_id):
    audio = tmp_path / 'long.mp3'
    audio.write_bytes(b'long audio')
    chunks = []
    for i in range(2):
        chunk = tmp_path / f'chunk_{i}.mp3'
        chunk.write_bytes(b'c%d' % i)
        chunks.append({'index': i, 'path': str(chunk), 'filename': chunk.name, 'size_mb': 0.1,
                       'start_time': i * 10.0, 'end_time': (i + 1) * 10.0, 'duration': 10.0})
    connector = _SlowAsr()
    with app.app_context():
        recording = db.session.get(Recording, recording_id)
        title_before = recording.title  # opens the transaction a job holds
        assert db.session().in_transaction()
        with patch.object(proc, 'chunking_service') as chunking:
            chunking.create_chunks.return_value = chunks
            chunking.merge_transcriptions.side_effect = lambda r: ' '.join(x['transcription'] for x in r)
            proc.transcribe_chunks_with_connector(connector, str(audio), 'long.mp3', 'audio/mpeg', 'en')
        # The row is reloaded on the next access; nothing is detached.
        assert recording.title == title_before
        db.session.rollback()
    return connector.in_transaction


def test_no_transaction_is_open_during_a_chunked_asr_call(clinic_on, world, tmp_path):
    assert _chunked_run(tmp_path, world['rec_a']) == [False, False]


def test_upstream_keeps_its_transaction_with_clinic_mode_off(clinic_off, world, tmp_path):
    assert _chunked_run(tmp_path, world['rec_a']) == [True, True]


def test_the_chunked_call_site_ends_the_transaction_first(clinic_on):
    with patch.object(proc, '_end_transaction_before_external_call') as end:
        proc._end_transaction_in_clinic_mode('x')
    end.assert_called_once_with('x')
