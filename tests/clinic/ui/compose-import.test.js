import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    ENDPOINTS, ImportView, IMPORT_TEMPLATE, createImportController, mayClarify, registerImportView, statusView,
} from '../../../static/js/clinic/compose-import.js';

const ok = (data) => ({ outcome: 'ok', data });
const INTENTS = [
    { id: 'i1', status: 'OPEN', source_ref: 'dev-a', expected_files: ['a.dss'], created_at: '2026-11-20T08:00:00Z' },
    { id: 'i2', status: 'FAILED', source_ref: 'dev-b', expected_files: ['b.dss', 'c.dss'], created_at: '2026-11-20T08:05:00Z' },
];
const INVENTORY = [{ source_id: 'dev-a', root_id: 'r', relative_path: 'a.dss', size: 10, mtime_utc: '2026-11-20T07:00:00Z', snapshot_version: 's1' }];
const UNASSIGNED = [{ id: 'd1', source_id: 'dev-a', relative_path: 'x.dss', snapshot_version: 's1' }];

function fakeClient(overrides) {
    const routes = {
        [ENDPOINTS.intents]: () => ok({ items: INTENTS }),
        [ENDPOINTS.inventory]: () => ok({ items: INVENTORY }),
        [ENDPOINTS.unassigned]: () => ok({ items: UNASSIGNED }),
        ...(overrides || {}),
    };
    return {
        request: vi.fn(async (path, opts) => {
            const h = routes[path];
            return h ? h(opts) : ok({});
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}
const calls = (client, path) => client.request.mock.calls.filter((c) => c[0] === path);

describe('import view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerImportView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-import-view', ImportView);
        expect(Object.keys(ImportView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('renders the list with a status per item and the device inventory', async () => {
        const c = createImportController({ client: fakeClient(), currentUser: { id: 5 } });
        await c.load();
        expect(c.state.intents.map((r) => r.id)).toEqual(['i1', 'i2']);
        expect(c.statusView('OPEN').label).toBe('Ausstehend');
        expect(c.statusView('FAILED').label).toBe('Fehlgeschlagen');
        expect(c.statusView('FULFILLED').label).toBe('Übernommen');
        expect(c.state.inventory).toHaveLength(1);
        expect(statusView('OPEN').style.border).toContain('var(--');
        expect(IMPORT_TEMPLATE).toContain('data-testid="intent-list"');
        expect(IMPORT_TEMPLATE).toContain('data-testid="inventory-list"');
    });

    it('shows no device detection without an agent report', async () => {
        const c = createImportController({ client: fakeClient({ [ENDPOINTS.inventory]: () => ok({ items: [] }) }), currentUser: { id: 5 } });
        await c.load();
        expect(c.state.inventory).toEqual([]);
        expect(c.text.inventoryEmpty).toContain('keine Geräteerkennung');
    });

    it('W13: an ordinary typist gets no clarification list and never requests it', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 5, role: 'typist' } });
        await c.load();
        expect(c.canClarify).toBe(false);
        expect(calls(client, ENDPOINTS.unassigned)).toHaveLength(0);
        expect(c.askAssign('d1')).toBe(false);
        expect(await c.confirmAssign('d1', 'i1', 's1')).toBeNull();
        expect(calls(client, ENDPOINTS.assign('d1'))).toHaveLength(0);
        expect(readFileSync(new URL('../../../static/js/clinic/compose-import.js', import.meta.url), 'utf8')).toContain('v-if="c.canClarify"');
    });

    it('W13: admins and pool leads see the clarification list', async () => {
        expect(mayClarify({ role: 'admin' })).toBe(true);
        expect(mayClarify({ isPoolLead: true })).toBe(true);
        expect(mayClarify({ role: 'typist' })).toBe(false);
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        expect(c.state.unassigned.map((r) => r.id)).toEqual(['d1']);
    });

    it('a server refusal hides the clarification list', async () => {
        const client = fakeClient({ [ENDPOINTS.unassigned]: () => ({ outcome: 'permission' }) });
        const c = createImportController({ client, currentUser: { id: 1, isPoolLead: true } });
        await c.load();
        expect(c.state.clarificationDenied).toBe(true);
        expect(c.state.unassigned).toEqual([]);
        expect(c.askAssign('d1')).toBe(false);
    });

    it('loads candidates and submits the author and pool chosen by a routed lead', async () => {
        const candidate = { target_author_user_id: 6, target_pool_id: 'p2', author_name: 'Test', pool_name: 'Pool 2' };
        const client = fakeClient({
            [ENDPOINTS.inventory]: () => ok({ items: INVENTORY, can_clarify: true }),
            '/imports/unassigned/d1/candidates': () => ok({ items: [candidate] }),
        });
        const c = createImportController({ client, currentUser: { id: 5, role: 'typist' } });
        await c.load();
        expect(c.canClarify).toBe(true);
        expect(c.state.unassigned).toEqual(UNASSIGNED);
        expect(c.askAssign('d1')).toBe(true);
        await c.loadCandidates('d1');
        expect(c.state.candidates).toEqual([candidate]);
        await c.confirmAssign('d1', candidate, 's1');
        expect(calls(client, ENDPOINTS.assign('d1'))[0][1].body).toEqual({
            target_author_user_id: 6, target_pool_id: 'p2', expected_snapshot_version: 's1',
        });
    });

    it('assignment needs a confirmation step and sends one request with a key', async () => {
        const client = fakeClient({ [ENDPOINTS.assign('d1')]: () => ok({}) });
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        expect(await c.confirmAssign('d1', 'i1', 's1')).toBeNull(); // not confirmed yet
        expect(calls(client, ENDPOINTS.assign('d1'))).toHaveLength(0);
        c.askAssign('d1');
        await c.confirmAssign('d1', 'i1', 's1');
        const sent = calls(client, ENDPOINTS.assign('d1'));
        expect(sent).toHaveLength(1);
        expect(sent[0][1].method).toBe('POST');
        expect(sent[0][1].body).toEqual({ intent_id: 'i1', expected_snapshot_version: 's1' });
        expect(sent[0][1].idempotencyKey.length).toBeGreaterThanOrEqual(8);
        expect(c.state.pending).toBeNull();
    });

    it('retry calls the API once per click with the same key until it succeeds', async () => {
        let n = 0;
        const client = fakeClient({ [ENDPOINTS.retry('i2')]: () => (++n < 3 ? { outcome: 'network' } : ok({})) });
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        await c.retry('i2');
        await c.retry('i2');
        await c.retry('i2');
        const sent = calls(client, ENDPOINTS.retry('i2'));
        expect(sent).toHaveLength(3);
        const keys = new Set(sent.map((s) => s[1].idempotencyKey));
        expect(keys.size).toBe(1);
        // after success a later retry gets a fresh key
        await c.retry('i2');
        const all = calls(client, ENDPOINTS.retry('i2'));
        expect(all[3][1].idempotencyKey).not.toBe(all[0][1].idempotencyKey);
    });

    it('a double click while a retry is in flight sends one request', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient({ [ENDPOINTS.retry('i2')]: async () => { await gate; return ok({}); } });
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        const first = c.retry('i2');
        const second = await c.retry('i2');
        expect(second).toBeNull();
        release();
        await first;
        expect(calls(client, ENDPOINTS.retry('i2'))).toHaveLength(1);
    });

    it('only failed or expired items can be retried', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        expect(await c.retry('i1')).toBeNull();
        expect(calls(client, ENDPOINTS.retry('i1'))).toHaveLength(0);
    });

    it('dispose releases data and the client context', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        c.dispose();
        expect(c.state.intents).toEqual([]);
        expect(c.state.unassigned).toEqual([]);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
    });

    it('uses no browser storage and no patient fields', () => {
        const src = readFileSync(new URL('../../../static/js/clinic/compose-import.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(src).not.toMatch(/patient_(name|ref|display|reference)/);
    });
});
