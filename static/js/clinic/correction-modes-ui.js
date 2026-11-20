/**
 * The two correction playback modes of the web editor (PAR-COR-005, owner decision W6).
 *
 * Both modes are offered per user. The default is "Auf zu hörendes Wort zeigen"
 * (id `stop_on_typing`): playback pauses on the first key press and, after the
 * idle time, resumes `rewindMs` before the pause position (never before 0), so
 * the cursor points at the word still to be heard. The second mode
 * (id `type_while_playing`, "Beim Tippen weiterlaufen") never touches playback.
 * Mode ids, 2000 ms rewind and 1500 ms idle time mirror src/clinic/correction_modes.py;
 * the rewind is in milliseconds only (no word-count rewind without stored anchors).
 * The mode is saved per user through the injected api-client (`PREFERENCE_PATH`);
 * a failed or refused save keeps the mode for this session and shows a message.
 *
 * Persistence contract (server side not yet built; follow-up WP needs route, authz, audit):
 *   GET  /api/clinic/v1/preferences/correction-mode  -> 200 { mode: <mode id> | null }
 *   PUT  /api/clinic/v1/preferences/correction-mode  body { mode: <mode id> } -> 200
 * until the route exists the server answers 404/405; the client then treats persistence
 * as unavailable (code `unsupported`, status 'local'): the mode applies to this session
 * only and a neutral note is shown instead of an error alert.
 * Clinical text and audio never touch browser storage; colours come from tokens.js.
 *
 * Exports (consumed by w212, w286):
 *   MODES, DEFAULT_MODE, LABELS, PREFERENCE_PATH
 *   useCorrectionModes({ alignment?, audio, client?, rewindMs?, resumeIdleMs?, tickMs?,
 *                        now?, timer?, persist?, vue? })
 *     `audio` adapter: { play(), pause(), seek(seconds), position() -> seconds, isPlaying() -> bool }.
 *     `alignment` is the useCorrectionAlignment() result of w150 (selection is forwarded).
 *     state refs  mode, status ('idle'|'saving'|'error'|'local'), error, waiting, announcement, options
 *     methods     load(), setMode(mode), onKey(), onSelectionChange(start, end), tick(),
 *                 start(), stop(), dispose()
 *     setMode / load return { ok, code?, message }; codes: invalid, denied, stale, unsupported, failed.
 *   CorrectionModes: Vue options object, props audio, alignment, client, vue.
 *   registerCorrectionModes(app): app.component('clinic-correction-modes', ...).
 *
 * Keyboard: the modes are native radio buttons in a labelled group (Arrow keys
 * change the mode, Tab leaves the group); the status line is a polite live region.
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const MODE_STOP_ON_TYPING = 'stop_on_typing';
export const MODE_TYPE_WHILE_PLAYING = 'type_while_playing';
export const DEFAULT_MODE = MODE_STOP_ON_TYPING;
export const PREFERENCE_PATH = '/preferences/correction-mode';

export const MODES = Object.freeze([
    Object.freeze({
        id: MODE_STOP_ON_TYPING,
        label: 'Auf zu hörendes Wort zeigen',
        hint: 'Die Wiedergabe hält beim Tippen an und springt danach zurück.',
    }),
    Object.freeze({
        id: MODE_TYPE_WHILE_PLAYING,
        label: 'Beim Tippen weiterlaufen',
        hint: 'Die Wiedergabe läuft beim Korrigieren unverändert weiter.',
    }),
]);

export const LABELS = Object.freeze({
    title: 'Korrekturmodus',
    group: 'Wiedergabe beim Korrigieren',
    saving: 'Modus wird gespeichert …',
    saved: 'Modus gespeichert.',
    waiting: 'Wiedergabe pausiert, setzt nach kurzer Pause fort.',
    invalid: 'Unbekannter Korrekturmodus.',
    denied: 'Keine Berechtigung, den Modus zu speichern. Er gilt nur für diese Sitzung.',
    stale: 'Die gespeicherte Einstellung ist veraltet. Der Modus gilt nur für diese Sitzung.',
    unsupported: 'Das Speichern pro Benutzer ist noch nicht verfügbar. Der Modus gilt nur für diese Sitzung.',
    failed: 'Der Modus konnte nicht gespeichert werden. Er gilt nur für diese Sitzung.',
    retry: 'Erneut speichern',
});

const DEFAULTS = Object.freeze({ rewindMs: 2000, resumeIdleMs: 1500, tickMs: 250 });
let nextViewId = 0;

function isMode(value) { return MODES.some((m) => m.id === value); }

function fail(code) { return { ok: false, code, message: LABELS[code] || LABELS.failed }; }

function errorFor(res) {
    if (res.outcome === 'permission') return fail('denied');
    if (res.outcome === 'conflict') return fail('stale');
    if (res.status === 404 || res.status === 405) return fail('unsupported');
    return fail('failed');
}

function positiveInt(value, fallback, min) {
    return Number.isInteger(value) && value >= min ? value : fallback;
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    return { ref, shallow };
}

/** @param {object} options see the header comment */
export function useCorrectionModes(options) {
    const opts = options || {};
    const audio = opts.audio;
    const alignment = opts.alignment || null;
    const client = opts.client || createClinicApiClient();
    const refs = makeRefs(opts.vue);
    const rewindMs = positiveInt(opts.rewindMs, DEFAULTS.rewindMs, 0);
    const resumeIdleMs = positiveInt(opts.resumeIdleMs, DEFAULTS.resumeIdleMs, 1);
    const tickMs = positiveInt(opts.tickMs, DEFAULTS.tickMs, 1);
    const now = typeof opts.now === 'function' ? opts.now : () => Math.round(globalThis.performance.now());
    const timer = opts.timer || { set: (fn, ms) => globalThis.setInterval(fn, ms), clear: (id) => globalThis.clearInterval(id) };
    const persist = opts.persist !== false;

    const mode = refs.ref(DEFAULT_MODE);
    const status = refs.ref('idle');
    const error = refs.shallow(null);
    const waiting = refs.ref(false);
    const announcement = refs.ref('');

    // Engine state, same transitions as CorrectionModeEngine.
    let lastKeyMs = null;
    let pendingResume = false;
    let handle = null;
    let disposed = false;
    let version = 0;

    function say(message) { announcement.value = message; }

    function positionMs() { return Math.max(0, Math.round(audio.position() * 1000)); }

    function clearPending() {
        pendingResume = false;
        lastKeyMs = null;
        waiting.value = false;
    }

    /** A key press in the editor. Stop mode: pause playback once and wait for idle time. */
    function onKey() {
        if (disposed || mode.value !== MODE_STOP_ON_TYPING) return { kind: 'none' };
        const t = now();
        if (!audio.isPlaying() && !pendingResume) return { kind: 'none' };
        if (lastKeyMs === null) {
            lastKeyMs = t;
            pendingResume = true;
            audio.pause();
            waiting.value = true;
            say(LABELS.waiting);
            return { kind: 'pause' };
        }
        lastKeyMs = Math.max(t, lastKeyMs);
        return { kind: 'none' };
    }

    /** Periodic check. After the idle time, rewind (not before 0) and resume. */
    function tick() {
        if (disposed || mode.value !== MODE_STOP_ON_TYPING || !pendingResume) return { kind: 'none' };
        if (lastKeyMs === null) {
            clearPending();
            return { kind: 'none' };
        }
        if (now() - lastKeyMs < resumeIdleMs) return { kind: 'none' };
        const target = Math.max(0, positionMs() - rewindMs);
        clearPending();
        audio.seek(target / 1000);
        audio.play();
        say('');
        return { kind: 'resume', seekToMs: target };
    }

    /** The editor selection moved. It is forwarded to the alignment; playback is not touched. */
    function onSelectionChange(start, end) {
        if (disposed || !alignment || typeof alignment.setSelection !== 'function') return null;
        return alignment.setSelection(start, end);
    }

    /** Persistence missing on the server is a neutral note ('local'); real failures raise the alert. */
    function report(problem) {
        if (problem.code === 'unsupported') {
            status.value = 'local';
            error.value = null;
        } else {
            status.value = 'error';
            error.value = problem;
        }
        say(problem.message);
        return problem;
    }

    async function save(next) {
        const mine = ++version;
        status.value = 'saving';
        say(LABELS.saving);
        const res = await client.request(PREFERENCE_PATH, { method: 'PUT', body: { mode: next } });
        if (mine !== version || disposed || res.outcome === 'cancelled') return { ok: true, message: '' };
        if (res.outcome !== 'ok') {
            return report(errorFor(res));
        }
        status.value = 'idle';
        error.value = null;
        say(LABELS.saved);
        return { ok: true, message: LABELS.saved };
    }

    /** Switch the mode, also during playback. A pending resume is cancelled and playback continues. */
    async function setMode(next) {
        if (disposed) return fail('failed');
        if (!isMode(next)) return fail('invalid');
        const wasWaiting = pendingResume;
        mode.value = next;
        clearPending();
        if (wasWaiting) audio.play();
        return persist ? save(next) : { ok: true, message: '' };
    }

    /** Read the saved mode of this user; the default stays when nothing valid is stored. */
    async function load() {
        if (disposed) return fail('failed');
        const mine = ++version;
        const res = await client.request(PREFERENCE_PATH);
        if (mine !== version || disposed || res.outcome === 'cancelled') return { ok: true, message: '' };
        if (res.outcome !== 'ok') {
            return report(errorFor(res));
        }
        const stored = res.data && res.data.mode;
        if (isMode(stored)) mode.value = stored;
        status.value = 'idle';
        error.value = null;
        return { ok: true, message: '' };
    }

    function start() {
        if (disposed || handle !== null) return;
        handle = timer.set(tick, tickMs);
    }

    function stop() {
        if (handle === null) return;
        timer.clear(handle);
        handle = null;
    }

    function dispose() {
        if (disposed) return;
        stop();
        disposed = true;
        version += 1;
        clearPending();
    }

    return {
        mode, status, error, waiting, announcement,
        options: MODES,
        load, setMode, onKey, onSelectionChange, tick, start, stop, dispose,
    };
}

/** Vue component. Props: audio (adapter), alignment, client, vue. Emits nothing; the parent calls onKey/onSelectionChange. */
export const CorrectionModes = {
    name: 'ClinicCorrectionModes',
    props: {
        audio: { type: Object, required: true },
        alignment: { type: Object, default: null },
        client: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const modes = useCorrectionModes({
            audio: props.audio, alignment: props.alignment || undefined,
            client: props.client || undefined, vue: props.vue || undefined,
        });
        if (V && typeof V.onMounted === 'function') {
            V.onMounted(() => { modes.load(); modes.start(); });
        }
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(modes.dispose);
        const problem = surfaceFor('error');
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        const problemStyle = { background: problem.background, color: problem.color, border: '1px solid ' + problem.border };
        function choose(id) { return modes.setMode(id); }
        const groupId = 'clinic-correction-mode-' + (++nextViewId);
        return { ...modes, LABELS, focusStyle, problemStyle, choose, groupId };
    },
    template: `
<fieldset class="clinic-correction-modes v8-page" :aria-busy="status === 'saving'" :data-mode="mode">
  <legend>{{ LABELS.title }}</legend>
  <div role="radiogroup" :aria-label="LABELS.group">
    <label v-for="opt in options" :key="opt.id" class="clinic-correction-mode">
      <input type="radio" :name="groupId" :value="opt.id" :checked="mode === opt.id"
        :aria-describedby="groupId + '-' + opt.id" :style="focusStyle" @change="choose(opt.id)">
      <span>{{ opt.label }}</span>
      <small :id="groupId + '-' + opt.id">{{ opt.hint }}</small>
    </label>
  </div>
  <div v-if="status === 'error'" class="note" role="alert" :style="problemStyle">
    {{ error && error.message }}
    <button type="button" class="small" :style="focusStyle" @click="choose(mode)">{{ LABELS.retry }}</button>
  </div>
  <div class="clinic-constituent-status" role="status" aria-live="polite">{{ status === 'error' ? '' : announcement }}</div>
</fieldset>`,
};

export function registerCorrectionModes(app) {
    app.component('clinic-correction-modes', CorrectionModes);
    return app;
}
