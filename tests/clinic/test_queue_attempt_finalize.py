"""Queue ownership survives a lease takeover while the old worker is running."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest

from src.app import app
from src.clinic import revisions
from src.clinic.asr_repeat import REPEAT_PARAM
from src.clinic.job_lease import recover_stale_jobs
from src.database import db
from src.models import ProcessingJob
from src.services.job_queue import ClinicJobRefused, StitchTargetMismatch, job_queue
from src.services.transcription.base import TranscriptionResponse
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
from src.tasks.processing import RecordingDeletedDuringJob
from test_wp_rest27_asr_successor import prepared, _repeat  # noqa: F401
from test_wp_v8_asr_link_and_receipt import upload  # noqa: F401
from test_wp_v8_web_ingest import author, originals  # noqa: F401


@pytest.fixture(autouse=True)
def quiet_queue(monkeypatch):
    job_queue.stop()
    job_queue.init_app(app)
    monkeypatch.setattr(job_queue, '_is_owner', False)
    yield
    job_queue.stop()


def _claim(job_id):
    claimed = job_queue._claim_next_job(['reprocess_transcription'], 'transcription')
    assert claimed is not None and claimed.id == job_id
    return claimed


@pytest.mark.parametrize('finish_first', ['old', 'replacement'])
def test_two_real_asr_workers_finish_without_resetting_processing(prepared, monkeypatch, finish_first):
    dictation, source, _ = prepared
    first = revisions.current_revision(dictation)
    job = _repeat(source)
    job_id = job.id
    job_queue.stop()
    monkeypatch.setattr(job_queue, '_is_owner', True)
    entered = {name: Event() for name in ('old', 'replacement')}
    release = {name: Event() for name in entered}
    calls = []

    def transcribe(_self, request):
        assert request.audio_file.read()
        name = 'old' if not calls else 'replacement'
        calls.append(name)
        db.session.commit()  # No transaction spans the synthetic ASR wait.
        entered[name].set()
        assert release[name].wait(15), f'{name} ASR was not released'
        return TranscriptionResponse(text=f'{name} Entwurf.', language='de')

    monkeypatch.setattr(ClinicStubTranscriptionConnector, 'transcribe', transcribe)
    claimed = _claim(job_id)
    old_started = claimed.started_at
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(job_queue._process_job, claimed)
        try:
            assert entered['old'].wait(15)
            # Production lease recovery and claiming create the replacement.
            # Never reset status to processing in the test.
            assert recover_stale_jobs(job_queue, now=datetime.utcnow() + timedelta(minutes=5)) >= 1
            replacement_claim = _claim(job_id)
            assert replacement_claim.started_at != old_started
            replacement_started = replacement_claim.started_at
            replacement = pool.submit(job_queue._process_job, replacement_claim)
            assert entered['replacement'].wait(15)
            if finish_first == 'old':
                release['old'].set()
                old.result(timeout=15)
                db.session.expire_all()
                current = db.session.get(ProcessingJob, job_id)
                assert current.status == 'processing'
                assert current.started_at == replacement_started
                assert current.completed_at is None
                assert 'receipt' not in json.loads(current.params)[REPEAT_PARAM]
                assert revisions.current_revision(dictation).id == first.id
            release['replacement'].set()
            replacement.result(timeout=15)
            db.session.expire_all()
            current = db.session.get(ProcessingJob, job_id)
            completed_at, params = current.completed_at, current.params
            release['old'].set()
            old.result(timeout=15)
            db.session.expire_all()
            assert current.status == 'completed'
            assert (current.completed_at, current.params) == (completed_at, params)
            assert current.started_at == replacement_started
            assert current.retry_count == 0  # Lease recovery need not change retry_count.
            receipt = json.loads(current.params)[REPEAT_PARAM]['receipt']
            assert receipt['outcome'] == 'applied' and receipt['attempt'] == 0
            assert revisions.current_revision(dictation).body == 'replacement Entwurf.'
            assert len(revisions.history(dictation)) == 2
            assert calls == ['old', 'replacement']
        finally:
            for signal in release.values():
                signal.set()


@pytest.mark.parametrize('changed', ['retry_count', 'started_at', 'status'])
@pytest.mark.parametrize('outcome', ['success', 'terminal', 'retry', 'permanent', 'deleted', 'mismatch'])
def test_stale_queue_outcomes_leave_replacement_untouched(prepared, monkeypatch, changed, outcome):
    _, source, _ = prepared
    job = _repeat(source)
    claimed = _claim(job.id)
    notices = [Mock() for _ in range(5)]
    for name, notice in zip(('_emit_completion_webhook', '_emit_completion_email',
                             '_emit_failure_webhook', '_emit_failure_email', '_release_remote_task'), notices):
        monkeypatch.setattr(job_queue, name, notice)
    expected = {}

    def replace():
        # A different connection commits while the worker still holds an ORM row.
        table = ProcessingJob.__table__
        values = {'retry_count': claimed.retry_count + 1} if changed == 'retry_count' else (
            {'started_at': claimed.started_at + timedelta(seconds=1)} if changed == 'started_at' else
            {'status': 'queued'})
        values.update(error_message='replacement marker', provider_task='replacement task',
                      restore_state='replacement restore', cancel_requested_at=None)
        with db.engine.begin() as conn:
            conn.execute(table.update().where(table.c.id == claimed.id).values(**values))
            expected.update(conn.execute(table.select().where(table.c.id == claimed.id)).mappings().one())

    def runner(*args, **kwargs):
        db.session.commit()
        if outcome != 'terminal':
            replace()
        errors = {'retry': RuntimeError, 'permanent': ClinicJobRefused,
                  'deleted': RecordingDeletedDuringJob, 'mismatch': StitchTargetMismatch}
        if outcome in errors:
            raise errors[outcome]('old worker failed')

    def terminal(*args):
        # Race after the runner returned and the queue read the row for completion.
        replace()
        return 'old worker terminal failure'

    monkeypatch.setattr(job_queue, '_run_audio_job', runner)
    if outcome == 'terminal':
        monkeypatch.setattr(job_queue, '_terminal_failure_error', terminal)
    db.session.refresh(source)  # The real claim updated it in another app context.
    before_recording = (source.status, source.transcription, source.error_message)
    job_queue._process_job(claimed)
    db.session.expire_all()
    current = db.session.get(ProcessingJob, claimed.id)
    assert {column.name: getattr(current, column.name) for column in ProcessingJob.__table__.columns} == expected
    assert (source.status, source.transcription, source.error_message) == before_recording
    for notice in notices:
        notice.assert_not_called()
