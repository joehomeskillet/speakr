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
 *   CorrectionSave: Vue options object, props editor, client, dictationId,
 *     lease, getPositionMs, vue.
 *   registerCorrectionSave(app): app.component('clinic-correction-save', ...).
 *
 * Keyboard: Ctrl+S / Cmd+S inside the section saves; all controls are native
 * buttons (Tab, Enter, Space).
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

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

/** @param {object} options see the header comment */
export function useCorrectionSave(options) {
    const opts = options || {};
    const editor = opts.editor;
    const client = opts.client || createClinicApiClient();
    const dictationId = String(opts.dictationId);
    const lease = opts.lease || null;
    const autosaveMs = opts.autosaveMs === undefined ? 5000 : opts.autosaveMs;
    const maxRetries = opts.maxRetries === undefined ? 3 : opts.maxRetries;
    const setTimer = opts.setTimer || ((fn, ms) => setTimeout(fn, ms));
    const clearTimer = opts.clearTimer || ((id) => clearTimeout(id));
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(dictationId) + '/correction';

    const state = refs.ref('saved');
    const message = refs.ref(LABELS.saved);
    const resumePosition = refs.ref(null);
    const stateVersion = refs.ref(null);
    const retries = refs.ref(0);

    let position = null;
    let pending = null; // { key, digest } kept across retries of the same payload
    let timer = null;
    let inflight = false;
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

    async function save() {
        if (disposed) return { ok: false, state: state.value, message: '' };
        if (inflight) return { ok: false, state: 'saving', message: LABELS.saving };
        if (terminal) return { ok: false, state: state.value, message: message.value };
        stop();
        const gen = generation();
        if (!editor.canEdit.value || gen === null) {
            return fail('lost', LABELS.readOnly);
        }
        const sent = editor.text.value;
        const body = {
            text: sent,
            audio_position_ms: currentPosition(),
            lease_generation: gen,
            expected_text_revision_id: editor.textRevisionId.value,
        };
        if (stateVersion.value !== null) body.expected_state_version = stateVersion.value;
        const digest = JSON.stringify(body);
        if (!pending || pending.digest !== digest) pending = { key: newKey(), digest };
        inflight = true;
        set('saving');
        const res = await client.request(base + '/save', {
            method: 'POST',
            idempotencyKey: pending.key,
            ifMatch: editor.etag.value || undefined,
            body,
        });
        inflight = false;
        if (disposed || res.outcome === 'cancelled') return { ok: false, state: state.value, message: '' };
        if (res.outcome === 'ok' && res.data && typeof res.data.text_revision_id === 'string') {
            const d = res.data;
            pending = null;
            retries.value = 0;
            editor.textRevisionId.value = d.text_revision_id;
            editor.etag.value = '"' + d.text_revision_id + '"';
            stateVersion.value = Number.isInteger(d.state_version) ? d.state_version : stateVersion.value;
            if (Number.isInteger(d.audio_position_ms)) resumePosition.value = d.audio_position_ms;
            if (editor.text.value === sent) {
                editor.dirty.value = false;
                if (lease) lease.setUnsaved(null);
                set('saved');
            } else {
                set('unsaved');
                schedule(autosaveMs);
            }
            return { ok: true, state: state.value };
        }
        if (res.status === 423) {
            if (lease) lease.notifyGeneration(-1);
            return fail('lost');
        }
        if (res.outcome === 'conflict' || res.status === 409) return fail('conflict');
        if (res.outcome === 'permission') return fail('lost', LABELS.denied);
        if (res.outcome === 'network' || res.outcome === 'UNKNOWN') {
            // Same key on retry: the server replays instead of duplicating.
            retries.value += 1;
            set('unsaved', LABELS.timeout);
            if (retries.value <= maxRetries) schedule(autosaveMs * 2 * retries.value);
            return { ok: false, state: 'unsaved', message: message.value, retryable: true };
        }
        pending = null;
        set('unsaved', LABELS.failed);
        return { ok: false, state: 'unsaved', message: message.value };
    }

    /** Stale tab: drop local edits, load the server text and the committed position. */
    async function reloadLatest() {
        stop();
        terminal = null;
        pending = null;
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
        state, message, resumePosition, stateVersion, retries,
        save, touch, loadResume, reloadLatest, refresh, setPosition, dispose,
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
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        function onKey(event) {
            if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === 's') {
                event.preventDefault();
                saver.save();
            }
        }
        return { ...saver, LABELS, surface, resumeText, focusStyle, onKey };
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
</section>`,
};

export function registerCorrectionSave(app) {
    app.component('clinic-correction-save', CorrectionSave);
    return app;
}
