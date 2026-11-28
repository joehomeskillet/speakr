"""PAR-WF-003: real worklist policy/HTTP client and Vue mount with synthetic users.

Reuse the worklist API fixture and the clinic browser launch helper. No external
services or production data. Capture the required viewport/theme/state matrix.
"""

from pathlib import Path
from threading import Event, Thread

from flask import request
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from test_wp_mount_correction import _launch
from test_wp_worklist_api import endpoint  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / '.wp' / 'artifacts'
HTML = '''<!doctype html><html lang="de-CH"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/clinic-admin.css">
<link rel="stylesheet" href="/static/css/clinic-lists.css">
<link rel="stylesheet" href="/static/css/phone.css">
<link rel="stylesheet" href="/static/css/queue.css">
<link rel="stylesheet" href="/static/css/tailwind.css">
<body class="v8-ui"><main id="app" style="padding:16px;max-width:1440px;margin:auto"></main>
<script src="/static/vendor/js/vue.global.js"></script><script type="module">
import { WorklistView } from '/static/js/clinic/compose-worklist.js';
import { createClinicApiClient } from '/static/js/clinic/api-client.js';
const params = new URLSearchParams(location.search);
document.documentElement.classList.toggle('dark', params.get('theme') === 'dark');
Vue.createApp(WorklistView, {
    client: createClinicApiClient(), currentUser: {id: Number(params.get('user'))},
    vue: Vue, intervalMs: 0,
}).mount('#app');
</script></html>'''


def test_search_marks_authorized_metadata_and_excludes_other_users(endpoint):
    app, entries, _, _ = endpoint
    entries[0].row.update(title='Synthetischer Suchwort Befund',
                          notes='Suchwort morgen <img src=x onerror=alert(1)>',
                          recipient='Team Nord')
    entries[2].row.update(title='Fremdes Suchwort', notes='Nicht sichtbar Suchwort')
    app.static_folder = str(ROOT / 'static')
    app.add_url_rule('/proof', 'proof', lambda: HTML)
    release_nord = Event()

    @app.after_request
    def hold_nord_response(response):
        if request.args.get('search') == 'Nord':
            assert release_nord.wait(timeout=30)
        return response

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            try:
                for width, height in ((1440, 900), (1280, 720), (390, 844)):
                    for theme in ('light', 'dark'):
                        context = browser.new_context(viewport={'width': width, 'height': height})
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        assert page.request.get(base + '/synthetic-login/7').ok
                        page.goto(f'{base}/proof?user=7&theme={theme}')
                        expect(page.locator('[data-row]')).to_have_count(2)
                        expect(page.locator('[data-search-field]')).to_have_count(0)
                        search = page.get_by_role('searchbox', name='Metadatensuche')
                        search.fill('Suchwort')
                        search.press('Enter')
                        rows = page.locator('[data-row]')
                        expect(rows).to_have_count(1)
                        expect(rows).to_have_attribute('data-id', entries[0].row['dictation_id'])
                        for field in ('title', 'notes'):
                            expect(rows.locator(f'[data-search-field="{field}"] mark')).to_have_text('Suchwort')
                        expect(rows.locator('[data-search-field="recipient"]')).to_have_count(0)
                        expect(rows.locator('img')).to_have_count(0)
                        assert 'Fremdes' not in rows.inner_text()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        mark = rows.locator('mark').first
                        assert mark.evaluate('(e) => getComputedStyle(e).backgroundColor') != 'rgba(0, 0, 0, 0)'
                        page.screenshot(path=str(ARTIFACTS / f'search-{width}x{height}-{theme}-rows.png'),
                                        full_page=True, animations='disabled')
                        if width == 1440 and theme == 'light':
                            search.fill('Nord')
                            search.press('Enter')
                            expect(rows.locator('[data-search-field="recipient"] mark')).to_have_text('Nord')
                            expect(rows.locator('[data-search-field="title"]')).to_have_count(0)
                            search.fill('')
                            expect(search).to_have_value('')
                            # Finish the previous refresh while the clear is still unsubmitted.
                            release_nord.set()
                            expect(page.get_by_test_id('refresh-button')).to_be_enabled()
                            expect(search).to_have_value('')
                            with page.expect_response(base + '/api/clinic/v1/worklist/rows') as cleared:
                                search.press('Enter')
                            assert cleared.value.ok
                            expect(rows).to_have_count(2)
                            expect(page.locator('mark')).to_have_count(0)

                        # Another real session identity has no view right to either match.
                        assert page.request.get(base + '/synthetic-login/9').ok
                        response = page.request.get(base + '/api/clinic/v1/worklist/rows?search=Suchwort')
                        assert response.ok and response.json()['total'] == 0
                        assert response.json()['items'] == []
                        page.goto(f'{base}/proof?user=9&theme={theme}')
                        search.fill('Suchwort')
                        search.press('Enter')
                        expect(page.locator('[data-testid="empty-state"]')).to_contain_text('Keine Treffer')
                        expect(page.locator('[data-row], mark')).to_have_count(0)
                        assert 'Synthetischer Suchwort Befund' not in page.content()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        page.screenshot(path=str(ARTIFACTS / f'search-{width}x{height}-{theme}-empty.png'),
                                        full_page=True, animations='disabled')
                        assert not errors, errors
                        context.close()
            finally:
                browser.close()
    finally:
        release_nord.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
