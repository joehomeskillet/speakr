"""A hung LLM must not block transcription workers (CLINIC_MODE).

Staging: the local LLM stopped answering while both transcription workers were
inside title/summary calls, so no new recording was transcribed. Title and
summary are now a follow-up job on the summary queue, with a short LLM timeout.
"""
import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.app import app, db
from src.clinic import llm_limits
from src.models import ProcessingJob, Recording
import src.tasks.processing as proc
from src.services.error_classification import SummaryStepFailed
from src.services.job_queue import job_queue


@pytest.fixture
def queue(monkeypatch):
    job_queue.stop()  # a worker thread leaked by an earlier test must not claim our jobs
    monkeypatch.setattr(job_queue, '_is_owner', False)  # enqueue only; no workers in the test
    job_queue.init_app(app)
    yield job_queue
    job_queue.stop()


def _connector():
    connector = MagicMock()
    connector.PROVIDER_NAME = 'test-connector'
    connector.supports_diarization = False
    connector.default_diarize = False
    specs = MagicMock()
    specs.max_duration_seconds = 1800
    specs.handles_chunking_internally = False
    specs.recommended_chunk_seconds = 600
    specs.unsupported_codecs = []
    connector.specifications = specs
    response = MagicMock()
    response.text = 'Dictated words.'
    response.segments = None
    response.speakers = None
    response.speaker_embeddings = None
    response.language = 'de'
    response.has_diarization.return_value = False
    connector.transcribe.side_effect = lambda request: response
    return connector


def _transcribe(recording_id, llm):
    """Run the transcription job of a recording; `llm` stands in for every LLM call."""
    path = os.path.join(app.config['UPLOAD_FOLDER'], f'dec_{recording_id}.mp3')
    with open(path, 'wb') as f:
        f.write(b'\x00' * 1024)
    conv = MagicMock()
    conv.was_converted = False
    with patch('src.services.transcription.get_connector', return_value=_connector()), \
         patch.object(proc, 'is_video_file', return_value=False), \
         patch.object(proc, 'convert_if_needed', return_value=conv), \
         patch.object(proc, 'client', MagicMock()), \
         patch.object(proc, 'call_llm_completion', side_effect=llm), \
         patch.object(proc, 'ENABLE_INQUIRE_MODE', False), \
         patch.object(proc, 'chunking_service') as chunking:
        chunking.needs_chunking.return_value = False
        chunking.get_audio_duration.return_value = 60.0
        proc.transcribe_with_connector(app.app_context(), recording_id, path, 'a.wav', time.time(),
                                       mime_type='audio/mpeg')


def _follow_ups(recording_id):
    with app.app_context():
        return [(j.job_type, json.loads(j.params or '{}')) for j in ProcessingJob.query.filter_by(
            recording_id=recording_id, job_type='summarize', status='queued').all()]


def test_transcription_queues_title_and_summary_and_never_calls_the_llm(clinic_on, queue, world):
    calls = []
    _transcribe(world['rec_a'], lambda *a, **k: calls.append(1))
    assert calls == []
    assert _follow_ups(world['rec_a']) == [('summarize', {'follow_up': True, 'summarize': True})]
    with app.app_context():
        assert db.session.get(Recording, world['rec_a']).transcription == 'Dictated words.'


def test_upstream_still_calls_the_llm_inside_the_transcription_job(clinic_off, queue, world):
    calls = []

    def llm(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('no llm in this test')

    _transcribe(world['rec_a'], llm)
    assert calls and _follow_ups(world['rec_a']) == []


def test_a_hung_llm_does_not_block_the_next_transcription(clinic_on, queue, world):
    release = threading.Event()
    entered = threading.Event()

    def hung_llm(*args, **kwargs):
        entered.set()
        release.wait(30)
        raise RuntimeError('LLM did not answer')

    errors = []

    def follow_up_worker():  # the summary worker running the queued job
        try:
            from src.clinic.follow_up import run_follow_up
            with patch.object(proc, 'client', MagicMock()), \
                 patch.object(proc, 'call_llm_completion', side_effect=hung_llm), \
                 patch.object(proc, 'ENABLE_INQUIRE_MODE', False):
                run_follow_up(app.app_context(), world['rec_a'], {'follow_up': True, 'summarize': True})
        except SummaryStepFailed:
            pass
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=follow_up_worker)
    worker.start()
    try:
        assert entered.wait(10), 'the summary worker never reached the LLM'
        _transcribe(world['rec_b'], hung_llm)  # a second transcription still finishes
        with app.app_context():
            assert db.session.get(Recording, world['rec_b']).transcription == 'Dictated words.'
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(30)
    assert errors == []


def test_a_failed_summary_keeps_the_transcript_and_shows_the_failure(clinic_on, queue, world):
    from src.clinic.follow_up import run_follow_up

    with app.app_context():
        rec = db.session.get(Recording, world['rec_a'])
        rec.status = 'SUMMARIZING'
        rec.transcription = 'Kept transcript ' * 20
        rec.summary = None
        db.session.commit()

    def down(*args, **kwargs):
        raise RuntimeError('LLM unreachable')

    with patch.object(proc, 'client', MagicMock()), \
         patch.object(proc, 'call_llm_completion', side_effect=down), \
         patch.object(proc, 'ENABLE_INQUIRE_MODE', False):
        with pytest.raises(SummaryStepFailed):
            run_follow_up(app.app_context(), world['rec_a'], {'follow_up': True, 'summarize': True})

    with app.app_context():
        rec = db.session.get(Recording, world['rec_a'])
        assert rec.transcription == 'Kept transcript ' * 20
        assert rec.status == 'FAILED' and rec.summary  # the failure is the summary text; reprocess retries


# --- the per-call limits ----------------------------------------------------

def test_llm_calls_get_a_short_timeout_and_bounded_retries(clinic_on, monkeypatch):
    monkeypatch.delenv('CLINIC_LLM_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('CLINIC_LLM_MAX_RETRIES', raising=False)
    client = MagicMock()
    assert llm_limits.limited(client) is client.with_options.return_value
    client.with_options.assert_called_once_with(timeout=120.0, max_retries=2)

    monkeypatch.setenv('CLINIC_LLM_TIMEOUT_SECONDS', '30')
    monkeypatch.setenv('CLINIC_LLM_MAX_RETRIES', '1')
    assert llm_limits.call_options() == {'timeout': 30.0, 'max_retries': 1}
    monkeypatch.setenv('CLINIC_LLM_TIMEOUT_SECONDS', 'soon')
    assert llm_limits.call_options()['timeout'] == 120


def test_upstream_llm_client_is_untouched_with_clinic_mode_off(clinic_off):
    client = MagicMock()
    assert llm_limits.limited(client) is client
    client.with_options.assert_not_called()
