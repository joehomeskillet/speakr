import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    ARCHIVE_STATUS,
    ArchiveView,
    RETENTION_LABELS,
    buildQuery,
    createArchiveController,
    formatMoment,
    registerArchiveView,
    retentionLabel,
} from '../../../static/js/clinic/compose-archive.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-archive.js'), 'utf8');

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const item = (id, extra = {}) => ({
    dictation_id: id,
    recording_id: 1,
    recorded_at: '2026-07-01T21:30:00Z',
    time_source: 'recorded',
    sort_time: '2026-07-01T21:30:00Z',
    day: '2026-07-01',
    dictated_by: { id: '7', display_name: 'Dr. Muster' },
    staff_pools: [{ id: 'p1', name: 'Pool A' }],
    lease_holder: null,
    workflow_status: ARCHIVE_STATUS,
    retention_state: 'active',
    archived_at: '2026-08-01T10:00:00Z',
    ...extra,
});

function fakeClient(handler) {
    const calls = [];
    return {
        calls,
        request: vi.fn(async (path, options) => { calls.push(path); return handler(path, options); }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}
const params = (path) => new URL(path, 'http://x').searchParams;

describe('archive filter', () => {
    it('always requests the archive state and sends only IDs and dates', async () => {
        const client = fakeClient(() => ok({ items: [item('a')], next_cursor: null }));
        const c = createArchiveController({ client });
        await c.load();
        expect(ARCHIVE_STATUS).toBe('zu_archivieren');
        expect(client.calls[0].startsWith('/recordings?')).toBe(true);
        expect(params(client.calls[0]).get('status')).toBe('zu_archivieren');
        await c.setFilters({ from: '2026-01-01', to: '2026-12-31', authorId: '7', poolId: 'p1' });
        const q = params(client.calls[1]);
        expect(Object.fromEntries(q)).toEqual({
            limit: '50', from: '2026-01-01', to: '2026-12-31', author_id: '7', pool_id: 'p1', status: 'zu_archivieren',
        });
        expect(c.rows.value).toHaveLength(1);
    });

    it('cannot be pointed at another status', () => {
        const q = params(buildQuery({ status: 'zu_schreiben' }, null, 50));
        expect(q.get('status')).toBe('zu_archivieren');
    });

    it('appends pages without duplicates and a filter change resets the cursor', async () => {
        const pages = {
            '': ok({ items: [item('a'), item('b')], next_cursor: 'c1' }),
            c1: ok({ items: [item('b'), item('c')], next_cursor: null }),
        };
        const client = fakeClient((path) => pages[params(path).get('cursor') || '']);
        const c = createArchiveController({ client });
        await c.load();
        await c.loadMore();
        expect(c.rows.value.map((r) => r.dictation_id)).toEqual(['a', 'b', 'c']);
        expect(c.hasMore()).toBe(false);
        await c.setFilters({ poolId: 'p2' });
        expect(params(client.calls[client.calls.length - 1]).get('cursor')).toBeNull();
        expect(c.rows.value.map((r) => r.dictation_id)).toEqual(['a', 'b']);
    });

    it('tells an empty archive from a filter without hits', async () => {
        const client = fakeClient(() => ok({ items: [], next_cursor: null }));
        const c = createArchiveController({ client });
        await c.load();
        expect(c.emptyText()).toBe('Keine archivierten Aufnahmen vorhanden.');
        await c.setFilters({ authorId: '9' });
        expect(c.emptyText()).toBe('Keine Treffer für die gewählten Filter.');
    });

    it('keeps rows marked stale on error and clears them without permission', async () => {
        let mode = 'ok';
        const client = fakeClient(() => (mode === 'ok'
            ? ok({ items: [item('a')], next_cursor: null })
            : mode === 'fail' ? { outcome: 'network', message: 'Die Verbindung ist fehlgeschlagen.' }
                : { outcome: 'permission', message: 'x' }));
        const c = createArchiveController({ client });
        await c.load();
        mode = 'fail';
        await c.load();
        expect(c.rows.value).toHaveLength(1);
        expect(c.error.value).toBe('Die Verbindung ist fehlgeschlagen.');
        mode = 'deny';
        await c.load();
        expect(c.rows.value).toEqual([]);
        expect(c.denied.value).toBe(true);
    });

    it('ignores a superseded response', async () => {
        const slow = {};
        const client = fakeClient((path) => new Promise((resolve) => { slow[params(path).get('author_id') || 'none'] = resolve; }));
        const c = createArchiveController({ client });
        const first = c.load();
        const second = c.setFilters({ authorId: '7' });
        slow['7'](ok({ items: [item('new')], next_cursor: null }));
        await second;
        slow.none(ok({ items: [item('old')], next_cursor: null }));
        await first;
        expect(c.rows.value.map((r) => r.dictation_id)).toEqual(['new']);
    });
});

describe('retention labels', () => {
    it('renders hold, due and active as text', () => {
        expect(retentionLabel({ retention_state: 'held' })).toBe('Hold');
        expect(retentionLabel({ retention_state: 'due' })).toBe('Frist fällig');
        expect(retentionLabel({ retention_state: 'active' })).toBe('Aktiv aufbewahrt');
        expect(retentionLabel({})).toBe('Aufbewahrung nicht angegeben');
        expect(Object.keys(RETENTION_LABELS)).not.toContain('archived');
    });

    it('shows the label, the Zurich archive time and the role cells in the row model', async () => {
        const client = fakeClient(() => ok({ items: [item('a', { retention_state: 'held', staff_pools: [] })], next_cursor: null }));
        const c = createArchiveController({ client });
        await c.load();
        const row = c.rows.value[0];
        expect(c.retention(row)).toBe('Hold');
        expect(c.pool(row)).toBe('Kein Pool zugewiesen');
        expect(c.author(row)).toBe('Dr. Muster');
        expect(c.archived(row)).toBe(formatMoment('2026-08-01T10:00:00Z'));
        expect(c.archived(row)).toBe('01.08.2026 12:00');
        expect(c.recorded(row).text).toBe('01.07.2026 23:30');
        expect(c.recorded(row).mark).toBeNull();
    });

    it('marks a fallback time and never presents it as recording time', () => {
        const client = fakeClient(() => ok({ items: [] }));
        const c = createArchiveController({ client });
        const cell = c.recorded(item('a', { time_source: 'created', recorded_at: null }));
        expect(cell.mark).toBe('erfasst');
        expect(cell.hint).toMatch(/Erfassungszeitpunkt/);
    });

    it('renders the label in the template and exposes it via aria-free text', () => {
        expect(ArchiveView.template).toContain('c.retention(row)');
        expect(ArchiveView.template).toContain('c.retentionStyle(row)');
    });
});

describe('read only', () => {
    it('has no delete, reopen or restore control', () => {
        expect(ArchiveView.template).not.toMatch(/Lösch|Loesch|delete|Reopen|zur Korrektur|zurücknehmen|wieder aktiv/i);
        expect(source).not.toMatch(/method:\s*'(POST|PUT|PATCH|DELETE)'/);
        expect(source).not.toMatch(/c\.(delete|remove|reopen|restore)\(/);
        const labels = [...ArchiveView.template.matchAll(/<button[^>]*>\s*(?:\{\{ ([^}]+) \}\}|([^<]*))/g)].map((m) => (m[1] || m[2] || '').trim());
        expect(labels.filter(Boolean).sort()).toEqual(
            ['c.text.apply', 'c.text.back', 'c.text.removeFilter', 'c.text.reset', 'c.text.retry', 'c.text.more', 'c.text.open'].sort(),
        );
    });

    it('sends only GET requests', async () => {
        const client = fakeClient(() => ok({ items: [item('a')], next_cursor: null }));
        const c = createArchiveController({ client });
        await c.load();
        for (const call of client.request.mock.calls) expect(call[1].method).toBe('GET');
        expect(client.command).not.toHaveBeenCalled();
    });

    it('reads no title or patient data and uses no browser storage', () => {
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
        expect(source).not.toMatch(/\b(row|r|item)\.(title|patient_reference|patient_display|patient_ref|patient_name)\b/);
        expect(ArchiveView.template).not.toMatch(/Titel|Patient/);
        expect(ArchiveView.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).toContain("from './tokens.js'");
    });

    it('emits the open event with the archive mark and back for leaving the view', async () => {
        const onOpen = vi.fn();
        const onBack = vi.fn();
        const client = fakeClient(() => ok({ items: [item('a')], next_cursor: null }));
        const c = createArchiveController({ client, onOpen, onBack });
        await c.load();
        expect(c.open(c.rows.value[0])).toBe(true);
        expect(onOpen).toHaveBeenCalledWith({ dictationId: 'a', mode: 'view', archived: true });
        c.back();
        expect(onBack).toHaveBeenCalledTimes(1);
        const before = client.request.mock.calls.length;
        expect(client.request.mock.calls.length).toBe(before);
    });

    it('registers as clinic-archive-view and disposes without further writes', async () => {
        const app = { component: vi.fn() };
        registerArchiveView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-archive-view', ArchiveView);
        const client = fakeClient(() => ok({ items: [item('a')], next_cursor: 'n' }));
        const c = createArchiveController({ client });
        await c.load();
        c.dispose();
        expect(c.rows.value).toEqual([]);
        expect(c.open({ dictation_id: 'a' })).toBe(false);
    });
});
