import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    OutputView,
    createOutputViewController,
    registerOutputView,
    targetView,
} from '../../../static/js/clinic/compose-output.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-output.js'), 'utf8');

const ok = (data, status = 200) => ({ outcome: 'ok', status, data });
const fail = (outcome, status, code) => ({ outcome, status, code: code || null, message: '' });

const TARGETS = (released, kis = true) => ({
    dictation_id: 'd1',
    released_revision_id: released,
    targets: [
        { id: 'txt', channel: 'file', available: Boolean(released), reason: released ? null : 'not_released' },
        { id: 'docx', channel: 'file', available: Boolean(released), reason: released ? null : 'not_released' },
        { id: 'kis', channel: 'kis', available: Boolean(released) && kis, reason: released ? (kis ? null : 'kis_unavailable') : 'not_released' },
        { id: 'clipboard', channel: 'clipboard', available: true, reason: null },
    ],
});

function fakeClient({ targets, exportResult } = {}) {
    const calls = [];
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            if (method === 'GET' && path.endsWith('/output-targets')) return targets;
            if (method === 'POST' && path.endsWith('/exports')) return typeof exportResult === 'function' ? exportResult(options) : exportResult;
            if (method === 'GET' && path.includes('/exports/')) return ok({ id: 'j1', status: 'delivered', format: 'kis' });
            return fail('http', 404);
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}

const job = (extra = {}) => ({ id: 'j1', kind: 'file', status: 'ready', format: 'docx', revision_id: 'r1', filename: 'x.docx', ...extra });
const posts = (client) => client.calls.filter((c) => c.method === 'POST');

async function loaded(client, extra = {}) {
    let n = 0;
    const c = createOutputViewController({ client, dictationId: 'd1', newKey: () => 'key-' + (++n), ...extra });
    await c.load();
    return c;
}

describe('compose-output', () => {
    it('offers export only for the released revision', async () => {
        const client = fakeClient({ targets: ok(TARGETS(null)) });
        const c = await loaded(client);
        expect(c.releasedRevisionId.value).toBeNull();
        expect(c.canExport('docx')).toBe(false);
        expect(await c.exportTo('docx')).toBeNull();
        expect(posts(client)).toHaveLength(0);

        const rel = await loaded(fakeClient({ targets: ok(TARGETS('r1')) }));
        expect(rel.releasedRevisionId.value).toBe('r1');
        expect(rel.canExport('docx')).toBe(true);
        expect(rel.canExport('clipboard')).toBe(false);
    });

    it('sends the released revision id and format', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: ok(job(), 201) });
        const c = await loaded(client);
        await c.exportTo('docx');
        expect(posts(client)[0].path).toBe('/dictations/d1/exports');
        expect(posts(client)[0].options.body).toEqual({ revision_id: 'r1', format: 'docx' });
        expect(c.results.value.docx.phase).toBe('ready');
    });

    it('the same click twice sends one idempotency key', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: async () => { await gate; return ok(job(), 201); } });
        const c = await loaded(client);
        const first = c.exportTo('docx');
        const second = c.exportTo('docx');
        release();
        await Promise.all([first, second]);
        expect(posts(client)).toHaveLength(1);
        await c.exportTo('docx');
        const keys = posts(client).map((p) => p.options.idempotencyKey);
        expect(new Set(keys)).toEqual(new Set(['key-1']));
    });

    it('reuses the key after an unknown network result', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('network', null) });
        const c = await loaded(client);
        await c.exportTo('docx');
        await c.exportTo('docx');
        expect(posts(client).map((p) => p.options.idempotencyKey)).toEqual(['key-1', 'key-1']);
    });

    it('takes a new key after a conflict', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('conflict', 409, 'NOT_RELEASED') });
        const c = await loaded(client);
        await c.exportTo('docx');
        expect(c.results.value.docx.phase).toBe('conflict');
        await c.exportTo('docx');
        expect(posts(client).map((p) => p.options.idempotencyKey)).toEqual(['key-1', 'key-2']);
    });

    it('renders a disabled KIS adapter as unavailable, never as success', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1', false)) });
        const c = await loaded(client);
        const kis = c.targets.value.find((t) => t.id === 'kis');
        expect(kis.available).toBe(false);
        expect(kis.reason).toBe('nicht verfügbar');
        expect(c.canExport('kis')).toBe(false);
        expect(await c.exportTo('kis')).toBeNull();
        expect(posts(client)).toHaveLength(0);
    });

    it('maps a 503 from the export route to unavailable', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('network', 503, 'KIS_UNAVAILABLE') });
        const c = await loaded(client);
        await c.exportTo('kis');
        expect(c.results.value.kis.phase).toBe('unavailable');
        expect(c.results.value.kis.label).toBe('nicht verfügbar');
    });

    it('shows UNKNOWN as unknown and reconciles by reading the status only', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: ok({ id: 'j1', kind: 'kis', status: 'unknown', format: 'kis' }, 201) });
        const c = await loaded(client);
        await c.exportTo('kis');
        expect(c.results.value.kis.phase).toBe('unknown');
        await c.refreshStatus('kis');
        expect(c.results.value.kis.phase).toBe('delivered');
        expect(posts(client)).toHaveLength(1);
    });

    it('clipboard copy uses the existing module with the released revision', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')) });
        client.request.mockImplementation(async (path, options) => {
            if (path.endsWith('/output-targets')) return ok(TARGETS('r1'));
            if (path.endsWith('/outcome')) return ok({ status: 'CONFIRMED' });
            return ok({ 'text/plain': 'Text', 'text/html': '<p>Text</p>' });
        });
        const writeText = vi.fn(async () => {});
        const c = await loaded(client, { clipboard: { writeText, write: vi.fn() } });
        const copier = c.clipboardCopy();
        await copier.copy('plain');
        expect(writeText).toHaveBeenCalledWith('Text');
        expect(copier.state.value.phase).toBe('copied');
        const paths = client.request.mock.calls.map((x) => x[0]);
        expect(paths).toContain('/dictations/d1/clipboard/r1');
        expect(c.clipboardCopy()).toBe(copier);
    });

    it('offers no clipboard copy without a released revision', async () => {
        const c = await loaded(fakeClient({ targets: ok(TARGETS(null)) }));
        expect(c.clipboardCopy()).toBeNull();
    });

    it('fails closed when the targets cannot be loaded', async () => {
        const c = await loaded(fakeClient({ targets: fail('permission', 403) }));
        expect(c.phase.value).toBe('failed');
        expect(c.targets.value).toEqual([]);
    });

    it('requires a dictation id and registers the view', () => {
        expect(() => createOutputViewController({ client: {}, dictationId: '' })).toThrow();
        const app = { component: vi.fn() };
        expect(registerOutputView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-output-view', OutputView);
        expect(targetView({ id: 'docx', channel: 'file', available: true }, null).available).toBe(false);
    });

    it('uses no browser storage and no patient fields', () => {
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
        expect(source).not.toMatch(/patient/i);
    });
});
