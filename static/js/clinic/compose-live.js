/**
 * Composed clinic live dictation view (V8 P05, PAR-LIVE-001/003/004).
 *
 * One Vue view over the existing live building block: live-session-ui
 * (createLiveSessionController: phrase states, durable receipts, resume from the
 * checkpoint). The view owns only the wiring: start, pause, continue and stop
 * commands, the reconnect state and the accessible announcements.
 *
 * Server contract (src/clinic/routes_live.py):
 *   start  parity command start_live_session {dictation_id}, then the resume
 *          POST adopts the new session id and position;
 *   pause / continue  POST live/sessions/<id>/pause and /continue;
 *   resume            recover() returns state PAUSED; the view stays paused until
 *                     continue answers with a state other than PAUSED;
 *   stop   parity command stop_live_session {session_id, generation, last_sequence}
 *          and expected_revision = ack_seq + 1 (positive server revision);
 *   phrases and reconnect  handled by the live-session controller.
 *
 * Phases: idle, starting, live, paused, reconnecting, stopping, stopped, stale, error.
 * The recorder announces the phase, never a word, a partial or a phrase.
 *
 * Persistence: none in the browser. Phrases, partial text and audio references
 * stay in memory and are cleared on dispose. Durable draft state is server-side
 * (src/clinic/live_drafts.py LiveDrafts, live_phrases) and comes back through the
 * resume call. Draft switching (POST live/drafts/<id>/switch) is not wired here.
 * Switching "dictationId" re-keys the body, which disposes the previous one.
 * No patient data is shown: the view shows the dictation id only.
 *
 * Exports:
 *   LiveView  Vue options; props client, dictationId, vue?, canWrite?, onReady? (receives the controller).
 *   registerLiveView(app)  app.component('clinic-live-view', LiveView).
 *   createLiveViewController(options)  the controller behind the view.
 *   liveViewActions(state)  the buttons for a state.
 */

import { createClinicApiClient } from './api-client.js';
import { scopedClient } from './compose-correction.js';
import { createLeaseController } from './lease-ui.js';
import { RichTextEditor } from './richtext-editor-ui.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { createLiveSessionController, liveActions } from './live-session-ui.js';

export const COMPOSE_LIVE_VERSION = 1;

export const PHASES = Object.freeze([
    'idle', 'starting', 'live', 'paused', 'reconnecting', 'stopping', 'stopped', 'stale', 'error',
]);

// clinic-v8.css states heading, button and form-control styles with zero-specificity element selectors; the Tailwind
// base layer, loaded after it, resets those. These strings restate them where this page needs them.
const H1 = 'font-size: clamp(26px, 4vw, 30px); line-height: 1.2; font-weight: 700; letter-spacing: -.8px; margin: 0 0 9px';
const H2 = 'font-size: 16px; line-height: 1.35; font-weight: 700; letter-spacing: -.2px; margin: 0 0 14px';

export const TEXT = Object.freeze({
    title: 'Live-Diktat',
    dictation: 'Diktat',
    controls: 'Aufnahme',
    phrases: 'Sätze',
    start: 'Starten',
    pause: 'Pausieren',
    continue: 'Fortsetzen',
    stop: 'Beenden',
    reconnect: 'Verbindung wiederherstellen',
    save: 'Erneut speichern',
    partial: 'Vorläufig',
    unsaved: 'Nicht gespeicherte Sätze sind noch nicht dauerhaft.',
    empty: 'Noch kein Text. Mit der Aufnahmetaste beginnen.',
    ready: 'Bereit zum Diktieren',
    eyebrow: 'Aufnahmen',
    subline: 'Live diktieren. Jeder Satz wird einzeln gespeichert und ist erst danach dauerhaft.',
    transcript: 'Live-Transkription',
    count: (n) => (n === 1 ? '1 Satz' : n + ' Sätze'),
});

/** Announced when the phase changes. Never used for words, partials or phrases. */
export const ANNOUNCEMENTS = Object.freeze({
    idle: 'Keine Live-Sitzung.',
    starting: 'Live-Sitzung wird gestartet.',
    live: 'Live-Diktat läuft.',
    paused: 'Live-Diktat pausiert.',
    reconnecting: 'Verbindung unterbrochen. Wiederaufnahme ab dem gespeicherten Stand.',
    stopping: 'Live-Sitzung wird beendet.',
    stopped: 'Live-Sitzung beendet.',
    stale: 'Sie sind nicht mehr der aktive Sprecher. Neue Sätze werden nicht gespeichert.',
    error: 'Die Live-Sitzung konnte nicht gestartet werden.',
});

const NOTES = Object.freeze({
    stopBlocked: 'Beenden nicht möglich: Es gibt nicht gespeicherte Sätze.',
    stopFailed: 'Beenden fehlgeschlagen. Bitte erneut versuchen.',
    denied: 'Keine Berechtigung für diese Live-Sitzung.',
    retry: 'Verbindung unterbrochen. Speichern wird wiederholt.',
});

/** Inline SVG paths (24x24) of the live actions shown as icon buttons. The other actions stay text buttons. */
const ICON_PATHS = Object.freeze({
    start: 'M12 5a7 7 0 1 0 0 14a7 7 0 0 0 0-14z',
    pause: 'M7 5h3v14H7z M14 5h3v14h-3z',
    continueRecording: 'M8 5l11 7-11 7z',
    stop: 'M6 6h12v12H6z',
});

const LIVE = '/api/clinic/v1/live';
const defaultKey = () => globalThis.crypto.randomUUID();

const FOCUS = { '--focus-ring': FOCUS_INDICATOR.outlineColor, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
const box = (name) => {
    const s = surfaceFor(name);
    return { background: s.background, color: s.color, border: '1px solid ' + s.border };
};

/** Buttons for a state. The save entry comes from the live-session block. */
export function liveViewActions(state) {
    const p = state.phase;
    const actions = [];
    if (p === 'idle' || p === 'stopped' || p === 'error') actions.push({ id: 'start', label: TEXT.start, disabled: false });
    if (p === 'live') actions.push({ id: 'pause', label: TEXT.pause, disabled: false });
    if (p === 'paused') actions.push({ id: 'continueRecording', label: TEXT.continue, disabled: false });
    if (p === 'live' || p === 'paused') actions.push({ id: 'stop', label: TEXT.stop, disabled: false });
    if (p === 'reconnecting' || p === 'stale') actions.push({ id: 'reconnect', label: TEXT.reconnect, disabled: false });
    for (const a of liveActions(state.session)) {
        if (a.id === 'save') actions.push({ id: 'saveUnsaved', label: TEXT.save, disabled: a.disabled });
    }
    return actions;
}

/**
 * Controller behind LiveView. Feed it from the transport with partial(), finalizePhrase()
 * and markDisconnected(); the view buttons call start, pause, continueRecording, stop, reconnect.
 */
export function createLiveViewController(options) {
    const opts = options || {};
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    const dictationId = String(opts.dictationId);
    const base = opts.client || createClinicApiClient();
    if (!base || typeof base.request !== 'function' || typeof base.command !== 'function') throw new Error('client is required');
    const newKey = opts.newKey || defaultKey;
    const canWriteOuter = opts.canWrite || (() => true);
    const listeners = new Set();

    let phase = 'idle';
    let announcement = '';
    let note = '';
    let lost = false; // the last request ended without an answer
    let disposed = false;

    const client = {
        async request(path, o) {
            const res = await base.request(path, o);
            if (res.outcome === 'network') lost = true;
            else if (res.outcome === 'ok') lost = false;
            return res;
        },
        command: (body, o) => base.command(body, o),
        switchContext: (next) => { if (typeof base.switchContext === 'function') base.switchContext(next); },
    };

    const session = createLiveSessionController({
        client, dictationId, newKey,
        canWrite: () => phase === 'live' && canWriteOuter(),
    });

    function snapshot() {
        const s = session.getState();
        return { phase, announcement, note, dictationId, session: s, actions: [] };
    }

    function state() {
        const snap = snapshot();
        snap.actions = liveViewActions(snap);
        return snap;
    }

    function emit() {
        const snap = state();
        for (const fn of listeners) fn(snap);
    }

    function setPhase(next, text) {
        note = text || '';
        if (next !== phase) {
            phase = next;
            announcement = ANNOUNCEMENTS[next] || '';
        }
        emit();
    }

    session.subscribe(() => { if (!disposed) emit(); });

    /** Map a failed answer to a phase. Conflict and permission end the holder role. */
    function failed(res, back) {
        if (res.outcome === 'conflict' || res.outcome === 'permission') {
            session.holderLost();
            setPhase('stale', res.outcome === 'permission' ? NOTES.denied : '');
        } else if (res.outcome === 'network') {
            setPhase('reconnecting', NOTES.retry);
        } else {
            setPhase(back, res.message || '');
        }
    }

    async function saveUnsaved() {
        for (const p of session.getState().phrases) {
            if (p.state === 'final_unsaved') await session.save(p.sequence);
        }
        if (lost && phase === 'live') setPhase('reconnecting', NOTES.retry);
        return state();
    }

    async function adoptSession() {
        const snap = await session.resume();
        if (disposed) return state();
        if (lost) setPhase('reconnecting', NOTES.retry);
        else if (snap.status === 'paused') setPhase('paused');
        else if (snap.status === 'live') setPhase('live');
        else if (snap.status === 'stale') setPhase('stale');
        else setPhase('error');
        return state();
    }

    return {
        getState: state,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        session,

        async start() {
            if (phase !== 'idle' && phase !== 'stopped' && phase !== 'error') return state();
            setPhase('starting');
            const res = await client.command({
                command: 'start_live_session', request_id: newKey(), expected_revision: 0,
                payload: { dictation_id: dictationId },
            }, { idempotencyKey: newKey() });
            if (disposed || res.outcome === 'cancelled') return state();
            // An open session of this holder (conflict) is adopted through the same resume call.
            if (res.outcome === 'ok' || res.outcome === 'conflict') return adoptSession();
            if (res.outcome === 'permission') setPhase('error', NOTES.denied);
            else setPhase('error', res.message || '');
            return state();
        },

        async pause() {
            if (phase !== 'live') return state();
            const id = session.getState().sessionId;
            session.setPartial(null); // a partial is never durable and is dropped
            const res = await client.request(LIVE + '/sessions/' + encodeURIComponent(id) + '/pause', {
                method: 'POST', idempotencyKey: newKey(), body: {},
            });
            if (disposed || res.outcome === 'cancelled') return state();
            if (res.outcome === 'ok') {
                const data = res.data || {};
                // An answer that is still RECORDING did not pause.
                session.applyServerState(data.state === 'RECORDING' ? data : { ...data, state: 'PAUSED' });
                setPhase(session.getState().status === 'live' ? 'live' : 'paused');
            } else failed(res, 'live');
            return state();
        },

        async continueRecording() {
            if (phase !== 'paused') return state();
            const id = session.getState().sessionId;
            const res = await client.request(LIVE + '/sessions/' + encodeURIComponent(id) + '/continue', {
                method: 'POST', idempotencyKey: newKey(), body: {},
            });
            if (disposed || res.outcome === 'cancelled') return state();
            // Phase stays paused until this answer. PAUSED is not recording.
            if (res.outcome === 'ok') {
                const data = res.data || {};
                if (data.state === 'PAUSED') {
                    session.applyServerState(data);
                    setPhase('paused');
                } else {
                    session.applyServerState({ ...data, state: 'RECORDING' });
                    setPhase('live');
                }
            } else failed(res, 'paused');
            return state();
        },

        /** Stop needs every final phrase saved; otherwise the stop would drop text. */
        async stop() {
            if (phase !== 'live' && phase !== 'paused') return state();
            const back = phase;
            const wasLive = phase === 'live';
            if (!wasLive) phase = 'live'; // saving needs the writable gate
            await saveUnsaved();
            phase = back;
            if (disposed) return state();
            const afterSave = session.getState();
            if (afterSave.hasUnsaved || afterSave.status === 'stale') {
                // A conflict or a failed save keeps the phrase text and does not end the session.
                if (afterSave.status === 'stale') setPhase('stale', afterSave.hasUnsaved ? NOTES.stopBlocked : NOTES.stopFailed);
                else setPhase(lost ? 'reconnecting' : back, NOTES.stopBlocked);
                return state();
            }
            const s = session.getState();
            if (!Number.isInteger(s.revision) || s.revision < 1) {
                setPhase(back, NOTES.stopFailed);
                return state();
            }
            setPhase('stopping');
            const res = await client.command({
                command: 'stop_live_session', request_id: newKey(), expected_revision: s.revision,
                payload: { session_id: s.sessionId, generation: s.generation, last_sequence: s.checkpoint },
            }, { idempotencyKey: newKey() });
            if (disposed || res.outcome === 'cancelled') return state();
            if (res.outcome === 'ok') setPhase('stopped');
            else if (res.outcome === 'conflict') setPhase(back, NOTES.stopFailed);
            else failed(res, back);
            return state();
        },

        /** Resume from the durable checkpoint, then retry the unsaved phrases of the adopted generation. */
        async reconnect() {
            if (phase !== 'reconnecting' && phase !== 'stale' && phase !== 'live' && phase !== 'paused') return state();
            setPhase('reconnecting');
            await adoptSession();
            if (disposed) return state();
            if (session.getState().hasUnsaved && (phase === 'live' || phase === 'paused')) {
                const back = phase;
                phase = 'live'; // save gate only; the view still shows the last emitted phase
                await saveUnsaved();
                if (!disposed && phase === 'live') setPhase(back);
            }
            return state();
        },

        /** The transport lost its connection. */
        markDisconnected() {
            if (phase === 'live' || phase === 'paused') setPhase('reconnecting', NOTES.retry);
        },

        /** Interim recognition text. It replaces the previous partial and is never announced. */
        partial(text) { return session.setPartial(text); },

        /** A final phrase from the engine: take the next sequence and save it. */
        async finalizePhrase(text, wire) {
            const p = session.finalize(text, wire);
            if (!p) return null;
            await session.save(p.sequence);
            if (lost && phase === 'live') setPhase('reconnecting', NOTES.retry);
            return p;
        },

        saveUnsaved,

        dispose() {
            disposed = true;
            listeners.clear();
            session.dispose(); // clears phrases, partial and session id from memory
        },
    };
}

const BODY_PROPS = {
    client: { type: Object, default: null },
    dictationId: { type: String, required: true },
    currentUser: { type: Object, default: null },
    vue: { type: Object, default: null },
    canWrite: { type: Function, default: null },
    onReady: { type: Function, default: null },
};

function useLiveDocument(V, props, state) {
    const client = scopedClient(props.client || createClinicApiClient());
    const scope = '/dictations/' + encodeURIComponent(props.dictationId);
    const path = scope + '/correction';
    const graphicsPath = scope + '/graphics';
    const editor = V.shallowRef(null);
    const document = V.shallowRef(null);
    const loading = V.ref(false);
    const loadFailed = V.ref(false);
    let base;
    let pending;
    let acquiring;
    let disposed = false;

    client.switchContext({ accountId: props.currentUser.id, dictationId: props.dictationId });
    const lease = createLeaseController({ client, dictationId: props.dictationId });
    const held = V.shallowRef(lease.getState());
    const unsubscribe = lease.subscribe((next) => { held.value = next; });
    const writable = V.computed(() => held.value.writable && document.value !== null && !loading.value
        && (!props.canWrite || props.canWrite()));

    async function load(discard = false) {
        if (disposed || loading.value || (!discard && editor.value?.dirty)) return false;
        loading.value = true;
        const res = await client.request(path);
        if (disposed) return false;
        loading.value = false;
        const data = res.outcome === 'ok' ? res.data : null;
        if (!data || typeof data.text !== 'string' || typeof data.etag !== 'string') {
            loadFailed.value = true;
            return false;
        }
        loadFailed.value = false;
        base = data;
        pending = null;
        // Old revisions have no snapshot. An empty document still needs a place to type.
        const lines = data.text.split('\n', 2049);
        document.value = data.document?.nodes.length ? data.document : {
            schema_version: 1,
            nodes: (lines.length > 2048 ? [data.text] : lines).map((text) => ({
                id: defaultKey(), kind: 'paragraph', level: 0,
                runs: [{ id: defaultKey(), text, audio_anchor_id: null }],
            })),
        };
        return true;
    }

    async function save(sent) {
        if (disposed || !writable.value || !lease.canWrite()) return { outcome: 'permission' };
        const { generation } = held.value;
        if (!pending || pending.body.lease_generation !== generation) {
            // Keep the exact key, validator and payload until an uncertain save is settled.
            const text = sent.nodes.map((node) => node.kind === 'graphic'
                ? '[graphic: ' + node.alt_text + ']' : node.runs.map((run) => run.text).join('')).join('\n');
            pending = { sent, method: 'POST', idempotencyKey: defaultKey(),
                ifMatch: base.etag.replace(/\.g\d+"$/, '.g' + generation + '"'),
                body: { text, document: sent, lease_generation: generation,
                    expected_text_revision_id: base.text_revision_id,
                    expected_document_version: base.document_version ?? 0,
                    expected_state_version: base.state?.version,
                    audio_position_ms: base.state?.audio_position_ms ?? 0 } };
        }
        const request = pending;
        const res = await client.request(path + '/save', request);
        if (disposed) return { outcome: 'cancelled' };
        if (res.status === 423 || res.code === 'LEASE_STALE' || res.outcome === 'permission') {
            if (held.value.generation === generation) lease.notifyGeneration(-1);
            pending = null;
            return { outcome: 'permission' };
        }
        if (res.outcome === 'network' || res.outcome === 'UNKNOWN') return res;
        if (res.outcome !== 'ok') { pending = null; return res; }
        const data = res.data;
        if (!data?.document || !Number.isInteger(data.document_version) || typeof data.etag !== 'string'
            || typeof data.text_revision_id !== 'string') return { outcome: 'UNKNOWN' };
        pending = null;
        base = { ...data, state: { version: data.state_version, audio_position_ms: data.audio_position_ms } };
        return { ...res, sent: request.sent };
    }

    async function addGraphic(file) {
        if (!writable.value || !lease.canWrite() || file.type !== 'image/png' || file.size > 2 * 1024 * 1024) return null;
        const generation = held.value.generation;
        const res = await client.request(graphicsPath, { method: 'POST', png: file });
        if (disposed || !writable.value || !lease.canWrite() || generation !== held.value.generation) return null;
        return res.status === 201 && res.data?.dictation_id === props.dictationId ? res.data.object_id : null;
    }

    V.watch(() => state.value.session.checkpoint, () => { void load(); });
    function warnOnLeave(event) {
        if (editor.value?.dirty) { event.preventDefault(); event.returnValue = ''; }
    }
    V.onMounted(() => { void load(); globalThis.addEventListener('beforeunload', warnOnLeave); });
    V.onBeforeUnmount(() => {
        disposed = true;
        globalThis.removeEventListener('beforeunload', warnOnLeave);
        unsubscribe();
        const release = () => Promise.resolve(lease.release()).finally(lease.dispose);
        if (acquiring) void acquiring.finally(release);
        else void release();
    });
    return { client, editor, document, loading, loadFailed, held, writable, load, save, addGraphic,
        graphicsPath: '/api/clinic/v1' + graphicsPath,
        acquire: () => {
            acquiring = lease.acquire().then((lock) => {
                if (lock.writable) return load();
            }).finally(() => { acquiring = null; });
        },
        release: () => lease.release() };
}

/** Inner body: one instance per dictation. */
const LiveBody = {
    name: 'ClinicLiveBody',
    props: BODY_PROPS,
    components: { RichTextEditor },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the live view');
        const c = createLiveViewController({
            client: props.client || undefined,
            dictationId: props.dictationId,
            canWrite: props.canWrite || undefined,
        });
        const state = V.shallowRef(c.getState());
        const stop = c.subscribe((snap) => { state.value = snap; });
        if (typeof V.onMounted === 'function') V.onMounted(() => { if (props.onReady) props.onReady(c); });
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => { stop(); c.dispose(); });
        const writing = props.currentUser?.id != null ? useLiveDocument(V, props, state) : null;
        const richEditor = writing?.editor;
        const canLeave = () => !richEditor?.value?.dirty
            || globalThis.confirm('Ungespeicherte Änderungen gehen verloren. Live-Diktat verlassen?');
        return { c, state, writing, richEditor, canLeave, FOCUS, box, TEXT, ICON_PATHS };
    },
    template: `
<section class="clinic-live-view v8-page" data-testid="clinic-live-view" aria-labelledby="clv-title" :data-phase="state.phase">
  <nav class="breadcrumb" aria-label="Pfad"><span>Speakr</span><span aria-hidden="true">›</span><span>{{ TEXT.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ TEXT.title }}</span></nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <h1 id="clv-title" style="${H1}">{{ TEXT.title }}</h1>
      <p class="subline">{{ TEXT.subline }}</p>
    </div>
    <div class="head-actions"></div>
  </div>
  <section class="editor-workspace" :aria-label="TEXT.transcript">
    <div class="editor-heading">
      <div class="document-title">
        <h2 style="${H2}; margin: 0">{{ TEXT.transcript }}</h2>
        <span class="document-id">{{ TEXT.dictation }} {{ state.dictationId }}</span>
      </div>
      <span class="tag neutral dot">{{ TEXT.count(state.session.phrases.length) }}</span>
    </div>
    <div class="recorder compact-recorder" :class="{ recording: state.phase === 'live' }">
      <div class="recorder-controls" role="group" :aria-label="TEXT.controls" data-testid="live-controls">
        <template v-for="a in state.actions" :key="a.id">
          <button v-if="ICON_PATHS[a.id]" type="button" class="icon-button" :class="{ 'record-button': a.id === 'start' }"
                  :style="[FOCUS, { minWidth: '48px', minHeight: '48px' }]"
                  :disabled="a.disabled" :data-action="a.id" :aria-label="a.label" :title="a.label" @click="c[a.id]()">
            <svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="ICON_PATHS[a.id]"></path></svg>
          </button>
          <button v-else type="button" class="button" style="min-height:48px" :style="FOCUS" :disabled="a.disabled" :data-action="a.id" @click="c[a.id]()">{{ a.label }}</button>
        </template>
      </div>
      <div class="recorder-caption">
        <strong v-if="!state.announcement" data-testid="live-caption">{{ TEXT.ready }}</strong>
        <span role="status" aria-live="polite" data-testid="live-announcement">{{ state.announcement }}</span>
        <small v-if="state.note" role="note" data-testid="live-note">{{ state.note }}</small>
      </div>
    </div>
    <p v-if="state.session.hasUnsaved" role="note" class="note warning" style="margin: 0; border-radius: 0; border-width: 0 0 1px">{{ TEXT.unsaved }}</p>
    <div class="writing-surface" style="min-height: clamp(360px, calc(100dvh - 370px), 760px)"
      :style="writing ? { minHeight: 'clamp(360px, calc(100dvh - 430px), 760px)' } : null">
      <div style="width: 100%; min-width: 0">
        <ol class="live-phrases" :aria-label="TEXT.phrases" aria-live="off" data-testid="live-phrases"
            style="list-style: none; margin: 0; display: grid; gap: 4px; font-size: 16px; line-height: 1.75; align-content: start"
            :style="state.session.phrases.length || state.session.partial ? { padding: '22px 28px', minHeight: '240px' } : null">
          <li v-for="p in state.session.phrases" :key="p.generation + '-' + p.sequence" :data-state="p.state"
              style="display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 14px; padding: 8px 0; border-bottom: 1px solid var(--v8-line-2)">
            <span style="flex: 1 1 320px; min-width: 0; overflow-wrap: anywhere">{{ p.text }}</span>
            <small class="tag" :class="p.state === 'final_unsaved' ? 'wait' : (p.state === 'stale' ? 'error' : 'neutral')">{{ p.view.position }}: <strong style="display: inline">{{ p.view.label }}</strong></small>
          </li>
          <li v-if="state.session.partial" data-state="partial" style="padding: 8px 0; color: var(--v8-muted)"><em>{{ state.session.partial }}</em> <small class="tag neutral">{{ TEXT.partial }}</small></li>
        </ol>
        <div v-if="writing" style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:8px 16px;border-bottom:1px solid var(--v8-line-2)">
          <p role="status" style="margin:0">{{ writing.held.value.message }}</p>
          <p v-if="writing.loading.value && !writing.document.value" role="status">Text wird geladen …</p>
          <button v-if="writing.document.value && ['idle', 'lost', 'error'].includes(writing.held.value.status) && writing.held.value.mayAcquire !== false"
            type="button" class="button" style="min-height:48px" @click="writing.acquire">Bearbeiten</button>
          <button v-if="writing.held.value.status === 'held'"
            type="button" class="button" style="min-height:48px" @click="writing.release">Bearbeitung beenden</button>
          <p v-if="writing.loadFailed.value" role="alert">Der Text konnte nicht geladen werden.</p>
          <button v-if="writing.loadFailed.value && !writing.document.value" type="button" class="button"
            style="min-height:48px" @click="writing.load()">Erneut laden</button>
        </div>
        <RichTextEditor v-if="writing?.document.value" ref="richEditor"
          :client="writing.client" :document="writing.document.value" :can-edit="writing.writable.value"
          :save-document="writing.save" :reload-document="() => writing.load(true)"
          :add-graphic="writing.addGraphic" :graphics-path="writing.graphicsPath" />
        <p v-else-if="!writing && !state.session.phrases.length && !state.session.partial"
          role="note" class="hint" style="margin: 0; padding: 22px 28px">{{ TEXT.empty }}</p>
      </div>
    </div>
    <div class="editor-status"><span>{{ TEXT.phrases }}: {{ state.session.phrases.length }}</span><span class="editor-status-right"><span>{{ state.announcement }}</span></span></div>
  </section>
</section>`,
};

/** Registered view. Re-keying the body on dictationId disposes the previous dictation's state. */
export const LiveView = {
    name: 'ClinicLiveView',
    components: { LiveBody },
    props: BODY_PROPS,
    methods: { canLeave() { return this.$refs.body?.canLeave() !== false; } },
    template: `<LiveBody ref="body" :key="dictationId" :client="client" :dictation-id="dictationId" :current-user="currentUser"
      :vue="vue" :can-write="canWrite" :on-ready="onReady" />`,
};

export function registerLiveView(app) {
    app.component('clinic-live-view', LiveView);
    return app;
}
