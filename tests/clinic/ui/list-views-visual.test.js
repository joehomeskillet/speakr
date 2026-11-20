import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { cellText, formatDateTime, WorklistView } from '../../../static/js/clinic/compose-worklist.js';
import { RecordingsView } from '../../../static/js/clinic/compose-recordings.js';
import { WorklistFiltersPanel } from '../../../static/js/clinic/worklist-filters-ui.js';
import { WorklistLayoutControls } from '../../../static/js/clinic/worklist-layout-ui.js';
import {
    collectOptions, createRecordingsController, createRecordingsMemory, optionList,
} from '../../../static/js/clinic/recordings-ui.js';

const item = (id, author, pools) => ({
    dictation_id: id, recording_id: 'r' + id, recorded_at: '2026-11-19T08:30:00Z', time_source: 'recorded',
    sort_time: '2026-11-19T08:30:00Z', day: '2026-11-19', dictated_by: author, staff_pools: pools,
    lease_holder: null, processing_status: 'completed', workflow_status: 'zu_schreiben', duration_ms: 65000, case_display: '',
});
const ok = (items) => ({ outcome: 'ok', status: 200, data: { items, next_cursor: null } });

describe('worklist list visuals', () => {
    it('formats instants as Zurich date and time and keeps other values as received', () => {
        expect(formatDateTime('2026-11-20T06:21:39.382Z')).toMatch(/^20\.11\.2026,? 07:21$/);
        expect(formatDateTime('morgen')).toBe('morgen');
        expect(cellText({ created_at: '2026-11-20T06:21:39Z' }, 'created_at')).toMatch(/07:21$/);
        expect(cellText({ deadline_at: null }, 'deadline_at')).toBe('–');
    });

    it('renders toolbar, popover and table with primitives, labels and test ids', () => {
        const view = WorklistView.template;
        for (const part of ['data-testid="refresh-button"', 'data-testid="last-update"', 'cl-table', 'pill-status', 'data-label', 'cl-state', 'cl-pager']) {
            expect(view).toContain(part);
        }
        const panel = WorklistFiltersPanel.template;
        for (const part of ['data-testid="filter-toggle"', 'data-testid="filter-count"', 'v-show="open"', '<slot name="start">', '<slot name="end">', 'class="field']) {
            expect(panel).toContain(part);
        }
        const layout = WorklistLayoutControls({ defineComponent: (o) => o, ref: (v) => ({ value: v }) }).template;
        for (const part of ['data-testid="view-toggle"', 'type="checkbox"', 'aria-expanded', 'LABELS.view']) expect(layout).toContain(part);
    });

    it('counts active conditions and the search text for the Filter badge', () => {
        const s = { draft: { conditions: [{}, {}] }, query: 'F-1' };
        expect(WorklistFiltersPanel.computed.activeCount.call({ s })).toBe(3);
        expect(WorklistFiltersPanel.computed.activeCount.call({ s: { draft: { conditions: [] }, query: ' ' } })).toBe(0);
    });

    it('uses tokens only in the list stylesheet and the templates', () => {
        const css = readFileSync('static/css/clinic-lists.css', 'utf8');
        expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(css).not.toMatch(/\b(rgb|rgba|hsl)\(/);
        for (const tpl of [WorklistView.template, RecordingsView.template, WorklistFiltersPanel.template]) {
            expect(tpl).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        }
        expect(readFileSync('templates/index.html', 'utf8')).toContain("css/clinic-lists.css");
    });
});

describe('recordings filter options by name', () => {
    it('collects names from the visible rows and sorts them', () => {
        const registry = collectOptions({ authors: new Map(), pools: new Map() }, [
            item(1, { id: 5, display_name: 'Dr. Zeller' }, [{ id: 2, name: 'Radiologie' }]),
            item(2, { id: 3, display_name: 'Dr. Amrein' }, [{ id: 1, name: 'Allgemein' }, { id: 2, name: 'Radiologie' }]),
            item(3, null, []),
        ]);
        expect(optionList(registry.authors, '').map((o) => o.label)).toEqual(['Dr. Amrein', 'Dr. Zeller']);
        expect(optionList(registry.pools, '').map((o) => o.value)).toEqual(['1', '2']);
        expect(optionList(registry.pools, '9').map((o) => o.value)).toContain('9');
    });

    it('keeps the choices after a filter narrows the rows and restores them on re-mount', async () => {
        const rows = [item(1, { id: 5, display_name: 'Dr. Zeller' }, [{ id: 2, name: 'Radiologie' }]), item(2, { id: 3, display_name: 'Dr. Amrein' }, [{ id: 1, name: 'Allgemein' }])];
        const queue = [ok(rows), ok([rows[0]])];
        const client = { request: vi.fn(async () => queue.shift() || ok([])) };
        const memory = createRecordingsMemory();
        const c = createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory });
        await c.start();
        expect(c.options.authors.map((o) => o.label)).toEqual(['Dr. Amrein', 'Dr. Zeller']);
        c.state.draft.author_id = '5';
        await c.applyFilters();
        expect(c.state.rows).toHaveLength(1);
        expect(c.options.authors).toHaveLength(2);
        expect(c.activeFilterCount()).toBe(1);
        expect(client.request.mock.calls[1][0]).toContain('author_id=5');
        c.dispose();
        const again = createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory });
        await again.start();
        expect(again.options.pools.map((o) => o.label)).toEqual(['Allgemein', 'Radiologie']);
    });

    it('offers names through a datalist, maps a name to its ID and keeps one primary and one secondary button', async () => {
        const tpl = RecordingsView.template;
        expect(tpl).toMatch(/<input[^>]*data-testid="filter-author"[^>]*list="crv-authors"/);
        expect(tpl).toMatch(/<input[^>]*data-testid="filter-pool"[^>]*list="crv-pools"/);
        expect(tpl).toContain('<datalist id="crv-authors">');
        expect(tpl).not.toContain('(ID)');
        const rows = [item(1, { id: 5, display_name: 'Dr. Zeller' }, [{ id: 2, name: 'Radiologie' }])];
        const client = { request: vi.fn(async () => ok(rows)) };
        const c = createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory: createRecordingsMemory() });
        await c.start();
        c.pick('author', 'Dr. Zeller');
        c.pick('pool', 'Radiologie');
        expect(c.state.draft).toMatchObject({ author_id: '5', pool_id: '2' });
        c.pick('author', '42');
        expect(c.state.draft.author_id).toBe('42');
        expect(c.picker.author).toBe('42');
        c.pick('author', 'Dr. Ze');
        expect(c.state.draft.author_id).toBe('');
        await c.resetFilters();
        expect(c.picker).toMatchObject({ author: '', pool: '' });
        expect(tpl).toContain('btn btn--primary');
        expect(tpl).toContain('data-testid="filters-toggle"');
    });
});
