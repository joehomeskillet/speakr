import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
    PlaybackTempo, SPEEDS, clampTempo, registerPlaybackTempo, seekMs, stepTempo, usePlaybackTempo,
} from '../../../static/js/clinic/playback-tempo-ui.js';

function fakeAudio(extra = {}) {
    const handlers = {};
    return {
        currentTime: 10, duration: 60, playbackRate: 1, preservesPitch: false,
        addEventListener: vi.fn((n, h) => { (handlers[n] ||= new Set()).add(h); }),
        removeEventListener: vi.fn((n, h) => { handlers[n]?.delete(h); }),
        emit(n) { (handlers[n] || []).forEach((h) => h()); },
        handlers,
        ...extra,
    };
}

describe('clinic playback tempo ui', () => {
    beforeEach(() => {
        global.localStorage = { setItem: vi.fn(), getItem: () => null };
        global.sessionStorage = { setItem: vi.fn(), getItem: () => null };
    });

    it('exposes the server speeds 0.5 to 2.0 in steps of 0.1', () => {
        expect(SPEEDS).toHaveLength(16);
        expect(SPEEDS[0]).toBe(0.5);
        expect(SPEEDS[15]).toBe(2);
        let v = 1;
        for (let i = 0; i < 10; i += 1) v = stepTempo(v, 1);
        expect(v).toBe(2);
        for (let i = 0; i < 15; i += 1) v = stepTempo(v, -1);
        expect(v).toBe(0.5);
    });

    it('sets the rate, keeps pitch and clamps out-of-range values', () => {
        const audio = fakeAudio();
        const t = usePlaybackTempo({ audio });
        expect(t.setRate(1.5)).toMatchObject({ ok: true, rate: 1.5 });
        expect(audio.playbackRate).toBe(1.5);
        expect(audio.preservesPitch).toBe(true);
        expect(t.setRate(9).rate).toBe(2);
        expect(t.setRate(0.1).rate).toBe(0.5);
        expect(t.faster().rate).toBe(0.6);
        expect(t.slower().rate).toBe(0.5);
        expect(clampTempo(1.234)).toBe(1.23);
    });

    it('rejects invalid rates without touching playback', () => {
        const audio = fakeAudio();
        const t = usePlaybackTempo({ audio });
        for (const bad of [NaN, Infinity, true, '1.5', null]) {
            expect(t.setRate(bad).ok).toBe(false);
        }
        expect(audio.playbackRate).toBe(1);
        expect(t.setRate(NaN).code).toBe('not_finite');
        expect(t.setRate(true).code).toBe('invalid_type');
        expect(() => stepTempo(1, 2)).toThrow();
    });

    it('reports missing pitch support as a warning but still sets the rate', () => {
        const audio = fakeAudio();
        delete audio.preservesPitch;
        const t = usePlaybackTempo({ audio });
        const r = t.setRate(1.2);
        expect(r).toMatchObject({ ok: true, code: 'pitch_unsupported' });
        expect(t.state.value.surface.status).toBe('warning');
        expect(audio.playbackRate).toBe(1.2);
    });

    it('skips and rewinds within [0, duration]', () => {
        const audio = fakeAudio();
        const t = usePlaybackTempo({ audio });
        expect(t.skip(5)).toMatchObject({ ok: true, positionMs: 15000 });
        expect(t.rewind(3).positionMs).toBe(12000);
        expect(t.rewind(100).positionMs).toBe(0);
        expect(t.skip(1000).positionMs).toBe(60000);
        expect(seekMs(5, -10, 100)).toBe(0);
        expect(seekMs(95, 10, 100)).toBe(100);
    });

    it('refuses bad seconds, unknown duration and missing audio', () => {
        const audio = fakeAudio({ duration: NaN });
        const t = usePlaybackTempo({ audio });
        expect(t.skip(5).code).toBe('no_duration');
        expect(t.rewind(-1).code).toBe('negative_seconds');
        expect(t.skip(NaN).code).toBe('not_finite');
        expect(audio.currentTime).toBe(10);
        const none = usePlaybackTempo({});
        expect(none.skip(1)).toMatchObject({ ok: false, code: 'no_audio' });
        expect(none.faster().code).toBe('no_audio');
    });

    it('forwards seeked and ratechange to the composer until disposed', () => {
        const audio = fakeAudio();
        const onSync = vi.fn();
        const t = usePlaybackTempo({ audio, onSync });
        t.setRate(1.5);
        audio.emit('ratechange');
        audio.currentTime = 20;
        audio.emit('seeked');
        expect(onSync).toHaveBeenNthCalledWith(1, { reason: 'ratechange', positionMs: 10000, rate: 1.5 });
        expect(onSync).toHaveBeenNthCalledWith(2, { reason: 'seeked', positionMs: 20000, rate: 1.5 });
        t.dispose();
        audio.emit('seeked');
        expect(onSync).toHaveBeenCalledTimes(2);
        expect(audio.removeEventListener).toHaveBeenCalledTimes(2);
        expect(t.skip(1).code).toBe('no_audio');
        expect(global.localStorage.setItem).not.toHaveBeenCalled();
    });

    it('exports a labelled, token-styled component and registers it', () => {
        expect(PlaybackTempo.template).toContain('aria-live="polite"');
        expect(PlaybackTempo.template).toContain('type="button"');
        expect(PlaybackTempo.template).toContain('aria-label="Wiedergabe"');
        expect(PlaybackTempo.template).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
        const app = { component: vi.fn() };
        registerPlaybackTempo(app);
        expect(app.component).toHaveBeenCalledWith('clinic-playback-tempo', PlaybackTempo);
    });
});
