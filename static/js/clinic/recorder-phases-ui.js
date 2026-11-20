/**
 * Recorder phases: title, recording and send, on top of the existing
 * recorder composable (static/js/modules/composables/audio.js).
 *
 * The recorder is injected, so its state and upload stay the single source.
 * A dictation counts as sent only when the upload resolves with a server
 * receipt (an object carrying an id). A rejected, interrupted or receipt-less
 * upload ends in `failed` and can be retried. Title and audio stay in memory;
 * nothing is written to browser storage. Colours come from tokens.js.
 */

import { surfaceFor } from './tokens.js';

export const PHASES = Object.freeze(['title', 'ready', 'recording', 'paused', 'review', 'sending', 'sent', 'failed']);

export const LABELS = Object.freeze({
    title: 'Titel',
    titleHint: 'Ein Titel ist erforderlich.',
    start: 'Aufnahme starten',
    pause: 'Pausieren',
    resume: 'Fortsetzen',
    stop: 'Aufnahme beenden',
    rewind: 'Zurückspulen',
    play: 'Abspielen',
    send: 'Senden',
    retry: 'Erneut senden',
    cancel: 'Abbrechen',
    sent: 'Gesendet',
    failed: 'Senden fehlgeschlagen. Das Diktat wurde nicht gesendet.',
    sending: 'Wird gesendet',
    unauthorized: 'Keine Berechtigung für diese Aktion.',
});

const STATUS = Object.freeze({
    title: 'normal', ready: 'normal', recording: 'processing', paused: 'warning',
    review: 'pending', sending: 'processing', sent: 'success', failed: 'error',
});

function hasReceipt(value) {
    if (!value || typeof value !== 'object') return false;
    const id = value.id ?? value.recording_id ?? value.recordingId;
    return (typeof id === 'string' && id !== '') || (Number.isInteger(id) && id >= 0);
}

/**
 * @param {object} options
 * @param {object} options.recorder  startRecording, pauseRecording, resumeRecording,
 *   stopRecording, discardRecording, uploadRecordedAudio.
 * @param {object} [options.player]  media element with currentTime and play().
 * @param {object} [options.api]     clinic api client; switchContext() is called on reset.
 * @param {() => boolean} [options.authorized]
 */
export function createRecorderPhases(options) {
    const { recorder, player = null, api = null, authorized = () => true } = options;
    const listeners = new Set();
    let generation = 0;
    let state = { phase: 'title', title: '', error: null, receipt: null };

    function set(patch) {
        state = Object.assign({}, state, patch);
        for (const listener of listeners) listener(state);
    }

    function denied(code) {
        return { ok: false, code: code };
    }

    function guard(allowed) {
        if (!authorized()) return denied('unauthorized');
        if (!allowed.includes(state.phase)) return denied('invalid_phase');
        return null;
    }

    function setTitle(value) {
        const title = typeof value === 'string' ? value : '';
        if (state.phase !== 'title' && state.phase !== 'ready') return denied('invalid_phase');
        set({ title: title, phase: title.trim() ? 'ready' : 'title' });
        return { ok: true };
    }

    async function start() {
        const blocked = guard(['ready']);
        if (blocked) return blocked;
        if (!state.title.trim()) return denied('missing_title');
        try {
            await recorder.startRecording();
        } catch (e) {
            set({ error: 'start_failed' });
            return denied('start_failed');
        }
        set({ phase: 'recording', error: null });
        return { ok: true };
    }

    function pause() {
        const blocked = guard(['recording']);
        if (blocked) return blocked;
        recorder.pauseRecording();
        set({ phase: 'paused' });
        return { ok: true };
    }

    function resume() {
        const blocked = guard(['paused']);
        if (blocked) return blocked;
        recorder.resumeRecording();
        set({ phase: 'recording' });
        return { ok: true };
    }

    async function stop() {
        const blocked = guard(['recording', 'paused']);
        if (blocked) return blocked;
        await recorder.stopRecording();
        set({ phase: 'review' });
        return { ok: true };
    }

    function rewind() {
        const blocked = guard(['review']);
        if (blocked) return blocked;
        if (player) player.currentTime = 0;
        return { ok: true };
    }

    function play() {
        const blocked = guard(['review']);
        if (blocked) return blocked;
        if (player && typeof player.play === 'function') player.play();
        return { ok: true };
    }

    async function send() {
        const blocked = guard(['review', 'failed']);
        if (blocked) return blocked;
        const mine = ++generation;
        set({ phase: 'sending', error: null });
        let receipt = null;
        try {
            receipt = await recorder.uploadRecordedAudio({ title: state.title.trim() });
        } catch (e) {
            receipt = null;
        }
        if (mine !== generation) return denied('stale');
        if (!hasReceipt(receipt)) {
            set({ phase: 'failed', error: 'no_receipt' });
            return denied('no_receipt');
        }
        set({ phase: 'sent', receipt: receipt });
        return { ok: true, receipt: receipt };
    }

    async function cancel() {
        if (!authorized()) return denied('unauthorized');
        if (state.phase === 'sending' || state.phase === 'sent') return denied('invalid_phase');
        generation += 1;
        if (state.phase !== 'title' && state.phase !== 'ready') {
            try {
                await recorder.discardRecording();
            } catch (e) {
                set({ error: 'discard_failed' });
                return denied('discard_failed');
            }
        }
        if (api && typeof api.switchContext === 'function') api.switchContext({});
        set({ phase: 'title', title: '', error: null, receipt: null });
        return { ok: true };
    }

    // Reset after `sent` so the next dictation starts clean.
    function reset() {
        if (state.phase !== 'sent') return denied('invalid_phase');
        set({ phase: 'title', title: '', error: null, receipt: null });
        return { ok: true };
    }

    function controls() {
        const p = state.phase;
        const on = authorized();
        const row = (id, label, enabled) => ({ id: id, label: label, disabled: !on || !enabled });
        return [
            row('start', LABELS.start, p === 'ready'),
            row('pause', LABELS.pause, p === 'recording'),
            row('resume', LABELS.resume, p === 'paused'),
            row('stop', LABELS.stop, p === 'recording' || p === 'paused'),
            row('rewind', LABELS.rewind, p === 'review'),
            row('play', LABELS.play, p === 'review'),
            row('send', p === 'failed' ? LABELS.retry : LABELS.send, p === 'review' || p === 'failed'),
            row('cancel', LABELS.cancel, p !== 'sending' && p !== 'sent'),
        ];
    }

    function announcement() {
        const p = state.phase;
        if (!authorized()) return LABELS.unauthorized;
        if (p === 'sent') return LABELS.sent;
        if (p === 'failed') return LABELS.failed;
        if (p === 'sending') return LABELS.sending;
        if (p === 'title') return LABELS.titleHint;
        return '';
    }

    function surface() {
        return surfaceFor(STATUS[state.phase]);
    }

    return {
        getState: () => state,
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        setTitle, start, pause, resume, stop, rewind, play, send, cancel, reset,
        controls, announcement, surface,
    };
}

const ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'send', 'cancel'];

/** Vue composable: reactive snapshot plus the phase actions. */
export function useRecorderPhases(vue, options) {
    const machine = createRecorderPhases(options);
    const state = vue.shallowRef(machine.getState());
    machine.subscribe((next) => { state.value = next; });
    return {
        state: state,
        machine: machine,
        controls: vue.computed(() => (state.value, machine.controls())),
        announcement: vue.computed(() => (state.value, machine.announcement())),
    };
}

/**
 * Component for the phases. Native inputs and buttons give Tab, Enter and
 * Space; the status line is a polite live region; disabled buttons keep
 * aria-disabled. Styling uses the token surface and the shared focus ring.
 */
export function createRecorderPhasesComponent(vue) {
    const h = vue.h;
    return vue.defineComponent({
        name: 'RecorderPhasesPanel',
        props: { options: { type: Object, required: true } },
        setup(props) {
            const view = useRecorderPhases(vue, props.options);
            return () => {
                const s = view.state.value;
                const surface = view.machine.surface();
                const titleLocked = s.phase !== 'title' && s.phase !== 'ready';
                // V8 look: a panel with the shared form, button and status classes; the phase colour only tints the status line.
                return h('section', {
                    class: 'v8-page panel',
                    'aria-label': 'Diktat aufnehmen',
                    'data-phase': s.phase,
                }, [
                    h('label', { for: 'recorder-title', style: { display: 'block', marginBottom: '6px', fontSize: '12px', fontWeight: 650 } }, LABELS.title),
                    h('input', {
                        id: 'recorder-title',
                        type: 'text',
                        required: true,
                        'aria-required': 'true',
                        'aria-invalid': s.phase === 'title' ? 'true' : 'false',
                        disabled: titleLocked,
                        value: s.title,
                        onInput: (e) => view.machine.setTitle(e.target.value),
                    }),
                    h('div', { role: 'group', 'aria-label': 'Aufnahmesteuerung', class: 'actions', style: { margin: '14px 0 12px' } },
                        view.controls.value.map((c) => h('button', {
                            key: c.id,
                            type: 'button',
                            class: 'button',
                            style: c.id === 'start' || c.id === 'send'
                                ? { background: 'var(--ink)', borderColor: 'var(--ink)', color: 'white', fontSize: '13px', fontWeight: 600, padding: '9px 13px' }
                                : { padding: '9px 13px' },
                            disabled: c.disabled,
                            'aria-disabled': c.disabled ? 'true' : 'false',
                            onClick: () => ACTIONS.includes(c.id) && view.machine[c.id](),
                        }, c.label))),
                    h('p', {
                        role: 'status',
                        'aria-live': 'polite',
                        class: 'text-status',
                        style: { margin: 0, color: surface.status === 'error' ? 'var(--danger)' : undefined },
                    }, view.announcement.value),
                ]);
            };
        },
    });
}
