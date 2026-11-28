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
import { LiveHandoff } from './live-handoff-ui.js';
import { RichTextEditor } from './richtext-editor-ui.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { createLiveSessionController, liveActions } from './live-session-ui.js';

export const COMPOSE_LIVE_VERSION = 1;

export const PHASES = Object.freeze([
    'idle', 'recovering', 'starting', 'live', 'paused', 'reconnecting', 'stopping', 'stopped', 'read_only', 'stale', 'error',
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
    unsaved: 'Nicht auf dem Server gespeichert',
    saved: 'Gespeichert',
    empty: 'Noch kein Text. Mit der Aufnahmetaste beginnen.',
    ready: 'Bereit zum Diktieren',
    eyebrow: 'Aufnahmen',
    subline: 'Nach jedem Satz kurz pausieren. Gespeicherte Sätze sind markiert.',
    transcript: 'Live-Transkription',
    count: (n) => (n === 1 ? '1 Satz' : n + ' Sätze'),
});

/** Announced when the phase changes. Never used for words, partials or phrases. */
export const ANNOUNCEMENTS = Object.freeze({
    idle: 'Keine Live-Sitzung.',
    recovering: 'Gespeicherte Sätze werden geladen.',
    starting: 'Live-Sitzung wird gestartet.',
    live: 'Live-Diktat läuft.',
    paused: 'Live-Diktat pausiert.',
    reconnecting: 'Verbindung unterbrochen. Wiederaufnahme ab dem gespeicherten Stand.',
    stopping: 'Live-Sitzung wird beendet.',
    stopped: 'Live-Sitzung beendet.',
    read_only: 'Sie können die gespeicherten Sätze ansehen, aber hier nicht aufnehmen.',
    stale: 'Sie sind nicht mehr der aktive Sprecher. Neue Sätze werden nicht gespeichert.',
    error: 'Die Live-Sitzung konnte nicht gestartet werden.',
});

const NOTES = Object.freeze({
    stopBlocked: 'Beenden nicht möglich: Es gibt nicht gespeicherte Sätze.',
    stopFailed: 'Beenden fehlgeschlagen. Bitte erneut versuchen.',
    denied: 'Keine Berechtigung für diese Live-Sitzung.',
    retry: 'Verbindung unterbrochen. Speichern wird wiederholt.',
    checkSaved: 'Bitte verbinden Sie sich erneut, um den Speicherstand zu prüfen.',
    recoveryFailed: 'Die gespeicherten Sätze konnten nicht vollständig geladen werden. Bitte versuchen Sie es erneut.',
    restored: 'Die gespeicherten Sätze wurden geladen.',
});

/** Inline SVG paths (24x24) of the live actions shown as icon buttons. The other actions stay text buttons. */
const ICON_PATHS = Object.freeze({
    start: 'M12 5a7 7 0 1 0 0 14a7 7 0 0 0 0-14z',
    pause: 'M7 5h3v14H7z M14 5h3v14h-3z',
    continueRecording: 'M8 5l11 7-11 7z',
    stop: 'M6 6h12v12H6z',
});

const LIVE = '/api/clinic/v1/live';
const DEFAULT_PROFILE_REVISION = '00000000-0000-0000-0000-000000000000';
const loadCapture = () => import('./live-capture-ui.js');
const microphoneNote = (error) => ({
    NotAllowedError: 'Bitte erlauben Sie den Mikrofonzugriff im Browser.',
    NotFoundError: 'Kein Mikrofon gefunden. Bitte schliessen Sie ein Mikrofon an.',
})[error?.name] || 'Das Mikrofon ist nicht verfügbar. Bitte prüfen Sie die Verbindung.';
const defaultKey = () => globalThis.crypto.randomUUID();

const FOCUS = { '--focus-ring': FOCUS_INDICATOR.outlineColor, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
const DISABLED = { background: 'var(--v8-s2)', color: 'var(--v8-muted)', cursor: 'not-allowed', opacity: 1 };
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
    const captureModule = opts.captureModule;
    const clientId = captureModule ? newKey() : null;
    const fence = () => clientId ? { client_id: clientId, generation: session.getState().generation } : {};
    const commandOptions = () => ({ idempotencyKey: newKey(), liveClientId: clientId });
    let capture;
    let busy = false;
    let pendingNote = '';
    let holder = null;

    let phase = 'idle';
    let announcement = '';
    let note = '';
    let lost = false; // the last request ended without an answer
    let disposed = false;
    let recovering = false;

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
        client, dictationId, newKey, fullRecovery: true,
        canWrite: () => phase === 'live' && canWriteOuter(),
    });

    function snapshot() {
        const s = session.getState();
        return { phase, announcement, note: pendingNote || note, dictationId, session: s, actions: [] };
    }

    function state() {
        const snap = snapshot();
        snap.actions = liveViewActions(snap).map((action) => busy ? { ...action, disabled: true } : action);
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

    function releaseHolder(position) {
        captureModule.releaseLiveHolder(position, { client_id: clientId, generation: position.generation });
    }

    async function adoptSession(autoContinue = false) {
        recovering = true;
        const onHolder = captureModule ? (position) => {
            if (disposed) releaseHolder(position);
            else holder = position;
        } : undefined;
        const snap = await session.resume(clientId, clientId ? (holder?.generation ?? session.getState().generation) : undefined, onHolder);
        recovering = false;
        if (disposed) return state();
        if (snap.recovery === 'failed') setPhase('reconnecting', NOTES.recoveryFailed);
        else if (snap.status === 'stale') setPhase('stale', snap.message);
        else if (!(autoContinue && snap.status === 'paused')) {
            const next = captureModule && snap.status === 'live' ? 'error' : snap.status;
            setPhase(next, snap.phrases.some((p) => p.state === 'server_saved') ? NOTES.restored : '');
        }
        return state();
    }

    function closeCapture() {
        session.setPartial(null);
        capture?.close();
        capture = null;
    }

    async function transition(name) {
        const { sessionId } = session.getState();
        return client.request(`${LIVE}/sessions/${encodeURIComponent(sessionId)}/${name}`, {
            method: 'POST', idempotencyKey: newKey(), body: fence(),
        });
    }

    function captureFailed(code) {
        closeCapture();
        if (disposed) return;
        if (code === 'POLICY_DENIED' || code === 'FORBIDDEN' || code === 'UNAUTHORIZED') {
            holder = null;
            session.holderLost();
            setPhase('stale', NOTES.denied);
        } else {
            setPhase('reconnecting', code === 'microphone' ? microphoneNote() : NOTES.checkSaved);
        }
    }

    async function prepareCapture() {
        if (!captureModule) return true;
        try {
            capture = captureModule.createLiveCapture({
                position: session.getState, clientId,
                onPartial: session.setPartial, onSaved: session.acceptSaved, onFailure: captureFailed,
                canWrite: canWriteOuter,
                renew: async () => (await transition('continue')).data?.state === 'RECORDING',
            });
            return await capture.prepare();
        } catch (error) {
            closeCapture();
            if (!disposed) setPhase(phase === 'starting' ? 'error' : 'paused', microphoneNote(error));
            return false;
        }
    }

    async function continueWithCapture() {
        const prepared = capture;
        const res = await transition('continue');
        if (disposed || capture !== prepared) return;
        if (res.outcome !== 'ok' || res.data?.state !== 'RECORDING') {
            closeCapture();
            failed(res, 'paused');
            return;
        }
        session.applyServerState(res.data);
        if (await prepared.start() && !disposed && capture === prepared) setPhase('live');
    }

    async function drainCapture() {
        if (!capture) return true;
        session.setPartial(null);
        const ok = await capture.drain();
        capture = null;
        return ok && !disposed;
    }

    async function exclusive(action, message = '') {
        if (disposed || busy) return state();
        busy = true;
        pendingNote = message;
        emit();
        try { await action(); }
        finally { busy = false; pendingNote = ''; if (!disposed) emit(); }
        return state();
    }

    return {
        getState: state,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        session,

        async restore() {
            if (disposed || recovering || phase !== 'idle') return state();
            setPhase('recovering');
            return adoptSession();
        },

        async start() {
            if (phase !== 'idle' && phase !== 'stopped' && phase !== 'error') return state();
            if (captureModule) return exclusive(async () => {
                if (!canWriteOuter()) return setPhase('error', NOTES.denied);
                session.reset();
                holder = null;
                setPhase('starting');
                if (!await prepareCapture() || disposed || !capture) return;
                const res = await client.command({
                    command: 'start_live_session', request_id: newKey(), expected_revision: 0,
                    payload: { dictation_id: dictationId, profile_revision_id: DEFAULT_PROFILE_REVISION, capture_mime: 'audio/pcm' },
                }, commandOptions());
                if (disposed && res.outcome !== 'ok') return;
                if (res.outcome !== 'ok' && res.outcome !== 'conflict') {
                    closeCapture();
                    failed(res, 'error');
                    return;
                }
                const prepared = capture;
                const fresh = res.outcome === 'ok';
                if (!fresh) closeCapture();
                await adoptSession(fresh);
                if (!disposed && phase === 'starting' && fresh && capture && capture === prepared) await continueWithCapture();
                else closeCapture();
            });
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
            if (captureModule) return exclusive(async () => {
                if (!await drainCapture()) return;
                const res = await transition('pause');
                if (disposed) return;
                if (res.outcome === 'ok' && res.data?.state === 'PAUSED') {
                    session.applyServerState(res.data);
                    setPhase('paused');
                } else failed(res, 'reconnecting');
            }, 'Aufnahme angehalten. Der letzte Satz wird gespeichert.');
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
            if (captureModule) return exclusive(async () => {
                if (!canWriteOuter()) return captureFailed('POLICY_DENIED');
                if (await prepareCapture() && !disposed && capture) await continueWithCapture();
            }, 'Mikrofon und Verbindung werden vorbereitet.');
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
                } else if (data.state === 'RECORDING') {
                    session.applyServerState(data);
                    setPhase('live');
                }
            } else failed(res, 'paused');
            return state();
        },

        /** Stop needs every final phrase saved; otherwise the stop would drop text. */
        async stop() {
            if (phase !== 'live' && phase !== 'paused') return state();
            if (captureModule) return exclusive(async () => {
                if (!await drainCapture()) return;
                const s = session.getState();
                setPhase('stopping');
                const res = await client.command({
                    command: 'stop_live_session', request_id: newKey(), expected_revision: s.revision,
                    payload: { session_id: s.sessionId, generation: s.generation, last_sequence: s.checkpoint },
                }, commandOptions());
                if (disposed) return;
                if (res.outcome === 'ok') {
                    holder = null;
                    setPhase('stopped');
                } else if (res.outcome === 'conflict') setPhase('reconnecting', NOTES.stopFailed);
                else failed(res, 'reconnecting');
            }, 'Aufnahme angehalten. Der letzte Satz wird gespeichert.');
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
            if (disposed || recovering || !['reconnecting', 'stale', 'live', 'paused'].includes(phase)) return state();
            if (captureModule) return exclusive(async () => {
                closeCapture();
                setPhase('reconnecting');
                await adoptSession();
            }, 'Der gespeicherte Stand wird geprüft.');
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
            if (captureModule) { if (capture) captureFailed('network'); return; }
            if (phase === 'live' || phase === 'paused') setPhase('reconnecting', NOTES.retry);
        },

        /** Interim recognition text. It replaces the previous partial and is never announced. */
        partial(text) { return !captureModule && session.setPartial(text); },

        /** A final phrase from the engine: take the next sequence and save it. */
        async finalizePhrase(text, wire) {
            if (captureModule) return null;
            const p = session.finalize(text, wire);
            if (!p) return null;
            await session.save(p.sequence);
            if (lost && phase === 'live') setPhase('reconnecting', NOTES.retry);
            return p;
        },

        saveUnsaved,

        dispose() {
            if (disposed) return;
            disposed = true;
            closeCapture();
            if (holder) releaseHolder(holder);
            holder = null;
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
    captureModule: { type: Object, default: null },
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
        base = { ...data, audio_revision_id: base.audio_revision_id,
            state: { version: data.state_version, audio_position_ms: data.audio_position_ms } };
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
    V.watch(() => state.value.phase, (phase) => { if (phase === 'stopped') void load(); });
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
        confirmedCorrection: () => base && (held.value.status === 'idle' || lease.canWrite())
            ? { ...base, lease_generation: held.value.generation } : null,
        refreshAfterHandoffConflict: (code) => {
            if (code === 'LEASE_CONFLICT') lease.notifyGeneration(-1);
            void load();
        },
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
    components: { LiveHandoff, RichTextEditor },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the live view');
        const captureEnabled = !props.client || props.client.supportsLiveCapture === true;
        let loaded = props.captureModule;
        const pending = V.shallowRef(captureEnabled && !loaded);
        const captureModule = loaded || (captureEnabled ? {
            createLiveCapture: (options) => loaded.createLiveCapture(options),
            releaseLiveHolder: (...args) => loaded.releaseLiveHolder(...args),
        } : null);
        const loading = pending.value ? loadCapture().then((module) => {
            loaded = module;
            pending.value = false;
        }, () => {
            loaded = {
                createLiveCapture: () => ({ prepare() { throw new Error('capture_unavailable'); }, close() {} }),
                releaseLiveHolder() {},
            };
            pending.value = false;
        }) : null;
        const c = createLiveViewController({
            client: props.client || undefined,
            dictationId: props.dictationId,
            canWrite: props.canWrite || undefined,
            captureModule,
        });
        const state = V.shallowRef(c.getState());
        const stop = c.subscribe((snap) => { state.value = snap; });
        const leave = () => { stop(); c.dispose(); };
        if (typeof V.onMounted === 'function') V.onMounted(() => {
            globalThis.addEventListener('pagehide', leave);
            globalThis.addEventListener('offline', c.markDisconnected);
            if (loading) void loading.then(() => c.restore());
            else void c.restore();
            if (props.onReady) props.onReady(c);
        });
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => {
            globalThis.removeEventListener('pagehide', leave);
            globalThis.removeEventListener('offline', c.markDisconnected);
            leave();
        });
        const confirmed = V.computed(() => state.value.session.phrases.filter((p) => p.state === 'server_saved'));
        const local = V.computed(() => state.value.session.phrases.filter((p) => p.state !== 'server_saved'));
        const writing = props.currentUser?.id != null ? useLiveDocument(V, props, state) : null;
        const richEditor = writing?.editor;
        const handoffLocked = V.ref(false);
        const submitted = V.ref(false);
        const mayEdit = V.computed(() => !submitted.value && state.value.phase !== 'read_only');
        const showHandoff = V.computed(() => writing && state.value.phase === 'stopped' && confirmed.value.length > 0
            && (!props.canWrite || props.canWrite()));
        const handoffReady = V.computed(() => showHandoff.value && !state.value.session.hasUnsaved
            && state.value.actions.every((action) => !action.disabled) && writing.document.value
            && !writing.loading.value && !writing.loadFailed.value && ['idle', 'held'].includes(writing.held.value.status)
            && !richEditor.value?.dirty && !richEditor.value?.uploading && richEditor.value?.status !== 'saving');
        const markSubmitted = () => { submitted.value = true; void writing.release(); };
        const canLeave = () => !richEditor?.value?.dirty
            || globalThis.confirm('Ungespeicherte Änderungen gehen verloren. Live-Diktat verlassen?');
        return { c, state, pending, confirmed, local, writing, richEditor, canLeave, handoffLocked, submitted,
            mayEdit, showHandoff, handoffReady, markSubmitted, FOCUS, DISABLED, box, TEXT, ICON_PATHS };
    },
    template: `
<section class="clinic-live-view v8-page" data-testid="clinic-live-view" aria-labelledby="clv-title" :data-phase="state.phase" style="width: 100%; max-width: 1440px; margin-inline: auto">
  <nav class="breadcrumb" aria-label="Pfad"><span>Speakr</span><span aria-hidden="true">›</span><span>{{ TEXT.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ TEXT.title }}</span></nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <h1 id="clv-title" style="${H1}">{{ TEXT.title }}</h1>
      <p class="subline">{{ TEXT.subline }}</p>
    </div>
    <div class="head-actions">
      <LiveHandoff v-if="showHandoff || submitted" :client="writing.client" :dictation-id="state.dictationId"
        :ready="Boolean(handoffReady)" :current-correction="writing.confirmedCorrection"
        @lock="handoffLocked = $event" @refresh="writing.refreshAfterHandoffConflict" @submitted="markSubmitted" />
    </div>
  </div>
  <section class="editor-workspace" :aria-label="TEXT.transcript">
    <div class="editor-heading" style="flex-wrap: wrap; gap: 8px 16px">
      <div class="document-title" style="flex-wrap: wrap; gap: 4px 12px">
        <h2 style="${H2}; margin: 0; white-space: normal">{{ TEXT.transcript }}</h2>
        <span class="document-id" style="white-space: normal; overflow-wrap: anywhere">{{ TEXT.dictation }} {{ state.dictationId }}</span>
      </div>
      <span class="tag neutral dot">{{ TEXT.count(confirmed.length) }} · {{ TEXT.saved }}</span>
    </div>
    <div class="recorder compact-recorder" :class="{ recording: state.phase === 'live' && !state.actions[0]?.disabled }">
      <div class="recorder-controls" style="flex-wrap: wrap; gap: 8px" role="group" :aria-label="TEXT.controls" data-testid="live-controls">
        <template v-for="a in submitted ? [] : state.actions" :key="a.id">
          <button type="button" class="button" style="min-width: 48px; min-height: 48px"
                  :class="{ primary: (a.id === 'start' && !showHandoff) || a.id === 'continueRecording' || a.id === 'reconnect' }"
                  :style="[FOCUS, (a.disabled || pending || handoffLocked) ? DISABLED : null]"
                  :disabled="a.disabled || pending || handoffLocked" :data-action="a.id" @click="c[a.id]()">
            <svg v-if="ICON_PATHS[a.id]" viewBox="0 0 24 24" width="19" height="19" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="ICON_PATHS[a.id]"></path></svg>
            {{ a.label }}
          </button>
        </template>
      </div>
      <div class="recorder-caption">
        <strong v-if="!state.announcement" data-testid="live-caption">{{ TEXT.ready }}</strong>
        <span role="status" aria-live="polite" data-testid="live-announcement">{{ state.announcement }}</span>
        <small v-if="state.note" role="note" data-testid="live-note" style="font-size: 13px">{{ state.note }}</small>
      </div>
    </div>
    <div class="writing-surface" style="min-height: clamp(360px, calc(100dvh - 370px), 760px)"
      :style="writing ? { minHeight: 'clamp(360px, calc(100dvh - 430px), 760px)' } : null">
      <div style="width: 100%; min-width: 0">
        <ol class="live-phrases" :aria-label="TEXT.phrases" aria-live="off" data-testid="live-phrases"
            style="list-style: none; margin: 0; display: grid; gap: 4px; font-size: 16px; line-height: 1.75; align-content: start"
            :style="confirmed.length ? { padding: '22px 28px', minHeight: '240px' } : null">
          <li v-for="(p, index) in confirmed" :key="JSON.stringify([p.sessionId, p.eventId])" :data-state="p.state"
              style="display: grid; gap: 4px; padding: 8px 0; min-width: 0; border-bottom: 1px solid var(--v8-line-2)">
            <small class="tag neutral" style="justify-self: start; font-size: 12px">Satz {{ index + 1 }} · {{ p.view.label }}</small>
            <span style="white-space: pre-wrap; overflow-wrap: anywhere">{{ p.text }}</span>
            <audio v-if="p.audioUrl" controls preload="none" :src="p.audioUrl" :aria-label="'Originalaufnahme zu Satz ' + (index + 1)"
                   style="width: 100%; max-width: 300px; height: 54px; color-scheme: light dark"></audio>
          </li>
        </ol>
        <p v-if="!writing && !confirmed.length && !local.length && !state.session.partial && (state.phase === 'idle' || state.phase === 'read_only' || state.phase === 'stopped')"
           role="note" class="hint" style="margin: 0; padding: 22px 28px">{{ state.phase === 'idle' ? TEXT.empty : 'Keine gespeicherten Sätze.' }}</p>
        <section v-if="local.length || state.session.partial" aria-labelledby="clv-local" style="padding: 12px 28px">
          <h3 id="clv-local" style="${H2}">{{ TEXT.unsaved }}</h3>
          <ul style="list-style: none; margin: 0; padding: 0; overflow-wrap: anywhere">
            <li v-for="p in local" :key="JSON.stringify([p.sessionId, p.eventId])" :data-state="p.state">
              {{ p.text }} <small class="tag wait">{{ p.state === 'stale' ? 'Nicht mehr speicherbar' : p.view.label }}</small>
            </li>
            <li v-if="state.session.partial" data-state="partial"><em>{{ state.session.partial }}</em> <small class="tag neutral">{{ TEXT.partial }}</small></li>
          </ul>
        </section>
        <div v-if="writing && mayEdit" style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:8px 16px;border-bottom:1px solid var(--v8-line-2)">
          <p role="status" style="margin:0">{{ writing.held.value.message }}</p>
          <p v-if="writing.loading.value && !writing.document.value" role="status">Text wird geladen …</p>
          <button v-if="writing.document.value && ['idle', 'lost', 'error'].includes(writing.held.value.status) && writing.held.value.mayAcquire !== false"
            type="button" class="button" style="min-height:48px" :disabled="handoffLocked" @click="writing.acquire">Bearbeiten</button>
          <button v-if="writing.held.value.status === 'held'"
            type="button" class="button" style="min-height:48px" :disabled="handoffLocked" @click="writing.release">Bearbeitung beenden</button>
          <p v-if="writing.loadFailed.value" role="alert">Der Text konnte nicht geladen werden.</p>
          <button v-if="writing.loadFailed.value && !writing.document.value" type="button" class="button"
            style="min-height:48px" @click="writing.load()">Erneut laden</button>
        </div>
        <RichTextEditor v-if="writing?.document.value" ref="richEditor"
          :client="writing.client" :document="writing.document.value" :can-edit="writing.writable.value && mayEdit && !handoffLocked"
          :save-document="writing.save" :reload-document="() => writing.load(true)"
          :add-graphic="writing.addGraphic" :graphics-path="writing.graphicsPath" />
      </div>
    </div>
    <div class="editor-status"><span>{{ TEXT.saved }}: {{ confirmed.length }}</span><span class="editor-status-right"><span>{{ state.announcement }}</span></span></div>
  </section>
</section>`,
};

/** Registered view. Re-keying the body on dictationId disposes the previous dictation's state. */
export const LiveView = {
    name: 'ClinicLiveView',
    components: { LiveBody },
    props: BODY_PROPS,
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const visit = V.ref(0);
        const restore = (event) => { if (event.persisted) visit.value += 1; };
        V.onMounted(() => globalThis.addEventListener('pageshow', restore));
        V.onBeforeUnmount(() => globalThis.removeEventListener('pageshow', restore));
        return { visit };
    },
    methods: { canLeave() { return this.$refs.body?.canLeave() !== false; } },
    template: `<LiveBody ref="body" :key="dictationId + ':' + visit" :client="client" :dictation-id="dictationId" :current-user="currentUser"
      :vue="vue" :can-write="canWrite" :on-ready="onReady" :capture-module="captureModule" />`,
};

export function registerLiveView(app) {
    app.component('clinic-live-view', LiveView);
    return app;
}
