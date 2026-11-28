import functools
import http.server
import os
import threading

from playwright.sync_api import expect, sync_playwright

from test_audit_live_session_browser import ART, HTML, ROOT, VIEWPORTS, VUE_FALLBACK, _theme

TRANSFER = """
    if (path.includes('/output-targets')) {
      const dictation = path.split('/')[2];
      const allowed = dictation === 'd-100' && window.releaseAvailable;
      const targets = ['txt', 'docx', 'rtf', 'odt', 'clipboard'].map((id) => ({
        id, channel: id === 'clipboard' ? id : 'file', available: allowed,
        reason: allowed ? null : window.blockReason,
      }));
      return { outcome: 'ok', data: { dictation_id: dictation, release_id: allowed ? 'rel-1' : null,
        released_revision_id: allowed ? 'r1' : null, targets } };
    }
    if (path.includes('/clipboard/r1?')) {
      if (window.holdClipboard) {
        await new Promise((resolve) => { window.finishPrepare = resolve; });
      }
      return { outcome: 'ok', data: { revision_id: 'r1', 'text/plain': 'Freigegebener Testtext.',
        status: 'UNKNOWN', generation: 1 } };
    }
    if (path.includes('/clipboard/r1/outcome?')) {
      return { outcome: 'ok', data: { status: call.body.outcome === 'copied' ? 'succeeded' : 'failed',
        generation: call.body.generation } };
    }
    if (path.endsWith('/exports')) return { outcome: 'ok', data: { id: 'job-1', kind: 'file', status: 'ready' } };
"""


def _capture(page, label: str) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    for width, height in VIEWPORTS:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            _theme(page, theme)
            assert page.evaluate('document.documentElement.scrollWidth') <= width
            for control in page.get_by_test_id('live-transfer').locator('button, select, a').all():
                bounds = control.bounding_box()
                assert bounds is not None and bounds['height'] >= 48 and bounds['width'] >= 48
                assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
            stem = ART / f'live-transfer-{label}-{width}x{height}-{theme}'
            page.screenshot(path=stem.with_suffix('.png'), full_page=True, animations='disabled')
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
            stem.with_suffix('.html').write_text(dom, encoding='utf-8')


class TransferHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == '/api/clinic/v1/dictations/d-100/exports/job-1/download':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Disposition', 'attachment; filename="synthetic.txt"')
            self.end_headers()
            self.wfile.write(b'Freigegebener Testtext.')
        else:
            super().do_GET()


def test_live_transfer_is_explicit_and_leaves_the_live_workspace_open() -> None:
    handler = functools.partial(TransferHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        vue = ROOT / 'static/vendor/js/vue.global.js'
        document = HTML.replace('window.calls = [];', "window.calls = []; window.blockReason = 'not_released';")
        document = document.replace('window.calls.push(call);', 'window.calls.push(call);' + TRANSFER)
        document = document[:document.index('Vue.createApp(LiveView, {')] + """
window.dictationId = Vue.ref('d-100');
Vue.createApp({
  render: () => Vue.h(LiveView, {
    client, dictationId: window.dictationId.value, vue: Vue,
    onReady(controller) { window.live = controller; },
  }),
}).mount('#app');
</script></body></html>"""
        try:
            with sync_playwright() as playwright:
                with playwright.chromium.launch(
                    executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/usr/bin/chromium'),
                ) as browser:
                    page = browser.new_page(permissions=['clipboard-read', 'clipboard-write'])
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/vue.global.js', lambda route: route.fulfill(
                        content_type='text/javascript', path=vue if vue.is_file() else VUE_FALLBACK))
                    page.route('**/live-proof', lambda route: route.fulfill(content_type='text/html', body=document))
                    page.goto(f'http://127.0.0.1:{server.server_port}/live-proof')
                    view = page.get_by_test_id('clinic-live-view')
                    show = page.get_by_role('button', name='Freigegebene Fassung ausgeben')
                    hide = page.get_by_role('button', name='Ausgabemöglichkeiten ausblenden')
                    copy = page.get_by_role('button', name='Text kopieren')
                    generate = page.get_by_role('button', name='Datei vorbereiten')
                    expect(view).to_have_attribute('data-phase', 'idle')
                    assert not page.evaluate('window.calls')
                    show.click()
                    expect(copy).to_be_disabled()
                    expect(generate).to_be_disabled()
                    assert page.evaluate('window.calls.map(c => c.path)') == [
                        '/dictations/d-100/output-targets?file_formats=all&mode=accepted']
                    _capture(page, 'empty')
                    hide.click()
                    page.evaluate("window.blockReason = 'forbidden'")
                    show.click()
                    expect(page.get_by_text('Keine Berechtigung für diese Ausgabe.')).to_be_visible()
                    expect(copy).to_be_disabled()
                    expect(generate).to_be_disabled()
                    hide.click()

                    page.get_by_role('button', name='Starten', exact=True).click()
                    page.get_by_role('button', name='Fortsetzen', exact=True).click()
                    expect(view).to_have_attribute('data-phase', 'live')
                    page.evaluate('''() => window.live.finalizePhrase('Gespeicherter Live-Satz.', {
                        audio_start_ms: 0, audio_end_ms: 500, audio_b64: 'AAAA', audio_revision_id: 'a1',
                        transcript_revision_id: 'r1', text_hash: 'h'})''')
                    row = page.get_by_text('Gespeicherter Live-Satz.')
                    expect(row).to_be_visible()
                    page.evaluate('window.releaseAvailable = true')
                    show.click()
                    expect(copy).to_be_enabled()
                    _capture(page, 'rows')
                    hide.focus()
                    page.keyboard.press('Tab')
                    expect(page.get_by_label('Kopierformat')).to_be_focused()
                    page.keyboard.press('Tab')
                    expect(page.get_by_label('Nach dem Kopieren')).to_be_focused()
                    page.keyboard.press('Tab')
                    expect(copy).to_be_focused()
                    assert copy.evaluate("""(button) =>
                        button.matches(':focus-visible') && getComputedStyle(button).outlineStyle !== 'none'""")
                    assert page.evaluate('navigator.clipboard.readText()') != 'Freigegebener Testtext.'
                    copy.click()
                    expect(page.get_by_text('In die Zwischenablage kopiert.')).to_be_visible()
                    assert page.evaluate('navigator.clipboard.readText()') == 'Freigegebener Testtext.'
                    page.evaluate('''() => {
                        const field = document.createElement('textarea');
                        field.setAttribute('aria-label', 'Testanwendung');
                        document.body.append(field);
                    }''')
                    external_field = page.get_by_role('textbox', name='Testanwendung')
                    expect(external_field).to_have_value('')
                    external_field.press('Control+v')
                    expect(external_field).to_have_value('Freigegebener Testtext.')
                    external_field.evaluate('(field) => field.remove()')
                    assert page.evaluate("window.calls.filter(c => c.path?.includes('/clipboard/')).map(c => c.path)") == [
                        '/dictations/d-100/clipboard/r1?format=plain&release_id=rel-1&live=1',
                        '/dictations/d-100/clipboard/r1/outcome?release_id=rel-1&live=1',
                    ]
                    with page.expect_download() as requested:
                        generate.click()
                        expect(page.get_by_text('Datei erzeugt')).to_be_visible()
                        page.get_by_role('link', name='Datei herunterladen').click()
                    assert requested.value.path().read_bytes() == b'Freigegebener Testtext.'
                    assert page.evaluate("window.calls.find(c => c.path?.endsWith('/exports')).body") == {
                        'revision_id': 'r1', 'release_id': 'rel-1', 'format': 'txt',
                    }
                    expect(page.get_by_text('Download gestartet')).to_be_visible()
                    expect(view).to_have_attribute('data-phase', 'live')
                    expect(row).to_be_visible()

                    page.evaluate("""async () => {
                        await navigator.clipboard.writeText('Unverändert');
                        window.holdClipboard = true;
                    }""")
                    copy.click()
                    page.wait_for_function('window.finishPrepare !== undefined')
                    page.evaluate("window.dictationId.value = 'd-200'")
                    expect(show).to_be_visible()
                    show.click()
                    expect(copy).to_be_disabled()
                    expect(generate).to_be_disabled()
                    expect(page.get_by_role('link', name='Datei herunterladen')).to_have_count(0)
                    page.evaluate('window.finishPrepare()')
                    page.wait_for_function("""window.calls.filter(c => c.path?.includes('/clipboard/r1/outcome')).length === 2""")
                    assert page.evaluate('navigator.clipboard.readText()') == 'Unverändert'
                    assert page.evaluate("window.calls.filter(c => c.path?.includes('/outcome')).at(-1).body") == {
                        'outcome': 'denied', 'generation': 1,
                    }
                    assert not errors, errors
        finally:
            server.shutdown()
            thread.join()
