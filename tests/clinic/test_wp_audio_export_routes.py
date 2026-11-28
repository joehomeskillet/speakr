from hashlib import sha256

import pytest
from flask import Flask
from flask_login import LoginManager

from src.app import app as real_app
from src.app import register_clinic_audio, register_clinic_resources
from src.clinic import config, pools
from src.clinic.ingest import storage_key_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models import AuditEvent, User
from src.services import audit

AUDIO = b'RIFF\x24\x00\x00\x00WAVE synthetic original audio'
URL = '/api/clinic/v1/dictations/{}/audio/original'


@pytest.fixture
def scene(world, tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    defaults = {'web_client': {'default_profile': 'managed_web_online'}}
    monkeypatch.setattr(config, 'dev_defaults', lambda: defaults)
    path = tmp_path / storage_key_for('audio-export-test')
    path.parent.mkdir(parents=True)
    path.write_bytes(AUDIO)
    document_path = tmp_path / 'clinic/documents/v1/test'
    document_path.parent.mkdir(parents=True)
    document_path.write_bytes(b'Synthetic text, not audio')
    with real_app.app_context():
        dictation = ClinicDictation(recording_id=world['rec_a'], author_user_id=world['user_a'])
        db.session.add(dictation)
        db.session.flush()
        db.session.add(ClinicOriginal(
            dictation_id=dictation.id, content_hash=sha256(AUDIO).hexdigest(), size_bytes=len(AUDIO),
            storage_key=path.relative_to(tmp_path).as_posix(), receipt='stored', origin_ref='test'))
        document = ClinicDictation(recording_id=world['rec_b'], author_user_id=world['user_b'])
        db.session.add(document)
        db.session.flush()
        document_bytes = document_path.read_bytes()
        db.session.add(ClinicOriginal(
            dictation_id=document.id, content_hash=sha256(document_bytes).hexdigest(),
            size_bytes=len(document_bytes), storage_key=document_path.relative_to(tmp_path).as_posix(),
            receipt='stored', origin_ref='document:txt:test'))
        db.session.commit()
        url, document_url = URL.format(dictation.id), URL.format(document.id)

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-audio-export',
                      SQLALCHEMY_DATABASE_URI=real_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(app)
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    register_clinic_resources(app)
    register_clinic_audio(app)

    def client(user_id=None):
        http = app.test_client()
        if user_id is not None:
            with http.session_transaction() as session:
                session['_user_id'] = str(user_id)
        return http

    return client, url, document_url, path, defaults


def grant(world, user, actions, scope='own'):
    with real_app.app_context():
        return pools.allow(world[user], scope, actor_id=world['user_admin'], actions=actions).id


def reads(world):
    audit.flush()
    with real_app.app_context():
        return [row.details for row in AuditEvent.query.filter_by(
            event='recording.content.read', actor_user_id=world['user_a'], recording_id=world['rec_a'])]


def test_explicit_grant_downloads_exact_original_until_revoked(scene, world):
    client, url, _, _, _ = scene
    http = client(world['user_a'])
    grant(world, 'user_a', ['view', 'play', 'export'])
    assert http.get(url).status_code == 404
    assert reads(world) == []

    grant_id = grant(world, 'user_a', ['audio_export'])
    with http.get(url) as response:
        assert response.status_code == 200
        assert response.data == AUDIO
        assert response.headers['X-Content-SHA256'] == sha256(response.data).hexdigest()
        assert response.mimetype == 'audio/wav'
        assert response.headers['Content-Disposition'].startswith('attachment; filename=')
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert reads(world) == [{'part': 'original_audio'}]

    with real_app.app_context():
        pools.revoke(grant_id, actor_id=world['user_admin'])
    assert http.get(url).status_code == 404
    assert reads(world) == [{'part': 'original_audio'}]


def test_export_fails_closed_for_wrong_profile_or_source(scene, world):
    client, url, document_url, path, defaults = scene
    grant(world, 'user_a', ['audio_export'])
    grant(world, 'user_b', ['audio_export'])
    http = client(world['user_a'])
    assert client().get(url).status_code == 401
    assert client(world['user_admin']).get(url).status_code == 404
    assert client(world['user_b']).get(url).status_code == 404
    assert client(world['user_b']).get(document_url).status_code == 404
    assert http.get(url + '?format=eml').status_code == 400
    defaults['web_client']['default_profile'] = 'byod_internal'
    assert http.get(url, headers={'X-Clinic-Channel': 'managed_web_online'}).status_code == 403
    defaults['web_client']['default_profile'] = 'managed_web_online'

    path.write_bytes(AUDIO[:-1] + b'!')
    mismatch = http.get(url)
    assert mismatch.status_code == 409
    assert 'X-Content-SHA256' not in mismatch.headers
    assert 'Content-Disposition' not in mismatch.headers
    path.unlink()
    assert http.get(url).status_code == 409
    assert reads(world) == []
