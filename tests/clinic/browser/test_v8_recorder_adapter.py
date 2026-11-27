"""Send a synthetic microphone take through the mounted clinic view and the real upload route."""
import os
import re

from playwright.sync_api import expect, sync_playwright

from v8_browser_support import ROLES, SERVER, login, serve_app


_STAGING_GRANT = '''
import os
from time import time
from src.clinic.config import dev_defaults
os.environ['CLINIC_PROFILE'] = 'staging'
os.environ['CLINIC_OFFLINE_TEST_PROXY_SECRET'] = 'synthetic-ingress-fixture-' * 2
with app.app_context():
    author = User.query.filter_by(email=emails['author']).one()
    dev_defaults()['web_client'].update(
        managed_offline_enabled=True,
        managed_offline_test_grant=dict(browser_id='a' * 64, account_version=author.get_id(),
                                        version=1, quota_bytes=1024 * 1024, expires_at=int(time()) + 300))
'''


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


def test_only_attested_managed_browser_retains_take_when_network_drops(tmp_path):
    source = SERVER.replace('server = make_server(', _STAGING_GRANT + '\nserver = make_server(')
    with serve_app(tmp_path, source) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,
            args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
        )
        try:
            session = login(browser, base, ROLES['author'])
            for managed in (False, True):
                context = browser.new_context(storage_state=session, service_workers='block')
                try:
                    _check_offline_take(context, base, managed)
                finally:
                    context.close()
        finally:
            browser.close()


def _check_offline_take(context, base, managed):
    page = context.new_page()
    path = '/api/clinic/v1/offline-web/capability'
    headers = {'x-clinic-channel': 'managed_web_offline'}
    if managed:
        headers.update({'x-clinic-client-cert-sha256': 'a' * 64,
                        'x-clinic-offline-proxy-secret': 'synthetic-ingress-fixture-' * 2})

    def attest(request_page):
        request_page.route('**' + path, lambda route: route.continue_(headers={**route.request.headers, **headers}))

    def unlock(offline_page):
        offline_page.get_by_label('Passphrase').fill('synthetic offline passphrase')
        offline_page.get_by_test_id('unlock').click()
        expect(offline_page.get_by_test_id('lock')).to_be_visible()

    attest(page)
    with page.expect_response(base + path) as checked:
        page.goto(base + '/#/offline')
    assert checked.value.json()['available'] is managed
    if managed:
        unlock(page)
    else:
        expect(page.get_by_test_id('notice')).to_contain_text('nicht freigegeben')

    page.evaluate("location.hash = '#/neu'")
    view = page.get_by_test_id('clinic-recording-view')
    view.get_by_test_id('rec-start').click()
    page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
    view.get_by_test_id('rec-stop').click()
    context.set_offline(True)
    view.get_by_test_id('rec-send').click()
    expect(view.get_by_test_id('created-id')).to_have_count(0)
    if not managed:
        expect(view.get_by_test_id('status')).to_contain_text('wird nicht lokal gespeichert')
        assert not page.evaluate("(async () => (await indexedDB.databases()).some(d => d.name === 'clinic-managed-offline'))()")
        return

    expect(view.get_by_test_id('status')).to_contain_text('liegt verschlüsselt')
    assert page.evaluate("""() => new Promise((resolve, reject) => {
        const request = indexedDB.open('clinic-managed-offline');
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
            const read = request.result.transaction('encrypted').objectStore('encrypted').getAll();
            read.onsuccess = () => {
                request.result.close();
                resolve(read.result.some(record => !!record.ct && !record.audio && !record.title));
            };
            read.onerror = () => reject(read.error);
        };
    })""")
    context.set_offline(False)
    recovery = context.new_page()
    attest(recovery)
    recovery.goto(base + '/#/offline')
    unlock(recovery)
    with recovery.expect_download() as restored:
        recovery.get_by_test_id('restore').click()
    assert restored.value.path().read_bytes().startswith(b'\x1a\x45\xdf\xa3')  # WebM header
    recovery.close()

    page.route('**/upload', lambda route: route.fulfill(status=503), times=1)
    view.get_by_test_id('rec-send').click()
    expect(view.get_by_test_id('status')).to_contain_text('liegt verschlüsselt')
    expect(view.get_by_test_id('created-id')).to_have_count(0)
    with page.expect_response(base + '/upload') as upload:
        view.get_by_test_id('rec-send').click()
    assert upload.value.status == 202, upload.value.text()
    expect(view.get_by_test_id('created-id')).to_have_text(upload.value.json()['dictation_id'])
    page.evaluate("location.hash = '#/offline'")
    expect(page.get_by_test_id('no-drafts')).to_be_visible()
