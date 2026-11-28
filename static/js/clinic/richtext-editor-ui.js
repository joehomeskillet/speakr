/**
 * Canonical rich text editor for the clinic fork (schema v1, see
 * src/clinic/richtext.py). Nodes and runs keep their IDs through edits and
 * selections. Text edits and insertions never carry an audio anchor, so they
 * stay unaligned. Pasted text is reduced to safe plain text. Export losses are
 * shown before export. Clinical text lives in memory only: nothing is written
 * to browser storage. Colours come from tokens.js, requests from api-client.js.
 */

import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const FORMATS = Object.freeze(['bold', 'italic', 'underline']);

const MAX_TEXT = 1000000;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const GRAPHIC_ID = /^[a-f0-9]{64}$/;
const LAYOUT = [
    { key: 'font_family', label: 'Schriftart', run: true,
        choices: [['', 'Standard'], ['serif', 'Serifenschrift'], ['sans-serif', 'Schlichte Schrift'], ['monospace', 'Feste Zeichenbreite']] },
    { key: 'text_align', label: 'Ausrichtung',
        choices: [['', 'Standard'], ['left', 'Links'], ['center', 'Mittig'], ['right', 'Rechts'], ['justify', 'Blocksatz']] },
    { key: 'line_spacing', label: 'Zeilenabstand',
        choices: [['', 'Standard'], [1, 'Einfach'], [1.15, '1,15'], [1.5, '1,5'], [2, 'Doppelt'], [2.5, '2,5'], [3, 'Dreifach']] },
];
const targetSize = { minWidth: '48px', minHeight: '48px' };
const fieldStyle = { ...targetSize, border: '1px solid var(--v8-text-2)' };

const DEFAULTS = {
    'clinic.richtext.toolbar': 'Textformatierung',
    'clinic.richtext.bold': 'Fett',
    'clinic.richtext.italic': 'Kursiv',
    'clinic.richtext.underline': 'Unterstrichen',
    'clinic.richtext.insert': 'Abschnitt einfügen',
    'clinic.richtext.save': 'Speichern',
    'clinic.richtext.retry': 'Erneut versuchen',
    'clinic.richtext.reload': 'Aktuellen Stand laden',
    'clinic.richtext.run': 'Textabschnitt {run} von Absatz {node}',
    'clinic.richtext.unaligned': 'nicht am Audio ausgerichtet',
    'clinic.richtext.losses': 'Beim Export gehen Formate verloren',
    'clinic.richtext.pasteCleaned': 'Eingefügter Text wurde bereinigt.',
    'clinic.richtext.saved': 'Gespeichert.',
    'clinic.richtext.stale': 'Der Stand ist veraltet. Die Änderungen sind noch lokal vorhanden.',
    'clinic.richtext.denied': 'Keine Berechtigung zum Bearbeiten.',
    'clinic.richtext.failed': 'Speichern fehlgeschlagen. Die Änderungen sind noch lokal vorhanden.',
};

// Existing i18n mechanism (window.i18n.t); German defaults when a key is missing.
export function label(key, params) {
    let text = DEFAULTS[key] || key;
    try {
        const translated = typeof window !== 'undefined' && window.i18n ? window.i18n.t(key, params) : null;
        if (typeof translated === 'string' && translated && translated !== key) text = translated;
    } catch (e) { /* keep default */ }
    return Object.entries(params || {}).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), text);
}

/** Plain-text paste only: no markup, no control characters, no lone surrogates. */
export function sanitizePaste(input) {
    const raw = typeof input === 'string' ? input : '';
    const text = raw
        .replace(/<[^<>]*>/g, '')
        .replace(/<(?=[A-Za-z/!?])/g, '')
        .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
        .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, '')
        .slice(0, MAX_TEXT);
    return { text, changed: text !== raw };
}

/** Same conditions as _export_losses in src/clinic/richtext_formats.py. */
export function exportLosses(doc) {
    const nodes = (doc && doc.nodes) || [];
    const runs = nodes.flatMap((n) => n.runs);
    const out = [];
    if (nodes.some((n) => n.kind === 'heading')) out.push({ code: 'heading', message: 'Überschrift wird ausser im Editor als Absatz exportiert.' });
    if (runs.some((r) => r.underline)) out.push({ code: 'underline', message: 'Unterstreichung entfällt in RTF, ODT und DOCX.' });
    if (nodes.some((n) => n.kind === 'list_item')) out.push({ code: 'list_plain', message: 'Liste wird in ODT und DOCX als Absatz geführt.' });
    if (runs.some((r) => r.bold || r.italic)) out.push({ code: 'odt_marks', message: 'ODT übernimmt Fett und Kursiv nicht.' });
    if (out.length) out.push({ code: 'plain_unformatted', message: 'TXT ist bewusst unformatiert.' });
    if (runs.some((r) => r.audio_anchor_id)) out.push({ code: 'html_anchors', message: 'Audioanker bleiben nur im Webbundle erhalten.' });
    return out;
}

function checkDocument(doc) {
    if (!doc || doc.schema_version !== 1 || !Array.isArray(doc.nodes)) return false;
    const seen = new Set();
    for (const node of doc.nodes) {
        if (!ID_PATTERN.test(node.id) || seen.has(node.id) || !Array.isArray(node.runs)) return false;
        seen.add(node.id);
    }
    for (const node of doc.nodes) {
        for (const run of node.runs) {
            if (!ID_PATTERN.test(run.id) || seen.has(run.id) || typeof run.text !== 'string') return false;
            seen.add(run.id);
        }
    }
    return true;
}

function idsOf(doc) {
    return new Set(doc.nodes.flatMap((n) => [n.id, ...n.runs.map((r) => r.id)]));
}

/**
 * @param {{client: {request: Function}, savePath: string, document: object,
 *          version: number, canEdit?: boolean, newId?: () => string,
 *          makeRef?: (v: any) => {value: any}}} options
 */
export function useRichTextEditor(options) {
    const opts = options || {};
    const makeRef = opts.makeRef
        || (typeof Vue !== 'undefined' && Vue.ref ? Vue.ref : (value) => ({ value }));
    let counter = 0;
    const newId = opts.newId || (() => {
        counter += 1;
        return `r-${Date.now().toString(36)}-${counter}`;
    });
    const valid = checkDocument(opts.document);

    const doc = makeRef(valid ? opts.document : { schema_version: 1, nodes: [] });
    const version = makeRef(Number.isInteger(opts.version) ? opts.version : 0);
    const selection = makeRef({ nodeId: null, runId: null });
    const status = makeRef(valid ? 'idle' : 'failed');
    const dirty = makeRef(false);
    const readOnly = makeRef(!valid || opts.canEdit === false);
    const announcement = makeRef('');
    const losses = makeRef(exportLosses(doc.value));
    let pendingKey = null;

    if (opts.saveDocument) selectFirst();

    function say(key) {
        announcement.value = label(key);
    }

    function commit(next) {
        doc.value = next;
        dirty.value = true;
        losses.value = exportLosses(next);
        if (opts.saveDocument ? !['saving', 'stale', 'failed'].includes(status.value) : status.value !== 'denied') {
            status.value = 'idle';
        }
        pendingKey = null;
        if (opts.saveDocument && status.value !== 'stale') announcement.value = 'Änderungen sind noch nicht gespeichert.';
    }

    function mapNode(nodeId, fn) {
        let found = false;
        const nodes = doc.value.nodes.map((n) => {
            if (n.id !== nodeId) return n;
            found = true;
            return fn(n);
        });
        return found ? { ...doc.value, nodes } : null;
    }

    function select(nodeId, runId) {
        const node = doc.value.nodes.find((n) => n.id === nodeId);
        if (!node || (runId != null && !node.runs.some((r) => r.id === runId))) return false;
        selection.value = { nodeId, runId: runId == null ? null : runId };
        return true;
    }

    function selectFirst() {
        const node = doc.value.nodes.find((n) => n.runs.length);
        if (node) select(node.id, node.runs[0].id);
    }

    function applyLayout(key, value, onRun) {
        if (readOnly.value) return;
        const { nodeId, runId } = selection.value;
        const next = mapNode(nodeId, (n) => onRun
            ? { ...n, runs: n.runs.map((r) => r.id === runId ? { ...r, [key]: value } : r) }
            : { ...n, [key]: value });
        if (!next) return;
        commit(next);
        announcement.value = 'Formatierung geändert.';
    }

    function toggleHeading() {
        if (readOnly.value) return;
        const next = mapNode(selection.value.nodeId, (n) => n.kind === 'heading'
            ? { ...n, kind: 'paragraph', level: 0 }
            : { ...n, kind: 'heading', level: 2 });
        if (!next) return;
        commit(next);
        announcement.value = 'Absatzformat geändert.';
    }

    function insertGraphic(objectId, altText, afterNode) {
        if (readOnly.value || !GRAPHIC_ID.test(objectId)) return;
        const node = { id: crypto.randomUUID(), kind: 'graphic', level: 0,
            runs: [], object_id: objectId, alt_text: altText };
        const nodes = [...doc.value.nodes];
        const at = nodes.findIndex((n) => n.id === afterNode);
        nodes.splice(at === -1 ? nodes.length : at + 1, 0, node);
        commit({ ...doc.value, nodes });
        announcement.value = 'Grafik eingefügt. Bitte speichern.';
        return true;
    }

    function editRun(nodeId, runId, text) {
        if (readOnly.value || typeof text !== 'string') return false;
        const clean = sanitizePaste(text).text;
        const node = doc.value.nodes.find((n) => n.id === nodeId);
        const run = node && node.runs.find((r) => r.id === runId);
        if (!run || run.text === clean) return false;
        commit(mapNode(nodeId, (n) => ({
            ...n, runs: n.runs.map((r) => (r.id === runId ? { ...r, text: clean, audio_anchor_id: null } : r)),
        })));
        return true;
    }

    function insertRun(nodeId, index, text) {
        if (readOnly.value) return null;
        const pasted = sanitizePaste(text);
        const used = idsOf(doc.value);
        let id = newId();
        while (used.has(id) || !ID_PATTERN.test(id)) id = newId();
        const next = mapNode(nodeId, (n) => {
            const at = Math.min(Math.max(Number.isInteger(index) ? index : n.runs.length, 0), n.runs.length);
            const run = { id, text: pasted.text, bold: false, italic: false, underline: false, audio_anchor_id: null };
            return { ...n, runs: [...n.runs.slice(0, at), run, ...n.runs.slice(at)] };
        });
        if (!next) return null;
        commit(next);
        selection.value = { nodeId, runId: id };
        if (pasted.changed) say('clinic.richtext.pasteCleaned');
        return id;
    }

    function toggleFormat(format, nodeId, runId) {
        if (readOnly.value || !FORMATS.includes(format)) return false;
        const target = nodeId || selection.value.nodeId;
        const run = runId || selection.value.runId;
        const next = mapNode(target, (n) => ({
            ...n, runs: n.runs.map((r) => (r.id === run ? { ...r, [format]: !r[format] } : r)),
        }));
        if (!next) return false;
        commit(next);
        return true;
    }

    function paste(nodeId, runId, clipboardText) {
        const pasted = sanitizePaste(clipboardText);
        const node = doc.value.nodes.find((n) => n.id === nodeId);
        const run = node && node.runs.find((r) => r.id === runId);
        if (!run || readOnly.value) return false;
        const ok = editRun(nodeId, runId, run.text + pasted.text);
        if (ok && pasted.changed) say('clinic.richtext.pasteCleaned');
        return ok;
    }

    async function save() {
        if (readOnly.value || !dirty.value || status.value === 'saving') return status.value;
        if (opts.saveDocument) {
            if (status.value === 'stale') return status.value;
            status.value = 'saving';
            const sent = doc.value;
            const res = await opts.saveDocument(sent);
            if (res.outcome === 'ok') {
                version.value = res.data.document_version;
                if (doc.value === res.sent) {
                    doc.value = res.data.document;
                    dirty.value = false;
                    status.value = 'saved';
                    say('clinic.richtext.saved');
                } else {
                    status.value = 'idle';
                    announcement.value = 'Weitere Änderungen sind noch nicht gespeichert.';
                }
            } else {
                status.value = res.outcome === 'conflict' ? 'stale' : 'failed';
                say(status.value === 'stale' ? 'clinic.richtext.stale' : 'clinic.richtext.failed');
            }
            return status.value;
        }
        if (!opts.client || !opts.savePath) { status.value = 'failed'; return status.value; }
        pendingKey = pendingKey || `richtext-${newId()}-${newId()}`.slice(0, 128);
        status.value = 'saving';
        const res = await opts.client.request(opts.savePath, {
            method: 'PUT',
            body: { ...doc.value, expected_version: version.value },
            ifMatch: String(version.value),
            idempotencyKey: pendingKey,
        });
        if (res.outcome === 'ok') {
            const saved = res.data && Number.isInteger(res.data.version) ? res.data.version : version.value + 1;
            version.value = saved;
            dirty.value = false;
            pendingKey = null;
            if (res.data && Array.isArray(res.data.losses)) losses.value = res.data.losses;
            status.value = 'saved';
            say('clinic.richtext.saved');
        } else if (res.outcome === 'conflict') {
            status.value = 'stale';
            say('clinic.richtext.stale');
        } else if (res.outcome === 'permission') {
            status.value = 'denied';
            readOnly.value = true;
            say('clinic.richtext.denied');
        } else if (res.outcome === 'cancelled') {
            status.value = 'idle';
        } else {
            status.value = 'failed';
            say('clinic.richtext.failed');
        }
        return status.value;
    }

    // After a stale save the caller loads the current snapshot and passes it here.
    function replace(nextDoc, nextVersion) {
        if (!checkDocument(nextDoc) || !Number.isInteger(nextVersion)) return false;
        doc.value = nextDoc;
        version.value = nextVersion;
        dirty.value = false;
        status.value = 'idle';
        losses.value = exportLosses(nextDoc);
        selection.value = { nodeId: null, runId: null };
        if (opts.saveDocument) selectFirst();
        announcement.value = '';
        pendingKey = null;
        return true;
    }

    return {
        doc, version, selection, status, dirty, readOnly, announcement, losses,
        select, editRun, insertRun, toggleFormat, applyLayout, toggleHeading, insertGraphic, paste, save, replace,
    };
}

const keyFormat = { b: 'bold', i: 'italic', u: 'underline' };

/** Vue component. Props: client, savePath, document, version, canEdit. */
export const RichTextEditor = {
    name: 'ClinicRichTextEditor',
    props: {
        client: { type: Object, required: true },
        savePath: { type: String, default: '' },
        document: { type: Object, required: true },
        version: { type: Number, default: 0 },
        canEdit: { type: Boolean, default: true },
        saveDocument: { type: Function, default: null },
        addGraphic: { type: Function, default: null },
        graphicsPath: { type: String, default: '' },
        reloadDocument: { type: Function, default: null },
    },
    setup(props) {
        const editor = useRichTextEditor({
            client: props.client, savePath: props.savePath, document: props.document,
            version: props.version, canEdit: props.canEdit, saveDocument: props.saveDocument,
        });
        const warning = surfaceFor('warning');
        const lossStyle = { background: warning.background, color: warning.color, border: `1px solid ${warning.border}` };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        function onKey(event, node, run) {
            const format = keyFormat[String(event.key).toLowerCase()];
            if ((event.ctrlKey || event.metaKey) && format) {
                event.preventDefault();
                editor.toggleFormat(format, node.id, run.id);
            }
        }
        function onPaste(event, node, run) {
            event.preventDefault();
            const data = event.clipboardData && event.clipboardData.getData('text/plain');
            if (props.saveDocument) {
                const { selectionStart, selectionEnd } = event.target;
                const pasted = sanitizePaste(data);
                if (editor.editRun(node.id, run.id, run.text.slice(0, selectionStart)
                    + pasted.text + run.text.slice(selectionEnd))) {
                    if (pasted.changed) editor.announcement.value = label('clinic.richtext.pasteCleaned');
                    const cursor = selectionStart + pasted.text.length;
                    Vue.nextTick(() => event.target.setSelectionRange(cursor, cursor));
                }
                return;
            }
            editor.paste(node.id, run.id, data || '');
        }
        function format(node, run) {
            return FORMATS.filter((f) => run[f]).map((f) => label('clinic.richtext.' + f)).join(', ');
        }
        const selectedNode = Vue.computed(() => editor.doc.value.nodes.find((n) => n.id === editor.selection.value.nodeId));
        const selectedRun = Vue.computed(() => selectedNode.value?.runs.find((r) => r.id === editor.selection.value.runId));
        const showLayout = Vue.ref(false);
        const showGraphic = Vue.ref(false);
        const uploading = Vue.ref(false);
        const alt = Vue.ref('');
        const file = Vue.shallowRef(null);
        if (props.saveDocument) {
            Vue.watch(() => props.canEdit, (writable) => { editor.readOnly.value = !writable; }, { flush: 'sync' });
            Vue.watch(() => props.document, (doc) => editor.replace(doc, props.version));
        }
        async function addPicture() {
            if (editor.readOnly.value || uploading.value || !file.value) return;
            const description = alt.value.trim();
            if (!description || /[<>\x00-\x1f]/.test(description)) {
                editor.announcement.value = 'Bitte eine kurze Bildbeschreibung ohne Sonderzeichen < und > eingeben.';
                return;
            }
            uploading.value = true;
            const afterNode = editor.selection.value.nodeId;
            const objectId = await props.addGraphic(file.value);
            uploading.value = false;
            if (!objectId || !editor.insertGraphic(objectId, description, afterNode)) {
                editor.announcement.value = 'Grafik konnte nicht eingefügt werden. Bitte eine PNG-Datei bis 2 MB wählen und die Bearbeitung prüfen.';
                return;
            }
            showGraphic.value = false;
            alt.value = '';
            file.value = null;
            await Vue.nextTick();
            graphicButton.value?.focus();
        }
        const graphicButton = Vue.ref(null);
        const pressedStyle = (pressed) => pressed ? {
            ...targetSize, backgroundColor: 'var(--v8-s2)', borderColor: 'var(--v8-text-2)',
        } : targetSize;
        function runStyle(node, run) {
            // The V8 phone input reset otherwise overrides the document's font size.
            const size = run.font_size_pt ? run.font_size_pt + 'pt' : (node.kind === 'heading' ? '1.25rem' : '1rem');
            return { ...focusStyle, minHeight: '48px', fieldSizing: 'content', overflowWrap: 'anywhere',
                width: '100%', background: 'var(--surface)', color: 'var(--v8-text)',
                border: '1px solid var(--v8-text-2)', borderRadius: '7px', padding: '10px 11px',
                backgroundColor: !editor.readOnly.value && selectedRun.value?.id === run.id ? 'var(--v8-s1)' : null,
                fontFamily: run.font_family, fontSize: size + ' !important',
                fontWeight: run.bold || node.kind === 'heading' ? 700 : 400,
                fontStyle: run.italic ? 'italic' : 'normal', textDecoration: run.underline ? 'underline' : 'none',
                textAlign: node.text_align, lineHeight: node.line_spacing };
        }
        async function reload() {
            if (editor.dirty.value && !globalThis.confirm('Ungespeicherte Änderungen verwerfen und den aktuellen Stand laden?')) return;
            const loaded = await props.reloadDocument();
            await Vue.nextTick();
            editor.announcement.value = loaded ? 'Aktueller Stand geladen.' : 'Der aktuelle Stand konnte nicht geladen werden.';
        }
        return { ...editor, label, lossStyle, focusStyle, onKey, onPaste, format, FORMATS, LAYOUT,
            selectedNode, selectedRun, targetSize, fieldStyle, pressedStyle, showLayout, graphicButton,
            showGraphic, uploading, alt, file, addPicture,
            runStyle, reload, isGraphicId: (id) => GRAPHIC_ID.test(id) };
    },
    template: `
<section class="clinic-richtext" :aria-label="label('clinic.richtext.toolbar')" :style="saveDocument ? 'display:grid;gap:12px;padding:16px' : null">
  <div role="toolbar" :aria-label="label('clinic.richtext.toolbar')"
    :style="saveDocument ? 'display:flex;flex-wrap:wrap;gap:8px;align-items:end' : null">
    <template v-if="saveDocument">
      <button type="button" class="button" :style="targetSize" :disabled="readOnly" :aria-expanded="showLayout"
        @click="showLayout = !showLayout">Formatieren</button>
      <button v-if="addGraphic" ref="graphicButton" type="button" class="button" :style="targetSize" :disabled="readOnly"
        :aria-expanded="showGraphic" @click="showGraphic = !showGraphic">Grafik einfügen</button>
      <button type="button" class="button primary"
        :disabled="readOnly || !dirty || status === 'saving' || status === 'stale'" :aria-busy="status === 'saving'"
        :style="targetSize" @click="save">{{ status === 'failed' ? label('clinic.richtext.retry') : label('clinic.richtext.save') }}</button>
    </template>
    <div v-if="saveDocument && showLayout" style="display:flex;flex:1 1 100%;flex-wrap:wrap;gap:8px;align-items:end;min-width:0">
      <label v-for="field in LAYOUT" :key="field.key" style="display:grid;gap:4px;flex:1 1 132px;min-width:0">
        {{ field.label }}
        <select :disabled="readOnly || !selectedRun" :style="fieldStyle"
          :value="(field.run ? selectedRun : selectedNode)?.[field.key] ?? ''"
          @change="applyLayout(field.key, field.key === 'line_spacing' ? Number($event.target.value) || null : $event.target.value || null, field.run)">
          <option v-for="[value, title] in field.choices" :key="value" :value="value">{{ title }}</option>
        </select>
      </label>
      <label style="display:grid;gap:4px;flex:1 1 104px;min-width:0">Schriftgrösse
        <input type="number" min="8" max="72" step="1" placeholder="Standard"
          :style="fieldStyle" :disabled="readOnly || !selectedRun" :value="selectedRun?.font_size_pt ?? ''"
          @change="$event.target.validity.valid && applyLayout('font_size_pt', $event.target.valueAsNumber || null, true)">
      </label>
      <button type="button" class="button" :style="pressedStyle(selectedNode?.kind === 'heading')" :disabled="readOnly || !selectedNode"
        :aria-pressed="selectedNode?.kind === 'heading'" @click="toggleHeading">Überschrift</button>
      <button v-for="f in FORMATS" :key="f" type="button" class="button" :aria-pressed="String(!!selectedRun?.[f])"
        :disabled="readOnly || !selectedRun" :style="pressedStyle(selectedRun?.[f])"
        @click="toggleFormat(f)">{{ label('clinic.richtext.' + f) }}</button>
    </div>
    <template v-if="!saveDocument">
      <button v-for="f in FORMATS" :key="f" type="button" :aria-pressed="String(!!selectedRun?.[f])"
        :disabled="readOnly || !selection.runId" :style="focusStyle" @click="toggleFormat(f)">{{ label('clinic.richtext.' + f) }}</button>
      <button type="button" :disabled="readOnly || !selection.nodeId" :style="focusStyle"
        @click="insertRun(selection.nodeId, undefined, '')">{{ label('clinic.richtext.insert') }}</button>
      <button type="button" :disabled="readOnly || !dirty || status === 'saving'" :aria-busy="status === 'saving'"
        :style="focusStyle" @click="save">{{ status === 'failed' ? label('clinic.richtext.retry') : label('clinic.richtext.save') }}</button>
    </template>
  </div>
  <form v-if="showGraphic && !readOnly" aria-label="Grafik einfügen" @submit.prevent="addPicture">
    <label class="field"><span>Bildbeschreibung</span><input type="text" maxlength="200" required v-model="alt" :style="fieldStyle"></label>
    <label class="field"><span>PNG-Datei</span><input type="file" accept="image/png" required
      :style="fieldStyle" @change="file = $event.target.files[0]"></label>
    <button class="button" :style="targetSize" type="submit" :disabled="uploading">{{ uploading ? 'Grafik wird eingefügt …' : 'Einfügen' }}</button>
  </form>
  <div v-for="(node, ni) in doc.nodes" :key="node.id" :data-node-id="node.id" role="group"
    :style="saveDocument ? 'display:grid;gap:4px;min-width:0' : null">
    <img v-if="graphicsPath && node.kind === 'graphic' && isGraphicId(node.object_id)"
      :src="graphicsPath + '/' + node.object_id" :alt="node.alt_text" style="max-width:100%;height:auto">
    <component :is="saveDocument ? 'textarea' : 'input'" v-for="(run, ri) in node.runs" :key="run.id"
      :rows="saveDocument ? 1 : undefined" :type="saveDocument ? undefined : 'text'" :data-run-id="run.id" :value="run.text"
      :readonly="readOnly" :style="saveDocument ? runStyle(node, run) : focusStyle"
      :aria-label="label('clinic.richtext.run', { run: ri + 1, node: ni + 1 }) + (format(node, run) ? ', ' + format(node, run) : '') + (run.audio_anchor_id ? '' : ', ' + label('clinic.richtext.unaligned'))"
      @focus="select(node.id, run.id)" @input="editRun(node.id, run.id, $event.target.value)"
      @keydown="onKey($event, node, run)" @paste="onPaste($event, node, run)">
  </div>
  <div v-if="!saveDocument && losses.length" role="note" :style="lossStyle">
    <strong>{{ label('clinic.richtext.losses') }}</strong>
    <ul><li v-for="l in losses" :key="l.code">{{ l.message }}</li></ul>
  </div>
  <button v-if="reloadDocument && status === 'stale'" type="button" class="button" :style="targetSize" @click="reload">
    {{ label('clinic.richtext.reload') }}
  </button>
  <p v-else-if="status === 'stale'" role="alert">{{ label('clinic.richtext.stale') }}</p>
  <p v-if="saveDocument && dirty && !announcement" role="note">Änderungen sind noch nicht gespeichert.</p>
  <div role="status" aria-live="polite">{{ announcement }}</div>
</section>`,
};
