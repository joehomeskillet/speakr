import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import { useCorrectionAlignment } from '../../../static/js/clinic/correction-alignment-ui.js';
import {
    CorrectionWaveform,
    LABELS,
    MAX_ZOOM,
    MIN_ZOOM,
    ZOOM_LEVELS,
    barsFor,
    useCorrectionWaveform,
} from '../../../static/js/clinic/correction-waveform-ui.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const composeSource = readFileSync(join(__dirname, '../../../static/js/clinic/compose-correction.js'), 'utf8');

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const AUDIO = '33333333-3333-4333-8333-333333333333';
const AUDIO2 = '44444444-4444-4444-8444-444444444444';
const REC = '55555555-5555-4555-8555-555555555555';
const base = '/dictations/' + D;
const TEXT = 'Anamnese Pause Husten Ende';
const ok = (data) => ({ outcome: 'ok', status: 200, data });

// Multi-level SPK1 peaks (80 Hz + 10 Hz over 8 seconds) so zoom selects the finer level.
function multiLevelPeaksBuffer(duration = 8) {
    const fineRate = 80;
    const coarseRate = 10;
    const fineLen = duration * fineRate;
    const coarseLen = duration * coarseRate;
    const header = new TextEncoder().encode(JSON.stringify({
        duration,
        levels: [
            { rate: fineRate, length: fineLen, offset: 0 },
            { rate: coarseRate, length: coarseLen, offset: fineLen * 2 },
        ],
    }));
    const buf = new ArrayBuffer(8 + header.length + fineLen * 2 + coarseLen * 2);
    const bytes = new Uint8Array(buf);
    bytes.set([0x53, 0x50, 0x4b, 0x31], 0);
    new DataView(buf).setUint32(4, header.length, true);
    bytes.set(header, 8);
    const fine = new Int8Array(buf, 8 + header.length, fineLen * 2);
    for (let i = 0; i < fineLen; i++) {
        fine[i * 2] = -20 - (i % 50);
        fine[i * 2 + 1] = 20 + (i % 50);
    }
    const coarse = new Int8Array(buf, 8 + header.length + fineLen * 2, coarseLen * 2);
    for (let i = 0; i < coarseLen; i++) {
        coarse[i * 2] = -10;
        coarse[i * 2 + 1] = 10;
    }
    return buf;
}

class FakeAudio extends EventTarget {
    constructor(duration = 8) {
        super();
        this.currentTime = 0;
        this.duration = duration;
        this.paused = true;
        this.ended = false;
    }

    play() {
        this.paused = false;
        this.dispatchEvent(new Event('play'));
        return Promise.resolve();
    }

    pause() {
        this.paused = true;
        this.dispatchEvent(new Event('pause'));
    }
}

function makeLinks() {
    return [
        { id: 'L0', origin: 'asr', text_start: 0, text_end: 8, audio_start_ms: 500, audio_end_ms: 1500 },
        { id: 'L1', origin: 'asr', text_start: 15, text_end: 21, audio_start_ms: 5000, audio_end_ms: 6200 },
    ];
}

function makeEvidence() {
    return [
        {
            id: 'M1', kind: 'uncertain', start_ms: 600, end_ms: 1600,
            source: 'fixture:1', run_id: 'run-1', text_revision_id: REV, audio_revision_id: AUDIO, confidence: 0.4,
        },
        {
            id: 'M2', kind: 'command', start_ms: 5200, end_ms: 6000,
            source: 'fixture:1', run_id: 'run-1', text_revision_id: REV, audio_revision_id: AUDIO, confidence: null,
        },
    ];
}

async function make({ duration = 8, withAudio = true } = {}) {
    const editor = {
        text: { value: TEXT },
        textRevisionId: { value: REV },
        audio: { value: { status: 'ready', revisionId: AUDIO } },
    };
    const correctionCalls = [];
    const client = {
        request: vi.fn(async (path) => {
            if (path === base + '/correction') {
                correctionCalls.push(path);
                return ok({
                    dictation_id: D, text_revision_id: REV, audio_revision_id: AUDIO,
                    text: TEXT, links: makeLinks(), markers: [], recognition_evidence: makeEvidence(),
                });
            }
            if (path === base + '/audio-revisions/current') {
                return ok({ revision_id: AUDIO, segments: [] });
            }
            return { outcome: 'http', status: 404, data: null };
        }),
    };
    const seek = vi.fn();
    const alignment = useCorrectionAlignment({ editor, client, dictationId: D, seek });
    await alignment.refresh();
    const peakCalls = [];
    const fetchFn = vi.fn(async (url) => {
        peakCalls.push(url);
        return { status: 200, arrayBuffer: async () => multiLevelPeaksBuffer(duration) };
    });
    const audio = withAudio ? new FakeAudio(duration) : null;
    const wave = useCorrectionWaveform({
        editor, alignment, client, dictationId: D, recordingId: REC, audio, seek, fetchFn, sleep: async () => {},
    });
    await wave.load();
    return { editor, alignment, wave, audio, seek, client, peakCalls, correctionCalls };
}

describe('waveform zoom and visibility controls (PAR-COR-003)', () => {
    it('scales bars over a clamped time window anchored on audible media position and selects finer peaks', async () => {
        const { wave, audio } = await make();
        expect(wave.zoom.value).toBe(MIN_ZOOM);
        expect(wave.viewWindow.value).toEqual({ startMs: 0, endMs: 8000, durationMs: 8000 });
        // At 1x over 8 s (125 pps), fine level (80 Hz) yields 640 buckets across 1000 px.
        const fullBars = wave.bars.value;
        expect(fullBars.length).toBeGreaterThan(0);

        // Anchor near start: window clamps at 0.
        audio.currentTime = 0.5;
        audio.dispatchEvent(new Event('seeked'));
        expect(wave.positionMs.value).toBe(500);
        expect(wave.zoomIn()).toEqual({ ok: true, zoom: 2 });
        expect(wave.viewWindow.value).toEqual({ startMs: 0, endMs: 4000, durationMs: 8000 });
        expect(wave.playheadX.value).toBeCloseTo((500 / 4000) * 1000, 3);

        // Anchor in middle: window centers on 5.0 s -> [4000, 6000] at 4x.
        audio.currentTime = 5.0;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(wave.zoomIn()).toEqual({ ok: true, zoom: 4 });
        expect(wave.viewWindow.value).toEqual({ startMs: 4000, endMs: 6000, durationMs: 8000 });
        expect(wave.playheadX.value).toBeCloseTo(500, 3);

        // Anchor near end: window clamps at duration (8000 ms).
        audio.currentTime = 7.9;
        audio.dispatchEvent(new Event('seeked'));
        expect(wave.zoomIn()).toEqual({ ok: true, zoom: 8 });
        expect(wave.viewWindow.value).toEqual({ startMs: 7000, endMs: 8000, durationMs: 8000 });

        // Stepped through ZOOM_LEVELS up to MAX_ZOOM, bounded cleanly at extremes.
        expect(wave.zoomIn()).toEqual({ ok: true, zoom: MAX_ZOOM });
        expect(wave.zoomIn()).toEqual({ ok: false, zoom: MAX_ZOOM });
        expect(wave.bars.value.length).toBe(40); // 0.5 s at 80 Hz = 40 buckets
        expect(wave.zoomOut()).toEqual({ ok: true, zoom: 8 });
        expect(wave.resetZoom()).toEqual({ ok: true, zoom: MIN_ZOOM });
        expect(wave.zoomOut()).toEqual({ ok: false, zoom: MIN_ZOOM });
        expect(ZOOM_LEVELS).toEqual([1, 2, 4, 8, 16]);
        wave.dispose();
    });

    it('keeps barsFor backwards compatible without window and zooms cleanly with windowRange', () => {
        const fineData = new Int8Array(320);
        for (let i = 0; i < 160; i++) { fineData[i * 2] = -50; fineData[i * 2 + 1] = 50; }
        const peaks = {
            duration: 4,
            levels: [
                { rate: 40, length: 160, data: fineData },
                { rate: 10, length: 40, data: new Int8Array(80).fill(10) },
            ],
        };
        const full = barsFor(peaks, 40);
        expect(full).toHaveLength(40);
        const zoomed = barsFor(peaks, 40, { startSec: 1, endSec: 2 });
        expect(zoomed.length).toBe(40);
        expect(zoomed[0].x).toBe(0);
        expect(zoomed[0].y1).toBeCloseTo(-50 / 127, 4);
        expect(zoomed[0].y2).toBeCloseTo(50 / 127, 4);
    });

    it('slides the zoom window when playback or alignment leaves the visible range', async () => {
        const { wave, audio, alignment } = await make();
        audio.currentTime = 1.0;
        audio.dispatchEvent(new Event('seeked'));
        wave.setZoom(4); // 2000 ms window -> [0, 2000]
        expect(wave.viewWindow.value).toEqual({ startMs: 0, endMs: 2000, durationMs: 8000 });

        // Seek to second phrase at 5.4 s -> window moves to keep audible position & phrase visible.
        audio.currentTime = 5.4;
        audio.dispatchEvent(new Event('seeked'));
        alignment.highlightAt(5.4);
        expect(wave.viewWindow.value.startMs).toBeLessThanOrEqual(5400);
        expect(wave.viewWindow.value.endMs).toBeGreaterThanOrEqual(5400);
        expect(wave.currentPhrase.value?.text).toBe('Husten');
        expect(wave.playheadX.value).not.toBeNull();
        wave.dispose();
    });

    it('toggles visibility without extra requests and preserves audible position and current phrase', async () => {
        const { wave, audio, alignment, peakCalls, correctionCalls } = await make();
        const peaksBefore = peakCalls.length;
        const corrBefore = correctionCalls.length;

        expect(wave.visible.value).toBe(true);
        audio.currentTime = 1.0;
        audio.dispatchEvent(new Event('timeupdate'));
        alignment.highlightAt(1.0);
        expect(wave.currentPhrase.value?.text).toBe('Anamnese');

        // Hide waveform: no network calls, position and current phrase remain live.
        expect(wave.toggleVisible()).toBe(false);
        expect(wave.visible.value).toBe(false);
        expect(wave.announcement.value).toBe(LABELS.hidden);
        expect(peakCalls.length).toBe(peaksBefore);
        expect(correctionCalls.length).toBe(corrBefore);

        // Audio moves while hidden.
        audio.currentTime = 5.5;
        audio.dispatchEvent(new Event('timeupdate'));
        alignment.highlightAt(5.5);
        expect(wave.positionMs.value).toBe(5500);
        expect(wave.currentPhrase.value?.text).toBe('Husten');

        // Zoomed view re-syncs immediately when shown again, still with zero extra requests.
        wave.setZoom(4);
        audio.currentTime = 5.8;
        audio.dispatchEvent(new Event('seeked'));
        expect(wave.toggleVisible()).toBe(true);
        expect(wave.announcement.value).toBe(LABELS.shown);
        expect(wave.positionMs.value).toBe(5800);
        expect(wave.viewWindow.value.startMs).toBeLessThanOrEqual(5800);
        expect(wave.viewWindow.value.endMs).toBeGreaterThanOrEqual(5800);
        expect(peakCalls.length).toBe(peaksBefore);
        expect(correctionCalls.length).toBe(corrBefore);
        wave.dispose();
    });

    it('clips recognition marks and current phrase to the zoom window in CorrectionWaveform.setup', async () => {
        const { editor, alignment, client } = await make({ withAudio: false });
        const audio = new FakeAudio(8);
        const mounted = CorrectionWaveform.setup({
            editor,
            alignment,
            client,
            dictationId: D,
            recordingId: REC,
            audio,
            fetchFn: async () => ({ status: 200, arrayBuffer: async () => multiLevelPeaksBuffer(8) }),
            sleep: async () => {},
        });
        await mounted.load();
        audio.currentTime = 1.0;
        audio.dispatchEvent(new Event('seeked'));
        alignment.highlightAt(1.0);
        mounted.setZoom(4); // [0, 2000]
        const [m1, m2] = mounted.uncertainSpans.value;
        expect(mounted.rect(m1)).toEqual({ x: 300, w: 500 }); // 600..1600 inside 0..2000
        expect(mounted.rect(m2)).toBeNull(); // 5200..6000 outside 0..2000

        // Partial edge clip: window centered on 1400ms -> [400, 2400]; m1 (600..1600) fits, shift to 2500 -> [1500, 3500] clips m1 (600..1600 -> 1500..1600)
        audio.currentTime = 2.5;
        audio.dispatchEvent(new Event('seeked'));
        expect(mounted.viewWindow.value).toEqual({ startMs: 1500, endMs: 3500, durationMs: 8000 });
        expect(mounted.rect(m1)).toEqual({ x: 0, w: 50 }); // 1500..1600 = 100ms of 2000ms = 5% of 1000 = 50
        mounted.dispose();
    });

    it('resets zoom on audio revision change and removes media listeners on dispose', async () => {
        const { wave, editor, audio } = await make();
        wave.setZoom(4);
        expect(wave.zoom.value).toBe(4);
        editor.audio.value = { status: 'ready', revisionId: AUDIO2 };
        await wave.load();
        expect(wave.zoom.value).toBe(MIN_ZOOM);

        wave.dispose();
        audio.currentTime = 3.2;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(wave.positionMs.value).toBeNull();
    });

    it('keeps accessible keyboard controls, 48px targets, focus preservation, and compose-correction wiring', () => {
        const tpl = CorrectionWaveform.template;
        expect(tpl).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(tpl).toContain('data-testid="waveform-toggle"');
        expect(tpl).toContain(':aria-expanded="String(visible)"');
        expect(tpl).toContain('data-testid="waveform-zoom-out"');
        expect(tpl).toContain('data-testid="waveform-zoom-in"');
        expect(tpl).toContain('data-testid="waveform-zoom-reset"');
        expect(tpl).toContain('data-kind="playhead"');
        // Native buttons use aria-disabled instead of disabled so keyboard focus is not dropped at bounds,
        // and @mousedown.prevent keeps existing editor focus and selection on pointer click.
        expect(tpl).toContain(':aria-disabled="zoom <= MIN_ZOOM ? \'true\' : null"');
        expect(tpl).toContain(':aria-disabled="zoom >= MAX_ZOOM ? \'true\' : null"');
        expect(tpl).toContain('@mousedown.prevent');
        expect(tpl).toContain('min-height: 48px; min-width: 48px');
        // Toggle stays outside the v-if="visible" SVG block; currentPhrase and marks stay visible.
        expect(tpl.indexOf('data-testid="waveform-toggle"')).toBeLessThan(tpl.indexOf('v-if="visible" role="img"'));
        expect(tpl).toContain('clinic-correction-waveform-current');
        expect(tpl).toContain('clinic-correction-waveform-uncertain');
        // Compose-correction wires reactive audio element and seek to CorrectionWaveform.
        expect(composeSource).toContain(':audio="audioEl"');
        expect(composeSource).toContain(':seek="c.seek"');
    });
});
