"""DS2/DSS upload and decode-before-ASR wiring, with a synthetic stub decoder.

No real dictation file is used: the "DS2" files here are a magic header plus
filler bytes, and ``dss-decode`` is replaced by a stub script that writes a
valid 16 kHz mono WAV (or fails the way the real tool does). Real decoding is
only covered by the env-gated tests in test_decoder.py.

SHARED-DB: every assertion is scoped to the rows the test created.
"""
import io
import os
import stat
import sys
import uuid
import wave
from datetime import datetime
from unittest.mock import patch

import pytest

from src.app import app, db
from src.clinic import decoder, dictation_upload
from src.models import ProcessingJob, Recording, User
from src.services.storage import get_storage_service
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector

DS2 = b'\x03ds2' + b'\x00' * 2048
DSS = b'\x02dss' + b'\x00' * 2048

STUB_OK = (
    "import sys, wave\n"
    "out = sys.argv[sys.argv.index('-O') + 1]\n"
    "w = wave.open(out, 'wb')\n"
    "w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)\n"
    "w.writeframes(b'\\x00\\x00' * 16000); w.close()\n"
)


def _stub_failing(message):
    return f"import sys\nsys.stderr.write({message!r})\nsys.exit(1)\n"


def _make_stub(tmp_path, body):
    path = tmp_path / f'stub_{uuid.uuid4().hex[:6]}.py'
    path.write_text(f'#!{sys.executable}\n' + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def stub_decoder(tmp_path, monkeypatch):
    def install(body=STUB_OK):
        monkeypatch.setenv(decoder.DSS_CODEC_BIN_ENV, _make_stub(tmp_path, body))
    return install


# --- check_upload / magic bytes -------------------------------------------

@pytest.mark.parametrize('name,content,container', [
    ('diktat.ds2', DS2, 'ds2'), ('DIKTAT.DSS', DSS, 'dss')])
def test_matching_extension_and_magic_is_accepted(tmp_path, name, content, container):
    path = tmp_path / 'f'
    path.write_bytes(content)
    check = dictation_upload.check_upload(str(path), name)
    assert (check.is_dictation, check.container, check.error) == (True, container, None)


@pytest.mark.parametrize('name,content', [
    ('x.ds2', b'RIFF\x00\x00\x00\x00WAVE'), ('x.ds2', DSS), ('x.dss', DS2),
    ('x.ds2', b'not a dictation file at all')])
def test_wrong_magic_is_refused_with_german_message(tmp_path, name, content):
    path = tmp_path / 'f'
    path.write_bytes(content)
    check = dictation_upload.check_upload(str(path), name)
    assert check.is_dictation and check.error == dictation_upload.MSG_BAD_HEADER
    assert 'Diktatdatei' in check.error


def test_other_extensions_are_not_dictation(tmp_path):
    path = tmp_path / 'f'
    path.write_bytes(DS2)
    assert dictation_upload.check_upload(str(path), 'meeting.mp3') == dictation_upload.UploadCheck(False)


# --- decode_for_asr ---------------------------------------------------------

def test_decode_for_asr_writes_wav_and_keeps_original(tmp_path, stub_decoder):
    stub_decoder()
    src = tmp_path / 'orig.ds2'
    src.write_bytes(DS2)
    before = decoder.sha256_file(src)
    wav_path = dictation_upload.decode_for_asr(src)
    assert wav_path.endswith('.16k.wav') and os.path.dirname(wav_path) == str(tmp_path)
    with wave.open(wav_path, 'rb') as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
    assert dictation_upload.wav_seconds(wav_path) == pytest.approx(1.0)
    assert decoder.sha256_file(src) == before


@pytest.mark.parametrize('stderr,expected', [
    ('Error decoding x: file is encrypted, password required', dictation_upload.MSG_ENCRYPTED),
    ('Error decoding x: unsupported mode 7', dictation_upload.MSG_UNSUPPORTED),
    ('Error decoding x: header checksum mismatch', dictation_upload.MSG_CORRUPT)])
def test_decoder_failures_map_to_german_messages(tmp_path, stub_decoder, stderr, expected):
    stub_decoder(_stub_failing(stderr))
    src = tmp_path / 'orig.ds2'
    src.write_bytes(DS2)
    with pytest.raises(dictation_upload.DictationDecodeError) as exc:
        dictation_upload.decode_for_asr(src)
    assert str(exc.value) == expected
    assert src.read_bytes() == DS2


def test_missing_decoder_binary_is_a_german_error(tmp_path, monkeypatch):
    monkeypatch.delenv(decoder.DSS_CODEC_BIN_ENV, raising=False)
    src = tmp_path / 'orig.dss'
    src.write_bytes(DSS)
    with pytest.raises(dictation_upload.DictationDecodeError) as exc:
        dictation_upload.decode_for_asr(src)
    assert str(exc.value) == dictation_upload.MSG_DECODER_MISSING


# --- upload route -----------------------------------------------------------

@pytest.fixture
def uid():
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        user = User(username=f'ds_{suffix}', email=f'ds_{suffix}@local.test', password='x')
        db.session.add(user)
        db.session.commit()
        yield user.id
        db.session.rollback()
        storage = get_storage_service()
        for rec in Recording.query.filter_by(user_id=user.id).all():
            try:
                storage.delete(rec.audio_path, missing_ok=True)
            except Exception:
                pass
        ProcessingJob.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        Recording.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        db.session.commit()


def _post(uid, filename, content, clinic_off=None):
    from flask import g, has_app_context
    if has_app_context():
        g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    with patch('src.services.recording_ingestion.get_duration', return_value=None), \
         patch('src.services.recording_ingestion.get_creation_date', return_value=None), \
         patch('src.services.recording_ingestion.get_codec_info',
               side_effect=AssertionError('a dictation upload must not be probed')), \
         patch('src.services.recording_ingestion.convert_if_needed',
               side_effect=AssertionError('a dictation upload must not be converted')), \
         patch('src.services.webhook_dispatch.emit_webhook_event'):
        return client.post('/upload', data={'file': (io.BytesIO(content), filename)},
                           content_type='multipart/form-data')


@pytest.mark.parametrize('name,content,mime', [
    ('diktat.ds2', DS2, 'audio/x-ds2'), ('diktat.dss', DSS, 'audio/x-dss')])
def test_upload_accepts_dictation_and_stores_original_unchanged(
        clinic_off, uid, stub_decoder, name, content, mime):
    stub_decoder()
    response = _post(uid, name, content)
    assert response.status_code == 202, response.get_data(as_text=True)
    recording_id = response.get_json()['id']
    with app.app_context():
        rec = db.session.get(Recording, recording_id)
        assert rec.mime_type == mime
        assert rec.original_filename == name
        with get_storage_service().materialize(rec.audio_path) as stored:
            assert open(stored.local_path, 'rb').read() == content


def test_upload_refuses_wrong_magic_with_german_message(clinic_off, uid):
    response = _post(uid, 'fake.ds2', b'RIFF' + b'\x00' * 64)
    assert response.status_code == 400
    body = response.get_json()
    assert body['error'] == dictation_upload.MSG_BAD_HEADER
    assert body['code'] == 'dictation_invalid_header'
    with app.app_context():
        assert Recording.query.filter_by(user_id=uid).count() == 0


# --- transcription pipeline -------------------------------------------------

def _recording(uid, tmp_path, content=DS2, name='diktat.ds2'):
    src = tmp_path / f'orig_{uuid.uuid4().hex[:6]}.ds2'
    src.write_bytes(content)
    with app.app_context():
        rec = Recording(user_id=uid, title='Diktat', status='PROCESSING', original_filename=name,
                        audio_path=str(src), mime_type='audio/x-ds2')
        db.session.add(rec)
        db.session.commit()
        return rec.id, src


class _RecordingStub(ClinicStubTranscriptionConnector):
    """The synthetic connector, noting what the ASR path was given."""
    seen = {}

    def transcribe(self, request):
        data = request.audio_file.read()
        request.audio_file.seek(0)
        type(self).seen = {'magic': data[:4], 'filename': request.filename,
                           'mime': request.mime_type}
        return super().transcribe(request)


def _run_asr(rec_id, src):
    from src.tasks.processing import transcribe_audio_task
    with patch('src.services.transcription.get_connector', return_value=_RecordingStub({})), \
         patch('src.tasks.processing.generate_title_task'), \
         patch('src.services.job_queue.job_queue.enqueue'):
        return transcribe_audio_task(app.app_context(), rec_id, str(src), 'diktat.ds2',
                                     datetime.utcnow(), language='de')


def test_asr_receives_decoded_wav_and_derived_file_is_removed(
        clinic_off, uid, tmp_path, stub_decoder):
    stub_decoder()
    rec_id, src = _recording(uid, tmp_path)
    _run_asr(rec_id, src)
    assert _RecordingStub.seen['magic'] == b'RIFF'
    assert _RecordingStub.seen['mime'] == 'audio/wav'
    assert _RecordingStub.seen['filename'].endswith('.16k.wav')
    assert src.read_bytes() == DS2
    assert not list(tmp_path.glob('*.16k.wav'))
    with app.app_context():
        rec = db.session.get(Recording, rec_id)
        assert rec.audio_duration_seconds == pytest.approx(1.0)
        assert rec.transcription


@pytest.mark.parametrize('stderr,expected', [
    ('Error decoding x: encrypted', dictation_upload.MSG_ENCRYPTED),
    ('Error decoding x: unsupported mode', dictation_upload.MSG_UNSUPPORTED)])
def test_decoder_error_fails_recording_in_german_and_keeps_original(
        clinic_off, uid, tmp_path, stub_decoder, stderr, expected):
    stub_decoder(_stub_failing(stderr))
    rec_id, src = _recording(uid, tmp_path)
    returned = _run_asr(rec_id, src)
    assert returned == expected
    assert src.read_bytes() == DS2
    with app.app_context():
        rec = db.session.get(Recording, rec_id)
        assert rec.status == 'FAILED'
        assert rec.error_message == expected
        assert not rec.transcription
