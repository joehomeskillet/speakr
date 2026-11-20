/**
 * Composed clinic recording view, "Neue Aufnahme" (V8 P04, PAR-REC-001, PAR-REC-002).
 *
 * One Vue view over recorder-phases-ui (prepare, record, pause, review, upload)
 * plus the file upload path. The recorder itself is injected (the existing
 * recorder composable) and nothing from recorder-phases-ui is re-implemented.
 *
 * Rules applied:
 *   - The author is the logged-in user, shown read-only. There is no author or
 *     pool picker; none is provided by an approved module.
 *   - Audio and title stay in memory. This module never touches IndexedDB,
 *     localStorage or sessionStorage; a failed upload says so explicitly.
 *   - A dictation counts as created only when the upload resolves with a server
 *     receipt (an object carrying an id). The view shows the dictation id only
 *     (W16: no patient name, no title).
 *   - Leaving the view (dispose) discards an unsent recording; an upload in
 *     flight is not cancelled and its late result is ignored.
 *
 * Exports:
 *   RecordingView  Vue options; props client, currentUser, recorder, uploadFile,
 *                  player?, vue?, authorized?; emits `open` ({ dictationId }) and
 *                  `worklist`.
 *   registerRecordingView(app)  app.component('clinic-recording-view', ...).
 *   createRecordingViewController(options)  the controller behind the view.
 *
 * Injected `uploadFile(file)` resolves with the server receipt (id or
 * dictation_id); it is supplied by the mount package together with the recorder.
 */

import { createRecorderPhases } from './recorder-phases-ui.js';
import { surfaceFor } from './tokens.js';

export const COMPOSE_RECORDING_VERSION = 1;

export const TEXT = Object.freeze({
    title: 'Neue Aufnahme',
    author: 'Diktiert von',
    modes: 'Quelle',
    modeRecord: 'Aufnehmen',
    modeFile: 'Datei hochladen',
    dictationTitle: 'Titel',
    file: 'Audiodatei',
    fileSelected: 'Ausgewählte Datei',
    fileInvalid: 'Bitte eine nicht leere Audiodatei wählen.',
    upload: 'Hochladen',
    uploading: 'Wird hochgeladen',
    recordingControls: 'Aufnahmesteuerung',
    startFailed: 'Die Aufnahme konnte nicht gestartet werden. Bitte die Mikrofonfreigabe im Browser prüfen oder eine Datei hochladen.',
    recordFailed: 'Senden fehlgeschlagen. Das Diktat wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert.',
    fileFailed: 'Hochladen fehlgeschlagen. Das Diktat wurde nicht gesendet. Die Audiodatei wird nicht lokal gespeichert.',
    sent: 'Das Diktat wurde gesendet.',
    created: 'Erstelltes Diktat',
    toWorklist: 'Zur Arbeitsliste',
    open: 'Öffnen',
    another: 'Weitere Aufnahme',
    discardFailed: 'Die Aufnahme konnte nicht verworfen werden.',
    denied: 'Keine Berechtigung für diese Aktion.',
});

export const MODES = Object.freeze(['record', 'file']);
export const FILE_PHASES = Object.freeze(['idle', 'selected', 'sending', 'sent', 'failed']);

const AUDIO_EXTENSION = /\.(wav|mp3|m4a|aac|ogg|oga|opus|flac|webm|wma|ds2|dss)$/i;

/** A non-empty file that is an audio MIME type or has a known audio extension. */
export function isAudioFile(file) {
    if (!file || typeof file !== 'object') return false;
    if (!(Number(file.size) > 0)) return false;
    const type = typeof file.type === 'string' ? file.type : '';
    return type.startsWith('audio/') || AUDIO_EXTENSION.test(String(file.name || ''));
}

/** Dictation id of a server receipt, or null. */
export function receiptDictationId(receipt) {
    if (!receipt || typeof receipt !== 'object') return null;
    const id = receipt.dictation_id ?? receipt.id ?? receipt.recording_id ?? receipt.recordingId;
    if (typeof id === 'string' && id !== '') return id;
    return Number.isInteger(id) && id >= 0 ? String(id) : null;
}

function displayName(user) {
    const name = user.display_name || user.name || user.username || '';
    return typeof name === 'string' && name.trim() ? name.trim() : '';
}

/**
 * Framework-free controller. Subscribers are called after every state change.
 * @param {{client?: object, currentUser: {id: *}, recorder: object, uploadFile: Function,
 *   player?: object, authorized?: () => boolean, onOpen?: Function, onWorklist?: Function}} options
 */
export function createRecordingViewController(options) {
    const opts = options || {};
    const user = opts.currentUser || {};
    if (user.id === undefined || user.id === null || String(user.id) === '') throw new Error('currentUser.id is required');
    if (!opts.recorder) throw new Error('recorder is required');
    if (typeof opts.uploadFile !== 'function') throw new Error('uploadFile is required');
    const client = opts.client || null;
    const authorized = typeof opts.authorized === 'function' ? opts.authorized : () => true;
    const allowed = () => authorized() !== false;

    const machine = createRecorderPhases({ recorder: opts.recorder, player: opts.player || null, api: client, authorized: allowed });
    const listeners = new Set();
    const state = { mode: 'record', file: null, filePhase: 'idle', fileError: null, fileReceipt: null, disposed: false };
    let fileGeneration = 0;

    const notify = () => { for (const listener of listeners) listener(); };
    machine.subscribe(notify);

    const denied = (code) => ({ ok: false, code: code });
    const recorderBusy = () => !['title', 'ready'].includes(machine.getState().phase);

    function setMode(mode) {
        if (!allowed()) return denied('unauthorized');
        if (!MODES.includes(mode)) return denied('invalid_mode');
        if (recorderBusy() || state.filePhase === 'sending' || state.filePhase === 'sent') return denied('invalid_phase');
        if (mode !== state.mode) {
            state.mode = mode;
            notify();
        }
        return { ok: true };
    }

    function selectFile(file) {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'file' || !['idle', 'selected', 'failed'].includes(state.filePhase)) return denied('invalid_phase');
        if (!isAudioFile(file)) {
            Object.assign(state, { file: null, filePhase: 'idle', fileError: 'invalid_file' });
            notify();
            return denied('invalid_file');
        }
        Object.assign(state, { file: file, filePhase: 'selected', fileError: null });
        notify();
        return { ok: true };
    }

    async function sendFile() {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'file' || !['selected', 'failed'].includes(state.filePhase)) return denied('invalid_phase');
        const mine = ++fileGeneration;
        Object.assign(state, { filePhase: 'sending', fileError: null });
        notify();
        let receipt = null;
        try {
            receipt = await opts.uploadFile(state.file);
        } catch (e) {
            receipt = null;
        }
        if (mine !== fileGeneration || state.disposed) return denied('stale');
        if (receiptDictationId(receipt) === null) {
            Object.assign(state, { filePhase: 'failed', fileError: 'no_receipt' });
            notify();
            return denied('no_receipt');
        }
        Object.assign(state, { filePhase: 'sent', fileReceipt: receipt });
        notify();
        return { ok: true, receipt: receipt };
    }

    function cancelFile() {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'file' || state.filePhase === 'sending' || state.filePhase === 'sent') return denied('invalid_phase');
        fileGeneration += 1;
        Object.assign(state, { file: null, filePhase: 'idle', fileError: null });
        notify();
        return { ok: true };
    }

    /** Receipt of the created dictation, or null while nothing was sent. */
    function created() {
        const receipt = state.mode === 'file'
            ? (state.filePhase === 'sent' ? state.fileReceipt : null)
            : (machine.getState().phase === 'sent' ? machine.getState().receipt : null);
        const id = receiptDictationId(receipt);
        return id === null ? null : { dictationId: id };
    }

    /** Opens the created dictation in the page; false when none was created. */
    function openCreated() {
        const done = created();
        if (!done || typeof opts.onOpen !== 'function') return false;
        opts.onOpen({ dictationId: done.dictationId });
        return true;
    }

    function toWorklist() {
        if (!created() || typeof opts.onWorklist !== 'function') return false;
        opts.onWorklist();
        return true;
    }

    /** Starts the next dictation from a clean state after a successful send. */
    function another() {
        if (!created()) return denied('invalid_phase');
        Object.assign(state, { file: null, filePhase: 'idle', fileError: null, fileReceipt: null });
        if (machine.getState().phase === 'sent') machine.reset();
        if (client && typeof client.switchContext === 'function') client.switchContext({});
        notify();
        return { ok: true };
    }

    function phase() {
        return state.mode === 'file' ? state.filePhase : machine.getState().phase;
    }

    function message() {
        if (!allowed()) return TEXT.denied;
        const s = machine.getState();
        if (state.mode === 'file') {
            if (state.filePhase === 'sending') return TEXT.uploading;
            if (state.filePhase === 'sent') return TEXT.sent;
            if (state.filePhase === 'failed') return TEXT.fileFailed;
            return state.fileError === 'invalid_file' ? TEXT.fileInvalid : '';
        }
        if (s.phase === 'failed') return TEXT.recordFailed;
        if (s.error === 'start_failed') return TEXT.startFailed;
        if (s.error === 'discard_failed') return TEXT.discardFailed;
        if (s.phase === 'sent') return TEXT.sent;
        return machine.announcement();
    }

    function surface() {
        if (state.mode !== 'file') return machine.surface();
        const status = { idle: 'normal', selected: 'pending', sending: 'processing', sent: 'success', failed: 'error' }[state.filePhase];
        return surfaceFor(status);
    }

    /** Plain snapshot for rendering. */
    function model() {
        const s = machine.getState();
        const fileBusy = state.filePhase === 'sending' || state.filePhase === 'sent';
        return {
            mode: state.mode,
            phase: phase(),
            author: displayName(user),
            authorId: String(user.id),
            title: s.title,
            titleLocked: s.phase !== 'title' && s.phase !== 'ready',
            controls: machine.controls(),
            file: state.file ? { name: String(state.file.name || ''), size: Number(state.file.size) || 0 } : null,
            canSelectFile: allowed() && !fileBusy,
            canUpload: allowed() && (state.filePhase === 'selected' || state.filePhase === 'failed'),
            uploadLabel: state.filePhase === 'failed' ? 'Erneut hochladen' : TEXT.upload,
            canCancelFile: allowed() && (state.filePhase === 'selected' || state.filePhase === 'failed'),
            canSwitchMode: allowed() && !recorderBusy() && !fileBusy,
            created: created(),
            message: message(),
            surface: surface(),
        };
    }

    /** Leaves the view: discards an unsent recording and drops late upload results. */
    async function dispose() {
        if (state.disposed) return;
        state.disposed = true;
        fileGeneration += 1;
        listeners.clear();
        const p = machine.getState().phase;
        if (p !== 'sending' && p !== 'sent') {
            try {
                await machine.cancel();
            } catch (e) {
                // Nothing is persisted locally, so a failed discard leaves nothing behind.
            }
        }
    }

    return {
        text: TEXT,
        machine,
        model,
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        setMode, setTitle: (value) => machine.setTitle(value),
        start: () => machine.start(), pause: () => machine.pause(), resume: () => machine.resume(),
        stop: () => machine.stop(), rewind: () => machine.rewind(), play: () => machine.play(),
        send: () => machine.send(), cancel: () => machine.cancel(),
        selectFile, sendFile, cancelFile,
        openCreated, toWorklist, another, dispose,
    };
}

const RECORDER_ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'send', 'cancel'];

export const RECORDING_TEMPLATE = `
<section class="clinic-recording-view" data-testid="clinic-recording-view" aria-labelledby="crv-title"
         :style="{ background: m.surface.background, color: m.surface.color, border: '1px solid ' + m.surface.border }">
  <h1 id="crv-title" class="text-lg font-semibold">{{ c.text.title }}</h1>
  <p data-testid="author"><span>{{ c.text.author }}:</span> <strong>{{ m.author || m.authorId }}</strong></p>
  <div role="group" :aria-label="c.text.modes" class="flex flex-wrap gap-2 mt-2">
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="mode-record"
            :aria-pressed="m.mode === 'record' ? 'true' : 'false'" :disabled="!m.canSwitchMode" @click="c.setMode('record')">{{ c.text.modeRecord }}</button>
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="mode-file"
            :aria-pressed="m.mode === 'file' ? 'true' : 'false'" :disabled="!m.canSwitchMode" @click="c.setMode('file')">{{ c.text.modeFile }}</button>
  </div>
  <template v-if="!m.created">
    <div v-if="m.mode === 'record'" class="mt-3">
      <label for="crv-title-input">{{ c.text.dictationTitle }}</label>
      <input id="crv-title-input" type="text" required aria-required="true" :aria-invalid="m.phase === 'title' ? 'true' : 'false'"
             :disabled="m.titleLocked" :value="m.title" @input="c.setTitle($event.target.value)" />
      <div role="group" :aria-label="c.text.recordingControls" class="flex flex-wrap gap-2 mt-2">
        <button v-for="b in m.controls" :key="b.id" type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem"
                :data-testid="'rec-' + b.id" :disabled="b.disabled" :aria-disabled="b.disabled ? 'true' : 'false'" @click="act(b.id)">{{ b.label }}</button>
      </div>
    </div>
    <div v-else class="mt-3">
      <label for="crv-file">{{ c.text.file }}</label>
      <input id="crv-file" type="file" accept="audio/*" :disabled="!m.canSelectFile" @change="pick($event)" />
      <p v-if="m.file" data-testid="file-name">{{ c.text.fileSelected }}: {{ m.file.name }}</p>
      <div class="flex flex-wrap gap-2 mt-2">
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="file-upload"
                :disabled="!m.canUpload" @click="c.sendFile()">{{ m.uploadLabel }}</button>
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="file-cancel"
                :disabled="!m.canCancelFile" @click="c.cancelFile()">Abbrechen</button>
      </div>
    </div>
  </template>
  <p role="status" aria-live="polite" class="mt-2" data-testid="status">{{ m.message }}</p>
  <div v-if="m.created" class="mt-2" data-testid="created">
    <p>{{ c.text.created }}: <strong data-testid="created-id">{{ m.created.dictationId }}</strong></p>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="to-worklist" @click="c.toWorklist()">{{ c.text.toWorklist }}</button>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="open-created" @click="c.openCreated()">{{ c.text.open }}</button>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="another" @click="c.another()">{{ c.text.another }}</button>
    </div>
  </div>
</section>`;

export const RecordingView = {
    name: 'ClinicRecordingView',
    emits: ['open', 'worklist'],
    props: {
        client: { type: Object, default: null },
        currentUser: { type: Object, required: true },
        recorder: { type: Object, required: true },
        uploadFile: { type: Function, required: true },
        player: { type: Object, default: null },
        authorized: { type: Function, default: null },
        vue: { type: Object, default: null },
    },
    setup(props, ctx) {
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        if (!V) throw new Error('Vue is required to mount the recording view');
        const emit = (name, payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name, payload); };
        const c = createRecordingViewController({
            client: props.client,
            currentUser: props.currentUser,
            recorder: props.recorder,
            uploadFile: props.uploadFile,
            player: props.player,
            authorized: props.authorized || undefined,
            onOpen: (payload) => emit('open', payload),
            onWorklist: () => emit('worklist'),
        });
        const tick = V.ref(0);
        const off = c.subscribe(() => { tick.value += 1; });
        const m = V.computed(() => (tick.value, c.model()));
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => { off(); c.dispose(); });
        return {
            c,
            m,
            act: (id) => { if (RECORDER_ACTIONS.includes(id)) c[id](); },
            pick: (event) => {
                const files = event && event.target && event.target.files;
                c.selectFile(files && files[0] ? files[0] : null);
            },
        };
    },
    template: RECORDING_TEMPLATE,
};

export function registerRecordingView(app) {
    app.component('clinic-recording-view', RecordingView);
    return app;
}
