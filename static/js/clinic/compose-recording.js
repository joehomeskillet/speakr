/**
 * Composed clinic recording view, "Neue Aufnahme" (V8 P04, PAR-REC-001, PAR-REC-002).
 *
 * One Vue view over recorder-phases-ui (prepare, record, pause, review, upload)
 * plus the file upload path. The recorder itself is injected (the existing
 * recorder composable) and nothing from recorder-phases-ui is re-implemented.
 *
 * Rules applied:
 *   - The author is the logged-in user. A server check confirms a route before capture.
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

import { createRecorderPhases, isTitleEditable, LABELS, receiptDictationId } from './recorder-phases-ui.js';
import { surfaceFor } from './tokens.js';

export { receiptDictationId };
export const COMPOSE_RECORDING_VERSION = 1;

const TOUCH = 'min-height: 2.75rem; min-block-size: max(2.75rem, 48px)';
const LINK = `${TOUCH}; display: inline-flex; align-items: center; text-decoration: underline`;

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
    startFailed: 'Prüfen Sie Ihr Mikrofon oder wählen Sie „oder Datei hochladen“.',
    microphoneDenied: 'Erlauben Sie den Mikrofonzugriff in den Website-Einstellungen Ihres Browsers und starten Sie die Aufnahme erneut.',
    routingPending: 'Berechtigung wird geprüft …',
    routingMissing: 'Ihr Konto ist noch keinem Autorenpool zugeordnet. Bitte wenden Sie sich an die Verwaltung.',
    routingError: 'Die Berechtigung konnte nicht geprüft werden. Bitte laden Sie die Seite neu.',
    recordFailed: 'Speichern nicht bestätigt. Das Diktat wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert.',
    fileFailed: 'Speichern nicht bestätigt. Das Diktat wurde nicht gesendet. Die Audiodatei wird nicht lokal gespeichert.',
    sent: 'Gespeichert.',
    submit: 'Einreichen',
    retrySave: 'Erneut sichern',
    submitting: 'Diktat wird eingereicht …',
    checkingTarget: 'Das vorgesehene Ziel wird geladen …',
    targetUnavailable: 'Die Vorgabe ist nicht verfügbar.',
    previewFailed: 'Das vorgesehene Ziel konnte nicht geladen werden. Bitte erneut versuchen.',
    submitted: 'Eingereicht.',
    submitUnknown: 'Einreichung nicht bestätigt. Bitte erneut versuchen.',
    created: 'Erstelltes Diktat',
    toWorklist: 'Zur Arbeitsliste',
    open: 'Diktat öffnen',
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
    NO_STAFF_POOL: 'Für dieses Diktat ist kein Schreibteam erreichbar.',
    STALE_VERSION: 'Der Entwurf oder die Vorgabe wurde geändert. Bitte das Ziel erneut prüfen.',
    RECIPIENT_NOT_ELIGIBLE: 'Das gewählte Ziel ist nicht mehr verfügbar. Bitte das Ziel erneut prüfen.',
    CHOICE_DISABLED: 'Die erlaubten Ziele haben sich geändert. Bitte das Ziel erneut prüfen.',
    PERSON_NOT_ALLOWED: 'Die erlaubten Ziele haben sich geändert. Bitte das Ziel erneut prüfen.',
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

    const machine = createRecorderPhases({
        recorder: opts.recorder, player: opts.player || null, api: client,
        authorized: allowed, automaticTitle: opts.oneButton,
    });
    const listeners = new Set();
    const state = { mode: 'record', file: null, filePhase: 'idle', fileError: null, fileReceipt: null, disposed: false };
    let fileGeneration = 0;
    let switchingUser = false;
    let submission = null;
    let submitError = '';
    let pendingSend = null;
    let sendPreview = null;
    let selectedTarget = -1;
    let confirmedTarget = '';
    // A host that calls checkRouting() right after mounting (RecordingView) starts without the pending state.
    let routing = opts.oneButton && !opts.preflightOnMount ? 'routingPending' : null;

    const notify = () => { for (const listener of listeners) listener(); };
    machine.subscribe(notify);

    const denied = (code) => ({ ok: false, code: code });
    const recorderBusy = () => !['title', 'ready'].includes(machine.getState().phase);

    async function checkRouting() {
        routing = 'routingPending';
        notify();
        let available;
        try {
            const response = await (opts.fetchPreflight || globalThis.fetch)('/api/config?recording_preflight=1', { cache: 'no-store' });
            if (response.ok) available = (await response.json()).routable;
        } catch {
            // A failed preflight must never enable capture.
        }
        if (state.disposed) return;
        routing = available === true ? null : available === false ? 'routingMissing' : 'routingError';
        notify();
    }

    function setMode(mode) {
        if (!allowed()) return denied('unauthorized');
        if (!MODES.includes(mode)) return denied('invalid_mode');
        if (machine.capturePending() || recorderBusy() || state.filePhase === 'sending' || state.filePhase === 'sent') return denied('invalid_phase');
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

    const dictationPath = () => '/dictations/' + encodeURIComponent(receipt().dictation_id);

    function handleRefusal(result) {
        submitError = SEND_REFUSALS[result.code] || result.message;
        if (result.code === 'NOT_DRAFT') submission = 'notDraft';
    }

    async function prepareSubmit() {
        if (!canSubmit() || sendPreview) return denied('invalid_phase');
        const id = receipt().dictation_id;
        submission = 'checkingTarget';
        submitError = '';
        notify();
        try {
            const res = await client.request(dictationPath() + '/send-preview');
            if (state.disposed || !allowed()) return denied('cancelled');
            const data = res.data;
            if (res.outcome === 'ok' && data?.dictation_id === id && data.etag
                && Number.isInteger(data.default_version) && Array.isArray(data.options)) {
                sendPreview = data;
                return { ok: true };
            }
            handleRefusal(res);
            return denied(res.code || 'preview_unavailable');
        } catch {
            return denied('preview_unavailable');
        } finally {
            if (submission === 'checkingTarget') submission = null;
            if (!sendPreview) submitError ||= TEXT.previewFailed;
            if (!state.disposed) notify();
        }
    }

    function selectTarget(index) {
        if (!allowed() || !sendPreview || submission || pendingSend) return;
        if (index !== -1 && !sendPreview.options[index]) return;
        selectedTarget = index;
        notify();
    }

    function cancelSubmit() {
        if (!sendPreview || submission || pendingSend) return;
        sendPreview = null;
        selectedTarget = -1;
        notify();
    }

    const canConfirm = () => canSubmit() && !!(sendPreview?.options[selectedTarget] || sendPreview?.default_label);

    async function submit() {
        if (!canConfirm()) return denied('invalid_phase');
        const id = receipt().dictation_id;
        const path = dictationPath();
        submission = 'submitting';
        submitError = '';
        notify();
        try {
            if (!pendingSend) {
                const body = { expected_version: sendPreview.default_version };
                const override = sendPreview.options[selectedTarget];
                if (override) body.override = { mode: override.mode, target_ids: override.target_ids };
                pendingSend = { method: 'POST', body, idempotencyKey: globalThis.crypto.randomUUID(), ifMatch: sendPreview.etag };
            }
            if (state.disposed || !allowed()) return denied('cancelled');
            const res = await client.request(path + '/send', pendingSend);
            if (state.disposed || !allowed()) return denied('cancelled');
            const label = res.data?.routing_target?.label;
            if (res.outcome === 'ok' && res.data?.dictation_id === id && res.data.status && typeof label === 'string' && label) {
                confirmedTarget = label;
                sendPreview = null;
                pendingSend = null;
                submission = 'submitted';
                return { ok: true };
            }
            if (['conflict', 'permission', 'validation'].includes(res.outcome)) {
                pendingSend = null;
                sendPreview = null;
                selectedTarget = -1;
                handleRefusal(res);
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

    async function sendRecording() {
        if (state.mode !== 'record' || routing || state.disposed) return denied('invalid_phase');
        return machine.send();
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
        if (!created() || submission === 'submitting' || submission === 'checkingTarget' || pendingSend) return denied('invalid_phase');
        submission = null;
        submitError = '';
        sendPreview = null;
        selectedTarget = -1;
        confirmedTarget = '';
        Object.assign(state, { file: null, filePhase: 'idle', fileError: null, fileReceipt: null });
        if (machine.getState().phase === 'sent') machine.reset();
        if (client && typeof client.switchContext === 'function') client.switchContext({});
        notify();
        if (opts.oneButton && state.mode === 'record') void checkRouting();
        return { ok: true };
    }

    function phase() {
        return state.mode === 'file' ? state.filePhase : machine.getState().phase;
    }

    function message() {
        if (!allowed()) return TEXT.denied;
        if (routing) return TEXT[routing];
        if (submitError) return submitError;
        if (submission) return TEXT[submission];
        if (sendPreview) return '';
        const s = machine.getState();
        if (state.mode === 'file') {
            if (state.filePhase === 'sending') return TEXT.uploading;
            if (state.filePhase === 'sent') return TEXT.sent;
            if (state.filePhase === 'failed') return TEXT.fileFailed;
            return state.fileError === 'invalid_file' ? TEXT.fileInvalid : '';
        }
        if (s.phase === 'failed') return opts.oneButton || s.title.trim() ? TEXT.recordFailed : LABELS.titleHint;
        if (s.error === 'microphone_denied') return TEXT.microphoneDenied;
        if (s.error === 'start_failed') return TEXT.startFailed;
        if (s.error === 'discard_failed') return TEXT.discardFailed;
        if (s.phase === 'sent') return TEXT.sent;
        return machine.announcement();
    }

    function surface() {
        if (submitError) return surfaceFor('error');
        if (submission === 'submitting' || submission === 'checkingTarget') return surfaceFor('processing');
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
            routing,
            canSubmit: canSubmit(),
            canConfirm: canConfirm(),
            sendPreview,
            selectedTarget,
            retrySend: !!pendingSend,
            confirmedTarget,
            author: displayName(user),
            authorId: String(user.id),
            title: s.title,
            titleLocked: !isTitleEditable(s.phase),
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

    const switchBusy = () => machine.capturePending() || machine.getState().phase === 'sending'
        || state.filePhase === 'sending' || submission === 'checkingTarget' || submission === 'submitting'
        || !!pendingSend;
    const hasDraft = () => {
        const { phase: capturePhase, title } = machine.getState();
        return switchBusy() || (capturePhase !== 'sent' && (recorderBusy() || !!title.trim()))
            || (!!state.file && state.filePhase !== 'sent');
    };

    async function prepare(action) {
        if (state.disposed || switchingUser || switchBusy()) return false;
        if (!['save', 'discard', 'leave'].includes(action) || (action === 'leave' && hasDraft())) return false;
        switchingUser = true;
        try {
            if (hasDraft()) {
                const p = machine.getState().phase;
                if (action === 'save') {
                    if (state.mode === 'file') {
                        if (!(await sendFile()).ok) return false;
                    } else {
                        if (['recording', 'paused'].includes(p) && !(await machine.stop()).ok) return false;
                        if (state.disposed || !(await sendRecording()).ok) return false;
                    }
                } else {
                    // The app calls this only after the user confirms discard.
                    if (state.mode === 'file' && state.file && !cancelFile().ok) return false;
                    if (p !== 'sent' && !(await machine.cancel()).ok) return false;
                }
            }
            if (state.disposed) return false;
            await dispose(true);
            return true;
        } catch {
            return false;
        } finally {
            switchingUser = false;
        }
    }

    const whileActive = (fn) => (...args) => state.disposed || switchingUser ? denied('invalid_phase') : fn(...args);

    /** Leaves the view: discards an unsent recording and drops late upload results. */
    async function dispose(forSwitch = false) {
        if (state.disposed) return;
        state.disposed = true;
        fileGeneration += 1;
        if (forSwitch) {
            Object.assign(state, { file: null, filePhase: 'idle', fileError: null, fileReceipt: null });
            submission = sendPreview = null;
            confirmedTarget = submitError = '';
            machine.clearForSwitch();
        }
        listeners.clear();
        const p = machine.getState().phase;
        if (!forSwitch && p !== 'sending' && p !== 'sent') {
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
        setMode: whileActive(setMode), setTitle: whileActive(machine.setTitle),
        start: whileActive(() => routing || state.mode !== 'record' ? denied('invalid_phase') : machine.start()),
        pause: whileActive(machine.pause), resume: whileActive(machine.resume),
        stop: whileActive(machine.stop), rewind: machine.rewind, play: machine.play,
        beginning: machine.beginning, end: machine.end,
        send: whileActive(machine.send), cancel: whileActive(machine.cancel),
        selectFile: whileActive(selectFile), sendFile: whileActive(sendFile), cancelFile: whileActive(cancelFile),
        prepareSubmit: whileActive(prepareSubmit), selectTarget, cancelSubmit, submit: whileActive(submit),
        sendRecording: whileActive(sendRecording), checkRouting,
        openCreated: () => !state.disposed && !switchingUser && openCreated(),
        toWorklist: () => !state.disposed && !switchingUser && toWorklist(), another: whileActive(another), dispose,
        hasDraft, prepare,
    };
}

const RECORDER_ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'beginning', 'end', 'send', 'cancel'];
const REVIEW_SKIP_SECONDS = 5;
const CAPTURE_TOGGLE = { title: 'start', ready: 'start', recording: 'pause', paused: 'resume' };

export const RECORDING_TEMPLATE = `
<section ref="root" class="clinic-recording-view v8-page" data-testid="clinic-recording-view" aria-labelledby="crv-title"
         style="display: grid; place-items: center; min-height: min(56dvh, 30rem); padding: var(--ui-space-6) var(--ui-space-4); color: var(--ui-text)">
  <h1 id="crv-title" class="sr-only">{{ c.text.title }}</h1>
  <div style="width: 100%; max-width: 28rem; text-align: center">
    <p v-show="m.message" role="status" aria-live="polite" data-testid="status" style="margin: 0 0 var(--ui-space-3)"
       :style="m.phase === 'submitted' ? { fontSize: '1.25rem', fontWeight: 650 } : null">{{ m.message }}
      <span v-if="m.confirmedTarget" data-testid="send-receipt" style="display: block; font-size: 1rem; font-weight: normal; overflow-wrap: anywhere">Ziel: {{ m.confirmedTarget }}</span>
    </p>
    <template v-if="!m.routing">
      <template v-if="m.mode === 'record'">
        <div v-if="m.phase === 'title' || m.phase === 'ready'" style="display: grid; justify-items: center; gap: var(--ui-space-2)">
          <button type="button" class="button accent" data-testid="rec-start" :aria-keyshortcuts="shortcuts.start" :disabled="!enabled('start')" @click="c.start()"
                  style="width: 10rem; height: 10rem; border-radius: 50%; flex-direction: column; gap: var(--ui-space-2); font-size: 1rem">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="currentColor" aria-hidden="true"><path :d="icons.start"></path></svg>
            Aufnahme starten
          </button>
          <a href="#/neu" data-testid="mode-file" @click.prevent="c.setMode('file')" style="${LINK}; font-size: .875rem">oder Datei hochladen</a>
        </div>
        <div v-else-if="m.phase === 'recording' || m.phase === 'paused'" style="display: grid; justify-items: center; gap: var(--ui-space-4)">
          <p v-if="m.phase === 'paused'" style="margin: 0">Pausiert</p>
          <output aria-label="Aufnahmedauer" aria-live="off" data-testid="elapsed" style="font-size: 2.75rem; font-variant-numeric: tabular-nums">{{ elapsed }}</output>
          <div role="meter" :aria-valuenow="Math.round(level * 100)" aria-valuemin="0" aria-valuemax="100" aria-label="Mikrofonpegel"
               style="width: min(100%, 18rem); height: .5rem; border-radius: var(--ui-radius-sm); background: var(--ui-border); overflow: hidden">
            <span style="display: block; height: 100%; background: var(--brand-accent); transform-origin: left"
                  :style="{ transform: 'scaleX(' + level + ')' }"></span>
          </div>
          <div class="actions" style="justify-content: center">
            <button v-if="m.phase === 'recording'" type="button" class="button" style="${TOUCH}" data-testid="rec-pause" :aria-keyshortcuts="shortcuts.pause" @click="c.pause()">Pause</button>
            <button v-else type="button" class="button" style="${TOUCH}" data-testid="rec-resume" :aria-keyshortcuts="shortcuts.resume" @click="c.resume()">Fortsetzen</button>
            <button type="button" class="button accent" style="${TOUCH}" data-testid="rec-stop" :aria-keyshortcuts="shortcuts.stop" @click="c.stop()">Stopp</button>
          </div>
        </div>
        <div v-else-if="m.phase === 'review' || m.phase === 'failed'"
             style="display: grid; justify-items: center; gap: var(--ui-space-3)">
          <audio v-if="previewUrl" :ref="bindReview" controls preload="metadata" :src="previewUrl" aria-label="Aufnahme anhören"
                 @loadedmetadata="loadReview" @durationchange="c.machine.syncPlayback()" @seeked="finishDurationProbe"
                 @play="c.machine.syncPlayback()" @pause="c.machine.syncPlayback()" @ended="c.machine.syncPlayback()"
                 @error="c.machine.playbackFailed()" style="width: 100%; min-height: 3rem"></audio>
          <button type="button" class="button primary" style="${TOUCH}; width: 100%; font-size: 1rem" data-testid="rec-send" @click="c.sendRecording()">
            {{ m.phase === 'review' ? 'Entwurf sichern' : c.text.retrySave }}
          </button>
          <button type="button" class="button quiet" style="${TOUCH}; font-size: .875rem"
                  data-testid="rec-cancel" @click="discard">Neu aufnehmen</button>
        </div>
      </template>
      <div v-else-if="!m.created" style="display: grid; gap: var(--ui-space-3); text-align: left">
        <label for="crv-file">{{ c.text.file }}</label>
        <input id="crv-file" type="file" accept="audio/*" :disabled="!m.canSelectFile" @change="pick" style="${TOUCH}; max-width: 100%" />
        <div class="actions">
          <button type="button" class="button primary" style="${TOUCH}" data-testid="file-upload"
                  :disabled="!m.canUpload" @click="c.sendFile()">{{ m.uploadLabel }}</button>
          <button type="button" class="button quiet" style="${TOUCH}" data-testid="file-cancel"
                  :disabled="!m.canSwitchMode" @click="backToRecorder">Zurück zur Aufnahme</button>
        </div>
      </div>
      <div v-if="m.created" data-testid="created">
        <span class="sr-only" data-testid="created-id">{{ m.created.dictationId }}</span>
        <div v-if="m.sendPreview" data-testid="send-preview" style="display: grid; gap: var(--ui-space-3); margin-bottom: var(--ui-space-3)">
          <h2 ref="previewHeading" tabindex="-1" style="margin: 0">Diktat einreichen</h2>
          <p v-if="m.sendPreview.default_label" style="margin: 0; overflow-wrap: anywhere">
            Vorgabe <strong data-testid="send-default">{{ m.sendPreview.default_label }}</strong>
          </p>
          <p v-else style="margin: 0">{{ c.text.targetUnavailable }}</p>
          <template v-if="m.sendPreview.options.length">
            <label for="crv-send-target" style="text-align: left">{{ m.sendPreview.default_label ? 'Anderes Ziel wählen' : 'Ziel wählen' }}</label>
            <select id="crv-send-target" :value="m.selectedTarget" :disabled="m.retrySend || m.phase === 'submitting'"
                    @change="c.selectTarget(Number($event.target.value))" style="${TOUCH}; min-width: 0; border-color: var(--ui-text-muted)">
              <option :value="-1" :disabled="!m.sendPreview.default_label">{{ m.sendPreview.default_label ? 'Vorgabe verwenden' : 'Bitte wählen' }}</option>
              <option v-for="(target, index) in m.sendPreview.options" :key="index" :value="index">{{ target.label }}</option>
            </select>
          </template>
          <div class="actions" style="justify-content: center">
            <button v-if="m.sendPreview.default_label || m.sendPreview.options.length" type="button" class="button primary"
                    style="${TOUCH}" :disabled="!m.canConfirm" @click="c.submit()">{{ m.retrySend && m.phase !== 'submitting' ? 'Erneut einreichen' : 'Jetzt einreichen' }}</button>
            <button v-if="!m.retrySend" type="button" class="button quiet" style="${TOUCH}"
                    :disabled="m.phase === 'submitting'" @click="c.cancelSubmit()">{{ c.text.cancel }}</button>
          </div>
        </div>
        <div class="actions" style="justify-content: center">
          <button v-if="!m.sendPreview && (m.canSubmit || m.phase === 'checkingTarget')" ref="submitOpener"
                  type="button" class="button primary" style="${TOUCH}" data-testid="submit-draft" :disabled="!m.canSubmit"
                  @click="c.prepareSubmit()">{{ c.text.submit }}</button>
          <a href="#/diktat" data-testid="open-created" style="${LINK}" @click.prevent="c.openCreated()">{{ c.text.open }}</a>
          <template v-if="!m.sendPreview && m.phase !== 'checkingTarget'">
            <button v-if="m.mode === 'file'" type="button" class="button" style="${TOUCH}" data-testid="to-worklist" @click="c.toWorklist()">{{ c.text.toWorklist }}</button>
            <button type="button" class="button" style="${TOUCH}" data-testid="another" @click="c.another()">{{ c.text.title }}</button>
          </template>
        </div>
      </div>
    </template>
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
        const registerSwitch = V.inject?.('clinicUserSwitch', null);
        let unregisterSwitch;
        const emit = (name, payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name, payload); };
        const c = createRecordingViewController({
            client: props.client,
            currentUser: props.currentUser,
            recorder: props.recorder,
            uploadFile: props.uploadFile,
            player: props.player,
            authorized: props.authorized || undefined,
            oneButton: true,
            preflightOnMount: true,
            onOpen: (payload) => emit('open', payload),
            onWorklist: () => emit('worklist'),
        });
        const tick = V.ref(0);
        const off = c.subscribe(() => { tick.value += 1; });
        const m = V.computed(() => (tick.value, c.model()));
        const root = V.ref(null);
        const previewHeading = V.ref(null);
        const submitOpener = V.ref(null);
        V.watch?.(() => !!m.value.sendPreview, (open) => {
            (open ? previewHeading.value : submitOpener.value)?.focus();
        }, { flush: 'post' });
        const reviewSource = () => props.player ? null : props.recorder.reviewSource?.();
        let probingDuration = false;
        function bindReview(media) {
            if (!media) {
                probingDuration = false;
                c.machine.setPlayer(null);
            }
        }
        function loadReview({ currentTarget: media }) {
            if (media.duration === Infinity) {
                probingDuration = true;
                media.currentTime = Number.MAX_SAFE_INTEGER;
            } else {
                c.machine.setPlayer(media);
            }
        }
        function finishDurationProbe({ currentTarget: media }) {
            if (!probingDuration) return;
            probingDuration = false;
            media.currentTime = 0;
            c.machine.setPlayer(media);
        }
        const shortcuts = {
            start: 'Alt+Shift+R', pause: 'Alt+Shift+R', resume: 'Alt+Shift+R',
            play: 'Alt+Shift+Space', stop: 'Alt+Shift+S',
        };
        function onKeydown(event) {
            const doc = root.value?.ownerDocument;
            if (!doc || event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || !event.altKey || !event.shiftKey) return;
            if (event.target !== doc.body && !root.value.contains(event.target)) return;
            const { mode, phase, controls } = m.value;
            if (mode !== 'record' || props.authorized?.() === false) return;
            const review = phase === 'review' && controls.some(b => b.id === 'play' && !b.disabled);
            let action;
            switch (event.key.toLowerCase()) {
                case 'r': action = c[CAPTURE_TOGGLE[phase]]; break;
                case 's': action = phase === 'recording' || phase === 'paused' ? c.stop : review ? c.machine.stopPlayback : null; break;
                case ' ': action = review && c.play; break;
                case 'arrowleft': action = review && (() => c.machine.skip(-REVIEW_SKIP_SECONDS)); break;
                case 'arrowright': action = review && (() => c.machine.skip(REVIEW_SKIP_SECONDS)); break;
            }
            if (!action) return;
            event.preventDefault();
            if (!event.repeat) return action();
        }
        const enabled = (id) => m.value.controls.some((control) => control.id === id && !control.disabled);
        const level = V.ref(0);
        const elapsed = V.computed(() => {
            const seconds = props.recorder.elapsedSeconds || 0;
            return [Math.floor(seconds / 60), seconds % 60].map((part) => String(part).padStart(2, '0')).join(':');
        });
        const previewUrl = V.computed(() => props.recorder.previewUrl);
        V.watchEffect?.((cleanup) => {
            if (m.value.phase !== 'recording') {
                level.value = 0;
                return;
            }
            let frame;
            const sample = () => {
                level.value = props.recorder.readLevel?.() || 0;
                frame = requestAnimationFrame(sample);
            };
            sample();
            cleanup(() => cancelAnimationFrame(frame));
        });
        const space = (event) => {
            if (event.code !== 'Space' || event.repeat || event.defaultPrevented
                || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
                || event.target?.isContentEditable
                || event.target?.closest?.('a, button, input, select, textarea, audio, summary')) return;
            if (m.value.routing || m.value.mode !== 'record') return;
            const action = enabled('start') ? 'start' : enabled('stop') ? 'stop' : null;
            if (!action) return;
            event.preventDefault();
            void c[action]();
        };
        let doc;
        V.onMounted?.(() => {
            unregisterSwitch = registerSwitch?.({ hasDraft: c.hasDraft, prepare: c.prepare });
            doc = root.value?.ownerDocument;
            doc?.addEventListener('keydown', onKeydown);
            globalThis.window?.addEventListener('keydown', space);
        });
        V.onMounted?.(() => { void c.checkRouting(); });
        V.onBeforeUnmount?.(() => {
            unregisterSwitch?.();
            doc?.removeEventListener('keydown', onKeydown);
            globalThis.window?.removeEventListener('keydown', space);
            off();
            return c.dispose();
        });
        return {
            c,
            m,
            root,
            previewHeading, submitOpener,
            reviewSource,
            bindReview,
            loadReview,
            finishDurationProbe,
            shortcuts,
            icons: ICON_PATHS,
            enabled, level, elapsed, previewUrl,
            act: (id) => { if (RECORDER_ACTIONS.includes(id)) c[id](); },
            discard: () => {
                if (window.confirm('Diese Aufnahme verwerfen und neu aufnehmen?')) void c.cancel();
            },
            backToRecorder: () => {
                c.cancelFile();
                c.setMode('record');
            },
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
