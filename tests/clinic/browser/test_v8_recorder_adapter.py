"""Send a synthetic microphone take through the mounted clinic view and the real upload route."""
import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from v8_browser_support import (OUT_DIR, ROLES, ROOT, THEMES, VIEWPORTS, horizontal_overflow,
                                login, theme_context_options, theme_init_script)


def _capture(page, prefix, state, size, theme):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f'{prefix}-{state}-{size}-{theme}'
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
    }""", os.path.relpath(ROOT / 'static', OUT_DIR))
    stem.with_suffix('.html').write_text(html, encoding='utf-8')
    overflow = horizontal_overflow(page)
    assert not (overflow['document'] or overflow['body']), overflow


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


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('size', VIEWPORTS)
def test_lost_confirmations_keep_one_take_and_offer_keyboard_retry(app_base, size, theme):
    width, height = VIEWPORTS[size]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,
            args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
        )
        try:
            context = browser.new_context(storage_state=login(browser, app_base, ROLES['author']),
                                          viewport={'width': width, 'height': height}, **theme_context_options(theme))
            context.add_init_script(theme_init_script(theme))
            page = context.new_page()
            page.goto(app_base + '/#/neu')
            view = page.get_by_test_id('clinic-recording-view')
            expect(view.get_by_test_id('rec-start')).to_be_enabled()
            expect(page.locator('html')).to_have_class(re.compile(r'\bdark\b' if theme == 'dark' else r'^(?!.*\bdark\b)'))
            _capture(page, 'pb-01', 'empty', size, theme)
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
            expect(send).to_have_text('Erneut senden')
            _capture(page, 'pb-01', 'unconfirmed-upload', size, theme)
            with page.expect_response(re.compile('/api/clinic/v1/recording-takes/')) as lookup:
                send.click()
            assert lookup.value.json() == uploads[0]
            expect(view.get_by_test_id('status')).to_have_text('Sie müssen nichts tun – es wird automatisch erneut gesendet.')
            expect(send).to_have_count(0)
            expect(view.get_by_test_id('status')).to_have_text('Aufnahme gespeichert. Senden hat nicht geklappt.')
            expect(view.get_by_role('button', name='Erneut senden', exact=True)).to_have_count(1)
            expect(view.get_by_test_id('send-destination')).to_have_text('Vorgesehen: Schreibdienst V860')
            assert send.bounding_box()['height'] >= 48
            _capture(page, 'pb-01', 'failure', size, theme)

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
            browser.close()


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('size', VIEWPORTS)
def test_send_shows_real_progress_stall_and_receipt(app_base, size, theme):
    width, height = VIEWPORTS[size]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,
            args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
        )
        try:
            context = browser.new_context(storage_state=login(browser, app_base, ROLES['author']),
                                          viewport={'width': width, 'height': height}, **theme_context_options(theme))
            context.add_init_script(theme_init_script(theme))
            page = context.new_page()
            page.goto(app_base + '/#/neu')
            view = page.get_by_test_id('clinic-recording-view')
            expect(view.get_by_test_id('rec-start')).to_be_enabled()
            expect(page.locator('html')).to_have_class(re.compile(r'\bdark\b' if theme == 'dark' else r'^(?!.*\bdark\b)'))
            _capture(page, 'pb-02', 'empty', size, theme)
            view.get_by_test_id('rec-start').click()
            page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 2")
            view.get_by_test_id('rec-stop').click()
            send = view.get_by_test_id('rec-send')
            expect(send).to_be_enabled()
            assert send.bounding_box()['height'] >= 48

            page.evaluate("""() => {
                window.pendingUploads = [];
                window.XMLHttpRequest = class extends XMLHttpRequest {
                    open(method, url, ...args) {
                        this.isRecording = method === 'POST' && url === '/upload';
                        super.open(method, url, ...args);
                    }
                    setRequestHeader(name, value) {
                        if (name.toLowerCase() === 'x-csrftoken') this.csrf = value;
                        super.setRequestHeader(name, value);
                    }
                    send(body) {
                        if (!this.isRecording) return super.send(body);
                        this.body = body;
                        pendingUploads.push(this);
                        this.emitUpload('loadstart');
                    }
                    emitUpload(type, loaded = 0) {
                        this.upload.dispatchEvent(new ProgressEvent(type, {lengthComputable: true, loaded, total: 100}));
                    }
                    reply(status, responseText) {
                        Object.defineProperties(this, {status: {value: status}, responseText: {value: responseText}});
                        this.dispatchEvent(new ProgressEvent('load'));
                    }
                };
            }""")
            page.clock.install()
            page.clock.pause_at(page.evaluate('Date.now()') + 1000)
            status = view.get_by_test_id('status')
            hint = view.get_by_text('Die Verbindung ist langsam. Es geht weiter – Sie müssen nichts tun.', exact=True)
            spinner = view.locator('[role=progressbar]:not([aria-valuenow]), .spinner, .animate-spin')
            send.evaluate('button => button.click()')
            expect(status).to_have_text('Wird gesendet …')
            page.clock.fast_forward(9_999)
            expect(hint).to_have_count(0)
            page.clock.fast_forward(1)
            expect(hint).to_be_visible()

            page.evaluate("pendingUploads[0].emitUpload('progress', 20)")
            expect(status).to_have_text('Wird gesendet … 20 %')
            expect(hint).to_have_count(0)
            page.evaluate("pendingUploads[0].emitUpload('progress', 60)")
            expect(status).to_have_text('Wird gesendet … 60 %')
            expect(spinner).to_have_count(0)
            _capture(page, 'pb-02', 'progress', size, theme)
            page.clock.fast_forward(10_000)
            expect(hint).to_be_visible()
            _capture(page, 'pb-02', 'slow', size, theme)

            page.evaluate("pendingUploads[0].emitUpload('load', 100)")
            expect(status).to_have_text('Empfang wird bestätigt …')
            page.clock.fast_forward(10_000)
            expect(hint).to_have_count(0)
            expect(spinner).to_have_count(0)
            _capture(page, 'pb-02', 'awaiting-receipt', size, theme)

            with page.expect_response(app_base + '/api/csrf-token') as refresh:
                page.evaluate("pendingUploads[0].reply(403, '{\"csrf_retryable\":true}')")
            assert refresh.value.ok
            expect(status).to_have_text('Wird gesendet …')
            assert page.evaluate("""() => pendingUploads.length === 2 && !!pendingUploads[1].csrf &&
                pendingUploads[0].body.get('clinic_take_id') === pendingUploads[1].body.get('clinic_take_id') &&
                !!pendingUploads[1].body.get('clinic_take_id')""")
            page.evaluate("""async () => {
                const xhr = pendingUploads[1];
                xhr.emitUpload('load', 100);
                const response = await window.originalFetch('/upload', {
                    method: 'POST', body: xhr.body, headers: {'X-CSRFToken': xhr.csrf},
                });
                xhr.reply(response.status, await response.text());
            }""")
            expect(status).to_have_text('Eingereicht.')
            expect(view.get_by_test_id('receipt-saved')).to_have_text('Aufnahme gespeichert')
            expect(view.get_by_test_id('receipt-arrived')).to_contain_text('Schreibdienst V860')
            _capture(page, 'pb-02', 'submitted', size, theme)
        finally:
            browser.close()
