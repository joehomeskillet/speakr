"""Author sends through real clinic startup, session policy and durable receipts."""
import io
import os
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from src.clinic import pools, recipient_defaults, revisions, web_ingest, workflow
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal
from src.database import db
from src.models import AuditEvent, ProcessingJob, Recording
from src.services import audit
from test_wp_v8_assignment_route import ROOT, _client, _dictation, _user


def test_send_through_real_app(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "send.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _draft(author, pool, *, audio=True, recognition=None):
    target, recording = _dictation(author, pool, sent=False)
    if audio:
        db.session.add(ClinicAudioRevision(dictation_id=target, content_hash='a' * 64))
    if recognition == 'done':
        original = ClinicOriginal(dictation_id=target, content_hash='b' * 64, size_bytes=1,
                                  storage_key='synthetic', receipt='stored', origin_ref='synthetic')
        db.session.add(original)
        db.session.flush()
        revisions.add_asr_revision(db.session.get(ClinicDictation, target), original, 'Test.', 'test')
    elif recognition == 'running':
        db.session.add(ProcessingJob(user_id=author, recording_id=recording,
                                     job_type='transcribe', status='processing'))
    return target


def _exercise_mic_replay(app, author, other):
    def audio(sample):
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(sample.to_bytes(2, 'little') * 8000)
        buf.seek(0)
        return buf

    clients = [_client(app, author), _client(app, author)]
    outsider, _ = _client(app, other)
    preflight = clients[0][0].get('/api/clinic/v1/recording-takes/preflight')
    assert preflight.json == {'routable': True, 'staff_service_names': ['Schreibdienst']}
    take_id = str(uuid4())
    lookup = f'/api/clinic/v1/recording-takes/{take_id}'
    label = 'Diktat 27.11.2026 09:03'

    def upload(actor, sample=1):
        browser, csrf = actor
        return browser.post('/upload', headers=csrf, data={'file': (audio(sample), 'aufnahme.wav'),
                            'from_recorder': 'true', 'clinic_take_id': take_id, 'title': label})

    with patch.object(web_ingest, 'queue_take', side_effect=RuntimeError('queue insert interrupted')):
        assert upload(clients[0]).status_code == 500
    assert clients[0][0].get(lookup).status_code == 404

    barrier = Barrier(2)

    def concurrent_upload(actor):
        barrier.wait(timeout=10)
        return upload(actor)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(concurrent_upload, clients))
    assert sorted(res.status_code for res in results) == [200, 202], [res.json for res in results]
    receipt = results[0].json
    assert results[1].json == receipt
    assert receipt['take_id'] == take_id and receipt['title'] == label and 1 <= receipt['audio_duration'] < 2
    browser, csrf = clients[0]
    assert browser.get(lookup).json == receipt
    assert browser.get(lookup).headers['Cache-Control'] == 'no-store'
    assert outsider.get(lookup).status_code == 404
    assert app.test_client().get(lookup).status_code == 401
    assert upload(clients[0], sample=2).status_code == 409

    target = receipt['dictation_id']
    url = f'/api/clinic/v1/dictations/{target}'
    etag = browser.get(url + '/detail').json['etag']
    headers = {**csrf, 'Idempotency-Key': take_id, 'If-Match': etag}
    assert browser.post(url + '/send', headers=headers, json={'take_id': str(uuid4())}).status_code == 400
    def send(actor):
        session, token = actor
        return session.post(url + '/send', headers={**headers, **token}, json={'take_id': take_id})

    def concurrent_send(actor):
        barrier.wait(timeout=10)
        return send(actor)

    with ThreadPoolExecutor(max_workers=2) as workers:
        sent, replay = workers.map(concurrent_send, clients)
    assert sent.status_code == replay.status_code == 200, (sent.json, replay.json)
    assert sent.json == replay.json
    assert sent.json['status'] == workflow.PREP_ONLINE
    assert sent.json['staff_service_names'] == ['Schreibdienst']

    with app.app_context():
        job = ProcessingJob.query.filter_by(recording_id=receipt['id'], job_type='transcribe').one()
        job.status = 'completed'
        db.session.commit()
    assert upload(clients[1]).json == receipt
    assert send(clients[1]).json == sent.json
    with app.app_context():
        assert Recording.query.filter_by(user_id=author, original_filename='aufnahme.wav').count() == 1
        assert ClinicDictation.query.filter_by(author_user_id=author).count() == 1
        dictation = db.session.get(ClinicDictation, target)
        assert dictation.version_counter == 1
        assert ClinicOriginal.query.filter_by(dictation_id=target).count() == 1
        assert ProcessingJob.query.filter_by(recording_id=receipt['id']).count() == 1
        assert AuditEvent.query.filter_by(recording_id=dictation.recording_id, event='recording.edited').count() == 1
        [assignment] = workflow.ClinicWorkflowDictation.query.filter_by(id=target).all()
        assert workflow.read_routing_target(assignment).mode == 'staff_pool'


def _exercise():
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        author, other = _user('author'), _user('other', admin=True)
        pool = pools.create_author_pool('Autoren', actor_id=other).id
        pools.add_author_member(pool, author, actor_id=other)
        pools.allow(other, 'pool', pool_id=pool, actions=['edit'], actor_id=other)
        idle = _draft(author, pool)
        asr = _draft(author, pool, recognition='done')
        running = _draft(author, pool, recognition='running')
        silent = _draft(author, pool, audio=False)
        db.session.commit()

    browser, outsider = _client(app, author), _client(app, other)
    preflight = '/api/config?recording_preflight=1'
    assert app.test_client().get(preflight).status_code == 401
    assert outsider[0].get(preflight).json == {'routable': False}
    assert browser[0].get(preflight).json == {'routable': False}

    def post(target, *, actor=browser, key=None, version=0, omit=None, body=None):
        client, csrf = actor
        headers = {**csrf, 'Idempotency-Key': key or str(uuid4()), 'If-Match': f'"{target}:{version}"'}
        headers.pop(omit, None)
        return client.post(f'/api/clinic/v1/dictations/{target}/send', headers=headers,
                           json={} if body is None else body)

    assert post(idle, actor=(app.test_client(), {})).status_code == 401
    assert post(idle, actor=outsider).status_code == 403
    assert post(idle, omit='X-CSRFToken').status_code == 400
    assert post(idle, omit='Idempotency-Key').status_code == 400
    assert post(idle, omit='If-Match').status_code == 428
    assert post(idle, body={'actor_id': other}).status_code == 400
    assert post(idle, version=1).json['code'] == 'STALE_VERSION'
    key = str(uuid4())
    unrouted = post(idle, key=key)
    assert unrouted.status_code == 409 and unrouted.mimetype == 'application/problem+json'
    assert unrouted.json['detail'] == 'Kein Schreibkräfte-Pool zugeordnet.'
    with app.app_context():
        staff = pools.create_staff_pool('Schreibdienst', actor_id=other)
        pools.link_staff_pool(pool, staff.id, actor_id=other)
        staff_id = staff.id
        staff.active = False
        db.session.commit()
    assert post(idle, key=key).json['code'] == 'NO_STAFF_POOL'
    assert browser[0].get(preflight).json == {'routable': False}
    with app.app_context():
        db.session.get(pools.ClinicStaffPool, staff_id).active = True
        for name, active in (('Ambulanz', True), ('Inaktiver Dienst', False)):
            staff = pools.create_staff_pool(name, actor_id=other)
            pools.link_staff_pool(pool, staff.id, actor_id=other)
            pools.set_pool_active('staff', staff.id, active, actor_id=other)

    service_names = ['Ambulanz', 'Schreibdienst']
    available = browser[0].get(preflight)
    assert available.status_code == 200 and available.json == {
        'routable': True, 'staff_service_names': service_names,
    }
    assert available.headers['Cache-Control'] == 'no-store'
    assert post(silent).json['code'] == 'AUDIO_REQUIRED'
    with patch.object(audit, 'record_in_tx', return_value=None):
        assert post(idle, key=key).status_code == 503
    # Independent clients retry the same send while the first receipt may still be uncommitted.
    clients = [browser, _client(app, author)]
    barrier = Barrier(2)

    def retry(client):
        barrier.wait(timeout=10)
        return post(idle, actor=client, key=key)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, replay = workers.map(retry, clients)
    assert first.status_code == replay.status_code == 200, (first.json, replay.json)
    assert first.json == replay.json == {'dictation_id': idle, 'status': workflow.TO_WRITE,
                                         'version': 1, 'etag': f'"{idle}:1"',
                                         'staff_service_names': service_names}
    repeated = post(idle, version=1)
    assert repeated.status_code == 409 and repeated.json['state']['status'] == workflow.TO_WRITE
    assert post(idle, key=key, version=1).json['code'] == 'IDEMPOTENCY_CONFLICT'
    for target, status in ((asr, workflow.TO_CORRECT), (running, workflow.PREP_ONLINE)):
        detail = browser[0].get(f'/api/clinic/v1/dictations/{target}/detail')
        assert detail.status_code == 200 and detail.json['etag'] == f'"{target}:0"'
        response = post(target)
        assert response.status_code == 200 and response.json['status'] == status, response.json

    worklist = browser[0].get('/api/clinic/v1/worklist/rows')
    assert worklist.status_code == 200
    assert {row['dictation_id']: row['status'] for row in worklist.json['items']} == {
        idle: workflow.TO_WRITE, asr: workflow.TO_CORRECT, running: workflow.PREP_ONLINE, silent: workflow.DRAFT,
    }
    with app.app_context():
        mic_author = _user('mic-author')
        pools.add_author_member(pool, mic_author, actor_id=other)
        recipient_defaults.set_default(mic_author, 'staff_pool', actor_id=mic_author,
                                       expected_version=0, target_ids=[staff_id])
    _exercise_mic_replay(app, mic_author, other)
    with app.app_context():
        labelled = _draft(author, pool)
        db.session.commit()
    admin = _client(app, other)
    created = admin[0].post('/api/clinic/v1/categories', headers=admin[1], json={
        'name': 'Versand', 'categories': ['Ambulant', 'Stationär'], 'required': True})
    assert created.status_code == 201, created.get_data(as_text=True)
    choice = {'set_id': created.json['id'], 'version': created.json['version'], 'value': 'Ambulant'}
    missing = post(labelled)
    assert missing.status_code == 422 and missing.json['code'] == 'missing_category', missing.get_data(as_text=True)
    with app.app_context():
        row = db.session.get(ClinicDictation, labelled)
        assert row.version_counter == 0
        assert db.session.get(workflow.ClinicWorkflowDictation, labelled).status == workflow.DRAFT
        assert not (db.session.get(Recording, row.recording_id).notes or '').strip()
    chosen = post(labelled, body={'categories': [choice]})
    assert chosen.status_code == 200, chosen.get_data(as_text=True)
    with app.app_context():
        row = db.session.get(ClinicDictation, labelled)
        assert 'Ambulant' in (db.session.get(Recording, row.recording_id).notes or '')
    assert browser[0].post('/api/clinic/v1/categories', headers=browser[1],
                           json={'name': 'Fremd', 'categories': ['Notfall'], 'required': True}).status_code == 403
    with app.app_context():
        pools.rename_pool('staff', staff_id, 'Zentraler Schreibdienst', actor_id=other)
    assert browser[0].get(preflight).json == {
        'routable': True, 'staff_service_names': ['Ambulanz', 'Zentraler Schreibdienst'],
    }
    after_rename = post(idle, key=key)
    assert after_rename.status_code == 200 and after_rename.json == first.json

    with app.app_context():
        for target, status in ((idle, workflow.TO_WRITE), (asr, workflow.TO_CORRECT),
                               (running, workflow.PREP_ONLINE)):
            row = db.session.get(ClinicDictation, target)
            assert row.version_counter == 1
            assert db.session.get(workflow.ClinicWorkflowDictation, target).status == status
            event = AuditEvent.query.filter_by(recording_id=row.recording_id, event='recording.edited').one()
            assert event.actor_user_id == author and event.details['fields'] == ['workflow_status', 'workflow_send']
        assert ProcessingJob.query.filter_by(job_type='clinic_notify').count() == 0
        pools.deny(author, 'own', actions=['edit'], actor_id=other)
    assert post(idle, key=key).status_code == 403


if __name__ == '__main__':
    _exercise()
