import { describe, expect, it, vi } from 'vitest';
import { LABELS, useWorklistLayout } from '../../../static/js/clinic/worklist-layout-ui.js';
import { flattenGroups, LIST_COLUMNS } from '../../../static/js/clinic/compose-worklist.js';

const columns = ['author', 'priority', 'status', 'id'].map((key) => ({ key, label: key }));
const ok = (data) => ({ outcome: 'ok', data });
function make(client = { request: vi.fn(async () => ok({ version: 0, layout: {} })) }) {
    return useWorklistLayout({ client, columns, newIdempotencyKey: () => 'layout-test-key' });
}

describe('PAR-WF-004 ordered layout controls', () => {
    it('groups real author DTOs by account identity and sorts their nested priority rows', () => {
        const layout = useWorklistLayout({ client: {}, columns: LIST_COLUMNS });
        const row = (id, author, priority, duration) => ({ id, dictated_by: { id: author, display_name: 'Muster' },
            priority, duration_seconds: duration });
        layout.setGroupLevel(0, 'dictated_by');
        layout.setGroupLevel(1, 'priority');
        layout.setSort('duration_seconds', 'asc');
        const groups = layout.applyToRows([row(1, 'a', 1, 30), row(2, 'a', 1, 10), row(3, 'b', 1, 20), row(4, 'a', 0, 5)]);
        expect(groups).toHaveLength(2);
        expect(groups[0].path).toEqual([{ id: 'a', display_name: 'Muster' }]);
        expect(groups[0].children.map((g) => g.rows.map((r) => r.id))).toEqual([[4], [2, 1]]);
        expect(groups[1].rows.map((r) => r.id)).toEqual([3]);
        expect(flattenGroups(groups, layout.state.layout.group_by)[0].label).toBe('Diktiert von: Muster');
        layout.setGroupBy([]);
        layout.setSort('dictated_by', 'asc');
        expect(layout.applyToRows([
            { id: 1, dictated_by: { id: 'a', display_name: 'Zoe' } },
            { id: 2, dictated_by: { id: 'b', display_name: 'Ada' } },
        ])[0].rows.map((r) => r.id)).toEqual([2, 1]);
    });

    it('groups by author then priority and preserves sorted leaf rows in the composed list', () => {
        const layout = make();
        layout.setGroupLevel(0, 'author');
        layout.setGroupLevel(1, 'priority');
        layout.setSort('id', 'desc');
        const rows = [
            { id: 1, author: 'Ada', priority: 1 },
            { id: 2, author: 'Ben', priority: 0 },
            { id: 3, author: 'Ada', priority: 1 },
            { id: 4, author: 'Ada', priority: 0 },
        ];
        const groups = layout.applyToRows(rows);
        expect(groups.map((g) => g.path)).toEqual([['Ada'], ['Ben']]);
        expect(groups[0].children.map((g) => g.path)).toEqual([['Ada', 0], ['Ada', 1]]);
        expect(groups[0].children[1].rows.map((r) => r.id)).toEqual([3, 1]);
        const items = flattenGroups(groups, layout.state.layout.group_by);
        expect(items.filter((i) => i.kind === 'group').map((i) => i.depth)).toEqual([0, 1, 1, 0, 1]);
        expect(items.filter((i) => i.kind === 'row').map((i) => i.row.id)).toEqual([4, 3, 1, 2]);
        expect(rows.map((r) => r.id)).toEqual([1, 2, 3, 4]);
    });

    it('allows three ordered levels, rejects gaps and duplicates, and trims cleared levels', () => {
        const layout = make();
        layout.setGroupLevel(1, 'priority');
        expect(layout.state.layout.group_by).toEqual([]);
        layout.setGroupLevel(0, 'author');
        layout.setGroupLevel(1, 'priority');
        layout.setGroupLevel(2, 'status');
        for (const [level, key] of [[3, 'id'], [-1, 'id'], [0.5, 'id'], [0, 'priority'], [0, 'unknown']]) {
            layout.setGroupLevel(level, key);
        }
        expect(layout.state.layout.group_by).toEqual(['author', 'priority', 'status']);
        layout.setPage(4);
        layout.setGroupLevel(1, 'id');
        expect(layout.state.layout.group_by).toEqual(['author', 'id', 'status']);
        expect(layout.state.page).toBe(1);
        layout.setGroupLevel(1, '');
        expect(layout.state.layout.group_by).toEqual(['author']);
        layout.setGroupLevel(0, '');
        expect(layout.state.layout.group_by).toEqual([]);
        layout.state.canEdit = false;
        layout.setGroupLevel(0, 'author');
        expect(layout.state.layout.group_by).toEqual([]);
    });

    it('groups pool sets and lease holders independently of DTO object identity', () => {
        const layout = useWorklistLayout({ client: {}, columns: LIST_COLUMNS });
        layout.setGroupBy(['staff_pools', 'lease_holder']);
        layout.setSort('staff_pools', 'asc');
        const pool = (id, name) => ({ id, name });
        const groups = layout.applyToRows([
            { id: 1, staff_pools: [pool('b', 'West'), pool('a', 'Ost')], lease_holder: { id: '7', display_name: 'Ada', until: 'one' } },
            { id: 2, staff_pools: [pool('a', 'Ost'), pool('b', 'West')], lease_holder: { id: '7', display_name: 'Ada', until: 'two' } },
            { id: 3, staff_pools: [], lease_holder: null },
        ]);
        expect(groups).toHaveLength(2);
        expect(groups[0].rows.map((r) => r.id)).toEqual([3]);
        expect(groups[1].children).toHaveLength(1);
        expect(groups[1].children[0].rows.map((r) => r.id)).toEqual([1, 2]);
    });

    it('saves column order and grouping through the account API and restores a fresh controller', async () => {
        let saved = { version: 0, layout: {} };
        const client = { request: vi.fn(async (_path, options) => {
            if (options.method === 'PUT') saved = { version: saved.version + 1, layout: structuredClone(options.body) };
            return ok(structuredClone(saved));
        }) };
        const layout = make(client);
        await layout.load();
        layout.move('author', -1);
        layout.move('id', 1);
        expect(layout.state.dirty).toBe(false);
        layout.move('author', 1);
        layout.move('status', -1);
        layout.setGroupLevel(0, 'author');
        layout.setGroupLevel(1, 'priority');
        layout.setSort('id', 'desc');
        expect(await layout.save()).toBe(true);
        const fresh = make(client);
        await fresh.load();
        expect(fresh.state.layout).toEqual(layout.state.layout);
        expect(fresh.state.layout.columns.map((c) => c.key)).toEqual(['priority', 'status', 'author', 'id']);
        expect(fresh.state.layout.group_by).toEqual(['author', 'priority']);
        expect(fresh.state.layout.sort).toEqual([['id', 'desc']]);
        expect(client.request.mock.calls[1][1].ifMatch).toBe('"0"');
    });

    it.each(['columns', 'sort', 'group_by'])('rejects an unknown %s field without writing or discarding edits', async (field) => {
        const client = { request: vi.fn(async () => ok({ version: 0, layout: {} })) };
        const layout = make(client);
        await layout.load();
        layout.move('author', 1);
        layout.state.layout[field].push(field === 'columns' ? { key: 'unknown', width: 120, visible: true }
            : field === 'sort' ? ['unknown', 'asc'] : 'unknown');
        const before = structuredClone(layout.state.layout);
        expect(await layout.save()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
        expect(layout.state.layout).toEqual(before);
        expect(layout.state.dirty).toBe(true);
        expect(layout.banner()).toMatchObject({ role: 'alert', text: LABELS.unknownColumn });
    });
});
