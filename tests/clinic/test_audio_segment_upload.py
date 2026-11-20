"""Real decoder, durable receipts, stored sources and edit/ASR rendering round trips."""

import io
from hashlib import sha256
import subprocess
import uuid
import wave
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf

from src.app import app, db
from src.clinic import audio_revisions, audio_segments, lease, policy, pool_policy, workflow
from src.clinic.app_wiring import make_source_loader
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.parity_receipts import ClinicParityReceipt, run_once
from src.clinic.routes_audio import create_blueprint
from src.models import Recording, User


def wav(ms, sample=123):
    out = io.BytesIO()
    with wave.open(out, 'wb') as handle:
        handle.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        handle.writeframes(sample.to_bytes(2, 'little', signed=True) * (ms * 16))
    return out.getvalue()


def pcm(data):
    with wave.open(io.BytesIO(data), 'rb') as handle:
        return handle.readframes(handle.getnframes())


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    original_bytes = wav(1000)
    (tmp_path / 'original.wav').write_bytes(original_bytes)
    with app.app_context():
        users = [User(username=uuid.uuid4().hex, email=f'{uuid.uuid4()}@test.invalid', password='x')
                 for _ in range(2)]
        db.session.add_all(users)
        db.session.flush()
        recording = Recording(user_id=users[0].id, title='Segment test', status='COMPLETED')
        db.session.add(recording)
        db.session.flush()
        item = ClinicDictation(recording_id=recording.id, author_user_id=users[0].id)
        db.session.add(item)
        db.session.flush()
        original = ClinicOriginal(dictation_id=item.id, content_hash=sha256(original_bytes).hexdigest(),
                                  size_bytes=len(original_bytes), storage_key='original.wav',
                                  receipt='stored', origin_ref='synthetic')
        db.session.add(original)
        db.session.flush()
        head = audio_revisions.init_audio_revision(item, original, 1000)
        active = lease.acquire(item.id, users[0].id, datetime.now(timezone.utc), duration=3600)
        db.session.add(workflow.ClinicWorkflowDictation(id=item.id, author_user_id=users[0].id, pool_id='synthetic',
                                                       status=workflow.DRAFT))
        db.session.commit()
        data = SimpleNamespace(id=item.id, head=head.id, generation=active.generation,
                               owner=users[0].id, other=users[1].id, root=tmp_path)
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-segment-test', UPLOAD_FOLDER=str(tmp_path),
                        SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    db.init_app(flask)
    login = LoginManager(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    flask.register_blueprint(create_blueprint(
        authorize=lambda user, action, item: pool_policy.decide(policy.build_subject(user.id), action, item).allowed,
        run_once=run_once, load_sources=make_source_loader(tmp_path)))

    @flask.get('/login/<int:uid>')
    def signin(uid):
        login_user(db.session.get(User, uid))
        return {'csrf': generate_csrf()}

    data.flask = flask
    data.url = f'/api/clinic/v1/dictations/{data.id}'
    return data


def browser(scene, uid=None):
    client = scene.flask.test_client()
    token = client.get(f'/login/{uid or scene.owner}').json['csrf']
    return client, {'X-CSRFToken': token, 'Idempotency-Key': str(uuid.uuid4())}


def upload(scene, client, headers, payload=None, **fields):
    body = {'audio_revision_id': scene.head, 'lease_generation': str(scene.generation),
            'file': (io.BytesIO(wav(200, 456) if payload is None else payload), '../../capture.webm')}
    body.update(fields)
    return client.post(scene.url + '/audio-segments', data=body, headers=headers)


def test_upload_insert_overwrite_fresh_session_undo_redo_and_asr(scene):
    client, headers = browser(scene)
    first = upload(scene, client, headers)
    assert first.status_code == 201, first.json
    assert first.json['duration_ms'] == 200
    with scene.flask.app_context():
        segment = db.session.get(audio_revisions.ClinicAudioSegment, first.json['segment_id'])
        stored = scene.root / segment.storage_key
        assert stored.is_file() and stored.stat().st_mode & 0o222 == 0
        assert sha256(stored.read_bytes()).hexdigest() == segment.content_hash
        assert ClinicParityReceipt.query.count() == 1

    def step(operation, **extra):
        nonlocal client, headers
        client, headers = browser(scene)  # new session, no in-memory history or receipt
        head = client.get(scene.url + '/audio-revisions/current').json
        response = client.post(scene.url + '/audio-edits', headers=headers, json={
            'operation': operation, 'audio_revision_id': head['revision_id'],
            'lease_generation': scene.generation, **extra})
        assert response.status_code == 200, response.json
        return client.get(scene.url + '/audio-revisions/current').json

    inserted = step('insert', position_ms=500, source_segment_id=first.json['segment_id'])
    assert (inserted['undo_depth'], inserted['redo_depth']) == (1, 0)
    expected = pcm(wav(500)) + pcm(wav(200, 456)) + pcm(wav(500))
    assert pcm(client.get(scene.url + '/audio/derived').data) == expected
    second = upload(scene, client, headers, audio_revision_id=inserted['revision_id'])
    assert second.status_code == 201, second.json
    overwritten = step('overwrite', position_ms=0, source_segment_id=second.json['segment_id'])
    expected = pcm(wav(200, 456)) + expected[6400:]
    response = client.get(scene.url + '/audio/derived')
    assert response.status_code == 200, response.json
    rendered = response.data
    assert pcm(rendered) == expected
    with scene.flask.app_context():
        from src.clinic.asr_input import _audio_bytes
        item = db.session.get(ClinicDictation, scene.id)
        assert _audio_bytes(item, {'audio_revision_id': overwritten['revision_id']})[0] == rendered
    undone = step('undo')
    assert (undone['undo_depth'], undone['redo_depth']) == (1, 1)
    redone = step('redo')
    assert (redone['undo_depth'], redone['redo_depth']) == (2, 0)
    assert pcm(client.get(scene.url + '/audio/derived').data) == expected
    # A proven replay remains a receipt even after the head has moved.
    again_client, again_headers = browser(scene)
    again_headers['Idempotency-Key'] = first.request.headers['Idempotency-Key']
    assert upload(scene, again_client, again_headers).json == first.json
    conflict = upload(scene, again_client, again_headers, wav(200, 789))
    assert (conflict.status_code, conflict.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    assert (scene.root / 'original.wav').read_bytes() == wav(1000)


def test_real_webm_opus_decoder(scene, tmp_path):
    source, target = tmp_path / 'tone.wav', tmp_path / 'tone.webm'
    source.write_bytes(wav(200, 456))
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-c:a', 'libopus', str(target)],
                   check=True, capture_output=True, timeout=20)
    client, headers = browser(scene)
    response = upload(scene, client, headers, target.read_bytes())
    assert response.status_code == 201, response.json
    assert response.json['duration_ms'] == 200


@pytest.mark.parametrize('case,status', [
    ('foreign', 404), ('csrf', 403), ('anonymous', 401), ('generation', 409), ('revision', 409),
    ('expired', 409), ('holder', 423), ('closed', 409), ('garbage', 415), ('empty', 415),
    ('forged', 400), ('oversize', 413), ('live', 423),
])
def test_refusal_leaves_no_segment_file_or_receipt(scene, monkeypatch, case, status):
    client, headers = browser(scene, scene.other if case == 'foreign' else None)
    fields, payload = {}, None
    if case == 'csrf':
        headers.pop('X-CSRFToken')
    if case == 'anonymous':
        client = scene.flask.test_client()
    if case == 'generation':
        fields['lease_generation'] = str(scene.generation + 1)
    if case == 'revision':
        fields['audio_revision_id'] = str(uuid.uuid4())
    if case in ('garbage', 'empty'):
        payload = b'garbage' if case == 'garbage' else b''
    if case == 'forged':
        fields['storage_key'] = '/tmp/forged.wav'
    if case == 'oversize':
        monkeypatch.setattr(audio_segments, 'MAX_INPUT_BYTES', 100)
    with scene.flask.app_context():
        if case in ('expired', 'holder'):
            active = lease.ClinicReviewLease.query.filter_by(dictation_id=scene.id).one()
            if case == 'expired':
                active.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            else:
                active.holder_user_id = scene.other
        if case == 'closed':
            db.session.get(workflow.ClinicWorkflowDictation, scene.id).status = workflow.DONE
        if case == 'live':
            from src.clinic.live_session import start
            start(scene.id, scene.other, datetime.now(timezone.utc))
        db.session.commit()
    response = upload(scene, client, headers, payload, **fields)
    assert response.status_code == status, response.json
    with scene.flask.app_context():
        assert audio_revisions.ClinicAudioSegment.query.count() == 0
        assert ClinicParityReceipt.query.count() == 0
    assert list(scene.root.rglob('*.wav')) == [scene.root / 'original.wav']
    assert not list(scene.root.glob('.clinic-segment-*'))


def test_source_checksum_rejects_tampered_segment(scene):
    client, headers = browser(scene)
    response = upload(scene, client, headers)
    assert response.status_code == 201
    with scene.flask.app_context():
        item = db.session.get(ClinicDictation, scene.id)
        segment = db.session.get(audio_revisions.ClinicAudioSegment, response.json['segment_id'])
        revision = audio_revisions.apply_insert(item, 0, segment)
        db.session.commit()
        path = scene.root / segment.storage_key
        path.chmod(0o600)
        path.write_bytes(wav(200, 789))
        with pytest.raises(audio_revisions.AudioRevisionError, match='hash'):
            make_source_loader(scene.root)(item, audio_revisions.get_revision_edl(item, revision))


def test_concurrent_uploads_share_one_durable_receipt(scene):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    key = str(uuid.uuid4())

    def send():
        client, headers = browser(scene)
        headers['Idempotency-Key'] = key
        barrier.wait(timeout=10)
        response = upload(scene, client, headers)
        return response.status_code, response.json

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: send(), range(2)))
    assert results[0] == results[1]
    assert results[0][0] == 201, results
    with scene.flask.app_context():
        assert audio_revisions.ClinicAudioSegment.query.count() == 1
        assert ClinicParityReceipt.query.count() == 1
    assert len(list(scene.root.glob('audio-segments/**/*.wav'))) == 1


def test_foreign_draft_with_edit_grant_and_lease_is_still_protected(scene):
    client, headers = browser(scene, scene.other)
    policy.register_grant_provider(lambda uid: [policy.Grant('all', frozenset({'edit'}))]
                                   if uid == scene.other else [])
    try:
        with scene.flask.app_context():
            active = lease.ClinicReviewLease.query.filter_by(dictation_id=scene.id).one()
            active.holder_user_id = scene.other
            db.session.commit()
        assert upload(scene, client, headers).status_code == 403
        with scene.flask.app_context():
            assert audio_revisions.ClinicAudioSegment.query.count() == 0
    finally:
        policy.clear_providers()


def test_failed_commit_removes_only_its_uncommitted_segment(scene):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    client, headers = browser(scene)

    def reject_commit(session):
        if any(isinstance(row, ClinicParityReceipt) for row in session.new):
            raise RuntimeError('synthetic commit failure')

    event.listen(Session, 'before_commit', reject_commit)
    try:
        with pytest.raises(RuntimeError, match='synthetic commit failure'):
            upload(scene, client, headers)
    finally:
        event.remove(Session, 'before_commit', reject_commit)
    with scene.flask.app_context():
        assert audio_revisions.ClinicAudioSegment.query.count() == 0
        assert ClinicParityReceipt.query.count() == 0
    assert list(scene.root.rglob('*.wav')) == [scene.root / 'original.wav']
    assert upload(scene, client, headers).status_code == 201


def test_source_bounds_refused_before_revision_persistence(scene):
    client, headers = browser(scene)
    segment = upload(scene, client, headers).json
    response = client.post(scene.url + '/audio-edits', headers=headers, json={
        'operation': 'insert', 'position_ms': 0, 'source_segment_id': segment['segment_id'],
        'duration_ms': 201, 'audio_revision_id': scene.head, 'lease_generation': scene.generation})
    assert response.status_code == 400
    with scene.flask.app_context():
        item = db.session.get(ClinicDictation, scene.id)
        assert audio_revisions.current_audio_revision(item).id == scene.head
        assert audio_revisions.undo_depth(item) == 0
