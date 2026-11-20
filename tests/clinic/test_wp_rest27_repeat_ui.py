"""Normal author entry, real editor lease, registered reprocess route and persisted queue.

Synthetic audio and isolated SQLite only; queue workers and all background tasks
stay disabled. This proves queue acceptance, not ASR execution.
"""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import test_wp_mount_correction as mount


@pytest.fixture(scope="module")
def repeat_server(tmp_path_factory):
    yield from _repeat_server(tmp_path_factory)


@pytest.fixture(scope="module", params=[False, True], ids=['normal', 'clinic'])
def upstream_server(tmp_path_factory, request):
    for server in _repeat_server(tmp_path_factory, clinic=request.param, standalone=True):
        yield (*server, request.param)


def _repeat_server(tmp_path_factory, clinic=True, standalone=False):
    seed = '''
from io import BytesIO
from hashlib import sha256
from pathlib import Path
import os, wave
audio_buffer = BytesIO()
with wave.open(audio_buffer, 'wb') as wav:
    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    wav.writeframes(b'\\x01\\x00' * 64000)
audio_bytes = audio_buffer.getvalue()
'''
    server = mount.SERVER.replace('with app.app_context():', seed + '\nwith app.app_context():', 1)
    if not clinic:
        # Normal upstream login uses bcrypt; the clinic fixture uses Argon2.
        server = server.replace('from src.app import app, db', 'from src.app import app, db\nfrom flask_bcrypt import generate_password_hash')
        server = server.replace('identity.hash_password(password)', "generate_password_hash(password).decode('utf-8')")
    server = server.replace('content_hash="o" * 64, size_bytes=1000',
                            'content_hash=sha256(audio_bytes).hexdigest(), size_bytes=len(audio_bytes)')
    server = server.replace('    dictation_id = dic.id', '''
    source = Path(app.config['UPLOAD_FOLDER']) / 'repeat.wav'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(audio_bytes)
    rec.audio_path = str(source)
    rec.transcription = 'Anamnese Husten seit drei Tagen.'
    media = Path(os.environ['CLINIC_ORIGINALS_ROOT']) / original.storage_key
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(audio_bytes)
    db.session.commit()
    dictation_id = dic.id''')
    if standalone:
        server = server.replace('    dictation_id = dic.id', '''
    db.session.add(Recording(user_id=author.id, title='Standalone', status='COMPLETED',
                             audio_path=str(source), original_filename='standalone.wav',
                             transcription='Standalone synthetic transcript.'))
    db.session.commit()
    dictation_id = dic.id''')
    original_env = mount._env
    scene = {}

    def env(path, _clinic):
        result = original_env(path, clinic)
        scene['url'] = result['SQLALCHEMY_DATABASE_URI']
        return {**result, 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
                'CLINIC_ORIGINALS_ROOT': str(path / 'originals'),
                'CLINIC_MODE': '1' if clinic else '0', 'CLINIC_ASR_PROMPTS_ENABLED': '1',
                'TRANSCRIPTION_CONNECTOR': 'asr_endpoint', 'ASR_BASE_URL': 'http://127.0.0.1:9'}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mount, 'SERVER', server)
        patch.setattr(mount, '_env', env)
        for base, did in mount.live_server.__wrapped__(tmp_path_factory):
            engine = create_engine(scene['url'])
            try:
                yield base, did, engine
            finally:
                engine.dispose()


def test_author_normal_entry_repeat_uses_established_generation_and_rejection_is_truthful(repeat_server, tmp_path):
    from src.models import ProcessingJob, Recording

    base, did, engine = repeat_server
    artifacts = Path(os.environ.get('REPEAT_UI_ARTIFACT_DIR', tmp_path))
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, 'author213@local.test')
            # The unchanged upstream dialog remains available. The real server,
            # not a clinic-wide client block, rejects mapped audio without a lease.
            with Session(engine) as session:
                recording_id = session.query(Recording).filter_by(title='Synthetisch').one().id
            _open_upstream_dialog(page, recording_id)
            page.locator('#asr-language-reprocess').select_option('fr')
            page.locator('#asr-hotwords-reprocess').fill('Synthetischer Hinweis')
            with page.expect_response(lambda r: r.url.endswith('/reprocess_transcription')) as missing:
                page.locator('.modal-panel:has(#asr-language-reprocess) .btn--primary').click()
            assert missing.value.status == 400, missing.value.text()
            assert missing.value.json()['code'] == 'invalid_lease_generation'
            assert missing.value.request.post_data_json['hotwords'] == 'Synthetischer Hinweis'
            assert 'lease_generation' not in missing.value.request.post_data_json
            expect(page.get_by_text('Das verknüpfte Diktat im Schreibplatz öffnen, „Bearbeiten“ wählen und Änderungen speichern. Danach die erneute Transkription anfordern.', exact=True)).to_be_visible()
            with Session(engine) as session:
                assert session.query(ProcessingJob).filter_by(job_type='reprocess_transcription').count() == 0
            page.get_by_test_id('open-link').first.click()
            expect(page.get_by_test_id('clinic-correction-view')).to_be_visible()
            page.locator('[data-context-toggle=options]').click()
            expect(page.get_by_test_id('repeat-open')).to_be_disabled()
            # Establish two real generations through the existing edit controls.
            page.locator('[data-action=acquire]').click()
            expect(page.get_by_test_id('text')).to_be_editable()
            page.locator('[data-action=release]').click()
            expect(page.get_by_test_id('text')).not_to_be_editable()
            with page.expect_response(lambda r: r.url.endswith('/leases') and r.request.method == 'POST') as held:
                page.locator('[data-action=acquire]').click()
            generation = held.value.json()['generation']
            assert generation > 1
            expect(page.get_by_test_id('repeat-open')).to_be_enabled()
            page.get_by_test_id('repeat-open').click()
            for width, height in [(1440, 900), (390, 844)]:
                page.set_viewport_size({'width': width, 'height': height})
                expect(page.get_by_test_id('repeat-confirm')).to_be_visible()
                page.screenshot(path=str(artifacts / f'repeat-confirm-{width}.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.get_by_role('button', name='Abbrechen', exact=True).click()
            with Session(engine) as session:
                assert session.query(ProcessingJob).filter_by(job_type='reprocess_transcription').count() == 0
            page.get_by_test_id('repeat-open').click()
            with page.expect_response(lambda r: r.url.endswith('/reprocess_transcription')) as queued:
                page.get_by_test_id('repeat-confirm').click()
            assert queued.value.status == 200, queued.value.text()
            assert queued.value.request.post_data_json == {'lease_generation': generation}
            assert queued.value.json()['success'] is True
            expect(page.get_by_test_id('repeat-result')).to_have_attribute('data-state', 'queued')
            expect(page.get_by_test_id('repeat-open')).to_be_disabled()
            with Session(engine) as session:
                job = session.query(ProcessingJob).filter_by(job_type='reprocess_transcription').one()
                assert job.status == 'queued'
                assert queued.value.request.url.endswith(f'/recording/{job.recording_id}/reprocess_transcription')
            # Re-enter normally: an already queued recording is rejected by the registered route.
            page.get_by_test_id('drawer-close').click()
            page.get_by_test_id('clinic-correction-back').click()
            page.get_by_test_id('open-link').first.click()
            page.locator('[data-action=acquire]').click()
            expect(page.get_by_test_id('text')).to_be_editable()
            page.locator('[data-context-toggle=options]').click()
            page.get_by_test_id('repeat-open').click()
            with page.expect_response(lambda r: r.url.endswith('/reprocess_transcription')) as rejected:
                page.get_by_test_id('repeat-confirm').click()
            assert rejected.value.status == 400, rejected.value.text()
            expect(page.get_by_test_id('repeat-result')).to_have_attribute('data-state', 'error')
            expect(page.get_by_test_id('repeat-result')).not_to_contain_text('Warteschlange hinzugefügt')
            with Session(engine) as session:
                assert session.query(ProcessingJob).filter_by(job_type='reprocess_transcription').count() == 1
            page.screenshot(path=str(artifacts / 'repeat-rejected.png'), full_page=True)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def _open_upstream_dialog(page, recording_id):
    page.wait_for_function("document.querySelector('#app').__vue_app__?._instance?.proxy?.confirmReprocess")
    page.evaluate('''async id => {
        const response = await fetch(`/api/recordings/${id}`);
        if (!response.ok) throw new Error('Recording detail unavailable');
        await document.querySelector('#app').__vue_app__._instance.proxy.confirmReprocess(
            'transcription', await response.json());
    }''', recording_id)
    expect(page.locator('#asr-language-reprocess')).to_be_visible()


def test_upstream_modal_keeps_unlinked_options_and_real_unleased_route(upstream_server):
    from src.models import ProcessingJob, Recording

    base, _, engine, clinic = upstream_server
    with Session(engine) as session:
        recording_id = session.query(Recording).filter_by(title='Standalone').one().id
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, 'author213@local.test')
            expect(page.locator('#app')).to_have_attribute('data-clinic-mode', str(clinic))
            _open_upstream_dialog(page, recording_id)
            page.locator('#asr-language-reprocess').select_option('fr')
            page.locator('#asr-hotwords-reprocess').fill('Synthetischer Hinweis')
            page.locator('#asr-initial-prompt-reprocess').fill('Synthetischer Kontext')
            with page.expect_response(lambda r: r.url.endswith('/reprocess_transcription')) as queued:
                page.locator('.modal-panel:has(#asr-language-reprocess) .btn--primary').click()
            assert queued.value.status == 200, queued.value.text()
            assert queued.value.request.post_data_json == {
                'language': 'fr', 'hotwords': 'Synthetischer Hinweis', 'initial_prompt': 'Synthetischer Kontext',
            }
            assert queued.value.json()['success'] is True
            with Session(engine) as session:
                job = session.query(ProcessingJob).filter_by(recording_id=recording_id, job_type='reprocess_transcription').one()
                assert job.status == 'queued'
                params = json.loads(job.params)
                assert params['language'] == 'fr'
                assert params['hotwords'] == 'Synthetischer Hinweis'
                assert params['initial_prompt'] == 'Synthetischer Kontext'
                assert 'clinic_asr_repeat' not in params
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
