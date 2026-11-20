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

import { createRecorderPhases, LABELS, receiptDictationId } from './recorder-phases-ui.js';
import { surfaceFor } from './tokens.js';

export { receiptDictationId };
export const COMPOSE_RECORDING_VERSION = 1;

// clinic-v8.css states heading, button and form-control styles with zero-specificity element selectors; the Tailwind
// base layer, loaded after it, resets those. These strings restate them where this page needs them.
const H1 = 'font-size: clamp(26px, 4vw, 30px); line-height: 1.2; font-weight: 700; letter-spacing: -.8px; margin: 0 0 9px';
const H2 = 'font-size: 16px; line-height: 1.35; font-weight: 700; letter-spacing: -.2px; margin: 0 0 14px';
const FIELD = 'display: grid; gap: 6px; margin: 0 0 16px; min-width: 0';
const LABEL = 'font-size: 12px; font-weight: 650';
const PRIMARY = 'background: var(--ink); border-color: var(--ink); color: white';
const HELP = 'font-size: 11px; color: var(--v8-muted)';
const INPUT = 'width: 100%; min-height: 40px; padding: 10px 11px; border: 1px solid var(--line); border-radius: 7px; background-color: var(--surface); color: var(--v8-text); font: inherit';

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
    upload: LABELS.send,
    uploading: LABELS.sending,
    recordingControls: 'Aufnahmesteuerung',
    startFailed: 'Die Aufnahme konnte nicht gestartet werden. Bitte die Mikrofonfreigabe im Browser prüfen oder eine Datei hochladen.',
    recordFailed: 'Senden fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert.',
    fileFailed: 'Hochladen fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Audiodatei wird nicht lokal gespeichert.',
    sent: 'Gespeichert. Noch nicht an den Schreibdienst gesendet.',
    submit: 'Einreichen',
    retrySave: 'Erneut sichern',
    submitting: 'Diktat wird eingereicht …',
    submitted: 'Eingereicht.',
    submitUnknown: 'Einreichung nicht bestätigt. Bitte erneut versuchen.',
    created: 'Erstelltes Diktat',
    toWorklist: 'Zur Arbeitsliste',
    open: 'Öffnen',
    another: 'Weitere Aufnahme',
    discardFailed: 'Die Aufnahme konnte nicht verworfen werden.',
    denied: 'Keine Berechtigung für diese Aktion.',
    eyebrow: 'Aufnahmen',
    subline: 'Titel festlegen, anschliessend die Aufnahme im Browser starten oder eine Audiodatei hochladen.',
    info: 'Aufnahmeinformationen',
    authorHint: 'Angemeldete Person. Sie kann hier nicht geändert werden.',
    titleHint: 'Ein Titel ist erforderlich, bevor die Aufnahme startet.',
    storageNote: '„Entwurf sichern“ speichert die Aufnahme auf dem Server. Mit „Einreichen“ geben Sie sie an den Schreibdienst weiter.',
    state: 'Stand',
    noValue: '–',
    cancel: 'Abbrechen',
});

/** Short phase caption next to the recording controls; the live region carries the full message. */
export const PHASE_CAPTIONS = Object.freeze({
    title: 'Titel eingeben',
    ready: 'Bereit zur Aufnahme',
    recording: 'Aufnahme läuft',
    paused: 'Pausiert',
    review: 'Aufnahme beendet · Entwurf noch nicht gesichert',
    sending: LABELS.sending,
    sent: LABELS.sent,
    failed: 'Speicherung nicht bestätigt',
    idle: 'Keine Datei gewählt',
    selected: 'Datei gewählt',
    submitting: TEXT.submitting,
    submitted: TEXT.submitted,
    notDraft: 'Kein neuer Entwurf',
});

/** Inline SVG paths (24x24, filled with currentColor) of the recorder icon buttons. Shared with the live page. */
export const ICON_PATHS = Object.freeze({
    start: 'M12 5a7 7 0 1 0 0 14a7 7 0 0 0 0-14z',
    pause: 'M7 5h3v14H7z M14 5h3v14h-3z',
    resume: 'M8 5l11 7-11 7z',
    play: 'M3 9h4l5-4v14l-5-4H3z M16 8a6 6 0 0 1 0 8l-1-1a5 5 0 0 0 0-6z M19 5a10 10 0 0 1 0 14l-1-1a9 9 0 0 0 0-12z',
    stop: 'M6 6h12v12H6z',
    rewind: 'M6 5h2v14H6z M20 5v14L9 12z',
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

const SEND_REFUSALS = Object.freeze({
    AUDIO_REQUIRED: 'Für diesen Entwurf liegt keine gültige Audiofassung vor.',
    NOT_DRAFT: 'Das Diktat ist kein neuer Entwurf mehr. Bitte den aktuellen Stand öffnen.',
    NO_STAFF_POOL: 'Kein Schreibkräfte-Pool zugeordnet.',
    STALE_VERSION: 'Der Entwurf wurde geändert. Bitte den aktuellen Stand prüfen.',
});

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
    let submission = null;
    let submitError = '';
    let pendingSend = null;

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

    function receipt() {
        return state.mode === 'file' ? state.fileReceipt : machine.getState().receipt;
    }

    function created() {
        const id = receiptDictationId(receipt());
        return id === null ? null : { dictationId: id };
    }

    // The numeric recording id on older upload receipts is not a workflow target.
    function canSubmit() {
        const id = receipt()?.dictation_id;
        return typeof id === 'string' && !!id.trim() && typeof client?.request === 'function'
            && allowed() && !state.disposed && !submission;
    }

    async function submit() {
        if (!canSubmit()) return denied('invalid_phase');
        const id = receipt().dictation_id;
        const path = '/dictations/' + encodeURIComponent(id);
        submission = 'submitting';
        submitError = '';
        notify();
        try {
            if (!pendingSend) {
                const detail = await client.request(path + '/detail');
                if (detail.outcome !== 'ok' || detail.data?.dictation_id !== id || !detail.data.etag) {
                    submitError = detail.message || 'Der aktuelle Diktatstand konnte nicht geladen werden.';
                    return denied('detail_unavailable');
                }
                pendingSend = { idempotencyKey: globalThis.crypto.randomUUID(), ifMatch: detail.data.etag };
            }
            if (state.disposed || !allowed()) return denied('cancelled');
            const res = await client.request(path + '/send', { method: 'POST', body: {}, ...pendingSend });
            if (state.disposed) return denied('cancelled');
            if (res.outcome === 'ok' && res.data?.dictation_id === id && res.data.status) {
                submission = 'submitted';
                return { ok: true };
            }
            if (['conflict', 'permission', 'validation'].includes(res.outcome)) {
                pendingSend = null;
                submitError = SEND_REFUSALS[res.code] || res.message;
                if (res.code === 'NOT_DRAFT') submission = 'notDraft';
            }
            return denied(res.code || 'unconfirmed');
        } catch {
            return denied('unconfirmed');
        } finally {
            if (submission === 'submitting') {
                submission = null;
                submitError ||= TEXT.submitUnknown;
            }
            if (!state.disposed) notify();
        }
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
        if (!created() || submission === 'submitting') return denied('invalid_phase');
        submission = null;
        submitError = '';
        pendingSend = null;
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
        if (submitError) return submitError;
        if (submission) return TEXT[submission];
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
        if (submitError) return surfaceFor('error');
        if (submission === 'submitting') return surfaceFor('processing');
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
            phase: submission || phase(),
            canSubmit: canSubmit(),
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
        selectFile, sendFile, cancelFile, submit,
        openCreated, toWorklist, another, dispose,
    };
}

const RECORDER_ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'send', 'cancel'];

export const RECORDING_TEMPLATE = `
<section class="clinic-recording-view v8-page" data-testid="clinic-recording-view" aria-labelledby="crv-title">
  <nav class="breadcrumb" aria-label="Pfad"><span>Speakr</span><span aria-hidden="true">›</span><span>{{ c.text.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ c.text.title }}</span></nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ c.text.eyebrow }}</div>
      <h1 id="crv-title" style="${H1}">{{ c.text.title }}</h1>
      <p class="subline">{{ c.text.subline }}</p>
    </div>
    <div class="head-actions"></div>
  </div>
  <div class="grid">
    <section class="panel" :aria-label="c.text.info">
      <h2 style="${H2}">{{ c.text.info }}</h2>
      <div class="field-grid">
        <div style="${FIELD}" data-testid="author"><span style="${LABEL}">{{ c.text.author }}</span><strong>{{ m.author || m.authorId }}</strong><small style="${HELP}">{{ c.text.authorHint }}</small></div>
        <div style="${FIELD}">
          <span style="${LABEL}">{{ c.text.modes }}</span>
          <div role="group" :aria-label="c.text.modes" class="actions text-sm">
            <button type="button" class="button" style="min-height: 2.75rem" data-testid="mode-record" :style="m.mode === 'record' ? '${PRIMARY}' : null"
                    :aria-pressed="m.mode === 'record' ? 'true' : 'false'" :disabled="!m.canSwitchMode" @click="c.setMode('record')">{{ c.text.modeRecord }}</button>
            <button type="button" class="button" style="min-height: 2.75rem" data-testid="mode-file" :style="m.mode === 'file' ? '${PRIMARY}' : null"
                    :aria-pressed="m.mode === 'file' ? 'true' : 'false'" :disabled="!m.canSwitchMode" @click="c.setMode('file')">{{ c.text.modeFile }}</button>
          </div>
        </div>
        <template v-if="!m.created">
          <div v-if="m.mode === 'record'" class="full" style="${FIELD}">
            <label style="${LABEL}" for="crv-title-input">{{ c.text.dictationTitle }}</label>
            <input style="${INPUT}" id="crv-title-input" type="text" required aria-required="true" :aria-invalid="m.phase === 'title' ? 'true' : 'false'"
                   :disabled="m.titleLocked" :value="m.title" @input="c.setTitle($event.target.value)" />
            <small style="${HELP}">{{ c.text.titleHint }}</small>
          </div>
          <div v-else class="full" style="${FIELD}">
            <label style="${LABEL}" for="crv-file">{{ c.text.file }}</label>
            <input style="${INPUT}" id="crv-file" type="file" accept="audio/*" :disabled="!m.canSelectFile" @change="pick($event)" />
            <small v-if="m.file" style="${HELP}" data-testid="file-name">{{ c.text.fileSelected }}: {{ m.file.name }}</small>
          </div>
        </template>
      </div>
      <div class="note">{{ c.text.storageNote }}</div>
    </section>
    <div class="stack">
      <section class="panel" :aria-label="m.mode === 'record' ? c.text.recordingControls : c.text.modeFile">
        <h2 style="${H2}">{{ m.mode === 'record' ? c.text.recordingControls : c.text.modeFile }}</h2>
        <template v-if="!m.created">
          <div v-if="m.mode === 'record'">
            <div class="recorder compact-recorder" :class="{ recording: m.phase === 'recording' }">
              <div role="group" :aria-label="c.text.recordingControls" class="recorder-controls">
                <button v-for="b in iconControls(m.controls)" :key="b.id" type="button" class="icon-button" :class="{ 'record-button': b.id === 'start' }"
                        :data-testid="'rec-' + b.id" :disabled="b.disabled" :aria-disabled="b.disabled ? 'true' : 'false'" :aria-label="b.label" :title="b.label" @click="act(b.id)">
                  <svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="icons[b.id]"></path></svg>
                </button>
              </div>
              <div class="recorder-caption"><strong>{{ captions[m.phase] || m.phase }}</strong></div>
            </div>
            <div class="actions text-sm" style="margin-top: 12px">
              <button v-for="b in textControls(m.controls)" :key="b.id" type="button" class="button" :style="b.id === 'send' ? '${PRIMARY}' : null" style="min-height: 2.75rem"
                      :data-testid="'rec-' + b.id" :disabled="b.disabled" :aria-disabled="b.disabled ? 'true' : 'false'" @click="act(b.id)">{{ b.id === 'send' && m.phase === 'failed' ? c.text.retrySave : b.label }}</button>
            </div>
          </div>
          <div v-else class="recorder compact-recorder" style="flex-wrap: wrap">
            <div class="recorder-controls text-sm" style="flex-wrap: wrap; gap: 8px">
              <button type="button" class="button" style="min-height: 2.75rem; ${PRIMARY}" data-testid="file-upload"
                      :disabled="!m.canUpload" @click="c.sendFile()">{{ m.uploadLabel }}</button>
              <button type="button" class="button" style="min-height: 2.75rem" data-testid="file-cancel"
                      :disabled="!m.canCancelFile" @click="c.cancelFile()">{{ c.text.cancel }}</button>
            </div>
            <div class="recorder-caption" style="flex-basis: 100%"><strong>{{ captions[m.phase] || m.phase }}</strong></div>
          </div>
        </template>
        <p role="status" aria-live="polite" class="text-status" :class="{ error: m.surface.status === 'error' }" style="margin-top: 12px" data-testid="status">{{ m.message }}</p>
        <div v-if="m.created" data-testid="created">
          <div class="note"><span class="tag neutral dot">{{ c.text.created }}</span> <strong class="break-all" data-testid="created-id">{{ m.created.dictationId }}</strong></div>
          <hr class="separator">
          <div class="actions text-sm">
            <button v-if="m.phase !== 'submitted'" type="button" class="button" style="min-height: 2.75rem; ${PRIMARY}" data-testid="submit-draft" :disabled="!m.canSubmit" @click="c.submit()">{{ c.text.submit }}</button>
            <button type="button" class="button" style="min-height: 2.75rem" data-testid="to-worklist" :disabled="m.phase === 'submitting'" @click="c.toWorklist()">{{ c.text.toWorklist }}</button>
            <button type="button" class="button" style="min-height: 2.75rem" data-testid="open-created" :disabled="m.phase === 'submitting'" @click="c.openCreated()">{{ c.text.open }}</button>
            <button type="button" class="button" style="min-height: 2.75rem" data-testid="another" :disabled="m.phase === 'submitting'" @click="c.another()">{{ c.text.another }}</button>
          </div>
        </div>
      </section>
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
            captions: PHASE_CAPTIONS,
            icons: ICON_PATHS,
            iconControls: (list) => list.filter((b) => ICON_PATHS[b.id]),
            textControls: (list) => list.filter((b) => !ICON_PATHS[b.id]),
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
