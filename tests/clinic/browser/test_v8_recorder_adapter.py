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


def test_only_attested_managed_browser_retains_take_when_network_drops(tmp_path):
    source = support.SERVER.replace('server = make_server(', _STAGING_GRANT + '\nserver = make_server(')
    with support.serve_app(tmp_path, source) as base, sync_playwright() as playwright:
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
    recovery.set_viewport_size({'width': 390, 'height': 844})
    recovery.goto(base + '/#/offline')
    unlock(recovery)
    row = recovery.get_by_test_id('draft')
    expect(row).to_be_visible()
    assert recovery.locator('.clinic-offline .table-wrap').evaluate(
        '(table) => table.scrollWidth === table.clientWidth'
    )
    for action in ('restore', 'discard'):
        bounds = row.get_by_test_id(action).bounding_box()
        assert bounds['width'] >= 48 and bounds['height'] >= 48 and 0 <= bounds['x'] <= 390 - bounds['width']
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
