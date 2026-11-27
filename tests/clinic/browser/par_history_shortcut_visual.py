"""Synthetic Strg+I Verlauf proof. Not a device, Entra or KIS proof.

Run from the worktree root:
venv/bin/python tests/clinic/browser/par_history_shortcut_visual.py
"""
from functools import partial
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / '.wp' / 'artifacts'
D = '11111111-1111-4111-8111-111111111111'
HTML = r'''<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verlauf Strg+I</title>
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/clinic-lists.css">
<body class="v8-ui"><main id="app" style="padding:16px"></main>
<script src="/static/vendor/js/vue.global.js"></script>
<script type="module">
import { WorklistView } from '/static/js/clinic/compose-worklist.js';
import { DetailView } from '/static/js/clinic/compose-detail.js';
const params = new URLSearchParams(location.search);
const state = params.get('state') || 'rows';
const view = params.get('view') || 'worklist';
document.documentElement.classList.toggle('dark', params.get('theme') === 'dark');
if (params.get('theme') === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
const D = '__D__';
const row = {dictation_id:D, status:'zu_schreiben', priority:1, duration_seconds:95,
  author_user_id:7, revision:3, etag:'"d:3"', allowed_actions:['assign'],
  dictated_by:{display_name:'Dr. Synthetisch'}, staff_pools:[{name:'Schreibdienst Staging'}],
  created_at:'2026-11-20T08:15:00Z', source_kind:'device'};
const assigned = {kind:'routing', id:'r1', action:'work_state_transferred', at:'2026-11-20T09:00:00Z', actor_label:'A. Muster'};
const events = state === 'empty' ? [] : [assigned];
const ok = (data) => ({outcome:'ok', status:200, data});
const client = {dictationChanges:0, switchContext(){}, async request(path, options){
  if (path.includes('/history')) return state === 'denied'
    ? {outcome:'permission', status:403} : ok({dictation_id:D, events});
  if (path.includes('/detail')) return ok({dictation_id:D, revision:3, etag:'"d:3"',
    author:{display_name:'Dr. Synthetisch'}, staff_pools:[{name:'Schreibdienst Staging'}],
    recording_time:{at:'2026-11-20T08:15:00Z', source:'recorded'}, duration_seconds:95,
    priority:1, allowed_actions:[], status_axes:{processing:'SUCCEEDED', workflow:'zu_schreiben',
    acceptance:'QUEUED', output:'NOT_RELEASED'}});
  if (path.includes('/audio-revisions')) return {outcome:'not_found', status:404};
  if (path.includes('/leases')) return ok({held:false, holder_user_id:null, may_acquire:true});
  if (path.includes('/context')) return {outcome:'permission', status:503};
  if (path.startsWith('/worklist/rows')) return ok({items: state === 'empty' && view === 'worklist' ? [] : [row], total: state === 'empty' && view === 'worklist' ? 0 : 1});
  if (path.startsWith('/worklist/layout')) return ok({version:1, layout:{version:1, columns:[], sort:[], group_by:[]}});
  if (path.startsWith('/worklist/filters')) return ok({items:[]});
  return ok({items:[]});
}};
const user = {id:7, role:'staff'};
const app = view === 'detail'
  ? Vue.createApp({components:{DetailView}, setup(){return {client, user, vue:Vue}},
      template:'<DetailView :client="client" dictation-id="'+D+'" :current-user="user" :vue="vue" />'})
  : Vue.createApp(WorklistView, {client, currentUser:user, vue:Vue, intervalMs:0});
app.mount('#app');
</script></body></html>'''.replace('__D__', D)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/':
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    failures = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'],
                                        executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None)
            for view in ('worklist', 'detail'):
                for state in ('rows', 'empty', 'denied'):
                    for width, height in ((1440, 900), (1280, 720), (390, 844)):
                        for theme in ('light', 'dark'):
                            page = browser.new_page(viewport={'width': width, 'height': height}, color_scheme=theme)
                            errors = []
                            page.on('pageerror', lambda e: errors.append(str(e)))
                            page.goto(f'{base}/?view={view}&state={state}&theme={theme}')
                            page.locator('[data-testid="clinic-worklist-view"], [data-testid="clinic-detail-view"]').wait_for()
                            if view == 'worklist' and state != 'empty':
                                page.locator('[data-row]').first.click()
                                page.locator('[data-testid="history-panel"]').wait_for()
                                page.keyboard.press('Control+i')
                            elif view == 'detail':
                                page.locator('[data-testid="history-panel"]').wait_for()
                                page.locator('body').click()
                                page.keyboard.press('Control+i')
                            page.wait_for_timeout(250)
                            name = f'history-{view}-{state}-{width}x{height}-{theme}.png'
                            page.screenshot(path=str(OUT / name), full_page=True)
                            overflow = page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth + 1')
                            if errors or overflow:
                                failures.append(f'{name}: errors={errors} overflow={overflow}')
                            else:
                                print('PASS', name)
                            page.close()
            browser.close()
    finally:
        server.shutdown()
    if failures:
        raise SystemExit('\n'.join(failures))


if __name__ == '__main__':
    main()
