import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    FILTER_ALL, JobsView, TEXT, createJobsViewController, errorText, filterOptions, registerJobsView,
} from '../../../static/js/clinic/compose-jobs.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-jobs.js'), 'utf8');

const RID = '22222222-2222-4222-8222-222222222222';
const CORR = '11111111-1111-4111-8111-111111111111';

const health = (failures) => ({
    outcome: 'ok', status: 200, code: null, correlationId: null, message: '',
    data: {
        counts: { queued: 2, processing: 1, completed: 5, failed: failures.length },
        stuck_ids: [9],
        recent_failures: failures,
    },
});
const row = (id, extra) => Object.assign({ id, job_type: 'transcribe', error_class: 'Timeout', message: 'Geheimer Befundtext', version: 3 }, extra || {});
const denied = { outcome: 'permission', status: 403, code: 'FORBIDDEN', correlationId: CORR, message: 'm' };

describe('compose-jobs', () => {
    it('renders health and list from the jobs-ui contract without free text', async () => {
        const client = { request: vi.fn().mockResolvedValue(health([row(4), row(5, { state: 'dead' })])) };
        const c = createJobsViewController({ client, canRetry: true });
        await c.load();
        expect(client.request).toHaveBeenCalledWith('/jobs/health');
        expect(c.state.value.progress).toMatchObject({ completed: 5, total: 10, failed: 2 });
        expect(c.state.value.stuckIds).toEqual([9]);
        expect(c.rows.value.map((r) => r.id)).toEqual([4, 5]);
        expect(JSON.stringify(c.rows.value)).toContain('Geheimer Befundtext'); // kept by jobs-ui model
        expect(JobsView.template).not.toMatch(/\.message\b/); // but never rendered
        expect(JobsView.template).toContain('c.state.value.progress.completed');
    });

    it('filters by state and falls back to all when the state disappears', async () => {
        const client = { request: vi.fn().mockResolvedValue(health([row(4), row(5, { state: 'dead' }), row(6, { error_class: 'UNKNOWN' })])) };
        const c = createJobsViewController({ client, canRetry: true });
        await c.load();
        expect(c.filterOptions.value).toEqual([FILTER_ALL, 'dead', 'failed', 'unknown']);
        c.setFilter('dead');
        expect(c.rows.value.map((r) => r.id)).toEqual([5]);
        c.setFilter('unknown');
        expect(c.rows.value.map((r) => r.id)).toEqual([6]);
        c.setFilter('gone');
        expect(c.rows.value).toHaveLength(3);
        expect(filterOptions([])).toEqual([FILTER_ALL]);
    });

    it('sends exactly one retry request per click, also on a double click', async () => {
        let release;
        const pending = new Promise((resolve) => { release = resolve; });
        const client = { request: vi.fn()
            .mockResolvedValueOnce(health([row(4)]))
            .mockReturnValueOnce(pending)
            .mockResolvedValueOnce(health([])) };
        const c = createJobsViewController({ client, canRetry: true, newRequestId: () => RID });
        await c.load();
        const first = c.retry(4);
        const second = await c.retry(4);
        expect(second.outcome).toBe('busy');
        release({ outcome: 'ok', data: { state: 'queued' } });
        await first;
        const posts = client.request.mock.calls.filter(([path]) => path === '/jobs/4/retry');
        expect(posts).toHaveLength(1);
        expect(posts[0][1]).toMatchObject({ method: 'POST', idempotencyKey: RID, ifMatch: '"3"', body: { request_id: RID, expected_version: 3 } });
        expect(c.rows.value).toEqual([]);
    });

    it('offers no retry and sends no request without the operations right', async () => {
        const client = { request: vi.fn().mockResolvedValue(health([row(4)])) };
        const c = createJobsViewController({ client, canRetry: false });
        await c.load();
        expect(c.canRetryJob(c.rows.value[0])).toBe(false);
        expect((await c.retry(4)).outcome).toBe('permission');
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('shows the permission message on 403 for load and for retry', async () => {
        const client = { request: vi.fn().mockResolvedValueOnce(denied) };
        const c = createJobsViewController({ client, canRetry: true });
        await c.load();
        expect(c.denied.value).toBe(true);
        expect(c.errorText()).toBe(TEXT.denied);
        expect(c.rows.value).toEqual([]);

        const client2 = { request: vi.fn()
            .mockResolvedValueOnce(health([row(4)]))
            .mockResolvedValueOnce(denied) };
        const c2 = createJobsViewController({ client: client2, canRetry: true, newRequestId: () => RID });
        await c2.load();
        await c2.retry(4);
        expect(c2.denied.value).toBe(true);
        expect(c2.errorText()).toBe(TEXT.denied);
        expect(errorText({ outcome: 'network', message: 'Netz', correlationId: CORR })).toContain(CORR);
    });

    it('routes UNKNOWN to reconciliation and ignores calls after dispose', async () => {
        const client = { request: vi.fn().mockResolvedValue(health([row(7, { error_class: 'UNKNOWN' })])) };
        const onReconcile = vi.fn();
        const c = createJobsViewController({ client, canRetry: true, onReconcile });
        await c.load();
        expect((await c.retry(7)).outcome).toBe('reconcile');
        expect(onReconcile).toHaveBeenCalledWith(7);
        c.dispose();
        expect(await c.load()).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('registers the component, needs a client and uses no storage or raw colours', () => {
        const app = { component: vi.fn() };
        expect(registerJobsView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-jobs-view', JobsView);
        expect(() => createJobsViewController({})).toThrow('client is required');
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(JobsView.template).toContain('role="alert"');
        expect(JobsView.template).toContain('aria-live="polite"');
    });
});
