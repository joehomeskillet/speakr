import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    DetailView,
    TEXT,
    createDetailViewController,
    formatRecordingTime,
    registerDetailView,
    statusAxes,
} from '../../../static/js/clinic/compose-detail.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-detail.js'), 'utf8');

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: 'x' });

const detail = (extra = {}) => ({
    dictation_id: 'd1',
    etag: '"d1:3"',
    revision: 3,
    author: { user_id: 7, display_name: 'Dr. Synthetisch' },
    author_pool: { id: 'ap1', name: 'Autoren Nord' },
    staff_pools: [{ id: 'sp1', name: 'Schreibkräfte Nord' }],
    takeover: { user_id: 9, display_name: 'Frau Muster' },
    recording_time: { at: '2026-11-02T08:30:00Z', source: 'recorded' },
    duration_seconds: 75,
    priority: 128,
    deadline_at: null,
    case_display: 'F-SYN-0042',
    status_axes: { processing: 'SUCCEEDED', workflow: 'zu_korrigieren', acceptance: 'QUEUED', output: 'NOT_RELEASED' },
    ...extra,
});

const lease = () => ok({ lease_id: 'lease-1', generation: 1, expires_at: new Date(Date.now() + 600000).toISOString() });

/** Fake clinic client: `routes` maps "METHOD path-suffix" to a handler or a fixed result. */
function fakeClient(routes = {}) {
    const calls = [];
    const find = (method, path) => {
        const key = Object.keys(routes).find((k) => {
            const [m, p] = k.split(' ');
            return m === method && path.endsWith(p);
        });
        return key ? routes[key] : null;
    };
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            const hit = find(method, path);
            if (hit === null) return ok({ items: [] });
            return typeof hit === 'function' ? hit(path, options) : hit;
        }),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

const user = { id: 7, role: 'staff' };
const controllerFor = (client, extra = {}) => createDetailViewController({
    client, currentUser: user, dictationId: 'd1', heartbeatMs: 30000, ...extra,
});

describe('compose-detail contract', () => {
    it('exports the view and registers it as clinic-detail-view', () => {
        const app = { component: vi.fn() };
        registerDetailView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-detail-view', DetailView);
        expect(Object.keys(DetailView.props)).toEqual(expect.arrayContaining(['client', 'dictationId', 'currentUser', 'vue', 'origin']));
        expect(DetailView.emits).toEqual(['back', 'open-editor']);
    });

    it('imports history, lease and workflow actions instead of copying them', () => {
        for (const mod of ['history-ui', 'lease-ui', 'workflow-actions-ui', 'worklist-ui', 'compose-worklist']) {
            expect(source).toContain(`./${mod}.js`);
        }
        expect(source).toContain("from './tokens.js'");
        expect(source).not.toMatch(/function createLeaseController|function createWorkflowActions|function useDictationHistory/);
    });

    it('uses German labels, no colour literals, no browser storage and no patient name, title or notes', () => {
        const body = source.slice(source.indexOf('template:'));
        expect(TEXT.author).toBe('Diktiert von');
        expect(TEXT.pool).toBe('Schreibkräfte-Pool');
        expect(TEXT.takeover).toBe('Übernahme');
        expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(body).not.toMatch(/\b(row|d|data|c\.data\.value|header\(\))\.(title|notes|recipient|patient\w*)/);
    });

    it('requires a client, a current user id and a dictation id', () => {
        expect(() => createDetailViewController({ currentUser: user, dictationId: 'd1' })).toThrow(/client/);
        expect(() => createDetailViewController({ client: fakeClient(), dictationId: 'd1' })).toThrow(/currentUser/);
        expect(() => createDetailViewController({ client: fakeClient(), currentUser: user })).toThrow(/dictationId/);
    });
});

describe('render of the header', () => {
    it('reads one detail resource and shows author, pool, takeover, recording time and Fall-ID', async () => {
        const client = fakeClient({ 'GET /dictations/d1/detail': ok(detail()) });
        const c = controllerFor(client);
        await c.start();
        expect(client.calls[0].path).toBe('/dictations/d1/detail');
        expect(c.status.value).toBe('ready');
        const h = c.header();
        expect(h.author).toBe('Dr. Synthetisch');
        expect(h.pool).toBe('Schreibkräfte Nord');
        expect(h.takeover).toBe('Frau Muster');
        expect(h.recordingTime).toBe('02.11.2026, 09:30');
        expect(h.recordingFallback).toBe(false);
        expect(h.caseId).toBe('F-SYN-0042');
        expect(h.priority).toBe('Dringend');
        expect(h.duration).toBe('01:15');
        c.dispose();
    });

    it('marks a fallback recording time and shows no Fall-ID or takeover when absent', async () => {
        const client = fakeClient({
            'GET /dictations/d1/detail': ok(detail({
                recording_time: { at: '2026-11-02T08:30:00Z', source: 'fallback' }, case_display: null, takeover: null, staff_pools: [],
            })),
        });
        const c = controllerFor(client);
        await c.start();
        const h = c.header();
        expect(h.recordingFallback).toBe(true);
        expect(h.caseId).toBeNull();
        expect(h.takeover).toBe(TEXT.noTakeover);
        expect(h.pool).toBe(TEXT.noPool);
        expect(formatRecordingTime({ at: 'kaputt' })).toEqual({ text: TEXT.notAvailable, fallback: false });
        c.dispose();
    });

    it('keeps the four status axes separate, each with its own label', async () => {
        const axes = statusAxes(detail());
        expect(axes.map((a) => a.key)).toEqual(['processing', 'workflow', 'acceptance', 'output']);
        expect(axes.map((a) => a.text)).toEqual(['Erkennung abgeschlossen', 'Zu korrigieren', 'Wartet auf Prüfung', 'Nicht freigegeben']);
        expect(new Set(axes.map((a) => a.label)).size).toBe(4);
        expect(statusAxes({ status_axes: { workflow: 'neu_erfunden' } })[1].text).toBe('neu_erfunden');
        expect(statusAxes({})[0].text).toBe(TEXT.notAvailable);
    });

    it('renders every header field in the template', () => {
        const t = DetailView.components.DetailBody.template;
        for (const key of ['TEXT.author', 'TEXT.pool', 'TEXT.takeover', 'recording-time', 'fallback-mark', 'case-id', 'status-axes', 'data-testid="back"']) {
            expect(t).toContain(key);
        }
        expect(t.indexOf('data-testid="back"')).toBeLessThan(t.indexOf('detail-header'));
    });
});

describe('denied, failed and offline states', () => {
    it.each([[403, 'permission'], [404, 'UNKNOWN']])('a %i answer shows one neutral message and no data', async (status, outcome) => {
        const client = fakeClient({ 'GET /dictations/d1/detail': fail(outcome, status) });
        const c = controllerFor(client);
        await c.start();
        expect(c.status.value).toBe('denied');
        expect(c.message.value).toBe(TEXT.denied);
        expect(c.message.value).not.toMatch(/d1|nicht gefunden|existiert/i);
        expect(c.data.value).toBeNull();
        expect(c.header()).toBeNull();
        expect(c.canOpenEditor()).toBe(false);
        expect(c.workflow.visible()).toEqual([]);
        c.dispose();
    });

    it('a refusal on reload clears data that was shown before', async () => {
        let answer = ok(detail());
        const client = fakeClient({ 'GET /dictations/d1/detail': () => answer });
        const c = controllerFor(client);
        await c.start();
        expect(c.header()).not.toBeNull();
        answer = fail('permission', 403);
        await c.load();
        expect(c.header()).toBeNull();
        expect(c.status.value).toBe('denied');
        c.dispose();
    });

    it('an answer for another dictation is not shown', async () => {
        const client = fakeClient({ 'GET /dictations/d1/detail': ok(detail({ dictation_id: 'other' })) });
        const c = controllerFor(client);
        await c.start();
        expect(c.status.value).toBe('error');
        expect(c.header()).toBeNull();
        expect(c.message.value).toBe(TEXT.mismatch);
        c.dispose();
    });

    it('a server error offers a retry that loads again', async () => {
        let answer = fail('network', 500);
        answer = { outcome: 'UNKNOWN', status: 500, code: 'UNKNOWN', message: 'x' };
        const client = fakeClient({ 'GET /dictations/d1/detail': () => answer });
        const c = controllerFor(client);
        await c.start();
        expect(c.status.value).toBe('error');
        expect(c.message.value).toBe(TEXT.failed);
        answer = ok(detail());
        await c.load();
        expect(c.status.value).toBe('ready');
        c.dispose();
    });

    it('a network failure keeps the last confirmed header, marks it and locks actions', async () => {
        let answer = ok(detail({ allowed_actions: ['archive'] }));
        const client = fakeClient({ 'GET /dictations/d1/detail': () => answer });
        const c = controllerFor(client);
        await c.start();
        expect(c.workflow.visible().map((a) => a.id)).toEqual(['archive']);
        answer = fail('network', null);
        await c.load();
        expect(c.status.value).toBe('offline');
        expect(c.message.value).toBe(TEXT.offline);
        expect(c.header().author).toBe('Dr. Synthetisch');
        expect(c.offline()).toBe(true);
        expect(c.canOpenEditor()).toBe(false);
        expect(c.workflow.visible()).toEqual([]);
        c.dispose();
    });
});

describe('lease, workflow actions and history', () => {
    it('workflow actions come from the server answer through workflow-actions-ui', async () => {
        const client = fakeClient({
            'GET /dictations/d1/detail': ok(detail({ allowed_actions: ['reopen', 'archive', 'bogus'], transitions: { reopen: 'zu_korrigieren' } })),
        });
        const c = controllerFor(client);
        await c.start();
        expect(c.workflow.visible().map((a) => a.id)).toEqual(['reopen', 'archive']);
        expect(c.workflow.state.etag).toBe('"d1:3"');
        expect(c.workflow.state.revision).toBe(3);
        c.dispose();
    });

    it('a header without capabilities offers no workflow action', async () => {
        const c = controllerFor(fakeClient({ 'GET /dictations/d1/detail': ok(detail()) }));
        await c.start();
        expect(c.workflow.visible()).toEqual([]);
        c.dispose();
    });

    it('a successful workflow write reloads the header', async () => {
        const client = fakeClient({
            'GET /dictations/d1/detail': ok(detail({ allowed_actions: ['archive'] })),
            'POST /archive': ok({ etag: '"d1:4"' }),
        });
        const c = controllerFor(client);
        await c.start();
        c.workflow.request('archive');
        await c.workflow.confirm();
        await Promise.resolve();
        await Promise.resolve();
        expect(client.calls.filter((x) => x.method === 'GET' && x.path.endsWith('/detail'))).toHaveLength(2);
        c.dispose();
    });

    it('opening the editor requests the lease, tells the page and does not release it', async () => {
        const client = fakeClient({ 'GET /dictations/d1/detail': ok(detail()), 'POST /leases': lease(), 'POST /leases/release': ok({}) });
        const onOpenEditor = vi.fn();
        const c = controllerFor(client, { onOpenEditor });
        await c.start();
        expect(await c.openEditor()).toBe(true);
        expect(onOpenEditor).toHaveBeenCalledWith({ dictationId: 'd1' });
        c.dispose();
        expect(client.calls.some((x) => x.path.endsWith('/leases/release'))).toBe(false);
    });

    it('leaving the view without opening the editor releases a held lease', async () => {
        const client = fakeClient({ 'GET /dictations/d1/detail': ok(detail()), 'POST /leases': lease(), 'POST /leases/release': ok({}) });
        const c = controllerFor(client);
        await c.start();
        await c.leaseRun.acquire();
        expect(c.leaseState.value.status).toBe('held');
        c.dispose();
        await Promise.resolve();
        expect(client.calls.some((x) => x.path.endsWith('/leases/release'))).toBe(true);
        expect(c.data.value).toBeNull();
    });

    it('a foreign lease blocks opening the editor and the page is not told', async () => {
        const client = fakeClient({ 'GET /dictations/d1/detail': ok(detail()), 'POST /leases': fail('conflict', 423) });
        client.request.mockImplementation(async (path, options) => {
            if (path.endsWith('/leases')) return { outcome: 'permission', status: 423, code: 'LOCKED', message: 'x' };
            return ok(detail());
        });
        const onOpenEditor = vi.fn();
        const c = controllerFor(client, { onOpenEditor });
        await c.start();
        expect(await c.openEditor()).toBe(false);
        expect(onOpenEditor).not.toHaveBeenCalled();
        c.dispose();
    });

    it('history is the history-ui component bound to this dictation through the detail client', () => {
        const t = DetailView.components.DetailBody.template;
        expect(t).toContain('parts.History');
        expect(t).toContain(':dictation-id="dictationId"');
        expect(t).toContain('parts.Lease');
        expect(t).toContain('parts.Workflow');
    });
});

describe('back event', () => {
    it('hands the caller origin back unchanged so the list can restore filter, page, scroll and focus', async () => {
        const origin = { view: 'aufnahmen', filterId: 'f1', page: 3, scroll: 420, focusId: 'd1' };
        const onBack = vi.fn();
        const c = controllerFor(fakeClient({ 'GET /dictations/d1/detail': ok(detail()) }), { origin, onBack });
        await c.start();
        expect(c.back()).toBe(true);
        expect(onBack).toHaveBeenCalledTimes(1);
        expect(onBack.mock.calls[0][0].origin).toBe(origin);
        expect(onBack.mock.calls[0][0].dictationId).toBe('d1');
        expect(origin).toEqual({ view: 'aufnahmen', filterId: 'f1', page: 3, scroll: 420, focusId: 'd1' });
        c.dispose();
    });

    it('works from the denied state and without an origin, and never after dispose', async () => {
        const onBack = vi.fn();
        const c = controllerFor(fakeClient({ 'GET /dictations/d1/detail': fail('permission', 403) }), { onBack });
        await c.start();
        c.back();
        expect(onBack).toHaveBeenCalledWith({ dictationId: 'd1', origin: null });
        c.dispose();
        expect(c.back()).toBe(false);
        expect(onBack).toHaveBeenCalledTimes(1);
    });

    it('the view emits back with the same payload', () => {
        const emit = vi.fn();
        const body = DetailView.components.DetailBody;
        const origin = { page: 2 };
        const vue = { ref: (v) => ({ value: v }), shallowRef: (v) => ({ value: v }), reactive: (o) => o, computed: (fn) => ({ get value() { return fn(); } }) };
        const setup = body.setup({
            client: fakeClient(), dictationId: 'd1', currentUser: user, vue, origin,
        }, { emit });
        setup.c.back();
        expect(emit).toHaveBeenCalledWith('back', { dictationId: 'd1', origin });
        setup.c.dispose();
    });
});
