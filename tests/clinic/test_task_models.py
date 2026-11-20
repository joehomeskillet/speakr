"""The model form, caller scope and the identifiers in real local HTTP requests."""

from io import BytesIO
from threading import Thread

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from src.clinic import routes_task_models
from src.clinic.asr_fallback import wrap_with_gpu_fallback
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import SystemSetting
from src.services import llm
from src.services.job_queue import DICTATION_SUMMARY_JOB, job_queue
from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
from tests.clinic.test_wp_asr_engine_wire import ctx  # noqa: F401
from tests.clinic.test_dictation_summary import scene  # noqa: F401

URL = '/api/clinic/v1/admin/task-models'


@pytest.fixture
def api(scene, monkeypatch):
    client, endpoint, signin, calls, row, owner, other, admin, source = scene
    from src.api.admin import admin_bp
    client.application.register_blueprint(routes_task_models.blueprint)
    client.application.register_blueprint(admin_bp)
    service = Flask('local_models_fixture')
    health = {'up': True}
    audio_calls = []

    @service.get('/healthz')
    def status():
        return jsonify(model_loaded=health['up'], model='bundled-model', versions={'openvino_genai': 'fixture'}), (200 if health['up'] else 503)

    @service.get('/<engine>/v1/models')
    def models(engine):
        if not health.get('catalog'):
            return jsonify(error='no_catalog'), 404
        return jsonify(data=[dict(id=engine + '-served')])

    @service.post('/<engine>/v1/audio/transcriptions')
    def transcribe(engine):
        audio_calls.append((engine, request.form.get('model')))
        return jsonify(text='Synthetische Erkennung.', segments=[], language='de', duration=1.0)

    server = make_server('127.0.0.1', 0, service, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('CLINIC_GPU_ASR_BASE_URL', base + '/gpu/v1')
    monkeypatch.delenv('CLINIC_GPU_ASR_MODEL', raising=False)
    monkeypatch.setattr(llm, 'TEXT_MODEL_NAME', 'deployment-llm')
    cpu = OpenAIWhisperConnector({'api_key': 'synthetic', 'base_url': base + '/cpu/v1', 'model': 'deployment-whisper'})
    wrapped = wrap_with_gpu_fallback(cpu)
    monkeypatch.setattr('src.services.transcription.get_connector', lambda: wrapped)
    try:
        yield scene, health, audio_calls, cpu, wrapped
    finally:
        cpu.client.close()
        wrapped._gpu.client.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_rights_validation_and_conflict_leave_all_models_unchanged(api):
    scene, _, _, _, _ = api
    client, _, signin, _, _, _, other, admin, _ = scene
    assert client.get(URL).status_code == 401
    signin(other)
    db.session.get(ClinicAccountPolicy, other).is_identity_admin = True
    db.session.commit()
    assert client.get(URL).status_code == 403
    hdr = signin(admin)
    snapshot = client.get(URL).json
    values = dict(asr_gpu='whisper-1', asr_cpu='lokal/Größe:3', summary='gemma-v1')
    def save(rev, models):
        return client.put(URL, headers=hdr, json=dict(revision=rev, models=models))
    assert client.put(URL, json=dict(revision='0', models=values)).status_code == 403
    assert save('0', dict(values, asr_gpu='unknown')).status_code == 400
    for invalid in ('https://outside.example/model', '../private', 'file:/tmp/a', 'bad\nname', {}, 'x'*201):
        assert save('0', dict(values, summary=invalid)).status_code == 400
    assert client.get(URL).json == snapshot
    accepted = save('0', values)
    assert accepted.status_code == 200
    assert accepted.json['models']['asr_cpu']['effective'] == 'lokal/Größe:3'
    assert accepted.json == client.get(URL).json
    assert 'clinic_task_models' not in [entry['key'] for entry in client.get('/admin/settings').json]
    assert client.post('/admin/settings', headers=hdr, json=dict(key='clinic_task_models', value='unvalidated')).status_code == 400
    assert save('0', dict(values, summary='stale')).status_code == 409
    assert client.get(URL).json == accepted.json
    db.session.get(ClinicAccountPolicy, admin).status = 'disabled'
    db.session.commit()
    assert client.get(URL).status_code == 403
    assert save(accepted.json['revision'], values).status_code == 403


def test_local_transport_uses_each_task_id_immediately_and_inherits_after_reset(api):
    scene, health, audio_calls, cpu, wrapped = api
    client, endpoint, signin, calls, _, owner, _, admin, _ = scene
    hdr = signin(admin)
    SystemSetting.set_setting('transcription_default_model', 'curated-cpu')
    original = client.get(URL).json
    m = original['models']
    assert m['asr_gpu'] == dict(active=True, choices=['bundled-model', 'whisper-1'], choices_strict=True, effective='bundled-model', source='service', override=None)
    assert m['asr_cpu']['effective'] == 'curated-cpu' and m['asr_cpu']['source'] == 'existing_default'
    assert m['summary']['effective'] == 'deployment-llm'
    values = dict(asr_gpu='whisper-1', asr_cpu='local/whisper:2', summary='local/gemma:2')
    updated = client.put(URL, headers=hdr, json=dict(revision=original['revision'], models=values)).json
    def audio():
        return TranscriptionRequest(audio_file=BytesIO(b'synthetic'), filename='test.wav', model='curated-cpu')
    assert wrapped.transcribe(audio()).engine_actual == 'gpu'
    health['up'] = False
    assert wrapped.transcribe(audio()).engine_actual == 'cpu'
    cpu.transcribe(audio())
    assert audio_calls == [('gpu', 'whisper-1'), ('cpu', 'local/whisper:2'), ('cpu', 'local/whisper:2')]
    manual = signin(owner)
    assert client.post(endpoint, headers=manual, json={}).status_code == 202
    job_queue._process_job(job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary'))
    llm.call_llm_completion([dict(role='user', content='Neutraler Titel')], operation_type='title')
    assert [call['model'] for call in calls] == ['local/gemma:2', 'deployment-llm']
    hdr = signin(admin)
    # With no list, admins can enter model identifiers; deployment endpoints stay fixed.
    edited = client.put(URL, headers=hdr, json=dict(revision=updated['revision'], models=dict(values, asr_gpu='new-local'))).json
    assert edited['models']['asr_gpu']['choices'] == [] and edited['models']['asr_gpu']['override'] == 'new-local'
    reset = client.put(URL, headers=hdr, json=dict(revision=edited['revision'], models=dict(asr_gpu=None, asr_cpu='', summary=None)))
    assert reset.status_code == 200
    assert all(field['override'] is None for field in reset.json['models'].values())
    health['up'] = True
    wrapped.transcribe(audio())
    cpu.transcribe(audio())
    assert audio_calls[-2:] == [('gpu', 'bundled-model'), ('cpu', 'curated-cpu')]


def test_authoritative_local_catalog_and_explicit_reset(api, monkeypatch):
    from openai import OpenAI

    scene, health, _, cpu, _ = api
    client, _, signin, _, _, _, _, admin, _ = scene
    hdr = signin(admin)
    health['catalog'] = True
    url = cpu.config['base_url'].replace('/cpu/', '/text/')
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', url)
    with OpenAI(base_url=url, api_key='synthetic') as text:
        monkeypatch.setattr(llm, 'client', text)
        first = client.get(URL).json
        fields = first['models']
        for kind, model in [('asr_cpu', 'cpu-served'), ('summary', 'text-served')]:
            assert fields[kind]['choices_strict'] and fields[kind]['choices'] == [model]
            assert fields[kind]['effective'] is None
        values = dict(asr_gpu=None, asr_cpu='cpu-served', summary='text-served')
        assert client.put(URL, headers=hdr, json=dict(revision='0', models=dict(values, summary='unknown'))).status_code == 400
        response = client.put(URL, headers=hdr, json=dict(revision='0', models=values))
        assert response.status_code == 200
        assert response.json['models']['summary']['effective'] == 'text-served'
        assert response.json['models']['asr_cpu']['effective'] == 'cpu-served'


def test_asr_revision_carries_the_concrete_completed_response_model(ctx):
    from src.clinic.revisions import history
    from tests.clinic.test_wp_asr_engine_wire import _connector, _dictation_with_head, _run

    rec, dictation, _, _ = _dictation_with_head(ctx, human=True)
    connector = _connector(text='Neue lokale Testfassung.', model='cpu-default')
    connector.transcribe.return_value.provider = 'openai_whisper'
    connector.transcribe.return_value.model = 'actual-on-gpu'
    _run(rec, connector, transcription_model='upload-default')
    assert history(dictation)[-1].model_ref == 'openai_whisper:actual-on-gpu'


def test_async_cpu_provenance_preserves_uncertainty_for_resumed_jobs():
    from tests.test_asr_async_jobs import BASE, FakeWhisperX, _connector, _request
    from src.tasks.processing import _response_model_ref

    fake = FakeWhisperX(script=[('done', None, 1)])
    connector = _connector(fake)
    sent = connector.transcribe(_request(model='newly-selected'))
    assert _response_model_ref(sent) == 'asr_endpoint:newly-selected'
    saved = {'job_id': fake.add_job(script=[('done', None, 1)]), 'base_url': BASE}
    resumed = connector.transcribe(_request(saved, model='different-selection-now'))
    assert _response_model_ref(resumed) == 'asr_endpoint:unspecified'
