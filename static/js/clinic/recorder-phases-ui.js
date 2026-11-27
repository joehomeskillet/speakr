/**
 * Recorder phases: title, recording and send, on top of the existing
 * recorder composable (static/js/modules/composables/audio.js).
 *
 * The recorder is injected, so its state and upload stay the single source.
 * A draft counts as saved only when the upload resolves with a server
 * receipt (an object carrying an id). A rejected, interrupted or receipt-less
 * upload ends in `failed` and can be retried. Title and audio stay in memory.
 * An unlocked managed staging store can retain an encrypted offline copy.
 * Colours come from tokens.js.
 */

import { surfaceFor } from './tokens.js';

export const PHASES = Object.freeze(['title', 'ready', 'recording', 'paused', 'review', 'sending', 'sent', 'failed']);

const EDITABLE_TITLE_PHASES = Object.freeze(['title', 'ready', 'review', 'failed']);

/** Whether the dictation title may be edited in the given recorder phase. */
export function isTitleEditable(phase) {
    return EDITABLE_TITLE_PHASES.includes(phase);
}

export const LABELS = Object.freeze({
    title: 'Titel',
    titleHint: 'Ein Titel ist erforderlich.',
    start: 'Aufnahme starten',
    pause: 'Pausieren',
    resume: 'Fortsetzen',
    stop: 'Aufnahme beenden',
    rewind: 'Zurückspulen',
    play: 'Abspielen',
    pausePlayback: 'Wiedergabe pausieren',
    playbackFailed: 'Die Aufnahme konnte nicht abgespielt werden.',
    beginning: 'Anfang',
    end: 'Ende',
    send: 'Entwurf sichern',
    retry: 'Erneut senden',
    cancel: 'Abbrechen',
    sent: 'Gespeichert',
    failed: 'Der Server hat das Speichern des Entwurfs nicht bestätigt.',
    sending: 'Entwurf wird gesichert …',
    unauthorized: 'Keine Berechtigung für diese Aktion.',
});

const STATUS = Object.freeze({
    title: 'normal', ready: 'normal', recording: 'processing', paused: 'warning',
    review: 'pending', sending: 'processing', sent: 'success', failed: 'error',
});

/** Clinic dictation id, or the stored recording id on a legacy upload receipt. */
export function receiptDictationId(value) {
    if (!value || typeof value !== 'object') return null;
    const id = value.dictation_id ?? value.id ?? value.recording_id ?? value.recordingId;
    if (typeof id === 'string' && id.trim()) return id;
    return Number.isInteger(id) && id >= 0 ? String(id) : null;
}

/** Use the stock capture controls and send the finished microphone take through the clinic file upload. */
export function createClinicRecorder(audio, state, uploadFile, offlineDrafts = () => null) {
    let samples;
    const retained = new WeakMap();

    async function stopRecording() {
        const recorder = state.mediaRecorder.value;
        if (!state.isRecording.value || !recorder) return;
        // MediaRecorder queues the final dataavailable and stop events after stop() returns.
        const stopped = new Promise(resolve => recorder.addEventListener('stop', resolve, { once: true }));
        await audio.stopRecording();
        await stopped;
    }

    function recordedFile() {
        const chunks = state.audioChunks.value;
        if (!state.audioBlobURL.value || !chunks.length) throw new Error('no_recorded_audio');
        if (chunks.length === 1 && chunks[0] instanceof File) return chunks[0];
        const type = state.mediaRecorder.value.mimeType.split(';')[0];
        return new File(chunks, 'aufnahme.webm', { type });
    }

    return {
        startRecording: audio.startRecording,
        pauseRecording: audio.pauseRecording,
        resumeRecording: audio.resumeRecording,
        get elapsedSeconds() { return state.recordingTime?.value || 0; },
        get previewUrl() { return state.audioBlobURL.value; },
        readLevel() {
            const analyser = state.analyser?.value;
            if (!analyser) return 0;
            samples ||= new Uint8Array(analyser.fftSize);
            analyser.getByteTimeDomainData(samples);
            let peak = 0;
            for (const sample of samples) peak = Math.max(peak, Math.abs(sample - 128));
            return peak / 128;
        },
        stopRecording,
        reviewSource: () => state.audioBlobURL.value,
        canUseLocalFiles: () => offlineDrafts()?.canUseLocalFiles === true,
        recordedFile,
        openRecordedFile(file) {
            if (offlineDrafts()?.canUseLocalFiles !== true) throw new Error('local_denied');
            const url = URL.createObjectURL(file);
            if (state.audioBlobURL.value) URL.revokeObjectURL(state.audioBlobURL.value);
            state.audioChunks.value = [file];
            state.audioBlobURL.value = url;
        },
        async discardRecording() {
            await stopRecording();
            await audio.clearRecording();
        },
        async uploadRecordedAudio({ title }) {
            const chunks = state.audioChunks.value;
            const file = recordedFile();
            const drafts = offlineDrafts();
            let binding = retained.get(chunks);
            let receipt;
            try {
                receipt = await uploadFile(file, title);
                if (receiptDictationId(receipt) === null) throw new Error('no_receipt');
            } catch (error) {
                if (drafts && (globalThis.navigator?.onLine === false || error instanceof TypeError)) {
                    try {
                        binding ||= { dictationId: crypto.randomUUID(), sequence: 0 };
                        if (await drafts.saveTake(binding, file, title)) retained.set(chunks, binding);
                    } catch (e) { /* the original upload failure still owns the retry state */ }
                }
                if (retained.has(chunks)) throw new Error('offline_retained');
                throw error;
            }
            if (binding && drafts) {
                try {
                    await drafts.removeTake(binding);
                } catch (e) { /* a local cleanup failure cannot invalidate a server receipt */ }
            }
            // A new take gets its own chunk array as soon as capture starts.
            if (state.audioChunks.value === chunks) await audio.clearRecording();
            return receipt;
        },
    };
}

function localDictationTitle() {
    const now = new Date();
    const twoDigits = (value) => String(value).padStart(2, '0');
    return 'Diktat ' + [twoDigits(now.getDate()), twoDigits(now.getMonth() + 1), now.getFullYear()].join('.')
        + ' ' + [twoDigits(now.getHours()), twoDigits(now.getMinutes())].join(':');
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
    const { recorder, player = null, api = null, authorized = () => true, automaticTitle = false } = options;
    let media = player;
    let capturePending = false;
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
        if (!isTitleEditable(state.phase)) return denied('invalid_phase');
        const nextPhase = (state.phase === 'title' || state.phase === 'ready')
            ? (title.trim() ? 'ready' : 'title')
            : state.phase;
        set({ title: title, phase: nextPhase });
        return { ok: true };
    }

    async function start() {
        const blocked = guard(automaticTitle ? ['title', 'ready'] : ['ready']);
        if (blocked) return blocked;
        if (capturePending) return denied('capture_pending');
        const title = state.title.trim() || localDictationTitle();
        const mine = generation;
        capturePending = true;
        set({ error: null });
        try {
            if (await recorder.startRecording() === false) return denied('start_cancelled');
            if (mine !== generation) {
                await recorder.discardRecording();
                return denied('stale');
            }
            set({ phase: 'recording', title, error: null });
            return { ok: true };
        } catch (e) {
            const code = ['NotAllowedError', 'SecurityError'].includes(e?.name) ? 'microphone_denied' : 'start_failed';
            if (mine === generation) set({ error: code });
            return denied(code);
        } finally {
            capturePending = false;
            set({});
        }
    }

    function openFile(file) {
        const blocked = guard(['title', 'ready', 'review', 'failed']);
        if (blocked) return blocked;
        if (capturePending) return denied('capture_pending');
        media?.pause?.();
        recorder.openRecordedFile(file);
        set({ phase: 'review', title: state.title.trim() || localDictationTitle(), error: null });
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
        if (capturePending) return denied('capture_pending');
        capturePending = true;
        const mine = generation;
        try {
            await recorder.stopRecording();
            if (mine !== generation) return denied('stale');
            set({ phase: 'review' });
            return { ok: true };
        } finally {
            capturePending = false;
        }
    }

    function setPlayer(element) {
        media = player || element;
        set({});
    }

    function seek(time) {
        const blocked = guard(['review']);
        if (blocked) return blocked;
        if (!media || !Number.isFinite(time)) return denied('playback_unavailable');
        const duration = Number.isFinite(media.duration) ? media.duration : Infinity;
        media.currentTime = Math.max(0, Math.min(time, duration));
        return { ok: true };
    }

    const rewind = () => seek(0);
    const end = () => seek(media?.duration);
    const skip = (seconds) => seek(media?.currentTime + seconds);
    const playing = () => media?.paused === false && !media.ended;

    function syncPlayback() {
        set({});
    }

    function playbackFailed() {
        if (state.phase === 'review') set({ error: 'playback_failed' });
    }

    async function play() {
        const blocked = guard(['review']);
        if (blocked) return blocked;
        if (!media || typeof media.play !== 'function') return denied('playback_unavailable');
        try {
            if (playing()) media.pause();
            else if (media.currentTime !== media.duration || media.ended) await media.play();
            set({ error: null });
            return { ok: true };
        } catch (e) {
            if (e.name !== 'AbortError') playbackFailed();
            return denied('playback_failed');
        }
    }

    function stopPlayback() {
        const blocked = guard(['review']);
        if (blocked) return blocked;
        media?.pause?.();
        return { ok: true };
    }

    async function send() {
        const blocked = guard(['review', 'failed']);
        if (blocked) return blocked;
        if (!automaticTitle && !state.title.trim()) return denied('missing_title');
        const mine = ++generation;
        media?.pause?.();
        set({ phase: 'sending', error: null });
        let receipt = null;
        let error = 'no_receipt';
        try {
            receipt = await recorder.uploadRecordedAudio({ title: state.title.trim() || localDictationTitle() });
        } catch (e) {
            if (e?.message === 'offline_retained') error = 'offline_retained';
        }
        if (mine !== generation) return denied('stale');
        if (receiptDictationId(receipt) === null) {
            set({ phase: 'failed', error });
            return denied(error);
        }
        set({ phase: 'sent', receipt: receipt });
        return { ok: true, receipt: receipt };
    }

    async function cancel() {
        if (!authorized()) return denied('unauthorized');
        if (state.phase === 'sending' || state.phase === 'sent') return denied('invalid_phase');
        generation += 1;
        media?.pause?.();
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
        const hasTitle = automaticTitle || Boolean(state.title.trim());
        const row = (id, label, enabled) => ({ id: id, label: label, disabled: !on || !enabled });
        return [
            row('start', LABELS.start, !capturePending && (p === 'ready' || (automaticTitle && p === 'title'))),
            row('pause', LABELS.pause, p === 'recording'),
            row('resume', LABELS.resume, p === 'paused'),
            row('stop', LABELS.stop, p === 'recording' || p === 'paused'),
            row('rewind', LABELS.rewind, p === 'review' && !!media),
            row('play', playing() ? LABELS.pausePlayback : LABELS.play, p === 'review' && !!media),
            ...(p === 'review' ? [
                row('beginning', LABELS.beginning, !!media),
                row('end', LABELS.end, Number.isFinite(media?.duration)),
            ] : []),
            row('send', p === 'failed' ? LABELS.retry : LABELS.send, (p === 'review' || p === 'failed') && hasTitle),
            row('cancel', LABELS.cancel, p !== 'sending' && p !== 'sent'),
        ];
    }

    function announcement() {
        const p = state.phase;
        if (!authorized()) return LABELS.unauthorized;
        if (p === 'sent') return LABELS.sent;
        if (p === 'sending') return LABELS.sending;
        if (!automaticTitle && !state.title.trim() && isTitleEditable(p)) return LABELS.titleHint;
        if (p === 'failed') return LABELS.failed;
        if (state.error === 'playback_failed') return LABELS.playbackFailed;
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
        setTitle, start, openFile, pause, resume, stop, rewind, play, send, cancel, reset,
        beginning: rewind, end, skip, stopPlayback, setPlayer, syncPlayback, playbackFailed,
        controls, announcement, surface,
    };
}

const ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'beginning', 'end', 'send', 'cancel'];

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
                const titleLocked = !isTitleEditable(s.phase);
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
                        'aria-invalid': !s.title.trim() ? 'true' : 'false',
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
