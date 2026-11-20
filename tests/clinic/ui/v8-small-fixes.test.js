import { describe, expect, it, vi } from 'vitest';
import { LABELS, useCorrectionWaveform } from '../../../static/js/clinic/correction-waveform-ui.js';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';

const D = 'd1';
const ok = (data) => ({ outcome: 'ok', status: 200, data });
const client = () => ({
    request: vi.fn(async (path) => {
        if (path.endsWith('/correction')) {
            return ok({ dictation_id: D, text_revision_id: 'r1', text: 'Hallo Welt', state: { version: 1 }, links: [], markers: [] });
        }
        if (path.endsWith('/audio-revisions/current')) return ok({ revision_id: 'a1', segments: [] });
        return { outcome: 'http', status: 404, code: null, message: 'nicht gefunden' };
    }),
    command: vi.fn(),
    switchContext: vi.fn(),
});
const problem = (status, code) => ({ status, clone() { return this; }, json: async () => ({ code }), arrayBuffer: async () => new ArrayBuffer(0) });

async function mount(get) {
    const c = createCorrectionViewController({ client: client(), dictationId: D, currentUser: { id: 1 }, contract: 'v8', peaksFetch: get });
    await c.load();
    const wave = useCorrectionWaveform({
        editor: c.editor, alignment: c.alignment, client: client(), dictationId: D,
        recordingId: c.peaksKey(), fetchFn: c.peaksFetch, sleep: async () => {},
    });
    return { c, wave };
}

describe('peaks error mapping', () => {
    it('shows the computation failure text for 500 PEAKS_FAILED', async () => {
        const { c, wave } = await mount(vi.fn(async () => problem(500, 'PEAKS_FAILED')));
        const res = await wave.load();
        expect(res.code).toBe('compute');
        expect(res.message).toBe('Wellenform konnte nicht berechnet werden.');
        expect(wave.status.value).toBe('error');
        expect(wave.announcement.value).toBe(LABELS.compute);
        c.dispose();
        wave.dispose();
    });

    it('keeps the missing-source text for 503 DEPENDENCY_UNAVAILABLE', async () => {
        const { c, wave } = await mount(vi.fn(async () => problem(503, 'DEPENDENCY_UNAVAILABLE')));
        const res = await wave.load();
        expect(res.code).toBe('none');
        expect(wave.announcement.value).toBe('Keine Wellenform verfügbar.');
        c.dispose();
        wave.dispose();
    });

    it('treats an unrelated 500 as a plain load failure', async () => {
        const { c, wave } = await mount(vi.fn(async () => problem(500, 'OTHER')));
        expect((await wave.load()).code).toBe('failed');
        c.dispose();
        wave.dispose();
    });
});
