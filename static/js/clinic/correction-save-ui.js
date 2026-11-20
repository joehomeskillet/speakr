/**
 * Save and resume for the correction view (PAR-COR-006).
 *
 * Wraps the w149 editor state: saves the text through the atomic correction
 * API (POST .../correction/save) with the lease generation, the text revision
 * the text was read at and the audio position. The resume position lives on
 * the server (GET .../correction, state.audio_position_ms); nothing is written
 * to browser storage and clinical text stays in memory. Text/audio sync (W10)
 * runs server-side on every save; the UI only adopts the returned revision.
 *
 * Exports:
 *   useCorrectionSave({ editor, client, dictationId, lease?, getPositionMs?,
 *                       autosaveMs?, maxRetries?, setTimer?, clearTimer?, vue? })
 *     editor  the object returned by useCorrectionEditor (text, dirty,
 *             textRevisionId, etag, lease, canEdit refs)
 *     lease   optional lease controller from lease-ui.js (clears the
 *             unsaved marker after a save, reports a lost holder)
 *     returns state ref ('saved' | 'unsaved' | 'saving' | 'conflict' | 'lost'),
 *             message, resumePosition (ms or null), stateVersion, error refs,
 *             and the methods save(), touch(), loadResume(), reloadLatest(),
 *             refresh(), setPosition(ms), dispose().
 *
 * V8 autosave contract (UI8-004):
 *   - One write request per editor at a time. Later edits stay dirty and become the next request
 *     once the answer arrives, on the newly confirmed revision and validator.
 *   - A success confirms only the version that was sent; newer local text stays unsaved.
 *   - Every operation has one idempotency key. After a lost answer the same key and body are sent
 *     again (the server replays the stored result). With `reconcileFirst` this also happens when
 *     the text changed meanwhile, before any new key is used.
 *   - If-Match carries the validator read from the server ("<revision>.g<lease generation>").
 *   - 409 REVISION_CONFLICT opens the conflict view: automatic saving stops, the local text and the
 *     newest server text are both kept, and keepMine() / takeServer() / compare() are the only ways out.
 *     409 LEASE_STALE (another tab took the lease over) and 423 show the lost state.
 *   - The debounce is the `autosaveMs` option (default AUTOSAVE_MS_DEFAULT).
 *   CorrectionSave: Vue options object, props editor, client, dictationId,
 *     lease, getPositionMs, vue.
 *   registerCorrectionSave(app): app.component('clinic-correction-save', ...).
 *
 * Keyboard: Ctrl+S / Cmd+S inside the section saves; all controls are native
 * buttons (Tab, Enter, Space).
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

/** Debounce of the automatic save in ms; configurable through the `autosaveMs` option (0 or null: off). */
export const AUTOSAVE_MS_DEFAULT = 5000;
const LEASE_STALE_CODE = 'LEASE_STALE';
const DIFF_CELL_LIMIT = 4_000_000;

export const SAVE_STATES = Object.freeze(['saved', 'unsaved', 'saving', 'conflict', 'lost']);

export const LABELS = Object.freeze({
    title: 'Speichern',
    saved: 'Gespeichert.',
    unsaved: 'Nicht gespeicherte Änderungen.',
    saving: 'Wird gespeichert …',
    conflict: 'Ein anderer Tab oder eine andere Person hat das Diktat geändert. Bitte aktuellen Stand laden.',
    lost: 'Die Bearbeitung wurde entzogen. Änderungen sind nicht gespeichert.',
    denied: 'Keine Berechtigung zum Speichern.',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
    timeout: 'Verbindung unterbrochen. Speichern wird wiederholt.',
    failed: 'Das Speichern ist fehlgeschlagen.',
    leaseStale: 'Die Bearbeitung wurde in einem anderen Tab übernommen. Änderungen sind nicht gespeichert.',
    precondition: 'Der Stand ist nicht bestätigt. Bitte aktuellen Stand laden.',
    serverUnavailable: 'Der aktuelle Serverstand konnte nicht geladen werden.',
    conflictChoice: 'Eigenen Text und Serverstand vergleichen und bewusst wählen.',
    keepMine: 'Eigenen Text behalten',
    takeServer: 'Serverstand übernehmen',
    compare: 'Vergleichen',
    mine: 'Eigener Text',
    server: 'Serverstand',
    save: 'Speichern',
    retry: 'Erneut versuchen',
    reload: 'Aktuellen Stand laden',
    resume: 'Letzte gespeicherte Position',
    noResume: 'Keine gespeicherte Position.',
});

const SURFACE = Object.freeze({
    saved: 'success', unsaved: 'warning', saving: 'processing', conflict: 'error', lost: 'error',
});

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    return { ref, shallow: V && V.shallowRef ? V.shallowRef : ref, V };
}

function newKey() {
    return globalThis.crypto.randomUUID();
}

function formatPosition(ms) {
    const total = Math.floor(ms / 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return pad(Math.floor(total / 60)) + ':' + pad(total % 60);
}

/**
 * Line diff of the local and the server text for the conflict view. Rows are
 * { kind: 'same' | 'mine' | 'server', text }: 'mine' exists only in the local
 * text, 'server' only in the server text. Pure; nothing is changed or dropped.
 */
export function diffLines(mine, server) {
    const a = String(mine).split('\n');
    const b = String(server).split('\n');
    let head = 0;
    while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
    let tail = 0;
    while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1;
    const rows = a.slice(0, head).map((text) => ({ kind: 'same', text }));
    const ma = a.slice(head, a.length - tail);
    const mb = b.slice(head, b.length - tail);
    if (ma.length * mb.length > DIFF_CELL_LIMIT) {
        for (const text of ma) rows.push({ kind: 'mine', text });
        for (const text of mb) rows.push({ kind: 'server', text });
    } else {
        const lcs = Array.from({ length: ma.length + 1 }, () => new Array(mb.length + 1).fill(0));
        for (let i = ma.length - 1; i >= 0; i -= 1) {
            for (let j = mb.length - 1; j >= 0; j -= 1) {
                lcs[i][j] = ma[i] === mb[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
            }
        }
        let i = 0;
        let j = 0;
        while (i < ma.length || j < mb.length) {
            if (i < ma.length && j < mb.length && ma[i] === mb[j]) {
                rows.push({ kind: 'same', text: ma[i] }); i += 1; j += 1;
            } else if (j >= mb.length || (i < ma.length && lcs[i + 1][j] >= lcs[i][j + 1])) {
                rows.push({ kind: 'mine', text: ma[i] }); i += 1;
            } else {
                rows.push({ kind: 'server', text: mb[j] }); j += 1;
            }
        }
    }
    for (const text of a.slice(a.length - tail)) rows.push({ kind: 'same', text });
    return rows;
}

/** @param {object} options see the header comment */
export function useCorrectionSave(options) {
    const opts = options || {};
    const editor = opts.editor;
    const client = opts.client || createClinicApiClient();
    const dictationId = String(opts.dictationId);
    const lease = opts.lease || null;
    const autosaveMs = opts.autosaveMs === undefined ? AUTOSAVE_MS_DEFAULT : opts.autosaveMs;
    const maxRetries = opts.maxRetries === undefined ? 3 : opts.maxRetries;
    const reconcileFirst = opts.reconcileFirst === true;
    const setTimer = opts.setTimer || ((fn, ms) => setTimeout(fn, ms));
    const clearTimer = opts.clearTimer || ((id) => clearTimeout(id));
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(dictationId) + '/correction';

    const state = refs.ref('saved');
    const message = refs.ref(LABELS.saved);
    const resumePosition = refs.ref(null);
    const stateVersion = refs.ref(null);
    const retries = refs.ref(0);
    // Conflict view data: { mine, server: { text, revisionId, etag, stateVersion, positionMs } | null }.
    const conflict = refs.shallow(null);

    let position = null;
    // The operation in flight or awaiting an answer: { key, digest, body, sent, ifMatch, unclear }.
    // It keeps the exact request so a lost answer is reconciled with the same key and body.
    let pending = null;
    let timer = null;
    let inflight = false;
    let resave = false; // a save was asked for while another one was in flight
    let terminal = null; // 'conflict' | 'lost' until the user resolves it
    let disposed = false;

    function set(next, text) {
        state.value = next;
        message.value = text || LABELS[next] || '';
    }

    function stop() {
        if (timer !== null) clearTimer(timer);
        timer = null;
    }

    function generation() {
        const snap = lease ? lease.getState() : editor.lease.value;
        return snap && Number.isInteger(snap.generation) ? snap.generation : null;
    }

    /** The server validator is "<revision>.g<generation>"; send the generation this tab holds. */
    function ifMatchFor(gen) {
        const tag = editor.etag.value;
        if (!tag) return undefined;
        const m = /^"(.+)\.g\d+"$/.exec(tag);
        return m && gen !== null ? '"' + m[1] + '.g' + gen + '"' : tag;
    }

    /** Recompute the visible state from the editor; terminal states stay until resolved. */
    function refresh() {
        if (disposed || inflight) return state.value;
        if (terminal) return state.value;
        const snap = lease ? lease.getState() : editor.lease.value;
        if (editor.dirty.value && snap && (snap.status === 'lost' || snap.status === 'foreign')) {
            set('lost');
        } else if (editor.dirty.value) {
            set('unsaved');
        } else {
            set('saved');
        }
        return state.value;
    }

    function setPosition(ms) {
        if (Number.isInteger(ms) && ms >= 0) position = ms;
    }

    function currentPosition() {
        if (typeof opts.getPositionMs === 'function') {
            const value = opts.getPositionMs();
            if (Number.isInteger(value) && value >= 0) return value;
        }
        if (position !== null) return position;
        return resumePosition.value !== null ? resumePosition.value : 0;
    }

    /** Read the committed position and state version from the server. */
    async function loadResume() {
        const res = await client.request(base);
        if (res.outcome !== 'ok' || !res.data) return { ok: false, message: res.message || LABELS.failed };
        const st = res.data.state;
        if (st && Number.isInteger(st.audio_position_ms)) {
            resumePosition.value = st.audio_position_ms;
            stateVersion.value = Number.isInteger(st.version) ? st.version : null;
        } else {
            resumePosition.value = null;
            stateVersion.value = null;
        }
        return { ok: true, position: resumePosition.value };
    }

    function schedule(ms) {
        stop();
        if (disposed || autosaveMs === null || autosaveMs === 0) return;
        timer = setTimer(() => { timer = null; save(); }, ms);
    }

    /** Run the next save right after the answer of the previous one (batched newer text). */
    function followUp() {
        stop();
        if (disposed) return;
        timer = setTimer(() => { timer = null; save(); }, 0);
    }

    /** Call after every edit: debounced autosave. */
    function touch() {
        refresh();
        if (state.value === 'unsaved') schedule(autosaveMs);
    }

    function fail(next, text) {
        terminal = next === 'conflict' ? next : null; // a lost lease clears once it is held again
        set(next, text);
        return { ok: false, state: next, message: message.value };
    }

    function buildBody(gen) {
        const body = {
            text: editor.text.value,
            audio_position_ms: currentPosition(),
            lease_generation: gen,
            expected_text_revision_id: editor.textRevisionId.value,
        };
        if (stateVersion.value !== null) body.expected_state_version = stateVersion.value;
        return body;
    }

    function transmit(req) {
        return client.request(base + '/save', {
            method: 'POST',
            idempotencyKey: req.key,
            ifMatch: req.ifMatch,
            body: req.body,
        });
    }

    /** The newest server text, revision, validator and position, or null when it cannot be read. */
    async function fetchServer() {
        const res = await client.request(base);
        const d = res && res.outcome === 'ok' ? res.data : null;
        if (!d || typeof d.text !== 'string') return null;
        const st = d.state && Number.isInteger(d.state.audio_position_ms) ? d.state : null;
        return {
            text: d.text,
            revisionId: typeof d.text_revision_id === 'string' ? d.text_revision_id : null,
            etag: typeof d.etag === 'string' && d.etag ? d.etag
                : (typeof d.text_revision_id === 'string' ? '"' + d.text_revision_id + '"' : null),
            stateVersion: st && Number.isInteger(st.version) ? st.version : null,
            positionMs: st ? st.audio_position_ms : null,
        };
    }

    /** Stop the automatic save, keep the local text and hold the newest server text for the choice. */
    async function enterConflict() {
        stop();
        pending = null;
        terminal = 'conflict';
        conflict.value = { mine: editor.text.value, server: null };
        set('conflict');
        const server = await fetchServer();
        if (disposed) return;
        conflict.value = { mine: editor.text.value, server };
    }

    /** Turn the answer to one request into state. A success confirms only the version that was sent. */
    async function settle(res, req) {
        inflight = false;
        if (disposed || res.outcome === 'cancelled') return { ok: false, state: state.value, message: '' };
        if (res.outcome === 'ok' && res.data && typeof res.data.text_revision_id === 'string') {
            const d = res.data;
            pending = null;
            retries.value = 0;
            editor.textRevisionId.value = d.text_revision_id;
            editor.etag.value = typeof d.etag === 'string' && d.etag ? d.etag : '"' + d.text_revision_id + '"';
            stateVersion.value = Number.isInteger(d.state_version) ? d.state_version : stateVersion.value;
            if (Number.isInteger(d.audio_position_ms)) resumePosition.value = d.audio_position_ms;
            if (editor.text.value === req.sent) {
                editor.dirty.value = false;
                if (lease) lease.setUnsaved(null);
                set('saved');
            } else {
                // Newer local text stays dirty and becomes the next, single request on the new base.
                set('unsaved');
                if (resave || (autosaveMs !== null && autosaveMs !== 0)) followUp();
            }
            resave = false;
            return { ok: true, state: state.value };
        }
        resave = false;
        if (res.code === LEASE_STALE_CODE) {
            pending = null;
            if (lease) lease.notifyGeneration(-1);
            return fail('lost', LABELS.leaseStale);
        }
        if (res.status === 423) {
            pending = null;
            if (lease) lease.notifyGeneration(-1);
            return fail('lost');
        }
        if (res.code === 'IDEMPOTENCY_CONFLICT') {
            pending = null;
            set('unsaved', LABELS.failed);
            return { ok: false, state: 'unsaved', message: message.value };
        }
        if (res.status === 428) {
            pending = null;
            set('unsaved', LABELS.precondition);
            return { ok: false, state: 'unsaved', message: message.value };
        }
        if (res.outcome === 'conflict' || res.status === 409) {
            await enterConflict();
            return { ok: false, state: 'conflict', message: message.value };
        }
        if (res.outcome === 'permission') {
            pending = null;
            return fail('lost', LABELS.denied);
        }
        if (res.outcome === 'network' || res.outcome === 'UNKNOWN') {
            // Result unclear: the same key and body are replayed, so the server answers with the stored result.
            if (pending) pending.unclear = true;
            retries.value += 1;
            set('unsaved', LABELS.timeout);
            if (retries.value <= maxRetries) schedule(autosaveMs * 2 * retries.value);
            return { ok: false, state: 'unsaved', message: message.value, retryable: true };
        }
        pending = null;
        set('unsaved', LABELS.failed);
        return { ok: false, state: 'unsaved', message: message.value };
    }

    async function save() {
        if (disposed) return { ok: false, state: state.value, message: '' };
        if (inflight) {
            resave = true;
            return { ok: false, state: 'saving', message: LABELS.saving };
        }
        if (terminal) return { ok: false, state: state.value, message: message.value };
        stop();
        const gen = generation();
        if (!editor.canEdit.value || gen === null) {
            return fail('lost', LABELS.readOnly);
        }
        const body = buildBody(gen);
        const digest = JSON.stringify(body);
        inflight = true;
        set('saving');
        if (reconcileFirst && pending && pending.unclear && pending.digest !== digest) {
            // The earlier answer was lost and the text moved on: learn that result before any new key is used.
            return settle(await transmit(pending), pending);
        }
        if (!pending || pending.digest !== digest) {
            pending = { key: newKey(), digest, body, sent: body.text, ifMatch: ifMatchFor(gen), unclear: false };
        }
        return settle(await transmit(pending), pending);
    }

    /** Conflict choice: keep the local text on top of the newest server revision. Nothing is discarded. */
    async function keepMine() {
        if (state.value !== 'conflict') return { ok: false, state: state.value, message: message.value };
        const server = (conflict.value && conflict.value.server) || await fetchServer();
        if (!server || !server.etag) return { ok: false, state: 'conflict', message: LABELS.serverUnavailable };
        editor.textRevisionId.value = server.revisionId;
        editor.etag.value = server.etag;
        stateVersion.value = server.stateVersion;
        pending = null;
        terminal = null;
        conflict.value = null;
        refresh();
        return { ok: true, state: state.value };
    }

    /** Conflict choice: replace the local text with the server text. The local text is dropped only by this call. */
    async function takeServer() {
        if (state.value !== 'conflict') return { ok: false, state: state.value, message: message.value };
        const server = (conflict.value && conflict.value.server) || await fetchServer();
        if (!server || !server.etag) return { ok: false, state: 'conflict', message: LABELS.serverUnavailable };
        editor.text.value = server.text;
        editor.textRevisionId.value = server.revisionId;
        editor.etag.value = server.etag;
        editor.dirty.value = false;
        stateVersion.value = server.stateVersion;
        resumePosition.value = server.positionMs;
        if (lease) lease.setUnsaved(null);
        pending = null;
        terminal = null;
        conflict.value = null;
        set('saved');
        return { ok: true, state: 'saved' };
    }

    /** Conflict choice: line diff of local and server text. Changes nothing. */
    function compare() {
        const c = conflict.value;
        return c && c.server ? diffLines(c.mine, c.server.text) : null;
    }

    /** Stale tab: drop local edits, load the server text and the committed position. */
    async function reloadLatest() {
        stop();
        terminal = null;
        pending = null;
        conflict.value = null;
        retries.value = 0;
        const res = await editor.load({ discard: true });
        if (res && res.ok) {
            if (lease) lease.setUnsaved(null);
            await loadResume();
            set('saved');
        } else {
            terminal = 'conflict';
            set('conflict');
        }
        return res;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        stop();
    }

    if (lease) lease.subscribe(() => { refresh(); });

    return {
        state, message, resumePosition, stateVersion, retries, conflict,
        save, touch, loadResume, reloadLatest, refresh, setPosition, dispose,
        keepMine, takeServer, compare,
    };
}

/** Vue component. Props: editor (from useCorrectionEditor), client, dictationId, lease, getPositionMs, vue. */
export const CorrectionSave = {
    name: 'ClinicCorrectionSave',
    props: {
        editor: { type: Object, required: true },
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        lease: { type: Object, default: null },
        getPositionMs: { type: Function, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const saver = useCorrectionSave({
            editor: props.editor, client: props.client || undefined, dictationId: props.dictationId,
            lease: props.lease || undefined, getPositionMs: props.getPositionMs || undefined,
            vue: props.vue || undefined,
        });
        if (V && typeof V.watch === 'function') {
            V.watch(() => props.editor.text.value, () => saver.touch());
        }
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { saver.loadResume(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(saver.dispose);
        const surface = V && V.computed
            ? V.computed(() => surfaceFor(SURFACE[saver.state.value]))
            : { value: surfaceFor(SURFACE[saver.state.value]) };
        const resumeText = V && V.computed
            ? V.computed(() => (saver.resumePosition.value === null
                ? LABELS.noResume : LABELS.resume + ': ' + formatPosition(saver.resumePosition.value)))
            : { value: LABELS.noResume };
        const showDiff = V && V.ref ? V.ref(false) : { value: false };
        const diffRows = V && V.computed
            ? V.computed(() => (showDiff.value ? saver.compare() || [] : []))
            : { value: [] };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        function onKey(event) {
            if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 's') {
                event.preventDefault();
                saver.save();
            }
        }
        return { ...saver, LABELS, surface, resumeText, focusStyle, onKey, showDiff, diffRows };
    },
    template: `
<section class="clinic-correction-save" :aria-label="LABELS.title" :data-state="state" @keydown="onKey"
  :style="{ background: surface.background, color: surface.color, border: '1px solid ' + surface.border }">
  <p role="status" aria-live="polite"><strong>{{ surface.label }}:</strong> {{ message }}</p>
  <p role="note">{{ resumeText }}</p>
  <button type="button" :style="focusStyle" :disabled="state === 'saving' || state === 'saved'" @click="save()">
    {{ retries > 0 ? LABELS.retry : LABELS.save }}
  </button>
  <button v-if="state === 'conflict'" type="button" :style="focusStyle" @click="reloadLatest()">{{ LABELS.reload }}</button>
  <div v-if="state === 'conflict'" role="group" :aria-label="LABELS.conflictChoice" data-testid="conflict-choice">
    <button type="button" :style="focusStyle" data-testid="keep-mine" @click="keepMine()">{{ LABELS.keepMine }}</button>
    <button type="button" :style="focusStyle" data-testid="take-server" @click="takeServer()">{{ LABELS.takeServer }}</button>
    <button type="button" :style="focusStyle" data-testid="compare" :aria-expanded="String(showDiff)" @click="showDiff = !showDiff">{{ LABELS.compare }}</button>
    <ul v-if="showDiff" data-testid="diff">
      <li v-for="(row, i) in diffRows" :key="i" :data-kind="row.kind">{{ row.kind === 'mine' ? LABELS.mine + ': ' : (row.kind === 'server' ? LABELS.server + ': ' : '') }}{{ row.text }}</li>
    </ul>
  </div>
</section>`,
};

export function registerCorrectionSave(app) {
    app.component('clinic-correction-save', CorrectionSave);
    return app;
}
