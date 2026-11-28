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


@pytest.fixture(scope="module")
def browser():
    # Share one sync Playwright instance with the role_states fixture.
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
        )
        try:
            yield instance
        finally:
            instance.close()


def test_recorder_can_retry_and_opens_no_upload_modal(browser, app_base):
    context = browser.new_context(storage_state=support.login(browser, app_base, support.ROLES['author']))
    try:
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
        context.close()


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


@pytest.mark.parametrize('theme', support.THEMES)
@pytest.mark.parametrize('size', support.VIEWPORTS)
def test_lost_confirmations_keep_one_take_and_offer_keyboard_retry(browser, app_base, size, theme):
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(storage_state=support.login(browser, app_base, support.ROLES['author']),
                                  viewport={'width': width, 'height': height}, **support.theme_context_options(theme))
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        page.goto(app_base + '/#/neu')
        view = page.get_by_test_id('clinic-recording-view')
        expect(view.get_by_test_id('rec-start')).to_be_enabled()
        expect(page.locator('html')).to_have_class(re.compile(r'\bdark\b' if theme == 'dark' else r'^(?!.*\bdark\b)'))
        support.OUT_DIR.mkdir(parents=True, exist_ok=True)
        static_path = os.path.relpath(support.ROOT / 'static', support.OUT_DIR)

        def capture(state):
            stem = support.OUT_DIR / f'pb-01-{state}-{size}-{theme}'
            page.screenshot(path=stem.with_suffix('.png'), animations='disabled')
            html = page.evaluate("""staticPath => {
                const copy = document.documentElement.cloneNode(true);
                copy.querySelectorAll('script, [name="csrf-token"], [name="csrf_token"]')
                    .forEach(node => node.remove());
                copy.querySelectorAll('[href^="/static/"], [src^="/static/"]').forEach(node => {
                    for (const name of ['href', 'src']) {
                        const value = node.getAttribute(name);
                        if (value?.startsWith('/static/')) node.setAttribute(name, staticPath + value.slice(7));
                    }
                });
                copy.querySelectorAll('[src^="blob:"]').forEach(node => node.removeAttribute('src'));
                return '<!doctype html>\\n' + copy.outerHTML;
            }""", static_path)
            stem.with_suffix('.html').write_text(html, encoding='utf-8')
            overflow = support.horizontal_overflow(page)
            assert not (overflow['document'] or overflow['body']), overflow

        capture('empty')
        view.get_by_test_id('rec-start').click()
        page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 2")
        view.get_by_test_id('rec-stop').click()
        uploads = []
        sends = []

        def lose_upload(route):
            response = route.fetch()
            assert response.status == 202, response.text()
            uploads.append(response.json())
            route.abort()

        def lose_send(route):
            sends.append(route.request.post_data_json)
            if len(sends) == 1:
                response = route.fetch()
                assert response.status == 200, response.text()
                route.abort()
            else:
                route.fulfill(status=503)

        send = view.get_by_test_id('rec-send')
        page.route('**/upload', lose_upload)
        page.route('**/api/clinic/v1/dictations/*/send', lose_send, times=2)
        send.click()
        expect(view.get_by_test_id('status')).to_contain_text('weiterhin in diesem Browser')
        expect(send).to_have_text('Erneut sichern')
        capture('unconfirmed-upload')
        with page.expect_response(re.compile('/api/clinic/v1/recording-takes/')) as lookup:
            send.click()
        assert lookup.value.json() == uploads[0]
        expect(view.get_by_test_id('created-id')).to_have_text(uploads[0]['dictation_id'])
        view.get_by_test_id('save-draft').click()
        view.get_by_test_id('submit-draft').click()
        expect(view.get_by_test_id('status')).to_have_text('Sie müssen nichts tun – es wird automatisch erneut gesendet.')
        expect(send).to_have_count(0)
        expect(view.get_by_test_id('status')).to_have_text('Aufnahme gespeichert. Senden hat nicht geklappt.')
        expect(send).to_have_text('Erneut senden')
        expect(view.get_by_role('button', name='Erneut senden', exact=True)).to_have_count(1)
        expect(view.get_by_test_id('send-destination')).to_have_text('Vorgesehen: Schreibdienst V860')
        assert send.bounding_box()['height'] >= 48
        capture('failure')

        page.evaluate('document.activeElement.blur()')
        for _ in range(32):
            page.keyboard.press('Tab')
            if send.evaluate('node => node === document.activeElement'):
                break
        expect(send).to_be_focused()
        page.keyboard.press('Enter')
        expect(view.get_by_test_id('status')).to_have_text('Eingereicht.')
        assert len(uploads) == 1
        assert sends == [{'take_id': uploads[0]['take_id']}] * 2
        page.get_by_test_id('nav-recordings').click()
        expect(page.locator(f'[data-row][data-id="{uploads[0]["dictation_id"]}"]')).to_have_count(1)
    finally:
        context.close()
