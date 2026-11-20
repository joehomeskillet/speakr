"""Browser recording formats through upload, derived playback, and legacy repair."""
import os
import subprocess
import sys

import pytest

from src.clinic.decoder import detect_container
from src.clinic.models import ClinicAudioRevision
from test_ingest import _deliver, opened  # noqa: F401
from test_wp_v8_app_wiring import _run
from test_wp_v8_hotfix_play_open import BACKFILL_SCRIPT
from test_wp_v8_web_ingest import author, originals  # noqa: F401


def _tone(tmp_path, extension, encoder):
    media = tmp_path / f'tone.{extension}'
    result = subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
         'sine=frequency=440:sample_rate=48000:duration=0.25', '-c:a', encoder, str(media)],
        capture_output=True, text=True, timeout=30)
    if result.returncode and f"Unknown encoder '{encoder}'" in result.stderr:
        pytest.skip(f'system ffmpeg lacks the {encoder} encoder')
    assert result.returncode == 0, result.stderr
    return media


@pytest.mark.parametrize(('extension', 'encoder', 'container'), [
    ('webm', 'libopus', 'webm'), ('ogg', 'libopus', 'ogg'), ('m4a', 'aac', 'mp4'),
])
def test_recorder_upload_creates_one_playable_revision(clinic_on, author, originals, tmp_path,
                                                      extension, encoder, container):
    media = _tone(tmp_path, extension, encoder)
    with media.open('rb') as stream:
        assert detect_container(stream.read(16)) == container
    # A fresh app binds the real clinic routes and source loader to the test originals root.
    result = _run(r'''
import io, json, sys, wave
from pathlib import Path
from unittest.mock import patch
from src.app import app
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal

uid, media = int(sys.argv[1]), Path(sys.argv[2])
client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True
headers = {'X-CSRF-Token': client.get('/api/csrf-token').json['csrf_token']}
# Exercise deferred conversion while pytest's background queue workers stay stopped.
with patch('src.services.recording_ingestion.CONVERT_WORKERS', 1), media.open('rb') as stream:
    response = client.post('/upload', headers=headers,
                           data={'file': (stream, media.name), 'from_recorder': 'true'})
assert response.status_code == 202, response.get_data(as_text=True)
with app.app_context():
    did = ClinicDictation.query.filter_by(author_user_id=uid).one().id
    original = ClinicOriginal.query.filter_by(dictation_id=did).one()
    stored = Path(sys.argv[3]) / original.storage_key
    assert stored.read_bytes() == media.read_bytes()
    revision_id = ClinicAudioRevision.query.filter_by(dictation_id=did).one().id
audio = client.get(f'/api/clinic/v1/dictations/{did}/audio/derived')
assert audio.status_code == 200, audio.get_data(as_text=True)
assert audio.mimetype == 'audio/wav'
assert audio.headers['X-Audio-Revision'] == revision_id
with wave.open(io.BytesIO(audio.data), 'rb') as wav:
    print('RESULT' + json.dumps([wav.getframerate(), wav.getnchannels(), wav.getsampwidth(),
                                 wav.getnframes()]))
''', os.environ.copy(), str(author['id']), str(media), str(originals))
    rate, channels, width, frames = result
    assert (rate, channels, width) == (16000, 1, 2)
    assert frames / rate == pytest.approx(0.25, abs=0.025)  # AAC frame padding


def test_init_audio_repairs_stored_webm(clinic_off, opened, originals, tmp_path):
    service, uid = opened
    receipt = _deliver(service, uid, data=_tone(tmp_path, 'webm', 'libopus').read_bytes())
    revisions = ClinicAudioRevision.query.filter_by(dictation_id=receipt.dictation_id)
    assert revisions.count() == 0
    repair = subprocess.run(
        [sys.executable, str(BACKFILL_SCRIPT), '--init-audio', '--apply'],
        env={**os.environ, 'CLINIC_MODE': '1'}, capture_output=True, text=True, timeout=60)
    assert repair.returncode == 0, repair.stderr
    assert f'dictation={receipt.dictation_id} action=create reason=initialized' in repair.stdout.splitlines()
    assert revisions.count() == 1
