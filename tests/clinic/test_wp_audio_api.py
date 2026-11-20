"""Focused tests for the audio editing and derived playback endpoints (w093-audio-api)."""

import io
import uuid
import wave
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf

from src.app import app, db
from src.clinic import audio_invalidation, audio_revisions
from src.clinic.audio_render import AudioSource
from src.clinic.audio_revisions import create_segment, current_audio_revision, init_audio_revision
from src.clinic.correction import link_asr_segments
from src.clinic.lease import acquire
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.release import active_release, create_release
from src.clinic.revisions import add_asr_revision
from src.clinic.routes_audio import PATH, create_blueprint
from src.models import Recording, User

BODY = 'Anamnese Husten seit drei Tagen. Befund Lunge frei.'
BASE = PATH.replace('<uuid:dictation_id>', '{}')


def wav(ms, rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setparams((1, 2, rate, rate * ms // 1000, 'NONE', 'not compressed'))
        writer.writeframes(b'\x01\x00' * (rate * ms // 1000))
    return out.getvalue()


def frames(data):
    with wave.open(io.BytesIO(data), 'rb') as reader:
        return reader.getnframes()


def make_user(prefix):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f'{prefix}_{tag}', email=f'{tag}@test.invalid', password='x')
    db.session.add(user)
    db.session.flush()
    return user


def make_dictation(author, helper=None):
    recording = Recording(user_id=author.id, title='audio_api_test', status='PENDING')
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dictation.id, content_hash='o' * 64, size_bytes=1000,
                              storage_key='orig/' + uuid.uuid4().hex, receipt='stored', origin_ref='device:test')
    db.session.add(original)
    db.session.flush()
    return dictation, original


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with app.app_context():
        from src.clinic import live_session as live_tables
        live_tables.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        author, other = make_user('author'), make_user('other')
        dictation, original = make_dictation(author)
        foreign, foreign_original = make_dictation(other)
        asr = add_asr_revision(dictation, original, BODY, 'model:test')
        head = init_audio_revision(dictation, original, 1000)
        init_audio_revision(foreign, foreign_original, 1000)
        lease = acquire(dictation.id, author.id, datetime.now(timezone.utc), duration=3600)
        links = link_asr_segments(dictation, asr, [
            {'text': 'Anamnese', 'start': 0.1, 'end': 0.25},
            {'text': 'Husten seit', 'start': 0.4, 'end': 0.55},
            {'text': 'Lunge frei.', 'start': 0.7, 'end': 0.85},
        ])
        context = ClinicContextRevision(dictation_id=dictation.id, case_ref='CASE-01')
        db.session.add(context)
        db.session.flush()
        release = create_release(dictation, head, asr, context, asr.normalised_text_hash, author.id,
                                 {'allowed': True}, datetime.now(timezone.utc),
                                 document_type='report', release_type='final')
        segment = create_segment(dictation, 'seg/' + uuid.uuid4().hex, 200, 's' * 64)
        foreign_segment = create_segment(foreign, 'seg/' + uuid.uuid4().hex, 200, 'f' * 64)
        db.session.commit()
        data = SimpleNamespace(
            author=author.id, other=other.id, dictation=dictation.id, original=original.id,
            original_hash=original.content_hash, head=head.id, generation=lease.generation,
            links=[link.id for link in links], release=release.id, segment=segment.id,
            foreign_segment=foreign_segment.id, foreign=foreign.id)

    state = SimpleNamespace(fail_sources=False, receipts={})
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-audio-test',
                        SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    db.init_app(flask)
    login = LoginManager(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    def authorize(user, action, dictation):
        return user.id == dictation.author_user_id and action in ('play', 'edit')

    def once(scope, digest, operation):
        if scope in state.receipts:
            old_digest, result = state.receipts[scope]
            if old_digest != digest:
                raise CommandProblem('IDEMPOTENCY_CONFLICT')
            return result
        try:
            result = operation()
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
        state.receipts[scope] = digest, result
        return result

    def load_sources(dictation, edl):
        if state.fail_sources:
            raise audio_revisions.AudioRevisionError('source unavailable')
        sources = {('ORIGINAL', data.original): AudioSource(wav(1000))}
        for entry in edl.entries:
            if entry.source_type == 'SEGMENT':
                sources[('SEGMENT', entry.source_id)] = AudioSource(wav(200))
        return sources

    flask.register_blueprint(create_blueprint(authorize=authorize, run_once=once, load_sources=load_sources))

    @flask.get('/synthetic-login/<int:uid>')
    def browser(uid):
        login_user(db.session.get(User, uid))
        return {'csrf': generate_csrf()}

    return flask, data, state


def session_for(flask, uid):
    client = flask.test_client()
    token = client.get(f'/synthetic-login/{uid}').json['csrf']
    return client, {'X-CSRFToken': token, 'Idempotency-Key': str(uuid.uuid4())}


def edit(client, headers, data, dictation=None, **body):
    body.setdefault('audio_revision_id', data.head)
    body.setdefault('lease_generation', data.generation)
    return client.post(BASE.format(dictation or data.dictation) + '/audio-edits', headers=headers, json=body)


def head_of(data):
    with app.app_context():
        return current_audio_revision(db.session.get(ClinicDictation, data.dictation)).id


def test_authentication_csrf_and_object_policy(scene, monkeypatch):
    flask, data, _ = scene
    current = BASE.format(data.dictation) + '/audio-revisions/current'
    anonymous = flask.test_client()
    assert anonymous.get(current).status_code == 401
    assert anonymous.post(BASE.format(data.dictation) + '/audio-edits', json={}).status_code == 401
    client, headers = session_for(flask, data.author)
    assert client.post(BASE.format(data.dictation) + '/audio-edits', json={}).status_code == 403
    stranger, stranger_headers = session_for(flask, data.other)
    assert stranger.get(current).status_code == 404
    assert edit(stranger, stranger_headers, data, operation='undo').status_code == 404
    assert stranger.get(BASE.format(data.dictation) + '/audio/derived').status_code == 404
    assert head_of(data) == data.head
    monkeypatch.delenv('CLINIC_MODE')
    assert client.get(current).status_code == 404


def test_current_revision_and_derived_audio(scene):
    flask, data, state = scene
    client, _ = session_for(flask, data.author)
    view = client.get(BASE.format(data.dictation) + '/audio-revisions/current')
    assert view.status_code == 200 and view.headers['Cache-Control'] == 'no-store'
    assert view.json == {'revision_id': data.head, 'undo_depth': 0, 'redo_depth': 0,
                         'segments': [{'source_id': data.original, 'start_ms': 0, 'end_ms': 1000}]}
    assert view.headers['ETag'] == f'"{data.head}"'
    url = BASE.format(data.dictation) + '/audio/derived'
    rendered = client.get(url + f'?audio_revision_id={data.head}')
    assert rendered.status_code == 200 and rendered.mimetype == 'audio/wav'
    assert frames(rendered.data) == 16000 and rendered.headers['Cache-Control'] == 'no-store'
    assert client.get(url + '?format=audio/mpeg').status_code == 415
    assert client.get(url + '?format=wav').status_code == 200
    assert client.get(url + f'?audio_revision_id={uuid.uuid4()}').status_code == 409
    assert client.get(url + '?audio_revision_id=not-a-uuid').status_code == 400
    state.fail_sources = True
    assert client.get(url).status_code == 503


def test_cut_invalidates_anchors_supersedes_release_and_keeps_original(scene):
    flask, data, _ = scene
    client, headers = session_for(flask, data.author)
    cut = edit(client, headers, data, operation='cut', start_ms=350, end_ms=600)
    assert cut.status_code == 200
    assert cut.json['previous_revision_id'] == data.head and cut.json['revision_id'] != data.head
    assert cut.json['preserved_link_ids'] == [data.links[0]]
    assert set(cut.json['invalidated_link_ids']) == set(data.links[1:])
    assert cut.json['superseded_release_ids'] == [data.release]
    with app.app_context():
        dictation = db.session.get(ClinicDictation, data.dictation)
        assert current_audio_revision(dictation).id == cut.json['revision_id']
        assert active_release(dictation) is None
        assert db.session.get(ClinicOriginal, data.original).content_hash == data.original_hash
        old = audio_revisions.get_revision_edl(dictation, db.session.get(audio_revisions.ClinicAudioRevision, data.head))
        assert old.total_duration_ms == 1000
        events = audio_invalidation.get_invalidation_events(data.dictation, cut.json['revision_id'])
        assert {event.actor_user_id for event in events} == {data.author}
    rendered = client.get(BASE.format(data.dictation) + '/audio/derived')
    assert frames(rendered.data) == 12000 and rendered.headers['X-Audio-Revision'] == cut.json['revision_id']


def test_trim_insert_overwrite_undo_redo_roundtrip(scene):
    flask, data, _ = scene
    client, headers = session_for(flask, data.author)

    def step(**body):
        response = edit(client, dict(headers, **{'Idempotency-Key': str(uuid.uuid4())}), data,
                        audio_revision_id=head_of(data), **body)
        assert response.status_code == 200, response.json
        return response.json

    def duration():
        return frames(client.get(BASE.format(data.dictation) + '/audio/derived').data) * 1000 // 16000

    step(operation='trim', position_ms=800)
    assert duration() == 800
    step(operation='insert', position_ms=100, source_segment_id=data.segment, duration_ms=100)
    assert duration() == 900
    step(operation='overwrite', position_ms=0, source_segment_id=data.segment, duration_ms=100, source_start_ms=100)
    assert duration() == 900
    step(operation='undo')
    assert duration() == 900
    step(operation='undo')
    assert duration() == 800
    step(operation='redo')
    assert duration() == 900
    with app.app_context():
        # every older head stays readable
        assert len(audio_revisions.history(db.session.get(ClinicDictation, data.dictation))) == 4


def test_stale_lease_forged_and_foreign_inputs_change_nothing(scene):
    flask, data, _ = scene
    client, headers = session_for(flask, data.author)
    stale = edit(client, headers, data, operation='trim', position_ms=500, audio_revision_id=str(uuid.uuid4()))
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    assert edit(client, headers, data, operation='trim', position_ms=500, lease_generation=data.generation + 5).status_code in (409, 423)
    forged = edit(client, headers, data, operation='trim', position_ms=500, actor=data.other, role='admin')
    assert forged.status_code == 400
    assert edit(client, headers, data, operation='cut', start_ms=10).status_code == 400
    assert edit(client, headers, data, operation='split', position_ms=10).status_code == 400
    assert edit(client, headers, data, operation='trim', position_ms=-1).status_code == 400
    assert edit(client, headers, data, operation='trim', position_ms=5000).status_code == 400
    assert edit(client, headers, data, operation='undo').status_code == 409  # nothing to undo
    foreign_segment = edit(client, headers, data, operation='insert', position_ms=0, source_segment_id=data.foreign_segment)
    assert foreign_segment.status_code == 404
    missing_key = client.post(BASE.format(data.dictation) + '/audio-edits', headers={'X-CSRFToken': headers['X-CSRFToken']},
                              json={'operation': 'undo', 'audio_revision_id': data.head, 'lease_generation': data.generation})
    assert missing_key.status_code == 400
    assert head_of(data) == data.head
    with app.app_context():
        assert active_release(db.session.get(ClinicDictation, data.dictation)) is not None


def test_retry_replays_receipt_and_changed_payload_conflicts(scene):
    flask, data, _ = scene
    client, headers = session_for(flask, data.author)
    first = edit(client, headers, data, operation='trim', position_ms=700)
    assert first.status_code == 200
    again = edit(client, headers, data, operation='trim', position_ms=700)
    assert again.status_code == 200 and again.json == first.json
    changed = edit(client, headers, data, operation='trim', position_ms=600)
    assert (changed.status_code, changed.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    assert head_of(data) == first.json['revision_id']
    with app.app_context():
        assert len(audio_revisions.history(db.session.get(ClinicDictation, data.dictation))) == 2


def test_live_holder_blocks_edit(scene):
    from src.clinic import live_session
    flask, data, _ = scene
    with app.app_context():
        started = live_session.start(data.dictation, data.other, datetime.now(timezone.utc))
        db.session.commit()
        assert started is not None
    client, headers = session_for(flask, data.author)
    blocked = edit(client, headers, data, operation='trim', position_ms=500)
    assert (blocked.status_code, blocked.json['code']) == (423, 'LOCKED')
    assert head_of(data) == data.head
