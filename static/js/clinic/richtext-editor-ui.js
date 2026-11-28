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

    function say(key) {
        announcement.value = label(key);
    }

    function commit(next) {
        doc.value = next;
        dirty.value = true;
        losses.value = exportLosses(next);
        if (status.value !== 'denied') status.value = 'idle';
        pendingKey = null;
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
        pendingKey = null;
        return true;
    }

    return {
        doc, version, selection, status, dirty, readOnly, announcement, losses,
        select, editRun, insertRun, toggleFormat, paste, save, replace,
    };
}

const keyFormat = { b: 'bold', i: 'italic', u: 'underline' };

/** Vue component. A correctionSave controller uses the shared correction save instead of the standalone save. */
export const RichTextEditor = {
    name: 'ClinicRichTextEditor',
    props: {
        client: { type: Object },
        savePath: { type: String },
        document: { type: Object },
        version: { type: Number, default: 0 },
        canEdit: { type: Boolean, default: true },
        correctionSave: { type: Object },
    },
    setup(props) {
        const correction = props.correctionSave?.document;
        const editor = correction || useRichTextEditor({
            client: props.client, savePath: props.savePath, document: props.document,
            version: props.version, canEdit: props.canEdit,
        });
        const warning = surfaceFor('warning');
        const lossStyle = { background: warning.background, color: warning.color, border: `1px solid ${warning.border}` };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset,
            '--border-focus': correction ? 'var(--brand-accent)' : undefined };
        const targetStyle = correction ? { minHeight: '48px' } : null;
        function runStyle(run) {
            if (!correction) return focusStyle;
            const active = correction.activeRunId.value === run.id;
            return { ...focusStyle, ...targetStyle, maxWidth: '100%', width: '100%',
                fontWeight: run.bold ? 'bold' : undefined, fontStyle: run.italic ? 'italic' : undefined,
                textDecoration: run.underline ? 'underline' : undefined,
                backgroundColor: active ? 'var(--ui-hover)' : undefined,
                borderColor: active ? 'var(--brand-accent)' : 'var(--ui-text-muted)' };
        }
        function onKey(event, node, run) {
            if (!event.ctrlKey && !event.metaKey) return;
            const key = String(event.key).toLowerCase();
            if (correction && (key === 'z' || key === 'y')) {
                event.preventDefault();
                event.stopPropagation();
                if (key === 'y' || event.shiftKey) correction.redo();
                else correction.undo();
            } else if (keyFormat[key]) {
                event.preventDefault();
                editor.toggleFormat(keyFormat[key], node.id, run.id);
            }
        }
        function onPaste(event, node, run) {
            event.preventDefault();
            const data = event.clipboardData && event.clipboardData.getData('text/plain');
            editor.paste(node.id, run.id, data || '');
        }
        function format(node, run) {
            return FORMATS.filter((f) => run[f]).map((f) => label('clinic.richtext.' + f)).join(', ');
        }
        const save = correction ? props.correctionSave : null;
        return { ...editor, label, lossStyle, focusStyle, targetStyle, runStyle, onKey, onPaste, format, FORMATS,
            correction, ...(save ? { save: save.save, status: save.state, saveMessage: save.message } : {}),
        };
    },
    template: `
<section class="clinic-richtext" :aria-label="label('clinic.richtext.toolbar')">
  <div role="toolbar" :aria-label="label('clinic.richtext.toolbar')" :style="correction ? { display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' } : null">
    <button v-for="f in FORMATS" :key="f" type="button" :aria-pressed="String(!!(selection.runId && doc.nodes.some(n => n.runs.some(r => r.id === selection.runId && r[f]))))"
      :disabled="readOnly || !selection.runId" :style="[focusStyle, targetStyle]" @click="toggleFormat(f)">{{ label('clinic.richtext.' + f) }}</button>
    <button type="button" :disabled="readOnly || !selection.nodeId" :style="[focusStyle, targetStyle]"
      @click="insertRun(selection.nodeId, undefined, '')">{{ label('clinic.richtext.insert') }}</button>
    <template v-if="correction">
      <button type="button" :disabled="readOnly || !historyState.canUndo" :style="[focusStyle, targetStyle]" @click="undo">Rückgängig</button>
      <button type="button" :disabled="readOnly || !historyState.canRedo" :style="[focusStyle, targetStyle]" @click="redo">Wiederholen</button>
      <button type="button" :disabled="readOnly || doc.nodes.findIndex(n => n.id === selection.nodeId) < 1" :style="[focusStyle, targetStyle]" @click="moveParagraph(-1)">Absatz nach oben</button>
      <button type="button" :disabled="readOnly || !selection.nodeId || selection.nodeId === doc.nodes.at(-1)?.id" :style="[focusStyle, targetStyle]" @click="moveParagraph(1)">Absatz nach unten</button>
      <button type="button" :disabled="!canSeek(selection.runId)" :style="[focusStyle, targetStyle]" @click="seekToSegment(selection.runId)">Zur Tonstelle springen</button>
    </template>
    <button type="button" :class="correction ? 'button primary' : null" :disabled="readOnly || (correction ? status !== 'unsaved' : !dirty || status === 'saving')" :aria-busy="status === 'saving'"
      :style="[focusStyle, targetStyle]" @click="save">{{ status === 'failed' ? label('clinic.richtext.retry') : label('clinic.richtext.save') }}</button>
  </div>
  <p v-if="correction && !doc.nodes.some(n => n.runs.some(r => r.text))">Noch kein Text vorhanden.</p>
  <div v-for="(node, ni) in doc.nodes" :key="node.id" :data-node-id="node.id" role="group" :style="correction ? { marginBottom: '8px' } : null">
    <component :is="correction ? 'textarea' : 'input'" v-for="(run, ri) in node.runs" :key="run.id" :type="correction ? null : 'text'" :rows="correction ? 1 : null" :data-run-id="run.id" :value="run.text"
      :readonly="readOnly" :style="runStyle(run)" :data-playing="correction && activeRunId === run.id ? 'true' : null"
      :aria-label="label('clinic.richtext.run', { run: ri + 1, node: ni + 1 }) + (format(node, run) ? ', ' + format(node, run) : '') + ((correction ? canSeek(run.id) : run.audio_anchor_id) ? '' : ', ' + label('clinic.richtext.unaligned'))"
      @focus="select(node.id, run.id)" @input="editRun(node.id, run.id, $event.target.value)"
      @keydown="onKey($event, node, run)" @paste="onPaste($event, node, run)"></component>
  </div>
  <div v-if="!correction && losses.length" role="note" :style="lossStyle">
    <strong>{{ label('clinic.richtext.losses') }}</strong>
    <ul><li v-for="l in losses" :key="l.code">{{ l.message }}</li></ul>
  </div>
  <p v-if="status === 'stale'" role="alert">{{ label('clinic.richtext.stale') }}</p>
  <p v-if="correction" role="status" aria-live="polite">{{ saveMessage }}</p>
  <div role="status" aria-live="polite">{{ announcement }}</div>
</section>`,
};
