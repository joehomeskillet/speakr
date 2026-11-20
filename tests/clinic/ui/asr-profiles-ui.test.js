import { describe, expect, it, vi } from 'vitest';
import { createAsrProfilesController } from '../../../static/js/clinic/asr-profiles-ui.js';

function ok(data) {
    return { outcome: 'ok', status: 200, data, message: '' };
}
function bad(outcome, code, status) {
    return { outcome, status, code, message: 'msg ' + outcome };
}

function makeClient(routes) {
    const calls = [];
    return {
        calls,
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ path, method, body: options && options.body });
            const handler = routes[method + ' ' + path];
            return handler ? handler(options) : bad('UNKNOWN', 'UNKNOWN', null);
        }),
    };
}

const PROFILES = {
    version: 4,
    profile: { id: 'p1', name: 'Klinik Turbo', version: 7 },
    source: 'headset',
    live_profile_id: 'l1',
    batch_profile_id: 'b1',
    profiles: [
        { id: 'l1', name: 'Live GPU', mode: 'live' },
        { id: 'l2', name: 'Live CPU', mode: 'live' },
        { id: 'b1', name: 'Batch GPU', mode: 'batch' },
    ],
    output_format: { container: 'wav', codec: 'pcm_s16le', sample_rate: 16000 },
    engine: { configured: 'gpu', actual: 'cpu', reason: 'GPU nicht verfügbar' },
    wait: { offer_manual_after_s: 90 },
    capabilities: { configured: ['gpu_turbo', 'cpu_fallback'], proven: ['cpu_fallback'] },
};

const routes = (extra) => Object.assign({ 'GET /asr-profiles': () => ok(PROFILES) }, extra || {});

describe('asr profiles ui', () => {
    it('distinguishes a configured engine from a verified transcription path', async () => {
        const status = { ...PROFILES, engine: { configured: 'cpu', actual: 'cpu', reason: 'nicht ermittelt' },
            capabilities: { configured: ['batch', 'live'], proven: [] } };
        const controller = createAsrProfilesController({ client: makeClient(routes({ 'GET /asr-profiles': () => ok(status) })) });
        await controller.load();
        expect(controller.view().engine.label).toBe('Nicht ermittelt');
        status.capabilities.proven = ['batch'];
        await controller.load();
        expect(controller.view().engine.label).toBe('CPU');
    });

    it('shows profile, source, format, actual engine and capability proof (positive)', async () => {
        const c = createAsrProfilesController({ client: makeClient(routes()) });
        expect(await c.load()).toBe(true);
        const v = c.view();
        expect(v.profile).toEqual({ id: 'p1', name: 'Klinik Turbo', version: 7 });
        expect(v.sources.map((s) => s.label)).toEqual(['Headset', 'Recorder']);
        expect(v.output).toEqual({ container: 'wav', codec: 'pcm_s16le', sampleRate: 16000 });
        expect(v.engine).toMatchObject({ configured: 'gpu', actual: 'cpu', label: 'CPU', fallback: true });
        expect(v.engine.fallbackLabel).toContain('CPU');
        expect(v.engine.surface.status).toBe('warning');
        expect(v.capabilities).toEqual([
            { name: 'gpu_turbo', proven: false, label: 'Nicht nachgewiesen' },
            { name: 'cpu_fallback', proven: true, label: 'Nachgewiesen' },
        ]);
        expect(v.a11y.sourceGroup.role).toBe('radiogroup');
    });

    it('keeps prompts off unless the server enables them', async () => {
        const c = createAsrProfilesController({ client: makeClient(routes()) });
        await c.load();
        expect(c.view().prompts).toEqual({ enabled: false, label: 'Aus' });
    });

    it('offers manual correction after the configured wait, or at once on failure', async () => {
        const c = createAsrProfilesController({ client: makeClient(routes()) });
        await c.load();
        expect(c.view('running', 89).manual.visible).toBe(false);
        expect(c.view('running', 90).manual).toEqual({ visible: true, label: 'Manuell schreiben' });
        expect(c.view('failed', 0).manual.visible).toBe(true);
        expect(c.view('succeeded', 500).manual.visible).toBe(false);
    });

    it('saves a new selection with the expected version and reloads', async () => {
        const puts = [];
        const client = makeClient(routes({
            'PUT /asr-profiles/selection': (o) => { puts.push(o.body); return ok({}); },
        }));
        const c = createAsrProfilesController({ client });
        await c.load();
        expect(c.setSource('recorder')).toBe(true);
        expect(c.setProfile('live', 'l2')).toBe(true);
        expect(c.setProfile('live', 'b1')).toBe(false);
        expect(c.setSource('mic')).toBe(false);
        expect(await c.save()).toBe(true);
        expect(puts).toEqual([{ source: 'recorder', live_profile_id: 'l2', batch_profile_id: 'b1', expected_version: 4 }]);
        expect(c.view().notice).toBe('Gespeichert.');
    });

    it('reports unauthorized and stale-version saves without losing the draft', async () => {
        let outcome = bad('permission', 'FORBIDDEN', 403);
        const c = createAsrProfilesController({
            client: makeClient(routes({ 'PUT /asr-profiles/selection': () => outcome })),
        });
        await c.load();
        c.setSource('recorder');
        expect(await c.save()).toBe(false);
        expect(c.view().error).toMatchObject({ outcome: 'permission', retryable: false });
        expect(c.view().errorSurface.status).toBe('error');
        outcome = bad('conflict', 'stale_version', 409);
        expect(await c.save()).toBe(false);
        expect(c.view().error.message).toBe('Der Stand ist veraltet. Bitte neu laden.');
        expect(c.view().sources.find((s) => s.selected).value).toBe('recorder');
    });

    it('marks network failures retryable and succeeds on retry', async () => {
        let fail = true;
        const c = createAsrProfilesController({
            client: makeClient({ 'GET /asr-profiles': () => (fail ? bad('network', 'NETWORK', null) : ok(PROFILES)) }),
        });
        expect(await c.load()).toBe(false);
        expect(c.view().error.retryable).toBe(true);
        fail = false;
        expect(await c.load()).toBe(true);
        expect(c.view().error).toBeNull();
    });

    it('drops selections the server no longer offers and resets on context switch', async () => {
        let data = PROFILES;
        const client = makeClient({ 'GET /asr-profiles': () => ok(data) });
        const c = createAsrProfilesController({ client });
        await c.load();
        data = Object.assign({}, PROFILES, { profiles: [{ id: 'b1', name: 'Batch GPU', mode: 'batch' }] });
        await c.switchContext({ accountId: 'u2' });
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: 'u2', dictationId: undefined });
        expect(c.view().live).toEqual([]);
        expect(c.view().notice).toBe('Die gewählte Auswahl ist nicht mehr verfügbar.');
    });

    it('moves the source with arrow keys and cancels with Escape', async () => {
        const c = createAsrProfilesController({ client: makeClient(routes()) });
        await c.load();
        expect(c.onKeydown({ key: 'ArrowRight' })).toBe(true);
        expect(c.view().sources.find((s) => s.selected).value).toBe('recorder');
        expect(c.onKeydown({ key: 'Escape' })).toBe(true);
        expect(c.view().sources.find((s) => s.selected).value).toBe('headset');
        expect(c.onKeydown({ key: 'a' })).toBe(false);
    });

    it('uses translations when present and German defaults otherwise', async () => {
        const t = (k) => (k === 'clinic.asr.save' ? 'Save' : k);
        const c = createAsrProfilesController({ client: makeClient(routes()), t });
        await c.load();
        expect(c.view().labels.save).toBe('Save');
        expect(c.view().labels.cancel).toBe('Abbrechen');
    });
});
