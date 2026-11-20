import { describe, expect, it, vi } from 'vitest';
import { RecordingsView } from '../../../static/js/clinic/compose-recordings.js';
import {
    createRecordingsController, createRecordingsMemory, dayHeading, formatLongDay, groupByDay, matchesQuery, periodOf, periodRange,
    shiftDay, toViewRow,
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

    it('derives the source icon, Fall-ID heading and fallback text of a row from contract fields only', () => {
        const rec = toViewRow(item(1));
        expect(rec).toMatchObject({ sourceKind: 'mic', sourceLabel: 'Mikrofon', heading: 'Fall-ID F-77', fallbackText: '', hasLease: false });
        const imp = toViewRow(item(2, { time_source: 'imported', case_display: '', lease_holder: { id: 9, display_name: 'Frau Typ' } }));
        expect(imp).toMatchObject({ sourceKind: 'upload', sourceLabel: 'Import', heading: 'Diktat', fallbackText: 'Importdatum', hasLease: true });
        expect(toViewRow(item(3, { time_source: 'created' })).fallbackText).toBe('Erstelldatum');
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
