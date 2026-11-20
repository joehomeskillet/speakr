"""Local P03 component proof with synthetic transport; not a live KIS proof.

Run from the worktree root: venv/bin/python tests/clinic/browser/context_bind_visual.py
"""
from functools import partial
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'evidence/v8-136'
PROTOTYPE = Path('/root/work/wp-backlog/ui-v8/03_Prototyp')
HTML = '''<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P03 synthetic context proof</title>
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/clinic-lists.css">
<body><main id="app" class="v8-ui" style="padding:24px" data-current-user-id="1"></main>
<script src="/static/vendor/js/vue.global.js"></script>
<script type="module">
import { DetailView } from '/static/js/clinic/compose-detail.js';
let bound = false;
let held = false;
window.posts = 0;
const ok = (data, status=200) => ({outcome:'ok',status,data});
const client = {async request(path, options) {
  if (path.endsWith('/detail')) return ok({dictation_id:'d1',revision:bound?2:1,
    author:{display_name:'Dr. Synthetisch'},staff_pools:[{name:'Schreibdienst Staging'}],
    recording_time:{at:'2026-11-20T09:00:00Z',source:'recorded'},duration_seconds:75,
    priority:128,case_display:bound?'SYN-0001':null,allowed_actions:[],
    status_axes:{processing:'SUCCEEDED',workflow:'zu_korrigieren',acceptance:'QUEUED',output:'NOT_RELEASED'}});
  if (path.endsWith('/context')) {
    if (window.noCapability) return {outcome:'permission',status:503};
    if (options?.method==='POST') {window.posts++;bound=true;}
    return ok({case_display:bound?'SYN-0001':null},options?.method==='POST'?201:200);
  }
  if (path.endsWith('/leases') && !options?.method) return ok({held,holder_user_id:held?1:null,may_acquire:true});
  if (path.includes('/leases')) {held=true;return ok({lease_id:'l1',generation:1,expires_at:new Date(Date.now()+600000).toISOString()});}
  return ok({items:[]});
}};
Vue.createApp({components:{DetailView},setup(){return {client,vue:Vue,user:{id:1}}},
  template:'<DetailView :client="client" dictation-id="d1" :current-user="user" :vue="vue" />'}).mount('#app');
</script></body></html>'''


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def translate_path(self, path):
        if path.startswith('/prototype/'):
            target = (PROTOTYPE / path.removeprefix('/prototype/').split('?')[0]).resolve()
            if target.is_relative_to(PROTOTYPE):
                return str(target)
        return super().translate_path(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'],
                                        executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                for theme in ['light', 'dark']:
                    page = browser.new_page(viewport={'width': width, 'height': height}, color_scheme=theme)
                    errors = []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto(base)
                    page.evaluate('(dark) => document.documentElement.classList.toggle("dark", dark)', theme == 'dark')
                    form = page.get_by_test_id('context-bind')
                    form.wait_for()
                    field = form.get_by_role('textbox', name='Synthetische Fall-ID')
                    assert field.is_disabled()
                    assert 'Bearbeitung zuerst anfordern.' in form.inner_text()
                    page.get_by_role('button', name='Bearbeiten', exact=True).click()
                    field.wait_for(state='visible')
                    field.fill('SYN-0001')
                    field.press('Tab')
                    assert page.get_by_role('button', name='Fall-ID verknüpfen', exact=True).evaluate('(el) => el === document.activeElement')
                    stem = f'{width}x{height}-{theme}'
                    page.screenshot(path=str(OUT / f'p03-{stem}.png'), full_page=True)
                    page.keyboard.press('Enter')
                    page.get_by_test_id('case-id').filter(has_text='SYN-0001').wait_for()
                    assert page.evaluate('window.posts') == 1
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    assert not errors, errors
                    page.screenshot(path=str(OUT / f'p03-bound-{stem}.png'), full_page=True)
                    print(f'PASS P03 {stem}: lease guard, keyboard bind, confirmed Fall-ID, no overflow or JS error')
                    page.goto(base + '/prototype/index.html#aufnahme/4524')
                    page.screenshot(path=str(OUT / f'prototype-{stem}.png'), full_page=True)
                    page.close()
            page = browser.new_page()
            page.add_init_script('window.noCapability = true')
            page.goto(base)
            page.get_by_test_id('detail-header').wait_for()
            page.wait_for_load_state('networkidle')
            assert page.get_by_test_id('context-bind').count() == 0
            print('PASS unavailable context: no simulation offered')
            browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
