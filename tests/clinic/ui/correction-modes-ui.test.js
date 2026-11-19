import { describe, expect, it, vi } from 'vitest';
import {
    CorrectionModes, DEFAULT_MODE, LABELS, MODES, PREFERENCE_PATH,
    registerCorrectionModes, useCorrectionModes,
} from '../../../static/js/clinic/correction-modes-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: '' });

// Fake player in seconds; the clock is driven by the test.
function makePlayer(position = 10, playing = true) {
    const p = { position, playing, log: [] };
    return {
        state: p,
        position: () => p.position,
        isPlaying: () => p.playing,
        pause: vi.fn(() => { p.playing = false; p.log.push('pause'); }),
        play: vi.fn(() => { p.playing = true; p.log.push('play'); }),
        seek: vi.fn((s) => { p.position = s; p.log.push('seek ' + s); }),
    };
}

function make(extra) {
    const clock = { t: 0 };
    const audio = makePlayer((extra && extra.position) || 10, true);
    const client = { request: vi.fn(async () => ok({ mode: null })) };
    const modes = useCorrectionModes({
        audio, client, now: () => clock.t, ...(extra || {}),
    });
    return { clock, audio, client, modes };
}

describe('correction modes ui', () => {
    it('offers both modes and defaults to "Auf zu hörendes Wort zeigen"', () => {
        const { modes } = make();
        expect(DEFAULT_MODE).toBe('stop_on_typing');
        expect(modes.mode.value).toBe('stop_on_typing');
        expect(MODES.map((m) => m.id)).toEqual(['stop_on_typing', 'type_while_playing']);
        expect(MODES[0].label).toBe('Auf zu hörendes Wort zeigen');
        expect(modes.options).toBe(MODES);
    });

    it('stop mode pauses on the first key, then rewinds 2 s and resumes after the idle time', () => {
        const { modes, audio, clock } = make({ position: 10 });
        clock.t = 1000;
        expect(modes.onKey()).toEqual({ kind: 'pause' });
        expect(audio.pause).toHaveBeenCalledTimes(1);
        expect(modes.waiting.value).toBe(true);
        clock.t = 1400;
        expect(modes.onKey()).toEqual({ kind: 'none' });
        expect(audio.pause).toHaveBeenCalledTimes(1);
        clock.t = 2800; // 1400 ms after last key: not yet
        expect(modes.tick()).toEqual({ kind: 'none' });
        clock.t = 2900;
        expect(modes.tick()).toEqual({ kind: 'resume', seekToMs: 8000 });
        expect(audio.state.log).toEqual(['pause', 'seek 8', 'play']);
        expect(modes.waiting.value).toBe(false);
        expect(modes.tick()).toEqual({ kind: 'none' });
    });

    it('never rewinds before the audio start', () => {
        const { modes, audio, clock } = make({ position: 0.5 });
        clock.t = 10;
        modes.onKey();
        clock.t = 2000;
        expect(modes.tick()).toEqual({ kind: 'resume', seekToMs: 0 });
        expect(audio.seek).toHaveBeenCalledWith(0);
    });

    it('honours a configured rewind and idle time', () => {
        const { modes, clock } = make({ position: 10, rewindMs: 500, resumeIdleMs: 100 });
        clock.t = 0;
        modes.onKey();
        clock.t = 100;
        expect(modes.tick()).toEqual({ kind: 'resume', seekToMs: 9500 });
    });

    it('does nothing for keys while paused or in continue mode', async () => {
        const { modes, audio, clock } = make();
        audio.state.playing = false;
        expect(modes.onKey()).toEqual({ kind: 'none' });
        audio.state.playing = true;
        await modes.setMode('type_while_playing');
        clock.t = 50;
        expect(modes.onKey()).toEqual({ kind: 'none' });
        clock.t = 9999;
        expect(modes.tick()).toEqual({ kind: 'none' });
        expect(audio.pause).not.toHaveBeenCalled();
        expect(audio.state.playing).toBe(true);
    });

    it('switching modes during playback cancels a pending resume and keeps audio playing', async () => {
        const { modes, audio, clock, client } = make();
        clock.t = 100;
        modes.onKey();
        expect(audio.state.playing).toBe(false);
        const res = await modes.setMode('type_while_playing');
        expect(res.ok).toBe(true);
        expect(client.request).toHaveBeenCalledWith(PREFERENCE_PATH, { method: 'PUT', body: { mode: 'type_while_playing' } });
        expect(audio.state.playing).toBe(true);
        expect(modes.waiting.value).toBe(false);
        clock.t = 5000;
        expect(modes.tick()).toEqual({ kind: 'none' });
        // Back to stop mode while playing: the next key pauses again.
        await modes.setMode('stop_on_typing');
        clock.t = 6000;
        expect(modes.onKey()).toEqual({ kind: 'pause' });
    });

    it('selection changes are forwarded and leave playback alone', () => {
        const alignment = { setSelection: vi.fn((a, b) => ({ start: a, end: b })) };
        const { modes, audio } = make({ alignment });
        expect(modes.onSelectionChange(3, 7)).toEqual({ start: 3, end: 7 });
        expect(alignment.setSelection).toHaveBeenCalledWith(3, 7);
        expect(audio.pause).not.toHaveBeenCalled();
        expect(audio.seek).not.toHaveBeenCalled();
        expect(make().modes.onSelectionChange(1, 2)).toBeNull();
    });

    it('loads the saved mode and ignores unknown values', async () => {
        const first = make();
        first.client.request.mockResolvedValueOnce(ok({ mode: 'type_while_playing' }));
        expect((await first.modes.load()).ok).toBe(true);
        expect(first.modes.mode.value).toBe('type_while_playing');
        const second = make();
        second.client.request.mockResolvedValueOnce(ok({ mode: 'bogus' }));
        await second.modes.load();
        expect(second.modes.mode.value).toBe(DEFAULT_MODE);
    });

    it('rejects an unknown mode without a request', async () => {
        const { modes, client } = make();
        expect(await modes.setMode('nope')).toMatchObject({ ok: false, code: 'invalid', message: LABELS.invalid });
        expect(modes.mode.value).toBe(DEFAULT_MODE);
        expect(client.request).not.toHaveBeenCalled();
    });

    it('keeps the mode for the session on 403, 409 and failure, and recovers on retry', async () => {
        for (const [outcome, status, code] of [['permission', 403, 'denied'], ['conflict', 409, 'stale'], ['network', 0, 'failed']]) {
            const { modes, client } = make();
            client.request.mockResolvedValueOnce(fail(outcome, status));
            const res = await modes.setMode('type_while_playing');
            expect(res).toMatchObject({ ok: false, code, message: LABELS[code] });
            expect(modes.mode.value).toBe('type_while_playing');
            expect(modes.status.value).toBe('error');
            expect(modes.announcement.value).toBe(LABELS[code]);
            client.request.mockResolvedValueOnce(ok({ mode: 'type_while_playing' }));
            expect((await modes.setMode('type_while_playing')).ok).toBe(true);
            expect(modes.status.value).toBe('idle');
            expect(modes.error.value).toBeNull();
        }
        const { modes, client } = make();
        client.request.mockResolvedValueOnce(fail('permission', 403));
        expect(await modes.load()).toMatchObject({ ok: false, code: 'denied' });
        expect(modes.mode.value).toBe(DEFAULT_MODE);
    });

    it('treats a missing preferences route (404/405) as session-only, not as an error alert', async () => {
        for (const status of [404, 405]) {
            const { modes, client } = make();
            client.request.mockResolvedValueOnce(fail('UNKNOWN', status));
            const res = await modes.setMode('type_while_playing');
            expect(res).toMatchObject({ ok: false, code: 'unsupported', message: LABELS.unsupported });
            expect(modes.mode.value).toBe('type_while_playing');
            expect(modes.status.value).toBe('local');
            expect(modes.error.value).toBeNull();
            expect(modes.announcement.value).toBe(LABELS.unsupported);
        }
        const { modes, client } = make();
        client.request.mockResolvedValueOnce(fail('UNKNOWN', 404));
        expect(await modes.load()).toMatchObject({ ok: false, code: 'unsupported' });
        expect(modes.mode.value).toBe(DEFAULT_MODE);
        expect(modes.status.value).toBe('local');
    });

    it('drives ticks with an injected timer and stops after dispose', () => {
        const timer = { set: vi.fn(() => 7), clear: vi.fn() };
        const { modes } = make({ timer, tickMs: 100 });
        modes.start();
        modes.start();
        expect(timer.set).toHaveBeenCalledTimes(1);
        expect(timer.set).toHaveBeenCalledWith(modes.tick, 100);
        modes.dispose();
        expect(timer.clear).toHaveBeenCalledWith(7);
        expect(modes.onKey()).toEqual({ kind: 'none' });
    });

    it('ignores a save that finished after dispose', async () => {
        const { modes, client } = make();
        let release;
        client.request.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
        const pending = modes.setMode('type_while_playing');
        modes.dispose();
        release(fail('permission', 403));
        expect((await pending).ok).toBe(true);
        expect(modes.status.value).toBe('saving');
    });

    it('exports a labelled, keyboard-operable component without hard colours', () => {
        expect(CorrectionModes.name).toBe('ClinicCorrectionModes');
        expect(CorrectionModes.template).toContain('<legend>');
        expect(CorrectionModes.template).toContain('type="radio"');
        expect(CorrectionModes.template).toContain('role="radiogroup"');
        expect(CorrectionModes.template).toContain('aria-live="polite"');
        expect(CorrectionModes.template).toContain('role="alert"');
        expect(CorrectionModes.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        const app = { component: vi.fn() };
        expect(registerCorrectionModes(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-modes', CorrectionModes);
    });
});
