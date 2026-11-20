"""G7 browser proof with synthetic API responses; no ASR or enrollment proof."""

import functools
import http.server
import os
from pathlib import Path
import threading

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
HTML = """<!doctype html><html lang="de"><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/clinic-admin.css">
<link rel="stylesheet" href="/static/css/tailwind.css">
<script src="/static/vendor/js/vue.global.js"></script></head>
<body class="v8-ui"><main id="app" class="v8-admin-page"></main><script type="module">
import { AsrProfilesView } from '/static/js/clinic/compose-asr-profiles.js';
let consent = {state:'none', latest_consent_id:0, embedding_count:0, history:[]};
window.writes = [];
const client = {switchContext() {}, async request(path, options = {}) {
  if (options.method) window.writes.push({path, ...options});
  if (window.deny) return {outcome:'permission', message:'Kein Zugriff.'};
  if (path === '/asr-profiles') return {outcome:'ok', data:{version:4,
    profile:{id:'p',name:'Klinik Deutsch',version:7}, source:'headset',
    profiles:[{id:'l',name:'Deutsch live',mode:'live'},{id:'b',name:'Deutsch Stapel',mode:'batch'}],
    live_profile_id:'l', batch_profile_id:'b',
    engine:{configured:'gpu', actual:'cpu'}, prompts_enabled:false,
    output_format:{container:'wav',codec:'pcm_s16le',sample_rate:16000},
    capabilities:{configured:['batch','live','negative_lexicon','alignment','local_tts'],proven:['batch']}}};
  if (path.endsWith('/grant')) consent = {...consent,state:'granted',latest_consent_id:1};
  if (path.endsWith('/revoke')) consent = {...consent,state:'revoked',latest_consent_id:2};
  return {outcome:'ok',data:consent};
}};
Vue.createApp({components:{AsrProfilesView}, setup:()=>({client,user:{id:1}}),
  template:'<AsrProfilesView :client="client" :current-user="user" />'}).mount('#app');
</script></body></html>"""


def test_g7_asr_consent_desktop_mobile_and_denied(tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proofs = Path(os.environ.get('V8_150B_PROOFS', str(tmp_path)))
    proofs.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/g7-proof', lambda route: route.fulfill(content_type='text/html', body=HTML))
            page.goto(f'http://127.0.0.1:{server.server_port}/g7-proof')
            expect(page.get_by_role('heading', name='Profilkonfiguration')).to_be_visible()
            expect(page.get_by_role('cell', name='Negativlexikon', exact=True)).to_be_visible()
            expect(page.get_by_role('cell', name='Lokale Sprachausgabe', exact=True)).to_be_visible()
            expect(page.get_by_text('Ausweichlösung aktiv: CPU statt GPU.', exact=True)).to_be_visible()
            page.screenshot(path=str(proofs / 'asr-consent-desktop.png'), full_page=True)
            page.get_by_role('radio', name='Headset').focus()
            page.keyboard.press('ArrowRight')
            expect(page.get_by_role('radio', name='Aufnahmegerät')).to_be_focused()
            page.get_by_role('button', name='Übernehmen', exact=True).click()
            assert page.evaluate('window.writes[0].body') == {
                'source': 'recorder', 'live_profile_id': 'l', 'batch_profile_id': 'b', 'expected_version': 4,
            }
            page.get_by_role('button', name='Einwilligen', exact=True).click()
            expect(page.locator('[data-testid=voice-status]')).to_have_text('Einwilligung erteilt')
            page.get_by_role('button', name='Einwilligung widerrufen', exact=True).click()
            expect(page.get_by_role('alertdialog')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.get_by_role('alertdialog')).to_have_count(0)
            page.get_by_role('button', name='Einwilligung widerrufen', exact=True).click()
            page.get_by_role('button', name='Widerruf bestätigen', exact=True).click()
            expect(page.locator('[data-testid=voice-status]')).to_have_text('Einwilligung widerrufen')
            assert [w['path'] for w in page.evaluate('window.writes')] == [
                '/asr-profiles/selection', '/voice-consent/grant', '/voice-consent/revoke',
            ]
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(proofs / 'asr-consent-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.add_init_script('window.deny = true')
            page.reload()
            expect(page.get_by_role('button', name='Einwilligen', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Übernehmen', exact=True)).to_be_disabled()
            expect(page.locator('[data-testid=voice-status]')).to_have_text('Einwilligungsstand nicht geladen')
            page.screenshot(path=str(proofs / 'asr-consent-denied.png'), full_page=True)
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
