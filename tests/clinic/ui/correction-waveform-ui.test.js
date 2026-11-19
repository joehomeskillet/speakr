import { describe, expect, it, vi } from 'vitest';
import { useCorrectionAlignment } from '../../../static/js/clinic/correction-alignment-ui.js';
import {
    CorrectionWaveform, LABELS, registerCorrectionWaveform, useCorrectionWaveform,
} from '../../../static/js/clinic/correction-waveform-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const AUDIO = '33333333-3333-4333-8333-333333333333';
const AUDIO2 = '44444444-4444-4444-8444-444444444444';
const REC = '55555555-5555-4555-8555-555555555555';
const base = '/dictations/' + D;

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const TEXT = 'Befund unauffällig heute noch';

// Synthetic SPK1 peaks: one level, 10 buckets per second, 4 seconds.
function peaksBuffer() {
    const length = 40;
    const header = new TextEncoder().encode(JSON.stringify({ duration: 4, levels: [{ rate: 10, length, offset: 0 }] }));
    const buf = new ArrayBuffer(8 + header.length + length * 2);
    const bytes = new Uint8Array(buf);
    bytes.set([0x53, 0x50, 0x4b, 0x31], 0);
    new DataView(buf).setUint32(4, header.length, true);
    bytes.set(header, 8);
    const data = new Int8Array(buf, 8 + header.length, length * 2);
    for (let i = 0; i < length; i++) { data[i * 2] = -20 - i; data[i * 2 + 1] = 30 + i; }
    return buf;
}

function links(uncertainIds = []) {
    const out = [];
    const re = /\S+/gu;
    let m;
    let t = 0;
    while ((m = re.exec(TEXT)) !== null) {
        const id = 'L' + out.length;
        const link = { id, origin: 'asr', text_start: m.index, text_end: m.index + m[0].length, audio_start_ms: t, audio_end_ms: t + 1000 };
        if (uncertainIds.includes(id)) link.uncertain = true;
        out.push(link);
        t += 1000;
    }
    return out;
}

function mockEditor(revisionId = AUDIO) {
    return {
        text: { value: TEXT },
        textRevisionId: { value: REV },
        audio: { value: { status: 'ready', revisionId } },
    };
}

function routes(uncertainIds) {
    return {
        ['GET ' + base + '/correction']: ok({ dictation_id: D, text_revision_id: REV, text: TEXT, links: links(uncertainIds), markers: [] }),
        ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: AUDIO, segments: [] }),
    };
}

function mockClient(table) {
    return {
        request: vi.fn(async (path, options) => {
            const hit = table[((options && options.method) || 'GET') + ' ' + path];
            return hit || { outcome: 'UNKNOWN', status: 500, data: null };
        }),
    };
}

function peaksFetch(status, calls) {
    return vi.fn(async (url) => {
        if (calls) calls.push(url);
        return { status, arrayBuffer: async () => peaksBuffer() };
    });
}

async function make({ uncertain = [], fetchFn = peaksFetch(200), editor = mockEditor(), recordingId = REC } = {}) {
    const client = mockClient(routes(uncertain));
    const seek = vi.fn();
    const alignment = useCorrectionAlignment({ editor, client, dictationId: D, seek });
    await alignment.refresh();
    const wave = useCorrectionWaveform({ editor, alignment, client, dictationId: D, recordingId, fetchFn, sleep: async () => {} });
    return { editor, alignment, wave, seek, client, fetchFn };
}

describe('correction waveform ui', () => {
    it('loads peaks of the recording and draws bars for the selected revision', async () => {
        const calls = [];
        const { wave } = await make({ fetchFn: peaksFetch(200, calls) });
        expect((await wave.load()).ok).toBe(true);
        expect(calls).toEqual(['/audio/' + REC + '/peaks']);
        expect(wave.status.value).toBe('ready');
        expect(wave.peaks.value.duration).toBe(4);
        expect(wave.bars.value.length).toBeGreaterThan(0);
        expect(wave.bars.value[0].y1).toBeLessThan(0);
        expect(wave.bars.value[0].y2).toBeGreaterThan(0);
    });

    it('shows the current phrase from the alignment playback position', async () => {
        const { wave, alignment } = await make();
        await wave.load();
        expect(wave.currentPhrase.value).toBeNull();
        alignment.highlightAt(1.5);
        expect(wave.currentPhrase.value).toEqual({
            id: 'L1', start: 7, end: 18, text: 'unauffällig', startMs: 1000, endMs: 2000,
        });
    });

    it('lists only server-flagged uncertain spans and never invents any', async () => {
        const none = await make();
        await none.wave.load();
        expect(none.wave.uncertainSpans.value).toEqual([]);
        expect(none.wave.uncertaintyKnown.value).toBe(false);

        const flagged = await make({ uncertain: ['L2'] });
        await flagged.wave.load();
        expect(flagged.wave.uncertaintyKnown.value).toBe(true);
        expect(flagged.wave.uncertainSpans.value.map((s) => s.text)).toEqual(['heute']);
        const res = flagged.wave.seekToSpan(flagged.wave.uncertainSpans.value[0]);
        expect(res.ok).toBe(true);
        expect(flagged.seek).toHaveBeenCalledWith(2);
    });

    it('marks text typed after the last save as unaligned', async () => {
        const { wave, editor } = await make();
        await wave.load();
        expect(wave.unalignedSpans.value).toEqual([]);
        editor.text.value = TEXT + ' neu';
        expect(wave.unalignedSpans.value.map((s) => s.text.trim())).toEqual(['neu']);
    });

    it('answers 403 with a denied state and no peaks', async () => {
        const { wave } = await make({ fetchFn: peaksFetch(403) });
        const res = await wave.load();
        expect(res).toMatchObject({ ok: false, code: 'denied', message: LABELS.denied });
        expect(wave.status.value).toBe('denied');
        expect(wave.peaks.value).toBeNull();
    });

    it('treats peaks of an earlier audio revision as stale', async () => {
        const { wave, editor } = await make();
        await wave.load();
        editor.audio.value = { status: 'ready', revisionId: AUDIO2 };
        expect(wave.status.value).toBe('stale');
        expect(wave.peaks.value).toBeNull();
        expect(wave.bars.value).toEqual([]);
        await wave.load();
        expect(wave.status.value).toBe('ready');
    });

    it('reports failure, then recovers on retry', async () => {
        let status = 500;
        const fetchFn = vi.fn(async () => ({ status, arrayBuffer: async () => peaksBuffer() }));
        const { wave } = await make({ fetchFn });
        expect((await wave.load()).code).toBe('failed');
        expect(wave.status.value).toBe('error');
        status = 200;
        expect((await wave.load()).ok).toBe(true);
        expect(wave.status.value).toBe('ready');
    });

    it('reports none for a missing recording and for 404', async () => {
        const absent = await make({ recordingId: null });
        expect((await absent.wave.load()).code).toBe('none');
        const gone = await make({ fetchFn: peaksFetch(404) });
        expect((await gone.wave.load()).code).toBe('none');
        expect(gone.wave.status.value).toBe('none');
    });

    it('waits while the server computes peaks (202) and ignores a result after dispose', async () => {
        let n = 0;
        const fetchFn = vi.fn(async () => { n += 1; return { status: n < 3 ? 202 : 200, arrayBuffer: async () => peaksBuffer() }; });
        const { wave } = await make({ fetchFn });
        expect((await wave.load()).ok).toBe(true);
        expect(n).toBe(3);
        const pending = wave.load();
        wave.dispose();
        expect((await pending).code).toBe('cancelled');
        expect(wave.peaks.value).toBeNull();
    });

    it('exports a component with tokens-only colours and registers it', () => {
        expect(CorrectionWaveform.name).toBe('ClinicCorrectionWaveform');
        expect(CorrectionWaveform.template).toContain('aria-label');
        expect(CorrectionWaveform.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        const app = { component: vi.fn() };
        registerCorrectionWaveform(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-waveform', CorrectionWaveform);
    });
});
