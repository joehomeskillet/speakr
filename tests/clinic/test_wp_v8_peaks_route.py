"""Waveform peaks route of the correction view (v8-42-peaks-route).

GET /api/clinic/v1/dictations/<uuid>/audio-revisions/<rev>/peaks: same object
policy as the correction view, peaks of exactly the requested revision, 404 for
foreign or unknown objects, cache-safe, no transcript text. Synthetic audio only.
"""

import io
import math
import struct
import uuid
import wave
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from src.app import app, db
from src.clinic import audio_revisions
from src.clinic.audio_render import AudioSource
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.lease import acquire
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision
from src.clinic.routes_audio import PATH, create_blueprint
from src.models import Recording, User
from src.services.waveform_peaks import decode_peaks

BASE = PATH.replace('<uuid:dictation_id>', '{}')
BODY = 'Anamnese Husten seit drei Tagen.'
ACTIONS = []


def tone(ms, rate=16000):
    out = io.BytesIO()
    count = rate * ms // 1000
    with wave.open(out, 'wb') as writer:
        writer.setparams((1, 2, rate, count, 'NONE', 'not compressed'))
        writer.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(n / 20))) for n in range(count)))
    return out.getvalue()


def make_user(prefix):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f'{prefix}_{tag}', email=f'{tag}@test.invalid', password='x')
    db.session.add(user)
    db.session.flush()
    return user


def make_dictation(author):
    recording = Recording(user_id=author.id, title='peaks_test', status='PENDING')
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
    ACTIONS.clear()
    with app.app_context():
        from src.clinic import live_session as live_tables
        live_tables.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        author, other = make_user('author'), make_user('other')
        dictation, original = make_dictation(author)
        foreign, foreign_original = make_dictation(other)
        add_asr_revision(dictation, original, BODY, 'model:test')
        head = init_audio_revision(dictation, original, 1000)
        foreign_head = init_audio_revision(foreign, foreign_original, 1000)
        lease = acquire(dictation.id, author.id, datetime.now(timezone.utc), duration=3600)
        db.session.commit()
        data = SimpleNamespace(author=author.id, other=other.id, dictation=dictation.id, original=original.id,
                               head=head.id, generation=lease.generation, foreign=foreign.id,
                               foreign_head=foreign_head.id)

    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-peaks-test',
                        SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    db.init_app(flask)
    login = LoginManager(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    state = SimpleNamespace(fail_sources=False)

    def authorize(user, action, dictation):
        ACTIONS.append(action)
        return user.id == dictation.author_user_id and action in ('correction_read', 'play')

    def load_sources(dictation, edl):
        if state.fail_sources:
            raise audio_revisions.AudioRevisionError('source unavailable')
        return {('ORIGINAL', dictation_original(dictation)): AudioSource(tone(1000))}

    def dictation_original(dictation):
        return db.session.query(ClinicOriginal).filter_by(dictation_id=dictation.id).one().id

    flask.register_blueprint(create_blueprint(authorize=authorize, run_once=lambda *a: None,
                                              load_sources=load_sources))

    @flask.get('/synthetic-login/<int:uid>')
    def browser(uid):
        login_user(db.session.get(User, uid))
        return {}

    return flask, data, state


def client_for(flask, uid):
    client = flask.test_client()
    client.get(f'/synthetic-login/{uid}')
    return client


def url(dictation, revision):
    return BASE.format(dictation) + f'/audio-revisions/{revision}/peaks'


def test_author_gets_peaks_of_the_requested_revision(scene):
    flask, data, _ = scene
    response = client_for(flask, data.author).get(url(data.dictation, data.head))
    assert response.status_code == 200
    assert response.mimetype == 'application/octet-stream'
    assert ACTIONS == ['correction_read']
    decoded = decode_peaks(response.data)
    assert decoded['duration'] == pytest.approx(1.0, abs=0.05)
    assert max(decoded['levels'][0]['max']) > 0
    assert response.headers['ETag'] == f'"{data.head}.peaks"'
    assert response.headers['Cache-Control'] == 'private, no-cache'
    assert response.headers['X-Audio-Revision'] == data.head
    assert BODY.encode() not in response.data


def test_peaks_follow_an_edited_revision_not_the_head_alone(scene):
    flask, data, _ = scene
    with app.app_context():
        dictation = db.session.get(ClinicDictation, data.dictation)
        edited = audio_revisions.apply_cut(dictation, 200, 700)
        db.session.commit()
        edited_id = edited.id
    client = client_for(flask, data.author)
    old = decode_peaks(client.get(url(data.dictation, data.head)).data)
    new = client.get(url(data.dictation, edited_id))
    assert new.status_code == 200 and new.headers['X-Audio-Revision'] == edited_id
    assert old['duration'] == pytest.approx(1.0, abs=0.05)
    assert decode_peaks(new.data)['duration'] == pytest.approx(0.5, abs=0.05)


def test_foreign_unknown_and_wrong_revision_are_404_without_leak(scene):
    flask, data, _ = scene
    stranger = client_for(flask, data.other)
    missing = stranger.get(url(data.dictation, data.head))
    unknown = stranger.get(url(uuid.uuid4(), data.head))
    assert missing.status_code == unknown.status_code == 404
    assert missing.json['code'] == unknown.json['code']
    author = client_for(flask, data.author)
    assert author.get(url(data.dictation, uuid.uuid4())).status_code == 404
    # a revision of another dictation is not addressable through this one
    assert author.get(url(data.dictation, data.foreign_head)).status_code == 404
    assert author.get(url(data.foreign, data.foreign_head)).status_code == 404
    assert flask.test_client().get(url(data.dictation, data.head)).status_code == 401


def test_clinic_mode_off_hides_the_route(scene, monkeypatch):
    flask, data, _ = scene
    client = client_for(flask, data.author)
    monkeypatch.delenv('CLINIC_MODE')
    assert client.get(url(data.dictation, data.head)).status_code == 404


def test_conditional_request_is_answered_without_rendering(scene):
    flask, data, state = scene
    client = client_for(flask, data.author)
    etag = client.get(url(data.dictation, data.head)).headers['ETag']
    state.fail_sources = True  # a render would now fail with 503
    again = client.get(url(data.dictation, data.head), headers={'If-None-Match': etag})
    assert again.status_code == 304 and again.headers['ETag'] == etag
    assert client.get(url(data.dictation, data.head)).status_code == 503
    # the revalidation shortcut stays behind the policy
    stranger = client_for(flask, data.other)
    assert stranger.get(url(data.dictation, data.head), headers={'If-None-Match': etag}).status_code == 404


def test_missing_audio_source_is_unavailable_not_faked(scene):
    flask, data, state = scene
    state.fail_sources = True
    response = client_for(flask, data.author).get(url(data.dictation, data.head))
    assert response.status_code == 503 and response.json['code'] == 'DEPENDENCY_UNAVAILABLE'
    assert response.headers['Cache-Control'] == 'no-store'


def test_upstream_route_and_policy_classification(scene):
    from src.clinic import route_policy
    table = route_policy._build()  # CLINIC_MODE is on in this scene
    assert table['clinic_audio.peaks'].kind == 'auth'
    assert table['recordings.get_audio_peaks'].kind == 'recording'
    rules = {r.rule for r in app.url_map.iter_rules() if r.endpoint == 'recordings.get_audio_peaks'}
    assert rules == {'/audio/<int:recording_id>/peaks'}
