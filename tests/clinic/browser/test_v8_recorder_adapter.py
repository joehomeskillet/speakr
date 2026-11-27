"""Send a synthetic microphone take through the mounted clinic view and the real upload route."""
import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from v8_browser_support import (OUT_DIR, ROLES, ROOT, THEMES, VIEWPORTS, horizontal_overflow,
                                login, theme_context_options, theme_init_script)


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
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            static_path = os.path.relpath(ROOT / 'static', OUT_DIR)

            def capture(state):
                stem = OUT_DIR / f'pb-01-{state}-{size}-{theme}'
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
                overflow = horizontal_overflow(page)
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
            expect(send).to_have_text('Erneut senden')
            capture('unconfirmed-upload')
            with page.expect_response(re.compile('/api/clinic/v1/recording-takes/')) as lookup:
                send.click()
            assert lookup.value.json() == uploads[0]
            expect(view.get_by_test_id('status')).to_have_text('Sie müssen nichts tun – es wird automatisch erneut gesendet.')
            expect(send).to_have_count(0)
            expect(view.get_by_test_id('status')).to_have_text('Aufnahme gespeichert. Senden hat nicht geklappt.')
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
            browser.close()
