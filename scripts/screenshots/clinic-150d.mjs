/** Synthetic component evidence; no staging credentials, backend or hardware claims.
 * PLAYWRIGHT_MODULE must point to an existing Playwright module. No install needed.
 */
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const root = process.cwd();
const out = process.env.VISUAL_OUTPUT || '/tmp/speakr-v8-150d';
await mkdir(out, { recursive: true });
const html = `<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
${['tokens','styles','primitives','clinic-v8','tailwind'].map(n => '<link rel="stylesheet" href="/static/css/'+n+'.css">').join('')}
</head><body><main class="v8-ui" style="padding:20px"><div id="app"></div></main><script src="/static/vendor/js/vue.global.js"></script>
<script type="module">
import {createJobsPanel} from '/static/js/clinic/jobs-ui.js';
import {createWorkflowActions, WORKFLOW_ACTIONS_TEMPLATE} from '/static/js/clinic/workflow-actions-ui.js';
import {DictationProtectionComponent} from '/static/js/clinic/dictation-protection-ui.js';
import {PedalKeyCapture} from '/static/js/clinic/pedal-ui.js';
const calls = window.calls = [];
const api = {request: async (url, options) => { calls.push({url,options});
 if (url === '/jobs/health') return {outcome:'ok',data:{counts:{queued:2,processing:1,completed:4,failed:2},stuck_ids:[8],recent_failures:[
 {id:4,job_type:'transcribe',error_class:'TIMEOUT',message:'Zeitlimit erreicht',version:1},
 {id:5,job_type:'clinic_output',error_class:'UNKNOWN',message:'Abgleich erforderlich',version:1}]}};
 return {outcome:'ok',data:{protected:options?.body?.secret !== null,version:2}};
}, command: async (body) => {calls.push({body});return {outcome:'ok',data:{}}}};
const wf = window.wf = createWorkflowActions({client:api,reactive:Vue.reactive});
wf.load({dictationId:'synthetic',revision:2,etag:'2',allowedActions:['submit','accept','reject','reopen','release','archive'],asrStatus:'completed',workState:'processing',binding:{}});
const jobs = createJobsPanel(Vue,{api,canRetry:true});
const app = Vue.createApp({setup(){return {api}},template:'<div class="v8-page stack"><jobs/><workflow/><protection :client="api" dictation-id="synthetic"/><section class="panel"><h2>Fußpedal</h2><pedal @capture="code => captured = code"/></section></div>',data(){return {captured:null}}});
app.component('jobs',jobs.component);
app.component('workflow',{setup(){return {wf}},template:WORKFLOW_ACTIONS_TEMPLATE});
app.component('protection',DictationProtectionComponent(Vue));
app.component('pedal',PedalKeyCapture);
app.mount('#app'); window.ready = true;
</script></body></html>`;
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE});
const results = [];
try {
 for (const width of [1440,390]) for (const dark of [false,true]) {
  const page = await browser.newPage({viewport:{width,height:900}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://clinic.test/**',async route => {
   const name = new URL(route.request().url()).pathname;
   if (name === '/') return route.fulfill({contentType:'text/html',body:html});
   assert(name.startsWith('/static/'));
   const type = name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'application/octet-stream';
   await route.fulfill({contentType:type,body:await readFile(path.join(root,name))});
  });
  await page.goto('https://clinic.test/');
  await page.waitForFunction(()=>window.ready);
  await page.evaluate(d=>document.documentElement.classList.toggle('dark',d),dark);
  await page.getByRole('button',{name:'Endgültig freigeben',exact:true}).click();
  await page.getByRole('button',{name:'Bestätigen',exact:true}).click();
  assert.match(await page.locator('.clinic-workflow-actions').innerText(),/Inhalt und Kontext müssen bestätigt werden/);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.url?.endsWith('/releases')).length),0);
  await page.getByLabel('Inhalt geprüft').check();
  await page.getByLabel('Kontext geprüft').check();
  await page.screenshot({path:path.join(out,`family-${width}-${dark?'dark':'light'}.png`),fullPage:true});
  await page.getByLabel('Schutzcode',{exact:true}).fill('synthetic-test-code');
  await page.getByLabel('Ich bestätige, dass der Schutz für dieses Diktat gesetzt wird.').check();
  await page.getByRole('button',{name:'Schutz aktivieren',exact:true}).click();
  assert.equal(await page.getByLabel('Schutzcode',{exact:true}).inputValue(),'');
  await page.getByRole('button',{name:'Entsperren',exact:true}).waitFor();
  await page.locator('.clinic-dictation-protection').screenshot({path:path.join(out,`protected-${width}-${dark?'dark':'light'}.png`)});
  await page.getByLabel('Schutzcode',{exact:true}).fill('synthetic-test-code');
  await page.getByRole('button',{name:'Entsperren',exact:true}).click();
  assert.equal(await page.getByLabel('Schutzcode',{exact:true}).inputValue(),'');
  await page.getByLabel('Ich bestätige, dass der Schutz für dieses Diktat entfernt wird.').check();
  await page.getByRole('button',{name:'Schutz entfernen',exact:true}).click();
  await page.getByRole('button',{name:'Schutz aktivieren',exact:true}).waitFor();
  await page.getByRole('button',{name:'Abbrechen',exact:true}).click();
  await page.getByRole('button',{name:'Zur Korrektur zurückweisen',exact:true}).click();
  await page.getByLabel('Begründung').fill('Synthetischer Prüfhinweis');
  await page.getByRole('button',{name:'Bestätigen',exact:true}).click();
  assert.equal(await page.evaluate(()=>calls.find(c=>c.body)?.body.payload.reason),'Synthetischer Prüfhinweis');
  await page.getByRole('button',{name:'Pedaltaste erfassen',exact:true}).click();
  await page.keyboard.press('F8');
  assert.match(await page.locator('.clinic-pedal-capture').innerText(),/Taste erfasst: F8/);
  await page.getByRole('button',{name:'Abgleich prüfen, Auftrag 5',exact:true}).click();
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.url?.includes('/retry')).length),0);
  await page.evaluate(()=>wf.load({allowedActions:[]}));
  assert.equal(await page.locator('.clinic-workflow-actions button').count(),0);
  const dimensions=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
  assert(dimensions.document<=width,JSON.stringify(dimensions));
  assert.deepEqual(errors,[]);
  results.push({width,dark,checks:'release confirmations, reject payload, protection enable/unlock/remove and secret clearing, pedal capture, UNKNOWN no retry, no allowed actions, no overflow, no page errors'});
  await page.close();
 }
 if (process.env.PROTOTYPE_DIR) {
  for (const width of [1440,390]) {
   const page=await browser.newPage({viewport:{width,height:900}});
   for (const name of ['verarbeitung','freigabe','schreibplatz']) {
    await page.goto(pathToFileURL(path.join(process.env.PROTOTYPE_DIR,name+'.html')).href);
    await page.screenshot({path:path.join(out,`prototype-${name}-${width}.png`),fullPage:true});
   }
   await page.close();
  }
 }
 await writeFile(path.join(out,'results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results,null,2));
} finally {await browser.close();}
