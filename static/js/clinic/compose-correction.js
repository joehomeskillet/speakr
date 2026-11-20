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

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { useCorrectionEditor } from './correction-editor-ui.js';
import { LABELS as ALIGN_LABELS, createPlaybackHighlighter, useCorrectionAlignment } from './correction-alignment-ui.js';
import { CorrectionWaveform } from './correction-waveform-ui.js';
import { PlaybackTempo } from './playback-tempo-ui.js';
import { MODES, useCorrectionModes } from './correction-modes-ui.js';
import { useCorrectionSave } from './correction-save-ui.js';
import { AudioMarkers } from './audio-markers-ui.js';
import { PlaybackGain, usePlaybackGain } from './playback-gain-ui.js';
import { leaseActions } from './lease-ui.js';

export const COMPOSE_CORRECTION_VERSION = 1;

/** The upstream player (modules/composables/audioPlayer.js) defines no skip step, so the view uses 3 s. */
export const STEP_SECONDS = 3;

/** V8 editor contract (UI8-004): skip and rewind step in seconds. Used when the controller runs with contract 'v8'. */
export const STEP_SECONDS_V8 = 5;
/** Extra save passes close() may run after the first one while edits are still dirty. */
const CLOSE_EXTRA_SAVES = 2;

export const TEXT = Object.freeze({
    title: 'Korrektur',
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
    player: 'Wiedergabe',
    audio: 'Audio zum Diktat',
    alignment: 'Text und Audio',
    alignmentText: 'Transkript mit Audio-Zuordnung',
    wordAria: 'Wort abspielen',
    unalignedMark: 'ohne Audio',
    markers: 'Marken im Audio',
    lease: 'Bearbeitungsstatus',
    save: 'Speichern',
    saveSection: 'Speicherstand',
    resume: 'Zur gespeicherten Position springen',
    mode: 'Korrekturmodus',
    modeGroup: 'Wiedergabe beim Korrigieren',
    modeRetry: 'Erneut speichern',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
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

    const saver = useCorrectionSave({
        editor, client, dictationId, getPositionMs, vue: V || undefined,
        lease: createLeaseAdapter(editor, V),
        ...(opts.autosaveMs !== undefined ? { autosaveMs: opts.autosaveMs } : {}),
        ...(v8 ? { reconcileFirst: true } : {}),
        ...(opts.saveOptions || {}),
    });

    const resumeLabel = (api.computed)(() => {
        const ms = saver.resumePosition.value;
        if (ms === null) return 'Keine gespeicherte Position.';
        const total = Math.floor(ms / 1000);
        const pad = (n) => String(n).padStart(2, '0');
        return 'Letzte gespeicherte Position: ' + pad(Math.floor(total / 60)) + ':' + pad(total % 60);
    });

    // --- waveform peaks (V8 P06) ---------------------------------------------
    // The waveform module asks for a URL by recording id; the view answers it from the peaks route of the
    // audio revision the editor currently shows. No revision or a 404 reads as "no peaks" (honest fallback text).
    const peaksKey = () => editor.audio.value.revisionId || null;
    function peaksFetch(_url, init) {
        const revision = peaksKey();
        if (!revision) return Promise.resolve({ status: 404 });
        const url = '/api/clinic/v1/dictations/' + encodeURIComponent(dictationId)
            + '/audio-revisions/' + encodeURIComponent(revision) + '/peaks';
        return (opts.peaksFetch || ((u, i) => fetch(u, i)))(url, init);
    }

    // --- audio wiring -------------------------------------------------------
    function unbindAudio() {
        for (const [node, name, fn] of audioListeners.splice(0)) {
            if (typeof node.removeEventListener === 'function') node.removeEventListener(name, fn);
        }
        if (highlighter) { highlighter.dispose(); highlighter = null; }
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
        listen('ended', () => { if (gain) gain.stop(); });
        // Highlight follows the real media position (rAF while playing, events otherwise); it never touches focus or editor state.
        highlighter = createPlaybackHighlighter({ alignment, audio: el, root: alignmentRoot });
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
            await Promise.all([alignment.refresh(), saver.loadResume(), modes.load()]);
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
        return alignment.refresh();
    }

    if (api.watch) stops.push(api.watch(editor.textRevisionId, () => { syncRevision(); }));

    async function save() {
        const res = await saver.save();
        if (res && res.ok) await syncRevision();
        return res;
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
            await alignment.refresh();
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
            await alignment.refresh();
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

    // --- lifecycle ------------------------------------------------------------
    const leaseRun = { acquire: editor.acquire, release: editor.release, takeOver: editor.takeOver };
    const leaseButtons = () => leaseActions(editor.lease.value).filter((a) => typeof leaseRun[a.id] === 'function');

    async function start() {
        if (disposed) return null;
        modes.start();
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
        for (const stop of stops.splice(0)) if (typeof stop === 'function') stop();
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
        vue: V, api, editor, alignment, modes, saver, gain: () => gain,
        ready, loadGeneration, revisionGeneration, audioEl, gainState, closeMessage, resumeLabel,
        recordingId: () => opts.recordingId || editor.audio.value.recordingId || null,
        peaksKey, peaksFetch,
        stepSeconds: v8 ? STEP_SECONDS_V8 : STEP_SECONDS,
        leaseRun, leaseButtons,
        start, load, save, setText, reloadLatest, keepMine, takeServer, compare: () => saver.compare(), close, dispose, syncRevision,
        client: scoped, bindAudio, bindAlignmentRoot, alignmentNotice, onSync, onShortcut, onKeydown, onInput, onSelect, onTemplate, seekToResume, getPositionMs, seek,
        text: TEXT,
    };
}

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
};

/** Inner body: one instance per dictation. The outer view re-keys it, which disposes everything. */
export const CorrectionBody = {
    name: 'ClinicCorrectionBody',
    components: { CorrectionWaveform, AudioMarkers, PlaybackTempo, PlaybackGain },
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
        const showDiff = V.ref(false);
        const e = c.editor;
        const a = c.alignment;
        const s = c.saver;
        const m = c.modes;
        const styleFor = (seg) => {
            const word = a.currentWord.value;
            if (word && seg.linkId === word.id) return box('processing');
            if (seg.flagged) { const w = surfaceFor('warning'); return { background: w.background, color: w.color }; }
            return {};
        };
        return {
            c, FOCUS, MODES, box, styleFor, TEXT, showDiff,
            text: e.text, canEdit: e.canEdit, dirty: e.dirty, loading: e.loading, error: e.error, manual: e.manual,
            lease: e.lease, audio: e.audio, template: e.template, templates: e.templates, templatesError: e.templatesError,
            editorAnnouncement: e.announcement,
            alignStatus: a.status, alignError: a.error, segments: a.segments, currentWord: a.currentWord, alignAnnouncement: a.announcement,
            saveState: s.state, saveMessage: s.message,
            mode: m.mode, modeStatus: m.status, modeError: m.error, modeAnnouncement: m.announcement,
            ready: c.ready, loadGeneration: c.loadGeneration, revisionGeneration: c.revisionGeneration,
            audioEl: c.audioEl, gainState: c.gainState, closeMessage: c.closeMessage, resumeLabel: c.resumeLabel,
        };
    },
    template: `
<section class="clinic-correction-view" data-testid="clinic-correction-view" aria-labelledby="ccv-title" :aria-busy="loading ? 'true' : 'false'"
         @keydown="c.onShortcut($event)">
  <header class="flex flex-wrap items-center gap-2">
    <h1 id="ccv-title" class="text-lg font-semibold">{{ TEXT.title }}</h1>
    <span class="text-sm" style="color: var(--text-secondary)">{{ TEXT.dictation }} {{ dictationId }}</span>
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS" data-testid="close" @click="c.close()">{{ TEXT.close }}</button>
  </header>
  <p v-if="closeMessage" role="alert" class="mt-2 p-2 rounded" :style="box('error')">{{ closeMessage }}</p>
  <p class="sr-only" id="ccv-keys">{{ TEXT.keys }}</p>

  <div v-if="loading && !ready" role="status" aria-live="polite" data-testid="loading-state">{{ TEXT.loading }}</div>
  <div v-if="error" role="alert" class="mt-2 p-2 rounded" :style="box('error')" data-testid="load-error">
    {{ error.message }}
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS"
            @click="c.load({ discard: error.code === 'stale' })">{{ error.code === 'stale' ? TEXT.reload : TEXT.retry }}</button>
  </div>

  <div class="mt-2" role="group" :aria-label="TEXT.lease" data-testid="lease-bar">
    <p role="status" aria-live="polite" :style="{ color: lease.surface.color }">{{ lease.message }}</p>
    <button v-for="b in c.leaseButtons()" :key="b.id" type="button" class="px-3 py-2 border rounded mr-2" style="min-height: 2.75rem"
            :style="FOCUS" :disabled="b.disabled" :data-action="b.id" @click="c.leaseRun[b.id]()">{{ b.label }}</button>
  </div>

  <template v-if="ready">
    <div class="flex flex-wrap items-center gap-2 mt-3" role="toolbar" :aria-label="TEXT.toolbar" data-testid="toolbar">
      <label>{{ TEXT.template }}
        <select :style="FOCUS" :disabled="!canEdit" :value="template ? template.id : ''" @change="c.onTemplate($event)">
          <option value="">{{ TEXT.noTemplate }}</option>
          <option v-for="row in templates.items" :key="row.id" :value="row.id">{{ row.name }} (v{{ row.version }})</option>
        </select>
      </label>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS"
              :disabled="alignStatus !== 'ready'" data-testid="seek-selection" @click="c.alignment.seekToSelection()">{{ TEXT.seekSelection }}</button>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS"
              :disabled="loading || dirty" data-testid="reload" @click="c.load()">{{ TEXT.reload }}</button>
    </div>
    <p v-if="templatesError" role="note">{{ templatesError }}</p>
    <ul v-if="template && template.issues.length" role="note" aria-label="Hinweise zur Vorlage">
      <li v-for="issue in template.issues" :key="issue.code">{{ issue.message }}</li>
    </ul>

    <div class="grid gap-4 lg:grid-cols-2 mt-3">
      <div>
        <p v-if="manual" role="note">{{ TEXT.manual }}</p>
        <label class="block">{{ TEXT.text }}
          <textarea rows="14" class="w-full" :style="FOCUS" :value="text" :readonly="!canEdit" :aria-readonly="String(!canEdit)"
                    :aria-describedby="canEdit ? 'ccv-keys' : 'ccv-keys ccv-readonly'" data-testid="text"
                    @input="c.onInput($event)" @keydown="c.onKeydown($event)" @select="c.onSelect($event)"
                    @keyup="c.onSelect($event)" @mouseup="c.onSelect($event)"></textarea>
        </label>
        <p v-if="!canEdit && !loading" id="ccv-readonly">{{ TEXT.readOnly }}</p>
        <p v-if="dirty" role="note">{{ TEXT.unsaved }}</p>

        <section class="mt-3" :aria-label="TEXT.alignment" :data-status="alignStatus" data-testid="alignment">
          <h2 class="font-semibold">{{ TEXT.alignment }}</h2>
          <div v-if="alignStatus === 'error' || alignStatus === 'stale'" role="alert" class="p-2 rounded"
               :style="box(alignStatus === 'error' ? 'error' : 'warning')">
            {{ alignStatus === 'error' && alignError ? alignError.message : 'Die Audio-Zuordnung ist veraltet.' }}
            <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS" @click="c.alignment.refresh()">{{ TEXT.retry }}</button>
          </div>
          <p v-if="alignmentNotice" role="note" data-testid="alignment-notice">{{ alignmentNotice }}</p>
          <p role="group" :aria-label="TEXT.alignmentText" :ref="c.bindAlignmentRoot">
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
          <div role="status" aria-live="polite">{{ alignAnnouncement }}</div>
        </section>
      </div>

      <div>
        <section :aria-label="TEXT.player" data-testid="player-bar">
          <h2 class="font-semibold">{{ TEXT.player }}</h2>
          <audio controls preload="metadata" :ref="c.bindAudio" :src="audio.status === 'ready' ? audio.src : null" :aria-label="TEXT.audio"></audio>
          <p v-if="audio.status !== 'ready' && audio.status !== 'idle' && audio.status !== 'loading'" role="note">{{ audio.message }}</p>
          <PlaybackTempo v-if="audioEl" :audio="audioEl" :step-seconds="c.stepSeconds" :on-sync="c.onSync" />
          <PlaybackGain v-if="gainState" :state="gainState.value" :controller="c.gain()" />
          <fieldset class="clinic-correction-modes" :aria-busy="modeStatus === 'saving'" :data-mode="mode">
            <legend>{{ TEXT.mode }}</legend>
            <div role="radiogroup" :aria-label="TEXT.modeGroup">
              <label v-for="opt in MODES" :key="opt.id" class="block">
                <input type="radio" name="clinic-correction-view-mode" :value="opt.id" :checked="mode === opt.id"
                       :aria-describedby="'ccv-mode-' + opt.id" :style="FOCUS" @change="c.modes.setMode(opt.id)">
                <span>{{ opt.label }}</span>
                <small :id="'ccv-mode-' + opt.id">{{ opt.hint }}</small>
              </label>
            </div>
            <div v-if="modeStatus === 'error'" role="alert" :style="box('error')">
              {{ modeError && modeError.message }}
              <button type="button" :style="FOCUS" @click="c.modes.setMode(mode)">{{ TEXT.modeRetry }}</button>
            </div>
            <div role="status" aria-live="polite">{{ modeAnnouncement }}</div>
          </fieldset>
        </section>

        <CorrectionWaveform :key="'w' + loadGeneration" :editor="c.editor" :alignment="c.alignment" :client="c.client"
                            :dictation-id="dictationId" :recording-id="c.peaksKey()" :fetch-fn="c.peaksFetch" :vue="c.vue" />
      </div>
    </div>

    <AudioMarkers :key="'m' + loadGeneration + '-' + revisionGeneration" :editor="c.editor" :alignment="c.alignment" :client="c.client"
                  :dictation-id="dictationId" :get-position-ms="c.getPositionMs" :seek="c.seek" :vue="c.vue" />

    <section class="mt-3 p-2 rounded" :aria-label="TEXT.saveSection" :data-state="saveState" data-testid="save-state"
             :style="box(saveState === 'saved' ? 'success' : (saveState === 'unsaved' ? 'warning' : (saveState === 'saving' ? 'processing' : 'error')))">
      <p role="status" aria-live="polite">{{ saveMessage }}</p>
      <p role="note">{{ resumeLabel }}</p>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS"
              :disabled="saveState === 'saving' || saveState === 'saved'" data-testid="save" @click="c.save()">{{ TEXT.save }}</button>
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS"
              :disabled="c.saver.resumePosition.value === null" data-testid="resume" @click="c.seekToResume()">{{ TEXT.resume }}</button>
            <div v-if="saveState === 'conflict'" role="group" :aria-label="TEXT.conflictChoice" data-testid="conflict-choice">
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS" data-testid="keep-mine" @click="c.keepMine()">{{ TEXT.keepMine }}</button>
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS" data-testid="take-server" @click="c.takeServer()">{{ TEXT.takeServer }}</button>
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" :style="FOCUS" data-testid="compare" :aria-expanded="String(showDiff)" @click="showDiff = !showDiff">{{ TEXT.compare }}</button>
        <ul v-if="showDiff" data-testid="diff">
          <li v-for="(row, i) in (c.compare() || [])" :key="i" :data-kind="row.kind">{{ row.kind === 'mine' ? TEXT.mine + ': ' : (row.kind === 'server' ? TEXT.server + ': ' : '') }}{{ row.text }}</li>
        </ul>
      </div>
    </section>
  </template>
  <div role="status" aria-live="polite">{{ editorAnnouncement }}</div>
</section>`,
};

/** Registered view. Re-keying the body on `dictationId` disposes all children of the previous dictation. */
export const CorrectionView = {
    name: 'ClinicCorrectionView',
    components: { CorrectionBody },
    props: BODY_PROPS,
    emits: ['close'],
    template: `<CorrectionBody :key="dictationId" :client="client" :dictation-id="dictationId" :current-user="currentUser"
        :vue="vue" :recording-id="recordingId" :create-audio-context="createAudioContext" @close="$emit('close')" />`,
};

export function registerCorrectionView(app) {
    app.component('clinic-correction-view', CorrectionView);
    return app;
}
