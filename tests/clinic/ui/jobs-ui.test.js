import { describe, expect, it, vi } from 'vitest';
import { createJobsPanel, summarizeProgress, useJobs, LABELS, TRAY_AUTOSTART_PARITY } from '../../../static/js/clinic/jobs-ui.js';

const RID = '22222222-2222-4222-8222-222222222222';
const CORR = '11111111-1111-4111-8111-111111111111';

function health(failures) {
    return {
        outcome: 'ok', status: 200, code: null, correlationId: null, message: '',
        data: {
            counts: { queued: 2, processing: 1, completed: 5, failed: failures.length },
            stuck_ids: [9],
            oldest_queued_age_s: 3,
            recent_failures: failures,
        },
    };
}
const row = (id, extra) => Object.assign({ id, job_type: 'transcribe', error_class: 'Timeout', message: 'Zeitlimit', version: 3 }, extra || {});
const fail = (outcome, code) => ({ outcome, status: 409, code, correlationId: CORR, message: 'm' });

describe('jobs ui', () => {
    it('renders progress and safe failure info (positive)', async () => {
        const api = { request: vi.fn().mockResolvedValue(health([row(4)])) };
        const jobs = useJobs({ api, canRetry: true });
        await jobs.load();
        expect(api.request).toHaveBeenCalledWith('/jobs/health');
        expect(jobs.state.value.progress).toEqual({ queued: 2, running: 1, completed: 5, failed: 1, total: 9, percent: 56 });
        expect(jobs.state.value.failures[0]).toMatchObject({ id: 4, errorClass: 'Timeout', unknown: false });
        expect(jobs.state.value.stuckIds).toEqual([9]);
        expect(summarizeProgress(null).total).toBe(0);
    });

    it('retries once with idempotency key and quoted version, then reloads', async () => {
        const api = { request: vi.fn()
            .mockResolvedValueOnce(health([row(4)]))
            .mockResolvedValueOnce({ outcome: 'ok', data: { state: 'queued' } })
            .mockResolvedValueOnce(health([])) };
        const jobs = useJobs({ api, canRetry: true, newRequestId: () => RID });
        await jobs.load();
        await jobs.retry(4);
        expect(api.request.mock.calls[1]).toEqual(['/jobs/4/retry', {
            method: 'POST', body: { request_id: RID, expected_version: 3 }, idempotencyKey: RID, ifMatch: '"3"',
        }]);
        expect(jobs.state.value.failures).toEqual([]);
        expect(jobs.state.value.notice).toBe(LABELS.retried);
    });

    it('offers no retry when unauthorized and never calls the api', async () => {
        const api = { request: vi.fn().mockResolvedValue(health([row(4)])) };
        const jobs = useJobs({ api, canRetry: false, newRequestId: () => RID });
        await jobs.load();
        expect(jobs.canRetryJob(jobs.state.value.failures[0])).toBe(false);
        expect((await jobs.retry(4)).outcome).toBe('permission');
        expect(api.request).toHaveBeenCalledTimes(1);
    });

    it('clears rows and retry offer on a permission refusal', async () => {
        const api = { request: vi.fn().mockResolvedValueOnce(health([row(4)])).mockResolvedValueOnce(fail('permission', 'FORBIDDEN')) };
        const jobs = useJobs({ api, canRetry: true });
        await jobs.load();
        await jobs.load();
        expect(jobs.state.value.failures).toEqual([]);
        expect(jobs.state.value.authorized).toBe(false);
        expect(jobs.state.value.error.correlationId).toBe(CORR);
    });

    it('stale version reloads, keeps no key, shows notice', async () => {
        const api = { request: vi.fn()
            .mockResolvedValueOnce(health([row(4)]))
            .mockResolvedValueOnce(fail('conflict', 'REVISION_CONFLICT'))
            .mockResolvedValueOnce(health([row(4, { version: 4 })])) };
        const jobs = useJobs({ api, canRetry: true, newRequestId: () => RID });
        await jobs.load();
        await jobs.retry(4);
        expect(jobs.state.value.failures[0].version).toBe(4);
        expect(jobs.state.value.notice).toBe(LABELS.staleVersion);
    });

    it('reuses the key after a network failure and keeps the row visible', async () => {
        const ids = [RID, '33333333-3333-4333-8333-333333333333'];
        const api = { request: vi.fn()
            .mockResolvedValueOnce(health([row(4)]))
            .mockResolvedValue(fail('network', 'NETWORK')) };
        const jobs = useJobs({ api, canRetry: true, newRequestId: () => ids.shift() });
        await jobs.load();
        await jobs.retry(4);
        await jobs.retry(4);
        expect(api.request.mock.calls[1][1].idempotencyKey).toBe(RID);
        expect(api.request.mock.calls[2][1].idempotencyKey).toBe(RID);
        expect(jobs.state.value.failures).toHaveLength(1);
        expect(jobs.state.value.busy).toBeNull();
    });

    it('routes UNKNOWN to reconciliation without a retry call', async () => {
        const api = { request: vi.fn().mockResolvedValue(health([row(7, { error_class: 'UNKNOWN' })])) };
        const onReconcile = vi.fn();
        const jobs = useJobs({ api, canRetry: true, onReconcile });
        await jobs.load();
        expect(jobs.canRetryJob(jobs.state.value.failures[0])).toBe(false);
        expect((await jobs.retry(7)).outcome).toBe('reconcile');
        expect(onReconcile).toHaveBeenCalledWith(7);
        expect(api.request).toHaveBeenCalledTimes(1);
    });

    it('refuses a row without a version and keeps tray parity open', async () => {
        const api = { request: vi.fn().mockResolvedValue(health([row(4, { version: undefined })])) };
        const jobs = useJobs({ api, canRetry: true, newRequestId: () => RID });
        await jobs.load();
        expect((await jobs.retry(4)).outcome).toBe('permission');
        expect(TRAY_AUTOSTART_PARITY).toEqual({ gap: 'WG-OPS-004', status: 'open' });
    });

    it('component exposes labelled keyboard-operable markup and no storage', async () => {
        const h = (tag, a, b) => ({ tag, props: Array.isArray(a) || typeof a === 'string' ? {} : a, kids: b === undefined && (Array.isArray(a) || typeof a === 'string') ? a : b });
        const Vue = { ref: (v) => ({ value: v }), h };
        const api = { request: vi.fn().mockResolvedValue(health([row(4)])) };
        const { component, jobs } = createJobsPanel(Vue, { api, canRetry: true });
        const render = component.setup();
        await jobs.load();
        const tree = JSON.stringify(render());
        expect(tree).toContain('"role":"progressbar"');
        expect(tree).toContain('"aria-live":"polite"');
        expect(tree).toContain('Erneut versuchen, Auftrag 4');
        expect(tree).toContain('"type":"button"');
        expect(tree).toContain('var(--bg-danger-light)');
        expect(tree).toContain(LABELS.parity);
        expect(tree).not.toMatch(/#[0-9a-fA-F]{3,8}[;"]/);
    });
});
