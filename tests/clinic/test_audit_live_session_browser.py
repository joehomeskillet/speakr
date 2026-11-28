"""Browser proof that the live view follows the server session (PAR-LIVE-001).

Resume (recover) is PAUSED. The recorder is not shown as recording until
continue answers with a state other than PAUSED. Stop sends ack_seq + 1.
A revision conflict leaves the phrase text on screen.
"""

from __future__ import annotations

import functools
import json
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
    window.calls.push({ kind: 'request', path, method: options && options.method, body });
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


def test_mounted_capture_renders_live_results_and_leaves_no_browser_copy() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    vue = ROOT / 'static/vendor/js/vue.global.js'
    vue_body = (vue if vue.is_file() else VUE_FALLBACK).read_text(encoding='utf-8')
    position = {'session_id': 's1', 'generation': 1}
    receipts, begins, wire_frames = [], [], []

    def stream(ws):
        phase = {'begun': False, 'partial': False}

        def receive(message):
            if not phase['begun']:
                begin = json.loads(message)
                begins.append(begin)
                phase['sequence'] = begin['after_sequence'] + 1
            sequence = phase['sequence']
            event = {**position, 'sequence': sequence}
            if not phase['begun']:
                phase['begun'] = True
                ws.send(json.dumps({**event, 'status': 'ready', 'sample_rate': 24000,
                                    'max_frame_bytes': 4800, 'max_audio_seconds': 30}))
            elif isinstance(message, bytes):
                assert len(message) == 960
                wire_frames.append(message)
                if any(message) and not phase['partial']:
                    phase['partial'] = True
                    ws.send(json.dumps({**event, 'status': 'partial', 'text': f'Synthetischer Zwischentext {sequence}'}))
            elif message == 'finish':
                assert phase['partial']
                receipts.append(sequence)
                ws.send(json.dumps({**event, 'status': 'server_saved', 'text': f'Synthetischer Testsatz {sequence}',
                                    'event_id': f'event-{sequence}', 'phrase_id': f'phrase-{sequence}'}))
            else:
                raise AssertionError(message)

        ws.on_message(receive)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('''
                const NativeSocket = window.WebSocket;
                window.WebSocket = class extends NativeSocket {
                    constructor(url, protocols) { super(url, protocols); }
                    get protocol() { return 'clinic-live-v1'; }
                };
                window.inputs = [];
                navigator.mediaDevices.getUserMedia = async () => {
                    const context = new AudioContext();
                    const tone = context.createOscillator();
                    const gain = context.createGain();
                    const output = context.createMediaStreamDestination();
                    tone.connect(gain).connect(output);
                    gain.gain.value = 0;
                    tone.start();
                    await context.resume();
                    window.inputs.push({ context, gain, track: output.stream.getAudioTracks()[0] });
                    return output.stream;
                };
            ''')
            page.route('**/api/csrf-token', lambda route: route.fulfill(json={'csrf_token': 'synthetic'}))
            page.route('**/vue.global.js', lambda route: route.fulfill(content_type='text/javascript', body=vue_body))
            html = HTML.replace('const client = {', 'const client = { supportsLiveCapture: true,')
            html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8"><title>Live-Diktat</title>')
            page.route('**/live-capture-proof', lambda route: route.fulfill(content_type='text/html', body=html))
            page.route_web_socket('**/api/clinic/v1/live/sessions/s1/stream', stream)
            page.goto(f'http://127.0.0.1:{server.server_port}/live-capture-proof#/live')
            view = page.get_by_test_id('clinic-live-view')
            expect(view).to_have_attribute('data-phase', 'idle')
            _shots(page, 'capture-empty')
            view.get_by_role('button', name='Starten').focus()
            assert view.get_by_role('button', name='Starten').evaluate(
                "button => getComputedStyle(button).outlineStyle") == 'solid'
            for width, height in VIEWPORTS:
                page.set_viewport_size({'width': width, 'height': height})
                for theme in ('light', 'dark'):
                    _theme(page, theme)
                    assert view.get_by_role('button', name='Starten').bounding_box()['height'] >= 48
            page.set_viewport_size({'width': 1440, 'height': 900})
            _theme(page, 'light')
            view.get_by_role('button', name='Starten').click()
            expect(view).to_have_attribute('data-phase', 'live')
            for number in (1, 2, 3):
                page.evaluate('window.inputs.at(-1).gain.gain.value = 0.2')
                expect(view.get_by_text(f'Synthetischer Zwischentext {number}')).to_be_visible()
                page.evaluate('window.inputs.at(-1).gain.gain.value = 0')
                expect(view.get_by_text(f'Synthetischer Testsatz {number}')).to_be_visible()
                expect(view).to_have_attribute('data-phase', 'live')
                page.evaluate('sequence => window.seq = sequence', number)
                if number == 1:
                    view.get_by_role('button', name='Pausieren').click()
                    expect(view).to_have_attribute('data-phase', 'paused')
                    assert page.evaluate('window.inputs[0].track.readyState') == 'ended'
                    view.get_by_role('button', name='Fortsetzen').click()
                    expect(view).to_have_attribute('data-phase', 'live')
            assert view.locator('[data-state=server_saved]').count() == 3
            for button in view.get_by_role('button').all():
                bounds = button.bounding_box()
                assert bounds['width'] >= 48 and bounds['height'] >= 48
            page.evaluate('document.activeElement.blur()')
            _shots(page, 'capture-rows')
            _theme(page, 'light')
            dom = page.evaluate('''async () => {
                const copy = document.documentElement.cloneNode(true);
                copy.querySelectorAll('script').forEach((node) => node.remove());
                for (const link of copy.querySelectorAll('link[rel=stylesheet]')) {
                    const style = document.createElement('style');
                    style.textContent = (await (await fetch(link.href)).text()).replace(/@import[^;]*;/g, '');
                    link.replaceWith(style);
                }
                return '<!doctype html>' + copy.outerHTML;
            }''')
            (ART / 'live-capture.html').write_text(dom, encoding='utf-8')
            assert page.evaluate('''async () => ({ local: localStorage.length, session: sessionStorage.length,
                databases: (await indexedDB.databases()).length, caches: (await caches.keys()).length })''') == {
                'local': 0, 'session': 0, 'databases': 0, 'caches': 0}
            view.get_by_role('button', name='Beenden').click()
            expect(view).to_have_attribute('data-phase', 'stopped')
            commands = page.evaluate('window.calls.filter(x => x.kind === "command").map(x => x.body)')
            assert commands[-1]['expected_revision'] == 4
            assert commands[-1]['payload']['last_sequence'] == 3
            assert page.evaluate('window.inputs.every(x => x.track.readyState === "ended")')
            assert begins and wire_frames and receipts == [1, 2, 3]
            view.get_by_role('button', name='Starten').click()
            expect(view).to_have_attribute('data-phase', 'live')
            page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
            assert page.evaluate('window.inputs.every(x => x.track.readyState === "ended")')
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
