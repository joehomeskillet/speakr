"""Import-to-ASR handoff on synthetic audio and the application's own queue."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from src.app import app, db
from src.clinic import import_centre, ingest, policy, revisions, web_ingest
from src.clinic.batch_ingest import BatchFile
from src.clinic.import_visibility import ClarificationPolicy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import ProcessingJob, Recording
from src.services.job_queue import job_queue
from src.services.storage import get_storage_service
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector, SYNTHETIC_SENTENCES

from test_import_centre import NOW, _env, _intent, _upload, env  # noqa: F401
from test_stub_asr import FIXTURE
from test_wp_batch_ingest import _run, ctx  # noqa: F401
from test_wp_v8_agent_routes import PROXY, _finalize


def _job(user):
    return ProcessingJob.query.filter_by(user_id=user, job_type='transcribe').one()


def _assert_link(job, original, data, store):
    source = job.recording
    dictation = db.session.get(ClinicDictation, original.dictation_id)
    assert original.origin_ref == f'upload:recording:{source.id}:time=upload'
    assert db.session.get(Recording, dictation.recording_id).audio_path is None
    assert source.id != dictation.recording_id and source.user_id == dictation.author_user_id
    path = store.file_path(original.storage_key)
    with get_storage_service().materialize(source.audio_path) as stored:
        working = Path(stored.local_path)
        assert not path.samefile(working)
        assert working.read_bytes() == path.read_bytes() == data


def _process_with_stub(job, monkeypatch):
    # The offline connector takes PCM WAV only.
    monkeypatch.setattr('src.utils.audio_conversion.AUDIO_COMPRESS_UPLOADS', False)
    monkeypatch.setattr('src.services.transcription.get_connector', lambda: ClinicStubTranscriptionConnector({}))
    job_queue._process_job(job)
    db.session.expire_all()
    assert job.status == 'completed', job.error_message


def test_batch_worker_reads_linked_audio_and_replay_keeps_completed_job(ctx, clinic_on, monkeypatch):
    service, author, other, _ = ctx
    data = FIXTURE.read_bytes()
    files = [BatchFile('tone.wav', data, author), BatchFile('refused.wav', data + b'!', other)]
    stored, refused = _run(ctx, files)
    assert (stored.status, refused.status) == ('stored', 'rejected')
    job = _job(author)
    original = db.session.get(ClinicOriginal, stored.original_id)
    _assert_link(job, original, data, service)
    assert json.loads(original.receipt)['origin_ref'] == 'tone.wav'

    _process_with_stub(job, monkeypatch)
    assert SYNTHETIC_SENTENCES[0] in job.recording.transcription
    assert service.file_path(original.storage_key).read_bytes() == data
    replay = _run(ctx, files)[0]
    assert replay.status == 'replayed' and replay.original_id == stored.original_id
    assert _job(author).id == job.id
    assert ProcessingJob.query.filter_by(user_id=other).count() == 0


@pytest.mark.skipif(not hasattr(web_ingest, 'dictation_for_recording'), reason='shared ASR hook requires v8-127')
def test_shared_asr_hook_publishes_revision_to_import(ctx, clinic_on, monkeypatch):
    stored, = _run(ctx, [BatchFile('tone.wav', FIXTURE.read_bytes(), ctx[1])])
    job = _job(ctx[1])
    _process_with_stub(job, monkeypatch)
    dictation = db.session.get(ClinicDictation, stored.dictation_id)
    head = revisions.current_revision(dictation)
    assert head.kind == 'ASR' and SYNTHETIC_SENTENCES[0] in head.body
    assert head.original_version_id == stored.original_id


def test_concurrent_batch_deliveries_share_one_job(ctx, clinic_on, monkeypatch):
    files = [BatchFile('tone.wav', FIXTURE.read_bytes(), ctx[1])]
    ready = Barrier(2)
    probe = ingest.probe_original_duration_ms

    def simultaneous_first_import(path, digest):
        ready.wait(timeout=10)
        return probe(path, digest)

    monkeypatch.setattr(ingest, 'probe_original_duration_ms', simultaneous_first_import)

    def deliver():
        with app.app_context():
            result = _run(ctx, files)[0]
            connection = db.session.connection()
            if connection.dialect.name == 'sqlite':
                assert not connection.connection.driver_connection.in_transaction
            return result

    with ThreadPoolExecutor(max_workers=2) as workers:
        deliveries = [workers.submit(deliver) for _ in range(2)]
        first, second = [delivery.result(timeout=30) for delivery in deliveries]
    assert first.original_id and first.original_id == second.original_id
    assert _job(ctx[1]).recording_id == Recording.query.filter_by(processing_source='import').one().id


def test_import_rollback_and_retry(ctx, clinic_on, monkeypatch):
    _, author, _, _ = ctx
    files = [BatchFile('retry.wav', FIXTURE.read_bytes(), author)]
    storage = get_storage_service()

    def fail(*args, **kwargs):
        raise OSError('synthetic storage failure')

    with monkeypatch.context() as patch:
        patch.setattr(storage, 'upload_local_file', fail)
        assert _run(ctx, files)[0].status == 'failed'
    assert ClinicOriginal.query.count() == 0
    assert ProcessingJob.query.filter_by(user_id=author).count() == 0
    assert _run(ctx, files)[0].status == 'stored'
    assert _job(author).status == 'queued'


def test_stock_batch_has_no_transcription_job(ctx, clinic_off):
    stored, = _run(ctx, [BatchFile('tone.wav', FIXTURE.read_bytes(), ctx[1])])
    original = db.session.get(ClinicOriginal, stored.original_id)
    assert original.origin_ref == 'tone.wav'
    assert ProcessingJob.query.filter_by(user_id=ctx[1]).count() == 0


def test_matched_agent_import_reports_unsupported_audio(env, clinic_on):
    author = env['author'].id
    policy.register_grant_provider(lambda uid: [policy.Grant('own', frozenset({'import'}))] if uid == author else [])
    _intent(env)
    data = b'unknown synthetic format'
    ingest_id, body, _ = _upload(env, data)
    result = import_centre.finalize_delivery(
        env['principal'], env['policy'], ingest_id=ingest_id, body=body, now=NOW,
        ingest_service=env['service'], device_map={'USB-1': author},
    )
    assert result['state'] == 'STORED'
    job = _job(author)
    assert job.status == 'failed' and job.recording.status == 'FAILED'
    assert job.error_message == job.recording.error_message
    _assert_link(job, db.session.get(ClinicOriginal, result['original_id']), data, env['service'])


def test_agent_finalize_and_late_assignment(env, monkeypatch, request):
    # The stock app registers its machine routes and binds the proxy credential at startup.
    if not os.environ.get('V8_145_SYNTHETIC_APP'):
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', request.node.nodeid, '-q', '-p', 'no:cacheprovider'],
            env={**os.environ, 'V8_145_SYNTHETIC_APP': '1', 'CLINIC_MODE': '1',
                 'PYTEST_DATABASE_URI': '', 'CLINIC_AGENT_PROXY_SECRET': PROXY},
            capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return
    author = env['author'].id
    policy.register_grant_provider(lambda uid: [policy.Grant('own', frozenset({'import'}))] if uid == author else [])
    intent = _intent(env, language_id=1033)
    data = FIXTURE.read_bytes()
    ingest_id, body, _ = _upload(env, data, name='dictation.wav')
    monkeypatch.setattr(import_centre, 'utcnow', lambda: NOW)

    def finalize():
        with app.app_context():
            response = _finalize({'client': app.test_client()}, ingest_id, body['chunks'], data=data)
            assert response.status_code == 200, response.get_data(as_text=True)
            return response.json

    first = finalize()
    assert first['state'] == 'UNKNOWN' and first['dictation_id'] is None
    assert ProcessingJob.query.filter_by(user_id=author).count() == 0
    import_centre.assign_unassigned(
        actor_user_id=author, delivery_id=first['import']['delivery_id'], intent_id=intent['id'],
        expected_snapshot_version=first['snapshot_version'],
        policy=ClarificationPolicy(may_access=lambda *_: True, may_target=lambda *_: True), now=NOW,
    )
    stored = finalize()
    assert stored['state'] == 'STORED'
    assert finalize() == stored
    job = _job(author)
    assert json.loads(job.params)['language'] == 'en'
    original = db.session.get(ClinicOriginal, stored['original_id'])
    assert json.loads(original.receipt)['origin'] == 'agent'
    assert original.origin_ref == f'upload:recording:{job.recording_id}:time=upload'
