/**
 * Live transcription display with durable phrase acknowledgements.
 *
 * A phrase is `partial` (still changing), `final_unsaved` (final text, no
 * server receipt yet), `server_saved` (the server receipt names this exact
 * session, generation and sequence) or `stale` (it can no longer become
 * durable). Only a matching receipt makes a phrase durable. Text and audio
 * stay in memory. Appending needs the live holder: when the caller's write
 * check fails or the server answers with a conflict or policy denial, the
 * phrase stays unsaved and is never shown as saved. Reconnecting resumes from
 * the durable checkpoint and adopts the server's stored phrases.
 */

import { surfaceFor } from './tokens.js';

export const PHRASE_STATES = Object.freeze(['partial', 'final_unsaved', 'server_saved', 'stale']);

const LIVE = '/api/clinic/v1/live';

const VIEW = Object.freeze({
    partial: { label: 'Vorläufig', surface: 'processing' },
    final_unsaved: { label: 'Nicht gespeichert', surface: 'warning' },
    server_saved: { label: 'Gespeichert', surface: 'success' },
    stale: { label: 'Veraltet', surface: 'error' },
});

const MESSAGES = Object.freeze({
    idle: 'Keine Live-Sitzung.',
    live: 'Live-Transkription läuft.',
    paused: 'Live-Diktat pausiert.',
    holderLost: 'Sie sind nicht mehr der aktive Sprecher. Neue Sätze werden nicht gespeichert.',
    retry: 'Verbindung unterbrochen. Speichern wird wiederholt.',
    mismatch: 'Die Bestätigung passt nicht zu diesem Satz.',
    resumed: 'Wiederaufnahme ab dem gespeicherten Stand.',
    denied: 'Keine Berechtigung für diese Live-Sitzung.',
});

function defaultKey() {
    return globalThis.crypto.randomUUID();
}

/** Display fields for one phrase. Meaning never depends on colour alone. */
export function phraseView(phrase) {
    const v = VIEW[phrase.state];
    return {
        label: v.label,
        surface: surfaceFor(v.surface),
        durable: phrase.state === 'server_saved',
        position: 'Satz ' + phrase.sequence + ', Generation ' + phrase.generation,
    };
}

/**
 * @param {object} options
 * @param {{request: Function}} options.client clinic api client
 * @param {string} options.dictationId
 * @param {() => boolean} [options.canWrite] live-holder / lease check; a false answer blocks appends
 * @param {() => string} [options.newKey]
 */
export function createLiveSessionController(options) {
    const { client, dictationId } = options;
    const canWrite = options.canWrite || (() => true);
    const newKey = options.newKey || defaultKey;
    const listeners = new Set();

    let sessionId = null;
    let generation = 1;
    let durableSequence = 0; // durable checkpoint: highest server_saved sequence
    let status = 'idle';
    let note = '';
    let partial = null; // { text } - never durable
    let phrases = [];

    function snapshot() {
        return {
            status,
            message: note || MESSAGES[status] || '',
            sessionId,
            generation,
            checkpoint: durableSequence,
            // stop_live_session compares expected_revision with ack_seq + 1.
            revision: sessionId === null ? 0 : durableSequence + 1,
            partial: partial ? partial.text : null,
            phrases: phrases.map((p) => ({ ...p, view: phraseView(p) })),
            writable: status === 'live' && canWrite(),
            hasUnsaved: phrases.some((p) => p.state === 'final_unsaved'),
        };
    }

    function emit() {
        const snap = snapshot();
        for (const fn of listeners) fn(snap);
    }

    function setStatus(next, text) {
        status = next;
        note = text || '';
    }

    function markStale(text) {
        for (const p of phrases) if (p.state === 'final_unsaved') p.state = 'stale';
        partial = null;
        setStatus('stale', text || MESSAGES.holderLost);
    }

    function find(sequence, gen) {
        return phrases.find((p) => p.sequence === sequence && p.generation === gen);
    }

    function adopt(data) {
        sessionId = data.session_id;
        generation = data.generation;
        durableSequence = Math.max(durableSequence, data.sequence);
        for (const ev of data.events || []) {
            let p = find(ev.sequence, ev.generation) || phrases.find((x) => x.eventId === ev.event_id);
            if (!p) {
                p = { sequence: ev.sequence, generation: ev.generation, eventId: ev.event_id, text: null };
                phrases.push(p);
            }
            p.sessionId = data.session_id;
            if (typeof ev.text === 'string') p.text = ev.text;
            p.phraseId = ev.phrase_id;
            p.state = 'server_saved';
            p.wire = null;
        }
        // Local phrases the server does not hold at the checkpoint can no longer become durable.
        for (const p of phrases) {
            if (p.state === 'final_unsaved' && (p.generation !== generation || p.sequence <= durableSequence)) p.state = 'stale';
        }
        phrases.sort((a, b) => a.generation - b.generation || a.sequence - b.sequence);
    }

    function receiptMatches(p, data) {
        return !!data && data.status === 'server_saved' && data.session_id === p.sessionId
            && data.generation === p.generation && data.sequence === p.sequence
            && data.event_id === p.eventId;
    }

    function reset() {
        phrases = [];
        partial = null;
        sessionId = null;
        generation = 1;
        durableSequence = 0;
        setStatus('idle');
    }

    return {
        getState: snapshot,
        reset,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },

        /** Attach to a started session (its id and position come from the start command / resume). */
        attach(position) {
            sessionId = position.session_id;
            generation = position.generation || 1;
            durableSequence = position.sequence || 0;
            setStatus(position.state === 'PAUSED' ? 'paused' : 'live');
            emit();
            return snapshot();
        },

        /**
         * Apply a pause, continue or resume position.
         * RECORDING is the only state that may accept new phrases.
         */
        applyServerState(data) {
            if (data && typeof data === 'object') {
                if (typeof data.session_id === 'string') sessionId = data.session_id;
                if (Number.isInteger(data.generation)) generation = data.generation;
                if (Number.isInteger(data.sequence)) durableSequence = Math.max(durableSequence, data.sequence);
                if (data.state === 'PAUSED') setStatus('paused', MESSAGES.paused);
                else if (data.state === 'RECORDING') setStatus('live');
            }
            emit();
            return snapshot();
        },

        acceptSaved(event) {
            if (status !== 'live' || event.session_id !== sessionId || event.generation !== generation
                    || event.sequence !== durableSequence + 1 || !event.event_id || !event.phrase_id
                    || event.status !== 'server_saved' || typeof event.text !== 'string' || !event.text.trim()) return false;
            adopt({ ...event, events: [event] });
            partial = null;
            emit();
            return true;
        },

        /** Partial text replaces the previous partial. It is ignored without write permission. */
        setPartial(text) {
            if (status !== 'live' || !canWrite()) return false;
            partial = text ? { text } : null;
            emit();
            return true;
        },

        /**
         * Finalize the current phrase with the next exact sequence.
         * `wire` carries audio_start_ms, audio_end_ms, audio_b64, audio_revision_id,
         * transcript_revision_id and text_hash.
         */
        finalize(text, wire) {
            if (status !== 'live' || !canWrite() || sessionId === null) return null;
            const last = phrases.reduce((m, p) => (p.generation === generation ? Math.max(m, p.sequence) : m), durableSequence);
            const phrase = {
                sessionId, generation, sequence: last + 1, eventId: newKey(), text,
                state: 'final_unsaved', wire: wire || {}, phraseId: null,
            };
            phrases.push(phrase);
            partial = null;
            emit();
            return { ...phrase };
        },

        /** Append one final phrase. Same event id on retry, so a replay never duplicates. */
        async save(sequence) {
            const p = find(sequence, generation);
            if (!p || p.state !== 'final_unsaved') return snapshot();
            if (!canWrite()) {
                setStatus('stale', MESSAGES.holderLost);
                emit();
                return snapshot();
            }
            const res = await client.request(LIVE + '/sessions/' + encodeURIComponent(p.sessionId) + '/phrases', {
                method: 'POST',
                idempotencyKey: p.eventId,
                body: { ...p.wire, generation: p.generation, sequence: p.sequence, event_id: p.eventId },
            });
            if (res.outcome === 'cancelled' || p.state !== 'final_unsaved') return snapshot();
            if (res.outcome === 'ok') {
                if (receiptMatches(p, res.data)) {
                    p.state = 'server_saved';
                    p.phraseId = res.data.phrase_id;
                    p.wire = null;
                    durableSequence = Math.max(durableSequence, p.sequence);
                    if (status === 'live') note = '';
                } else {
                    setStatus(status, MESSAGES.mismatch);
                }
            } else if (res.outcome === 'conflict' || res.outcome === 'permission') {
                markStale(res.outcome === 'permission' ? MESSAGES.denied : MESSAGES.holderLost);
            } else {
                setStatus(status, MESSAGES.retry); // network / unknown: stays final_unsaved
            }
            emit();
            return snapshot();
        },

        /** Reconnect from the durable checkpoint. */
        async resume(clientId, holderGeneration) {
            const body = { after_sequence: durableSequence };
            if (clientId) body.client_id = clientId;
            if (holderGeneration !== undefined) body.generation = holderGeneration;
            const res = await client.request(LIVE + '/dictations/' + encodeURIComponent(dictationId) + '/resume', {
                method: 'POST',
                idempotencyKey: newKey(),
                body,
            });
            if (res.outcome === 'cancelled') return snapshot();
            if (res.outcome === 'ok' && res.data && typeof res.data.session_id === 'string'
                    && Number.isInteger(res.data.generation) && Number.isInteger(res.data.sequence)) {
                adopt(res.data);
                // recover() always leaves the session PAUSED. Recording waits for continue.
                if (res.data.state === 'PAUSED') setStatus('paused', MESSAGES.paused);
                else setStatus('live', MESSAGES.resumed);
            } else if (res.outcome === 'conflict' || res.outcome === 'permission') {
                markStale(res.outcome === 'permission' ? MESSAGES.denied : MESSAGES.holderLost);
            } else {
                setStatus(status, MESSAGES.retry);
            }
            emit();
            return snapshot();
        },

        /** The holder or lease was lost elsewhere: unsaved text stays visible but never durable. */
        holderLost() {
            markStale();
            emit();
        },

        dispose() { listeners.clear(); reset(); },
    };
}

/** Buttons the panel offers. */
export function liveActions(state) {
    const actions = [];
    if (state.hasUnsaved) actions.push({ id: 'save', label: 'Erneut speichern', disabled: !state.writable });
    if (state.status === 'live' || state.status === 'stale') actions.push({ id: 'resume', label: 'Ab Stand fortsetzen', disabled: false });
    return actions;
}

/** Vue composable. Pass the Vue namespace (window.Vue in the app). */
export function useLiveSession(options, vue) {
    const V = vue || globalThis.Vue;
    const controller = createLiveSessionController(options);
    const state = V.shallowRef(controller.getState());
    const unsubscribe = controller.subscribe((snap) => { state.value = snap; });
    const run = {
        save: async () => {
            for (const p of controller.getState().phrases) {
                if (p.state === 'final_unsaved') await controller.save(p.sequence);
            }
        },
        resume: () => controller.resume(),
    };
    function dispose() { unsubscribe(); controller.dispose(); }
    if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(dispose);
    return { state, controller, run, dispose };
}

/** Component: transcript list in a polite live region plus native buttons (Tab, Enter, Space). */
export function createLiveSessionComponent(vue) {
    const V = vue || globalThis.Vue;
    return {
        name: 'ClinicLiveSession',
        props: { state: { type: Object, required: true }, run: { type: Object, required: true } },
        setup(props) {
            const actions = V.computed(() => liveActions(props.state));
            return { actions };
        },
        template: `
            <section class="clinic-live v8-page state-box" aria-label="Live-Transkription" :data-status="state.status">
                <p role="status" aria-live="polite" class="text-status" style="margin: 0 0 10px">{{ state.message }}</p>
                <ol class="clinic-live-phrases" aria-label="Sätze" style="list-style: none; margin: 0; padding: 0; display: grid; gap: 4px">
                    <li v-for="p in state.phrases" :key="p.generation + '-' + p.sequence"
                        :data-state="p.state"
                        style="display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 12px; padding: 8px 0; border-bottom: 1px solid var(--v8-line-2)">
                        <span style="flex: 1 1 240px; min-width: 0; overflow-wrap: anywhere">{{ p.text }}</span>
                        <small class="tag" :class="p.state === 'final_unsaved' ? 'wait' : (p.state === 'stale' ? 'error' : 'neutral')">{{ p.view.position }}: <strong style="display: inline">{{ p.view.label }}</strong></small>
                    </li>
                    <li v-if="state.partial" data-state="partial" aria-label="Vorläufig" style="padding: 8px 0; color: var(--v8-muted)"><em>{{ state.partial }}</em></li>
                </ol>
                <p v-if="state.hasUnsaved" role="alert" class="note warning">Nicht gespeicherte Sätze sind noch nicht dauerhaft.</p>
                <div class="actions" style="margin-top: 12px">
                    <button v-for="a in actions" :key="a.id" type="button" class="button small" :disabled="a.disabled"
                        @click="run[a.id]()">{{ a.label }}</button>
                </div>
            </section>`,
    };
}
