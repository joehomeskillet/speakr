import { afterEach, expect, it, vi } from 'vitest';
import { CorrectionBody } from '../../../static/js/clinic/compose-correction.js';
import { createWorklistViewController } from '../../../static/js/clinic/compose-worklist.js';
import { FinishOptions } from '../../../static/js/clinic/finish-options-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const row = (dictation_id, revision = 1) => ({ dictation_id, revision, status: 'zu_korrigieren', author_user_id: 7 });
const key = (repeat = false) => ({ key: 'F5', repeat, preventDefault: vi.fn(), stopPropagation: vi.fn() });
const user = { id: 7, role: 'author' };

afterEach(() => vi.useRealTimers());

it('polls and refreshes the same filtered page, preserving the selection by dictation ID', async () => {
    vi.useFakeTimers();
    const scopedPath = '/worklist/rows?page=2&page_size=20&filter_id=f1&search=Befund';
    let snapshot = ok({ items: [row('selected'), row('other')], total: 22 });
    const client = { switchContext() {}, request: vi.fn(async (path) => path === scopedPath ? snapshot : ok({ items: [] })) };
    const list = createWorklistViewController({ client, currentUser: user, intervalMs: 1000, storage: null });
    try {
        await list.start();
        await list.applyFilter('f1', 'Befund');
        await list.goto(2);
        list.select(list.worklist.rows.value[0]);
        list.toggleChecked(list.worklist.selectedRow.value);
        client.request.mockClear();

        snapshot = ok({ items: [row('other'), row('selected', 2), row('new')], total: 23 });
        await vi.advanceTimersByTimeAsync(1000);
        expect(client.request.mock.calls.filter(([path]) => path.startsWith('/worklist/rows'))).toEqual([[scopedPath]]);
        expect(list.view).toMatchObject({ page: 2, filterId: 'f1', search: 'Befund', total: 23 });
        expect(list.selectedRow.value).toMatchObject(row('selected', 2));
        expect(list.worklist.selectedIndex.value).toBe(1);
        expect(list.checkedCount()).toBe(1);

        snapshot = ok({ items: [row('selected', 3), row('new')], total: 22 });
        const event = key();
        await list.onRefreshKey(event);
        await list.onRefreshKey(key(true));
        expect(event.preventDefault).toHaveBeenCalledOnce();
        expect(client.request.mock.calls.filter(([path]) => path.startsWith('/worklist/rows'))).toEqual([[scopedPath], [scopedPath]]);
        expect(list.view).toMatchObject({ page: 2, filterId: 'f1', search: 'Befund', total: 22 });
        expect(list.selectedRow.value).toMatchObject(row('selected', 3));
        expect(list.checkedCount()).toBe(1);
    } finally {
        list.dispose();
    }
});

it('discards a late poll response from the old scope', async () => {
    vi.useFakeTimers();
    let answerOldPoll;
    let requests = 0;
    const client = { switchContext() {}, request: vi.fn(async (path) => {
        if (path.includes('filter_id=f1')) return ok({ items: [row('filtered')], total: 1 });
        if (path === '/worklist/rows' && ++requests === 2) return new Promise((resolve) => { answerOldPoll = resolve; });
        return ok({ items: [] });
    }) };
    const list = createWorklistViewController({ client, currentUser: user, intervalMs: 1000, storage: null });
    try {
        await list.start();
        await vi.advanceTimersByTimeAsync(1000);
        await list.applyFilter('f1', '');
        answerOldPoll(ok({ items: [row('unfiltered')], total: 42 }));
        await vi.advanceTimersByTimeAsync(0);
        expect(list.worklist.rows.value.map((item) => item.dictation_id)).toEqual(['filtered']);
        expect(list.view).toMatchObject({ page: 1, filterId: 'f1', total: 1 });
    } finally {
        list.dispose();
    }
});

it('delegates editor F5 to the finish dialog and writes only after explicit confirmation', async () => {
    const ref = (value) => ({ value });
    const unmount = [];
    const vue = { ref, shallowRef: ref, computed: (read) => ({ get value() { return read(); } }),
        reactive: (value) => value, watch: () => () => {}, onBeforeUnmount: (fn) => unmount.push(fn),
        defineAsyncComponent: (loader) => loader };
    const client = { switchContext() {}, request: vi.fn(async (path) => path.endsWith('/leases')
        ? ok({ held: true, lease_id: 'lease', generation: 1 })
        : ok({ transcript_revision_id: 'transcript' })) };
    const view = CorrectionBody.setup({ vue, client, currentUser: user, dictationId: 'case' });
    try {
        const workflow = view.finishWorkflow;
        workflow.load({ dictationId: 'case', etag: '"case:2"', revision: 2, allowedActions: ['release'],
            binding: { release: { original_version_id: 'audio', transcript_revision_id: 'transcript',
                context_revision_id: 'context', text_sha256: 'hash' } } });
        const finish = FinishOptions.setup({ vue, client, workflow }, { expose: (api) => { view.finishOptions.value = api; } });
        finish.dialog.value = { showModal: vi.fn() };

        const event = key();
        view.onFinishKeydown(event);
        view.onFinishKeydown(key(true));
        view.onFinishKeydown(key());
        expect(event.preventDefault).toHaveBeenCalled();
        expect(event.stopPropagation).toHaveBeenCalledOnce();
        expect(finish.dialog.value.showModal).toHaveBeenCalledOnce();
        expect(finish.c.state.step).toBe('release');
        expect(client.request).not.toHaveBeenCalled();

        finish.c.state.content = finish.c.state.context = true;
        await Promise.all([finish.c.release(false), finish.c.release(false)]);
        expect(client.request.mock.calls.map(([path, options]) => [path.split('/').at(-1), options?.method ?? 'GET']))
            .toEqual([['leases', 'GET'], ['releases', 'POST']]);
    } finally {
        unmount.forEach((fn) => fn());
    }
});
