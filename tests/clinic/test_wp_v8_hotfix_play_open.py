"""Regression tests for opening Aufnahmen and playing newly stored originals."""

import hashlib
import os
import subprocess
import sys
import wave
from pathlib import Path

from src.app import app, db
from src.clinic.audio_revisions import current_audio_revision, get_revision_edl
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal
from src.models import Recording

from test_wp_mount_correction import _env, _launch, _login, live_server  # noqa: F401
from test_wp_v8_app_wiring import _run
from test_wp_v8_web_ingest import author  # noqa: F401


BACKFILL_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/clinic_backfill_dictations.py'


def _write_wav(path):
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x00' * 16015)


def test_upload_initializes_audio_and_author_can_play_it(tmp_path):
    media = tmp_path / 'original.wav'
    _write_wav(media)
    env = {**_env(tmp_path, True), 'CLINIC_ORIGINALS_ROOT': str(tmp_path / 'originals')}
    result = _run(r'''
import io, json, sys, wave
from pathlib import Path
from src.app import app, db
from src.clinic.audio_revisions import current_audio_revision
from src.clinic.models import ClinicAudioRevision, ClinicDictation
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.init_db import initialize_database
from src.models import User

with app.app_context():
    initialize_database(app)
    user = User(username='hotfix-author', email='hotfix-author@local.test', password='x')
    db.session.add(user)
    db.session.flush()
    pool = ClinicAuthorPool(name='Synthetic authors', created_by=user.id)
    db.session.add(pool)
    db.session.flush()
    db.session.add(ClinicAuthorPoolMember(pool_id=pool.id, user_id=user.id, created_by=user.id))
    db.session.commit()
    uid = user.id

client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(uid)
    session['_fresh'] = True
headers = {'X-CSRF-Token': client.get('/api/csrf-token').json['csrf_token']}
for _ in range(2):
    with Path(sys.argv[1]).open('rb') as media:
        upload = client.post('/upload', headers=headers,
                             data={'file': (media, 'original.wav'), 'client_upload_id': 'hotfix-upload'})
    assert upload.status_code == 202, upload.get_data(as_text=True)
with app.app_context():
    dictation = ClinicDictation.query.filter_by(author_user_id=uid).one()
    revision = current_audio_revision(dictation)
    assert revision is not None
    assert ClinicAudioRevision.query.filter_by(dictation_id=dictation.id).count() == 1
    did, revision_id = dictation.id, revision.id
audio = client.get(f'/api/clinic/v1/dictations/{did}/audio/derived')
assert audio.status_code == 200, audio.get_data(as_text=True)
assert audio.mimetype == 'audio/wav'
assert audio.headers['X-Audio-Revision'] == revision_id
with wave.open(io.BytesIO(audio.data), 'rb') as wav:
    print('RESULT' + json.dumps([wav.getframerate(), wav.getnframes()]))
''', env, str(media))
    assert result == [16000, 16000]


def test_repair_cli_plans_applies_and_skips_an_existing_head(clinic_on, author, tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', os.fspath(tmp_path))
    media = tmp_path / 'clinic/originals/v1' / ('a' * 64)
    media.parent.mkdir(parents=True)
    _write_wav(media)
    leftover = media.with_name('.partial-synthetic')
    leftover.hardlink_to(media)
    data = media.read_bytes()
    with app.app_context():
        recording = Recording(user_id=author['id'], status='COMPLETED')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=author['id'])
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, storage_key=str(media.relative_to(tmp_path)),
                                  content_hash=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                                  receipt='{}', origin_ref='synthetic')
        db.session.add(original)
        db.session.commit()
        did, oid = dictation.id, original.id

    def repair(*flags):
        done = subprocess.run([sys.executable, str(BACKFILL_SCRIPT), '--init-audio', *flags],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout.splitlines()

    assert f'dictation={did} action=create reason=missing_audio_revision' in repair()
    assert leftover.is_file()
    with app.app_context():
        assert current_audio_revision(db.session.get(ClinicDictation, did)) is None

    assert f'dictation={did} action=create reason=initialized' in repair('--apply')
    with app.app_context():
        dictation = db.session.get(ClinicDictation, did)
        revision = current_audio_revision(dictation)
        revision_id = revision.id
        edl = get_revision_edl(dictation, revision)
        assert edl.total_duration_ms == 1000 and edl.entries[0].source_id == oid

    assert f'dictation={did} action=skip reason=already_initialized' in repair('--apply')
    with app.app_context():
        assert ClinicAudioRevision.query.filter_by(dictation_id=did).one().id == revision_id
    assert media.read_bytes() == data
    assert leftover.is_file()


def test_author_opens_own_dictation_from_aufnahmen(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as playwright:
        with _launch(playwright) as browser:
            page = _login(browser, base, 'author213@local.test')
            assert page.locator('#app').get_attribute('data-clinic-role') == 'author'
            page.get_by_test_id('clinic-recordings-view').get_by_test_id('open-link').first.click()
            correction = page.get_by_test_id('clinic-correction-view')
            correction.wait_for(state='visible', timeout=5000)
            assert dictation in correction.inner_text()
            assert not page.js_errors, page.js_errors
