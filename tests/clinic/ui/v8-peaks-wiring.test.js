import { describe, expect, it, vi } from 'vitest';
import { useCorrectionAlignment } from '../../../static/js/clinic/correction-alignment-ui.js';
import { LABELS, useCorrectionWaveform } from '../../../static/js/clinic/correction-waveform-ui.js';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';

const D = 'd1';
const REV = 'a1';

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
    for (let i = 0; i < length; i++) { data[i * 2] = -20; data[i * 2 + 1] = 30; }
    return buf;
}

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const client = () => ({
    request: vi.fn(async (path) => {
        if (path.endsWith('/correction')) {
            return ok({ dictation_id: D, text_revision_id: 'r1', text: 'Hallo Welt', state: { version: 1 }, links: [], markers: [] });
        }
        if (path.endsWith('/audio-revisions/current')) return ok({ revision_id: REV, segments: [] });
        return { outcome: 'http', status: 404, code: null, message: 'nicht gefunden' };
    }),
    command: vi.fn(),
    switchContext: vi.fn(),
});
const user = { id: 1 };

async function mount(peaksFetch) {
    const c = createCorrectionViewController({ client: client(), dictationId: D, currentUser: user, contract: 'v8', peaksFetch });
    await c.load();
    const wave = useCorrectionWaveform({
        editor: c.editor, alignment: c.alignment, client: c.client, dictationId: D,
        recordingId: c.peaksKey(), fetchFn: c.peaksFetch, sleep: async () => {},
    });
    return { c, wave };
}

describe('peaks wiring in the correction view', () => {
    it('renders peaks fetched from the route of the current audio revision', async () => {
        const urls = [];
        const get = vi.fn(async (url) => { urls.push(url); return { status: 200, arrayBuffer: async () => peaksBuffer() }; });
        const { c, wave } = await mount(get);
        expect(c.peaksKey()).toBe(REV);
        expect((await wave.load()).ok).toBe(true);
        expect(urls).toEqual(['/api/clinic/v1/dictations/' + D + '/audio-revisions/' + REV + '/peaks']);
        expect(wave.status.value).toBe('ready');
        expect(wave.bars.value.length).toBeGreaterThan(0);
        c.dispose();
        wave.dispose();
    });

    it('shows the honest fallback text on 404', async () => {
        const get = vi.fn(async () => ({ status: 404, arrayBuffer: async () => new ArrayBuffer(0) }));
        const { c, wave } = await mount(get);
        const res = await wave.load();
        expect(res.ok).toBe(false);
        expect(res.code).toBe('none');
        expect(wave.status.value).toBe('none');
        expect(wave.announcement.value).toBe(LABELS.none);
        expect(LABELS.none).toBe('Keine Wellenform verfügbar.');
        expect(wave.bars.value).toEqual([]);
        c.dispose();
        wave.dispose();
    });

    it('maps only 503 DEPENDENCY_UNAVAILABLE to the unavailable state', async () => {
        const problem = (status, code) => ({ status, clone() { return this; }, json: async () => ({ code }), arrayBuffer: async () => new ArrayBuffer(0) });
        const gone = await mount(vi.fn(async () => problem(503, 'DEPENDENCY_UNAVAILABLE')));
        expect((await gone.wave.load()).code).toBe('none');
        gone.c.dispose();
        gone.wave.dispose();
        const other = await mount(vi.fn(async () => problem(503, 'SOMETHING_ELSE')));
        expect((await other.wave.load()).code).toBe('failed');
        other.c.dispose();
        other.wave.dispose();
    });

    it('does not request peaks without an audio revision', async () => {
        const get = vi.fn();
        const c = createCorrectionViewController({ client: client(), dictationId: D, currentUser: user, peaksFetch: get });
        expect(c.peaksKey()).toBeNull();
        expect((await c.peaksFetch('/x', {})).status).toBe(404);
        expect(get).not.toHaveBeenCalled();
        c.dispose();
    });
});
