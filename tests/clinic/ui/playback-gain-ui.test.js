import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    AGC, PlaybackGain, clampGainDb, levelOf, nextGainDb, registerPlaybackGain, usePlaybackGain,
} from '../../../static/js/clinic/playback-gain-ui.js';

const V = { shallowRef: (v) => ({ value: v }), onBeforeUnmount: vi.fn() };

// Synthetic PCM: a 440 Hz tone at the given linear amplitude, 16 kHz.
function tone(amplitude, n = 2048) {
    const out = new Float32Array(n);
    for (let i = 0; i < n; i += 1) out[i] = amplitude * Math.sin((2 * Math.PI * 440 * i) / 16000);
    return out;
}
const sha = (buf) => createHash('sha256').update(Buffer.from(buf.buffer.slice(0))).digest('hex');
const outputLevel = (source, gainNode) => levelOf(source.map((x) => Math.max(-1, Math.min(1, x * gainNode.gain.value))));

// Fake playback graph: the analyser reads the source before the gain node.
function fakeGraph(source) {
    const gainNode = { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() };
    const analyser = {
        fftSize: source.length,
        getFloatTimeDomainData: vi.fn((target) => target.set(source)),
        disconnect: vi.fn(),
    };
    const ctx = {
        destination: {},
        createMediaElementSource: () => ({ connect: vi.fn() }),
        createGain: () => gainNode,
        createAnalyser: () => analyser,
    };
    return { ctx, gainNode, analyser };
}

function setup(source) {
    const g = fakeGraph(source);
    const api = usePlaybackGain({ audio: {}, createAudioContext: () => g.ctx }, V);
    return { ...g, api };
}

describe('playback gain ui', () => {
    it('raises a quiet fixture in auto mode, measurably, without touching the source buffer', () => {
        const source = tone(0.02); // about -37 dBFS RMS
        const before = sha(source);
        const { api, gainNode } = setup(source);
        const quiet = outputLevel(source, gainNode).rmsDbfs;
        for (let i = 0; i < 40; i += 1) api.step();
        const loud = outputLevel(source, gainNode).rmsDbfs;
        expect(api.gain.value).toBeGreaterThan(5);
        expect(api.gain.value).toBeLessThanOrEqual(AGC.maxGainDb);
        expect(loud - quiet).toBeGreaterThan(5);
        expect(sha(source)).toBe(before);
    });

    it('applies a manual gain exactly and leaves the source byte-identical', () => {
        const source = tone(0.05);
        const before = sha(source);
        const { api, gainNode } = setup(source);
        const base = outputLevel(source, gainNode).rmsDbfs;
        const state = api.setGain(6);
        expect(state).toMatchObject({ mode: 'manual', gainDb: 6 });
        expect(gainNode.gain.value).toBeCloseTo(10 ** (6 / 20), 6);
        expect(outputLevel(source, gainNode).rmsDbfs - base).toBeCloseTo(6, 1);
        api.step(); // manual mode does not move the gain
        expect(api.gain.value).toBe(6);
        expect(sha(source)).toBe(before);
        expect(api.setMode('auto').mode).toBe('auto');
    });

    it('bounds manual gain to -12..+18 dB', () => {
        const { api } = setup(tone(0.001));
        expect(api.setGain(40)).toMatchObject({ gainDb: 18, message: 'Verstärkung auf den zulässigen Bereich begrenzt.' });
        expect(api.setGain(-40).gainDb).toBe(-12);
    });

    it('protects against clipping: the peak ceiling wins over auto target and manual value', () => {
        const source = tone(0.5); // peak about -6 dBFS
        const { api, gainNode } = setup(source);
        for (let i = 0; i < 60; i += 1) api.step();
        const out = outputLevel(source, gainNode);
        expect(out.peakDbfs).toBeLessThanOrEqual(AGC.peakCeilingDbfs + 0.05);
        const limited = api.setGain(18);
        expect(limited.gainDb).toBeLessThan(18);
        expect(limited.message).toBe('Verstärkung begrenzt: Übersteuerungsschutz aktiv.');
        expect(outputLevel(source, gainNode).peakDbfs).toBeLessThanOrEqual(AGC.peakCeilingDbfs + 0.05);
        expect(clampGainDb(10, -3)).toBe(2);
    });

    it('follows the server curve: silence holds, falling gain uses attack, rising uses release', () => {
        expect(nextGainDb(4, { rmsDbfs: -90, peakDbfs: -80 })).toBe(4);
        expect(nextGainDb(10, { rmsDbfs: -20, peakDbfs: -30 })).toBeCloseTo(5, 6); // target 0, gap -10, attack .5
        expect(nextGainDb(0, { rmsDbfs: -40, peakDbfs: -30 })).toBeCloseTo(1.8, 6); // target clamps to 18, release 0.1
    });

    it('fails soft without an audio context: unavailable, no throw, no gain', () => {
        const api = usePlaybackGain({ audio: {} }, V);
        expect(api.state.value).toMatchObject({ available: false, message: 'Wiedergabeverstärkung ist in diesem Browser nicht verfügbar.' });
        expect(api.setGain(3).gainDb).toBe(0);
        expect(api.step().available).toBe(false);
    });

    it('rejects invalid input and survives a throwing context or analyser', () => {
        const { api } = setup(tone(0.1));
        expect(api.setGain(NaN).message).toBe('Ungültiger Verstärkungswert.');
        expect(api.setGain('3').message).toBe('Ungültiger Verstärkungswert.');
        expect(api.setMode('loud').message).toBe('Ungültiger Modus.');
        const broken = usePlaybackGain({ audio: {}, createAudioContext: () => { throw new Error('denied'); } }, V);
        expect(broken.state.value.available).toBe(false);
        const g = fakeGraph(tone(0.1));
        g.analyser.getFloatTimeDomainData.mockImplementation(() => { throw new Error('boom'); });
        const flaky = usePlaybackGain({ audio: {}, createAudioContext: () => g.ctx }, V);
        expect(flaky.step().message).toBe('Wiedergabeverstärkung konnte nicht angewendet werden.');
    });

    it('runs on an injected timer and stops on dispose', () => {
        const g = fakeGraph(tone(0.02));
        const setIntervalFn = vi.fn(() => 7);
        const clearIntervalFn = vi.fn();
        const api = usePlaybackGain({
            audio: {}, createAudioContext: () => g.ctx, setInterval: setIntervalFn, clearInterval: clearIntervalFn,
        }, V);
        api.start();
        expect(setIntervalFn).toHaveBeenCalledWith(expect.any(Function), AGC.windowMs);
        api.dispose();
        expect(clearIntervalFn).toHaveBeenCalledWith(7);
        expect(api.state.value.available).toBe(false);
    });

    it('exposes labelled, keyboard-reachable German markup, tokens only, no storage', () => {
        const t = PlaybackGain.template;
        expect(t).toContain('role="group" aria-label="Wiedergabeverstärkung"');
        expect(t).toContain('role="status" aria-live="polite"');
        expect(t).toContain('type="range"');
        expect(t).toContain('aria-pressed');
        expect(t).toContain('Automatisch');
        expect(t).toContain('Manuell');
        const app = { component: vi.fn() };
        registerPlaybackGain(app);
        expect(app.component).toHaveBeenCalledWith('clinic-playback-gain', PlaybackGain);
        const source = readFileSync(new URL('../../../static/js/clinic/playback-gain-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|fetch\(/);
    });
});
