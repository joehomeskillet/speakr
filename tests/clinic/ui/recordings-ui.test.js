import { describe, expect, it, vi } from 'vitest';
import {
    buildPath, createRecordingsController, createRecordingsMemory, formatDay, formatTime, groupByDay, mergePage, toViewRow,
} from '../../../static/js/clinic/recordings-ui.js';

const item = (id, extra = {}) => ({
    dictation_id: id, recording_id: 'r' + id, recorded_at: '2026-11-19T08:30:00Z', time_source: 'recorded',
    sort_time: '2026-11-19T08:30:00Z', day: '2026-11-19',
    dictated_by: { id: 3, display_name: 'Dr. Muster' }, staff_pools: [{ id: 1, name: 'Pool A' }],
    lease_holder: null, processing_status: 'completed', workflow_status: 'zu_schreiben', duration_ms: 65000,
    case_display: 'F-100', ...extra,
});
const ok = (items, next = null) => ({ outcome: 'ok', status: 200, data: { items, next_cursor: next } });
const fakeClient = (pages) => {
    const queue = pages.slice();
    return { request: vi.fn(async () => queue.shift() || ok([])), command: vi.fn(), switchContext: vi.fn() };
};
const make = (client, extra = {}) => createRecordingsController({ client, currentUser: { id: 1, role: 'typist' }, memory: createRecordingsMemory(), ...extra });

describe('recordings view model', () => {
    it.each([
        ['aufgabe', 'Aufgabe geplant'],
        ['neu_angelegt', 'Entwurf bei Dr. Muster'],
        ['vorbereitung_offline', 'Das System bereitet das Diktat vor'],
        ['vorbereitung_online', 'Das System bereitet das Diktat vor'],
        ['zu_schreiben', 'Beim Schreibdienst Pool A'],
        ['zu_korrigieren', 'Beim Schreibdienst Pool A'],
        ['in_bearbeitung', 'Beim Schreibdienst Pool A'],
        ['abzunehmen', 'Wartet auf die zuständige Person zur Abnahme'],
        ['abgenommen', 'Wartet auf die zuständige Person zur Freigabe'],
        ['fertiggestellt', 'Fertig'],
        ['zu_archivieren', 'Zum Ablegen vorgemerkt'],
        ['zu_loeschen', 'Zum Löschen vorgemerkt'],
    ])('names the next turn for %s without assigning it to the viewer', (workflow_status, label) => {
        expect(toViewRow(item('1', { workflow_status }), 1)).toMatchObject({
            nextStep: `${label} – Sie müssen nichts tun`, needsAction: false,
        });
    });

    it('marks only the current author, assigned writer or active holder in their workflow step', () => {
        const assignee = { id: '1', display_name: 'Schreibkraft' };
        const lease_holder = { id: '2', display_name: 'Andere Person' };
        expect(toViewRow(item('1', { workflow_status: 'neu_angelegt' }), '3')).toMatchObject({
            nextStep: 'Sie sind dran: Diktat an den Schreibdienst senden', needsAction: true,
        });
        expect(toViewRow(item('1', { assignee }), 1)).toMatchObject({
            nextStep: 'Sie sind dran: Diktat schreiben', needsAction: true,
        });
        expect(toViewRow(item('1', { workflow_status: 'zu_korrigieren', assignee }), 1)).toMatchObject({
            nextStep: 'Sie sind dran: Text prüfen', needsAction: true,
        });
        const active = item('1', { workflow_status: 'in_bearbeitung', assignee, lease_holder });
        expect(toViewRow(active, 1)).toMatchObject({
            nextStep: 'In Bearbeitung durch Andere Person – Sie müssen nichts tun', needsAction: false,
        });
        expect(toViewRow(active, 2)).toMatchObject({
            nextStep: 'Sie sind dran: Diktat weiterbearbeiten', needsAction: true,
        });
        expect(toViewRow(item('1', { assignee }), 2).nextStep).toBe('Bei Schreibkraft – Sie müssen nichts tun');
        expect(toViewRow(item('1', { assignee, workflow_status: 'abzunehmen' }), 1).needsAction).toBe(false);
        expect(toViewRow(item('1', { workflow_status: 'abzunehmen', viewer_review_turn: true }), 1).nextStep)
            .toBe('Sie sind dran: Text zur Abnahme prüfen');
        expect(toViewRow(item('1', { workflow_status: 'abgenommen', viewer_review_turn: true }), 1).nextStep)
            .toBe('Sie sind dran: Text freigeben');
    });

    it('keeps processing secondary and does not infer completion or a writer from missing data', () => {
        expect(toViewRow(item('1', { staff_pools: [] })).nextStep).toBe('Wartet auf eine Zuteilung – Sie müssen nichts tun');
        expect(toViewRow(item('1', { archived_at: '2026-11-19T08:30:00Z', workflow_status: 'zu_archivieren' })).nextStep)
            .toBe('Archiviert – Sie müssen nichts tun');
        expect(toViewRow(item('1', { workflow_status: 'unknown' })).nextStep).toBe('Der nächste Schritt ist nicht bekannt');
        expect(toViewRow(item('1')).processing).toBe('');
        expect(toViewRow(item('1', { processing_status: 'queued' })).processing).toBe('Text wird erstellt');
        expect(toViewRow(item('1', { text_created: true })).processing).toBe('Text erstellt');
    });
    it('formats the day as de-CH and keeps unknown days explicit', () => {
        expect(formatDay('2026-11-20')).toBe('Freitag, 20.11.2026');
        expect(formatDay(null)).toBe('Ohne Datum');
        expect(formatTime('2026-11-19T08:30:00Z')).toBe('09:30');
        expect(formatTime('2026-07-19T08:30:00Z')).toBe('10:30');
        expect(formatTime(null)).toBe('–');
    });

    it('groups consecutive days in contract order without re-sorting', () => {
        const rows = [item('1', { day: '2026-11-19' }), item('2', { day: '2026-11-19' }), item('3', { day: '2026-11-18' }), item('4', { day: null })]
            .map(toViewRow);
        const groups = groupByDay(rows);
        expect(groups.map((g) => g.label)).toEqual(['Donnerstag, 19.11.2026', 'Mittwoch, 18.11.2026', 'Ohne Datum']);
        expect(groups.map((g) => g.rows.map((r) => r.key))).toEqual([['1', '2'], ['3'], ['4']]);
    });

    it('marks fallback times as erfasst and never as recording time', () => {
        const recorded = toViewRow(item('1'));
        const created = toViewRow(item('2', { time_source: 'created' }));
        const imported = toViewRow(item('3', { time_source: 'imported' }));
        expect(recorded.isFallback).toBe(false);
        expect(recorded.fallbackLabel).toBe('');
        expect(created.isFallback).toBe(true);
        expect(created.fallbackLabel).toBe('erfasst');
        expect(created.fallbackTip).toContain('erstellt');
        expect(created.fallbackTip).toContain('nicht die Aufnahmezeit');
        expect(imported.fallbackTip).toContain('importiert');
    });

    it('keeps author, pool and lease holder as three separate cells', () => {
        const row = toViewRow(item('1', { staff_pools: [{ id: 1, name: 'Pool A' }, { id: 2, name: 'Pool B' }], lease_holder: { id: 9, display_name: 'Frau Typ', until: '2026-11-19T10:00:00Z' } }));
        expect(row.author).toBe('Dr. Muster');
        expect(row.poolText).toBe('Pool A, Pool B');
        expect(row.leaseHolder).toBe('Frau Typ');
        const none = toViewRow(item('2', { staff_pools: [], dictated_by: null }));
        expect(none.poolText).toBe('Kein Schreibdienst zugeordnet');
        expect(none.leaseHolder).toBe('–');
        expect(none.author).toBe('Unbekannt');
    });

    it('reads no patient or title fields', () => {
        const row = toViewRow(item('1', { title: 'Mustermann Hans', patient_name: 'Hans', notes: 'geheim' }));
        expect(JSON.stringify(row)).not.toMatch(/Mustermann|Hans|geheim/);
        expect(row.caseDisplay).toBe('F-100');
    });

    it('builds the query path and drops empty filters', () => {
        expect(buildPath({ author_id: '', pool_id: ' 4 ', status: 'zu_schreiben', from: '', to: '' }, 'c1', 50))
            .toBe('/recordings?limit=50&cursor=c1&pool_id=4&status=zu_schreiben');
        expect(buildPath({}, null)).toBe('/recordings?limit=50');
    });

    it('merges pages without duplicates', () => {
        const first = mergePage([], [item('1'), item('2')]);
        const next = mergePage(first, [item('2'), item('3')]);
        expect(next.map((r) => r.key)).toEqual(['1', '2', '3']);
    });
});

describe('recordings controller', () => {
    it('loads, appends via next_cursor and skips duplicates', async () => {
        const client = fakeClient([ok([item('1'), item('2')], 'c1'), ok([item('2'), item('3')], null)]);
        const c = make(client);
        await c.start();
        expect(c.state.rows.map((r) => r.key)).toEqual(['1', '2']);
        await c.loadMore();
        expect(client.request.mock.calls[1][0]).toBe('/recordings?limit=50&cursor=c1');
        expect(c.state.rows.map((r) => r.key)).toEqual(['1', '2', '3']);
        expect(c.state.nextCursor).toBeNull();
        expect(await c.loadMore()).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(2);
    });

    it('a filter change resets the cursor and rows', async () => {
        const client = fakeClient([ok([item('1')], 'c1'), ok([item('9')], null)]);
        const c = make(client);
        await c.start();
        c.state.draft.author_id = '3';
        c.state.draft.from = '2026-11-01';
        await c.applyFilters();
        expect(client.request.mock.calls[1][0]).toBe('/recordings?limit=50&author_id=3&from=2026-11-01');
        expect(c.state.rows.map((r) => r.key)).toEqual(['9']);
        expect(c.state.nextCursor).toBeNull();
        expect(c.hasFilters()).toBe(true);
    });

    it('reset clears the filters and reloads from the first page', async () => {
        const client = fakeClient([ok([item('1')]), ok([item('1')])]);
        const c = make(client);
        await c.start();
        c.state.draft.status = 'zu_schreiben';
        await c.applyFilters();
        await c.resetFilters();
        expect(client.request.mock.calls[2][0]).toBe('/recordings?limit=50');
        expect(c.hasFilters()).toBe(false);
    });

    it('restores filter, loaded pages and scroll offset after a re-mount', async () => {
        const memory = createRecordingsMemory();
        const client = fakeClient([ok([item('1')], 'c1'), ok([item('2')], 'c2'), ok([item('3')], null)]);
        const a = make(client, { memory });
        await a.start();
        a.state.draft.pool_id = '4';
        await a.applyFilters();
        await a.loadMore();
        a.setScroll(321);
        a.dispose();
        const calls = client.request.mock.calls.length;
        const b = make(client, { memory });
        await b.start();
        expect(client.request).toHaveBeenCalledTimes(calls);
        expect(b.state.rows.map((r) => r.key)).toEqual(['2', '3']);
        expect(b.state.filters.pool_id).toBe('4');
        expect(b.state.nextCursor).toBeNull();
        expect(b.scrollTop()).toBe(321);
        expect(b.state.loaded).toBe(true);
    });

    it('reloads previously viewed pages with fresh cursors after a dictation changes', async () => {
        const memory = createRecordingsMemory();
        const client = fakeClient([
            ok([]), ok([item('1')], 'old'), ok([item('2')]),
            ok([item('3')], 'new'), { outcome: 'UNKNOWN', status: 503 },
            ok([item('3')], 'new'), ok([item('1')], 'next'),
        ]);
        client.dictationChanges = 0;
        const first = make(client, { memory });
        await first.start();
        first.state.draft.pool_id = '4';
        await first.applyFilters();
        await first.loadMore();
        first.state.query = 'Muster';
        first.setScroll(321);
        first.dispose();

        client.dictationChanges += 1;
        const reopened = make(client, { memory });
        await reopened.start();
        expect(reopened.state.rows.map(row => row.key)).toEqual(['1', '2']);
        expect(reopened.state.stale).toBe(true);
        expect(reopened.scrollTop()).toBe(321);
        await reopened.refreshIfChanged();
        expect(reopened.state.rows.map(row => row.key)).toEqual(['3', '1']);
        expect(client.request.mock.calls.at(-1)[0]).toContain('cursor=new');
        expect(reopened.state.filters.pool_id).toBe('4');
        expect(reopened.state.query).toBe('Muster');
        expect(reopened.state.nextCursor).toBe('next');
        expect(reopened.scrollTop()).toBe(321);
    });

    it('updates a processing window without collapsing its pages, filter or scroll', async () => {
        const client = fakeClient([ok([]), ok([item('1')], 'old'), ok([item('2', { processing_status: 'queued' })]),
            ok([item('3')], 'fresh'), ok([item('2', { processing_status: 'completed' })], 'more')]);
        const c = make(client);
        await c.start();
        c.state.draft.pool_id = '4';
        await c.applyFilters();
        await c.loadMore();
        c.setScroll(321);
        c.state.query = 'Muster';
        const active = () => c.visibleRows().some(row => row.processingActive);
        const checking = c.refreshIfChanged(active);
        expect(c.state.loading).toBe(false);
        expect(c.state.rows.map(row => row.key)).toEqual(['1', '2']);
        await checking;
        expect(client.request.mock.calls.at(-1)[0]).toContain('cursor=fresh');
        expect(c.state.rows.map(row => row.key)).toEqual(['3', '2']);
        expect(c.state.nextCursor).toBe('more');
        expect(c.state.filters.pool_id).toBe('4');
        expect(c.state.query).toBe('Muster');
        expect(c.scrollTop()).toBe(321);
        expect(active()).toBe(false);
        expect(await c.refreshIfChanged(active)).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(5);
    });

    it('open reports the worklist payload', async () => {
        const onOpen = vi.fn();
        const c = make(fakeClient([ok([item('7'), item('8', { workflow_status: 'zu_korrigieren' })])]), { onOpen });
        await c.start();
        c.open(c.state.rows[0]);
        c.open(c.state.rows[1]);
        expect(onOpen.mock.calls.map((x) => x[0])).toEqual([
            { dictationId: '7', mode: 'player' }, { dictationId: '8', mode: 'correction' },
        ]);
    });

    it('ignores a superseded response', async () => {
        let release;
        const slow = new Promise((resolve) => { release = resolve; });
        const client = { request: vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(ok([item('2')])) };
        const c = make(client);
        const first = c.load();
        await c.load();
        release(ok([item('1')]));
        await first;
        expect(c.state.rows.map((r) => r.key)).toEqual(['2']);
    });

    it('shows a permission loss without data and keeps stale rows on a network error', async () => {
        const c = make(fakeClient([ok([item('1')]), { outcome: 'network', message: 'Die Verbindung ist fehlgeschlagen.' }, { outcome: 'permission', message: 'x' }]));
        await c.start();
        await c.load();
        expect(c.state.rows).toHaveLength(1);
        expect(c.state.stale).toBe(true);
        await c.load();
        expect(c.state.denied).toBe(true);
        expect(c.state.rows).toEqual([]);
    });

    it('writes nothing to browser storage', async () => {
        const set = vi.fn();
        globalThis.localStorage = { setItem: set, getItem: vi.fn(), removeItem: vi.fn() };
        globalThis.sessionStorage = { setItem: set, getItem: vi.fn(), removeItem: vi.fn() };
        try {
            const c = make(fakeClient([ok([item('1')], 'c1'), ok([item('2')])]));
            await c.start();
            await c.loadMore();
            c.setScroll(10);
            c.dispose();
            expect(set).not.toHaveBeenCalled();
        } finally {
            delete globalThis.localStorage;
            delete globalThis.sessionStorage;
        }
    });
});
