import { describe, expect, it, vi } from 'vitest';

import { JobsView, createJobsViewController, scopeToDictation } from '../../../static/js/clinic/compose-jobs.js';
import { ActivityView, ACTIVITY_TEMPLATE, createActivityController } from '../../../static/js/clinic/compose-activity.js';

const D1 = '11111111-1111-4111-8111-111111111111';
const D2 = '22222222-2222-4222-8222-222222222222';

const health = (failures) => ({
    outcome: 'ok', status: 200, code: null, correlationId: null, message: '',
    data: { counts: { queued: 0, processing: 0, completed: 3, failed: failures.length }, stuck_ids: [], recent_failures: failures },
});
const row = (id, extra) => Object.assign({ id, job_type: 'transcribe', error_class: 'Timeout', message: '', version: 1 }, extra || {});

describe('jobs view scoped to one dictation', () => {
    const failures = [row(1, { dictation_id: D1 }), row(2, { dictation_id: D2 }), row(3, { dictation_id: D1, state: 'dead' }), row(4)];

    it('exposes the dictationId prop', () => {
        expect(Object.keys(JobsView.props)).toContain('dictationId');
        expect(JobsView.props.dictationId.default).toBe('');
    });

    it('shows only the jobs of that dictation', async () => {
        const client = { request: vi.fn().mockResolvedValue(health(failures)) };
        const c = createJobsViewController({ client, dictationId: D1 });
        await c.load();
        expect(c.scoped()).toBe(true);
        expect(c.rows.value.map((r) => r.id)).toEqual([1, 3]);
        expect(c.filterOptions.value).toEqual(['all', 'dead', 'failed']);
    });

    it('is unchanged without a dictationId', async () => {
        const client = { request: vi.fn().mockResolvedValue(health(failures)) };
        const c = createJobsViewController({ client });
        await c.load();
        expect(c.scoped()).toBe(false);
        expect(c.rows.value.map((r) => r.id)).toEqual([1, 2, 3, 4]);
    });

    it('never falls back to the global list when the rows name no dictation', async () => {
        const client = { request: vi.fn().mockResolvedValue(health([row(7), row(8)])) };
        const c = createJobsViewController({ client, dictationId: () => D1 });
        await c.load();
        expect(c.rows.value).toEqual([]);
        expect(scopeToDictation([{ id: 7 }], undefined, D1)).toEqual([]);
    });

    it('hides the global health line in the scoped template', () => {
        expect(JobsView.template).toMatch(/v-if="!c\.scoped\(\)"[^>]*data-testid="health"/);
    });
});

describe('activity view with a dictationId (history only)', () => {
    function fakeVue() {
        const hooks = { watchers: [], unmount: [] };
        return {
            hooks, reactive: (o) => o, ref: (v) => ({ value: v }), markRaw: (o) => o,
            computed: (fn) => ({ get value() { return fn(); } }),
            watch: (getter, cb) => hooks.watchers.push({ getter, cb }),
            onMounted: () => {}, onBeforeUnmount: (fn) => hooks.unmount.push(fn),
        };
    }
    const client = () => ({ request: vi.fn(async () => ({ outcome: 'ok', data: {} })), command: vi.fn(), switchContext: vi.fn() });

    it('opens the history and hides the statistics block', async () => {
        const c = client();
        const view = ActivityView.setup({ client: c, currentUser: { id: 5 }, dictationId: D1, vue: fakeVue() });
        expect(view.historyOnly.value).toBe(true);
        expect(view.state.dictationId).toBe(D1);
        expect(await view.load()).toBeNull();
        expect(c.request).not.toHaveBeenCalled();
    });

    it('keeps statistics and the id form without a dictationId', async () => {
        const c = client();
        const view = ActivityView.setup({ client: c, currentUser: { id: 5 }, dictationId: '', vue: fakeVue() });
        expect(view.historyOnly.value).toBe(false);
        await view.load();
        expect(c.request).toHaveBeenCalledTimes(1);
    });

    it('follows a changed dictationId', () => {
        const Vue = fakeVue();
        const props = { client: client(), currentUser: { id: 5 }, dictationId: D1, vue: Vue };
        const view = ActivityView.setup(props);
        const watcher = Vue.hooks.watchers.find((w) => w.getter() === D1);
        props.dictationId = D2;
        watcher.cb(D2);
        expect(view.state.dictationId).toBe(D2);
    });

    it('guards the statistics section and the id form in the template', () => {
        expect(ACTIVITY_TEMPLATE).toMatch(/<section v-if="!historyOnly"[^>]*data-testid="statistics-section"/);
        expect(ACTIVITY_TEMPLATE).toMatch(/<form v-if="!historyOnly"/);
        expect(createActivityController).toBeTypeOf('function');
    });
});
