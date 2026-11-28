/**
 * Composed clinic recording view, "Neue Aufnahme" (V8 P04, PAR-REC-001, PAR-REC-002).
 *
 * One Vue view over recorder-phases-ui (prepare, record, pause, review, upload)
 * plus the file upload path. The recorder itself is injected (the existing
 * recorder composable) and nothing from recorder-phases-ui is re-implemented.
 *
 * Rules applied:
 *   - The author is the logged-in user. A server check confirms a route before capture.
 *   - Audio and title stay in memory. The recorder reports if a managed
 *     staging browser retained an encrypted copy after a failed upload.
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
 * Injected `uploadFile(file, title, options)` resolves with the server receipt (id or
 * dictation_id); it is supplied by the mount package together with the recorder.
 */

import { createHandmicView } from './handmic-webhid.js';
import { DictationProtectionComponent } from './dictation-protection-ui.js';
import { createRecorderPhases, isTitleEditable, LABELS, receiptDictationId } from './recorder-phases-ui.js';
import { createRecordLevelController, RECORD_LEVEL_TEMPLATE } from './record-level-ui.js';
import { createPedalController } from './pedal-ui.js';
import { CATEGORIES, RequiredCategories } from './categories-ui.js';
import { PRIORITY_OPTIONS } from './dictation-properties-ui.js';
import { surfaceFor } from './tokens.js';
import { CorrectionView } from './compose-correction.js';
import { SessionTransportNotice } from './compose-settings.js';

function microphoneChoices(microphone) {
    return (microphone?.inputs || []).filter(({ deviceId }) => deviceId && deviceId !== 'default')
        .map(({ deviceId, label }, index) => ({ deviceId, label: label || `Mikrofon ${index + 1}` }));
}

export { receiptDictationId };
export const COMPOSE_RECORDING_VERSION = 1;

const TOUCH = 'min-height: 2.75rem; min-block-size: max(2.75rem, 48px)';
const LINK = `${TOUCH}; display: inline-flex; align-items: center; text-decoration: underline`;
const DRAFT_EDIT_ACTION = { title: 'rename', notes: 'rename', priority: 'priority', deadline_date: 'priority' };

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
    localFileHint: 'Nur auf einem freigegebenen Gerät speichern. Die Datei ist nicht verschlüsselt.',
    localSaveStarted: 'Dateispeicherung im Browser gestartet. Prüfen Sie, ob die Datei gespeichert wurde.',
    localSaveFailed: 'Die Dateispeicherung konnte nicht gestartet werden.',
    localOpened: 'Datei geöffnet. Sie können sie anhören und bei Verbindung senden.',
    localOpenFailed: 'Die Datei konnte nicht geöffnet werden.',
    localDenied: 'Dateien auf diesem Gerät zu speichern oder hier zu öffnen ist nicht freigegeben.',
    upload: LABELS.send,
    uploading: LABELS.sending,
    recordingControls: 'Aufnahmesteuerung',
    pairHandmic: 'Handmikrofon koppeln',
    startFailed: 'Prüfen Sie Ihr Mikrofon oder wählen Sie „oder Datei hochladen“.',
    noCaptureFormat: 'Dieser Browser kann hier keine Aufnahme starten. Sie können eine Audiodatei hochladen.',
    microphoneDenied: 'Erlauben Sie den Mikrofonzugriff in den Website-Einstellungen Ihres Browsers und starten Sie die Aufnahme erneut.',
    routingPending: 'Berechtigung wird geprüft …',
    routingMissing: 'Ihr Konto ist noch keinem Autorenpool zugeordnet. Bitte wenden Sie sich an die Verwaltung.',
    routingError: 'Die Berechtigung konnte nicht geprüft werden. Bitte laden Sie die Seite neu.',
    recordFailed: 'Senden fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert.',
    recordRetry: 'Senden fehlgeschlagen. Der Entwurf wurde nicht gesendet. Sie können es erneut versuchen.',
    recordRetained: 'Die Aufnahme liegt verschlüsselt unter „Offline und Wiederherstellung“. Der Server hat den Entwurf nicht bestätigt.',
    fileFailed: 'Hochladen fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Audiodatei wird nicht lokal gespeichert.',
    sent: 'Gespeichert. Noch nicht an den Schreibdienst gesendet.',
    submit: 'Einreichen',
    retrySave: 'Erneut sichern',
    submitting: 'Diktat wird eingereicht …',
    submitted: 'Eingereicht.',
    submitUnknown: 'Einreichung nicht bestätigt. Bitte erneut versuchen.',
    recognition: 'Spracherkennung',
    recognitionOn: 'ein',
    recognitionOff: 'aus',
    categoriesLoading: 'Pflichtkategorien werden geladen …',
    categoriesFailed: 'Pflichtkategorien konnten nicht geladen werden. Bitte versuchen Sie es erneut.',
    categoriesChanged: 'Die Pflichtkategorien wurden geändert. Bitte prüfen Sie Ihre Auswahl.',
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
    vox: 'Stimmsteuerung',
    voxOff: 'Aus: Sprachpausen bleiben erhalten.',
    voxNext: 'Ein ab der nächsten Aufnahme.',
    voxActive: 'Diese Aufnahme wird stimmgesteuert.',
    voxActiveNextOff: 'Diese Aufnahme bleibt stimmgesteuert. Ab der nächsten Aufnahme aus.',
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

const CATEGORY_REFUSALS = ['missing_category', 'invalid_category', 'unknown_category_set', 'stale_version'];

function displayName(user) {
    const name = user.display_name || user.name || user.username || '';
    return typeof name === 'string' && name.trim() ? name.trim() : '';
}

/**
 * Framework-free controller. Subscribers are called after every state change.
 * @param {{client?: object, currentUser: {id: *}, recorder: object, uploadFile: Function,
 *   player?: object, hid?: object, authorized?: () => boolean, onOpen?: Function, onWorklist?: Function}} options
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
    let knownMicrophone = null;
    let attemptedMicrophone = '';

    const machine = createRecorderPhases({
        recorder: opts.recorder, player: opts.player || null, api: client,
        authorized: allowed, automaticTitle: opts.oneButton,
    });
    const listeners = new Set();
    const state = { mode: 'record', file: null, filePhase: 'idle', fileError: null, fileReceipt: null, disposed: false };
    let fileGeneration = 0;
    let useSpeechRecognition = true;
    let clientUploadId = null;
    let submission = null;
    let submitError = '';
    let pendingSend = null;
    let localMessage = '';
    let usedLocalFile = false;
    let pendingMicrophone = null;
    let microphoneSwitch = null;
    let microphoneNotice = '';
    let draft = null;
    let resumedDraftId = opts.draftId || null;
    let pendingMetadata = null;
    let pedal;
    let releasePedalCapture;
    let pedalPlayback = false;
    let categoryLoad = null;
    let categorySets = [];
    let categoryValues = {};
    // A host that calls checkRouting() right after mounting (RecordingView) starts without the pending state.
    let routing = opts.oneButton && !opts.preflightOnMount ? 'routingPending' : null;
    let voxEnabled = false;
    let takeVoxEnabled = false;

    const notifyUi = () => { for (const listener of listeners) listener(); };
    const handmic = createHandmicView(opts.hid, { feedback: handmicFeedback, onCommand: onHandmicCommand, onChange: notifyUi });
    const notify = () => {
        handmic.sync();
        notifyUi();
    };
    const unsubscribeMachine = machine.subscribe(({ phase }) => {
        if (!['title', 'ready', 'recording'].includes(phase)) releasePedalCapture = null;
        if (phase !== 'review' || !machine.isPlaying()) pedalPlayback = false;
        notify();
    });

    const denied = (code) => ({ ok: false, code: code });
    const recorderBusy = () => !['title', 'ready'].includes(machine.getState().phase);

    function canChangeSpeechRecognition() {
        return allowed() && !state.disposed && !routing && !created()
            && ['title', 'ready', 'review', 'idle', 'selected'].includes(phase());
    }

    function toggleSpeechRecognition() {
        if (!canChangeSpeechRecognition()) return denied('invalid_phase');
        useSpeechRecognition = !useSpeechRecognition;
        notify();
        return { ok: true };
    }

    function uploadOptions() {
        // The same draft keeps its identity on retry, even if another draft uses identical audio.
        clientUploadId ||= globalThis.crypto.randomUUID();
        return { useSpeechRecognition, clientUploadId };
    }

    function resetUploadChoice() {
        useSpeechRecognition = true;
        clientUploadId = null;
    }

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
        if (recorderBusy() || state.filePhase === 'sending' || state.filePhase === 'sent') return denied('invalid_phase');
        if (mode !== state.mode) {
            localMessage = '';
            state.mode = mode;
            notify();
        }
        return { ok: true };
    }

    function toggleVox() {
        if (!allowed()) return denied('unauthorized');
        if (state.disposed) return denied('invalid_phase');
        voxEnabled = !voxEnabled;
        notify();
        return { ok: true, enabled: voxEnabled };
    }

    function selectFile(file) {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'file' || !['idle', 'selected', 'failed'].includes(state.filePhase)) return denied('invalid_phase');
        if (state.filePhase === 'failed') resetUploadChoice();
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
            receipt = await opts.uploadFile(state.file, undefined, uploadOptions());
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
        if (opts.draftMetadata) await loadDraft();
        notify();
        return { ok: true, receipt: receipt };
    }

    function cancelFile() {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'file' || state.filePhase === 'sending' || state.filePhase === 'sent') return denied('invalid_phase');
        fileGeneration += 1;
        resetUploadChoice();
        Object.assign(state, { file: null, filePhase: 'idle', fileError: null });
        notify();
        return { ok: true };
    }

    function receipt() {
        return resumedDraftId ? { dictation_id: resumedDraftId }
            : state.mode === 'file' ? state.fileReceipt : machine.getState().receipt;
    }

    function created() {
        const id = receiptDictationId(receipt());
        return id === null ? null : { dictationId: id };
    }

    async function loadDraft() {
        const id = receipt()?.dictation_id;
        if (typeof id !== 'string' || !client?.request || !allowed() || state.disposed || draft?.loading) return;
        const path = '/dictations/' + encodeURIComponent(id);
        draft ||= { path, fields: null, editActions: [], etag: null, saved: false, saving: false, acceptanceRequired: null };
        opts.onDraft?.(id);
        const target = draft;
        target.loading = true;
        submitError = '';
        notify();
        try {
            const [detail, policy] = await Promise.allSettled([
                client.request(path + '/detail'), client.request(path + '/acceptance-policy'),
            ]);
            if (state.disposed || target !== draft) return;
            const current = detail.value?.data;
            if (detail.value?.outcome === 'ok' && current?.dictation_id === id && current.etag) {
                if (current.status_axes?.workflow && current.status_axes.workflow !== 'neu_angelegt') {
                    submission = 'notDraft';
                    submitError = SEND_REFUSALS.NOT_DRAFT;
                    return;
                }
                target.etag = current.etag;
                target.useSpeechRecognition = current.use_speech_recognition;
                target.fields = null;
                target.editActions = Array.isArray(current.draft_edit_actions) ? current.draft_edit_actions : [];
                target.canSend = current.allowed_actions?.includes('send') === true;
                if (current.draft_metadata) {
                    const { title, ...fields } = current.draft_metadata;
                    target.fields = { ...fields, title: title || receipt().title || machine.getState().title || state.file?.name || '' };
                } else {
                    submitError = 'Die gespeicherten Versanddaten sind nicht verfügbar.';
                }
                target.saved = false;
                target.stale = false;
            } else {
                submitError = 'Der aktuelle Entwurf konnte nicht geladen werden.';
            }
            const required = policy.value?.data?.acceptance_required;
            if (policy.value?.outcome === 'ok' && typeof required === 'boolean') target.acceptanceRequired = required;
        } finally {
            target.loading = false;
            if (!state.disposed && target === draft) notify();
        }
    }

    function setDraftField(field, value) {
        if (!editableDraft() || !draft.editActions.includes(DRAFT_EDIT_ACTION[field]) || draft.fields[field] === value) return;
        draft.fields[field] = value;
        draft.saved = false;
        pendingMetadata = null;
        submitError = '';
        notify();
    }

    function editableDraft() {
        return draft?.fields && draft.editActions.length > 0 && !draft.saving && !draft.loading
            && !draft.stale && !submission && !pendingSend && !state.disposed && allowed();
    }

    async function saveDraft() {
        if (!editableDraft() || (draft.editActions.includes('rename') && !draft.fields.title.trim())) return denied('invalid_phase');
        const target = draft;
        const id = receipt().dictation_id;
        target.saving = true;
        target.saved = false;
        submitError = '';
        notify();
        const saveFailed = 'Entwurfsdaten wurden nicht bestätigt. Bitte erneut speichern.';
        try {
            const command = (body) => ({ method: 'POST', body, idempotencyKey: globalThis.crypto.randomUUID() });
            const held = await client.request(target.path + '/leases', command({}));
            if (state.disposed || !allowed()) return denied('cancelled');
            if (held.outcome !== 'ok' || held.data?.dictation_id !== id || !held.data.lease_id) {
                submitError = 'Bearbeitung nicht verfügbar. Der Entwurf bleibt geöffnet.';
                return denied(held.code || 'lease_unavailable');
            }
            const { title, notes, priority, deadline_date } = target.fields;
            const fields = {
                ...(target.editActions.includes('rename') && { title, notes }),
                ...(target.editActions.includes('priority') && { priority, deadline_date: deadline_date || null, deadline_time: null }),
            };
            pendingMetadata ||= { ...command({ fields }), ifMatch: target.etag };
            const res = await client.request(target.path + '/metadata', pendingMetadata);
            if (state.disposed || !allowed()) return denied('cancelled');
            if (res.outcome !== 'ok' || res.data?.dictation_id !== id || !res.data.etag) {
                if (['conflict', 'permission', 'validation'].includes(res.outcome)) pendingMetadata = null;
                target.stale = res.code === 'STALE_VERSION';
                submitError = SEND_REFUSALS[res.code] || (res.code?.startsWith('LEASE_')
                    ? 'Bearbeitung nicht mehr gültig. Der Entwurf bleibt geöffnet.' : (res.message || saveFailed));
                return denied(res.code || 'unconfirmed');
            }
            target.etag = res.data.etag;
            target.saved = true;
            pendingMetadata = null;
            return { ok: true };
        } catch {
            submitError = saveFailed;
            return denied('unconfirmed');
        } finally {
            target.saving = false;
            if (!state.disposed) notify();
        }
    }

    // The numeric recording id on older upload receipts is not a workflow target.
    function canSubmit() {
        const id = receipt()?.dictation_id;
        // A read-only draft already has a durable ETag from detail and has no local changes to save.
        const confirmed = draft?.canSend && draft.fields && !draft.loading && !draft.stale
            && (draft.saved || !draft.editActions.length);
        return typeof id === 'string' && !!id.trim() && typeof client?.request === 'function'
            && allowed() && !state.disposed && !submission && (!opts.draftMetadata || Boolean(confirmed));
    }

    async function loadCategories() {
        if (!client?.request || !allowed() || state.disposed || categoryLoad === 'loading' || pendingSend) return false;
        categoryLoad = 'loading';
        categorySets = [];
        categoryValues = {};
        notify();
        try {
            const result = await client.request(CATEGORIES);
            if (!state.disposed && result.outcome === 'ok' && Array.isArray(result.data?.sets)) {
                categorySets = result.data.sets.filter((set) => set.required);
                categoryLoad = 'ready';
                return true;
            }
        } catch {
            return false;
        } finally {
            if (categoryLoad === 'loading') categoryLoad = 'failed';
            if (!state.disposed) notify();
        }
        return false;
    }

    function selectCategory(setId, value) {
        if (!canSubmit() || pendingSend) return;
        const set = categorySets.find((item) => item.id === setId);
        if (!set || (value !== '' && !set.categories.includes(value))) return;
        categoryValues[setId] = value;
        submitError = '';
        notify();
    }

    async function submit() {
        if (!canSubmit()) return denied('invalid_phase');
        if (categoryLoad === 'loading') return denied('categories_loading');
        if (categoryLoad === 'failed' && !(await loadCategories())) return denied('categories_unavailable');
        if (!canSubmit()) return denied('invalid_phase');
        const missing = categorySets.find((set) => !categoryValues[set.id]);
        if (!pendingSend && missing) {
            submitError = 'Bitte wählen Sie eine Kategorie für „' + missing.name + '“.';
            notify();
            return denied('missing_category');
        }
        const id = receipt().dictation_id;
        const path = '/dictations/' + encodeURIComponent(id);
        submission = 'submitting';
        submitError = '';
        notify();
        try {
            if (!pendingSend) {
                const categories = categorySets.map((set) => ({ set_id: set.id, version: set.version, value: categoryValues[set.id] }));
                const detail = draft ? { outcome: 'ok', data: { dictation_id: id, etag: draft.etag } }
                    : await client.request(path + '/detail');
                if (detail.outcome !== 'ok' || detail.data?.dictation_id !== id || !detail.data.etag) {
                    submitError = detail.message || 'Der aktuelle Diktatstand konnte nicht geladen werden.';
                    return denied('detail_unavailable');
                }
                pendingSend = {
                    body: categories.length ? { categories } : {},
                    idempotencyKey: globalThis.crypto.randomUUID(), ifMatch: detail.data.etag,
                };
            }
            if (state.disposed || !allowed()) return denied('cancelled');
            const res = await client.request(path + '/send', { method: 'POST', ...pendingSend });
            if (state.disposed) return denied('cancelled');
            if (res.outcome === 'ok' && res.data?.dictation_id === id && res.data.status) {
                submission = 'submitted';
                return { ok: true };
            }
            if (CATEGORY_REFUSALS.includes(res.code)) {
                pendingSend = null;
                const loaded = await loadCategories();
                submitError = loaded ? TEXT.categoriesChanged : TEXT.categoriesFailed;
            } else if (['conflict', 'permission', 'validation'].includes(res.outcome)) {
                pendingSend = null;
                submitError = SEND_REFUSALS[res.code] || res.message;
                if (res.code === 'NOT_DRAFT') submission = 'notDraft';
                if (draft && res.code === 'STALE_VERSION') {
                    draft.saved = false;
                    draft.stale = true;
                }
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

    const localReady = () => allowed() && !state.disposed && state.mode === 'record' && !created()
        && opts.recorder.canUseLocalFiles?.() === true;
    const canOpenLocal = () => localReady() && ['title', 'ready', 'review', 'failed'].includes(phase());
    const canSaveLocal = () => localReady() && ['review', 'failed'].includes(phase());
    function localResult(result, text) {
        localMessage = text;
        notify();
        return result;
    }

    function saveLocal() {
        if (!canSaveLocal()) return localResult(denied('local_denied'), TEXT.localDenied);
        try {
            const take = opts.recorder.recordedFile();
            const link = document.createElement('a');
            link.href = opts.recorder.reviewSource();
            link.download = take.name;
            link.click();
            usedLocalFile = true;
            return localResult({ ok: true }, TEXT.localSaveStarted);
        } catch {
            return localResult(denied('local_save_failed'), TEXT.localSaveFailed);
        }
    }

    function openLocal(file) {
        if (!canOpenLocal()) return localResult(denied('local_denied'), TEXT.localDenied);
        if (!isAudioFile(file)) return localResult(denied('invalid_file'), TEXT.fileInvalid);
        try {
            const result = machine.openFile(file);
            if (result.ok) {
                usedLocalFile = true;
                clientUploadId = null;
            }
            return localResult(result, result.ok ? TEXT.localOpened : TEXT.localOpenFailed);
        } catch {
            return localResult(denied('local_open_failed'), TEXT.localOpenFailed);
        }
    }

    function send() {
        return machine.send(opts.oneButton || opts.draftMetadata || !useSpeechRecognition ? uploadOptions() : undefined);
    }

    async function start() {
        if (routing || created() || state.mode !== 'record' || state.disposed || noCaptureFormat()) return denied('invalid_phase');
        localMessage = '';
        usedLocalFile = false;
        if (opts.microphone && machine.controls().some(({ id, disabled }) => id === 'start' && !disabled)) {
            clearMicrophoneChoice();
            microphoneNotice = '';
            attemptedMicrophone = microphoneLabel(selectedMicrophone());
        }
        const forTake = voxEnabled;
        const result = await machine.start({ voxEnabled: forTake });
        if (result.ok) {
            takeVoxEnabled = forTake;
            notify();
        }
        return result;
    }

    function saveRecording() {
        if (state.mode !== 'record' || routing || state.disposed) return denied('invalid_phase');
        localMessage = '';
        return send();
    }

    async function sendRecording() {
        if (!allowed()) return denied('unauthorized');
        if (state.mode !== 'record' || routing || state.disposed) return denied('invalid_phase');
        localMessage = '';
        if (!receipt()) {
            const uploaded = await saveRecording();
            if (!uploaded.ok) return uploaded;
            if (!opts.draftMetadata && (categorySets.length || ['loading', 'failed'].includes(categoryLoad))) return uploaded;
        }
        if (opts.draftMetadata) {
            await loadDraft();
            return { ok: true };
        }
        return submit();
    }

    async function cancelRecording() {
        localMessage = '';
        const result = await machine.cancel();
        if (result.ok) {
            localMessage = '';
            resetUploadChoice();
            notify();
        }
        return result;
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
        if (!created() || draft?.saving || draft?.loading || submission === 'submitting') return denied('invalid_phase');
        submission = null;
        submitError = '';
        pendingSend = null;
        pendingMetadata = null;
        draft = null;
        resumedDraftId = null;
        opts.onDraft?.(null);
        categoryValues = {};
        Object.assign(state, { file: null, filePhase: 'idle', fileError: null, fileReceipt: null });
        resetUploadChoice();
        if (machine.getState().phase === 'sent') machine.reset();
        if (client && typeof client.switchContext === 'function') client.switchContext({});
        if (categoryLoad) void loadCategories();
        notify();
        if (opts.oneButton && state.mode === 'record') void checkRouting();
        return { ok: true };
    }

    function phase() {
        return state.mode === 'file' ? state.filePhase : machine.getState().phase;
    }

    const canUseRecorder = () => !state.disposed && state.mode === 'record' && !routing && allowed()
        && !(opts.draftMetadata && created());
    const enabled = (id) => machine.controls().some((control) => control.id === id && !control.disabled);
    const { pause, resume, play, skip, stopPlayback } = machine;

    function handmicFeedback() {
        if (!canUseRecorder()) return { message: 'Handmikrofon inaktiv.', led: 'off' };
        const p = phase();
        if (p === 'recording') return { message: 'Aufnahme läuft.', led: 'recording' };
        if (p === 'paused') return { message: 'Aufnahme pausiert.', led: 'standby' };
        if (enabled('start')) return { message: 'Bereit zur Aufnahme.', led: 'standby' };
        if (p === 'review' || p === 'failed') return { message: 'Aufnahme beendet.', led: 'off' };
        return { message: 'Handmikrofon inaktiv.', led: 'off' };
    }

    function onHandmicCommand(command, fromSlide) {
        if (!canUseRecorder()) return;
        const p = phase();
        switch (command) {
            case 'record':
                if (enabled('start')) return start();
                if (!fromSlide && enabled('stop')) return stop();
                return fromSlide && p === 'paused' ? resume() : undefined;
            case 'play':
                if (p === 'recording') return pause();
                if (p === 'paused') return resume();
                return enabled('play') ? play() : undefined;
            case 'stop':
                if (enabled('stop')) return stop();
                return enabled('play') ? stopPlayback() : undefined;
            case 'eol': return (p === 'sent' ? canSubmit() : enabled('send')) ? sendRecording() : undefined;
            case 'rewind': return enabled('rewind') ? skip(-REVIEW_SKIP_SECONDS) : undefined;
            case 'forward': return enabled('play') ? skip(REVIEW_SKIP_SECONDS) : undefined;
        }
    }

    function microphoneOptions() {
        const deviceId = opts.microphone?.deviceId;
        const choices = microphoneChoices(opts.microphone);
        const selected = choices.find((device) => device.deviceId === deviceId);
        if (selected) knownMicrophone = selected;
        if (deviceId && deviceId !== 'default' && !selected) {
            choices.push({ deviceId, label: knownMicrophone?.deviceId === deviceId ? knownMicrophone.label : 'Gewähltes Mikrofon' });
        }
        return choices;
    }

    const selectedMicrophone = () => opts.microphone?.deviceId === 'default' ? '' : (opts.microphone?.deviceId || '');
    const microphoneLabel = (id) => microphoneOptions().find(device => device.deviceId === id)?.label || 'Standardmikrofon';
    const canSwitchMicrophone = () => allowed() && !state.disposed && state.mode === 'record'
        && ['recording', 'paused'].includes(phase()) && typeof opts.microphone?.switchInput === 'function';
    const noCaptureFormat = () => opts.microphone?.formats?.length === 0;

    function chooseMicrophone(id) {
        if (!opts.microphone || !allowed() || state.disposed || microphoneSwitch) return;
        if (id && !microphoneOptions().some(device => device.deviceId === id)) return;
        if (canSwitchMicrophone()) {
            pendingMicrophone = id === selectedMicrophone() ? null : { id, label: microphoneLabel(id) };
            microphoneNotice = '';
        } else if (!recorderBusy()) {
            opts.microphone.select(id);
        }
        notify();
    }

    function cancelMicrophone() {
        if (pendingMicrophone === null || microphoneSwitch) return;
        pendingMicrophone = null;
        microphoneNotice = `„${microphoneLabel(selectedMicrophone())}“ bleibt ausgewählt.`;
        notify();
    }

    async function confirmMicrophone() {
        if (!canSwitchMicrophone() || pendingMicrophone === null || microphoneSwitch) return denied('invalid_phase');
        const { id, label } = pendingMicrophone;
        const attempt = Symbol();
        microphoneSwitch = attempt;
        notify();
        try {
            if (!await opts.microphone.switchInput(id) || microphoneSwitch !== attempt || !canSwitchMicrophone()) return denied('stale');
            opts.microphone.select(id);
            microphoneNotice = `Neuer Abschnitt mit „${label}“.`;
            return { ok: true };
        } catch {
            if (microphoneSwitch === attempt) {
                microphoneNotice = `Wechsel fehlgeschlagen. Die Aufnahme bleibt bei „${microphoneLabel(selectedMicrophone())}“.`;
            }
            return denied('microphone_unavailable');
        } finally {
            if (microphoneSwitch === attempt) {
                clearMicrophoneChoice();
                notify();
            }
        }
    }

    function clearMicrophoneChoice() {
        pendingMicrophone = null;
        microphoneSwitch = null;
    }

    function stop() {
        clearMicrophoneChoice();
        return machine.stop();
    }

    function message() {
        if (!allowed()) return TEXT.denied;
        if (submitError) return submitError;
        if (state.mode === 'record' && machine.getState().error === 'playback_failed') return LABELS.playbackFailed;
        if (localMessage) return routing ? `${localMessage} ${TEXT[routing]}` : localMessage;
        if (routing && !draft) return TEXT[routing];
        if (submission) return TEXT[submission];
        if (created() && categoryLoad === 'failed') return TEXT.categoriesFailed;
        if (created() && categoryLoad === 'loading') return TEXT.categoriesLoading;
        const s = machine.getState();
        if (state.mode === 'file') {
            if (state.filePhase === 'sending') return TEXT.uploading;
            if (state.filePhase === 'sent') return TEXT.sent;
            if (state.filePhase === 'failed') return TEXT.fileFailed;
            return state.fileError === 'invalid_file' ? TEXT.fileInvalid : '';
        }
        if (!recorderBusy() && noCaptureFormat()) return TEXT.noCaptureFormat;
        if (canSwitchMicrophone() && microphoneNotice) return microphoneNotice;
        if (s.phase === 'sent' && editableDraftId()) return TEXT.sent;
        if (s.phase === 'failed') {
            if (s.error === 'offline_retained') return TEXT.recordRetained;
            if (usedLocalFile) return TEXT.recordRetry;
            return opts.oneButton || s.title.trim() ? TEXT.recordFailed : LABELS.titleHint;
        }
        if (s.error === 'microphone_unavailable' && attemptedMicrophone) {
            return `„${attemptedMicrophone}“ ist nicht verfügbar. Schliessen Sie es erneut an oder wählen Sie ein anderes Mikrofon.`;
        }
        if (s.error === 'microphone_denied') return TEXT.microphoneDenied;
        if (s.error === 'start_failed' || s.error === 'microphone_unavailable') return TEXT.startFailed;
        if (s.error === 'discard_failed') return TEXT.discardFailed;
        if (s.phase === 'sent') return opts.oneButton && !opts.draftMetadata && !categorySets.length ? '' : TEXT.sent;
        if (opts.oneButton && s.phase === 'sending') return TEXT.submitting;
        return machine.announcement();
    }

    function surface() {
        if (submitError) return surfaceFor('error');
        if (submission === 'submitting') return surfaceFor('processing');
        if (state.mode !== 'file') return machine.surface();
        const status = { idle: 'normal', selected: 'pending', sending: 'processing', sent: 'success', failed: 'error' }[state.filePhase];
        return surfaceFor(status);
    }

    function editableDraftId() {
        if (routing || pendingSend) return null;
        const canEditAudio = opts.draftMetadata
            ? draft?.canSend && !draft.loading && !draft.saving && !submission && !state.disposed && allowed()
            : canSubmit();
        return canEditAudio ? receipt().dictation_id : null;
    }

    /** Plain snapshot for rendering. */
    function model() {
        const s = machine.getState();
        const fileBusy = state.filePhase === 'sending' || state.filePhase === 'sent';
        const separateSubmit = categorySets.length > 0 || ['loading', 'failed'].includes(categoryLoad);
        let recordActionLabel = s.phase === 'review' ? 'Senden' : 'Erneut senden';
        if (separateSubmit) recordActionLabel = created() ? TEXT.submit : s.phase === 'review' ? TEXT.upload : TEXT.retrySave;
        if (opts.draftMetadata) recordActionLabel = s.phase === 'review' ? 'Entwurf sichern' : TEXT.retrySave;
        return {
            mode: state.mode,
            phase: submission || phase(),
            routing,
            canSubmit: canSubmit(),
            canOpenLocal: canOpenLocal(),
            canSaveLocal: canSaveLocal(),
            draft: draft && { ...draft, fields: draft.fields && { ...draft.fields } },
            canEditDraft: Boolean(editableDraft()),
            recordActionLabel,
            categoriesLoading: categoryLoad === 'loading',
            canSelectCategory: canSubmit() && !pendingSend,
            categories: categorySets.map((set) => ({ ...set, value: categoryValues[set.id] || '' })),
            author: displayName(user),
            authorId: String(user.id),
            title: s.title,
            titleLocked: !isTitleEditable(s.phase),
            handmic: handmic.model(),
            authorized: allowed(),
            microphoneOptions: microphoneOptions(),
            selectedMicrophoneId: selectedMicrophone(),
            pendingMicrophone: pendingMicrophone?.id ?? null,
            pendingMicrophoneLabel: pendingMicrophone?.label || '',
            canSwitchMicrophone: canSwitchMicrophone(),
            microphoneSwitching: microphoneSwitch !== null,
            needsMicrophoneAccess: !opts.microphone?.inputs?.some(({ deviceId, label }) => deviceId && deviceId !== 'default' && label),
            voxEnabled,
            voxStatus: ['recording', 'paused', 'review', 'failed'].includes(s.phase) && takeVoxEnabled
                ? (voxEnabled ? TEXT.voxActive : TEXT.voxActiveNextOff)
                : (voxEnabled ? TEXT.voxNext : TEXT.voxOff),
            canToggleVox: allowed() && !state.disposed,
            controls: machine.controls().map(control =>
                control.id === 'start' && noCaptureFormat() ? { ...control, disabled: true } : control),
            file: state.file ? { name: String(state.file.name || ''), size: Number(state.file.size) || 0 } : null,
            canSelectFile: allowed() && !fileBusy,
            canUpload: allowed() && (state.filePhase === 'selected' || state.filePhase === 'failed'),
            uploadLabel: state.filePhase === 'failed' ? 'Erneut hochladen' : TEXT.upload,
            canCancelFile: allowed() && (state.filePhase === 'selected' || state.filePhase === 'failed'),
            canSwitchMode: allowed() && !recorderBusy() && !fileBusy,
            canChangeSpeechRecognition: canChangeSpeechRecognition(),
            useSpeechRecognition: draft?.useSpeechRecognition ?? receipt()?.use_speech_recognition
                ?? (created() && !clientUploadId ? null : useSpeechRecognition),
            created: created(),
            editableDraftId: editableDraftId(),
            canSaveRecording: !opts.draftMetadata && !routing && state.mode === 'record' && !separateSubmit
                && !s.receipt && ['review', 'failed'].includes(s.phase),
            message: message() || (created() && s.phase === 'sent' ? TEXT.sent : ''),
            surface: surface(),
        };
    }

    function attachPedal(target) {
        if (pedal || state.disposed) return;
        const inRecorder = () => !routing && state.mode === 'record' && !state.disposed;
        const enabled = (id) => inRecorder()
            && machine.controls().some((control) => control.id === id && !control.disabled);
        function hold() {
            const action = machine.getState().phase === 'paused' ? 'resume' : 'start';
            if (!enabled(action)) return;
            const release = () => {
                if (releasePedalCapture !== release) return;
                releasePedalCapture = null;
                machine.pauseActiveMedia('recording');
            };
            releasePedalCapture = release;
            if (action === 'resume') controller.resume();
            else void controller.start().then(({ ok }) => {
                // Keyup or blur can arrive while the browser is still asking for microphone access.
                if (ok && (releasePedalCapture !== release || !inRecorder() || !allowed())) {
                    if (releasePedalCapture === release) releasePedalCapture = null;
                    machine.pauseActiveMedia('recording');
                }
                if (!ok && releasePedalCapture === release) releasePedalCapture = null;
            });
            return release;
        }

        pedal = createPedalController({
            ...opts.pedalOptions, target, context: 'recording',
            commands: {
                recordToggle: () => enabled('pause') ? controller.pause() : hold(),
                hold,
                stop: () => enabled('stop') ? controller.stop() : enabled('play') && machine.stopPlayback(),
                playPause: () => {
                    if (!enabled('play')) return;
                    pedalPlayback = !machine.isPlaying();
                    return controller.play();
                },
                rewind: (seconds) => { if (enabled('rewind')) machine.skip(-seconds); },
            },
            onFocusLost: () => {
                releasePedalCapture?.();
                if (pedalPlayback) machine.pauseActiveMedia('review');
                pedalPlayback = false;
            },
        });
        pedal.attach();
    }

    /** Leaves the view: discards an unsent recording and drops late upload results. */
    async function dispose() {
        if (state.disposed) return;
        state.disposed = true;
        clearMicrophoneChoice();
        pedal?.detach();
        fileGeneration += 1;
        unsubscribeMachine();
        listeners.clear();
        const releasingHandmic = handmic.dispose();
        const p = machine.getState().phase;
        const discard = p !== 'sending' && p !== 'sent' ? machine.cancel().catch(() => {
            // The managed offline view owns any encrypted copy.
        }) : undefined;
        await Promise.all([releasingHandmic, discard]);
    }

    const controller = {
        text: TEXT,
        machine,
        handmic,
        model,
        saveRecording,
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        setMode, setTitle: (value) => machine.setTitle(value),
        toggleVox,
        start, chooseMicrophone, cancelMicrophone, confirmMicrophone,
        pause, resume, stop, rewind: machine.rewind, play,
        beginning: machine.beginning, end: machine.end,
        send, cancel: cancelRecording, toggleSpeechRecognition,
        canOpenLocal, saveLocal, openLocal,
        selectFile, sendFile, cancelFile, submit, sendRecording, checkRouting, loadCategories, selectCategory,
        loadDraft, setDraftField, saveDraft, openCreated, toWorklist, another, dispose,
        attachPedal,
    };
    return controller;
}

const RECORDER_ACTIONS = ['start', 'pause', 'resume', 'stop', 'rewind', 'play', 'beginning', 'end', 'send', 'cancel'];
const REVIEW_SKIP_SECONDS = 5;
const CAPTURE_TOGGLE = { title: 'start', ready: 'start', recording: 'pause', paused: 'resume' };

const DRAFT_FIELD = 'display: grid; gap: var(--ui-space-1); margin-bottom: var(--ui-space-3); min-width: 0';

const DraftSendFields = {
    props: ['m', 'c'],
    emits: ['reload'],
    setup: () => ({ priorities: PRIORITY_OPTIONS }),
    template: `
  <div>
    <h2 style="margin: 0 0 var(--ui-space-3); font-size: 1.25rem">Versanddaten</h2>
    <p role="status" v-if="m.draft.loading">Entwurfsdaten werden geladen …</p>
    <button v-else-if="!m.draft.fields" type="button" class="button" style="${TOUCH}" @click="c.loadDraft()">Erneut laden</button>
    <form v-else class="draft-metadata" data-testid="draft-metadata" style="text-align: left" @submit.prevent="c.saveDraft()">
      <fieldset :disabled="!m.canEditDraft" style="min-width: 0">
        <legend class="sr-only">Versanddaten bearbeiten</legend>
        <label style="${DRAFT_FIELD}"><span>Titel</span>
          <input class="!border-ui-text-muted" required maxlength="200" :disabled="!m.draft.editActions.includes('rename')" :value="m.draft.fields.title" @input="c.setDraftField('title', $event.target.value)" />
        </label>
        <label style="${DRAFT_FIELD}"><span>Anmerkung</span>
          <textarea class="!border-ui-text-muted" maxlength="4000" rows="2" :disabled="!m.draft.editActions.includes('rename')" :value="m.draft.fields.notes" @input="c.setDraftField('notes', $event.target.value)"></textarea>
        </label>
        <div class="field-grid">
          <label style="${DRAFT_FIELD}"><span>Priorität</span>
            <select class="!border-ui-text-muted" :disabled="!m.draft.editActions.includes('priority')" :value="m.draft.fields.priority" @change="c.setDraftField('priority', Number($event.target.value))">
              <option v-for="p in priorities" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </label>
          <label style="${DRAFT_FIELD}"><span>Frist</span>
            <input class="!border-ui-text-muted" type="date" :disabled="!m.draft.editActions.includes('priority')" :value="m.draft.fields.deadline_date" @input="c.setDraftField('deadline_date', $event.target.value)" />
            <small>Tagesende (Europe/Zurich)</small>
          </label>
        </div>
        <button v-if="m.draft.editActions.length" type="submit" class="button" style="${TOUCH}"
                :disabled="m.draft.editActions.includes('rename') && !m.draft.fields.title.trim()" data-testid="save-draft">Versanddaten speichern</button>
      </fieldset>
      <p role="status" style="margin: var(--ui-space-2) 0">{{ m.draft.saving ? 'Versanddaten werden gespeichert …' : m.draft.saved ? 'Versanddaten gespeichert.' : '' }}</p>
      <p data-testid="draft-acceptance" style="margin: var(--ui-space-2) 0 var(--ui-space-3)">Abnahme: {{ m.draft.acceptanceRequired === null ? 'Nicht verfügbar' : m.draft.acceptanceRequired ? 'Erforderlich' : 'Nicht erforderlich' }}</p>
    </form>
    <button v-if="m.draft.stale && !m.draft.loading" type="button" class="button" style="${TOUCH}"
            @click="$emit('reload')">Gespeicherten Stand laden</button>
  </div>`,
};

export const RECORDING_TEMPLATE = `
<CorrectionView v-if="editingDraft" :client="client" :current-user="currentUser" :dictation-id="editingDraft"
                open-audio-edits @close="closeAudioEdits">
  <template #back-action="{ close }">
    <button type="button" class="button" style="${TOUCH}" data-testid="close" @click="close()">Zurück zur Aufnahme</button>
  </template>
</CorrectionView>
<section v-else ref="root" class="clinic-recording-view v8-page" data-testid="clinic-recording-view" aria-labelledby="crv-title"
         style="display: grid; place-items: center; min-height: min(56dvh, 30rem); padding: var(--ui-space-6) var(--ui-space-4); color: var(--ui-text)">
  <h1 id="crv-title" class="sr-only">{{ c.text.title }}</h1>
  <div style="width: 100%; max-width: 28rem; text-align: center">
    <component :is="SessionTransportNotice" :client="client" :vue="vue" class="mb-3" />
    <button v-if="m.canChangeSpeechRecognition" type="button" role="switch" data-testid="recognition-switch"
            class="button quiet focus-visible:outline focus-visible:outline-2 focus-visible:outline-ui-text"
            :aria-checked="m.useSpeechRecognition" @click="c.toggleSpeechRecognition()"
            style="${TOUCH}; border-color: var(--ui-border); margin-bottom: var(--ui-space-3)">
      {{ c.text.recognition }} <span aria-hidden="true">{{ m.useSpeechRecognition ? c.text.recognitionOn : c.text.recognitionOff }}</span>
    </button>
    <p v-else-if="m.useSpeechRecognition !== null && (m.created || (!m.routing && ['sending', 'failed'].includes(m.phase)))"
       data-testid="recognition-status" style="margin: 0 0 var(--ui-space-3)">
      {{ c.text.recognition }} {{ m.useSpeechRecognition ? c.text.recognitionOn : c.text.recognitionOff }}
    </p>
    <p v-show="m.message" role="status" aria-live="polite" data-testid="status" style="margin: 0 0 var(--ui-space-3)"
       :style="m.phase === 'submitted' ? { fontSize: '1.25rem', fontWeight: 650 } : null">{{ m.message }}</p>
    <template v-if="!m.routing || m.created || m.canOpenLocal">
      <template v-if="!m.created && m.mode === 'record'">
        <div v-if="!m.routing" class="vox-control" style="display: grid; justify-items: center; gap: var(--ui-space-2); margin: 0 auto var(--ui-space-4); max-width: 100%">
          <button type="button" role="switch" class="button" :class="{ accent: m.voxEnabled }" data-testid="vox-toggle"
                  aria-label="Stimmsteuerung" aria-describedby="vox-status" :aria-checked="m.voxEnabled"
                  :disabled="!m.canToggleVox" @click="c.toggleVox()" style="${TOUCH}; font-size: .875rem">
            {{ c.text.vox }}: {{ m.voxEnabled ? 'Ein' : 'Aus' }}
          </button>
          <small id="vox-status" class="hint" data-testid="vox-status" style="text-align: center">{{ m.voxStatus }}</small>
        </div>
        <div v-if="!m.routing && (m.phase === 'title' || m.phase === 'ready')" style="display: grid; justify-items: center; gap: var(--ui-space-2)">
          <button type="button" class="button accent" data-testid="rec-start" :aria-keyshortcuts="shortcuts.start" :disabled="!enabled('start')" @click="c.start()"
                  style="width: 10rem; height: 10rem; border-radius: 50%; flex-direction: column; gap: var(--ui-space-2); padding-inline: var(--ui-space-2); font-size: 1rem; white-space: nowrap">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="currentColor" aria-hidden="true"><path :d="icons.start"></path></svg>
            Aufnahme starten
          </button>
          <div v-if="microphone" role="group" aria-labelledby="crv-microphone-label"
               style="display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--ui-space-2); width: 100%; max-width: 20rem; text-align: left">
            <span id="crv-microphone-label">Mikrofon</span>
            <button v-for="device in [{ deviceId: '', label: 'Standardmikrofon' }, ...m.microphoneOptions]" :key="device.deviceId"
                    type="button" class="button" :class="{ accent: device.deviceId === m.selectedMicrophoneId }"
                    :aria-pressed="device.deviceId === m.selectedMicrophoneId" :disabled="!enabled('start')"
                    @click="microphone.select(device.deviceId)" style="${TOUCH}; white-space: normal; overflow-wrap: anywhere">
              {{ device.label }}
            </button>
            <button v-if="m.needsMicrophoneAccess" type="button" class="button quiet"
                    :disabled="!enabled('start')" @click="microphone.requestAccess()" style="${TOUCH}">
              Mikrofonzugriff erlauben
            </button>
          </div>
        </div>
        <div v-else-if="m.phase === 'recording' || m.phase === 'paused'" style="display: grid; justify-items: center; gap: var(--ui-space-4)">
          <p role="status" data-testid="capture-status" style="margin: 0">{{ captureCaption[m.phase] }}</p>
          <output aria-label="Aufnahmedauer" aria-live="off" data-testid="elapsed" style="font-size: 2.75rem; font-variant-numeric: tabular-nums">{{ elapsed }}</output>
          ${RECORD_LEVEL_TEMPLATE}
          <div class="actions" style="justify-content: center">
            <button v-if="m.phase === 'recording'" type="button" class="button" style="${TOUCH}" data-testid="rec-pause" :aria-keyshortcuts="shortcuts.pause" @click="c.pause()">Pause</button>
            <button v-else type="button" class="button" style="${TOUCH}" data-testid="rec-resume" :aria-keyshortcuts="shortcuts.resume" @click="c.resume()">Fortsetzen</button>
            <button type="button" class="button" :class="{ accent: m.pendingMicrophone === null }" style="${TOUCH}" data-testid="rec-stop" :aria-keyshortcuts="shortcuts.stop" @click="c.stop()">Stopp</button>
          </div>
        </div>
        <div v-else-if="m.phase === 'review' || m.phase === 'failed'"
             style="display: grid; justify-items: center; gap: var(--ui-space-3)">
          <audio v-if="previewUrl" :key="previewUrl" :ref="bindReview" controls controlslist="nodownload" preload="metadata" :src="previewUrl" aria-label="Aufnahme anhören"
                 @contextmenu.prevent
                 @loadedmetadata="loadReview" @durationchange="c.machine.syncPlayback()" @seeked="finishDurationProbe"
                 @play="c.machine.syncPlayback()" @pause="c.machine.syncPlayback()" @ended="c.machine.syncPlayback()"
                 @error="c.machine.playbackFailed()" style="width: 100%; min-height: 3rem"></audio>
          <div v-if="!m.created && previewUrl" style="display: grid; gap: var(--ui-space-1); width: 100%; text-align: left; font-size: .875rem">
            <span id="crv-output-label">Wiedergabegerät</span>
            <div role="group" aria-labelledby="crv-output-label" data-testid="rec-output" style="display: grid; gap: var(--ui-space-1)">
              <button type="button" class="button" :class="{ accent: outputSupported && outputId === '' }" :aria-pressed="outputId === ''"
                      :disabled="!outputSupported" data-testid="rec-output-default" @click="chooseOutput('')" style="${TOUCH}; justify-content: flex-start">Systemstandard</button>
              <button v-for="(d, i) in outputs" :key="d.deviceId" type="button" class="button" :class="{ accent: outputId === d.deviceId }"
                      :aria-pressed="outputId === d.deviceId" :disabled="!outputSupported" data-testid="rec-output-device" @click="chooseOutput(d.deviceId)"
                      style="${TOUCH}; justify-content: flex-start; min-width: 0; overflow-wrap: anywhere">{{ d.label || ('Ausgabegerät ' + (i + 1)) }}</button>
            </div>
            <p v-if="!outputSupported" data-testid="rec-output-note" style="margin: 0; color: var(--ui-text-muted, var(--ui-text))">Dieser Browser verwendet für die Wiedergabe immer den Systemausgang.</p>
          </div>
          <button type="button" class="button primary" style="${TOUCH}; width: 100%; font-size: 1rem" data-testid="rec-send" :disabled="!!m.routing" @click="c.sendRecording()">
            {{ m.recordActionLabel }}
          </button>
          <button type="button" class="button quiet" style="${TOUCH}; font-size: .875rem"
                  data-testid="rec-cancel" @click="discard">Neu aufnehmen</button>
        </div>
        <div v-if="m.canOpenLocal || m.canSaveLocal" class="actions" style="justify-content: center; margin-top: var(--ui-space-3)">
          <button v-if="m.canSaveLocal" type="button" class="button" style="${TOUCH}" data-testid="rec-save-file"
                  aria-describedby="crv-local-hint" @click="c.saveLocal()">Als Datei speichern</button>
          <button v-if="m.canOpenLocal" type="button" class="button quiet" style="${TOUCH}" data-testid="rec-open-file"
                  @click="chooseLocal">Aufnahmedatei öffnen</button>
          <input v-if="m.canOpenLocal" ref="localPicker" type="file" accept="audio/*,.webm" hidden @change="pickLocal" />
        </div>
        <p v-if="m.canSaveLocal" id="crv-local-hint" style="margin-top: var(--ui-space-2); font-size: .875rem">{{ c.text.localFileHint }}</p>
        <div v-if="microphone && m.canSwitchMicrophone"
             style="display: grid; gap: var(--ui-space-2); width: 100%; max-width: 20rem; margin: var(--ui-space-4) auto 0; text-align: left">
          <div ref="microphonePicker" role="group" aria-labelledby="crv-active-microphone-label"
               style="display: grid; gap: var(--ui-space-2)">
            <span id="crv-active-microphone-label">Mikrofon</span>
            <button v-for="device in [{ deviceId: '', label: 'Standardmikrofon' }, ...m.microphoneOptions]" :key="device.deviceId"
                    type="button" class="button" :class="{ accent: device.deviceId === m.selectedMicrophoneId }"
                    :aria-pressed="device.deviceId === m.selectedMicrophoneId"
                    :disabled="m.microphoneSwitching" @click="c.chooseMicrophone(device.deviceId)"
                    style="${TOUCH}; white-space: normal; overflow-wrap: anywhere">
              {{ device.label }}
            </button>
          </div>
          <div v-if="m.canSwitchMicrophone && m.pendingMicrophone !== null" role="group" aria-labelledby="crv-microphone-prompt"
               @keydown.esc.stop="finishMicrophoneChoice(false)">
            <p id="crv-microphone-prompt" role="status" style="margin: 0 0 var(--ui-space-2)">
              Zu „{{ m.pendingMicrophoneLabel }}“ wechseln? Der bisherige Ton bleibt erhalten.
            </p>
            <div class="actions">
              <button type="button" class="button primary" :disabled="m.microphoneSwitching" @click="finishMicrophoneChoice(true)" style="${TOUCH}">
                {{ m.microphoneSwitching ? 'Mikrofon wird geöffnet …' : 'Mikrofon wechseln' }}
              </button>
              <button type="button" class="button quiet" :disabled="m.microphoneSwitching" @click="finishMicrophoneChoice(false)" style="${TOUCH}">Abbrechen</button>
            </div>
          </div>
        </div>
        <details v-if="(m.phase === 'title' || m.phase === 'ready') && microphone?.formats?.length > 1"
                 data-testid="capture-format" style="width: 100%; max-width: 20rem; margin: var(--ui-space-4) auto 0; text-align: left">
          <summary class="focus-visible:outline-ui-text" style="${TOUCH}; align-content: center; cursor: pointer">
            Aufnahmeformat ({{ microphone.formats.find(format => format.mimeType === microphone.mimeType)?.label || 'Automatisch' }})
          </summary>
          <div class="actions" role="group" aria-label="Aufnahmeformat" style="margin-top: var(--ui-space-2)">
            <button type="button" class="button" :aria-pressed="!microphone.mimeType" :disabled="!enabled('start')"
                    @click="microphone.selectFormat('')" style="${TOUCH}">Automatisch</button>
            <button v-for="format in microphone.formats" :key="format.mimeType" type="button" class="button"
                    :aria-pressed="format.mimeType === microphone.mimeType" :disabled="!enabled('start')"
                    @click="microphone.selectFormat(format.mimeType)" style="${TOUCH}">{{ format.label }}</button>
          </div>
        </details>
        <a v-if="m.phase === 'title' || m.phase === 'ready'" href="#/neu" data-testid="mode-file" @click.prevent="c.setMode('file')"
           style="${LINK}; font-size: .875rem">oder Datei hochladen</a>
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
        <draft-send-fields v-if="m.draft && !['submitted', 'notDraft'].includes(m.phase)" :m="m" :c="c" @reload="reloadDraft" />
        <required-categories v-if="m.categories.length && (m.canSubmit || m.phase === 'submitting')"
                             :items="m.categories" :disabled="!m.canSelectCategory" @select="c.selectCategory" />
        <details v-if="m.canSubmit" class="draft-protection" style="margin-bottom: var(--ui-space-3); text-align: left">
          <summary style="${TOUCH}; cursor: pointer; align-content: center">Diktat-Schutz (optional)</summary>
          <component :is="Protection" ref="protection" :key="m.created.dictationId"
                     :client="client" :dictation-id="m.created.dictationId" />
        </details>
        <span class="sr-only" data-testid="created-id">{{ m.created.dictationId }}</span>
        <fieldset class="actions" :disabled="protecting()" style="justify-content: center; border: 0; padding: 0; margin: 0; min-inline-size: 0">
          <button v-if="!['submitted', 'notDraft'].includes(m.phase)" type="button" class="button primary" style="${TOUCH}"
                  :disabled="!m.canSubmit || m.categoriesLoading" data-testid="submit-draft" @click="c.submit()">{{ c.text.submit }}</button>
          <a href="#/diktat" data-testid="open-created" style="${LINK}" @click.prevent="c.openCreated()">{{ m.mode === 'file' ? c.text.open : 'Diktat öffnen' }}</a>
          <button v-if="m.mode === 'file'" type="button" class="button" style="${TOUCH}" data-testid="to-worklist" @click="c.toWorklist()">{{ c.text.toWorklist }}</button>
          <button type="button" class="button" style="${TOUCH}" data-testid="another" :disabled="m.draft?.saving || m.draft?.loading || m.phase === 'submitting'" @click="c.another()">{{ c.text.title }}</button>
        </fieldset>
      </div>
    </template>
    <div v-if="m.mode === 'record'" style="margin-block-start: var(--ui-space-3); font-size: .875rem">
      <button v-if="!m.created && m.handmic.available" type="button" class="button quiet" style="${TOUCH}" data-testid="handmic-pair"
              :disabled="!m.authorized || !m.handmic.canPair" @click="c.handmic.pair()">{{ c.text.pairHandmic }}</button>
      <p role="status" aria-live="polite" data-testid="handmic-status" style="margin: 0">{{ m.handmic.message }}</p>
    </div>
    <div v-if="m.canSaveRecording" style="margin-top: var(--ui-space-3)">
      <button type="button" class="button" style="${TOUCH}; width: 100%" data-testid="rec-save-draft" @click="c.saveRecording()">
        {{ m.phase === 'review' ? c.text.upload : c.text.retrySave }}
      </button>
    </div>
    <div v-if="m.editableDraftId" class="actions" style="justify-content: center; margin-top: var(--ui-space-3)">
      <button ref="audioEditButton" type="button" class="button" style="${TOUCH}"
              data-testid="edit-created-audio" @click="openAudioEdits">Audio bearbeiten</button>
    </div>
  </div>
</section>`;


export const RecordingView = {
    name: 'ClinicRecordingView',
    components: { CorrectionView, DraftSendFields, RequiredCategories },
    emits: ['open', 'worklist'],
    props: {
        client: { type: Object, default: null },
        currentUser: { type: Object, required: true },
        recorder: { type: Object, required: true },
        uploadFile: { type: Function, required: true },
        player: { type: Object, default: null },
        authorized: { type: Function, default: null },
        vue: { type: Object, default: null },
        microphone: { type: Object, default: null },
    },
    setup(props, ctx) {
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        if (!V) throw new Error('Vue is required to mount the recording view');
        const emit = (name, payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name, payload); };
        const history = globalThis.window?.history;
        const c = createRecordingViewController({
            client: props.client,
            currentUser: props.currentUser,
            recorder: props.recorder,
            uploadFile: props.uploadFile,
            player: props.player,
            authorized: () => props.authorized?.() !== false,
            hid: globalThis.navigator?.hid,
            microphone: props.microphone,
            oneButton: true,
            draftMetadata: true,
            draftId: history?.state?.recordingDraftId,
            onDraft: (id) => history?.replaceState({ ...history.state, recordingDraftId: id }, ''),
            preflightOnMount: true,
            onOpen: (payload) => emit('open', payload),
            onWorklist: () => emit('worklist'),
        });
        const level = V.ref(0);
        const recordLevel = createRecordLevelController({
            level,
            readLevel: () => props.recorder.readLevel?.() ?? 0,
        });
        const tick = V.ref(0);
        const off = c.subscribe(() => {
            tick.value += 1;
            recordLevel.sync(c.machine.getState().phase);
        });
        const m = V.computed(() => (tick.value, c.model()));
        V.watch?.(() => props.authorized?.() !== false, () => c.handmic.sync());
        const root = V.ref(null);
        const localPicker = V.ref(null);
        const microphonePicker = V.ref(null);
        async function finishMicrophoneChoice(confirm) {
            if (confirm) await c.confirmMicrophone();
            else c.cancelMicrophone();
            await V.nextTick();
            microphonePicker.value?.querySelector('[aria-pressed="true"]')?.focus();
        }
        const protection = V.ref(null);
        const Protection = { ...DictationProtectionComponent(V), mounted() { void this.model.load(); } };
        const protecting = () => protection.value?.model.state.status === 'busy';
        const editingDraft = V.ref(null);
        const audioEditButton = V.ref(null);
        function openAudioEdits() {
            editingDraft.value = c.model().editableDraftId;
        }
        function closeAudioEdits() {
            editingDraft.value = null;
            V.nextTick(() => audioEditButton.value?.focus());
        }
        const reviewSource = () => props.player ? null : props.recorder.reviewSource?.();
        let probingDuration = false;
        const outputSupported = V.ref(false);
        const outputs = V.ref([]);
        const outputId = V.ref('');
        let reviewMedia = null;
        let switchingMedia = null;
        async function chooseOutput(deviceId) {
            const media = reviewMedia;
            if (!outputSupported.value || switchingMedia === media) return;
            switchingMedia = media;
            try {
                await media.setSinkId(deviceId);
                if (reviewMedia === media) outputId.value = deviceId;
            } catch {
                if (reviewMedia !== media) return;
                if (media.sinkId && deviceId) {
                    try { await media.setSinkId(''); } catch { /* Keep the actual sink if fallback fails. */ }
                }
                if (reviewMedia === media) outputId.value = media.sinkId || '';
            } finally {
                if (switchingMedia === media) switchingMedia = null;
            }
        }
        async function listOutputs(media) {
            try {
                const all = await globalThis.navigator?.mediaDevices?.enumerateDevices?.();
                if (reviewMedia === media) {
                    outputs.value = (all || []).filter((d) => d.kind === 'audiooutput' && d.deviceId && d.deviceId !== 'default');
                }
            } catch {
                // The system output stays available when the browser cannot list devices.
            }
        }
        function bindReview(media) {
            if (reviewMedia === media) return;
            reviewMedia = media;
            outputs.value = [];
            outputSupported.value = !!media && typeof media.setSinkId === 'function';
            if (outputSupported.value) {
                void listOutputs(media);
                if (outputId.value) void chooseOutput(outputId.value);
            } else if (!media) {
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
            if (!doc || editingDraft.value || event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || !event.altKey || !event.shiftKey) return;
            if (event.target !== doc.body && !root.value.contains(event.target)) return;
            const { mode, phase, controls, created } = m.value;
            if (created || mode !== 'record' || props.authorized?.() === false) return;
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
        const elapsed = V.computed(() => {
            const seconds = props.recorder.elapsedSeconds || 0;
            return [Math.floor(seconds / 60), seconds % 60].map((part) => String(part).padStart(2, '0')).join(':');
        });
        const previewUrl = V.computed(() => (tick.value, props.recorder.previewUrl));
        function recheckRouting() {
            if (m.value.routing) void c.checkRouting();
        }
        const space = (event) => {
            if (event.code !== 'Space' || event.repeat || event.defaultPrevented
                || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
                || event.target?.isContentEditable
                || event.target?.closest?.('a, button, input, select, textarea, audio, summary')) return;
            if (editingDraft.value || m.value.routing || m.value.created || m.value.mode !== 'record') return;
            const action = enabled('start') ? 'start' : enabled('stop') ? 'stop' : null;
            if (!action) return;
            event.preventDefault();
            void c[action]();
        };
        let doc;
        V.onMounted?.(() => {
            doc = root.value?.ownerDocument;
            doc?.addEventListener('keydown', onKeydown);
            if (doc) c.attachPedal(doc);
            globalThis.window?.addEventListener('keydown', space);
            globalThis.window?.addEventListener('online', recheckRouting);
        });
        V.onMounted?.(() => {
            void c.checkRouting();
            void c.loadCategories();
            if (m.value.created) void c.loadDraft();
        });
        V.onBeforeUnmount?.(() => {
            off();
            recordLevel.stop();
            doc?.removeEventListener('keydown', onKeydown);
            globalThis.window?.removeEventListener('keydown', space);
            globalThis.window?.removeEventListener('online', recheckRouting);
            return c.dispose();
        });
        return {
            SessionTransportNotice,
            c,
            m,
            root,
            microphonePicker, finishMicrophoneChoice,
            Protection, protection, protecting,
            editingDraft, audioEditButton, openAudioEdits, closeAudioEdits,
            reviewSource,
            bindReview,
            outputSupported, outputs, outputId, chooseOutput,
            loadReview,
            finishDurationProbe,
            shortcuts,
            captureCaption: PHASE_CAPTIONS,
            icons: ICON_PATHS,
            enabled, level, elapsed, previewUrl, localPicker,
            chooseLocal() {
                if (!c.canOpenLocal()) return;
                if (m.value.canSaveLocal && !window.confirm('Die aktuelle Aufnahme durch eine Datei ersetzen?')) return;
                localPicker.value?.click();
            },
            pickLocal({ target }) {
                const file = target.files?.[0];
                target.value = '';
                if (file) c.openLocal(file);
            },
            reloadDraft: () => {
                if (window.confirm('Ungespeicherte Änderungen verwerfen und den gespeicherten Stand laden?')) void c.loadDraft();
            },
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
