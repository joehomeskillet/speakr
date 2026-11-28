"""Assignment dialog against real Flask routes with synthetic, isolated accounts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE = ROOT / 'tests/fixtures/prototypes/ui-v8'
SERVER = r'''
import runpy, sys
from flask import session, send_from_directory
from werkzeug.serving import make_server
from src.app import app
from src.init_db import initialize_database
from src.database import db
from src.clinic import pools
from src.clinic.route_policy import ROUTE_POLICY, RouteRule, PUBLIC
# Only the isolated test server serves these fixture and reference pages.
ROUTE_POLICY.update(proof=RouteRule(PUBLIC), prototype=RouteRule(PUBLIC))
t = runpy.run_path('tests/clinic/test_wp_v8_assignment_route.py')
with app.app_context():
    initialize_database(app)
    admin = t['_user']('admin', admin=True)
    author, lead, typist = map(t['_user'], ('author', 'lead', 'qa-visual'))
    pool = pools.ClinicAuthorPool(name='Autorenpool', created_by=admin)
    db.session.add(pool)
    db.session.flush()
    staff = t['_staff_pool'](admin, 'Schreibpool', [(lead, 'admin'), (typist, 'member')])
    db.session.add(pools.ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff, created_by=admin))
    sent, _ = t['_dictation'](author, pool.id, sent=True)
    db.session.commit()
@app.get('/assignment-proof/<actor>')
def proof(actor):
    uid = {'lead': lead, 'typist': typist}[actor]
    session.update(_user_id=str(uid), _fresh=True)
    return """<!doctype html><html lang="de"><head><link rel="stylesheet" href="/static/css/tokens.css"><link rel="stylesheet" href="/static/css/clinic-v8.css"></head>
<body><div class="v8-ui" id="proof"><clinic-worklist-view :vue="V" :client="client" :current-user="user" :interval-ms="0" :fetch-changes="feed" /></div>
<script src="/static/vendor/js/vue.global.js"></script><script type="module">
import { WorklistView } from '/static/js/clinic/compose-worklist.js';
import { createClinicApiClient } from '/static/js/clinic/api-client.js';
const client = createClinicApiClient();
Vue.createApp({components: {'clinic-worklist-view': WorklistView}, setup() { return {V: Vue, client, user: {id: USER_ID, role:'typist'}, feed: async () => ({outcome:'ok', data:{changes:[], has_more:false}})}; }}).mount('#proof');
</script></body></html>""".replace('USER_ID', str(uid))
@app.get('/prototype/<path:name>')
def prototype(name):
    return send_from_directory(sys.argv[2], name)
server = make_server('127.0.0.1', int(sys.argv[1]), app, threaded=True)
print('READY', flush=True)
server.serve_forever()
'''


def test_assignment_browser(tmp_path):
    output = Path(os.environ.get('ASSIGN_UI_PROOF_DIR', str(tmp_path / 'proofs')))
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "ui.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads'), 'RATELIMIT_ENABLED': 'false',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1'}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = tmp_path / 'server.log'
    with log.open('w') as stream:
        process = subprocess.Popen([sys.executable, '-c', SERVER, str(port), str(PROTOTYPE)], cwd=ROOT, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while 'READY' not in log.read_text():
                assert process.poll() is None and time.monotonic() < deadline, log.read_text()[-4000:]
                time.sleep(.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    base = f'http://127.0.0.1:{port}'
                    page.goto(base + '/assignment-proof/lead')
                    page.locator('[data-row]').first.click()
                    history = page.get_by_test_id('history-panel')
                    page.keyboard.press('Control+i')
                    expect(history.get_by_text('Keine Ereignisse vorhanden.', exact=True)).to_be_visible()
                    trigger = page.get_by_role('button', name='Zuweisen', exact=True)
                    expect(trigger).to_be_visible()
                    expect(page.get_by_role('button', name='Für mich übernehmen')).to_be_enabled()
                    page.get_by_role('button', name='Für mich übernehmen').click()
                    expect(page.get_by_text('Zugewiesen an: Lead', exact=True)).to_be_visible()
                    page.keyboard.press('Control+i')
                    expect(history.get_by_text('Zuweisung geändert', exact=True)).to_be_visible()
                    trigger.click()
                    dialog = page.get_by_role('dialog', name='Diktat zuweisen')
                    search = dialog.get_by_label('Schreibkraft suchen')
                    expect(search).to_be_focused()
                    search.fill('Qa-visual')
                    for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        for theme in ('light', 'dark'):
                            page.evaluate('(theme) => document.documentElement.classList.toggle("dark", theme === "dark")', theme)
                            page.screenshot(path=str(output / f'assign-{width}-{theme}.png'))
                            box = dialog.bounding_box()
                            assert box['x'] >= 0 and box['x'] + box['width'] <= width
                    # Native dialog traps focus and Escape returns focus without closing the detail.
                    for _ in range(8):
                        page.keyboard.press('Tab')
                        assert dialog.evaluate('(el) => el.contains(document.activeElement)')
                    page.keyboard.press('Escape')
                    expect(dialog).not_to_be_visible()
                    expect(trigger).to_be_focused()
                    expect(page.locator('[data-testid=detail-panel]')).to_be_visible()
                    trigger.click()
                    search.fill('qa-vis')
                    dialog.get_by_label('Schreibkraft', exact=True).select_option(label='Qa-visual')
                    with page.expect_response(lambda response: response.url.endswith('/assignment') and response.request.method == 'POST') as assigned:
                        dialog.get_by_role('button', name='Zuweisen', exact=True).click()
                    assert assigned.value.status == 200, assigned.value.text()
                    expect(page.get_by_text('Zugewiesen an: Qa-visual', exact=True)).to_be_visible()
                    expect(dialog).not_to_be_visible()
                    expect(page.locator('[data-testid=detail-panel]')).to_have_count(0)
                    expect(page.get_by_text('Zugewiesen an: Qa-visual', exact=True)).to_be_visible()
                    page.goto(base + '/assignment-proof/typist')
                    page.locator('[data-row]').first.click()
                    expect(page.get_by_text('Zugewiesen an: Qa-visual', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='Zuweisen', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='Für mich übernehmen')).to_have_count(0)
                    assert not errors, errors
                    assert page.goto(base + '/prototype/index.html#arbeitsliste').status == 200
                    for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                        page.set_viewport_size({'width': width, 'height': height})
                        page.screenshot(path=str(output / f'prototype-{width}.png'))
                    page.evaluate('openPoolDialog(4524)')
                    page.screenshot(path=str(output / 'prototype-pool-dialog.png'))
                    print(f'Browser: {browser.version}; real lead assignment, typist visibility, focus, Escape, six screenshots passed; proofs: {output}')
                    (output / 'result.txt').write_text(f'Chromium {browser.version}\nReal Flask candidates/assignment routes; lead assignment and self-assignment; typist row visibility; keyboard focus and Escape; six Light/Dark screenshots. No page errors.\n')
                finally:
                    browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)
