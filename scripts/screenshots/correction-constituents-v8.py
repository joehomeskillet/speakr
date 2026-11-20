"""Offline, synthetic browser proof for v8-150c. No credentials or server data.

Run with the repository venv Python. Saves screenshots and checks real Vue
components at desktop/mobile sizes. Network is intercepted, never forwarded.
"""
import argparse
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
HTML = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<style>body{margin:0;font-family:Arial,sans-serif}main{padding:20px;max-width:900px;margin:auto}</style>
<body class="v8-ui"><main id="app" class="v8-page stack">
<h1>Schreibplatz · synthetischer Komponententest</h1>
<section class="panel"><audio-markers :editor="editor" :client="client" dictation-id="demo" :seek="seek"></audio-markers></section>
<section class="panel"><correction-modes :audio="audio" :client="client"></correction-modes></section>
<section class="panel"><correction-modes :audio="audio" :client="client"></correction-modes></section>
<section class="panel"><correction-alignment :editor="editor" :client="client" dictation-id="demo" :seek="seek"></correction-alignment></section>
</main><script src="/static/vendor/js/vue.global.js"></script><script type="module">
import {AudioMarkers} from '/static/js/clinic/audio-markers-ui.js';
import {CorrectionModes} from '/static/js/clinic/correction-modes-ui.js';
import {CorrectionAlignment} from '/static/js/clinic/correction-alignment-ui.js';
const {ref,createApp}=Vue;
const text='Fiktives Beispiel ohne Patientenbezug.';
const editor={text:ref(text),textRevisionId:ref('text-1'),audio:ref({status:'ready',revisionId:'audio-1'}),
  lease:ref({generation:1,writable:true}),canEdit:ref(true)};
const marker=(id,revision,label)=>({id,marker_type:'SECTION',position_ms:1000,label,
  audio_revision_id:revision,version:1,deleted:false});
const markers=[marker('current','audio-1','<img src=x onerror=alert(1)> Nur synthetischer Text.'),
  marker('old','audio-0','Frühere Prüfmarke')];
window.calls=[];window.positions=[];window.editor=editor;
const client={async request(path,options={}){
  window.calls.push({path,options});
  if(path.includes('/preferences/')) return {outcome:'ok',status:200,data:{mode:'stop_on_typing'}};
  if(path.endsWith('/audio-revisions/current')) return {outcome:'ok',status:200,data:{revision_id:'audio-1',segments:[]}};
  if(path.includes('/alignment')) return {outcome:'ok',status:200,data:{state:'unaligned'}};
  return {outcome:'ok',status:200,data:{dictation_id:'demo',text_revision_id:'text-1',text,markers,
    links:[{id:'word-1',origin:'asr',text_start:0,text_end:8,audio_start_ms:0,audio_end_ms:500}]}};
}};
const audio={play(){},pause(){},seek(){},position:()=>0,isPlaying:()=>false};
createApp({components:{AudioMarkers,CorrectionModes,CorrectionAlignment},
  setup:()=>({editor,client,audio,seek:(seconds)=>window.positions.push(seconds)})}).mount('#app');
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='/tmp/speakr-v8-150c')
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request):
            from urllib.parse import urlparse
            url = urlparse(request.request.url)
            if url.netloc != 'clinic.test':
                request.abort()
                return
            if url.path == '/':
                request.fulfill(body=HTML, content_type='text/html')
                return
            path = ROOT / url.path.lstrip('/')
            if not url.path.startswith('/static/') or '..' in Path(url.path).parts:
                request.abort()
                return
            mime = 'text/javascript' if path.suffix == '.js' else 'text/css'
            request.fulfill(body=path.read_bytes(), content_type=mime)

        page.route('**/*', route)
        page.goto('http://clinic.test/')
        current = page.locator('[data-ownership=current]')
        historical = page.locator('[data-ownership=historical]')
        expect(current).to_be_visible()
        expect(historical.locator('button')).to_have_count(0)
        expect(current.locator('img')).to_have_count(0)
        expect(current).to_contain_text('<img src=x onerror=alert(1)>')
        current.get_by_role('button', name='Zur Marke springen', exact=True).click()
        assert page.evaluate('window.positions') == [1]
        groups = page.get_by_role('radiogroup')
        groups.nth(0).get_by_role('radio', name='Beim Tippen weiterlaufen').check()
        expect(groups.nth(1).get_by_role('radio', name='Auf zu hörendes Wort zeigen')).to_be_checked()
        groups.nth(0).get_by_role('radio', name='Beim Tippen weiterlaufen').focus()
        page.keyboard.press('ArrowLeft')
        expect(groups.nth(0).get_by_role('radio', name='Auf zu hörendes Wort zeigen')).to_be_checked()
        assert page.evaluate("new Set([...document.querySelectorAll('[id]')].map(e=>e.id)).size === document.querySelectorAll('[id]').length")
        word = page.get_by_role('button', name='Wort abspielen: Fiktives', exact=True)
        word.focus()
        page.keyboard.press('Enter')
        assert page.evaluate('window.positions') == [1, 0]
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1080})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / f'constituents-{width}.png'), full_page=True)
        page.evaluate('window.editor.canEdit.value=false')
        expect(page.get_by_role('button', name='Abschnittsmarke setzen', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Marke entfernen', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Zur Marke springen', exact=True)).to_be_enabled()
        page.screenshot(path=str(output / 'readonly-390.png'), full_page=True)
        assert not errors, errors
        assert page.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
        browser.close()
    print('PASS: Vue render, desktop/mobile overflow, escaped labels, historical/read-only controls, independent radios, keyboard seek, no browser storage, no browser errors.')
    print(f'Screenshots: {output}')


if __name__ == '__main__':
    main()
