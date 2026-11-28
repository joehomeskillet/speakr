"""Send a synthetic microphone take through the mounted clinic view and the real upload route."""
import io
import os
import re
import uuid
import wave
from pathlib import Path

import pytest

from playwright.sync_api import expect, sync_playwright

import v8_browser_support as support
from v8_browser_support import ROLES, login


@pytest.fixture(scope="module")
def app_base(tmp_path_factory):
    # P-030 requires explicit grants for each action, including on the author's own draft.
    seed = support.SERVER.replace(
        "server = make_server",
        '    pools.allow(author.id, "own", actor_id=admin.id, actions=("rename", "priority"))\n'
        "server = make_server",
        1,
    )
    with support.serve_app(tmp_path_factory.mktemp("p04"), seed) as base:
        yield base


def test_recorder_can_retry_and_opens_no_upload_modal(app_base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
        )
        try:
            context = browser.new_context(storage_state=login(browser, app_base, ROLES["author"]))
            page = context.new_page()
            page.goto(app_base + "/#/neu")
            view = page.get_by_test_id("clinic-recording-view")
            expect(view.get_by_test_id("rec-start")).to_be_enabled()
            expect(view.locator("input")).to_have_count(0)
            posts = []
            page.on("request", lambda req: posts.append(req.url)
                    if req.method == "POST" and req.url.startswith(app_base + "/upload") else None)
            page.evaluate("""() => {
                const app = document.querySelector('#app').__vue_app__._instance.proxy;
                window.stockUploadUsed = false;
                app.$watch(() => app.showUploadModal || app.uploadQueue.length > 0, used => {
                    if (used) window.stockUploadUsed = true;
                }, { flush: 'sync', immediate: true });
            }""")
            view.get_by_test_id("rec-start").click()
            page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
            view.get_by_test_id("rec-stop").click()
            page.route("**/upload", lambda route: route.fulfill(status=503), times=1)
            view.get_by_test_id("rec-send").click()
            expect(view.get_by_test_id("status")).to_contain_text("Senden fehlgeschlagen")
            assert page.url.endswith("/#/neu")

            with page.expect_response(app_base + "/upload") as upload:
                view.get_by_test_id("rec-send").click()
            assert upload.value.status == 202, upload.value.text()
            receipt = upload.value.json()
            assert re.fullmatch(r"Diktat \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", receipt["title"]), receipt
            expect(view.get_by_test_id("created-id")).to_have_text(receipt["dictation_id"])
            expect(view.get_by_test_id("submit-draft")).to_be_disabled()
            view.get_by_test_id("save-draft").click()
            expect(view.get_by_test_id("submit-draft")).to_be_enabled()
            view.get_by_test_id("submit-draft").click()
            expect(view.get_by_test_id("status")).to_have_text("Eingereicht.")
            assert page.url.endswith("/#/neu")
            assert page.evaluate("window.stockUploadUsed") is False
            assert posts == [app_base + "/upload"] * 2
            page.get_by_test_id("nav-recordings").click()
            row = page.locator(f'[data-row][data-id="{receipt["dictation_id"]}"]')
            expect(row).to_be_visible()
            expect(row).to_contain_text(receipt["title"])
        finally:
            browser.close()


def test_protected_draft_before_submission(app_base, browser, role_states):
    wav = io.BytesIO()
    with wave.open(wav, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x01' * 16000)
    code = 'synthetic-draft-code'
    out = Path(os.environ['P165_ARTIFACTS']) if 'P165_ARTIFACTS' in os.environ else None
    static_root = os.path.relpath(support.ROOT / 'static', out) if out is not None else None

    def capture(page, state, size, theme):
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
        assert not support.horizontal_overflow(page)['document']
        if out is not None:
            stem = out / f'{state}-{size}-{theme}'
            page.screenshot(path=stem.with_suffix('.png'), animations='disabled', caret='hide')
            stem.with_suffix('.html').write_text(page.evaluate("""staticRoot => {
                const copy = document.documentElement.cloneNode(true);
                copy.querySelectorAll('script, meta[http-equiv], meta[name=csrf-token]').forEach(el => el.remove());
                copy.querySelectorAll('link[rel=stylesheet]').forEach(link => {
                    const url = new URL(link.getAttribute('href'), location.href);
                    if (url.origin !== location.origin) { link.remove(); return; }
                    link.href = staticRoot + url.pathname.split('/static')[1];
                });
                return '<!doctype html>' + copy.outerHTML;
            }""", static_root), encoding='utf-8')

    def open_page(role, size, theme):
        width, height = support.VIEWPORTS[size]
        ctx = browser.new_context(storage_state=role_states[role], viewport={'width': width, 'height': height},
                                  **support.theme_context_options(theme))
        ctx.add_init_script(support.theme_init_script(theme))
        return ctx, ctx.new_page()

    for size in support.VIEWPORTS if out else ('390x844',):
        for theme in support.THEMES if out else ('light',):
            author, page = open_page('author', size, theme)
            page.goto(app_base + '/#/neu')
            view = page.get_by_test_id('clinic-recording-view')
            expect(view.get_by_test_id('rec-start')).to_be_enabled()
            capture(page, 'recording-empty', size, theme)
            view.get_by_test_id('mode-file').click()
            payload = wav.getvalue()[:-2] + bytes((list(support.VIEWPORTS).index(size), support.THEMES.index(theme)))
            view.locator('input[type=file]').set_input_files({
                'name': f'schutz-{size}-{theme}.wav', 'mimeType': 'audio/wav', 'buffer': payload,
            })
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type in ('warning', 'error') else None)
            with page.expect_response(lambda res: res.url.endswith('/upload') and res.request.method == 'POST') as upload:
                view.get_by_test_id('file-upload').click()
            assert upload.value.status == 202, upload.value.text()
            expect(view.get_by_test_id('status')).to_contain_text('Gespeichert')
            expect(view.get_by_test_id('created-id')).to_have_text(upload.value.json()['dictation_id'])
            assert not any('Vue warn' in error for error in errors), errors
            did = upload.value.json()['dictation_id']
            path = app_base + f'/api/clinic/v1/dictations/{did}'
            view.get_by_test_id('save-draft').click()
            expect(view.get_by_test_id('submit-draft')).to_be_enabled()
            view.get_by_text('Diktat-Schutz (optional)', exact=True).click()
            enable = view.get_by_role('button', name='Schutz aktivieren')
            expect(enable).to_be_enabled()
            capture(page, 'recording-draft', size, theme)
            view.get_by_label('Schutzcode', exact=True).fill(code)
            view.get_by_role('checkbox').check()
            enable.click()
            expect(view.get_by_text('Der Schutz wurde aktiviert.', exact=True)).to_be_visible()
            view.get_by_test_id('submit-draft').click()
            expect(view.get_by_test_id('status')).to_have_text('Eingereicht.')
            author.close()

            typist, page = open_page('typist', size, theme)
            assert typist.request.get(path + '/detail').json()['protection_required'] is True
            support._show_recordings(page, app_base)
            page.evaluate("""id => document.querySelector('#app').__vue_app__._instance.proxy
                .openClinicDictation({dictationId: id, mode: 'player'})""", did)
            view = page.get_by_test_id('clinic-detail-view')
            expect(view.get_by_label('Schutzcode', exact=True)).to_be_visible()
            capture(page, 'detail-locked', size, theme)
            view.get_by_label('Schutzcode', exact=True).fill('incorrect')
            view.get_by_role('button', name='Entsperren').click()
            expect(view.get_by_text('Code falsch oder keine Berechtigung.', exact=True)).to_be_visible()
            expect(view.locator('audio')).to_have_count(0)
            for suffix in ('/audio/derived', '/audio-revisions/current', '/correction'):
                assert typist.request.get(path + suffix).status == 404
            view.get_by_label('Schutzcode', exact=True).fill(code)
            view.get_by_role('button', name='Entsperren').click()
            expect(view.locator('audio')).to_be_attached()
            view.get_by_role('button', name='Wiedergabe starten').click()
            page.wait_for_function("document.querySelector('[data-testid=detail-playback] audio').currentTime > 0")
            assert typist.request.get(path + '/audio/derived').body().startswith(b'RIFF')
            assert 'text' in typist.request.get(path + '/correction').json()
            capture(page, 'detail-open', size, theme)
            typist.close()

            outsider, page = open_page('lead', size, theme)
            csrf = outsider.request.get(app_base + '/api/csrf-token').json()['csrf_token']
            denied = outsider.request.post(path + '/unlock', data={'secret': code}, headers={'X-CSRF-Token': csrf})
            assert denied.status == 404
            for suffix in ('/detail', '/audio/derived', '/correction'):
                assert outsider.request.get(path + suffix).status == 404
            outsider.close()


@pytest.mark.parametrize('size', support.VIEWPORTS)
@pytest.mark.parametrize('theme', support.THEMES)
def test_saved_draft_round_trip(browser, app_base, role_states, size, theme):
    width, height = support.VIEWPORTS[size]
    author = browser.new_context(viewport=dict(width=width, height=height),
                                 storage_state=role_states['author'], **support.theme_context_options(theme))
    admin = browser.new_context(storage_state=role_states['admin'])
    try:
        author.add_init_script(support.theme_init_script(theme))
        page = author.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        support.navigation.show_recording(page, app_base)
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
        view = page.get_by_test_id('clinic-recording-view')
        out = os.environ.get('P04_ARTIFACTS')
        if out:
            page.screenshot(path=Path(out) / f'p04-empty-{size}-{theme}.png', full_page=True, animations='disabled')
        view.get_by_test_id('mode-file').click()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(bytes([width % 256, height % 256, len(theme), 0]) * 8000)
        view.locator('#crv-file').set_input_files(dict(name=f'synthetic-{size}-{theme}.wav',
                                                       mimeType='audio/wav', buffer=audio.getvalue()))
        with page.expect_response(app_base + '/upload') as upload:
            view.get_by_test_id('file-upload').click()
        assert upload.value.status == 202, upload.value.text()
        receipt = upload.value.json()
        expect(view.get_by_test_id('created-id')).to_have_text(receipt['dictation_id'])
        form = view.get_by_test_id('draft-metadata')
        expect(view.get_by_test_id('draft-acceptance')).to_have_text('Abnahme: Nicht erforderlich')
        expect(view.get_by_test_id('submit-draft')).to_be_disabled()
        form.get_by_label('Titel').fill('Synthetischer P04-Entwurf')
        form.get_by_label('Anmerkung').fill('Synthetische Anmerkung für den Versand.')
        form.get_by_label('Priorität').select_option(label='Dringend')
        form.get_by_label('Frist').fill('2026-07-20')
        path = app_base + '/api/clinic/v1/dictations/' + receipt['dictation_id']
        with page.expect_response(path + '/metadata') as save:
            view.get_by_test_id('save-draft').click()
        assert save.value.ok, save.value.text()
        expect(view.get_by_test_id('submit-draft')).to_be_enabled()
        policy = admin.request.put(path + '/acceptance-policy', headers={
            'X-CSRF-Token': admin.request.get(app_base + '/api/csrf-token').json()['csrf_token'],
            'If-Match': '0', 'Idempotency-Key': str(uuid.uuid4()),
        }, data=dict(acceptance_required=True, approver_ids=[]))
        assert policy.ok, policy.text()
        view.get_by_test_id('open-created').click()
        expect(page.get_by_role('heading', name='Schreibplatz')).to_be_visible()
        page.go_back()
        expect(form.get_by_label('Titel')).to_have_value('Synthetischer P04-Entwurf')
        expect(form.get_by_label('Anmerkung')).to_have_value('Synthetische Anmerkung für den Versand.')
        expect(form.get_by_label('Priorität')).to_have_value('128')
        expect(form.get_by_label('Frist')).to_have_value('2026-07-20')
        expect(view.get_by_test_id('draft-acceptance')).to_have_text('Abnahme: Erforderlich')
        assert author.request.get(path + '/detail').json()['deadline_at'] == '2026-07-20T21:59:59Z'
        assert not errors, errors
        overflow = support.horizontal_overflow(page)
        assert not (overflow['document'] or overflow['body']), overflow
        if out:
            page.screenshot(path=Path(out) / f'p04-draft-{size}-{theme}.png', full_page=True, animations='disabled')
    finally:
        author.close()
        admin.close()
