import { describe, expect, it, vi } from 'vitest';
import { RecordingsView } from '../../../static/js/clinic/compose-recordings.js';
import {
    createRecordingsController, createRecordingsMemory, dayHeading, formatLongDay, groupByDay, matchesQuery, periodOf, periodRange,
    shiftDay, toViewRow, TEXT,
} from '../../../static/js/clinic/recordings-ui.js';

const item = (id, extra) => ({
    dictation_id: id, recorded_at: '2026-11-20T08:30:00Z', time_source: 'recorded', sort_time: '2026-11-20T08:30:00Z', day: '2026-11-20',
    dictated_by: { id: 5, display_name: 'Dr. Zeller' }, staff_pools: [{ id: 2, name: 'Radiologie' }], lease_holder: null,
    processing_status: 'completed', workflow_status: 'zu_schreiben', duration_ms: 65000, case_display: 'F-77', ...extra,
});

describe('recordings prototype look (V8 P01)', () => {
    it('renders the prototype structure: eyebrow, count pill, filter card, day groups and record rows', () => {
        const tpl = RecordingsView.template;
        for (const part of ['class="eyebrow"', 'count-pill', 'record-toolbar', 'record-search', 'record-select', 'record-role-filters',
            'record-list-meta', 'date-group', 'date-heading', 'group-count', 'record-group-list', 'recording-row', 'record-icon',
            'record-roles', 'record-subtitle', 'class="status"', 'record-source', 'record-duration', 'record-chevron']) {
            expect(tpl).toContain(part);
        }
        expect(tpl).toContain('data-testid="open-link"');
        expect(tpl).toContain('data-testid="filter-status"');
        expect(tpl).toContain('data-testid="filter-period"');
    });

    it('formats day headings relative to the Zurich day', () => {
        expect(formatLongDay('2026-11-20')).toBe('Freitag, 20. November 2026');
        expect(formatLongDay('')).toBe('Ohne Datum');
        expect(dayHeading('2026-11-20', '2026-11-20')).toEqual({ title: 'Heute', caption: 'Freitag, 20. November 2026' });
        expect(dayHeading('2026-11-19', '2026-11-20')).toEqual({ title: 'Gestern', caption: 'Donnerstag, 19. November 2026' });
        expect(dayHeading('2026-11-10', '2026-11-20')).toEqual({ title: 'Dienstag, 10. November 2026', caption: '' });
        expect(shiftDay('2026-03-01', -1)).toBe('2026-02-28');
        const groups = groupByDay([toViewRow(item(1)), toViewRow(item(2)), toViewRow(item(3, { day: '2026-11-19' }))], '2026-11-20');
        expect(groups.map((g) => [g.title, g.countText])).toEqual([['Heute', '2 Aufnahmen'], ['Gestern', '1 Aufnahme']]);
    });

    it('derives the Fall-ID heading and fallback text of a row from contract fields only and infers no origin from time_source', () => {
        const rec = toViewRow(item(1));
        expect(rec).toMatchObject({ heading: 'Fall-ID F-77', fallbackText: '', hasLease: false });
        const imp = toViewRow(item(2, { time_source: 'imported', case_display: '', lease_holder: { id: 9, display_name: 'Frau Typ' } }));
        expect(imp).toMatchObject({ heading: 'Diktat', fallbackText: 'Importdatum', hasLease: true });
        for (const row of [rec, imp, toViewRow(item(3, { time_source: 'created' }))]) {
            expect(row).not.toHaveProperty('sourceKind');
            expect(row).not.toHaveProperty('sourceLabel');
        }
        expect(RecordingsView.template).not.toMatch(/Mikrofon|sourceKind|sourceLabel/);
        expect(toViewRow(item(3, { time_source: 'created' })).fallbackText).toBe('Erstelldatum');
    });

    it('carries the author-entered title apart from the Fall-ID and renders it only when present', () => {
        const own = toViewRow(item(1, { own_title: ' Eigener Titel ' }));
        expect(own).toMatchObject({ ownTitle: 'Eigener Titel', heading: 'Fall-ID F-77', caseDisplay: 'F-77' });
        expect(toViewRow(item(2, { own_title: null })).ownTitle).toBe('');
        expect(toViewRow(item(3)).ownTitle).toBe('');
        expect(RecordingsView.template).toContain('v-if="r.ownTitle"');
    });

    it('maps period presets to a Zurich date range and back', () => {
        expect(periodRange('today', '2026-11-20')).toEqual({ from: '2026-11-20', to: '2026-11-20' });
        expect(periodRange('week', '2026-11-20')).toEqual({ from: '2026-11-14', to: '2026-11-20' });
        expect(periodRange('month', '2026-11-20')).toEqual({ from: '2026-10-22', to: '2026-11-20' });
        expect(periodRange('all', '2026-11-20')).toEqual({ from: '', to: '' });
        expect(periodOf('', '', '2026-11-20')).toBe('all');
        expect(periodOf('2026-11-14', '2026-11-20', '2026-11-20')).toBe('week');
        expect(periodOf('2026-11-01', '', '2026-11-20')).toBe('custom');
    });

    it('searches the loaded rows by author, pool, Fall-ID and ID and reports "x von y"', async () => {
        const rows = [item(1), item(2, { dictated_by: { id: 6, display_name: 'Dr. Amrein' }, staff_pools: [], case_display: '' })];
        const client = { request: vi.fn(async () => ({ outcome: 'ok', data: { items: rows, next_cursor: null } })) };
        const c = createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory: createRecordingsMemory() });
        await c.start();
        expect(matchesQuery(c.state.rows[0], 'zeller radio')).toBe(true);
        expect(matchesQuery(c.state.rows[0], 'amrein')).toBe(false);
        c.state.query = 'amrein';
        expect(c.visibleRows().map((r) => r.key)).toEqual(['2']);
        expect(c.countLabel()).toBe('1 von 2 Aufnahmen');
        expect(c.totalLabel()).toBe('2');
        expect(client.request).toHaveBeenCalledTimes(1); // the search never goes to the server
        await c.resetFilters();
        expect(c.state.query).toBe('');
    });

    it('sets a period preset on the draft and keeps "custom" for the date fields', async () => {
        const client = { request: vi.fn(async () => ({ outcome: 'ok', data: { items: [], next_cursor: null } })) };
        const c = createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory: createRecordingsMemory() });
        await c.start();
        c.setPeriod('week');
        expect(c.state.draft.from).not.toBe('');
        expect(c.picker.period).toBe('week');
        await c.applyFilters();
        expect(client.request.mock.calls[1][0]).toContain('from=');
        c.setPeriod('custom');
        expect(c.picker.period).toBe('custom');
        expect(c.state.draft.from).not.toBe('');
        c.setPeriod('all');
        expect(c.state.draft).toMatchObject({ from: '', to: '' });
    });
});

describe('recordings review fixes (V8 P01)', () => {
    const tpl = RecordingsView.template;
    const mk = (rows, cursor) => ({ request: vi.fn(async () => ({ outcome: 'ok', data: { items: rows, next_cursor: cursor || null } })) });
    const user = { id: 1, role: 'typist' };

    it('has no inline colour that only reads on a light surface', () => {
        expect(tpl).not.toContain('color: var(--ink)');
        expect(tpl).not.toContain('var(--ink)');
        expect(tpl).not.toMatch(/style="[^"]*(?:^|[\s;"])color\s*:/);
    });

    it('remembers the search text over open and return', async () => {
        const memory = createRecordingsMemory();
        const client = mk([item(1), item(2, { dictated_by: { id: 6, display_name: 'Dr. Amrein' } })]);
        const a = createRecordingsController({ client, currentUser: user, memory });
        await a.start();
        a.state.query = 'amrein';
        a.open(a.visibleRows()[0]);
        const b = createRecordingsController({ client, currentUser: user, memory });
        await b.start();
        expect(b.state.query).toBe('amrein');
        expect(b.visibleRows().map((r) => r.key)).toEqual(['2']);
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('keeps load-more outside the row list and warns that the search covers loaded rows only', async () => {
        expect(tpl.indexOf('data-testid="load-more"')).toBeGreaterThan(tpl.indexOf('data-testid="cards"'));
        expect(tpl).toMatch(/<div v-if="c\.state\.loaded && c\.state\.nextCursor" class="cl-more">/);
        const c = createRecordingsController({ client: mk([item(1), item(2)], 'next'), currentUser: user, memory: createRecordingsMemory() });
        await c.start();
        expect(c.searchPartial()).toBe(false);
        c.state.query = 'kein-treffer';
        expect(c.visibleRows()).toEqual([]);
        expect(c.searchPartial()).toBe(true);
        expect(c.countLabel()).toBe('0 von 2+ Aufnahmen');
        expect(tpl).toContain('t.noMatchLoaded');
        expect(tpl).toContain('data-testid="search-hint"');
        c.state.nextCursor = null;
        expect(c.searchPartial()).toBe(false);
        expect(c.countLabel()).toBe('0 von 2 Aufnahmen');
    });

    it('renders scope and sort selects, pool before author, and the Diktiert-von label', () => {
        expect(tpl).toContain('data-testid="filter-scope"');
        expect(tpl).toContain('record-select sort-select');
        expect(tpl.indexOf('data-testid="filter-pool"')).toBeLessThan(tpl.indexOf('data-testid="filter-author"'));
        expect(TEXT.authorId).toBe('Diktiert von');
        expect(tpl).toContain('class="empty-icon"');
        expect(tpl).toContain('class="dictator-name" data-cell="lease"');
    });

    it('disables the fixed scope and sort choices and explains each one', () => {
        for (const id of ['filter-scope', 'sort-select']) {
            const select = tpl.match(new RegExp('<select\\b[^>]*data-testid="' + id + '"[^>]*>'))?.[0];
            expect(select).toMatch(/\sdisabled\b/);
            expect(select).toContain(':title=');
        }
    });

    it('maps workflow statuses to prototype tints and builds a label that starts with the visible heading', () => {
        expect(toViewRow(item(1, { workflow_status: 'in_bearbeitung' })).statusClass).toBe('in-arbeit');
        expect(toViewRow(item(1, { workflow_status: 'fertiggestellt' })).statusClass).toBe('erledigt');
        expect(toViewRow(item(1, { workflow_status: 'zu_schreiben' })).statusClass).toBe('wartet');
        expect(toViewRow(item(1, { workflow_status: 'unbekannt' })).statusClass).toBe('neu');
        const row = toViewRow(item(1));
        expect(row.ariaLabel.startsWith('Fall-ID F-77, Diktiert von Dr. Zeller, Schreibkräfte-Pool Radiologie, Freitag, 20. November 2026')).toBe(true);
        expect(row.ariaLabel).toMatch(/Zu schreiben, 01:05$/);
        expect(row.datetime).toBe('2026-11-20T08:30:00Z');
        expect(tpl).toContain(':datetime="r.datetime || null"');
        expect(tpl).toContain(':aria-label="r.ariaLabel"');
    });

    it('restores the scroll position on the element that really scrolls and stops listening on unmount', async () => {
        const mounted = []; const unmounted = [];
        const vue = { ref: (v) => ({ value: v }), reactive: (o) => o, onMounted: (f) => mounted.push(f), onBeforeUnmount: (f) => unmounted.push(f), nextTick: () => Promise.resolve() };
        const host = { scrollTop: 0, addEventListener: vi.fn(), removeEventListener: vi.fn() };
        const memory = { snapshot: { rows: [], nextCursor: null, filters: {}, draft: {}, query: '', scrollTop: 340 } };
        const s = RecordingsView.setup({ client: mk([]), currentUser: user, vue, memory, autoStart: true }, { emit: vi.fn() });
        s.scroller.value = host; // no DOM in the unit test: the fallback host is used
        await mounted[0]();
        expect(host.scrollTop).toBe(340);
        expect(host.addEventListener).toHaveBeenCalledWith('scroll', expect.any(Function), { passive: true });
        const listener = host.addEventListener.mock.calls[0][1];
        host.scrollTop = 500; listener();
        expect(s.c.scrollTop()).toBe(500);
        unmounted[0]();
        expect(host.removeEventListener).toHaveBeenCalledWith('scroll', listener);
    });
});
