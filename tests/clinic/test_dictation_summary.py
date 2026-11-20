"""The Flask session, queue, current clinic revision and local model transport together."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf
from openai import OpenAI

from src.clinic import revisions, routes_dictation_detail
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services import llm
from src.services.job_queue import DICTATION_SUMMARY_JOB, job_queue


@contextmanager
def model_server():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'id': 'synthetic', 'object': 'chat.completion', 'created': 0, 'model': 'test',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Synthetisches Ergebnis.'},
                             'finish_reason': 'stop'}],
            }).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def scene(tmp_path, monkeypatch, request):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-fixture', SQLALCHEMY_DATABASE_URI='sqlite:///' + str(tmp_path/'db'))
    db.init_app(app)
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    app.register_blueprint(routes_dictation_detail.create_blueprint(authorize=None, dictation_snapshot=None))

    @app.get('/test-login/<int:uid>')
    def sign_in(uid):
        login_user(db.session.get(User, uid))
        return generate_csrf()

    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setattr(job_queue, '_app', app)
    with model_server() as (model_url, calls), app.app_context():
        monkeypatch.setenv('TEXT_MODEL_BASE_URL', model_url)
        for key in ('CHAT_MODEL_BASE_URL', 'TRANSLATION_MODEL_BASE_URL', 'EMBEDDING_BASE_URL'):
            monkeypatch.delenv(key, raising=False)
        with OpenAI(base_url=model_url, api_key='synthetic') as local:
            monkeypatch.setattr(llm, 'client', local)
            db.create_all()
            owner = User(username='syn-owner', email='own@example.invalid', password='x')
            other = User(username='syn-other', email='other@example.invalid', password='x')
            admin = User(username='syn-admin', email='admin@example.invalid', password='x', is_admin=True)
            db.session.add_all([owner, other, admin])
            db.session.flush()
            for user in (owner, other, admin):
                db.session.add(ClinicAccountPolicy(user_id=user.id, login_policy='local_only', status='active'))
            source = Recording(user_id=owner.id, title='source', transcription='Veraltete Ausgangsabschrift.', summary='Historisches Ergebnis.')
            anchor = Recording(user_id=owner.id, title='anchor', status='COMPLETED', transcription='Stocktext.')
            db.session.add_all([source, anchor])
            db.session.flush()
            row = ClinicDictation(author_user_id=owner.id, recording_id=anchor.id)
            db.session.add(row)
            db.session.flush()
            original = ClinicOriginal(dictation_id=row.id, content_hash='a'*64, size_bytes=8, storage_key='syn',
                                      receipt='syn', origin_ref=f'upload:recording:{source.id}:time=upload')
            db.session.add(original)
            db.session.flush()
            if getattr(request, 'param', None) != 'fresh':
                first = revisions.add_asr_revision(row, original, 'Fehlerhafter neutraler Beispielsatz.', 'synthetic')
                revisions.add_human_revision(row, first, 'Korrigierter neutraler Beispielsatz.', owner.id)
            db.session.commit()
            client = app.test_client()
            url = f'/api/clinic/v1/dictations/{row.id}/summary'
            def signin(uid):
                return {'X-CSRF-Token': client.get(f'/test-login/{uid}').get_data(as_text=True)}
            yield client, url, signin, calls, row, owner.id, other.id, admin.id, source


def test_explicit_regular_queue_uses_corrected_text_and_is_independent_of_title(scene, monkeypatch):
    client, url, signin, calls, row, owner, _, _, source = scene
    hdr = signin(owner)
    payload = client.get(url).json
    assert payload == {'text': 'Historisches Ergebnis.', 'status': 'stale', 'may_generate': True}
    assert not calls and ProcessingJob.query.count() == 0
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    job_queue.enqueue(owner, row.recording_id, 'summarize', {'follow_up': True, 'summarize': False})
    for _ in range(2):
        result = client.post(url, json={}, headers=hdr)
        assert result.status_code == 202 and result.json['status'] == 'queued'
    assert ProcessingJob.query.filter_by(job_type=DICTATION_SUMMARY_JOB).count() == 1
    claim = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    assert claim is not None
    assert client.get(url).json['status'] == 'processing'
    job_queue._process_job(claim)
    db.session.expire_all()
    assert client.get(url).json == {'text': 'Synthetisches Ergebnis.', 'status': 'ready', 'may_generate': True}
    assert len(calls) == 1
    prompt = str(calls[0]['messages'])
    assert 'Korrigierter neutraler Beispielsatz.' in prompt
    assert 'Veraltete Ausgangsabschrift.' not in prompt and 'Stocktext.' not in prompt
    assert source.summary == 'Historisches Ergebnis.'
    assert revisions.current_revision(row).body == 'Korrigierter neutraler Beispielsatz.'
    revisions.add_human_revision(row, revisions.current_revision(row), 'Zweite synthetische Korrektur.', owner)
    db.session.commit()
    assert client.get(url).json['status'] == 'stale'


def test_privacy_feature_gate_and_markers(scene, monkeypatch):
    client, url, signin, calls, row, owner, other, admin, source = scene
    assert client.get(url).status_code == 401
    foreign = signin(other)
    assert client.get(url).status_code == 404
    assert client.post(url, json={}, headers=foreign).status_code == 404
    readonly = signin(admin)
    assert client.get(url).json['may_generate'] is False
    assert client.post(url, json={}, headers=readonly).status_code == 403
    hdr = signin(owner)
    assert client.post(url, json={}).status_code == 403
    source.summary = '[Summary generation failed: fixture]'
    db.session.commit()
    assert client.get(url).json['text'] is None
    assert client.get(url).json['status'] == 'failed'
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://example.invalid/v1')
    assert not client.get(url).json['may_generate']
    assert client.post(url, json={}, headers=hdr).status_code == 503
    assert not calls and ProcessingJob.query.count() == 0


@pytest.mark.parametrize('race', ['scope', 'head', 'provider'])
def test_worker_rechecks_before_using_clinical_content(scene, monkeypatch, race):
    client, url, signin, calls, row, owner, _, _, source = scene
    hdr = signin(owner)
    assert client.post(url, json={}, headers=hdr).status_code == 202
    claim = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    if race == 'scope':
        db.session.get(ClinicAccountPolicy, owner).status = 'disabled'
    elif race == 'head':
        revisions.add_human_revision(row, revisions.current_revision(row), 'Eine spätere neue Fassung.', owner)
    else:
        monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://example.invalid/v1')
    db.session.commit()
    job_queue._process_job(claim)
    db.session.expire_all()
    assert db.session.get(ProcessingJob, claim.id).status == 'failed' and calls == []
    assert source.summary == 'Historisches Ergebnis.'
    assert db.session.get(Recording, row.recording_id).status == 'COMPLETED'


def test_revision_change_during_inference_discards_result(scene, monkeypatch):
    client, url, signin, calls, row, owner, _, _, source = scene
    hdr = signin(owner)
    assert client.post(url, json={}, headers=hdr).status_code == 202
    original_call = llm.call_llm_completion

    def advance(*args, **kwargs):
        result = original_call(*args, **kwargs)
        current = db.session.get(ClinicDictation, row.id)
        revisions.add_human_revision(current, revisions.current_revision(current), 'Späte echte Korrektur.', owner)
        db.session.commit()
        return result

    monkeypatch.setattr(llm, 'call_llm_completion', advance)
    claim = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    job_queue._process_job(claim)
    db.session.expire_all()
    assert db.session.get(ProcessingJob, claim.id).status == 'failed' and len(calls) == 1
    assert client.get(url).json['text'] == source.summary == 'Historisches Ergebnis.'


def test_ambiguous_historical_source_is_never_disclosed(scene):
    client, url, signin, calls, row, owner, _, _, source = scene
    signin(owner)
    other_anchor = Recording(user_id=owner, title='Zweiter Anker')
    db.session.add(other_anchor)
    db.session.flush()
    other = ClinicDictation(author_user_id=owner, recording_id=other_anchor.id)
    db.session.add(other)
    db.session.flush()
    db.session.add(ClinicOriginal(dictation_id=other.id, content_hash='b'*64, size_bytes=8, storage_key='other',
                                  receipt='second', origin_ref=f'legacy:recording:{source.id}:time=upload'))
    db.session.commit()
    assert client.get(url).json == {'status': 'empty', 'text': None, 'may_generate': True}
    assert not calls


def first_asr_event(row, source, kind='transcribe'):
    from src.clinic import asr_repeat
    from src.services import job_control
    from src.tasks.processing import _handle_late_asr

    job = ProcessingJob(user_id=source.user_id, recording_id=source.id, job_type=kind, status='processing')
    db.session.add(job)
    db.session.commit()
    attempt = asr_repeat.attempt_identity(job.id)

    def deliver(body):
        with job_control.running(job_control.JobControl(job.id)):
            prior = asr_repeat.lock_projection(source)
            source.transcription = body
            assert _handle_late_asr(source, prior, 'local-test', attempt=attempt).id == row.id
    return deliver


@pytest.mark.parametrize('scene', ['fresh'], indirect=True)
def test_first_asr_and_job_commit_together_and_retry_only_titles_source(scene, monkeypatch):
    from src.clinic.follow_up import enqueue_follow_up, run_follow_up
    from src.tasks import processing

    client, url, signin, calls, row, owner, _, _, source = scene
    hdr = signin(owner)
    deliver = first_asr_event(row, source)
    text = 'Erste synthetische automatische Abschrift.'
    deliver(text)
    db.session.rollback()
    assert revisions.current_revision(row) is None and ProcessingJob.query.filter_by(job_type=DICTATION_SUMMARY_JOB).count() == 0
    deliver(text)
    db.session.commit()
    head = revisions.current_revision(row)
    queued = ProcessingJob.query.filter_by(job_type=DICTATION_SUMMARY_JOB).one()
    assert queued.recording_id == row.recording_id and queued.user_id == owner
    assert json.loads(queued.params) == {'revision_id': head.id, 'dictation_id': row.id, 'automatic': True}
    title = db.session.get(ProcessingJob, enqueue_follow_up(source, True, dictation=row))
    assert title.job_type == 'summarize' and title.recording_id == source.id
    assert json.loads(title.params) == {'follow_up': True, 'summarize': False}
    titled = []
    monkeypatch.setattr(processing, 'generate_title_task',
                        lambda _, rid, will_auto_summarize: titled.append((rid, will_auto_summarize)))
    monkeypatch.setattr(processing, 'generate_summary_only_task', lambda *_: pytest.fail('unbound summary'))
    run_follow_up(client.application.app_context(), source.id, {'follow_up': True, 'summarize': True})
    assert titled == [(source.id, False)]
    assert client.post(url, json={}, headers=hdr).status_code == 202
    claim = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    job_queue._process_job(claim)
    db.session.expire_all()
    assert client.get(url).json['status'] == 'ready' and len(calls) == 1
    assert 'Erste synthetische automatische Abschrift.' in str(calls[0]['messages'])
    assert source.summary == 'Historisches Ergebnis.'
    deliver(text)
    db.session.commit()
    revisions.add_human_revision(row, head, 'Spätere menschliche Korrektur.', owner)
    db.session.commit()
    deliver('Erneute automatische Erkennung.')
    db.session.commit()
    assert client.get(url).json['status'] == 'stale'
    assert ProcessingJob.query.filter_by(job_type=DICTATION_SUMMARY_JOB).count() == 1


@pytest.mark.parametrize('scene', ['fresh'], indirect=True)
@pytest.mark.parametrize('condition', ['opt_out', 'disabled', 'external', 'inactive', 'reprocess'])
def test_first_asr_respects_existing_controls_and_does_not_backfill(scene, monkeypatch, condition):
    from src.models import SystemSetting

    client, url, signin, calls, row, owner, _, _, source = scene
    if condition == 'opt_out':
        db.session.get(User, owner).auto_summarization = False
    elif condition == 'disabled':
        SystemSetting.set_setting('disable_auto_summarization', True, setting_type='boolean')
    elif condition == 'external':
        monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://example.invalid/v1')
    elif condition == 'inactive':
        db.session.get(ClinicAccountPolicy, owner).status = 'disabled'
    db.session.commit()
    deliver = first_asr_event(row, source, 'reprocess_transcription' if condition == 'reprocess' else 'transcribe')
    deliver('Eine synthetische Erstfassung.')
    db.session.commit()
    assert revisions.current_revision(row) is not None
    assert ProcessingJob.query.filter_by(job_type=DICTATION_SUMMARY_JOB).count() == 0
    if condition in ('opt_out', 'disabled'):
        assert client.post(url, json={}, headers=signin(owner)).status_code == 202
    assert not calls


@pytest.mark.parametrize('scene', ['fresh'], indirect=True)
def test_pending_automatic_job_honors_opt_out_and_can_still_be_requested_manually(scene):
    client, url, signin, calls, row, owner, _, _, source = scene
    deliver = first_asr_event(row, source)
    deliver('Neue synthetische Aufzeichnung.')
    db.session.commit()
    db.session.get(User, owner).auto_summarization = False
    db.session.commit()
    claim = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    job_queue._process_job(claim)
    db.session.expire_all()
    assert db.session.get(ProcessingJob, claim.id).status == 'failed' and calls == []
    assert client.post(url, json={}, headers=signin(owner)).status_code == 202
    manual = job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary')
    job_queue._process_job(manual)
    db.session.expire_all()
    assert len(calls) == 1 and client.get(url).json['status'] == 'ready'


@pytest.mark.parametrize('scene', ['fresh'], indirect=True)
def test_early_open_waits_only_while_first_recognition_and_auto_are_active(scene):
    client, url, signin, calls, row, owner, _, _, source = scene
    signin(owner)
    source.summary = None
    db.session.commit()
    assert client.get(url).json['status'] == 'empty'
    convert = ProcessingJob(user_id=owner, recording_id=source.id, job_type='convert', status='queued')
    db.session.add(convert)
    db.session.commit()
    assert client.get(url).json == {'status': 'waiting_for_transcription', 'may_generate': False, 'text': None}
    db.session.get(User, owner).auto_summarization = False
    db.session.commit()
    assert client.get(url).json['status'] == 'empty'
    db.session.get(User, owner).auto_summarization = True
    convert.status = 'failed'
    db.session.commit()
    assert client.get(url).json['status'] == 'empty'
    repeat = ProcessingJob(user_id=owner, recording_id=source.id, job_type='reprocess_transcription', status='queued')
    db.session.add(repeat)
    db.session.commit()
    assert client.get(url).json['status'] == 'empty'
    deliver = first_asr_event(row, source)
    assert client.get(url).json['status'] == 'waiting_for_transcription'
    deliver('Neuer synthetischer Text für die laufende Ansicht.')
    db.session.commit()
    assert client.get(url).json['status'] == 'queued'
    job_queue._process_job(job_queue._claim_next_job([DICTATION_SUMMARY_JOB], 'summary'))
    db.session.expire_all()
    assert client.get(url).json['status'] == 'ready' and len(calls) == 1
