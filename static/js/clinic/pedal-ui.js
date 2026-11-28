/**
 * Foot pedal support for recording and correction (PAR-OPS-001, UI-P06-06).
 *
 * Many USB foot pedals present themselves as a keyboard and send a configurable key (F-key,
 * media key). This module maps those keys and direct pedal presses to the active view's commands.
 * Only the WebHID adapter talks to compatible devices, and it only reads input reports.
 *
 * Contract
 *   - Commands are injected by the host: the same functions the on-screen controls use, so
 *     there is one execution path per action. A command flagged `mutating` additionally
 *     needs `canMutate()` to be true; the pedal never bypasses the lease or permission checks.
 *   - Active only between attach() and detach() and only while the page has focus. A key that is
 *     mapped is consumed (preventDefault) and never reaches the editor; keys that are not
 *     mapped are ignored. Text, selection, undo and focus are never touched.
 *   - Printable keys cannot be mapped (they would type text). Allowed: F2 to F24 without the
 *     browser-reserved F5, F11, F12, media keys, Pause and ScrollLock.
 *   - Settings (mapping, step seconds, auto-rewind) are per workstation in localStorage. They
 *     hold key codes and numbers only, no clinical data.
 *
 * Exports: ACTIONS, TEXT, STORAGE_KEY, DEFAULT_SETTINGS, isMappableKey(code), normalizeSettings(raw),
 * loadSettings(storage), saveSettings(settings, storage), createPedalController(options),
 * createKeyCapture(options), PedalKeyCapture (Vue options component).
 */

import { getPedalWebHid } from './pedal-webhid.js';

export const STORAGE_KEY = 'clinic.pedal.keyboard.v1';

export const TEXT = Object.freeze({
    active: 'Pedal: Tastaturmodus aktiv',
    none: 'Keine Pedalzuordnung',
    statusLabel: 'Pedalstatus',
    capturePrompt: 'Taste drücken …',
    captureStart: 'Pedaltaste erfassen',
    captureRejected: 'Diese Taste kann nicht zugeordnet werden, weil sie Text eingeben würde.',
    captured: 'Taste erfasst',
});

/** Playback actions a key can trigger. `hold`: press plays, release pauses (then optional auto-rewind). */
export const ACTIONS = Object.freeze(['playPause', 'hold', 'rewind', 'skip']);
const RECORDING_ACTIONS = ['playPause', 'hold', 'rewind', 'recordToggle', 'stop'];
export const CONFIGURABLE_ACTIONS = Object.freeze([...new Set([...ACTIONS, ...RECORDING_ACTIONS])]);

export const DEFAULT_SETTINGS = Object.freeze({
    mapping: Object.freeze({}),
    rewindSeconds: 3,
    skipSeconds: 3,
    autoRewindSeconds: 0,
});

const MAX_SECONDS = 60;
const RESERVED = new Set(['F5', 'F11', 'F12']);

/** Whitelist: only non-printable keys. Everything else (letters, digits, Space, Enter, ...) is rejected. */
export function isMappableKey(code) {
    if (typeof code !== 'string') return false;
    const f = /^F([1-9]|1\d|2[0-4])$/.exec(code);
    if (f) return f[1] !== '1' && !RESERVED.has(code);
    return /^Media[A-Za-z]+$/.test(code) || code === 'Pause' || code === 'ScrollLock';
}

const seconds = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= MAX_SECONDS ? v : fallback);

/** Drops anything invalid; never throws. */
export function normalizeSettings(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    const mapping = {};
    const given = src.mapping && typeof src.mapping === 'object' ? src.mapping : {};
    for (const [code, action] of Object.entries(given)) {
        if (isMappableKey(code) && CONFIGURABLE_ACTIONS.includes(action)) mapping[code] = action;
    }
    return {
        mapping,
        rewindSeconds: seconds(src.rewindSeconds, DEFAULT_SETTINGS.rewindSeconds),
        skipSeconds: seconds(src.skipSeconds, DEFAULT_SETTINGS.skipSeconds),
        autoRewindSeconds: seconds(src.autoRewindSeconds, DEFAULT_SETTINGS.autoRewindSeconds),
    };
}

function defaultStorage() {
    try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (_) { return null; }
}

export function loadSettings(storage) {
    const store = storage === undefined ? defaultStorage() : storage;
    try {
        const text = store ? store.getItem(STORAGE_KEY) : null;
        return normalizeSettings(text ? JSON.parse(text) : null);
    } catch (_) {
        return normalizeSettings(null);
    }
}

/** Returns the stored (normalized) settings or null when storage is unavailable. */
export function saveSettings(settings, storage) {
    const store = storage === undefined ? defaultStorage() : storage;
    const clean = normalizeSettings(settings);
    try {
        if (!store) return null;
        store.setItem(STORAGE_KEY, JSON.stringify(clean));
        return clean;
    } catch (_) {
        return null;
    }
}

/**
 * Pedal controller.
 * options: {
 *   commands: { playPause, play, pause, rewind(seconds), skip(seconds) } host functions (shared path),
 *   context?: 'recording'         recording uses recordToggle, stop and hold() returning a release function,
 *   onFocusLost?: () => void      release media started by a latched command,
 *   mutating?: string[]            command ids that change data (none of the four playback actions do),
 *   canMutate?: () => boolean      lease / permission check for mutating commands,
 *   settings?: object, storage?: Storage|null, target?: EventTarget (default document), vue?: {ref},
 *   webHid?: direct input adapter (defaults to the tab's shared connection)
 * }
 * Returns { attach, detach, setSettings, settings, status, active, handleKeyDown, handleKeyUp, releaseAll }.
 */
export function createPedalController(options) {
    const opts = options || {};
    const commands = opts.commands || {};
    const mutating = new Set(opts.mutating || []);
    const canMutate = typeof opts.canMutate === 'function' ? opts.canMutate : () => true;
    const target = opts.target || (typeof document !== 'undefined' ? document : null);
    const recording = opts.context === 'recording';
    const actions = recording ? RECORDING_ACTIONS : ACTIONS;
    const mkRef = (opts.vue && opts.vue.ref) || ((value) => ({ value }));
    let settings = normalizeSettings(opts.settings !== undefined ? opts.settings : loadSettings(opts.storage));
    const held = new Map();
    let attached = false;
    let unsubscribeHid;
    const status = mkRef('');
    const active = mkRef(false);

    const refreshStatus = () => {
        const mapped = Object.values(settings.mapping).some((action) => actions.includes(action));
        status.value = mapped ? TEXT.active : TEXT.none;
        active.value = attached && mapped;
    };

    /** The single gate every command passes: missing host function or failed lease check means no call. */
    function run(id, ...args) {
        const fn = commands[id];
        if (typeof fn !== 'function') return false;
        if (mutating.has(id) && !canMutate()) return false;
        return fn(...args);
    }

    const hasFocus = () => (typeof document === 'undefined' || typeof document.hasFocus !== 'function' ? true : document.hasFocus());

    function release(code) {
        const end = held.get(code);
        if (!held.delete(code)) return false;
        end();
        return true;
    }

    function press(source, action) {
        if (action === 'rewind') run('rewind', settings.rewindSeconds);
        else if (action === 'skip') run('skip', settings.skipSeconds);
        else if (action !== 'hold') run(action);
        else if (!held.has(source)) {
            if (recording) {
                const end = run('hold');
                if (typeof end === 'function') held.set(source, end);
            } else {
                held.set(source, () => {
                    run('pause');
                    if (settings.autoRewindSeconds > 0) run('rewind', settings.autoRewindSeconds);
                });
                run('play');
            }
        }
    }

    function handleDirectPedal(action, pressed) {
        const source = 'webhid:' + action;
        if (!pressed) release(source);
        else if (attached && actions.includes(action) && hasFocus()) press(source, action);
    }

    function handleKeyDown(event) {
        if (!attached || !event || event.ctrlKey || event.altKey || event.metaKey) return false;
        const action = settings.mapping[event.code];
        if (!actions.includes(action) || !hasFocus()) return false;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        if (!event.repeat) press(event.code, action); // a held pedal repeats keydown; one press is one command
        return true;
    }

    function handleKeyUp(event) {
        if (!attached || !event || !held.has(event.code)) return false;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        release(event.code);
        return true;
    }

    /** Focus lost while a hold key is down: the keyup would never arrive, so playback stops here. */
    function releaseAll() {
        for (const code of [...held.keys()]) release(code);
    }

    function focusLost() {
        releaseAll();
        opts.onFocusLost?.();
    }

    const doc = target?.ownerDocument || (typeof document !== 'undefined' ? document : null);
    const win = doc?.defaultView || (typeof window !== 'undefined' ? window : null);
    const events = [
        [target, 'keydown', handleKeyDown, true],
        [target, 'keyup', handleKeyUp, true],
        [win, 'blur', focusLost, false],
        [doc, 'visibilitychange', () => { if (doc.hidden) focusLost(); }, false],
    ];

    function attach() {
        if (attached) return;
        attached = true;
        for (const [node, name, fn, capture] of events) if (node && node.addEventListener) node.addEventListener(name, fn, capture);
        unsubscribeHid = (opts.webHid || getPedalWebHid()).subscribe({ onButton: handleDirectPedal });
        refreshStatus();
    }

    function detach() {
        if (!attached) return;
        focusLost();
        attached = false;
        unsubscribeHid();
        for (const [node, name, fn, capture] of events) if (node && node.removeEventListener) node.removeEventListener(name, fn, capture);
        refreshStatus();
    }

    function setSettings(next) {
        settings = normalizeSettings(next);
        saveSettings(settings, opts.storage);
        for (const code of [...held.keys()]) if (isMappableKey(code) && settings.mapping[code] !== 'hold') release(code);
        refreshStatus();
        return settings;
    }

    refreshStatus();
    return {
        attach, detach, setSettings, handleKeyDown, handleKeyUp, releaseAll,
        settings: () => settings, status, active, text: TEXT,
    };
}

/**
 * Key capture for the settings page. start() makes the next key press report its code through
 * onCapture; printable or reserved keys call onReject and are not reported. The captured key is
 * consumed so it does not type or trigger anything. Esc cancels.
 */
export function createKeyCapture(options) {
    const opts = options || {};
    const target = opts.target || (typeof document !== 'undefined' ? document : null);
    const mkRef = (opts.vue && opts.vue.ref) || ((value) => ({ value }));
    const capturing = mkRef(false);
    const message = mkRef('');

    function onKey(event) {
        if (!capturing.value || !event) return;
        if (event.code === 'Escape') { stop(); return; }
        if (typeof event.preventDefault === 'function') event.preventDefault();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        if (!isMappableKey(event.code)) {
            message.value = TEXT.captureRejected;
            if (typeof opts.onReject === 'function') opts.onReject(event.code);
            return;
        }
        const code = event.code;
        stop();
        message.value = TEXT.captured + ': ' + code;
        if (typeof opts.onCapture === 'function') opts.onCapture(code);
    }

    function start() {
        if (capturing.value) return;
        capturing.value = true;
        message.value = TEXT.capturePrompt;
        if (target && target.addEventListener) target.addEventListener('keydown', onKey, true);
    }

    function stop() {
        capturing.value = false;
        if (target && target.removeEventListener) target.removeEventListener('keydown', onKey, true);
    }

    return { start, stop, capturing, message, text: TEXT };
}

/** Vue component for the settings page: <pedal-key-capture @capture="code => ..." />. */
export const PedalKeyCapture = {
    name: 'PedalKeyCapture',
    emits: ['capture'],
    setup(_props, ctx) {
        const V = globalThis.Vue;
        const capture = createKeyCapture({ vue: V, onCapture: (code) => ctx.emit('capture', code) });
        return { capture, capturing: capture.capturing, message: capture.message, TEXT };
    },
    beforeUnmount() { this.capture.stop(); },
    template: `<span class="v8-page clinic-pedal-capture stack">
  <button class="button" type="button" :aria-pressed="String(capturing)" @click="capturing ? capture.stop() : capture.start()">{{ capturing ? TEXT.capturePrompt : TEXT.captureStart }}</button>
  <span class="hint" role="status" aria-live="polite">{{ message }}</span>
</span>`,
};
