import { isControlTarget, isTypingTarget, keyToken } from '../modules/utils/keyboard-shortcuts.js';

export const KEY_BINDING_SCOPE = 'Tastenkürzel gelten nur im aktiven Browserfenster. Systemweite Tastenkürzel ausserhalb des Browsers bleiben offen (WG-OPS-003).';

export const KEY_BINDING_LABELS = Object.freeze({
    record_toggle: 'Aufnahme starten, pausieren oder fortsetzen',
    play_pause: 'Wiedergabe / Pause',
    stop: 'Stopp',
    rewind: 'Zurückspringen',
    forward: 'Vorspringen',
    speed_up: 'Schneller abspielen',
    speed_down: 'Langsamer abspielen',
    undo: 'Rückgängig',
    redo: 'Wiederholen',
    insert_marker: 'Audiomarke setzen',
    next_marker: 'Nächste Audiomarke',
    previous_marker: 'Vorherige Audiomarke',
    finish: 'Aufnahme senden',
});

const DEFAULTS = Object.freeze({
    record_toggle: 'Alt+Shift+R', play_pause: 'Alt+Shift+Space', stop: 'Alt+Shift+S',
    rewind: 'Alt+Shift+ArrowLeft', forward: 'Alt+Shift+ArrowRight',
    speed_up: 'Alt+Shift+ArrowUp', speed_down: 'Alt+Shift+ArrowDown',
    undo: 'Alt+Shift+Z', redo: 'Alt+Shift+Y', insert_marker: 'Alt+Shift+M',
    next_marker: 'Alt+Shift+PageDown', previous_marker: 'Alt+Shift+PageUp', finish: 'Alt+Shift+Enter',
});
const ENDPOINT = '/preferences/key-bindings';
const MODIFIERS = ['Ctrl', 'Alt', 'Shift', 'Meta'];
const RESERVED = new Set([
    'f1', 'tab', 'shift+tab', 'escape',
    ...['ctrl', 'meta'].flatMap((mod) => ['a', 'c', 'f', 'h', 'l', 'n', 'q', 'r', 's', 't', 'v', 'w', 'x', 'y', 'z',
        'enter', 'shift+f', 'shift+z', 'shift+t', 'shift+n', 'tab', 'shift+tab'].map((key) => `${mod}+${key}`)),
]);
const MESSAGES = {
    binding_conflict: 'Dieses Tastenkürzel ist bereits vergeben. Wählen Sie ein anderes.',
    reserved_chord: 'Dieses Tastenkürzel ist fest belegt. Wählen Sie ein anderes.',
    bad_chord: 'Dieses Tastenkürzel ist ungültig. Geben Sie zum Beispiel Alt+Shift+P ein.',
    stale_version: 'Die Tastenkürzel wurden inzwischen geändert. Versuchen Sie es erneut.',
};
const NATIVE_KEYS = /^(?:Space|Enter|Escape|Tab|Arrow\w+|PageUp|PageDown|Home|End|Delete|Backspace)$/;

function reserved(chord) {
    const parts = chord.toLowerCase().split('+').map((p) => p.trim());
    const key = parts.pop();
    if (new Set(parts).size !== parts.length || parts.some((p) => !MODIFIERS.some((mod) => mod.toLowerCase() === p))) return false;
    return RESERVED.has([...MODIFIERS.map((mod) => mod.toLowerCase()).filter((mod) => parts.includes(mod)), key === 'esc' ? 'escape' : key].join('+'));
}

export function captureChord(event) {
    if (event.isComposing || event.keyCode === 229 || event.repeat || event.getModifierState?.('AltGraph')) return null;
    const token = keyToken(event);
    const shiftedDigit = event.shiftKey && /^Digit[0-9]$/.test(event.code) && token?.length === 1 && !/^[a-z0-9]$/i.test(token);
    const key = shiftedDigit ? event.code.slice(-1) : token;
    if (!key) return null;
    const mods = [event.ctrlKey, event.altKey, event.shiftKey, event.metaKey];
    if (!mods.some(Boolean) && (key === 'Tab' || key === 'Escape')) return null;
    return [...MODIFIERS.filter((_, i) => mods[i]), /^(?:[a-z]|f\d+)$/i.test(key) ? key.toUpperCase() : key].join('+');
}

export function scopedKeyEvent(event, root) {
    const doc = root?.ownerDocument;
    return Boolean(doc?.hasFocus() && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229
        && !event.repeat && !event.getModifierState?.('AltGraph') && event.target
        && (root.contains(event.target) || (event.target === doc.body && (!doc.activeElement || doc.activeElement === doc.body))));
}

export function dispatchBinding(event, root, bindings, actions) {
    if (!scopedKeyEvent(event, root)) return false;
    const chord = captureChord(event);
    if (!chord) return false;
    const target = event.target;
    const commandModifier = event.altKey || event.ctrlKey || event.metaKey;
    const key = keyToken(event);
    if (isTypingTarget(target) && !commandModifier && !/^F\d+$/.test(key)) return false;
    if (!commandModifier && NATIVE_KEYS.test(key)
        && (isControlTarget(target) || target.closest?.('a, button, summary, audio, video'))) return false;
    const action = Object.keys(actions).find((id) => bindings[id] === chord);
    if (!action) return false;
    event.preventDefault();
    actions[action]();
    return true;
}

export function useKeyBindings(Vue, client, accountId) {
    const initial = () => ({
        bindings: DEFAULTS, defaults: DEFAULTS, version: null, busy: false,
        error: '', errorAction: null, notice: '', noticeAction: null,
    });
    const state = Vue.reactive(initial());
    let generation = 0;
    let mounted = false;

    function adopt(response) {
        const data = response?.data;
        if (response?.outcome !== 'ok' || !data?.bindings || !data.defaults || !Number.isInteger(data.version)) return false;
        Object.assign(state, { bindings: data.bindings, defaults: data.defaults, version: data.version });
        return true;
    }

    async function load() {
        if (!mounted || !client?.request || state.busy) return false;
        const seen = generation;
        Object.assign(state, { busy: true, error: '', errorAction: null, notice: '' });
        const response = await client.request(ENDPOINT);
        if (seen !== generation) return false;
        state.busy = false;
        if (adopt(response)) return true;
        state.error = 'Tastenkürzel konnten nicht geladen werden. Versuchen Sie es erneut.';
        return false;
    }

    async function change(action, chord, method) {
        if (!mounted || state.busy || state.version === null) return false;
        Object.assign(state, { error: '', errorAction: null, notice: '' });
        const error = (message) => Object.assign(state, { error: message, errorAction: action ?? null });
        if (method === 'PUT' && (reserved(chord) || /^pedal:/i.test(chord.trim()))) {
            error(reserved(chord) ? MESSAGES.reserved_chord : MESSAGES.bad_chord);
            return false;
        }
        const seen = generation;
        state.busy = true;
        const query = new URLSearchParams(action == null ? {} : { action });
        query.set('expected_version', state.version);
        const response = method === 'PUT'
            ? await client.request(ENDPOINT, { method, body: { action, chord, expected_version: state.version } })
            : await client.request(`${ENDPOINT}?${query}`, { method });
        if (seen !== generation) return false;
        state.busy = false;
        if (adopt(response)) {
            state.noticeAction = action ?? null;
            state.notice = method === 'PUT' ? 'Tastenkürzel gespeichert.' : action == null
                ? 'Alle Tastenkürzel wurden zurückgesetzt.' : 'Tastenkürzel zurückgesetzt.';
            return true;
        }
        if (response?.code === 'stale_version') {
            state.version = null;
            if (!await load() || seen !== generation) return false;
        }
        error(MESSAGES[response?.code] || 'Tastenkürzel konnte nicht geändert werden. Versuchen Sie es erneut.');
        return false;
    }

    Vue.onMounted?.(() => { mounted = true; return load(); });
    Vue.onBeforeUnmount?.(() => { mounted = false; generation += 1; });
    if (accountId) Vue.watch?.(accountId, () => {
        generation += 1;
        Object.assign(state, initial());
        return load();
    }, { flush: 'sync' });
    return { state, load, set: (action, chord) => change(action, chord, 'PUT'), reset: (action) => change(action, null, 'DELETE') };
}
