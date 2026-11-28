import { describe, expect, it, vi } from 'vitest';
import { captureChord, dispatchBinding, useKeyBindings } from '../../../static/js/clinic/key-bindings-ui.js';

const endpoint = '/preferences/key-bindings';
const defaults = { play_pause: 'Alt+Shift+Space', rewind: 'Alt+Shift+ArrowLeft' };
const ok = (version, overrides) => ({ outcome: 'ok', data: { defaults, bindings: { ...defaults, ...overrides }, version } });
const problem = (code) => ({ outcome: 'validation', code });

function setup() {
    const hooks = {};
    const request = vi.fn().mockResolvedValueOnce(ok(0));
    const store = useKeyBindings({
        reactive: (value) => value,
        onMounted: (fn) => { hooks.mount = fn; },
        onBeforeUnmount: (fn) => { hooks.unmount = fn; },
        watch: (_, fn) => { hooks.switchAccount = fn; },
    }, { request }, () => 1);
    return { ...store, request, hooks };
}

describe('per-user key bindings', () => {
    it('exposes conflict and reserved errors on the affected row without adopting the rejected chord', async () => {
        const { hooks, state, set, request } = setup();
        await hooks.mount();
        request.mockResolvedValueOnce(problem('binding_conflict')).mockResolvedValueOnce(problem('reserved_chord'));
        expect(await set('play_pause', defaults.rewind)).toBe(false);
        expect(request).toHaveBeenLastCalledWith(endpoint, { method: 'PUT', body: {
            action: 'play_pause', chord: defaults.rewind, expected_version: 0,
        } });
        expect(state).toMatchObject({ bindings: defaults, errorAction: 'play_pause', error: expect.stringContaining('bereits vergeben') });
        expect(await set('play_pause', 'F5')).toBe(false);
        expect(state).toMatchObject({ bindings: defaults, errorAction: 'play_pause', error: expect.stringContaining('fest belegt') });
        expect(await set('play_pause', ' Shift + Ctrl + F ')).toBe(false);
        expect(request).toHaveBeenCalledTimes(3);
        expect(state.notice).toBe('');
    });

    it('applies saved chords and resets one without changing the other, then resets all', async () => {
        const { hooks, state, set, reset, request } = setup();
        await hooks.mount();
        request.mockResolvedValueOnce(ok(1, { play_pause: 'P' }));
        expect(await set('play_pause', 'P')).toBe(true);
        expect(state.bindings.play_pause).toBe('P');
        expect(state.notice).toBe('Tastenkürzel gespeichert.');
        request.mockResolvedValueOnce(ok(2, { play_pause: 'P', rewind: 'B' }));
        await set('rewind', 'B');
        request.mockResolvedValueOnce(problem('binding_conflict'));
        expect(await reset('play_pause')).toBe(false);
        expect(state).toMatchObject({ bindings: { play_pause: 'P', rewind: 'B' }, version: 2, errorAction: 'play_pause' });
        request.mockResolvedValueOnce(ok(3, { rewind: 'B' }));
        expect(await reset('play_pause')).toBe(true);
        expect(request).toHaveBeenLastCalledWith(`${endpoint}?action=play_pause&expected_version=2`, { method: 'DELETE' });
        expect(state.bindings).toEqual({ ...defaults, rewind: 'B' });
        request.mockResolvedValueOnce(ok(4));
        expect(await reset()).toBe(true);
        expect(request).toHaveBeenLastCalledWith(`${endpoint}?expected_version=3`, { method: 'DELETE' });
        expect(state.bindings).toEqual(defaults);
    });

    it('refreshes a stale version and requires an explicit retry', async () => {
        const { hooks, state, set, request } = setup();
        await hooks.mount();
        request.mockResolvedValueOnce(problem('stale_version')).mockResolvedValueOnce(ok(4, { rewind: 'B' }));
        expect(await set('play_pause', 'P')).toBe(false);
        expect(request).toHaveBeenLastCalledWith(endpoint);
        expect(state).toMatchObject({ bindings: { ...defaults, rewind: 'B' }, version: 4, errorAction: 'play_pause' });
    });

    it('ignores responses after an account switch or unmount', async () => {
        const { hooks, state, set, load, request } = setup();
        await hooks.mount();
        let respond;
        request.mockImplementationOnce(() => new Promise((resolve) => { respond = resolve; })).mockResolvedValueOnce(ok(0));
        const pending = set('play_pause', 'P');
        await hooks.switchAccount();
        respond(ok(1, { play_pause: 'P' }));
        expect(await pending).toBe(false);
        expect(state.bindings).toEqual(defaults);
        request.mockImplementationOnce(() => new Promise((resolve) => { respond = resolve; }));
        const lateLoad = load();
        hooks.unmount();
        respond(ok(2, { play_pause: 'B' }));
        expect(await lateLoad).toBe(false);
        expect(state.bindings).toEqual(defaults);
    });
});

it('captures canonical chords without trapping navigation or interpreting AltGr as Ctrl+Alt', () => {
    expect(captureChord({ key: 'p', altKey: true, ctrlKey: true, shiftKey: true, metaKey: true })).toBe('Ctrl+Alt+Shift+Meta+P');
    expect(captureChord({ key: ' ', altKey: true })).toBe('Alt+Space');
    expect(captureChord({ key: '!', code: 'Digit1', shiftKey: true })).toBe('Shift+1');
    expect(captureChord({ key: 'Tab' })).toBeNull();
    expect(captureChord({ key: 'p', getModifierState: () => true })).toBeNull();
});

it('dispatches only the configured chord once in the focused view without stealing typing or native controls', () => {
    const textarea = { tagName: 'TEXTAREA' };
    const button = { tagName: 'BUTTON' };
    const doc = { hasFocus: vi.fn(() => true) };
    const root = { ownerDocument: doc, contains: (target) => [textarea, button].includes(target) };
    const actions = { play_pause: vi.fn() };
    const dispatch = (changes, bindings = { play_pause: 'Alt+P' }, active = actions) => {
        const event = { target: textarea, key: 'p', altKey: true, preventDefault() { this.defaultPrevented = true; }, ...changes };
        return [dispatchBinding(event, root, bindings, active), event];
    };
    for (const changes of [{ target: {} }, { key: ' ' }, { repeat: true }, { isComposing: true }, { defaultPrevented: true },
        { getModifierState: (key) => key === 'AltGraph' }]) {
        expect(dispatch(changes)[0]).toBe(false);
    }
    expect(dispatch({}, undefined, {})[0]).toBe(false);
    expect(dispatch({ altKey: false }, { play_pause: 'P' })[0]).toBe(false);
    expect(dispatch({ key: ' ', altKey: false, target: button }, { play_pause: 'Space' })[0]).toBe(false);
    doc.hasFocus.mockReturnValueOnce(false);
    expect(dispatch({})[0]).toBe(false);
    const [handled, event] = dispatch({});
    expect(handled).toBe(true);
    expect(dispatchBinding(event, root, { play_pause: 'Alt+P' }, actions)).toBe(false);
    expect(actions.play_pause).toHaveBeenCalledOnce();
});
