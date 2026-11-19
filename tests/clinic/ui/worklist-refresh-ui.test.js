import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    ClinicWorklistRefresh,
    fetchRecordingChanges,
    formatLastUpdate,
    useWorklistRefresh,
} from '../../../static/js/clinic/worklist-refresh-ui.js';
import { ROLE_STAFF } from '../../../static/js/clinic/worklist-ui.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/worklist-refresh-ui.js'), 'utf8');

const row = (id, extra = {}) => ({ dictation_id: id, title: `Diktat ${id}`, status: 'zu_schreiben', duration_seconds: 60, ...extra });

function deferred() {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
}

function mockClient(handler) {
    return {
        switchContext: vi.fn(),
        request: vi.fn(handler || (async () => ({ outcome: 'ok', data: { items: [] } }))),
        command: vi.fn(),
    };
}

const page = (changes, next = 'c2', more = false) => ({ outcome: 'ok', data: { changes, next_cursor: next, has_more: more } });

describe('worklist-refresh-ui', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('formats the last-update state in German', () => {
        expect(formatLastUpdate(null)).toBe('Noch nicht aktualisiert');
        expect(formatLastUpdate(new Date(2026, 0, 1, 9, 5, 7))).toBe('Zuletzt aktualisiert: 09:05:07');
    });

    it('manual refresh reloads rows and shows the update time', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1), row(2)] } }));
        const w = useWorklistRefresh({ apiClient: client, now: () => new Date(2026, 0, 1, 8, 0, 0), fetchChanges: async () => page([]) });
        await w.refresh();
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([1, 2]);
        expect(w.totals.value.totalCount).toBe(2);
        expect(w.lastUpdateLabel.value).toBe('Zuletzt aktualisiert: 08:00:00');
        expect(w.isRefreshing.value).toBe(false);
    });

    it('discards an out-of-order refresh response', async () => {
        const first = deferred();
        let n = 0;
        const client = mockClient(() => (++n === 1 ? first.promise : Promise.resolve({ outcome: 'ok', data: { items: [row(2)] } })));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges: async () => page([]) });
        const slow = w.refresh();
        await w.refresh();
        first.resolve({ outcome: 'ok', data: { items: [row(1)] } });
        expect((await slow).outcome).toBe('discarded');
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([2]);
    });

    it('applies upserts and deletes from the cursor and keeps the selection', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1), row(2)] } }));
        const calls = [];
        const fetchChanges = vi.fn(async (cursor) => {
            calls.push(cursor);
            if (calls.length === 1) return page([], 'c1');
            return page([
                { type: 'upsert', recording: row(3) },
                { type: 'upsert', recording: row(2, { title: 'Neu' }) },
                { type: 'delete', id: 1 },
            ], 'c2');
        });
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 0 });
        await w.start();
        w.selectRow(1);
        await w.pollChanges();
        expect(calls).toEqual([null, 'c1']);
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([2, 3]);
        expect(w.selectedRow.value.title).toBe('Neu');
        expect(w.selectedIndex.value).toBe(0);
        w.stop();
    });

    it('removed access drops row and detail view immediately', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1), row(2)] } }));
        let n = 0;
        const fetchChanges = async () => (++n === 1 ? page([], 'c1') : page([{ type: 'delete', id: 2 }], 'c2'));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 0 });
        await w.start();
        w.selectRow(1);
        await w.pollChanges();
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([1]);
        expect(w.selectedRow.value).toBeNull();
        expect(w.selectedIndex.value).toBe(-1);
        expect(w.totals.value.totalCount).toBe(1);
        w.stop();
    });

    it('removes a row whose upsert falls out of the role scope', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1, { assignee_user_id: 5 })] } }));
        let n = 0;
        const fetchChanges = async () => (++n === 1 ? page([], 'c1') : page([{ type: 'upsert', recording: row(1, { assignee_user_id: 9 }) }]));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 0, role: ROLE_STAFF, userId: 5 });
        await w.start();
        await w.pollChanges();
        expect(w.rows.value).toEqual([]);
        w.stop();
    });

    it('follows has_more pages in one poll', async () => {
        const client = mockClient();
        const seen = [];
        const fetchChanges = async (cursor) => {
            seen.push(cursor);
            if (cursor === null) return page([], 'c0');
            if (cursor === 'c0') return page([{ type: 'upsert', recording: row(1) }], 'c1', true);
            return page([{ type: 'upsert', recording: row(2) }], 'c2');
        };
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 0 });
        await w.start();
        await w.pollChanges();
        expect(seen).toEqual([null, 'c0', 'c1']);
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([1, 2]);
        w.stop();
    });

    it('expired cursor falls back to a full reload', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(7)] } }));
        let n = 0;
        const fetchChanges = async () => {
            n += 1;
            if (n === 1) return page([], 'c1');
            if (n === 2) return { outcome: 'expired', status: 410 };
            return page([], 'c9');
        };
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 0 });
        await w.start();
        await w.pollChanges();
        expect(w.rows.value.map((r) => r.dictation_id)).toEqual([7]);
        w.stop();
    });

    it('failure keeps data, shows a German error and retries with backoff', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1)] } }));
        let n = 0;
        const fetchChanges = vi.fn(async () => (++n === 1 ? page([], 'c1') : { outcome: 'network' }));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 1000 });
        await w.start();
        expect(fetchChanges).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(1000);
        expect(fetchChanges).toHaveBeenCalledTimes(2);
        expect(w.refreshError.value).toContain('fehlgeschlagen');
        expect(w.rows.value).toHaveLength(1);
        await vi.advanceTimersByTimeAsync(1000);
        expect(fetchChanges).toHaveBeenCalledTimes(2);
        await vi.advanceTimersByTimeAsync(1000);
        expect(fetchChanges).toHaveBeenCalledTimes(3);
        w.stop();
    });

    it('unauthorized feed answer clears data and stops polling', async () => {
        const client = mockClient(async () => ({ outcome: 'ok', data: { items: [row(1)] } }));
        let n = 0;
        const fetchChanges = vi.fn(async () => (++n === 1 ? page([], 'c1') : { outcome: 'permission', status: 403 }));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 1000 });
        await w.start();
        await vi.advanceTimersByTimeAsync(1000);
        expect(w.rows.value).toEqual([]);
        expect(w.isPermissionLost.value).toBe(true);
        expect(w.isRunning.value).toBe(false);
        await vi.advanceTimersByTimeAsync(10000);
        expect(fetchChanges).toHaveBeenCalledTimes(2);
    });

    it('unauthorized and stale-version row responses behave per outcome', async () => {
        const denied = useWorklistRefresh({ apiClient: mockClient(async () => ({ outcome: 'permission', status: 403, message: 'x' })), fetchChanges: async () => page([]) });
        await denied.refresh();
        expect(denied.isPermissionLost.value).toBe(true);
        const conflict = useWorklistRefresh({ apiClient: mockClient(async () => ({ outcome: 'conflict', status: 409, message: 'Der Stand ist veraltet.' })), initialRows: [row(1)], fetchChanges: async () => page([]) });
        await conflict.refresh();
        expect(conflict.rows.value).toHaveLength(1);
        expect(conflict.refreshError.value).toBe('Der Stand ist veraltet.');
    });

    it('logout stops polling, discards late answers and clears memory', async () => {
        const late = deferred();
        let n = 0;
        const client = mockClient(() => (++n === 1 ? Promise.resolve({ outcome: 'ok', data: { items: [row(1)] } }) : late.promise));
        const fetchChanges = vi.fn(async () => page([], 'c1'));
        const w = useWorklistRefresh({ apiClient: client, fetchChanges, intervalMs: 1000 });
        await w.start();
        const pending = w.refresh();
        w.logout();
        late.resolve({ outcome: 'ok', data: { items: [row(5)] } });
        expect((await pending).outcome).toBe('discarded');
        expect(w.rows.value).toEqual([]);
        expect(w.isRunning.value).toBe(false);
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: null, dictationId: null });
        const calls = fetchChanges.mock.calls.length;
        await vi.advanceTimersByTimeAsync(10000);
        expect(fetchChanges.mock.calls.length).toBe(calls);
    });

    it('default feed reader maps statuses and reuses the session cookie', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ changes: [] }) })
            .mockResolvedValueOnce({ ok: false, status: 410 })
            .mockResolvedValueOnce({ ok: false, status: 403 })
            .mockResolvedValueOnce({ ok: false, status: 500 });
        vi.stubGlobal('fetch', fetchMock);
        expect((await fetchRecordingChanges('abc')).outcome).toBe('ok');
        expect(fetchMock.mock.calls[0][0]).toContain('/api/v1/recordings/changes?');
        expect(fetchMock.mock.calls[0][0]).toContain('cursor=abc');
        expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin');
        expect((await fetchRecordingChanges(null)).outcome).toBe('expired');
        expect((await fetchRecordingChanges(null)).outcome).toBe('permission');
        expect((await fetchRecordingChanges(null)).outcome).toBe('network');
        vi.unstubAllGlobals();
    });

    it('exports a component with labelled, keyboard-operable markup', () => {
        const t = ClinicWorklistRefresh.template;
        expect(ClinicWorklistRefresh.name).toBe('ClinicWorklistRefresh');
        expect(typeof ClinicWorklistRefresh.setup).toBe('function');
        expect(t).toContain('data-testid="refresh-button"');
        expect(t).toContain('>Aktualisieren</button>');
        expect(t).toContain('data-testid="last-update"');
        expect(t).toContain('aria-live="polite"');
        expect(t).toContain('role="alert"');
        expect(t).toContain('tabindex="0"');
        const emit = vi.fn();
        const setup = ClinicWorklistRefresh.setup({ role: 'author', userId: null, accountId: null, tokenVersion: 1, initialRows: [row(1)], apiClient: mockClient(), intervalMs: 0, autoStart: false }, { emit });
        expect(setup.rows.value).toHaveLength(1);
        expect(typeof setup.refresh).toBe('function');
    });

    it('uses tokens only and stores nothing in the browser', () => {
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
