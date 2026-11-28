/**
 * Composed clinic correction view for typists (PAR-COR-001, PAR-COR-002, PAR-COR-005).
 *
 * One Vue view over the eight correction building blocks: correction-editor-ui
 * (text, audio revision, lease, templates), correction-alignment-ui (text/audio
 * words), correction-waveform-ui, playback-tempo-ui, correction-modes-ui,
 * correction-save-ui, audio-markers-ui and playback-gain-ui. Nothing from those
 * modules is re-implemented; the composed view owns only the wiring.
 *
 * Shared wiring (one instance per dictation):
 *   - one editor (w149) with its single lease controller; the save module reads
 *     the lease through an adapter on the editor's authoritative lease state;
 *   - one audio element; its play position feeds alignment, markers, save and
 *     modes, the element itself feeds tempo (w152) and gain (w156).
 * Switching `dictationId` re-keys the inner body, which disposes every child,
 * releases the held lease and clears all clinical data held in memory. Nothing is
 * written to browser storage. Colours come from tokens.js, requests from the
 * injected api-client.js client.
 *
 * Owner decisions applied: W6 default mode "Auf zu hörendes Wort zeigen"
 * (correction-modes-ui), W10 text and audio follow every save (the alignment is
 * reloaded after each new text revision; words typed since carry no audio anchor),
 * W16 no patient data (the view shows the dictation id only).
 *
 * Exports:
 *   CorrectionView  Vue options; props client, dictationId, currentUser, vue,
 *                   recordingId?, createAudioContext?; emits `close`.
 *   registerCorrectionView(app)  app.component('clinic-correction-view', ...).
 *   createCorrectionViewController(options)  the controller behind the view.
 */

import { useCorrectionPlayerPosition } from './correction-player-position.js';
import { formatDictationReference } from './worklist-ui.js';
import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { CorrectionAudioPanel, DictationSummary, createCorrectionToolbar, useCorrectionEditor, vTip, createRepeatTranscription, REPEAT_TEXT } from './correction-editor-ui.js';
import { LABELS as ALIGN_LABELS, createPlaybackHighlighter, useCorrectionAlignment } from './correction-alignment-ui.js';
import { CorrectionWaveform } from './correction-waveform-ui.js';
import { PlaybackTempo, usePlaybackTempo } from './playback-tempo-ui.js';
import { MODES, useCorrectionModes } from './correction-modes-ui.js';
import { useCorrectionSave } from './correction-save-ui.js';
import { AudioMarkers } from './audio-markers-ui.js';
import { PlaybackGain, usePlaybackGain } from './playback-gain-ui.js';
import { FinishOptions } from './finish-options-ui.js';
import { createWorkflowActions } from './workflow-actions-ui.js';

export const COMPOSE_CORRECTION_VERSION = 1;

const CLINIC_OPEN_MODES = Object.freeze(['author_case', 'player', 'correction']);

/**
 * Where a list open event lands.
 * An own author case opened from the worklist goes to the case details.
 * The Aufnahmen row control keeps the editor: that click is not the worklist double-click.
 * A permitted typist reaches the player for audio still to write, and the correction editor
 * for a recognised case or a document. A denied row stays closed (null).
 */
export function resolveClinicOpenTarget(payload, context) {
    const ctx = context || {};
    if (!payload || typeof payload !== 'object') return null;
    if (payload.denied === true || payload.permitted === false) return null;
    const mode = payload.mode;
    if (!CLINIC_OPEN_MODES.includes(mode)) return null;
    if (payload.dictationId === undefined || payload.dictationId === null || payload.dictationId === '') return null;
    const id = String(payload.dictationId);
    const user = ctx.user || {};
    const role = String(user.role || ctx.role || '').toLowerCase();
    const authorId = payload.author_user_id != null ? payload.author_user_id : payload.authorUserId;
    const hasAuthor = authorId != null && user.id != null && String(user.id) !== '';
    const isAuthor = hasAuthor && String(authorId) === String(user.id);
    if (mode === 'author_case' && !isAuthor && (hasAuthor || role === 'typist' || role === 'staff')) return null;
    if (mode === 'author_case' && ctx.view !== 'clinic-recordings') {
        return { surface: 'details', dictationId: id, openMode: null };
    }
    return { surface: 'correction', dictationId: id, openMode: typistOpenMode(payload, mode) };
}

function typistOpenMode(payload, mode) {
    if (mode === 'author_case') return 'correction';
    const status = String(payload.status || '').toLowerCase();
    const kind = String(payload.source_kind || payload.sourceKind || '').toLowerCase();
    if (kind === 'document') return 'correction';
    if (kind === 'recognized' || status === 'zu_korrigieren') return 'correction';
    if (kind === 'audio' || status === 'zu_schreiben' || mode === 'player') return 'player';
    return 'correction';
}

/** The upstream player (modules/composables/audioPlayer.js) defines no skip step, so the view uses 3 s. */
export const STEP_SECONDS = 3;

/** V8 editor contract (UI8-004): skip and rewind step in seconds. Used when the controller runs with contract 'v8'. */
export const STEP_SECONDS_V8 = 5;
/** Extra save passes close() may run after the first one while edits are still dirty. */
const CLOSE_EXTRA_SAVES = 2;

export const TEXT = Object.freeze({
    title: 'Korrektur',
    page: 'Schreibplatz',
    playerPage: 'Wiedergabe',
    playerSubline: 'Aufnahme anhören. Der Text bleibt sichtbar und wird hier nicht umgeschrieben.',
    subline: 'Diktat prüfen, Text korrigieren und mit der Aufnahme abgleichen.',
    eyebrow: 'Arbeitsliste',
    toWorklist: 'Zur Arbeitsliste',
    workspace: 'Editorarbeitsbereich',
    dictatedBy: 'Diktiert von',
    pool: 'Schreibkräfte-Pool',
    editing: 'Bearbeitung',
    workerHeld: 'Von Ihnen übernommen',
    workerForeign: 'Von anderer Person übernommen',
    workerLost: 'Übernahme verloren',
    workerFree: 'Noch nicht übernommen',
    templateMenu: 'Textbaustein einfügen',
    openAssignment: 'Zuordnung offen',
    noPool: 'Kein Pool zugewiesen',
    options: 'Wiedergabeoptionen',
    audioUnsynced: 'Text und Audio sind nicht synchronisiert.',
    highlightOn: 'Wortmarkierung ausschalten',
    highlightOff: 'Wortmarkierung einschalten',
    follow: 'Aktuelles Wort mitverfolgen',
    undo: 'Rückgängig',
    redo: 'Wiederholen',
    paragraph: 'Absatz einfügen',
    marker: 'Prüfmarke einfügen',
    markerTip: 'Prüfmarke einfügen (noch nicht angebunden)',
    ctxAssignment: 'Zuständigkeit und Übernahme',
    ctxMarks: 'Prüfmarken und Hinweise',
    ctxOptions: 'Diktatdetails und Wiedergabeoptionen',
    closeContext: 'Kontext schliessen',
    ctxClosed: 'Kontextbereich',
    find: 'Im Text suchen',
    replace: 'Suchen und Ersetzen',
    fontDown: 'Text kleiner anzeigen',
    fontUp: 'Text grösser anzeigen',
    focusOn: 'Vollbild öffnen',
    focusOff: 'Vollbild schliessen',
    shortcuts: 'Tastaturhilfe',
    shortcutsClose: 'Tastaturhilfe schliessen',
    query: 'Suchen nach',
    replacement: 'Ersetzen durch',
    caseSensitive: 'Gross-/Kleinschreibung beachten',
    prev: 'Vorheriger Treffer',
    next: 'Nächster Treffer',
    replaceOne: 'Ersetzen',
    replaceAll: 'Alle ersetzen',
    closeSearch: 'Suche schliessen',
    noMatches: 'Keine Treffer',
    saveDirty: 'Speichern (nicht gespeicherte Änderungen)',
    dictation: 'Diktat',
    close: 'Schliessen',
    closeFailed: 'Schliessen nicht möglich: Die Änderungen konnten nicht gespeichert werden.',
    toolbar: 'Werkzeugleiste',
    template: 'Vorlage',
    noTemplate: 'Keine Vorlage gewählt',
    seekSelection: 'Zur Auswahl im Audio springen',
    reload: 'Aktuellen Stand laden',
    conflictChoice: 'Eigenen Text und Serverstand vergleichen und bewusst wählen.',
    keepMine: 'Eigenen Text behalten',
    takeServer: 'Serverstand übernehmen',
    compare: 'Vergleichen',
    mine: 'Eigener Text',
    server: 'Serverstand',
    retry: 'Erneut versuchen',
    text: 'Transkript',
    audio: 'Audio zum Diktat',
    alignment: 'Text und Audio',
    alignmentText: 'Transkript mit Audio-Zuordnung',
    wordAria: 'Wort abspielen',
    unalignedMark: 'ohne Audio',
    markers: 'Marken im Audio',
    reviewTitle: 'Zu prüfende Wörter',
    reviewHint: 'Pfeiltasten wechseln den Treffer. Eingabe bestätigt, Esc wählt die Textstelle aus.',
    reviewConfirm: 'Bestätigen',
    reviewConfirmed: 'Bestätigt',
    reviewOpen: 'Offen',
    reviewNone: 'Keine offenen Treffer',
    reviewFailed: 'Bestätigung nicht möglich. Bitte erneut versuchen.',
    reviewLoadFailed: 'Treffer konnten nicht geladen werden.',
    reviewDraft: 'Geänderte Stelle zuerst speichern.',
    lease: 'Bearbeitungsstatus',
    save: 'Speichern',
    saveSection: 'Speicherstand',
    resume: 'Zur gespeicherten Position springen',
    mode: 'Korrekturmodus',
    modeGroup: 'Wiedergabe beim Korrigieren',
    modeRetry: 'Erneut speichern',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
    readOnlyAccess: 'Sie haben für dieses Diktat Leserechte.',
    unsaved: 'Nicht gespeicherte Änderungen sind vorhanden.',
    manual: 'Keine automatische Erkennung vorhanden. Bitte manuell transkribieren.',
    loading: 'Wird geladen …',
    keys: 'Tastatur: Strg + S speichert. Im Transkript hält die Wiedergabe beim Tippen an, wenn der Modus «Auf zu hörendes Wort zeigen» gewählt ist. Das Audio wird mit den Tasten des Players bedient.',
});

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

const PLAIN_VUE = Object.freeze({
    ref: plainRef,
    shallowRef: plainRef,
    computed: (fn) => ({ get value() { return fn(); } }),
});

const isNode = (n) => n !== null && typeof n === 'object';

/**
 * Client handed to every child. The editor clears the client context when it is
 * disposed, which would abort the lease release that is still in flight; only a
 * switch to a real dictation is forwarded, so the next dictation's context
 * replaces this one and a plain unmount lets the release finish.
 */
export function scopedClient(client) {
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const shared = releaseState(client);
    return {
        request: (path, options) => {
            if (LEASE_RELEASE.test(String(path))) return trackRelease(shared, client.request(path, options));
            return shared.gate ? shared.gate.then(() => client.request(path, options)) : client.request(path, options);
        },
        command: (body, options) => client.command(body, options),
        switchContext(next) {
            if (!next || next.dictationId === null || next.dictationId === undefined) return;
            if (shared.pending.size === 0) {
                client.switchContext(next);
                return;
            }
            // A lease release of the previous dictation is still in flight. A context switch would abort it
            // and the server would keep the lease until it expires, so the switch waits for the release and
            // requests of the new dictation queue behind it.
            shared.next = next;
            shared.gate = Promise.allSettled([...shared.pending]).then(() => {
                const queued = shared.next;
                shared.next = null;
                shared.gate = null;
                if (queued) client.switchContext(queued);
            });
        },
    };
}

const LEASE_RELEASE = /\/leases\/release$/;
const releaseStates = new WeakMap();

function releaseState(client) {
    let shared = releaseStates.get(client);
    if (!shared) {
        shared = { pending: new Set(), gate: null, next: null };
        releaseStates.set(client, shared);
    }
    return shared;
}

function trackRelease(shared, promise) {
    const tracked = Promise.resolve(promise);
    shared.pending.add(tracked);
    const done = () => { shared.pending.delete(tracked); };
    tracked.then(done, done);
    return tracked;
}

/**
 * Lease access for the save module. The editor (w149) owns the only lease
 * controller; this adapter exposes its authoritative snapshot. A controller the
 * editor may expose later (`editor.leaseController`) is used as is.
 */
export function createLeaseAdapter(editor, vue) {
    if (editor.leaseController) return editor.leaseController;
    return {
        getState: () => editor.lease.value,
        subscribe(fn) {
            if (vue && typeof vue.watch === 'function') return vue.watch(editor.lease, (snap) => fn(snap));
            return () => {};
        },
        setUnsaved() { return false; },
        notifyGeneration() {},
    };
}

/**
 * Controller behind CorrectionView. `vue` supplies ref, shallowRef, computed and
 * optionally watch; without it plain (non-reactive) holders are used, which is
 * what the unit tests rely on.
 */
export function createCorrectionViewController(options) {
    const opts = options || {};
    const scoped = scopedClient(opts.client || createClinicApiClient());
    const client = scoped;
    const user = opts.currentUser || {};
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    if (user.id === undefined || user.id === null) throw new Error('currentUser.id is required');
    const dictationId = String(opts.dictationId);
    const V = opts.vue || globalThis.Vue || null;
    const api = V && V.ref && V.shallowRef && V.computed ? V : { ...PLAIN_VUE, ...(V || {}) };
    const ref = api.ref || plainRef;
    const shallowRef = api.shallowRef || ref;

    const ready = ref(false);
    const loadGeneration = ref(0); // bumps on every successful (re)load: remounts waveform and markers
    const revisionGeneration = ref(0); // bumps when a save produced a new text revision
    const audioEl = shallowRef(null);
    const gainState = shallowRef(null);
    const closeMessage = ref('');
    let gain = null;
    let gainContext = null;
    let highlighter = null;
    let alignmentRoot = null;
    let knownRevision;
    let disposed = false;
    const stops = [];
    const audioListeners = [];
    let tempo = null;
    let pedal = null;
    let pedalReady = Promise.resolve(null);
    const pedalStatus = ref('');

    // contract 'v8': shared lease controller, reconcile before a new key, +-5 s step. Without it the pre-V8 behaviour stays.
    const v8 = opts.contract === 'v8';
    const editor = useCorrectionEditor({
        client, dictationId, currentUser: user, vue: V || undefined, exposeLeaseController: v8,
    });

    const position = () => {
        const el = audioEl.value;
        return el && Number.isFinite(el.currentTime) ? el.currentTime : 0;
    };
    const getPositionMs = () => (audioEl.value ? Math.round(position() * 1000) : null);
    const seek = (seconds) => { if (audioEl.value) audioEl.value.currentTime = seconds; };

    const alignment = useCorrectionAlignment({ editor, client, dictationId, seek, vue: V || undefined });

    // Adapter for the correction modes (w153); inert until the element is bound.
    const modeAudio = {
        play() { const el = audioEl.value; if (el && typeof el.play === 'function') Promise.resolve(el.play()).catch(() => {}); },
        pause() { const el = audioEl.value; if (el && typeof el.pause === 'function') el.pause(); },
        seek,
        position,
        isPlaying() { const el = audioEl.value; return Boolean(el) && el.paused === false; },
    };
    const modes = useCorrectionModes({
        alignment, audio: modeAudio, client, vue: V || undefined,
        ...(opts.modesOptions || {}),
    });

    let manuallyPaused = false;
    function togglePlayback() {
        const wasWaiting = modes.waiting.value;
        modes.cancelResume();
        if (modeAudio.isPlaying()) {
            manuallyPaused = true;
            modeAudio.pause();
        } else {
            const backstep = manuallyPaused || wasWaiting;
            manuallyPaused = false;
            if (backstep) modes.resumeWithBackstep();
            else modeAudio.play();
        }
    }

    function pedalPlayback(command) {
        manuallyPaused = false;
        modes.cancelResume();
        command();
    }

    const saver = useCorrectionSave({
        editor, client, dictationId, getPositionMs, vue: V || undefined,
        lease: createLeaseAdapter(editor, V),
        ...(opts.autosaveMs !== undefined ? { autosaveMs: opts.autosaveMs } : {}),
        ...(v8 ? { reconcileFirst: true } : {}),
        ...(opts.saveOptions || {}),
    });

    const repeat = createRepeatTranscription({ editor, ref });
    const resumeLabel = (api.computed)(() => {
        const ms = saver.resumePosition.value;
        if (ms === null) return 'Keine gespeicherte Position.';
        const total = Math.floor(ms / 1000);
        const pad = (n) => String(n).padStart(2, '0');
        return 'Letzte gespeicherte Position: ' + pad(Math.floor(total / 60)) + ':' + pad(total % 60);
    });

    // --- waveform peaks (V8 P06) ---------------------------------------------
    // The waveform module asks for a URL by recording id; the view answers it from the peaks route of the
    // audio revision the editor currently shows. No revision, a 404, or a 503 DEPENDENCY_UNAVAILABLE (no durable
    // audio source) reads as "no peaks" (honest fallback text); every other failure stays a load failure.
    const peaksKey = () => editor.audio.value.revisionId || null;
    async function peaksFetch(_url, init) {
        const revision = peaksKey();
        if (!revision) return { status: 404 };
        const url = '/api/clinic/v1/dictations/' + encodeURIComponent(dictationId)
            + '/audio-revisions/' + encodeURIComponent(revision) + '/peaks';
        const res = await (opts.peaksFetch || ((u, i) => fetch(u, i)))(url, init);
        if (res && res.status === 503 && typeof res.clone === 'function') {
            const body = await res.clone().json().catch(() => null);
            if (body && body.code === 'DEPENDENCY_UNAVAILABLE') return { status: 404 };
        }
        if (res && res.status === 500 && typeof res.clone === 'function') {
            const body = await res.clone().json().catch(() => null);
            if (body && body.code === 'PEAKS_FAILED') return { status: 500, peaksFailed: true };
        }
        return res;
    }

    // --- audio wiring -------------------------------------------------------
    function unbindAudio() {
        manuallyPaused = false;
        modes.cancelResume();
        for (const [node, name, fn] of audioListeners.splice(0)) {
            if (typeof node.removeEventListener === 'function') node.removeEventListener(name, fn);
        }
        if (highlighter) { highlighter.dispose(); highlighter = null; }
        if (tempo) { tempo.dispose(); tempo = null; }
        if (gain) { gain.dispose(); gain = null; }
        if (gainContext && typeof gainContext.close === 'function') Promise.resolve(gainContext.close()).catch(() => {});
        gainContext = null;
        gainState.value = null;
    }

    /** Called with the audio element when it is mounted and with null when it is removed. */
    function bindAudio(node) {
        const el = isNode(node) ? node : null;
        if (disposed || el === audioEl.value) return;
        unbindAudio();
        audioEl.value = el;
        if (!el) return;
        const listen = (name, fn) => {
            if (typeof el.addEventListener !== 'function') return;
            el.addEventListener(name, fn);
            audioListeners.push([el, name, fn]);
        };
        // The page's AudioContext factory is injected; without one (jsdom, old browser) gain reports "nicht verfügbar".
        const Ctor = globalThis.AudioContext || globalThis.webkitAudioContext;
        const factory = opts.createAudioContext || (typeof Ctor === 'function' ? () => new Ctor() : null);
        gain = usePlaybackGain({
            audio: el,
            createAudioContext: factory ? () => { gainContext = factory(); return gainContext; } : undefined,
        }, { shallowRef: api.shallowRef || shallowRef });
        gainState.value = gain.state;
        listen('play', () => {
            if (gainContext && typeof gainContext.resume === 'function') Promise.resolve(gainContext.resume()).catch(() => {});
            if (gain) gain.start();
        });
        listen('pause', () => { if (gain) gain.stop(); });
        listen('ended', () => {
            manuallyPaused = false;
            modes.cancelResume();
            if (gain) gain.stop();
        });
        // Same tempo controller the player buttons use (seek, rewind); the pedal calls it too.
        tempo = usePlaybackTempo({ audio: el, onSync });
        // Highlight follows the real media position (rAF while playing, events otherwise); it never touches focus or editor state.
        highlighter = createPlaybackHighlighter({ alignment, audio: el, root: alignmentRoot, follow: false });
    }

    /** Element holding the aligned words; the highlighter marks [data-link] children and follows them. */
    function bindAlignmentRoot(node) {
        alignmentRoot = isNode(node) ? node : null;
        if (highlighter) highlighter.setRoot(alignmentRoot);
    }

    /** Honest notice for the current binding: "nicht ausgerichtet" or the coarse segment highlight. */
    const alignmentNotice = api.computed(() => {
        alignment.status.value; alignment.currentWord.value; // dependencies
        return highlighter ? highlighter.notice() : (alignment.status.value === 'stale' || alignment.status.value === 'error' ? ALIGN_LABELS.notAligned : '');
    });

    /** Tempo sync (seek, rate change): the current word follows the player. */
    function onSync(event) {
        if (!disposed && event && Number.isFinite(event.positionMs)) alignment.highlightAt(event.positionMs / 1000);
    }

    // --- load and revision follow-up (W10) -----------------------------------
    async function load(loadOptions) {
        if (disposed) return { ok: false, code: 'cancelled', message: '' };
        closeMessage.value = '';
        const res = await editor.load(loadOptions);
        if (disposed || (res && res.code === 'cancelled')) return res;
        if (res && res.ok) {
            knownRevision = editor.textRevisionId.value;
            ready.value = true;
            loadGeneration.value += 1;
            await Promise.all([alignment.refresh(), saver.loadResume(), modes.load(), loadReview()]);
        }
        return res;
    }

    /** After a save produced a new text revision, text and audio are linked again by the server; adopt the new links. */
    function syncRevision() {
        if (disposed || !ready.value) return null;
        const revision = editor.textRevisionId.value;
        if (revision === knownRevision) return null;
        knownRevision = revision;
        revisionGeneration.value += 1;
        return Promise.all([alignment.refresh(), loadReview()]);
    }

    if (api.watch) stops.push(api.watch(editor.textRevisionId, () => { syncRevision(); }));

    async function save() {
        const res = await saver.save();
        if (res && res.ok) await syncRevision();
        return res;
    }

    function adoptAudioRevision(id) {
        if (disposed || !id || editor.audio.value.revisionId === id) return;
        editor.audio.value = { status: 'ready', revisionId: id, etag: '"' + id + '"', src: editor.audioSource(id), message: '' };
        alignment.refresh();
    }

    /** Conflict choice: keep the local text on the newest server revision. */
    function keepMine() {
        return saver.keepMine();
    }

    /** Conflict choice: adopt the server text; the new revision's alignment is loaded again. */
    async function takeServer() {
        const res = await saver.takeServer();
        if (res && res.ok) {
            knownRevision = editor.textRevisionId.value;
            loadGeneration.value += 1;
            await Promise.all([alignment.refresh(), loadReview()]);
        }
        return res;
    }

    function setText(next) {
        const accepted = editor.setText(next);
        if (accepted) saver.touch();
        return accepted;
    }

    async function reloadLatest() {
        const res = await saver.reloadLatest();
        if (res && res.ok) {
            knownRevision = editor.textRevisionId.value;
            loadGeneration.value += 1;
            await Promise.all([alignment.refresh(), loadReview()]);
        }
        return res;
    }

    // --- text area handlers --------------------------------------------------
    const MODIFIER_KEYS = ['Shift', 'Control', 'Alt', 'Meta', 'AltGraph', 'CapsLock'];

    /** Ctrl/Cmd + S anywhere in the view saves. */
    function onShortcut(event) {
        if (!event || !(event.ctrlKey || event.metaKey) || String(event.key).toLowerCase() !== 's') return false;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        save();
        return true;
    }

    /** A typing key in the text area. Shortcuts and bare modifiers do not count as typing. */
    function onKeydown(event) {
        if (!event || event.ctrlKey || event.metaKey || MODIFIER_KEYS.includes(event.key)) return;
        modes.onKey();
    }

    function onInput(event) {
        if (event && event.target) setText(event.target.value);
    }

    function onSelect(event) {
        const t = event && event.target;
        if (t && Number.isInteger(t.selectionStart) && Number.isInteger(t.selectionEnd)) {
            modes.onSelectionChange(t.selectionStart, t.selectionEnd);
        }
    }

    function onTemplate(event) {
        if (event && event.target && event.target.value) editor.applyTemplate(event.target.value);
    }

    function seekToResume() {
        const ms = saver.resumePosition.value;
        if (ms === null) return false;
        seek(ms / 1000);
        return true;
    }

    // --- review markers at the affected span (PAR-VOC-004) ---------------------------
    // Server offsets and confirmations belong to the saved text revision (UTF-16 units).
    const reviewHits = ref([]);
    const reviewActive = ref(0);
    const reviewMessage = ref('');
    let reviewBase = '';
    const reviewRevision = ref(null);
    const reviewLoaded = ref(false);
    let reviewSeq = 0;
    const reviewPath = () => '/dictations/' + encodeURIComponent(dictationId) + '/correction/review-markers';
    const reviewKeys = new Map();
    const newReviewKey = () => (globalThis.crypto && globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID() : 'rk-' + Date.now() + '-' + Math.random().toString(16).slice(2));

    /** Changed range in the saved and draft texts, excluding their common prefix and suffix. */
    function reviewEdit(current) {
        if (current === reviewBase) return null;
        let p = 0;
        const max = Math.min(current.length, reviewBase.length);
        while (p < max && current[p] === reviewBase[p]) p += 1;
        let s = 0;
        while (s < max - p && current[current.length - 1 - s] === reviewBase[reviewBase.length - 1 - s]) s += 1;
        return { start: p, end: reviewBase.length - s, draftEnd: current.length - s };
    }

    /** Hits with their state: 'confirmed' only while the confirmed span is untouched in the editor text. */
    const reviewList = api.computed(() => {
        if (reviewRevision.value !== editor.textRevisionId.value) return [];
        const text = editor.text.value;
        const edit = reviewEdit(text);
        // A word also changes when an adjacent edit joins it to another word or extends it.
        const joinedBefore = edit && text.slice(0, edit.draftEnd).match(/[\p{L}\p{N}_]+$/u)?.[0];
        const joinedAfter = edit && text.slice(edit.start).match(/^[\p{L}\p{N}_]+/u)?.[0];
        return reviewHits.value.map((h) => {
            const savedEnd = h.offset + h.label.length;
            const shift = edit ? edit.draftEnd - edit.end : 0;
            const start = !edit || h.offset < edit.start ? h.offset : h.offset >= edit.end ? h.offset + shift : edit.start;
            const end = !edit || savedEnd <= edit.start ? savedEnd : savedEnd >= edit.end ? savedEnd + shift : edit.draftEnd;
            const before = edit && h.offset === edit.end && joinedBefore ? joinedBefore.length : 0;
            const after = edit && savedEnd === edit.start && joinedAfter ? joinedAfter.length : 0;
            const touched = Boolean(before || after || (edit && h.offset < edit.end && savedEnd > edit.start));
            return {
                ...h, start: start - before, end: end + after, touched, state: h.confirmed && !touched ? 'confirmed' : 'open',
                context: reviewBase.slice(Math.max(0, h.offset - 20), savedEnd + 20).replace(/\s+/g, ' '),
            };
        });
    });
    const reviewOutstanding = api.computed(() => reviewList.value.filter((h) => h.state === 'open').length);

    async function loadReview() {
        if (disposed) return { ok: false, code: 'cancelled', message: '' };
        const mine = ++reviewSeq;
        const revision = editor.textRevisionId.value;
        const text = editor.text.value;
        if (reviewRevision.value !== revision) reviewLoaded.value = false;
        const [res, base] = await Promise.all([
            client.request(reviewPath()),
            editor.dirty.value ? client.request('/dictations/' + encodeURIComponent(dictationId) + '/correction') : null,
        ]);
        if (mine !== reviewSeq || disposed || revision !== editor.textRevisionId.value || res?.outcome === 'cancelled') {
            return { ok: false, code: 'cancelled', message: '' };
        }
        const data = res?.data;
        if (res?.outcome !== 'ok' || data?.text_revision_id !== revision || !Array.isArray(data?.review_markers)
            || (base && (base.outcome !== 'ok' || base.data?.text_revision_id !== revision || typeof base.data.text !== 'string'))) {
            reviewMessage.value = TEXT.reviewLoadFailed;
            return { ok: false, code: 'failed', message: TEXT.reviewLoadFailed };
        }
        reviewBase = base ? base.data.text : text;
        reviewRevision.value = revision;
        const list = data.review_markers;
        reviewHits.value = list.filter((h) => h && typeof h.id === 'string' && typeof h.label === 'string' && Number.isInteger(h.anchor_offset))
            .map((h) => ({ id: h.id, label: h.label, offset: h.anchor_offset, confirmed: false }));
        reviewActive.value = 0;
        reviewMessage.value = '';
        reviewLoaded.value = true;
        return { ok: true };
    }

    function moveReview(step) {
        const n = reviewHits.value.length;
        if (!n) return null;
        reviewActive.value = (reviewActive.value + step + n) % n;
        return reviewActive.value;
    }

    /** Confirm one open hit (server: lease generation and expected text revision). */
    async function confirmReview(id) {
        const hit = reviewList.value.find((h) => h.id === id);
        const snap = editor.lease && editor.lease.value;
        const generation = snap && Number.isInteger(snap.generation) ? snap.generation : null;
        if (disposed || !hit || hit.touched || hit.state !== 'open') return { ok: false, code: 'noop', message: '' };
        const revision = reviewRevision.value;
        if (!editor.canEdit.value || generation === null || !revision) return { ok: false, code: 'readOnly', message: TEXT.readOnly };
        const body = { expected_text_revision_id: revision, lease_generation: generation };
        const digest = id + JSON.stringify(body);
        if (!reviewKeys.has(digest)) reviewKeys.set(digest, newReviewKey());
        const res = await client.request(reviewPath() + '/' + encodeURIComponent(id) + '/confirm',
            { method: 'POST', idempotencyKey: reviewKeys.get(digest), body });
        if (disposed || revision !== editor.textRevisionId.value || res?.outcome === 'cancelled') {
            return { ok: false, code: 'cancelled', message: '' };
        }
        if (res && res.outcome === 'ok') {
            reviewKeys.delete(digest);
            reviewHits.value = reviewHits.value.map((h) => (h.id === id ? { ...h, confirmed: true } : h));
            reviewMessage.value = '';
            return { ok: true };
        }
        reviewMessage.value = TEXT.reviewFailed;
        return { ok: false, code: 'failed', message: TEXT.reviewFailed };
    }


    // --- lifecycle ------------------------------------------------------------
    const leaseRun = { acquire: editor.acquire, release: editor.release, takeOver: editor.takeOver };
    const leaseButtons = () => editor.availableLeaseActions().filter((a) => typeof leaseRun[a.id] === 'function');

    // --- foot pedal, keyboard mode (PAR-OPS-001) -----------------------------
    // Pedal commands bypass correction backstep: hold release already applies its own rewind.
    // No playback action mutates data; `mutating` stays empty and canMutate
    // follows the lease so a future mutating command cannot bypass it. The module is loaded on demand.
    function startPedal() {
        if (opts.pedal === false) return Promise.resolve(null);
        return import('./pedal-ui.js').then((mod) => {
            if (disposed) return null;
            pedal = mod.createPedalController({
                vue: api,
                commands: {
                    play: () => pedalPlayback(modeAudio.play),
                    pause: () => pedalPlayback(modeAudio.pause),
                    playPause: () => pedalPlayback(modeAudio.isPlaying() ? modeAudio.pause : modeAudio.play),
                    rewind: (s) => pedalPlayback(() => tempo?.rewind(s)),
                    skip: (s) => pedalPlayback(() => tempo?.skip(s)),
                },
                mutating: [],
                canMutate: () => Boolean(editor.canEdit.value),
                ...(opts.pedalOptions || {}),
            });
            pedal.attach();
            pedalStatus.value = pedal.status.value;
            return pedal;
        }).catch(() => null);
    }

    /** Applies and stores new pedal settings (settings page); keeps the status line in step. */
    function setPedalSettings(next) {
        if (!pedal) return null;
        const applied = pedal.setSettings(next);
        pedalStatus.value = pedal.status.value;
        return applied;
    }

    async function start() {
        if (disposed) return null;
        modes.start();
        pedalReady = startPedal();
        return load();
    }

    /** Unsaved edits are saved before the page is told to close; a failed save keeps the view open. */
    async function close() {
        if (disposed) return false;
        // A save can settle an older pending operation and leave newer text dirty (reconcile first),
        // or the text can change while the save is awaited: save again, bounded, until nothing is dirty.
        // A save already in flight (autosave follow-up) is awaited, not counted as a failure or a pass.
        for (let pass = 0; editor.dirty.value;) {
            const res = pass > CLOSE_EXTRA_SAVES ? null : await save();
            if (disposed) return false;
            if (res && res.state === 'saving') {
                await saver.whenSettled();
                if (disposed) return false;
                continue;
            }
            pass += 1;
            if (!res || !res.ok) {
                closeMessage.value = TEXT.closeFailed;
                return false;
            }
        }
        if (typeof opts.onClose === 'function') opts.onClose({ dictationId });
        return true;
    }

    /** Stops every timer, listener and request, releases a held lease and clears clinical data held in memory. */
    function dispose() {
        if (disposed) return;
        const holdsLease = editor.lease.value && editor.lease.value.status === 'held';
        disposed = true;
        repeat.dispose();
        for (const stop of stops.splice(0)) if (typeof stop === 'function') stop();
        if (pedal) { pedal.detach(); pedal = null; }
        pedalStatus.value = '';
        unbindAudio();
        audioEl.value = null;
        const released = holdsLease ? editor.release() : null; // writes stop first, then the server is told
        modes.dispose();
        saver.dispose();
        alignment.dispose();
        editor.dispose();
        ready.value = false;
        editor.text.value = '';
        Promise.resolve(released).catch(() => {});
    }

    return {
        vue: V, api, editor, alignment, modes, saver, repeat, gain: () => gain,
        ready, loadGeneration, revisionGeneration, audioEl, gainState, closeMessage, resumeLabel,
        reviewList, reviewOutstanding, reviewActive, reviewMessage, reviewLoaded, loadReview, moveReview, confirmReview,
        recordingId: () => opts.recordingId || editor.audio.value.recordingId || null,
        peaksKey, peaksFetch,
        stepSeconds: v8 ? STEP_SECONDS_V8 : STEP_SECONDS, togglePlayback,
        leaseRun, leaseButtons,
        pedalStatus, pedalReady: () => pedalReady, pedal: () => pedal, setPedalSettings,
        start, load, save, setText, reloadLatest, keepMine, takeServer, compare: () => saver.compare(), close, dispose, syncRevision,
        client: scoped, adoptAudioRevision, bindAudio, bindAlignmentRoot, alignmentRootEl: () => alignmentRoot, alignmentNotice, onSync, onShortcut, onKeydown, onInput, onSelect, onTemplate, seekToResume, getPositionMs, seek,
        text: TEXT,
    };
}

/** Icon paths of the V8 symbol set (24x24 grid, stroke icons). Static markup, never built from data. */
const ICON_PATHS = Object.freeze({
    snippet: '<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
    'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    undo: '<path d="M9 5 4 10l5 5M4 10h9a6 6 0 0 1 0 12" transform="translate(0 -2)"/>',
    redo: '<path d="m15 5 5 5-5 5m5-5h-9a6 6 0 0 0 0 12" transform="translate(0 -2)"/>',
    paragraph: '<path d="M13 4v16m5-16v16M20 4H9a5 5 0 0 0 0 10h4"/>',
    marker: '<path d="M6 21V4m0 0c4-4 8 4 12 0v10c-4 4-8-4-12 0"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    replace: '<path d="M4 6h13l-3-3m3 3-3 3M20 18H7l3-3m-3 3 3 3M5 11v2m14-2v2"/>',
    'font-down': '<path d="m3 19 6-14 6 14M5 14h8m4-2h5"/>',
    'font-up': '<path d="m2 19 6-14 6 14M4 14h8m4-2h6m-3-3v6"/>',
    focus: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    unfocus: '<path d="M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5"/>',
    keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M5 9h1m3 0h1m3 0h1m3 0h1M5 12h1m3 0h1m3 0h1m3 0h1M7 16h10"/>',
    save: '<path d="M4 3h13l3 3v15H4zM8 3v6h8V3M8 21v-7h8v7"/>',
    previous: '<path d="m15 5-7 7 7 7"/>',
    next: '<path d="m9 5 7 7-7 7"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    highlight: '<path d="m8 13 7-9 5 4-7 9zM8 13l-3 6h6M3 22h18M13 6l5 4"/>',
    'follow-word': '<path d="M4 5h9M4 10h7M4 15h9m4-12v16m-4-4 4 4 4-4M4 21h7"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
    users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
    settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    'move-player': '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>'
});

/** One inline SVG icon of the V8 set; decorative (aria-hidden), the button carries the accessible name. */
const UiIcon = {
    name: 'ClinicUiIcon',
    props: { name: { type: String, required: true } },
    setup(props) {
        return { markup: () => ICON_PATHS[props.name] || '' };
    },
    template: '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" v-html="markup()"></svg>',
};

/**
 * Page-local rules on top of clinic-v8.css (owned by v8-110): narrow-screen wrapping of the status row,
 * theme-aware colours for the dirty status line and the key chips, lease buttons, the context drawer
 * and the spacing of the child panels. Tokens only, so light and dark both work.
 */
const LOCAL_CSS = `
.v8-ui .editor-page :is(.subline,.document-id,.document-context dt,.word-sync-info,.font-size,.template-meta,.editor-status){color:var(--v8-text-2)}
.v8-ui .editor-page :is(.context-tools,.player-controls,.word-sync-controls) .icon-button:not(:hover):not([aria-pressed=true]):not([aria-expanded=true]){background:transparent;border-color:transparent}
.v8-ui .editor-page :is(.context-tools,.player-controls,.word-sync-controls) .icon-button:is(:hover,[aria-expanded=true]):not([aria-pressed=true]){background:var(--v8-s2);border-color:var(--line)}
.v8-ui .editor-page .compact-player select{appearance:auto}
.v8-ui .correction-audio-panel {margin:16px 0;border-block:1px solid var(--line);padding-block:12px}
.v8-ui .audio-edit-fields {display:grid;gap:14px;margin-top:12px}
.v8-ui .audio-edit-fields fieldset {display:grid;gap:8px;min-width:0;padding:0;border:0}
.v8-ui .audio-edit-fields legend {font-weight:650;margin-bottom:6px}
.v8-ui .audio-edit-fields label {display:grid;gap:3px;min-width:0}
.v8-ui .audio-edit-range {display:grid;grid-template-columns:minmax(75px,1fr) auto;gap:8px;align-items:end}
.v8-ui .audio-edit-fields input {max-width:100%;min-width:0}
.v8-ui .audio-edit-fields button {justify-self:start}
.v8-ui .audio-edit-fields .clinic-audio-edit {display:flex;gap:6px;flex-wrap:wrap;padding:10px;border-radius:8px}
.v8-ui .audio-edit-fields .clinic-audio-edit p {flex-basis:100%;margin:0}
.v8-ui .editor-page :is(.context-tools,.toolbar-group:last-child,.find-row,.word-sync-controls,.dialoghead,.popover-heading) .tooltip{left:auto;right:0;transform:none}
.v8-ui .editor-page .alignment-text{max-height:8lh;overflow:auto;overflow-wrap:anywhere;white-space:pre-wrap;line-height:1.8;margin:0}
.v8-ui .editor-page .review-hits button:focus-visible{outline:2px solid var(--brand-accent);outline-offset:2px}
.v8-ui .editor-page .review-hits button[aria-disabled=true]{cursor:default}
.v8-ui .editor-page .editor-status{flex-wrap:wrap}
.v8-ui .editor-page .editor-status>[role=status]{flex:1 1 220px;min-width:0}
.v8-ui .editor-page .editor-status-right{flex-wrap:wrap;white-space:normal;min-width:0}
.v8-ui .editor-page .editor-workspace[data-dirty=true] .editor-status>[role=status]{color:var(--v8-text);font-weight:650}
.v8-ui .editor-page .editor-workspace[data-dirty=true] .status-dot{background:var(--burgundy)}
.v8-ui .editor-page .shortcut-list kbd{background:var(--v8-s2);color:var(--v8-text);border-color:var(--line)}
.v8-ui .editor-page .live-inline-status .button{min-height:32px;padding:5px 12px;font-size:12px}
.v8-ui .editor-page .live-inline-status .button:not(.primary){color:var(--v8-text)}
.v8-ui .editor-page .live-inline-status .button:disabled{opacity:.55}
.v8-ui .editor-page .context-drawer{background:var(--surface);color:var(--v8-text);border:1px solid var(--line);box-shadow:0 12px 40px rgb(0 0 0 / .22);z-index:120}
.v8-ui .editor-page .context-drawer:not([data-open]){transform:translateX(calc(100% + 40px));pointer-events:none;box-shadow:none}
.v8-ui .editor-page .context-drawer .dialoghead{display:flex;align-items:center;justify-content:space-between;gap:12px}
.v8-ui .editor-page .context-drawer h2{font-size:16px;font-weight:700;letter-spacing:-.2px;color:var(--v8-text);margin:0}
.v8-ui .editor-page .context-drawer h3{font-size:13px;font-weight:700;color:var(--v8-text);margin:0 0 6px}
.v8-ui .editor-page .context-drawer .panel-header h2{font-size:14px}
.v8-ui .editor-page .context-drawer .hint{font-size:11px;color:var(--v8-text-2)}
.v8-ui .editor-page .context-drawer .keyvalues{margin:0}
.v8-ui .editor-page .context-drawer .keyvalues>div{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid var(--v8-line-2)}
.v8-ui .editor-page .context-drawer .keyvalues dt{color:var(--v8-text-2)}
.v8-ui .editor-page .context-drawer .keyvalues dd{margin:0;font-weight:600;text-align:right}
.v8-ui .editor-page .clinic-audio-markers-actions{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.v8-ui .editor-page .clinic-audio-markers button{padding:5px 12px;font-size:12px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--v8-text)}
.v8-ui .editor-page .clinic-audio-markers ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.v8-ui .editor-page .clinic-audio-markers li{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;padding:8px;border-radius:8px}
.v8-ui .editor-page .clinic-correction-waveform-uncertain,.v8-ui .editor-page .clinic-correction-waveform-unaligned{font-size:12px}
.v8-ui .editor-page .editor-aux{display:grid;gap:14px;margin-top:16px}
.v8-ui .editor-page .correction-backstep{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-block:14px 0}
.v8-ui .editor-page .correction-backstep input{width:7ch;min-height:44px;padding:6px 8px;border:1px solid var(--v8-input-line);border-radius:7px;color:var(--v8-text);background:var(--surface)}
.v8-ui .editor-page :is(.panel-header h2,.clinic-correction-waveform h3){font-size:14px;font-weight:700;letter-spacing:-.1px;color:var(--v8-text)}
.v8-ui .editor-page .sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
@media(max-width:767px){.v8-ui .editor-page .document-id{min-width:0;white-space:normal;overflow-wrap:anywhere}.v8-ui .editor-page .document-title h2{flex-shrink:0}}
@media(max-width:700px){
  .v8-ui .editor-page .editor-popover{position:static;width:auto;max-height:none;margin:8px 12px}
  .v8-ui .editor-page .find-row{display:grid;grid-template-columns:minmax(0,1fr) repeat(3,auto)}
  .v8-ui .editor-page .find-row>input{min-width:0;width:100%}
  .v8-ui .editor-page .find-count{grid-column:2 / -1;min-width:0}
  .v8-ui .editor-page .review-hits button{min-height:44px}
}
.v8-ui .editor-page[data-open-mode="player"] .compact-player{border:1px solid var(--line);background:var(--v8-s2);border-radius:8px;padding:8px 10px}
.v8-ui .editor-page .player-dock .compact-player{box-sizing:border-box;flex-wrap:wrap}
.v8-ui .editor-page .player-dock[data-player-moved] .compact-player{position:relative;z-index:30;width:min(100%, 640px);transform:translate(var(--player-x, 0px),var(--player-y, 0px));border:1px solid var(--line);border-radius:var(--ui-radius-sm);box-shadow:var(--shadow)}
.v8-ui .editor-page .player-dock:not([data-player-moved]) .player-reset{display:none}
.v8-ui .editor-page .player-dock .tempo-controls>input[type=range]{flex:1;min-width:100px}
.v8-ui .editor-page .player-placement{display:flex;gap:2px;align-items:center;flex:none}
.v8-ui .editor-page .player-move{cursor:grab;touch-action:none;user-select:none}
.v8-ui .editor-page .player-move:active{cursor:grabbing}
@media(max-width:700px){
  .v8-ui .editor-page .player-dock .tempo-controls>input[type=range]{flex-basis:100%;min-width:0}
  .v8-ui .editor-page .player-placement .icon-button{width:44px;height:44px;min-height:44px}
}
`;
const LOCAL_CSS_ID = 'clinic-v8-schreibplatz-local';

const FOCUS = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
const box = (name) => {
    const s = surfaceFor(name);
    return { background: s.background, color: s.color, border: '1px solid ' + s.border };
};

const BODY_PROPS = {
    client: { type: Object, default: null },
    dictationId: { type: String, required: true },
    currentUser: { type: Object, required: true },
    vue: { type: Object, default: null },
    recordingId: { type: String, default: null },
    createAudioContext: { type: Function, default: null },
    dictator: { type: String, default: null },
    pool: { type: String, default: null },
    openMode: { type: String, default: '' },
};

/** Inner body: one instance per dictation. The outer view re-keys it, which disposes everything. */
export const CorrectionBody = {
    name: 'ClinicCorrectionBody',
    components: { DictationSummary, CorrectionAudioPanel, CorrectionWaveform, AudioMarkers, PlaybackTempo, PlaybackGain, UiIcon, FinishOptions },
    directives: { tip: vTip },
    props: BODY_PROPS,
    emits: ['close'],
    setup(props, ctx) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the correction view');
        const c = createCorrectionViewController({
            client: props.client || undefined,
            dictationId: props.dictationId,
            currentUser: props.currentUser,
            vue: props.vue || undefined,
            contract: 'v8', // the mounted view runs the V8 save contract: lease controller, reconcile first, +-5 s step
            recordingId: props.recordingId || undefined,
            createAudioContext: props.createAudioContext || undefined,
            onClose: () => { if (ctx && typeof ctx.emit === 'function') ctx.emit('close'); },
        });
        if (typeof V.onMounted === 'function') V.onMounted(() => { c.start(); });
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        if (typeof document !== 'undefined' && document.head && !document.getElementById(LOCAL_CSS_ID)) {
            const styleEl = document.createElement('style');
            styleEl.id = LOCAL_CSS_ID;
            styleEl.textContent = LOCAL_CSS;
            document.head.appendChild(styleEl);
        }
        const showDiff = V.ref(false);
        const e = c.editor;
        const a = c.alignment;
        const s = c.saver;
        const m = c.modes;
        const finishOptions = V.shallowRef(null);
        const finishWorkflow = createWorkflowActions({ client: c.client, reactive: V.reactive });
        if (typeof V.watch === 'function') {
            V.watch([c.loadGeneration, e.textRevisionId, () => e.audio.value.revisionId], async ([loaded, textId, audioId], _, onCleanup) => {
                finishWorkflow.load(null);
                if (!loaded || !textId || !audioId || props.openMode === 'player') return;
                let current = true;
                onCleanup(() => { current = false; });
                const { outcome, data } = await c.client.request('/dictations/' + encodeURIComponent(props.dictationId) + '/detail');
                const binding = data?.binding?.release;
                if (!current || outcome !== 'ok' || !Array.isArray(data?.allowed_actions)
                        || binding?.transcript_revision_id !== textId
                        || binding.original_version_id !== audioId || !binding.context_revision_id || !binding.text_sha256) return;
                finishWorkflow.load({ dictationId: props.dictationId, etag: data.etag, revision: data.revision,
                    allowedActions: data.allowed_actions, binding: data.binding });
            });
        }
        function onFinishKeydown(event) {
            // Handle F5 before the nested dialog's own shortcut listener.
            event.stopPropagation();
            finishOptions.value?.onKeydown(event);
            if (!event.defaultPrevented) event.preventDefault();
        }
        const highlightOn = V.ref(true);
        const editorScroll = V.ref(0);
        const reviewFocused = V.ref(false);
        const reviewMark = V.shallowRef(null);
        const reviewSpan = V.computed(() => reviewFocused.value ? c.reviewList.value[c.reviewActive.value] : null);
        const inlineWord = V.computed(() => {
            if (reviewSpan.value) return reviewSpan.value;
            if (!highlightOn.value || e.dirty.value || a.activeGranularity.value !== 'word') return null;
            const state = a.alignmentState.value;
            return state === 'succeeded' || state === 'partial' ? a.currentWord.value : null;
        });
        const styleFor = (seg) => {
            const word = a.currentWord.value;
            if (highlightOn.value && word && seg.linkId === word.id) return box('processing');
            if (seg.flagged) { const w = surfaceFor('warning'); return { background: w.background, color: w.color }; }
            return {};
        };

        // --- V8 editor chrome: toolbar controller over the text area, view state ---------------------------
        const textEl = V.shallowRef(null);
        const fontPx = V.ref(16);
        const focusMode = V.ref(false);
        const shortcutsOpen = V.ref(false);
        const followOn = V.ref(false);
        const drawer = V.ref('');
        const drawerClose = V.shallowRef(null);
        let drawerOpener = null;
        const later = (fn) => { if (typeof V.nextTick === 'function') V.nextTick(fn); else fn(); };
        const editorPort = {
            read() {
                const el = textEl.value;
                return { text: e.text.value, start: el ? el.selectionStart : 0, end: el ? el.selectionEnd : 0 };
            },
            write(next) {
                c.setText(next.text);
                later(() => { const el = textEl.value; if (el) el.setSelectionRange(next.start, next.end); });
            },
            select(start, end) {
                const el = textEl.value;
                if (!el) return;
                el.focus();
                el.setSelectionRange(start, end);
            },
        };
        const tb = createCorrectionToolbar({
            editor: editorPort,
            canEdit: () => Boolean(e.canEdit.value),
            onFontSize: (px) => { fontPx.value = px; },
            onFocusMode: (on) => {
                focusMode.value = on;
                if (typeof document !== 'undefined' && document.body) document.body.classList.toggle('editor-focus-mode', on);
            },
            onShowShortcuts: () => { shortcutsOpen.value = !shortcutsOpen.value; },
        });
        const tbState = V.shallowRef(tb.getState());
        const offToolbar = tb.subscribe((snapshot) => { tbState.value = snapshot; });
        if (typeof V.onBeforeUnmount === 'function') {
            V.onBeforeUnmount(() => {
                offToolbar();
                if (typeof document !== 'undefined' && document.body) document.body.classList.remove('editor-focus-mode');
            });
        }
        if (typeof V.watch === 'function') {
            V.watch(c.loadGeneration, () => { tb.reset(); });
            V.watch(e.canEdit, () => { tb.refresh(); });
            V.watch([followOn, a.currentWord], ([follow, word]) => {
                if (!follow || !word || drawer.value) return;
                const root = c.alignmentRootEl();
                if (!root || !root.clientHeight || root.scrollHeight <= root.clientHeight) return;
                const panel = root.getBoundingClientRect();
                if (panel.bottom <= 0 || panel.top >= root.ownerDocument.documentElement.clientHeight) return;
                const node = root.querySelector('[aria-current="true"]');
                if (!node) return;
                const cue = node.getBoundingClientRect();
                if (cue.top < panel.top) root.scrollTop += cue.top - panel.top;
                else if (cue.bottom > panel.bottom) root.scrollTop += cue.bottom - panel.bottom;
            }, { flush: 'post' });
        }
        const setTextEl = (node) => { textEl.value = node && typeof node === 'object' ? node : null; };
        const onTextInput = (event) => { c.onInput(event); tb.notifyChange(); };
        const onTextKeydown = (event) => {
            c.onKeydown(event);
            if (tb.shortcut(event) && typeof event.preventDefault === 'function') event.preventDefault();
        };
        const onFindKeydown = (event) => {
            if (event.key === 'Enter') { event.preventDefault(); tb.step(event.shiftKey ? -1 : 1); }
        };
        const onQuery = (event) => { tb.setQuery(event.target.value); };
        const onReplacement = (event) => { tb.setReplacement(event.target.value); };
        const onCase = (event) => { tb.setCaseSensitive(event.target.checked); };
        const countLabel = () => {
            const s = tbState.value;
            if (!s.query) return '';
            return s.matches ? (s.current >= 0 ? (s.current + 1) + ' / ' + s.matches : s.matches + ' Treffer') : TEXT.noMatches;
        };
        const SHORTCUTS = Object.freeze([
            ['Speichern', 'Strg/Cmd+S'], [TEXT.undo, 'Strg/Cmd+Z'], [TEXT.redo, 'Strg/Cmd+Umschalt+Z'],
            [TEXT.paragraph, 'Strg/Cmd+Enter'], [TEXT.marker, 'Strg/Cmd+Umschalt+M'], [TEXT.find, 'Strg/Cmd+F'],
            ['Vollbild', 'Strg/Cmd+Umschalt+F'], [TEXT.shortcuts, 'F1'], ['Fertigstellen (wenn verfügbar)', 'F5'],
        ]);
        const WORKER = { held: TEXT.workerHeld, foreign: TEXT.workerForeign, lost: TEXT.workerLost };
        const workerLabel = () => WORKER[e.lease.value.status] || TEXT.workerFree;
        const templatesOpen = V.ref(false);
        const DRAWER_TITLES = { assignment: TEXT.ctxAssignment, marks: TEXT.ctxMarks, options: TEXT.ctxOptions };
        const drawerTitle = () => DRAWER_TITLES[drawer.value] || '';
        const closeDrawer = () => {
            const opener = drawerOpener;
            drawerOpener = null;
            drawer.value = '';
            later(() => { if (!drawer.value && opener?.isConnected) opener.focus(); });
        };
        const toggleDrawer = (id, opener) => {
            if (drawer.value === id) return closeDrawer();
            drawerOpener = opener;
            drawer.value = id;
            later(() => { if (drawer.value === id) drawerClose.value?.focus(); });
        };
        // saveMessage already says "Nicht gespeicherte Änderungen." while dirty; do not repeat it.
        const unsavedSuffix = () => Boolean(e.dirty.value) && !/nicht gespeicherte/i.test(String(s.message.value || ''));
        const pickTemplate = (id) => { templatesOpen.value = false; if (id) e.applyTemplate(id); };
        const toolStatus = () => tbState.value.readOnlyReason || tbState.value.message || '';
        const reviewRefs = new Map();
        const setReviewRef = (id, node) => { if (node) reviewRefs.set(id, node); else reviewRefs.delete(id); };
        const activateReview = (index) => {
            c.reviewActive.value = index;
            reviewFocused.value = true;
            const hit = c.reviewList.value[index];
            textEl.value?.setSelectionRange(hit.start, hit.end);
            later(() => {
                const el = textEl.value;
                if (!reviewFocused.value || c.reviewActive.value !== index || !el || !reviewMark.value) return;
                const editorBounds = el.getBoundingClientRect();
                const markBounds = reviewMark.value.getBoundingClientRect();
                if (markBounds.top < editorBounds.top || markBounds.bottom > editorBounds.bottom) {
                    el.scrollTop += markBounds.top - editorBounds.top - el.clientHeight / 2;
                }
            });
        };
        const onReviewKeydown = (event, index) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                const hit = c.reviewList.value[index];
                editorPort.select(hit.start, hit.end);
                return;
            }
            const n = c.reviewList.value.length;
            const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1, Home: -index, End: n - 1 - index }[event.key];
            if (typeof step !== 'number') return;
            event.preventDefault();
            const next = c.reviewList.value[c.moveReview(step)];
            if (next) reviewRefs.get(next.id)?.focus();
        };
        return {
            ...useCorrectionPlayerPosition(V),
            reviewList: c.reviewList, reviewOutstanding: c.reviewOutstanding, reviewActive: c.reviewActive, reviewMessage: c.reviewMessage,
            reviewLoaded: c.reviewLoaded, reviewFocused, reviewSpan, reviewMark, setReviewRef, activateReview, onReviewKeydown,
            formatDictationReference,
            c, FOCUS, MODES, box, styleFor, TEXT, showDiff, finishOptions, finishWorkflow, onFinishKeydown,
            tb, tbState, textEl, setTextEl, fontPx, focusMode, shortcutsOpen, highlightOn, inlineWord, editorScroll, followOn, onTextInput, onTextKeydown, onFindKeydown,
            onQuery, onReplacement, onCase, countLabel, SHORTCUTS, toolStatus, workerLabel, templatesOpen, pickTemplate, drawer, drawerTitle, drawerClose, closeDrawer, toggleDrawer, unsavedSuffix,
            repeatState: c.repeat.state, REPEAT_TEXT,
            text: e.text, canEdit: e.canEdit, dirty: e.dirty, textRevisionId: e.textRevisionId, loading: e.loading, error: e.error, manual: e.manual,
            lease: e.lease, audio: e.audio, audioSynced: e.audioSynced,
            template: e.template, templates: e.templates, templatesError: e.templatesError,
            editorAnnouncement: e.announcement,
            alignStatus: a.status, alignError: a.error, segments: a.segments, currentWord: a.currentWord, alignAnnouncement: a.announcement,
            saveState: s.state, saveMessage: s.message,
            mode: m.mode, modeStatus: m.status, modeError: m.error, modeAnnouncement: m.announcement,
            backstepSeconds: m.backstepSeconds,
            ready: c.ready, loadGeneration: c.loadGeneration, revisionGeneration: c.revisionGeneration,
            audioEl: c.audioEl, gainState: c.gainState, closeMessage: c.closeMessage, resumeLabel: c.resumeLabel,
            pedalStatus: c.pedalStatus, alignmentNotice: c.alignmentNotice,
        };
    },
    template: `
<section class="clinic-correction-view editor-page v8-page" data-testid="clinic-correction-view" :data-open-mode="openMode === 'player' ? 'player' : 'correction'" aria-labelledby="ccv-title" :aria-busy="loading ? 'true' : 'false'"
         @keydown.f5.exact.capture="onFinishKeydown" @keydown="c.onShortcut($event)">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <div class="hline">
        <h1 id="ccv-title" style="font-weight: 700; letter-spacing: -.8px; color: var(--v8-text)">{{ openMode === 'player' ? TEXT.playerPage : TEXT.page }}</h1>
        <FinishOptions v-if="finishWorkflow.state.allowed.includes('release')"
                       ref="finishOptions" :workflow="finishWorkflow" :client="c.client" :vue="vue"
                       :disabled="dirty || loading || !!error" @finished="c.close()" />
      </div>
      <p class="subline">{{ openMode === 'player' ? TEXT.playerSubline : TEXT.subline }}</p>
    </div>
    <div class="head-actions">
      <slot name="back-action" :close="c.close">
        <button type="button" class="button" :style="FOCUS" data-testid="close" @click="c.close()">
          <UiIcon name="arrow-right" /><span>{{ TEXT.toWorklist }}</span>
        </button>
      </slot>
    </div>
  </div>
  <p v-if="closeMessage" role="alert" class="note" :style="box('error')">{{ closeMessage }}</p>
  <p class="sr-only" id="ccv-keys">{{ TEXT.keys }}</p>

  <div v-if="loading && !ready" role="status" aria-live="polite" class="note" data-testid="loading-state">{{ TEXT.loading }}</div>
  <div v-if="error" role="alert" class="note" :style="box('error')" data-testid="load-error">
    {{ error.message }}
    <button type="button" class="small" :style="FOCUS"
            @click="c.load({ discard: error.code === 'stale' })">{{ error.code === 'stale' ? TEXT.reload : TEXT.retry }}</button>
  </div>

  <DictationSummary v-if="ready" :client="c.client" :dictation-id="dictationId" :text-revision-id="textRevisionId" :dirty="dirty" />
  <section class="editor-workspace" :class="{ 'is-focus': focusMode }" :data-dirty="dirty ? 'true' : 'false'" :aria-label="TEXT.workspace">
    <div class="editor-heading">
      <div class="document-title">
        <UiIcon name="snippet" />
        <h2 style="font-weight: 700; color: var(--v8-text)">{{ TEXT.dictation }}</h2>
        <details class="document-id">
          <summary :title="dictationId"><span aria-hidden="true">{{ formatDictationReference(dictationId) }}</span><span class="sr-only">Vollständige Diktatkennung {{ dictationId }}</span></summary>
          <span class="document-id-full">{{ dictationId }}</span>
        </details>
      </div>
      <div class="context-tools">
        <span class="tool-wrap" v-tip="{ label: TEXT.ctxAssignment }">
          <button type="button" class="icon-button" :style="FOCUS" :disabled="!ready" :aria-label="TEXT.ctxAssignment" :aria-expanded="String(drawer === 'assignment')"
                  aria-haspopup="dialog" aria-controls="ccv-drawer" data-context-toggle="assignment" @click="toggleDrawer('assignment', $event.currentTarget)"><UiIcon name="users" /></button>
        </span>
        <span class="tool-wrap" v-tip="{ label: TEXT.ctxMarks }">
          <button type="button" class="icon-button" :style="FOCUS" :disabled="!ready" :aria-label="TEXT.ctxMarks" :aria-expanded="String(drawer === 'marks')"
                  aria-haspopup="dialog" aria-controls="ccv-drawer" data-context-toggle="marks" @click="toggleDrawer('marks', $event.currentTarget)"><UiIcon name="marker" /></button>
        </span>
        <span class="tool-wrap" v-tip="{ label: TEXT.ctxOptions }">
          <button type="button" class="icon-button" :style="FOCUS" :disabled="!ready" :aria-label="TEXT.ctxOptions" :aria-expanded="String(drawer === 'options')"
                  aria-haspopup="dialog" aria-controls="ccv-drawer" data-context-toggle="options" @click="toggleDrawer('options', $event.currentTarget)"><UiIcon name="settings" /></button>
        </span>
        <span class="tool-wrap" v-tip="{ label: TEXT.reload }">
          <button type="button" class="icon-button" :style="FOCUS" :disabled="loading || dirty" :aria-label="TEXT.reload"
                  data-testid="reload" @click="c.load()"><UiIcon name="refresh" /></button>
        </span>
      </div>
    </div>

    <dl class="document-context">
      <div><dt>{{ TEXT.dictatedBy }}</dt><dd data-dictator-name>{{ dictator || TEXT.openAssignment }}</dd></div>
      <div><dt>{{ TEXT.pool }}</dt><dd data-pool-name>{{ pool || TEXT.noPool }}</dd></div>
      <div><dt>{{ TEXT.editing }}</dt><dd data-worker-name>{{ workerLabel() }}</dd></div>
    </dl>

    <div class="live-inline-status" role="group" :aria-label="TEXT.lease" data-testid="lease-bar"
         style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">
      <span role="status" aria-live="polite" :style="{ color: lease.surface.color }">{{ lease.message }}</span>
      <button v-for="b in c.leaseButtons()" :key="b.id" type="button" :class="['button', 'small', b.id === 'acquire' ? 'primary' : '']" :style="FOCUS" :disabled="b.disabled"
              :data-action="b.id" @click="c.leaseRun[b.id]()">{{ b.label }}</button>
    </div>

    <template v-if="ready">
      <div class="player-dock" ref="playerDock">
      <div class="player compact-player" data-testid="player-bar">
        <span class="player-placement">
          <span class="tool-wrap" v-tip="{ label: 'Player verschieben', keys: 'Pfeiltasten · Pos1 zurücksetzen' }">
            <button type="button" class="icon-button player-move" :style="FOCUS" data-testid="player-move"
                    aria-label="Player verschieben" aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Home Escape"
                    @pointerdown="startMove" @pointermove="movePlayer" @pointerup="endMove"
                    @pointercancel="endMove" @lostpointercapture="endMove" @keydown="playerPositionKey"><UiIcon name="move-player" /></button>
          </span>
          <span class="tool-wrap player-reset" v-tip="{ label: 'Playerposition zurücksetzen' }">
            <button type="button" class="icon-button" :style="FOCUS" data-testid="player-reset"
                    aria-label="Playerposition zurücksetzen" @click="resetPlayer(); $event.currentTarget.closest('.player-placement').querySelector('.player-move').focus({ preventScroll: true })"><UiIcon name="refresh" /></button>
          </span>
        </span>
        <audio preload="metadata" hidden :ref="c.bindAudio" :src="audio.status === 'ready' ? audio.src : null" :aria-label="TEXT.audio"></audio>
        <PlaybackTempo v-if="audioEl" :audio="audioEl" :step-seconds="c.stepSeconds" :on-sync="c.onSync" :on-toggle="c.togglePlayback" />
        <span class="word-sync-controls">
          <span class="tool-wrap" v-tip="{ label: highlightOn ? TEXT.highlightOn : TEXT.highlightOff }">
            <button type="button" class="icon-button" :style="FOCUS" :aria-pressed="String(highlightOn)" :aria-label="highlightOn ? TEXT.highlightOn : TEXT.highlightOff"
                    data-testid="highlight-toggle" @click="highlightOn = !highlightOn"><UiIcon name="highlight" /></button>
          </span>
          <span class="tool-wrap" v-tip="{ label: TEXT.follow }">
            <button type="button" class="icon-button" :style="FOCUS" :aria-pressed="String(followOn)" :aria-label="TEXT.follow"
                    data-testid="follow-toggle" @click="followOn = !followOn"><UiIcon name="follow-word" /></button>
          </span>
        </span>
      </div>
      </div>
      <p class="word-sync-info" role="status" aria-live="polite">
        <template v-if="audio.status !== 'ready' && audio.status !== 'idle' && audio.status !== 'loading'">{{ audio.message + ' ' }}</template>
        <span v-if="audioSynced === false">{{ TEXT.audioUnsynced + ' ' }}</span>
        <span v-if="alignmentNotice" data-testid="alignment-notice">{{ alignmentNotice + ' ' }}</span>
        <span v-if="pedalStatus" data-testid="pedal-status">{{ pedalStatus }}</span>
      </p>

      <div class="editor-document" @keydown.esc="shortcutsOpen = false; templatesOpen = false">
        <div class="editorbar symbol-toolbar" role="toolbar" :aria-label="TEXT.toolbar" data-testid="toolbar">
          <div class="toolbar-group">
            <span class="tool-wrap" v-tip="{ label: TEXT.undo, keys: 'Strg/Cmd+Z' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.undo"
                    :aria-disabled="tbState.canUndo ? null : 'true'" data-tool="undo" @click="tb.undo()"><UiIcon name="undo" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.redo, keys: 'Strg/Cmd+Umschalt+Z' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.redo"
                    :aria-disabled="tbState.canRedo ? null : 'true'" data-tool="redo" @click="tb.redo()"><UiIcon name="redo" /></button></span>
          </div>
          <div class="toolbar-group">
            <span class="tool-wrap" v-tip="{ label: TEXT.paragraph, keys: 'Strg/Cmd+Enter' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.paragraph"
                    data-tool="paragraph" @click="tb.insertParagraph()"><UiIcon name="paragraph" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.markerTip, keys: 'Strg/Cmd+Umschalt+M' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.marker"
                    aria-disabled="true" data-tool="marker" @click="tb.insertMarker()"><UiIcon name="marker" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.templateMenu }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.templateMenu"
                    :aria-expanded="String(templatesOpen)" aria-controls="ccv-templates" :disabled="!canEdit" data-tool="template" @click="templatesOpen = !templatesOpen; shortcutsOpen = false"><UiIcon name="snippet" /></button></span>
          </div>
          <div class="toolbar-group">
            <span class="tool-wrap" v-tip="{ label: TEXT.find, keys: 'Strg/Cmd+F' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.find"
                    :aria-expanded="String(tbState.searchOpen)" data-tool="search" @click="tb.toggleSearch()"><UiIcon name="search" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.replace }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.replace"
                    :aria-expanded="String(tbState.searchOpen)" data-tool="replace" @click="tb.openSearch()"><UiIcon name="replace" /></button></span>
          </div>
          <div class="toolbar-group font-group">
            <span class="tool-wrap" v-tip="{ label: TEXT.fontDown }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.fontDown"
                    :aria-disabled="tbState.fontMin ? 'true' : null" data-tool="fontDown" @click="tb.fontDown()"><UiIcon name="font-down" /></button></span>
            <span class="font-size" role="status" data-testid="font-size">{{ fontPx }} px</span>
            <span class="tool-wrap" v-tip="{ label: TEXT.fontUp }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.fontUp"
                    :aria-disabled="tbState.fontMax ? 'true' : null" data-tool="fontUp" @click="tb.fontUp()"><UiIcon name="font-up" /></button></span>
          </div>
          <span class="toolbar-spacer"></span>
          <div class="toolbar-group">
            <span class="tool-wrap" v-tip="{ label: focusMode ? TEXT.focusOff : TEXT.focusOn }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="focusMode ? TEXT.focusOff : TEXT.focusOn"
                    :aria-pressed="String(focusMode)" data-tool="focus" @click="tb.toggleFocus()"><UiIcon :name="focusMode ? 'unfocus' : 'focus'" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.shortcuts, keys: 'F1' }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.shortcuts"
                    :aria-expanded="String(shortcutsOpen)" data-tool="shortcuts" @click="shortcutsOpen = !shortcutsOpen; templatesOpen = false"><UiIcon name="keyboard" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.save, keys: 'Strg/Cmd+S' }"><button type="button" class="icon-button save-button" :class="{ 'has-changes': dirty }" :style="FOCUS"
                    :aria-label="dirty ? TEXT.saveDirty : TEXT.save"
                    :disabled="saveState === 'saving' || saveState === 'saved'" data-testid="save" @click="c.save()"><UiIcon name="save" /></button></span>
          </div>
        </div>

        <div v-if="tbState.searchOpen" class="editor-find" role="search" :aria-label="TEXT.replace" data-testid="search-panel">
          <div class="find-row">
            <input type="text" :aria-label="TEXT.query" :placeholder="TEXT.query" :value="tbState.query" data-testid="search-query" @input="onQuery" @keydown="onFindKeydown">
            <label class="case-option"><input type="checkbox" :checked="tbState.caseSensitive" data-testid="search-case" @change="onCase"> {{ TEXT.caseSensitive }}</label>
            <span class="find-count" role="status" data-testid="search-count">{{ countLabel() }}</span>
            <span class="tool-wrap" v-tip="{ label: TEXT.prev }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.prev" @click="tb.step(-1)"><UiIcon name="previous" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.next }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.next" @click="tb.step(1)"><UiIcon name="next" /></button></span>
            <span class="tool-wrap" v-tip="{ label: TEXT.closeSearch }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.closeSearch" @click="tb.closeSearch()"><UiIcon name="close" /></button></span>
          </div>
          <div class="replace-row">
            <input type="text" :aria-label="TEXT.replacement" :placeholder="TEXT.replacement" :value="tbState.replacement" data-testid="search-replacement" @input="onReplacement">
            <button type="button" class="small" :style="FOCUS" :disabled="!canEdit" @click="tb.replaceCurrent()">{{ TEXT.replaceOne }}</button>
            <button type="button" class="small" :style="FOCUS" :disabled="!canEdit" @click="tb.replaceAll()">{{ TEXT.replaceAll }}</button>
          </div>
        </div>

        <div v-if="shortcutsOpen" class="editor-popover shortcut-popover" role="dialog" :aria-label="TEXT.shortcuts">
          <div class="popover-heading">
            <strong>{{ TEXT.shortcuts }}</strong>
            <span class="tool-wrap" v-tip="{ label: TEXT.shortcutsClose }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.shortcutsClose" @click="shortcutsOpen = false"><UiIcon name="close" /></button></span>
          </div>
          <dl class="shortcut-list">
            <div v-for="row in SHORTCUTS" :key="row[0]"><dt>{{ row[0] }}</dt><dd><kbd>{{ row[1] }}</kbd></dd></div>
          </dl>
        </div>

        <div v-if="templatesOpen" id="ccv-templates" class="editor-popover" role="dialog" :aria-label="TEXT.templateMenu" data-testid="template-menu" style="left: 13px">
          <div class="popover-heading">
            <strong>{{ TEXT.template }}</strong>
            <span class="tool-wrap" v-tip="{ label: TEXT.close }"><button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.close" @click="templatesOpen = false"><UiIcon name="close" /></button></span>
          </div>
          <div class="snippet-options">
            <button type="button" class="snippet-option" :style="FOCUS" :disabled="!canEdit" :aria-pressed="String(!template)" @click="pickTemplate('')">
              <span><strong>{{ TEXT.noTemplate }}</strong></span>
            </button>
            <button v-for="row in templates.items" :key="row.id" type="button" class="snippet-option" :style="FOCUS" :disabled="!canEdit"
                    :aria-pressed="String(Boolean(template && template.id === row.id))" @click="pickTemplate(row.id)">
              <UiIcon name="snippet" /><span><strong>{{ row.name }}</strong><small>Version {{ row.version }}</small></span>
            </button>
          </div>
        </div>

        <p v-if="templatesError" class="template-meta" role="note">{{ templatesError }}</p>
        <ul v-if="template && template.issues.length" class="template-meta" role="note" aria-label="Hinweise zur Vorlage" style="margin: 0; list-style: none">
          <li v-for="issue in template.issues" :key="issue.code">{{ issue.message }}</li>
        </ul>
        <p v-if="manual" class="template-meta" role="note">{{ TEXT.manual }}</p>
        <p v-if="toolStatus()" class="template-meta" role="status" data-testid="toolbar-reason">{{ toolStatus() }}</p>

        <div class="writing-surface" :class="{ 'has-word-sync': inlineWord }">
          <div v-if="inlineWord" class="word-backdrop" aria-hidden="true">
            <div class="word-backdrop-content" :style="{ fontSize: fontPx + 'px', transform: 'translateY(' + (-editorScroll) + 'px)' }">{{ text.slice(0, inlineWord.start) }}<mark ref="reviewMark" data-active-word :data-review-span="reviewSpan ? '' : null" :style="reviewSpan ? { background: 'var(--ui-hover)', boxShadow: 'inset 0 -2px 0 var(--brand-accent)' } : null">{{ text.slice(inlineWord.start, inlineWord.end) }}</mark>{{ text.slice(inlineWord.end) }}</div>
          </div>
          <textarea class="editor" :ref="setTextEl" :style="{ ...FOCUS, fontSize: fontPx + 'px' }" :value="text" :readonly="!canEdit" :aria-readonly="String(!canEdit)"
                    :aria-label="TEXT.text" :aria-describedby="canEdit ? 'ccv-keys' : 'ccv-keys ccv-readonly'" data-testid="text"
                    @scroll.passive="editorScroll = $event.target.scrollTop" @input="onTextInput($event)" @keydown="onTextKeydown($event)" @select="followOn = false; c.onSelect($event)"
                    @keyup="c.onSelect($event)" @mouseup="c.onSelect($event)"></textarea>
        </div>
        <section v-if="reviewLoaded || reviewMessage" class="panel review-hits" :aria-label="TEXT.reviewTitle" data-testid="review-hits">
          <div class="panel-header" style="flex-wrap: wrap; margin-bottom: 0">
            <h2>{{ TEXT.reviewTitle }}</h2>
            <span v-if="reviewLoaded" class="hint" role="status" data-testid="review-outstanding">{{ reviewOutstanding ? reviewOutstanding + ' ' + TEXT.reviewOpen.toLowerCase() : TEXT.reviewNone }}</span>
          </div>
          <p v-if="reviewList.length" id="ccv-review-keys" class="hint" style="margin: var(--ui-space-2) 0">{{ TEXT.reviewHint }}</p>
          <ul v-if="reviewList.length" role="list" style="list-style: none; margin: 0; padding: 0; display: grid; gap: var(--ui-space-2)">
            <li v-for="(hit, i) in reviewList" :key="hit.id" class="review-hit" :data-state="hit.state" :data-testid="'review-hit-' + hit.id"
                style="display: flex; flex-wrap: wrap; align-items: center; gap: var(--ui-space-2); min-width: 0; overflow-wrap: anywhere">
              <button type="button" class="button small" :ref="node => setReviewRef(hit.id, node)" :style="FOCUS" :tabindex="i === reviewActive ? 0 : -1"
                      :aria-disabled="!canEdit || hit.touched || hit.state !== 'open' ? 'true' : null"
                      :aria-label="(hit.state === 'confirmed' ? TEXT.reviewConfirmed : TEXT.reviewConfirm) + ': ' + hit.label"
                      :aria-describedby="hit.touched ? 'ccv-review-keys ccv-review-draft-' + i : 'ccv-review-keys'"
                      @focus="activateReview(i)" @blur="reviewFocused = false" @keydown="onReviewKeydown($event, i)" @click="c.confirmReview(hit.id)">
                {{ hit.state === 'confirmed' ? TEXT.reviewConfirmed : TEXT.reviewConfirm }}
              </button>
              <strong>{{ hit.label }}</strong>
              <span class="hint" style="min-width: 0">{{ hit.context }}</span>
              <span class="sr-only">{{ hit.state === 'confirmed' ? TEXT.reviewConfirmed : TEXT.reviewOpen }}</span>
              <span v-if="hit.touched" :id="'ccv-review-draft-' + i" class="hint">{{ TEXT.reviewDraft }}</span>
            </li>
          </ul>
          <p v-if="reviewMessage" role="alert" class="hint" data-testid="review-message">{{ reviewMessage }}</p>
        </section>
        <p v-if="!canEdit && !loading" id="ccv-readonly" class="template-meta" style="margin: 0">{{ lease.mayAcquire === false ? TEXT.readOnlyAccess : TEXT.readOnly }}</p>
        <section class="panel" :aria-label="TEXT.alignment" :data-status="alignStatus" data-testid="alignment">
          <div class="panel-header">
            <h2>{{ TEXT.alignment }}</h2>
            <button type="button" class="small" :style="FOCUS" :disabled="alignStatus !== 'ready'" data-testid="seek-selection" @click="c.alignment.seekToSelection()">{{ TEXT.seekSelection }}</button>
          </div>
          <div v-if="alignStatus === 'error' || alignStatus === 'stale'" role="alert" class="note" :style="box(alignStatus === 'error' ? 'error' : 'warning')">
            {{ alignStatus === 'error' && alignError ? alignError.message : 'Die Audio-Zuordnung ist veraltet.' }}
            <button type="button" class="small" :style="FOCUS" @click="c.alignment.refresh()">{{ TEXT.retry }}</button>
          </div>
          <p role="group" :aria-label="TEXT.alignmentText" :ref="c.bindAlignmentRoot" class="alignment-text"
             @wheel.passive="followOn = false" @touchmove.passive="followOn = false" @pointerdown="followOn = false">
            <template v-for="seg in segments" :key="seg.start">
              <span v-if="seg.aligned" role="button" tabindex="0" :data-start="seg.start" :data-link="seg.linkId"
                    :aria-label="TEXT.wordAria + ': ' + seg.text" :aria-current="currentWord && currentWord.id === seg.linkId ? 'true' : null"
                    :style="{ ...FOCUS, ...styleFor(seg) }"
                    @click="c.alignment.seekToSegment(seg)" @keydown.enter.prevent="c.alignment.seekToSegment(seg)"
                    @keydown.space.prevent="c.alignment.seekToSegment(seg)">{{ seg.text }}</span>
              <span v-else :data-start="seg.start" :data-unaligned="seg.flagged || seg.unavailable ? 'true' : null"
                    :title="seg.flagged ? TEXT.unalignedMark : null" :style="styleFor(seg)">{{ seg.text }}</span>
            </template>
          </p>
          <div role="status" aria-live="polite" class="sr-only">{{ alignAnnouncement }}</div>
        </section>
      </div>

      <div class="editor-status" :data-state="saveState" data-testid="save-state"
           :style="saveState === 'error' || saveState === 'conflict' ? { color: 'var(--danger)' } : {}">
        <span role="status" aria-live="polite"><span class="status-dot" aria-hidden="true"></span>{{ saveMessage }}<template v-if="unsavedSuffix()"> · {{ TEXT.unsaved }}</template></span>
        <span class="editor-status-right">
          <span role="note">{{ resumeLabel }}</span>
          <button type="button" class="small" :style="FOCUS" :disabled="c.saver.resumePosition.value === null" data-testid="resume" @click="c.seekToResume()">{{ TEXT.resume }}</button>
        </span>
      </div>
      <div v-if="saveState === 'conflict'" class="editor-workflow" role="group" :aria-label="TEXT.conflictChoice" data-testid="conflict-choice">
        <p class="hint">{{ TEXT.conflictChoice }}</p>
        <div class="actions">
          <button type="button" class="small" :style="FOCUS" data-testid="keep-mine" @click="c.keepMine()">{{ TEXT.keepMine }}</button>
          <button type="button" class="small" :style="FOCUS" data-testid="take-server" @click="c.takeServer()">{{ TEXT.takeServer }}</button>
          <button type="button" class="small" :style="FOCUS" data-testid="compare" :aria-expanded="String(showDiff)" @click="showDiff = !showDiff">{{ TEXT.compare }}</button>
        </div>
        <ul v-if="showDiff" data-testid="diff" style="flex-basis: 100%; margin: 0; padding-left: 18px">
          <li v-for="(row, i) in (c.compare() || [])" :key="i" :data-kind="row.kind">{{ row.kind === 'mine' ? TEXT.mine + ': ' : (row.kind === 'server' ? TEXT.server + ': ' : '') }}{{ row.text }}</li>
        </ul>
      </div>
    </template>
  </section>

  <div v-if="ready" class="editor-aux">
    <section class="panel" :aria-label="TEXT.options">
      <div class="panel-header"><h2>{{ TEXT.mode }}</h2></div>
      <fieldset class="clinic-correction-modes" :aria-busy="modeStatus === 'saving'" :data-mode="mode" style="border: 0; padding: 0; margin: 0">
        <legend class="sr-only">{{ TEXT.mode }}</legend>
          <div role="radiogroup" :aria-label="TEXT.modeGroup" style="display: grid; gap: 8px">
          <label v-for="opt in MODES" :key="opt.id" class="inline-label" style="display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; align-items: start">
            <input type="radio" name="clinic-correction-view-mode" :value="opt.id" :checked="mode === opt.id"
                   :aria-describedby="'ccv-mode-' + opt.id" :style="FOCUS" @change="c.modes.setMode(opt.id)">
            <span>{{ opt.label }}</span>
            <small :id="'ccv-mode-' + opt.id" class="hint" style="grid-column: 2; margin: 0">{{ opt.hint }}</small>
          </label>
        </div>
        <label class="correction-backstep">
          <span>Rücksprung in Sekunden</span>
          <input type="number" min="0" step="1" required :value="backstepSeconds" :style="FOCUS"
                 aria-describedby="ccv-backstep-hint" @input="c.modes.setBackstepSeconds" @change="c.modes.setBackstepSeconds">
        </label>
        <p id="ccv-backstep-hint" class="hint">Gilt nach einer Tipp-Pause und beim erneuten Starten. 0 bedeutet kein Rücksprung.</p>
        <div v-if="modeStatus === 'error'" role="alert" class="note" :style="box('error')">
          {{ modeError && modeError.message }}
          <button type="button" class="small" :style="FOCUS" @click="c.modes.setMode(mode)">{{ TEXT.modeRetry }}</button>
        </div>
        <div role="status" aria-live="polite" class="sr-only">{{ modeAnnouncement }}</div>
      </fieldset>
    </section>
    <section class="panel">
      <CorrectionWaveform :key="'w' + loadGeneration + audio.revisionId" :editor="c.editor" :alignment="c.alignment" :client="c.client"
                          :dictation-id="dictationId" :recording-id="c.peaksKey()" :fetch-fn="c.peaksFetch" :seek="c.seek" :vue="c.vue" />
    </section>

  </div>

  <div v-if="ready" id="ccv-drawer" class="context-drawer" role="dialog" aria-modal="false" :aria-label="drawerTitle() || TEXT.ctxClosed" :inert="!drawer" :aria-hidden="drawer ? null : 'true'" :data-open="drawer ? 'true' : null"
       data-testid="context-drawer" @keydown.esc.stop.prevent="closeDrawer">
    <div class="dialoghead">
      <h2>{{ drawerTitle() }}</h2>
      <span class="tool-wrap" v-tip="{ label: TEXT.closeContext }">
        <button type="button" class="icon-button" :style="FOCUS" :aria-label="TEXT.closeContext" ref="drawerClose" data-testid="drawer-close" @click="closeDrawer"><UiIcon name="close" /></button>
      </span>
    </div>
    <div v-show="!drawer || drawer === 'assignment'" style="display: grid; gap: 14px; min-width: 0">
      <p class="hint" style="margin: 0">{{ lease.message }}</p>
      <dl class="keyvalues">
        <div><dt>{{ TEXT.dictatedBy }}</dt><dd>{{ dictator || TEXT.openAssignment }}</dd></div>
        <div><dt>{{ TEXT.pool }}</dt><dd>{{ pool || TEXT.noPool }}</dd></div>
        <div><dt>{{ TEXT.editing }}</dt><dd>{{ workerLabel() }}</dd></div>
      </dl>
    </div>
    <div v-show="!drawer || drawer === 'marks'" style="display: grid; gap: 20px; min-width: 0">
      <section class="panel">
        <AudioMarkers :key="'m' + loadGeneration + '-' + revisionGeneration + audio.revisionId" :editor="c.editor" :alignment="c.alignment" :client="c.client"
                      :dictation-id="dictationId" :get-position-ms="c.getPositionMs" :seek="c.seek" :vue="c.vue" />
      </section>
    </div>

    <div v-show="!drawer || drawer === 'options'" style="display: grid; gap: 20px; min-width: 0">
      <section class="panel" :aria-label="TEXT.options">
        <div class="panel-header"><h2>{{ TEXT.options }}</h2></div>
        <PlaybackGain v-if="gainState" :state="gainState.value" :controller="c.gain()" />
        <CorrectionAudioPanel v-if="canEdit && audio.status === 'ready'" :client="c.client" :dictation-id="dictationId"
            :lease="c.editor.leaseController" :position="c.getPositionMs" :visible="drawer === 'options'"
            @revision="c.adoptAudioRevision" />
        <div data-testid="repeat-transcription">
          <button type="button" class="button small" :style="FOCUS" :disabled="!c.repeat.eligible()"
                  data-testid="repeat-open" @click="c.repeat.open()">{{ REPEAT_TEXT.title }}</button>
          <p v-if="!c.repeat.eligible() && !repeatState.message" class="hint">{{ REPEAT_TEXT.unavailable }}</p>
          <div v-if="repeatState.confirming" role="group" :aria-label="REPEAT_TEXT.title">
            <p class="hint">{{ REPEAT_TEXT.explanation }}</p>
            <button type="button" class="button primary small" :style="FOCUS" :disabled="!c.repeat.eligible()"
                    data-testid="repeat-confirm" @click="c.repeat.submit()">{{ REPEAT_TEXT.confirm }}</button>
            <button type="button" class="button small" :style="FOCUS" @click="c.repeat.cancel()">Abbrechen</button>
          </div>
          <p v-if="repeatState.message" :role="repeatState.phase === 'error' ? 'alert' : 'status'"
             class="hint" data-testid="repeat-result" :data-state="repeatState.phase">{{ repeatState.message }}</p>
        </div>
      </section>
    </div>
  </div>
  <div role="status" aria-live="polite" class="sr-only">{{ editorAnnouncement }}</div>
</section>`,
};

/** Registered view. Re-keying the body on `dictationId` disposes all children of the previous dictation. */
export const CorrectionView = {
    name: 'ClinicCorrectionView',
    components: { CorrectionBody },
    props: BODY_PROPS,
    emits: ['close'],
    template: `<CorrectionBody :key="dictationId" :client="client" :dictation-id="dictationId" :current-user="currentUser"
                              :vue="vue" :recording-id="recordingId" :create-audio-context="createAudioContext"
        :dictator="dictator" :pool="pool" :open-mode="openMode" @close="$emit('close')">
          <template v-if="$slots['back-action']" #back-action="scope"><slot name="back-action" v-bind="scope"></slot></template>
        </CorrectionBody>`,
};

export function registerCorrectionView(app) {
    app.component('clinic-correction-view', CorrectionView);
    return app;
}
