import { describe, expect, it, vi } from 'vitest';

import {
    LIST_COLUMNS,
    TEXT,
    WorklistView,
    cellText,
    createWorklistViewController,
    flattenGroups,
    roleText,
} from '../../../static/js/clinic/compose-worklist.js';
import { FIELDS, LABELS } from '../../../static/js/clinic/worklist-filters-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const layoutDoc = { version: 1, columns: [], sort: [], group_by: [] };

const row = (id, extra = {}) => ({
    dictation_id: id,
    status: 'zu_schreiben',
    priority: 1,
    duration_seconds: 60,
    author_user_id: 7,
    assignee_user_id: 7,
    dictated_by: { id: '7', display_name: 'Dr. Muster' },
    staff_pools: [{ id: 'sp1', name: 'Pool Nord' }, { id: 'sp2', name: 'Pool Sued' }],
    lease_holder: { id: '9', display_name: 'Frau Schreib', until: '2026-11-20T10:00:00Z' },
    revision: 1,
    etag: `"${id}:1"`,
    allowed_actions: [],
    ...extra,
});

function fakeClient(rows) {
    const run = async (path) => {
        if (path.startsWith('/worklist/rows')) return ok({ items: rows, total: rows.length });
        if (path.startsWith('/worklist/layout')) return ok({ version: 1, layout: layoutDoc });
        return ok({ items: [] });
    };
    return { request: vi.fn(run), command: vi.fn(run), switchContext: vi.fn() };
}

const controller = (rows) => createWorklistViewController({
    client: fakeClient(rows),
    currentUser: { id: 7, role: 'staff' },
    fetchChanges: vi.fn(async () => ({ outcome: 'ok', data: { changes: [], next_cursor: 'c', has_more: false } })),
    storage: null,
    intervalMs: 0,
    heartbeatMs: 30000,
});

describe('role columns (V8 P02)', () => {
    it('lists three separate role columns and no "Zuständig"/"Autor" label for a person', () => {
        const labels = Object.fromEntries(LIST_COLUMNS.map((c) => [c.key, c.label]));
        expect(labels.dictated_by).toBe('Diktiert von');
        expect(labels.staff_pools).toBe('Schreibkräfte-Pool');
        expect(labels.lease_holder).toBe('Übernommen von');
        expect(Object.values(labels)).not.toContain('Zuständig');
        expect(Object.values(labels)).not.toContain('Autor');
        expect(LIST_COLUMNS.map((c) => c.key)).not.toContain('author_user_id');
        expect(Object.values(LABELS.fields)).not.toContain('Zuständig');
    });

    it('renders the three columns with their own cell text', async () => {
        const c = controller([row('1')]);
        await c.start();
        const keys = c.columns().map((col) => col.key);
        for (const key of ['dictated_by', 'staff_pools', 'lease_holder']) expect(keys).toContain(key);
        expect(keys).not.toContain('author_user_id');
        const r = c.worklist.rows.value[0];
        expect(c.cell(r, 'dictated_by')).toBe('Dr. Muster');
        expect(c.cell(r, 'staff_pools')).toBe('Pool Nord, Pool Sued');
        expect(c.cell(r, 'lease_holder')).toBe('Frau Schreib');
        expect(c.columns().find((col) => col.key === 'lease_holder').label).toBe('Übernommen von');
        c.dispose();
    });

    it('shows empty states: no pool, no lease holder, unknown author', () => {
        expect(cellText(row('1', { staff_pools: [] }), 'staff_pools')).toBe(TEXT.noPool);
        expect(cellText(row('1', { lease_holder: null }), 'lease_holder')).toBe('–');
        expect(roleText('dictated_by', { id: '7', display_name: null })).toBe('7');
        expect(roleText('dictated_by', null)).toBe('–');
    });

    it('never renders a patient name, title or notes (W16)', () => {
        const text = ['dictated_by', 'staff_pools', 'lease_holder']
            .map((key) => cellText(row('1', { title: 'Mustermann', notes: 'N' }), key)).join('|');
        expect(text).not.toMatch(/Mustermann/);
        expect(WorklistView.template).not.toMatch(/row\.(title|notes|recipient)\b/);
    });

    it('group headings of a role column use the role text', () => {
        const items = flattenGroups(
            [{ path: [{ id: '7', display_name: 'Dr. Muster' }], rows: [row('1')], children: [] }],
            ['dictated_by'],
        );
        expect(items[0].label).toBe('Diktiert von: Dr. Muster');
    });

    it('filters by ID: dictated_by (author), staff pool and lease holder', () => {
        expect(FIELDS).toEqual(expect.arrayContaining(['author', 'staff_pool', 'lease_holder']));
        expect(LABELS.fields.author).toBe('Diktiert von (ID)');
        expect(LABELS.fields.staff_pool).toBe('Schreibkräfte-Pool (ID)');
        expect(LABELS.fields.lease_holder).toBe('Übernommen von (ID)');
    });
});

describe('preview panel only with a selection', () => {
    it('the aside is conditional on the selection and no column is reserved without it', () => {
        const t = WorklistView.template;
        expect(t.match(/<aside\b[^>]*>/g)).toHaveLength(1);
        expect(t).toMatch(/<aside v-if="c\.selectedId\.value"/);
        expect(t).toContain(`:class="c.selectedId.value ? 'lg:grid-cols-2' : ''"`);
        expect(t).not.toContain('lg:grid-cols-2">');
        expect(t).not.toContain('noSelection');
        expect(TEXT.noSelection).toBeUndefined();
    });

    it('selecting shows it; Escape, the close action and a vanished row remove it', async () => {
        const c = controller([row('1'), row('2')]);
        await c.start();
        expect(c.selectedId.value).toBe(null);
        const first = c.worklist.rows.value[0];
        c.select(first);
        expect(c.selectedId.value).toBe('1');
        c.onRowKey({ key: 'Escape', preventDefault() {} }, first);
        expect(c.selectedId.value).toBe(null);
        c.select(first);
        expect(c.selectedId.value).toBe('1');
        c.select(null); // close button and the aside's @keydown.esc
        expect(c.selectedId.value).toBe(null);
        expect(WorklistView.template).toContain('data-testid="close-detail"');
        expect(WorklistView.template).toContain('@keydown.esc="c.select(null)"');
        c.dispose();
    });
});
