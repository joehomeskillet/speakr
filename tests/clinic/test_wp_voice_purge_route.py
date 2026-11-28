"""Exercise the mounted purge route with synthetic voice data and real audit writes."""

import struct

import pytest
from flask import Flask, session
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import load_user, register_clinic_voice_consent
from src.clinic import voice_consent as consent
from src.clinic.guard import clinic_guard
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.route_policy import _build
from src.database import db
from src.models import AuditEvent, Recording, Speaker, SpeakerSnippet, SpeakerVoiceSample, User


URL = '/api/clinic/v1/admin/voice-purge'
VOICE = struct.pack('<f', 1)


@pytest.fixture
def scene(clinic_on, monkeypatch, tmp_path):
    local = Flask(__name__)
    local.config.update(TESTING=True, SECRET_KEY='voice-purge-test',
                        SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "purge.db"}',
                        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'timeout': 0.1}})
    db.init_app(local)
    LoginManager(local).user_loader(load_user)
    monkeypatch.setattr('src.clinic.guard.ROUTE_POLICY', _build())
    local.before_request(clinic_guard)
    CSRFProtect(local)
    register_clinic_voice_consent(local)

    audio = tmp_path / 'dictation.wav'
    audio.write_bytes(b'synthetic dictation audio')
    with local.app_context():
        db.create_all()
        admin = User(username='voice-purge-admin', email='purge-admin@example.invalid', is_admin=True)
        author = User(username='voice-purge-author', email='purge-author@example.invalid')
        db.session.add_all([admin, author])
        db.session.flush()
        db.session.add_all(ClinicAccountPolicy(user_id=user.id, status='active') for user in (admin, author))
        recording = Recording(user_id=author.id, audio_path=str(audio), speaker_embeddings={'SPEAKER_01': [1]})
        speaker = Speaker(user_id=author.id, name='Synthetic speaker')
        db.session.add_all([recording, speaker])
        db.session.flush()
        db.session.add_all([
            SpeakerSnippet(speaker_id=speaker.id, recording_id=recording.id, segment_index=0,
                           text_snippet='Synthetic quote'),
            SpeakerVoiceSample(user_id=author.id, speaker_id=speaker.id, embedding=VOICE, dimension=1),
        ])
        db.session.commit()
        consent.grant_consent(actor_user_id=author.id, user_id=author.id, statement_text='Synthetic opt-in')
        consent.enrol(author.id, consent.EmbeddingRef(VOICE, 1), actor_user_id=author.id)
        return local, admin.id, author.id, recording.id, audio


def _client(local, user_id):
    client = local.test_client()
    with local.test_request_context():
        token = generate_csrf()
        csrf_secret = session['csrf_token']
    with client.session_transaction() as browser:
        browser['_user_id'] = str(user_id)
        browser['csrf_token'] = csrf_secret
    return client, {'X-CSRF-Token': token}


def test_author_and_disabled_admin_get_403(scene):
    local, admin, author, _, _ = scene
    client, headers = _client(local, author)
    assert client.post(URL, json={}, headers=headers).status_code == 403

    with local.app_context():
        db.session.get(ClinicAccountPolicy, admin).status = 'disabled'
        db.session.commit()
    client, headers = _client(local, admin)
    assert client.post(URL, json={}, headers=headers).status_code == 403


def test_preview_then_guarded_purge_preserves_consent_and_dictation_audio(scene):
    local, admin, author, recording_id, audio = scene
    client, headers = _client(local, admin)
    with local.app_context():
        history = consent.consent_history(author)

    preview = client.post(URL, json={}, headers=headers)
    assert preview.status_code == 200
    version = preview.json['version']
    assert preview.json['dry_run'] is True and len(version) == 64
    assert preview.json['counts']['speaker'] == 1
    assert preview.json['counts']['speaker_voice_sample'] == 1
    assert preview.json['counts']['speaker_snippet'] == 1

    assert client.post(URL, json={'expected_version': version}).status_code == 403
    assert client.post(URL, json={'expected_version': 1}, headers=headers).status_code == 422
    assert client.post(URL, json={'expected_version': version, 'sample_store': str(audio.parent)},
                       headers=headers).status_code == 422
    assert client.post(URL, json={'expected_version': '0' * 64}, headers=headers).status_code == 409
    with local.app_context():
        assert Speaker.query.count() == SpeakerSnippet.query.count() == SpeakerVoiceSample.query.count() == 1
        assert db.session.get(Recording, recording_id).speaker_embeddings == {'SPEAKER_01': [1]}
        assert AuditEvent.query.filter_by(event='admin.upstream_voice.purge').count() == 0
        assert audio.read_bytes() == b'synthetic dictation audio'

    result = client.post(URL, json={'expected_version': version}, headers=headers)
    assert result.status_code == 200 and result.json['dry_run'] is False
    assert client.post(URL, json={'expected_version': version}, headers=headers).status_code == 409
    with local.app_context():
        assert Speaker.query.count() == SpeakerSnippet.query.count() == SpeakerVoiceSample.query.count() == 0
        recording = db.session.get(Recording, recording_id)
        assert recording.audio_path == str(audio) and recording.speaker_embeddings is None
        assert audio.read_bytes() == b'synthetic dictation audio'
        assert consent.consent_history(author) == history
        assert consent.ClinicVoiceEmbedding.query.filter_by(user_id=author).one().embedding == VOICE
        event = AuditEvent.query.filter_by(event='admin.upstream_voice.purge').one()
        assert (event.actor_user_id, event.outcome, event.details['phase']) == (admin, 'success', 'complete')


def test_apply_conflicts_with_a_concurrent_database_writer(scene):
    local, admin, _, _, _ = scene
    client, headers = _client(local, admin)
    preview = client.post(URL, headers=headers)
    assert preview.status_code == 200
    body = {'expected_version': preview.json['version']}

    with local.app_context():
        engine = db.engine
    with engine.begin() as writer:
        writer.execute(Speaker.__table__.update().values(name='Changed speaker'))
        assert client.post(URL, json=body, headers=headers).status_code == 503

    assert client.post(URL, json=body, headers=headers).status_code == 409
    with local.app_context():
        assert Speaker.query.one().name == 'Changed speaker'
        assert AuditEvent.query.filter_by(event='admin.upstream_voice.purge').count() == 0
