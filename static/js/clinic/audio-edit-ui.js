/**
 * Operate the non-destructive audio edit list of one clinic dictation.
 *
 * Insert, overwrite, cut and trim go to the audio-edits route through the
 * clinic api client. Every call carries the audio revision the user is looking
 * at and the current lease generation; the original audio is never touched.
 * A stale head (409) clears the selection, reloads the head and reports the
 * conflict instead of retrying silently. Text anchors the server invalidated
 * stay visible until the caller acknowledges them. Clinical state lives in
 * memory only.
 */

import { surfaceFor } from './tokens.js';

export const OPERATIONS = Object.freeze(['insert', 'overwrite', 'cut', 'trim']);
export const MIN_UNDO_DEPTH = 16;
const DEFAULT_SAMPLE_RATE = 16000;

const MESSAGES = Object.freeze({
    idle: 'Audio wird geladen …',
    ready: 'Bereit zur Bearbeitung.',
    readOnly: 'Keine Bearbeitung ohne aktive Bearbeitungssperre.',
    busy: 'Eine Änderung wird gespeichert …',
    invalid: 'Die Auswahl ist ungültig.',
    stale: 'Die Audioversion ist veraltet. Auswahl zurückgesetzt, aktuelle Version geladen.',
    nothingToUndo: 'Nichts zum Rückgängigmachen.',
    nothingToRedo: 'Nichts zum Wiederholen.',
    anchors: 'Textmarken wurden ungültig und müssen geprüft werden.',
});

const OP_LABELS = Object.freeze({
    insert: 'Einfügen', overwrite: 'Überschreiben', cut: 'Ausschneiden', trim: 'Kürzen',
    undo: 'Rückgängig', redo: 'Wiederholen',
});

function defaultKey() {
    return globalThis.crypto.randomUUID();
}

function totalMs(segments) {
    return segments.reduce((sum, s) => sum + Math.max(0, s.end_ms - s.start_ms), 0);
}

/** Selection in samples, bounded to the audio and rounded to whole milliseconds. */
export function boundSelection(startSample, endSample, sampleRate, durationMs) {
    const rate = Number.isInteger(sampleRate) && sampleRate > 0 ? sampleRate : DEFAULT_SAMPLE_RATE;
    if (!Number.isFinite(startSample) || !Number.isFinite(endSample)) return null;
    const toMs = (n) => Math.round((Math.trunc(n) * 1000) / rate);
    const lo = Math.min(Math.max(toMs(Math.min(startSample, endSample)), 0), durationMs);
    const hi = Math.min(Math.max(toMs(Math.max(startSample, endSample)), 0), durationMs);
    return { startMs: lo, endMs: hi };
}

/**
 * @param {object} options
 * @param {{request: Function}} options.client clinic api client
 * @param {string} options.dictationId
 * @param {{canWrite: () => boolean, getState: () => {generation: number|null}}} options.lease
 * @param {number} [options.sampleRate]
 * @param {() => string} [options.newKey] must return a canonical lowercase UUID
 */
export function createAudioEditController(options) {
    const { client, dictationId, lease } = options;
    const sampleRate = options.sampleRate || DEFAULT_SAMPLE_RATE;
    const newKey = options.newKey || defaultKey;
    const base = '/dictations/' + encodeURIComponent(dictationId);
    const listeners = new Set();

    let status = 'idle';
    let revisionId = null;
    let segments = [];
    let selection = null;
    let undoDepth = 0;
    let redoDepth = 0;
    let note = '';
    let busy = false;
    let invalidated = [];
    let pending = null; // { digest, key } kept after an unknown outcome so a retry replays

    function canWrite() {
        return Boolean(lease && lease.canWrite()) && revisionId !== null && status !== 'idle';
    }

    function snapshot() {
        const writable = canWrite() && !busy;
        const key = status === 'error' || status === 'stale' ? 'error' : (busy ? 'processing' : 'normal');
        return {
            status,
            revisionId,
            durationMs: totalMs(segments),
            selection,
            writable,
            busy,
            canUndo: writable && undoDepth > 0,
            canRedo: writable && redoDepth > 0,
            undoDepth,
            redoDepth,
            invalidatedLinkIds: invalidated.slice(),
            needsAnchorReview: invalidated.length > 0,
            message: note || (invalidated.length ? MESSAGES.anchors : (canWrite() ? MESSAGES.ready : (status === 'idle' ? MESSAGES.idle : MESSAGES.readOnly))),
            surface: surfaceFor(key),
        };
    }

    function emit() {
        const snap = snapshot();
        for (const fn of listeners) fn(snap);
    }

    function setNote(text) {
        note = text || '';
        emit();
        return snapshot();
    }

    async function load() {
        const res = await client.request(base + '/audio-revisions/current');
        if (res.outcome === 'cancelled') return snapshot();
        if (res.outcome !== 'ok' || !res.data || typeof res.data.revision_id !== 'string' || !Array.isArray(res.data.segments)) {
            status = 'error';
            return setNote(res.message || MESSAGES.invalid);
        }
        revisionId = res.data.revision_id;
        segments = res.data.segments;
        status = 'ready';
        selection = null;
        note = '';
        emit();
        return snapshot();
    }

    function select(startSample, endSample) {
        const sel = boundSelection(startSample, endSample, sampleRate, totalMs(segments));
        if (!sel) {
            selection = null;
            return setNote(MESSAGES.invalid);
        }
        selection = sel;
        note = '';
        emit();
        return snapshot();
    }

    /** Local what-if for the preview: resulting duration, or null when the request is invalid. */
    function preview(operation, params) {
        const total = totalMs(segments);
        const p = params || {};
        if (operation === 'cut') {
            if (!selection || selection.endMs <= selection.startMs) return null;
            return { operation, durationMs: total - (selection.endMs - selection.startMs), url: derivedUrl() };
        }
        if (operation === 'trim') {
            const at = p.positionMs !== undefined ? p.positionMs : (selection && selection.startMs);
            if (!Number.isInteger(at) || at < 0 || at > total) return null;
            return { operation, durationMs: at, url: derivedUrl() };
        }
        if (operation === 'insert' || operation === 'overwrite') {
            const at = p.positionMs !== undefined ? p.positionMs : (selection && selection.startMs);
            if (!Number.isInteger(at) || at < 0 || at > total || !Number.isInteger(p.durationMs) || p.durationMs < 1) return null;
            const durationMs = operation === 'insert' ? total + p.durationMs : Math.max(total, at + p.durationMs);
            return { operation, durationMs, url: derivedUrl() };
        }
        return null;
    }

    function derivedUrl() {
        return revisionId === null ? null
            : '/api/clinic/v1' + base + '/audio/derived?format=wav&audio_revision_id=' + encodeURIComponent(revisionId);
    }

    function bodyFor(operation, p) {
        const body = { operation, audio_revision_id: revisionId, lease_generation: lease.getState().generation };
        if (operation === 'cut') {
            if (!selection || selection.endMs <= selection.startMs) return null;
            body.start_ms = selection.startMs;
            body.end_ms = selection.endMs;
        } else if (operation === 'trim') {
            const at = p.positionMs !== undefined ? p.positionMs : (selection && selection.startMs);
            if (!Number.isInteger(at) || at < 0 || at > totalMs(segments)) return null;
            body.position_ms = at;
        } else if (operation === 'insert' || operation === 'overwrite') {
            const at = p.positionMs !== undefined ? p.positionMs : (selection && selection.startMs);
            if (!Number.isInteger(at) || at < 0 || at > totalMs(segments) || typeof p.sourceSegmentId !== 'string') return null;
            body.position_ms = at;
            body.source_segment_id = p.sourceSegmentId;
            if (p.durationMs !== undefined) body.duration_ms = p.durationMs;
            if (p.sourceStartMs !== undefined) body.source_start_ms = p.sourceStartMs;
        }
        return body;
    }

    async function send(operation, params) {
        if (busy) return setNote(MESSAGES.busy);
        if (!canWrite()) return setNote(MESSAGES.readOnly);
        const body = bodyFor(operation, params || {});
        if (!body) return setNote(MESSAGES.invalid);
        const digest = JSON.stringify(body);
        if (!pending || pending.digest !== digest) pending = { digest, key: newKey() };
        busy = true;
        note = '';
        emit();
        const res = await client.request(base + '/audio-edits', { method: 'POST', idempotencyKey: pending.key, body });
        busy = false;
        if (res.outcome === 'cancelled') return setNote('');
        if (res.outcome === 'ok') {
            pending = null;
            invalidated = invalidated.concat((res.data && res.data.invalidated_link_ids) || []);
            if (operation === 'undo') { undoDepth = Math.max(0, undoDepth - 1); redoDepth += 1; }
            else if (operation === 'redo') { redoDepth = Math.max(0, redoDepth - 1); undoDepth += 1; }
            else { undoDepth += 1; redoDepth = 0; }
            return load();
        }
        if (res.outcome === 'conflict') {
            pending = null;
            const prior = invalidated;
            await load();
            invalidated = prior;
            status = 'stale';
            return setNote(MESSAGES.stale);
        }
        if (res.outcome === 'network' || res.outcome === 'UNKNOWN') {
            // keep the key: a retry of the same request replays instead of duplicating
            return setNote(res.message);
        }
        pending = null;
        return setNote(res.message);
    }

    return {
        load,
        select,
        preview,
        derivedUrl,
        insert: (params) => send('insert', params),
        overwrite: (params) => send('overwrite', params),
        cut: () => send('cut'),
        trim: (params) => send('trim', params),
        undo: () => (undoDepth > 0 ? send('undo') : setNote(MESSAGES.nothingToUndo)),
        redo: () => (redoDepth > 0 ? send('redo') : setNote(MESSAGES.nothingToRedo)),
        /** The caller has reviewed the invalidated text anchors. */
        acknowledgeAnchors() { invalidated = []; return setNote(''); },
        /** Clears a stale marker after the user saw the reloaded head. */
        acknowledgeStale() { if (status === 'stale') { status = 'ready'; return setNote(''); } return snapshot(); },
        getState: snapshot,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        dispose() { listeners.clear(); segments = []; selection = null; invalidated = []; pending = null; },
    };
}

/** Toolbar buttons with labels, shortcuts and disabled state. */
export function audioEditActions(state) {
    const sel = state.selection && state.selection.endMs > state.selection.startMs;
    const w = state.writable;
    return [
        { id: 'cut', label: OP_LABELS.cut, shortcut: 'Delete', disabled: !w || !sel },
        { id: 'trim', label: OP_LABELS.trim, shortcut: 'Alt+T', disabled: !w || !state.selection },
        { id: 'insert', label: OP_LABELS.insert, shortcut: 'Alt+I', disabled: !w || !state.selection },
        { id: 'overwrite', label: OP_LABELS.overwrite, shortcut: 'Alt+O', disabled: !w || !state.selection },
        { id: 'undo', label: OP_LABELS.undo, shortcut: 'Strg+Z', disabled: !state.canUndo },
        { id: 'redo', label: OP_LABELS.redo, shortcut: 'Strg+Umschalt+Z', disabled: !state.canRedo },
    ];
}

/** Vue composable. Pass the Vue namespace (window.Vue in the app). */
export function useAudioEdit(options, vue) {
    const V = vue || globalThis.Vue;
    const controller = createAudioEditController(options);
    const state = V.shallowRef(controller.getState());
    const unsubscribe = controller.subscribe((snap) => { state.value = snap; });
    function dispose() { unsubscribe(); controller.dispose(); }
    if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(dispose);
    return { state, controller, dispose };
}

/** Component: toolbar with live status region; native buttons and Ctrl+Z / Ctrl+Shift+Z. */
export function createAudioEditComponent(vue) {
    const V = vue || globalThis.Vue;
    return {
        name: 'ClinicAudioEdit',
        props: {
            state: { type: Object, required: true },
            controller: { type: Object, required: true },
            sourceSegmentId: { type: String, default: null },
            insertDurationMs: { type: Number, default: undefined },
        },
        setup(props) {
            const actions = V.computed(() => audioEditActions(props.state));
            function run(id) {
                const c = props.controller;
                if (id === 'insert' || id === 'overwrite') {
                    return c[id]({ sourceSegmentId: props.sourceSegmentId, durationMs: props.insertDurationMs });
                }
                return c[id]();
            }
            function onKey(event) {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
                    event.preventDefault();
                    run(event.shiftKey ? 'redo' : 'undo');
                }
            }
            return { actions, run, onKey };
        },
        template: `
            <section class="clinic-audio-edit" role="group" aria-label="Audio bearbeiten"
                :data-status="state.status" @keydown="onKey"
                :style="{ background: state.surface.background, color: state.surface.color, border: '1px solid ' + state.surface.border }">
                <p role="status" aria-live="polite">{{ state.message }}</p>
                <p v-if="state.needsAnchorReview" role="alert">
                    Textmarken wurden ungültig. Bitte prüfen und bestätigen.
                    <button type="button" @click="controller.acknowledgeAnchors()">Geprüft</button>
                </p>
                <p v-if="state.selection">
                    Auswahl: {{ state.selection.startMs }} bis {{ state.selection.endMs }} ms
                    von {{ state.durationMs }} ms
                </p>
                <button v-for="a in actions" :key="a.id" type="button" :disabled="a.disabled"
                    :aria-keyshortcuts="a.shortcut" @click="run(a.id)">{{ a.label }}</button>
            </section>`,
    };
}
