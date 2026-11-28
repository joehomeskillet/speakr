"""Negotiated upload MIME must match the container bytes (PAR-ASR-007).

WAV and Opus samples are stored as uploaded and playable. A file whose bytes
are not the declared format is refused; the original is not renamed into a
recording. Clinic mode only: with it off the declaration is ignored.
"""
import io
import shutil
import subprocess
import uuid
import wave
from unittest.mock import patch

import pytest

from src.app import app, db
from src.clinic import dictation_upload
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.models import ProcessingJob, Recording, User
from src.services.storage import get_storage_service

needs_ffmpeg = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg missing')


def _encode(tmp_path, extension, encoder):
    media = tmp_path / f'tone.{extension}'
    result = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'sine=frequency=440:sample_rate=48000:duration=0.25',
         '-c:a', encoder, str(media)],
        capture_output=True, text=True, timeout=30)
    if result.returncode and f"Unknown encoder '{encoder}'" in result.stderr:
        pytest.skip(f'system ffmpeg lacks the {encoder} encoder')
    assert result.returncode == 0, result.stderr
    return media.read_bytes()


def _wav_pcm(frames=4000):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x10' * frames)
    return buf.getvalue()


@pytest.fixture
def originals(tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    return tmp_path


@pytest.fixture
def uid(originals):
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        user = User(username=f'ac_{suffix}', email=f'ac_{suffix}@local.test', password='x')
        db.session.add(user)
        db.session.flush()
        pool = ClinicAuthorPool(name=f'ac pool {suffix}', created_by=user.id)
        db.session.add(pool)
        db.session.flush()
        db.session.add(ClinicAuthorPoolMember(pool_id=pool.id, user_id=user.id, created_by=user.id))
        db.session.commit()
        yield user.id
        db.session.rollback()
        storage = get_storage_service()
        for rec in Recording.query.filter_by(user_id=user.id).all():
            try:
                storage.delete(rec.audio_path, missing_ok=True)
            except OSError:
                pass
        ClinicAuthorPoolMember.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        ClinicAuthorPool.query.filter_by(created_by=user.id).delete(synchronize_session=False)
        ProcessingJob.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        Recording.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        db.session.commit()


def _post(uid, content, filename, **fields):
    from flask import g, has_app_context
    if has_app_context():
        g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    with patch('src.services.recording_ingestion.get_duration', return_value=0.25), \
         patch('src.services.recording_ingestion.get_creation_date', return_value=None), \
         patch('src.services.webhook_dispatch.emit_webhook_event'):
        return client.post(
            '/upload',
            data={'file': (io.BytesIO(content), filename), **fields},
            content_type='multipart/form-data')


def _stored(uid, recording_id):
    with app.app_context():
        rec = db.session.get(Recording, recording_id)
        assert rec is not None and rec.user_id == uid
        with get_storage_service().materialize(rec.audio_path) as stored:
            return rec.mime_type, open(stored.local_path, 'rb').read()


def test_wav_declaration_matches_and_is_playable(clinic_on, uid, originals):
    payload = _wav_pcm()
    response = _post(uid, payload, 'note.wav', audio_mime='audio/wav')
    assert response.status_code == 202, response.get_data(as_text=True)
    mime, stored = _stored(uid, response.get_json()['id'])
    assert mime == 'audio/wav'
    assert stored == payload
    with wave.open(io.BytesIO(stored), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 4000


@needs_ffmpeg
def test_opus_declaration_matches_container_and_decodes(clinic_on, uid, originals, tmp_path):
    payload = _encode(tmp_path, 'ogg', 'libopus')
    response = _post(uid, payload, 'note.ogg', audio_mime='audio/ogg;codecs=opus')
    assert response.status_code == 202, response.get_data(as_text=True)
    mime, stored = _stored(uid, response.get_json()['id'])
    assert stored == payload
    assert mime.startswith('audio/')
    assert stored[:4] == b'OggS'
    assert b'OpusHead' in stored[:64]
    decoded = tmp_path / 'played.wav'
    result = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', '-', '-f', 'wav', str(decoded)],
        input=stored, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    with wave.open(str(decoded), 'rb') as wav:
        assert wav.getnframes() > 0


@needs_ffmpeg
def test_ogg_vorbis_declared_as_opus_is_refused(clinic_on, uid, originals, tmp_path):
    payload = _encode(tmp_path, 'ogg', 'libvorbis')
    response = _post(uid, payload, 'renamed.ogg', audio_mime='audio/ogg;codecs=opus')
    assert response.status_code == 400, response.get_data(as_text=True)
    body = response.get_json()
    assert body['code'] == dictation_upload.FORMAT_MISMATCH
    assert body['error'] == dictation_upload.MSG_FORMAT_MISMATCH
    with app.app_context():
        assert Recording.query.filter_by(user_id=uid).count() == 0


def test_renamed_wav_declared_as_opus_is_refused(clinic_on, uid, originals):
    payload = _wav_pcm()
    response = _post(uid, payload, 'renamed.ogg', audio_mime='audio/ogg;codecs=opus')
    assert response.status_code == 400
    body = response.get_json()
    assert body['code'] == dictation_upload.FORMAT_MISMATCH
    assert body['error'] == dictation_upload.MSG_FORMAT_MISMATCH
    with app.app_context():
        assert Recording.query.filter_by(user_id=uid).count() == 0


def test_undeclared_upload_is_not_refused(clinic_on, uid, originals):
    response = _post(uid, _wav_pcm(), 'note.wav')
    assert response.status_code == 202, response.get_data(as_text=True)
    assert response.get_json().get('code') != dictation_upload.FORMAT_MISMATCH


def test_clinic_off_does_not_refuse_a_mismatched_declaration(clinic_off, uid, originals):
    response = _post(uid, _wav_pcm(), 'renamed.ogg', audio_mime='audio/ogg;codecs=opus')
    assert response.status_code == 202, response.get_data(as_text=True)
    body = response.get_json()
    assert body.get('code') != dictation_upload.FORMAT_MISMATCH
