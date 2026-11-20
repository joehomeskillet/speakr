import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

import {
    HOSTED_CSS,
    LIST_COLUMNS,
    WorklistView,
    cellText,
    dictationLabel,
    countBuckets,
    createWorklistViewController,
    sourceLabel,
    statusBucket,
    statusTone,
} from '../../../static/js/clinic/compose-worklist.js';
import { DetailView } from '../../../static/js/clinic/compose-detail.js';
import { WorklistFiltersPanel } from '../../../static/js/clinic/worklist-filters-ui.js';
import { WorklistLayoutControls, useWorklistLayout } from '../../../static/js/clinic/worklist-layout-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const row = (id, extra = {}) => ({
    dictation_id: id, status: 'zu_schreiben', priority: 1, duration_seconds: 60, author_user_id: 7,
    revision: 1, etag: '"' + id + ':1"', allowed_actions: [], ...extra,
});

function fakeClient(rows, layout) {
    const run = async (path) => {
        if (path.startsWith('/worklist/rows')) return ok({ items: rows, total: rows.length });
        if (path.startsWith('/worklist/layout')) return ok(layout);
        return ok({ items: [] });
    };
    return { request: vi.fn(run), command: vi.fn(async () => ok({})), switchContext: vi.fn() };
}

const feed = () => vi.fn(async () => ({ outcome: 'ok', data: { changes: [], next_cursor: 'c1', has_more: false } }));
const controllerFor = (rows, layout = { version: 1, layout: { version: 1, columns: [], sort: [], group_by: [] } }) => createWorklistViewController({
    client: fakeClient(rows, layout), currentUser: { id: 7, role: 'staff' }, fetchChanges: feed(), storage: null, intervalMs: 0,
});

describe('V8 status buckets', () => {
    it('sorts every workflow status into open, working or done and picks the pill tone', () => {
        expect(['aufgabe', 'neu_angelegt', 'vorbereitung_offline', 'vorbereitung_online', 'zu_schreiben'].map(statusBucket)).toEqual(Array(5).fill('open'));
        expect(['in_bearbeitung', 'zu_korrigieren', 'abzunehmen'].map(statusBucket)).toEqual(Array(3).fill('working'));
        expect(['abgenommen', 'fertiggestellt', 'zu_archivieren', 'zu_loeschen'].map(statusBucket)).toEqual(Array(4).fill('done'));
        expect(statusBucket('etwas_neues')).toBe('open');
        expect(statusTone('zu_schreiben')).toBe('neu');
        expect(statusTone('in_bearbeitung')).toBe('in-arbeit');
        expect(statusTone('abzunehmen')).toBe('wartet');
        expect(statusTone('fertiggestellt')).toBe('erledigt');
    });

    it('labels a dictation from its clinical timestamp, including older rows without a date', () => {
        expect(dictationLabel(row('internal-key', { created_at: '2026-11-20T09:15:00Z' }))).toBe('Diktat vom 20.11.2026, 10:15');
        expect(dictationLabel(row('internal-key'))).toBe('Diktat');
    });

    it('counts the loaded rows per bucket', () => {
        expect(countBuckets([row('1'), row('2', { status: 'in_bearbeitung' }), row('3', { status: 'abgenommen' }), null])).toEqual({ all: 3, open: 1, working: 1, done: 1 });
        expect(countBuckets(undefined)).toEqual({ all: 0, open: 0, working: 0, done: 0 });
    });

    it('labels the contract source kinds in German and keeps unknown kinds as given', () => {
        expect(sourceLabel('device')).toBe('Diktiergerät');
        expect(sourceLabel('browser')).toBe('Mikrofon');
        expect(sourceLabel('manual_file')).toBe('Upload');
        expect(sourceLabel('neu')).toBe('neu');
        expect(sourceLabel(null)).toBe('–');
        expect(cellText({ source_kind: 'device' }, 'source_kind')).toBe('Diktiergerät');
        expect(cellText({ source_kind: 'audio', source_label: 'Import' }, 'source_kind')).toBe('Import');
        expect(cellText({ assignee_user_id: 7, assignee: { display_name: 'Testperson' } }, 'assignee_user_id')).toBe('Testperson');
    });
});

describe('V8 worklist controller', () => {
    const rows = [row('1'), row('2', { status: 'in_bearbeitung', priority: 128 }), row('3', { status: 'abgenommen', priority: 0 })];

    it('KPI counts use all loaded rows while the status chips narrow the list', async () => {
        const c = controllerFor(rows);
        await c.start();
        expect(c.counts.value).toEqual({ all: 3, open: 1, working: 1, done: 1 });
        c.setChip('working');
        expect(c.shownRows().map((r) => r.dictation_id)).toEqual(['2']);
        expect(c.counts.value.all).toBe(3);
        c.setChip('nonsense');
        expect(c.view.chip).toBe('all');
        expect(c.shownRows()).toHaveLength(3);
        c.dispose();
    });

    it('shows the filter-empty state and closes the excluded detail when a chip has no matches', async () => {
        const c = controllerFor([rows[0]]);
        await c.start();
        c.select(rows[0]);
        c.setChip('done');
        expect(c.counts.value.all).toBe(1);
        expect(c.chipRows.value).toHaveLength(0);
        expect(c.selectedId.value).toBeNull();
        expect(c.pagerInfo()).toContain('0 sichtbar');
        expect(WorklistView.template).toMatch(/v-else-if="!c\.chipRows\.value\.length"[^>]*data-testid="empty-state"/);
        expect(WorklistView.template).toContain('c.worklist.rows.value.length ? c.text.noMatch : c.text.empty');
        c.setChip('all');
        expect(c.shownRows()).toHaveLength(1);
        c.dispose();
    });

    it('hides the list heading and its selection hint for an empty list', async () => {
        const c = controllerFor([]);
        await c.start();
        expect(c.chipRows.value).toHaveLength(0);
        expect(WorklistView.template).toMatch(/<div v-if="c\.chipRows\.value\.length" class="listhead">[^]*?c\.text\.hintSelect[^]*?<\/div>/);
        c.dispose();
    });

    it('shows the V8 column set in V8 order as long as no layout is stored (version 0)', async () => {
        const c = controllerFor(rows, { version: 0, layout: { version: 1, columns: LIST_COLUMNS.map((x) => ({ key: x.key, width: 140, visible: true })), sort: [], group_by: [] } });
        await c.start();
        expect(c.columns().map((x) => x.key)).toEqual(['status', 'priority', 'staff_pools', 'dictated_by', 'source_kind', 'duration_seconds', 'created_at']);
        expect(c.layout.state.dirty).toBe(false);
        c.dispose();
        const stored = controllerFor(rows, { version: 3, layout: { version: 1, columns: LIST_COLUMNS.map((x) => ({ key: x.key, width: 140, visible: true })), sort: [], group_by: [] } });
        await stored.start();
        expect(stored.columns()).toHaveLength(LIST_COLUMNS.length);
        stored.dispose();
    });

    it('the sort select drives the layout sort and the note names it', async () => {
        const c = controllerFor(rows);
        await c.start();
        expect(c.sortValue()).toBe('');
        expect(c.sortNote()).toBe('Standardreihenfolge');
        c.setSort('priority:asc');
        expect(c.sortValue()).toBe('priority:asc');
        expect(c.sortNote()).toBe('Sortiert nach: Priorität');
        expect(c.shownRows().map((r) => r.dictation_id)).toEqual(['2', '1', '3']);
        expect(c.sortMark('priority')).toBe('↑');
        c.setSort('');
        expect(c.sortValue()).toBe('');
        c.dispose();
    });

    it('disables the sort select when the layout is read-only', async () => {
        const c = controllerFor(rows);
        await c.start();
        c.layout.state.canEdit = false;
        c.setSort('priority:asc');
        expect(c.sortValue()).toBe('');
        expect(WorklistView.template).toMatch(/<select[^>]+:disabled="!c\.layout\.state\.canEdit"[^>]+:value="c\.sortValue\(\)"/);
        c.dispose();
    });

    it('bulk selection by checkbox is independent of the detail selection', async () => {
        const c = controllerFor(rows);
        await c.start();
        c.toggleChecked(rows[0]);
        expect(c.isChecked(rows[0])).toBe(true);
        expect(c.checkedCount()).toBe(1);
        expect(c.selectedId.value).toBeNull();
        expect(c.allChecked()).toBe(false);
        c.toggleAll();
        expect(c.checkedCount()).toBe(3);
        expect(c.allChecked()).toBe(true);
        c.toggleAll();
        expect(c.checkedCount()).toBe(0);
        c.toggleChecked(rows[1]);
        c.clearChecked();
        expect(c.checkedCount()).toBe(0);
        c.dispose();
    });

    it('layout setSort sets one key, clears on an empty key and ignores unknown keys', async () => {
        const layout = useWorklistLayout({ client: fakeClient([], { version: 1, layout: { version: 1, columns: [], sort: [], group_by: [] } }), columns: LIST_COLUMNS });
        await layout.load();
        layout.setSort('created_at', 'desc');
        expect(layout.state.layout.sort).toEqual([['created_at', 'desc']]);
        expect(layout.state.dirty).toBe(true);
        layout.setSort('nope', 'asc');
        expect(layout.state.layout.sort).toEqual([['created_at', 'desc']]);
        layout.setSort(null);
        expect(layout.state.layout.sort).toEqual([]);
    });
});

describe('V8 prototype markup', () => {
    const tpl = WorklistView.template;

    it('ellipsizes the priority text and exposes its full value in the title', () => {
        expect(cellText(row('1', { priority: 0 }), 'priority')).toBe('Bei Gelegenheit');
        expect(tpl).toMatch(/class="prio"[^>]*:title="c\.cell\(item\.row, col\.key\)"><span>{{ c\.cell\(item\.row, col\.key\) }}/);
        expect(HOSTED_CSS).toMatch(/\.worktable \.prio span\{[^}]*text-overflow:ellipsis/);
    });

    it('follows the DOM of the prototype Arbeitsliste page', () => {
        for (const part of [
            'class="page-head"', 'class="eyebrow"', 'class="count-pill"', 'class="subtitle"', 'class="last-update"', 'class="icon-btn"',
            'class="metrics"', 'class="metric"', 'class="workspace"', 'class="listbody"', 'class="table-container"',
            'class="worktable"', 'class="titlecell"', 'class="mini-rec"', 'class="pool-pill"', 'class="entity-name"',
            'class="filter-pill"', 'class="num-circle"', 'class="tablefooter"', 'class="detailhead"', 'class="detail-line"',
            'data-testid="refresh-button"', 'data-testid="last-update"', 'data-testid="detail-panel"', 'data-testid="open-dictation"',
        ]) expect(tpl).toContain(part);
        expect(tpl).toContain('c.text.refresh');
        expect(tpl).toContain('v8-page');
    });

    it('keeps the legacy list hooks as data attributes, not as classes that restyle the table', () => {
        for (const hook of ['cl-table', 'cl-state', 'cl-pager', 'pill-status']) {
            expect(tpl).toContain('data-layout="' + hook + '"');
            expect(tpl).not.toMatch(new RegExp('class="[^"]*\\b' + hook + '\\b'));
        }
    });

    it('puts Erweiterte Filter, the saved-filter select, search and slots in the prototype toolbar', () => {
        const t = WorklistFiltersPanel.template;
        for (const part of ['class="viewline"', 'class="viewcontrols"', 'class="toolbar"', 'class="searchwrap"', 'class="advanced show"', '<slot name="toolbar">', 'data-testid="filter-editor"']) {
            expect(t).toContain(part);
        }
        const layout = WorklistLayoutControls({ defineComponent: (o) => o, ref: (v) => ({ value: v }) }).template;
        expect(layout).toContain('class="icon-btn"');
        expect(layout).toContain('{{ LABELS.view }}');
    });

    it('uses no hard-coded colours and no patient or title fields in the V8 templates', () => {
        const detail = DetailView.components.DetailBody.template;
        for (const t of [tpl, detail, WorklistFiltersPanel.template]) {
            expect(t).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
            expect(t).not.toMatch(/row\.(title|notes|recipient)\b/);
        }
        expect(tpl).not.toMatch(/Patient|Fall-ID/);
    });

    it('renders the detail page after the V8 record page (back bar, heading, grid, metadata pairs)', () => {
        const t = DetailView.components.DetailBody.template;
        for (const part of ['class="record-backbar"', 'class="back-link"', 'class="record-detail-heading"', 'class="record-detail-grid"', 'class="record-metadata"', 'class="metadata-pair"']) {
            expect(t).toContain(part);
        }
        expect(t.indexOf('data-testid="back"')).toBeLessThan(t.indexOf('detail-header'));
    });

    it('reads clinic-v8.css classes only (no extra stylesheet is needed for the V8 look)', () => {
        const css = readFileSync('static/css/clinic-v8.css', 'utf8');
        for (const cls of ['.page-head', '.metrics', '.workspace', '.worktable', '.filter-pill', '.pool-pill', '.record-detail-grid']) {
            expect(css).toContain(cls);
        }
    });
});
