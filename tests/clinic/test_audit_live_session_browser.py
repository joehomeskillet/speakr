"""Browser proof that the live view follows the server session (PAR-LIVE-001).

Resume (recover) is PAUSED. The recorder is not shown as recording until
continue answers with a state other than PAUSED. Stop sends ack_seq + 1.
A revision conflict leaves the phrase text on screen.
"""

from __future__ import annotations

import functools
import http.server
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / '.wp' / 'artifacts'
VUE_FALLBACK = Path('/tmp/vue.global.js')
VIEWPORTS = ((1440, 900), (1280, 720), (390, 844))

HTML = """<!doctype html><html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/phone.css">
<script src="/static/vendor/js/vue.global.js"></script></head>
<body class="v8-ui" style="margin:0;background:var(--canvas);color:var(--text-primary)">
<main id="app"></main>
<script type="module">
import { LiveView } from '/static/js/clinic/compose-live.js';
window.calls = [];
window.mountCalls = [];
window.started = false;
window.seq = 0;
window.holdContinue = false;
window.continueState = 'RECORDING';
window.stopConflict = false;
const position = (state, sequence) => ({
  session_id: 's1', dictation_id: 'd-100', state, generation: 1, sequence,
  audio_offset_ms: 0, events: [],
});
const client = {
  switchContext() {},
  async request(path, options) {
    const body = options && options.body;
    const call = { kind: 'request', path, method: options && options.method, body };
    if (path.endsWith('/resume') && !window.started) {
      window.mountCalls.push(call);
      return { outcome: 'ok', status: 200, data: { session_id: null } };
    }
    window.calls.push(call);
    if (path.endsWith('/resume')) return { outcome: 'ok', status: 200, data: position('PAUSED', window.seq) };
    if (path.endsWith('/pause')) return { outcome: 'ok', status: 200, data: position('PAUSED', window.seq) };
    if (path.endsWith('/continue')) {
      if (window.holdContinue) {
        window.holdContinue = false;
        await new Promise((resolve) => { window.releaseContinue = resolve; });
      }
      return { outcome: 'ok', status: 200, data: position(window.continueState, window.seq) };
    }
    if (path.endsWith('/phrases')) {
      window.seq = body.sequence;
      return { outcome: 'ok', status: 200, data: {
        status: 'server_saved', phrase_id: 'p' + body.sequence, session_id: 's1',
        generation: body.generation, sequence: body.sequence, event_id: body.event_id,
      } };
    }
    return { outcome: 'ok', status: 200, data: {} };
  },
  async command(body) {
    window.calls.push({ kind: 'command', body });
    if (body.command === 'stop_live_session' && window.stopConflict) {
      return { outcome: 'conflict', status: 409, message: 'Versionskonflikt' };
    }
    if (body.command === 'start_live_session') window.started = true;
    const revision = body.command === 'start_live_session' ? 1 : (window.seq + 1);
    return { outcome: 'ok', status: 200, data: { status: 'committed', revision } };
  },
};
Vue.createApp(LiveView, {
  client, dictationId: 'd-100', vue: Vue,
  onReady(controller) { window.live = controller; },
}).mount('#app');
</script></body></html>"""


def _theme(page, theme: str) -> None:
    page.evaluate(
        '''(theme) => {
            document.documentElement.classList.toggle('dark', theme === 'dark');
            if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
            else document.documentElement.removeAttribute('data-theme');
        }''',
        theme,
    )


def _shots(page, label: str) -> None:
    for width, height in VIEWPORTS:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            _theme(page, theme)
            page.locator('[data-testid=live-controls]').scroll_into_view_if_needed()
            overflow = page.evaluate(
                '() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            assert overflow <= 1, (label, width, theme, overflow)
            boxes = page.locator('[data-testid=live-controls] button').evaluate_all(
                '''els => els.map((e) => {
                    const b = e.getBoundingClientRect();
                    return {top: b.top, left: b.left, right: b.right, bottom: b.bottom, width: b.width, height: b.height};
                })''')
            assert boxes, (label, width, theme)
            for box in boxes:
                assert box['width'] >= 24 and box['height'] >= 24, (label, width, theme, box)
                assert box['right'] <= width + 1 and box['left'] >= -1, (label, width, theme, box)
            page.screenshot(
                path=str(ART / f'live-{label}-{width}x{height}-{theme}.png'),
                full_page=True, animations='disabled')


def test_live_session_matches_server_state() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    vue = ROOT / 'static/vendor/js/vue.global.js'
    vue_body = (vue if vue.is_file() else VUE_FALLBACK).read_text(encoding='utf-8')
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/vue.global.js', lambda route: route.fulfill(
                content_type='text/javascript', body=vue_body))
            page.route('**/live-proof', lambda route: route.fulfill(content_type='text/html', body=HTML))
            page.goto(f'http://127.0.0.1:{server.server_port}/live-proof')
            view = page.locator('[data-testid=clinic-live-view]')
            expect(view).to_have_attribute('data-phase', 'idle')
            expect(page.get_by_text('Noch kein Text. Mit der Aufnahmetaste beginnen.')).to_be_visible()
            page.wait_for_function('() => window.mountCalls.length > 0')
            mount_calls = page.evaluate('window.mountCalls')
            assert len(mount_calls) == 1
            assert mount_calls[0]['method'] == 'POST'
            assert mount_calls[0]['path'].endswith('/dictations/d-100/resume')
            assert not page.evaluate('window.calls')
            _shots(page, 'empty')
            page.set_viewport_size({'width': 1440, 'height': 900})
            _theme(page, 'light')

            page.get_by_role('button', name='Starten', exact=True).click()
            expect(view).to_have_attribute('data-phase', 'paused')
            expect(page.locator('.recorder.recording')).to_have_count(0)
            expect(page.get_by_test_id('live-announcement')).to_have_text('Live-Diktat pausiert.')
            order = page.evaluate(
                '''() => window.calls.map((c) => c.kind === "command" ? c.body.command : c.path)''')
            assert order[0] == 'start_live_session'
            assert order[1].endswith('/resume')
            assert not any(str(item).endswith('/continue') for item in order)

            page.evaluate('window.holdContinue = true')
            page.get_by_role('button', name='Fortsetzen', exact=True).click()
            page.wait_for_function(
                '() => window.calls.some((c) => c.path && c.path.endsWith("/continue"))')
            expect(view).to_have_attribute('data-phase', 'paused')
            expect(page.locator('.recorder.recording')).to_have_count(0)
            page.evaluate('window.releaseContinue()')
            expect(view).to_have_attribute('data-phase', 'live')
            expect(page.locator('.recorder.recording')).to_have_count(1)

            page.get_by_role('button', name='Pausieren', exact=True).click()
            expect(view).to_have_attribute('data-phase', 'paused')
            expect(page.locator('.recorder.recording')).to_have_count(0)
            page.get_by_role('button', name='Fortsetzen', exact=True).click()
            expect(view).to_have_attribute('data-phase', 'live')
            expect(page.locator('.recorder.recording')).to_have_count(1)

            page.evaluate(
                '''() => window.live.finalizePhrase("Guten Tag, der Befund ist unauffällig.", {
                    audio_start_ms: 0, audio_end_ms: 500, audio_b64: "AAAA",
                    audio_revision_id: "a1", transcript_revision_id: "r1", text_hash: "h"})''')
            expect(page.get_by_text('Guten Tag, der Befund ist unauffällig.')).to_be_visible()
            _shots(page, 'rows')

            page.evaluate('window.stopConflict = true')
            page.get_by_role('button', name='Beenden', exact=True).click()
            expect(view).to_have_attribute('data-phase', 'live')
            expect(page.get_by_test_id('live-note')).to_have_text(
                'Beenden fehlgeschlagen. Bitte erneut versuchen.')
            expect(page.get_by_text('Guten Tag, der Befund ist unauffällig.')).to_be_visible()
            stopped = page.evaluate(
                '''() => window.calls.filter((c) => c.kind === "command" && c.body.command === "stop_live_session").pop().body''')
            assert stopped['expected_revision'] == 2
            assert stopped['expected_revision'] > 0
            assert stopped['payload'] == {
                'session_id': 's1', 'generation': 1, 'last_sequence': 1}

            page.evaluate('window.stopConflict = false')
            page.get_by_role('button', name='Beenden', exact=True).click()
            expect(view).to_have_attribute('data-phase', 'stopped')
            expect(page.get_by_text('Guten Tag, der Befund ist unauffällig.')).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
