import { describe, expect, it, vi } from 'vitest';
import {
    LABELS,
    groupRows,
    normalizeLayout,
    sortRows,
    useWorklistLayout,
} from '../../../static/js/clinic/worklist-layout-ui.js';

const COLUMNS = [
    { key: 'id', label: 'Nr.' },
    { key: 'author', label: 'Autor' },
    { key: 'priority', label: 'Priorität' },
];

const SERVER = {
    version: 3,
    layout: {
        version: 1,
        columns: [
            { key: 'priority', width: 90, visible: true },
            { key: 'gone', width: 90, visible: true },
            { key: 'author', width: 5000, visible: false },
        ],
        sort: [['author', 'desc'], ['gone', 'asc']],
        group_by: ['priority', 'gone'],
    },
};

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome) => ({ outcome, status: 500, code: null, message: 'x' });

function make(responses) {
    const calls = [];
    const queue = [...responses];
    const client = { request: vi.fn(async (path, opts) => { calls.push([path, opts]); return queue.shift(); }) };
    let n = 0;
    const layout = useWorklistLayout({ client, columns: COLUMNS, newIdempotencyKey: () => `key-${++n}` });
    return { layout, calls, client };
}

describe('worklist layout ui', () => {
    it('restores only valid fields after reload and appends new columns', async () => {
        const { layout } = make([ok(SERVER)]);
        await layout.load();
        const s = layout.state;
        expect(s.status).toBe('ready');
        expect(s.version).toBe(3);
        expect(s.layout.columns.map((c) => c.key)).toEqual(['priority', 'author', 'id']);
        expect(s.layout.columns[1]).toEqual({ key: 'author', width: 800, visible: false });
        expect(s.layout.sort).toEqual([['author', 'desc']]);
        expect(s.layout.group_by).toEqual(['priority']);
        expect(layout.orderedColumns().map((c) => c.label)).toEqual(['Priorität', 'Nr.']);
    });

    it('saves with If-Match and a stable idempotency key across a network retry', async () => {
        const { layout, calls } = make([ok(SERVER), fail('network'), ok({ ...SERVER, version: 4 })]);
        await layout.load();
        layout.move('priority', 1);
        expect(await layout.save()).toBe(false);
        expect(layout.state.status).toBe('error');
        expect(layout.state.dirty).toBe(true);
        expect(await layout.save()).toBe(true);
        expect(calls[1][1].method).toBe('PUT');
        expect(calls[1][1].ifMatch).toBe('"3"');
        expect(calls[1][1].body.columns[1].key).toBe('priority');
        expect(calls[2][1].idempotencyKey).toBe(calls[1][1].idempotencyKey);
        expect(layout.state.version).toBe(4);
        expect(layout.state.dirty).toBe(false);
    });

    it('reloads on a stale version and tells the user', async () => {
        const fresh = { ...SERVER, version: 5 };
        const { layout, calls } = make([ok(SERVER), fail('conflict'), ok(fresh)]);
        await layout.load();
        layout.resize('id', 20);
        expect(await layout.save()).toBe(false);
        expect(calls[2][1].method).toBe('GET');
        expect(layout.state.version).toBe(5);
        expect(layout.state.status).toBe('stale');
        expect(layout.state.message).toBe(LABELS.stale);
        expect(layout.banner().role).toBe('status');
    });

    it('becomes read-only when unauthorized and does not send a write', async () => {
        const { layout, client } = make([ok(SERVER), fail('permission')]);
        await layout.load();
        layout.cycleSort('id');
        await layout.save();
        expect(layout.state.canEdit).toBe(false);
        expect(layout.state.message).toBe(LABELS.readOnly);
        expect(layout.banner().role).toBe('alert');
        const before = client.request.mock.calls.length;
        layout.move('author', -1);
        layout.cycleSort('author');
        expect(await layout.save()).toBe(false);
        expect(client.request.mock.calls.length).toBe(before);
    });

    it('persists the standard order, visibility and sort only after a successful reset request', async () => {
        const columns = [...COLUMNS, { key: 'station', label: 'Station', visible: false }];
        const defaults = { version: 1, columns: [
            { key: 'id', width: 140, visible: true },
            { key: 'author', width: 140, visible: true },
            { key: 'priority', width: 140, visible: false },
        ], sort: [], group_by: [] };
        const saved = { ...SERVER, layout: { ...SERVER.layout, columns: [...SERVER.layout.columns,
            { key: 'station', width: 160, visible: true },
        ] } };
        const client = { request: vi.fn().mockResolvedValueOnce(ok(saved)).mockResolvedValueOnce(fail('network'))
            .mockImplementationOnce(async (_path, { body }) => ok({ version: 4, layout: body }))
            .mockImplementationOnce(async (_path, { body }) => ok({ version: 5, layout: body })) };
        const layout = useWorklistLayout({ client, columns, defaults, newIdempotencyKey: () => 'reset-key' });
        await layout.load();
        layout.setPage(3);
        expect(await layout.restoreDefaults()).toBe(false);
        expect(layout.state.layout.sort).toEqual([['author', 'desc']]);
        expect(layout.state.page).toBe(3);
        expect(await layout.restoreDefaults()).toBe(true);
        expect(client.request.mock.calls[2]).toEqual(['/worklist/layout', {
            method: 'PUT', body: { ...defaults, columns: [...defaults.columns,
                { key: 'station', width: 120, visible: false },
            ] }, ifMatch: '"3"', idempotencyKey: 'reset-key',
        }]);
        expect(layout.orderedColumns().map(({ key }) => key)).toEqual(['id', 'author']);
        expect(layout.state.page).toBe(1);
        expect(layout.banner()).toMatchObject({ role: 'status', text: 'Standardspalten wurden wiederhergestellt.' });
        expect(await layout.undoRestore()).toBe(true);
        expect(client.request.mock.calls[3][1]).toMatchObject({ method: 'PUT', ifMatch: '"4"' });
        expect(layout.orderedColumns().map(({ key }) => key)).toEqual(['priority', 'station', 'id']);
        expect(layout.state.layout.sort).toEqual([['author', 'desc']]);
        expect(layout.state.layout.group_by).toEqual(['priority']);
    });

    it('keeps paging stable while filters change and sort adds an id tie-breaker', async () => {
        const { layout } = make([ok(SERVER)]);
        await layout.load();
        layout.setPage(4);
        layout.setFilters({ q: 'a', state: 'open' });
        expect(layout.state.page).toBe(1);
        layout.setPage(4);
        layout.setFilters({ state: 'open', q: 'a' });
        expect(layout.state.page).toBe(4);
        expect(layout.query({ q: 'a' })).toEqual({
            q: 'a', sort: 'author:desc,id:asc', group_by: 'priority', page: 4,
        });
        layout.cycleSort('id');
        expect(layout.state.page).toBe(1);
        expect(layout.query().sort).toBe('id:asc,author:desc');
    });

    it('cycles sort asc, desc, off and exposes aria-sort', async () => {
        const { layout } = make([ok({ version: 0, layout: { version: 1, columns: [], sort: [], group_by: [] } })]);
        await layout.load();
        expect(layout.headerProps('author')['aria-sort']).toBe('none');
        layout.cycleSort('author');
        expect(layout.headerProps('author')['aria-sort']).toBe('ascending');
        layout.cycleSort('author');
        expect(layout.headerProps('author')['aria-label']).toBe('Autor, absteigend');
        layout.cycleSort('author');
        expect(layout.state.layout.sort).toEqual([]);
        expect(layout.headerProps('author').tabindex).toBe(0);
    });

    it('handles keyboard: Alt+arrow moves, Shift+arrow resizes, other keys pass', async () => {
        const { layout } = make([ok({ version: 0, layout: { version: 1, columns: [], sort: [], group_by: [] } })]);
        await layout.load();
        const ev = (key, mods) => ({ key, preventDefault: vi.fn(), ...mods });
        expect(layout.onKeydown('id', ev('ArrowRight', { altKey: true }))).toBe(true);
        expect(layout.state.layout.columns.map((c) => c.key)).toEqual(['author', 'id', 'priority']);
        layout.onKeydown('id', ev('ArrowLeft', { shiftKey: true }));
        expect(layout.state.layout.columns[1].width).toBe(110);
        expect(layout.onKeydown('id', ev('Tab'))).toBe(false);
        expect(layout.onKeydown('id', ev('Enter'))).toBe(true);
        expect(layout.state.layout.sort).toEqual([['id', 'asc']]);
    });

    it('clamps widths and limits grouping depth', async () => {
        expect(normalizeLayout({ columns: [{ key: 'id', width: 1, visible: true }] }, COLUMNS).columns[0].width).toBe(40);
        const { layout } = make([ok(SERVER)]);
        await layout.load();
        layout.setGroupBy(['id', 'author', 'priority', 'nope']);
        expect(layout.state.layout.group_by).toEqual(['id', 'author', 'priority']);
    });

    it('sorts and groups rows in memory only', () => {
        const rows = [
            { id: 1, author: 'Zoe', priority: 0 },
            { id: 2, author: 'Émile', priority: 128 },
            { id: 3, author: null, priority: 128 },
            { id: 4, author: 'adam', priority: 1 },
        ];
        expect(sortRows(rows, [['priority', 'asc']]).map((r) => r.id)).toEqual([2, 3, 4, 1]);
        expect(sortRows(rows, [['author', 'asc']]).map((r) => r.id)).toEqual([4, 2, 1, 3]);
        const groups = groupRows(rows, ['priority']);
        expect(groups.map((g) => g.path)).toEqual([[0], [128], [1]]);
        expect(groups[1].rows.map((r) => r.id)).toEqual([2, 3]);
    });
});
