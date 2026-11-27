import { describe, expect, it, vi } from 'vitest';

import { createPedalController, normalizeSettings, saveSettings } from '../../../static/js/clinic/pedal-ui.js';

// PAR-COR-004, PAR-OPS-001..003: synthetic keyboard-emulation reports only. No device, no WebHID.
// Nothing here is evidence for a real foot pedal; such tests stay not_run.
const key = (type, code, extra = {}) => Object.assign(new Event(type, { cancelable: true }), { code, repeat: false, ...extra });
const fire = (target, e) => { target.dispatchEvent(e); return e; };

function memoryStorage() {
    const data = {};
    return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

/** Controller whose commands append to an action log; focus is switched by the fixture. */
function fixture(settings) {
    const target = new EventTarget();
    const log = [];
    const commands = {
        play: () => log.push('play'),
        pause: () => log.push('pause'),
        playPause: () => log.push('playPause'),
        rewind: (s) => log.push(`rewind:${s}`),
        skip: (s) => log.push(`skip:${s}`),
    };
    const storage = memoryStorage();
    const pedal = createPedalController({ commands, settings, target, storage });
    pedal.attach();
    let focused = true;
    const setFocus = (value) => {
        focused = value;
        globalThis.document = { hasFocus: () => focused };
    };
    return { target, log, pedal, storage, setFocus, cleanup: () => { delete globalThis.document; } };
}

const SCRIPT = [
    ['keydown', 'F9'], ['keyup', 'F9'],
    ['keydown', 'F8'], ['keydown', 'F8', { repeat: true }], ['keyup', 'F8'],
    ['keydown', 'F10'], ['keydown', 'F11'], ['keydown', 'KeyA'],
];
const SETTINGS = { mapping: { F9: 'playPause', F8: 'hold', F10: 'rewind' }, rewindSeconds: 4, autoRewindSeconds: 2 };

function replay(f, script) {
    for (const [type, code, extra] of script) fire(f.target, key(type, code, extra));
    return [...f.log];
}

describe('PAR-OPS-001 replay of synthetic keyboard reports', () => {
    it('replaying the same script twice yields an identical action log', () => {
        const a = fixture(SETTINGS);
        const b = fixture(SETTINGS);
        try {
            const first = replay(a, SCRIPT);
            const second = replay(b, SCRIPT);
            expect(first).toEqual(['playPause', 'play', 'pause', 'rewind:2', 'rewind:4']);
            expect(second).toEqual(first);
        } finally { a.cleanup(); b.cleanup(); }
    });
});

describe('PAR-OPS-002 focus loss', () => {
    it('without document focus no action fires and no event is consumed', () => {
        const f = fixture(SETTINGS);
        try {
            f.setFocus(false);
            const e = fire(f.target, key('keydown', 'F9'));
            expect(e.defaultPrevented).toBe(false);
            expect(replay(f, SCRIPT)).toEqual([]);
        } finally { f.cleanup(); }
    });

    it('focus change mid-script: actions stop while unfocused and resume on return, log is stable on replay', () => {
        const run = () => {
            const f = fixture(SETTINGS);
            try {
                fire(f.target, key('keydown', 'F9'));
                f.setFocus(false);
                fire(f.target, key('keydown', 'F9'));
                fire(f.target, key('keydown', 'F10'));
                f.setFocus(true);
                fire(f.target, key('keydown', 'F10'));
                return [...f.log];
            } finally { f.cleanup(); }
        };
        expect(run()).toEqual(['playPause', 'rewind:4']);
        expect(run()).toEqual(run());
    });

    it('hold key released by focus loss: releaseAll pauses and auto-rewinds once, the late keyup is ignored', () => {
        const f = fixture(SETTINGS);
        try {
            fire(f.target, key('keydown', 'F8'));
            f.setFocus(false);
            f.pedal.releaseAll(); // what the window blur listener runs
            fire(f.target, key('keyup', 'F8'));
            expect(f.log).toEqual(['play', 'pause', 'rewind:2']);
            f.pedal.releaseAll();
            expect(f.log).toEqual(['play', 'pause', 'rewind:2']);
        } finally { f.cleanup(); }
    });

    it('window blur event releases a held key', () => {
        const prev = globalThis.window;
        const win = new EventTarget();
        globalThis.window = win;
        const f = fixture(SETTINGS);
        try {
            fire(f.target, key('keydown', 'F8'));
            win.dispatchEvent(new Event('blur'));
            expect(f.log).toEqual(['play', 'pause', 'rewind:2']);
            f.pedal.detach();
        } finally {
            f.cleanup();
            if (prev === undefined) delete globalThis.window; else globalThis.window = prev;
        }
    });
});

describe('PAR-OPS-003 synthetic events certify nothing', () => {
    it('a synthetic event carries no trust and the controller records no device identity', () => {
        const f = fixture(SETTINGS);
        try {
            const e = key('keydown', 'F9', { vendorId: 0x0911, productId: 0x1844, deviceModel: 'Philips ACC2310' });
            expect(e.isTrusted).toBe(false);
            fire(f.target, e);
            expect(f.log).toEqual(['playPause']);
            const stored = JSON.parse(JSON.stringify(f.pedal.settings()));
            expect(Object.keys(stored).sort()).toEqual(['autoRewindSeconds', 'mapping', 'rewindSeconds', 'skipSeconds']);
            expect(JSON.stringify(stored)).not.toMatch(/vendor|product|model|device|paired|certif/i);
            expect(Object.keys(f.pedal).some((k) => /device|model|pair|certif/i.test(k))).toBe(false);
        } finally { f.cleanup(); }
    });

    it('device-specific fields in settings are dropped; pairing stays opt-in (never persisted by key events)', () => {
        const clean = normalizeSettings({ ...SETTINGS, deviceModel: 'Philips', paired: true, vendorId: 1 });
        expect(Object.keys(clean).sort()).toEqual(['autoRewindSeconds', 'mapping', 'rewindSeconds', 'skipSeconds']);
        const storage = memoryStorage();
        const saved = saveSettings({ ...SETTINGS, paired: true }, storage);
        expect(saved).not.toHaveProperty('paired');
        const f = fixture({});
        try {
            replay(f, SCRIPT);
            expect(Object.keys(f.storage.data)).toEqual([]);
            expect(f.log).toEqual([]);
        } finally { f.cleanup(); }
    });

    it('unmapped synthetic events never reach a command', () => {
        const f = fixture({ mapping: { F9: 'playPause' } });
        const spy = vi.fn();
        try {
            f.target.addEventListener('keydown', spy);
            const e = fire(f.target, key('keydown', 'MediaPlayPause'));
            expect(e.defaultPrevented).toBe(false);
            expect(f.log).toEqual([]);
            expect(spy).toHaveBeenCalledTimes(1);
        } finally { f.cleanup(); }
    });
});
