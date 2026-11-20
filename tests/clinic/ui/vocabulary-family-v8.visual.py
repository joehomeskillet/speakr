"""Local synthetic browser proof. Run with python3; no staging or credentials."""
import functools
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = Path('/tmp/speakr-v8-150a')
HTML = '''<!doctype html><html lang="de-CH"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<body class="v8-ui"><main id="app" style="padding:20px;max-width:1100px;margin:auto"></main>
<script src="/static/vendor/js/vue.global.js"></script><script type="module">
import { VocabularyView } from '/static/js/clinic/compose-vocabulary.js';
import { createVoiceCommandsComponent } from '/static/js/clinic/voice-commands-ui.js';
const ok = data => ({outcome:'ok', data});
window.calls = [];
window.empty = false;
const client = {
  switchContext() {},
  async request(path, opts) {
    window.calls.push({path, opts});
    if (path.startsWith('/vocabulary/search')) return ok({entries:window.empty ? [] : [
      {id:'e1',written:'Frühdienstübergabe',spoken:'frühdienst übergabe',status:'active',version:'1'},
      {id:'e2',written:'Beispielausdruck',spoken:null,status:'suppressed',version:'2'}]});
    if (path.startsWith('/resources/vocabulary/reviews')) return ok({revision:1,items:[
      {id:'proposal-1',written_form:'Teamnotiz',spoken_form:'team notiz',status:'PENDING',revision:1}]});
    if (path === '/pronunciations/capabilities') return ok({training_proven:false});
    return ok({items:[]});
  },
  async command(body) {
    window.calls.push({body});
    if (body.payload.decision === 'pool') return {outcome:'permission',message:'Keine Berechtigung für den Autorenpool.'};
    return ok({revision:2});
  }
};
const voice = location.search.includes('voice');
const component = voice ? createVoiceCommandsComponent(Vue) : VocabularyView;
Vue.createApp(component, {client, currentUser:{id:7}, scope:{type:'user',id:7}}).mount('#app');
</script></html>'''


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    OUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for width in (1280, 390):
                page = browser.new_page(viewport={'width': width, 'height': 900})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.route('**/proof*', lambda route: route.fulfill(content_type='text/html', body=HTML))
                url = f'http://127.0.0.1:{server.server_port}/proof'
                page.goto(url)
                page.get_by_text('Frühdienstübergabe', exact=True).wait_for()
                assert page.locator('#voc-mode option:checked').inner_text() == 'Enthält'
                assert page.locator('#voc-filter option:checked').inner_text() == 'Aktiv'
                assert page.locator('#voc-q').evaluate('(e) => getComputedStyle(e).minHeight') == '38px'
                page.locator('#voc-q').fill('Herz')
                page.locator('#voc-q').press('Enter')
                page.wait_for_function("calls.some(c => c.path?.includes('q=Herz'))")
                assert page.evaluate("calls.filter(c => c.path?.includes('q=Herz')).length") == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(OUT / f'words-{width}.png'), full_page=True, animations='disabled')
                if width == 1280:
                    page.locator('html').evaluate("(e) => e.classList.add('dark')")
                    page.screenshot(path=str(OUT / 'words-dark.png'), full_page=True, animations='disabled')
                    page.locator('html').evaluate("(e) => e.classList.remove('dark')")
                    page.locator('html').evaluate("(e) => e.style.zoom = '2'")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(OUT / 'words-zoom-200.png'), full_page=True, animations='disabled')
                    page.locator('html').evaluate("(e) => e.style.zoom = ''")
                page.evaluate('window.empty = true')
                page.get_by_role('button', name='Aktuellen Stand laden', exact=True).click()
                page.get_by_text('Keine Einträge für diese Auswahl.', exact=False).wait_for()
                page.screenshot(path=str(OUT / f'empty-{width}.png'), full_page=True, animations='disabled')
                page.get_by_role('tab', name='Vorschläge', exact=True).click()
                page.get_by_text('Teamnotiz', exact=True).wait_for()
                page.get_by_role('button', name='Für den Pool übernehmen', exact=True).click()
                assert page.get_by_role('button', name='Für den Pool übernehmen', exact=True).is_disabled()
                assert page.get_by_role('button', name='Für mich übernehmen', exact=True).is_enabled()
                page.get_by_role('button', name='Zurückstellen', exact=True).click()
                assert 'Teamnotiz · Zurückgestellt · Zurückgestellt' in page.get_by_role('log').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(OUT / f'review-{width}.png'), full_page=True, animations='disabled')
                page.goto(url + '?voice')
                page.get_by_text('Aussprachetraining nicht verfügbar.', exact=False).wait_for()
                page.locator('#vc-phrase').fill('Diktat freigeben')
                page.locator('#vc-steps').fill('invalid_action')
                page.get_by_role('button', name='Speichern', exact=True).click()
                assert 'Eine Aktion ist unbekannt.' in page.locator('section').inner_text()
                assert 'unknown_action' not in page.locator('section').inner_text()
                page.screenshot(path=str(OUT / f'command-error-{width}.png'), full_page=True, animations='disabled')
                page.locator('#vc-steps').fill('release_dictation')
                page.get_by_role('button', name='Speichern', exact=True).click()
                assert page.evaluate('calls.filter(c => c.body).length') == 0
                page.get_by_role('button', name='Aktion bestätigen', exact=True).click()
                page.get_by_role('button', name='Speichern', exact=True).click()
                page.get_by_text('Gespeichert.', exact=True).wait_for()
                assert page.evaluate('calls.filter(c => c.body)[0].body.payload.action_ids') == ['release_dictation']
                page.screenshot(path=str(OUT / f'commands-{width}.png'), full_page=True, animations='disabled')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                print(f'PASS {width}px: real Vue mounts, search once, empty state, review denial/history, German validation, confirmation guard, no page errors')
                page.close()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            for name in ('wortschatz', 'befehle-vorlagen'):
                page.goto('file:///root/work/wp-backlog/ui-v8/03_Prototyp/' + name + '.html')
                page.screenshot(path=str(OUT / ('prototype-' + name + '.png')), full_page=True, animations='disabled')
            browser.close()
    finally:
        server.shutdown()
    print(f'Visual proofs: {OUT}')


if __name__ == '__main__':
    main()
