import { describe, expect, it, vi } from 'vitest';

import {
    ACTIONS, STORAGE_KEY, TEXT, createKeyCapture, createPedalController, isMappableKey, loadSettings, normalizeSettings,
} from '../../../static/js/clinic/pedal-ui.js';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';
import { usePlaybackTempo } from '../../../static/js/clinic/playback-tempo-ui.js';

// Vitest runs in the node environment (no DOM): plain events carry the fields the pedal reads.
const key = (type, code, extra = {}) => Object.assign(new Event(type, { cancelable: true }), { code, repeat: false, ...extra });

function memoryStorage(initial = {}) {
    const data = { ...initial };
    return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

function setup(settings, extra = {}) {
    const target = new EventTarget();
    const commands = { play: vi.fn(), pause: vi.fn(), playPause: vi.fn(), rewind: vi.fn(), skip: vi.fn() };
    const pedal = createPedalController({ commands, settings, target, storage: memoryStorage(), ...extra });
    return { target, commands, pedal };
}

const fire = (target, e) => { target.dispatchEvent(e); return e; };

describe('pedal mapper', () => {
    it('rejects printable and reserved keys, accepts F-keys and media keys', () => {
        for (const code of ['KeyA', 'Digit1', 'Space', 'Enter', 'Tab', 'Backspace', 'ArrowLeft', 'Numpad5', 'Comma', 'F1', 'F5', 'F11', 'F12', '', undefined, 7]) {
            expect(isMappableKey(code)).toBe(false);
        }
        for (const code of ['F2', 'F9', 'F13', 'F24', 'MediaPlayPause', 'MediaTrackNext', 'Pause', 'ScrollLock']) {
            expect(isMappableKey(code)).toBe(true);
        }
        expect(isMappableKey('F25')).toBe(false);
    });

    it('normalizeSettings drops printable keys, unknown actions and bad numbers', () => {
        const s = normalizeSettings({
            mapping: { KeyA: 'playPause', F9: 'rewind', F10: 'bogus' }, rewindSeconds: -1, skipSeconds: 'x', autoRewindSeconds: 2,
        });
        expect(s.mapping).toEqual({ F9: 'rewind' });
        expect(s.rewindSeconds).toBe(3);
        expect(s.skipSeconds).toBe(3);
        expect(s.autoRewindSeconds).toBe(2);
        expect(ACTIONS).toEqual(['playPause', 'hold', 'rewind', 'skip']);
    });

    it('settings persist per workstation and ignore corrupt storage', () => {
        const storage = memoryStorage();
        const { pedal } = setup({}, { storage });
        pedal.setSettings({ mapping: { F9: 'playPause' }, rewindSeconds: 4 });
        expect(JSON.parse(storage.data[STORAGE_KEY])).toMatchObject({ mapping: { F9: 'playPause' }, rewindSeconds: 4 });
        expect(loadSettings(storage).mapping).toEqual({ F9: 'playPause' });
        expect(loadSettings(memoryStorage({ [STORAGE_KEY]: '{broken' })).mapping).toEqual({});
        expect(loadSettings(null).mapping).toEqual({});
    });
});

describe('pedal commands', () => {
    it('a mapped key calls the injected command, consumes the event, and repeat does not re-fire', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'playPause', F10: 'rewind', F11: 'skip', F8: 'skip' }, rewindSeconds: 4, skipSeconds: 6 });
        pedal.attach();
        expect(fire(target, key('keydown', 'F9')).defaultPrevented).toBe(true);
        expect(commands.playPause).toHaveBeenCalledTimes(1);
        fire(target, key('keydown', 'F9', { repeat: true }));
        expect(commands.playPause).toHaveBeenCalledTimes(1);
        fire(target, key('keydown', 'F10'));
        expect(commands.rewind).toHaveBeenCalledWith(4);
        fire(target, key('keydown', 'F8'));
        expect(commands.skip).toHaveBeenCalledWith(6);
    });

    it('unmapped keys and modified keys are ignored and not consumed', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'playPause' } });
        pedal.attach();
        expect(fire(target, key('keydown', 'F10')).defaultPrevented).toBe(false);
        expect(fire(target, key('keydown', 'KeyA')).defaultPrevented).toBe(false);
        expect(fire(target, key('keydown', 'F9', { ctrlKey: true })).defaultPrevented).toBe(false);
        expect(Object.values(commands).every((c) => c.mock.calls.length === 0)).toBe(true);
    });

    it('hold mode: press plays, release pauses, optional auto-rewind after release', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'hold' }, autoRewindSeconds: 2 });
        pedal.attach();
        fire(target, key('keydown', 'F9'));
        fire(target, key('keydown', 'F9', { repeat: true }));
        expect(commands.play).toHaveBeenCalledTimes(1);
        expect(commands.pause).not.toHaveBeenCalled();
        fire(target, key('keyup', 'F9'));
        expect(commands.pause).toHaveBeenCalledTimes(1);
        expect(commands.rewind).toHaveBeenCalledWith(2);
        fire(target, key('keyup', 'F9'));
        expect(commands.pause).toHaveBeenCalledTimes(1);
    });

    it('hold mode without auto-rewind only pauses; detach while held pauses', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'hold' } });
        pedal.attach();
        fire(target, key('keydown', 'F9'));
        fire(target, key('keyup', 'F9'));
        expect(commands.rewind).not.toHaveBeenCalled();
        fire(target, key('keydown', 'F9'));
        pedal.detach();
        expect(commands.pause).toHaveBeenCalledTimes(2);
    });

    it('is inactive before attach and after detach', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'playPause' } });
        fire(target, key('keydown', 'F9'));
        pedal.attach();
        pedal.detach();
        expect(fire(target, key('keydown', 'F9')).defaultPrevented).toBe(false);
        expect(commands.playPause).not.toHaveBeenCalled();
    });

    it('is inactive while the page has no focus', () => {
        const { target, commands, pedal } = setup({ mapping: { F9: 'playPause' } });
        pedal.attach();
        globalThis.document = { hasFocus: () => false };
        try { fire(target, key('keydown', 'F9')); } finally { delete globalThis.document; }
        expect(commands.playPause).not.toHaveBeenCalled();
        fire(target, key('keydown', 'F9'));
        expect(commands.playPause).toHaveBeenCalledTimes(1);
    });

    it('mutating commands need canMutate: a lost lease blocks them, playback is not affected', () => {
        let writable = true;
        const save = vi.fn();
        const { target, commands, pedal } = setup({ mapping: { F9: 'playPause', F10: 'skip' } }, {
            mutating: ['playPause'], canMutate: () => writable,
        });
        pedal.attach();
        fire(target, key('keydown', 'F9'));
        expect(commands.playPause).toHaveBeenCalledTimes(1);
        writable = false; // lease lost
        fire(target, key('keydown', 'F9'));
        expect(commands.playPause).toHaveBeenCalledTimes(1);
        fire(target, key('keydown', 'F10'));
        expect(commands.skip).toHaveBeenCalledTimes(1);
        expect(save).not.toHaveBeenCalled();
    });

    it('status line follows the mapping', () => {
        const { pedal } = setup({});
        expect(pedal.status.value).toBe(TEXT.none);
        pedal.setSettings({ mapping: { F9: 'playPause' } });
        expect(pedal.status.value).toBe('Pedal: Tastaturmodus aktiv');
        expect(TEXT.none).toBe('Keine Pedalzuordnung');
    });

    it('consumes only mapped keys and never stops propagation', () => {
        const { target, pedal } = setup({ mapping: { F9: 'playPause' } });
        pedal.attach();
        const touched = vi.fn();
        const e = key('keydown', 'F9', { stopPropagation: touched });
        fire(target, e);
        expect(e.defaultPrevented).toBe(true);
        expect(touched).not.toHaveBeenCalled();
    });
});

describe('key capture', () => {
    it('reports a mappable key once, rejects printable keys, Escape cancels', () => {
        const target = new EventTarget();
        const onCapture = vi.fn();
        const onReject = vi.fn();
        const cap = createKeyCapture({ target, onCapture, onReject });
        fire(target, key('keydown', 'F9'));
        expect(onCapture).not.toHaveBeenCalled();
        cap.start();
        expect(cap.message.value).toBe('Taste drücken …');
        expect(fire(target, key('keydown', 'KeyA')).defaultPrevented).toBe(true);
        expect(onReject).toHaveBeenCalledWith('KeyA');
        expect(cap.capturing.value).toBe(true);
        fire(target, key('keydown', 'F9'));
        expect(onCapture).toHaveBeenCalledWith('F9');
        expect(cap.capturing.value).toBe(false);
        fire(target, key('keydown', 'F10'));
        expect(onCapture).toHaveBeenCalledTimes(1);
        cap.start();
        fire(target, key('keydown', 'Escape'));
        expect(cap.capturing.value).toBe(false);
    });
});

describe('correction view wiring', () => {
    const ok = (data) => ({ outcome: 'ok', status: 200, data });
    const client = () => ({
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            if (method === 'GET' && path.endsWith('/correction')) {
                return ok({ dictation_id: 'd1', text_revision_id: 'r1', text: 'Hallo', state: { version: 1, audio_position_ms: 0 }, links: [], markers: [] });
            }
            if (method === 'POST' && path.endsWith('/leases')) {
                return ok({ lease_id: 'l', generation: 1, expires_at: new Date(Date.now() + 600000).toISOString() });
            }
            return ok({ items: [] });
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    });
    class FakeAudio extends EventTarget {
        constructor() { super(); this.currentTime = 20; this.duration = 60; this.playbackRate = 1; this.paused = true; this.preservesPitch = true; }
        play() { this.paused = false; return Promise.resolve(); }
        pause() { this.paused = true; }
    }
    const pedalTarget = new EventTarget();
    const make = (storage) => createCorrectionViewController({
        client: client(), dictationId: 'd1', currentUser: { id: 7 }, heartbeatMs: 600000, autosaveMs: 0,
        pedalOptions: { target: pedalTarget, storage, settings: { mapping: { F9: 'playPause', F10: 'rewind', F8: 'hold' }, rewindSeconds: 5 } },
    });

    it('pedal rewind goes through the same usePlaybackTempo path as the Zurueckspulen button', async () => {
        const c = make(memoryStorage());
        const audio = new FakeAudio();
        c.bindAudio(audio);
        await c.start();
        await c.pedalReady();
        expect(c.pedalStatus.value).toBe('Pedal: Tastaturmodus aktiv');
        // The button runs tempo.rewind(stepSeconds) on the same element; the pedal reaches the same effect.
        const viaButton = new FakeAudio();
        usePlaybackTempo({ audio: viaButton }).rewind(5);
        pedalTarget.dispatchEvent(key('keydown', 'F10'));
        expect(audio.currentTime).toBe(viaButton.currentTime);
        expect(audio.currentTime).toBe(15);
        pedalTarget.dispatchEvent(key('keydown', 'F9'));
        expect(audio.paused).toBe(false);
        pedalTarget.dispatchEvent(key('keydown', 'F9'));
        expect(audio.paused).toBe(true);
        pedalTarget.dispatchEvent(key('keydown', 'F8'));
        expect(audio.paused).toBe(false);
        pedalTarget.dispatchEvent(key('keyup', 'F8'));
        expect(audio.paused).toBe(true);
        c.dispose();
    });

    it('is inactive once the view is disposed (unmounted)', async () => {
        const c = make(memoryStorage());
        const audio = new FakeAudio();
        c.bindAudio(audio);
        await c.start();
        await c.pedalReady();
        c.dispose();
        const e = key('keydown', 'F10');
        pedalTarget.dispatchEvent(e);
        expect(e.defaultPrevented).toBe(false);
        expect(audio.currentTime).toBe(20);
        expect(c.pedalStatus.value).toBe('');
    });

    it('does not attach when disposed before the module finished loading', async () => {
        const c = make(memoryStorage());
        await c.start();
        c.dispose();
        await c.pedalReady();
        expect(c.pedal()).toBe(null);
    });

    it('shows Keine Pedalzuordnung without a mapping', async () => {
        const c = createCorrectionViewController({
            client: client(), dictationId: 'd1', currentUser: { id: 7 }, heartbeatMs: 600000, autosaveMs: 0,
            pedalOptions: { target: pedalTarget, storage: memoryStorage(), settings: {} },
        });
        await c.start();
        await c.pedalReady();
        expect(c.pedalStatus.value).toBe('Keine Pedalzuordnung');
        c.setPedalSettings({ mapping: { F9: 'playPause' } });
        expect(c.pedalStatus.value).toBe('Pedal: Tastaturmodus aktiv');
        c.dispose();
    });
});
