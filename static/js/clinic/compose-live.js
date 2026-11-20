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
 *   stop   parity command stop_live_session {session_id, generation, last_sequence};
 *   phrases and reconnect  handled by the live-session controller.
 *
 * Phases: idle, starting, live, paused, reconnecting, stopping, stopped, stale, error.
 * The only live region announces the phase, never a word, a partial or a phrase.
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
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { createLiveSessionController, liveActions } from './live-session-ui.js';

export const COMPOSE_LIVE_VERSION = 1;

export const PHASES = Object.freeze([
    'idle', 'starting', 'live', 'paused', 'reconnecting', 'stopping', 'stopped', 'stale', 'error',
]);

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
    empty: 'Noch kein Text. Mit «Starten» beginnen.',
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
            if (res.outcome === 'ok') setPhase('paused');
            else failed(res, 'live');
            return state();
        },

        async continueRecording() {
            if (phase !== 'paused') return state();
            const id = session.getState().sessionId;
            const res = await client.request(LIVE + '/sessions/' + encodeURIComponent(id) + '/continue', {
                method: 'POST', idempotencyKey: newKey(), body: {},
            });
            if (disposed || res.outcome === 'cancelled') return state();
            if (res.outcome === 'ok') setPhase('live');
            else failed(res, 'paused');
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
            if (session.getState().hasUnsaved) {
                setPhase(lost ? 'reconnecting' : back, NOTES.stopBlocked);
                return state();
            }
            const s = session.getState();
            setPhase('stopping');
            const res = await client.command({
                command: 'stop_live_session', request_id: newKey(), expected_revision: 0,
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
            if (phase === 'live') await saveUnsaved();
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
    vue: { type: Object, default: null },
    canWrite: { type: Function, default: null },
    onReady: { type: Function, default: null },
};

/** Inner body: one instance per dictation. */
const LiveBody = {
    name: 'ClinicLiveBody',
    props: BODY_PROPS,
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
        return { c, state, FOCUS, box, TEXT };
    },
    template: `
<section class="clinic-live-view" data-testid="clinic-live-view" aria-labelledby="clv-title" :data-phase="state.phase">
  <header class="flex flex-wrap items-center gap-2">
    <h1 id="clv-title" class="text-lg font-semibold">{{ TEXT.title }}</h1>
    <span class="text-sm" style="color: var(--text-secondary)">{{ TEXT.dictation }} {{ state.dictationId }}</span>
  </header>
  <p role="status" aria-live="polite" data-testid="live-announcement">{{ state.announcement }}</p>
  <p v-if="state.note" role="note" data-testid="live-note">{{ state.note }}</p>
  <div class="flex flex-wrap items-center gap-2 mt-2" role="group" :aria-label="TEXT.controls" data-testid="live-controls">
    <button v-for="a in state.actions" :key="a.id" type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem"
            :style="FOCUS" :disabled="a.disabled" :data-action="a.id" @click="c[a.id]()">{{ a.label }}</button>
  </div>
  <p v-if="state.session.hasUnsaved" role="note" class="mt-2 p-2 rounded" :style="box('warning')">{{ TEXT.unsaved }}</p>
  <ol class="mt-3" :aria-label="TEXT.phrases" aria-live="off" data-testid="live-phrases">
    <li v-for="p in state.session.phrases" :key="p.generation + '-' + p.sequence" :data-state="p.state"
        :style="{ background: p.view.surface.background, color: p.view.surface.color, border: '1px solid ' + p.view.surface.border }">
      <span>{{ p.text }}</span>
      <small>{{ p.view.position }}: <strong>{{ p.view.label }}</strong></small>
    </li>
    <li v-if="state.session.partial" data-state="partial"><em>{{ state.session.partial }}</em> <small>{{ TEXT.partial }}</small></li>
  </ol>
  <p v-if="!state.session.phrases.length && !state.session.partial" role="note">{{ TEXT.empty }}</p>
</section>`,
};

/** Registered view. Re-keying the body on dictationId disposes the previous dictation's state. */
export const LiveView = {
    name: 'ClinicLiveView',
    components: { LiveBody },
    props: BODY_PROPS,
    template: `<LiveBody :key="dictationId" :client="client" :dictation-id="dictationId" :vue="vue" :can-write="canWrite" :on-ready="onReady" />`,
};

export function registerLiveView(app) {
    app.component('clinic-live-view', LiveView);
    return app;
}
