import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    ADMIN_POOLS_TEMPLATE, AdminPoolsView, TEXT, createAdminPoolsController, fromLocalInput, poolStatus,
    registerAdminPoolsView, searchAccounts, toLocalInput,
} from '../../../static/js/clinic/compose-admin-pools.js';

const ok = (data) => ({ outcome: 'ok', data });
const fail = (outcome, code, status) => ({ outcome, code: code || null, status: status || null, message: 'm' });

// Shapes follow src/clinic/routes_pool_admin.py (v8-29), routes_pools.py and src/api/admin.py.
const META = () => ({
    author_pools: [{ kind: 'author', id: 'a1', name: 'Orthopädie Autoren', active: true, valid_from: null, valid_to: null, routable: true }],
    staff_pools: [
        { kind: 'staff', id: 's1', name: 'Schreibdienst Nord', active: true, valid_from: null, valid_to: null, routable: true },
        { kind: 'staff', id: 's2', name: 'Schreibdienst Süd', active: false, valid_from: '2026-01-01T00:00:00+00:00', valid_to: null, routable: false },
        { kind: 'staff', id: 's3', name: 'Leerer Pool', active: true, valid_from: null, valid_to: null, routable: true },
    ],
});
const MEMBERS = (rev) => ({
    revision: rev,
    author_pools: [{ id: 'a1', name: 'Orthopädie Autoren', member_user_ids: [1] }],
    staff_pools: [
        { id: 's1', name: 'Schreibdienst Nord', member_user_ids: [11, 12] },
        { id: 's2', name: 'Schreibdienst Süd', member_user_ids: [12] },
        { id: 's3', name: 'Leerer Pool', member_user_ids: [] },
    ],
});
const USERS = () => ({
    users: [
        { id: 11, username: 'mueller', email: 'm@example.test', status: 'active', login_policy: 'local_only', version: 1 },
        { id: 12, username: 'meier', email: 'me@example.test', status: 'active', login_policy: 'local_only', version: 1 },
        { id: 13, username: 'mara', email: 'ma@example.test', status: 'active', login_policy: 'local_only', version: 1 },
        { id: 14, username: 'marco', email: 'mc@example.test', status: 'disabled', login_policy: 'local_only', version: 1 },
    ],
});
const USAGE = { kind: 'staff', pool_id: 's1', open_dictations: 4, members: 2, links: 1 };

function fakeClient(overrides) {
    const routes = {
        'GET /pool-admin/pools': () => ok(META()),
        'GET /pools': () => ok(MEMBERS(5)),
        'GET /admin/users': () => ok(USERS()),
        'GET /pool-admin/pools/staff/s1/usage': () => ok(USAGE),
        ...(overrides || {}),
    };
    const request = vi.fn(async (path, opts) => {
        const method = String((opts && opts.method) || 'GET').toUpperCase();
        const key = method + ' ' + path;
        const handler = routes[key];
        if (!handler) return ok({});
        return handler(opts);
    });
    return { request, command: vi.fn(), switchContext: vi.fn() };
}

const calls = (client, method, path) => client.request.mock.calls.filter(
    (c) => String((c[1] && c[1].method) || 'GET').toUpperCase() === method && c[0] === path);
const source = () => readFileSync(new URL('../../../static/js/clinic/compose-admin-pools.js', import.meta.url), 'utf8');

async function ready(overrides) {
    const client = fakeClient(overrides);
    const c = createAdminPoolsController({ client });
    await c.load();
    return { client, c };
}

describe('admin pools view: render with real API shapes', () => {
    it('lists only staff pools with status and member count, members by name', async () => {
        const { c } = await ready();
        expect(c.state.status).toBe('ready');
        const rows = c.rows();
        expect(rows.map((r) => r.name)).toEqual(['Schreibdienst Nord', 'Schreibdienst Süd', 'Leerer Pool']);
        expect(rows.map((r) => r.status.key)).toEqual(['active', 'inactive', 'active']);
        expect(rows.map((r) => r.memberUserIds.length)).toEqual([2, 1, 0]);
        await c.select('s1');
        expect(c.members()).toEqual([{ id: 11, name: 'mueller' }, { id: 12, name: 'meier' }]);
        expect(c.state.usage).toEqual({ openDictations: 4, members: 2, links: 1 });
    });

    it('shows an unknown account without an id', async () => {
        const { c } = await ready({ 'GET /pools': () => ok({ ...MEMBERS(5), staff_pools: [{ id: 's1', name: 'x', member_user_ids: [99] }] }) });
        await c.select('s1');
        expect(c.members()).toEqual([{ id: 99, name: TEXT.unknownMember }]);
        expect(TEXT.unknownMember).not.toMatch(/\d/);
    });

    it('marks a pool without members as not staffed', async () => {
        const { c } = await ready();
        await c.select('s3');
        expect(c.members()).toEqual([]);
        expect(ADMIN_POOLS_TEMPLATE).toContain('c.text.noMembers');
        expect(TEXT.noMembers).toContain('nicht besetzt');
    });

    it('marks an active pool outside its validity window', () => {
        expect(poolStatus({ active: true, routable: false }).key).toBe('outside');
        expect(poolStatus({ active: false, routable: false }).key).toBe('inactive');
        expect(poolStatus({ active: true, routable: true }).key).toBe('active');
    });
});

describe('admin pools view: 403 and empty states', () => {
    it('shows the denied state on 403 and loads nothing else', async () => {
        const { client, c } = await ready({ 'GET /pool-admin/pools': () => fail('permission', 'forbidden', 403) });
        expect(c.state.status).toBe('denied');
        expect(c.state.message).toBe(TEXT.denied);
        expect(c.rows()).toEqual([]);
        expect(calls(client, 'GET', '/pools')).toHaveLength(0);
        expect(calls(client, 'GET', '/admin/users')).toHaveLength(0);
        // The denied branch precedes the admin form in the template.
        const t = ADMIN_POOLS_TEMPLATE;
        expect(t.indexOf('c.state.status === \'denied\'')).toBeGreaterThan(-1);
        expect(t.indexOf('data-testid="denied-state"')).toBeLessThan(t.indexOf('id="cap-create-name"'));
    });

    it('does not run a write while denied', async () => {
        const { client, c } = await ready({ 'GET /pool-admin/pools': () => fail('permission', 'forbidden', 403) });
        c.state.createName = 'Neu';
        const res = await c.create();
        expect(res.ok).toBe(false);
        expect(calls(client, 'POST', '/pool-admin/pools')).toHaveLength(0);
    });

    it('treats a 403 on the member list as denied as well', async () => {
        const { c } = await ready({ 'GET /pools': () => fail('permission', 'forbidden', 403) });
        expect(c.state.status).toBe('denied');
        expect(c.rows()).toEqual([]);
    });

    it('shows the empty state without pools', async () => {
        const { c } = await ready({
            'GET /pool-admin/pools': () => ok({ author_pools: [], staff_pools: [] }),
            'GET /pools': () => ok({ revision: 0, author_pools: [], staff_pools: [] }),
        });
        expect(c.state.status).toBe('ready');
        expect(c.rows()).toEqual([]);
        expect(ADMIN_POOLS_TEMPLATE).toContain('data-testid="empty-state"');
        expect(TEXT.empty).toBe('Noch keine Schreibkräfte-Pools.');
    });

    it('shows a retryable error on a network failure', async () => {
        const { c } = await ready({ 'GET /pool-admin/pools': () => fail('network', 'NETWORK') });
        expect(c.state.status).toBe('error');
        expect(c.state.message).toBe(TEXT.failed);
        expect(ADMIN_POOLS_TEMPLATE).toContain('c.load()');
    });
});

describe('admin pools view: pool administration', () => {
    it('creates a staff pool and selects it', async () => {
        const created = { kind: 'staff', id: 's9', name: 'Neu', active: true, valid_from: null, valid_to: null, routable: true };
        const { client, c } = await ready({
            'POST /pool-admin/pools': () => ok(created),
            'GET /pool-admin/pools/staff/s9/usage': () => ok({ ...USAGE, pool_id: 's9', open_dictations: 0, members: 0, links: 0 }),
        });
        c.state.createName = '  Neu  ';
        const res = await c.create();
        expect(res.ok).toBe(true);
        expect(calls(client, 'POST', '/pool-admin/pools')[0][1].body).toEqual({ kind: 'staff', name: 'Neu' });
        expect(c.state.createName).toBe('');
    });

    it('refuses an empty name locally and maps a server name clash', async () => {
        const { client, c } = await ready({ 'POST /pool-admin/pools': () => fail('conflict', 'name_taken', 409) });
        c.state.createName = '   ';
        expect((await c.create()).message).toBe(TEXT.nameRequired);
        expect(calls(client, 'POST', '/pool-admin/pools')).toHaveLength(0);
        c.state.createName = 'Schreibdienst Nord';
        expect((await c.create()).message).toBe('Dieser Name ist bereits vergeben.');
        expect(c.state.createName).toBe('Schreibdienst Nord');
    });

    it('renames only when the name changed', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s1/name': () => ok({}) });
        await c.select('s1');
        expect((await c.rename()).unchanged).toBe(true);
        c.state.renameName = 'Schreibdienst Nordost';
        expect((await c.rename()).ok).toBe(true);
        const sent = calls(client, 'PUT', '/pool-admin/pools/staff/s1/name');
        expect(sent).toHaveLength(1);
        expect(sent[0][1].body).toEqual({ name: 'Schreibdienst Nordost' });
    });

    it('sets the validity window as ISO instants and checks the order', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s1/validity': () => ok({}) });
        await c.select('s1');
        c.state.validFrom = '2026-12-01T08:00';
        c.state.validTo = '2026-11-01T08:00';
        expect((await c.saveValidity()).message).toBe(TEXT.validityOrder);
        c.state.validTo = '2027-01-01T08:00';
        expect((await c.saveValidity()).ok).toBe(true);
        const body = calls(client, 'PUT', '/pool-admin/pools/staff/s1/validity')[0][1].body;
        expect(body.valid_from).toBe(new Date('2026-12-01T08:00').toISOString());
        expect(body.valid_to).toBe(new Date('2027-01-01T08:00').toISOString());
        expect(body.valid_from).toMatch(/Z$/);
    });

    it('opens the window on either side with null and round-trips the input value', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s1/validity': () => ok({}) });
        await c.select('s1');
        c.state.validFrom = '';
        c.state.validTo = '';
        await c.saveValidity();
        expect(calls(client, 'PUT', '/pool-admin/pools/staff/s1/validity')[0][1].body).toEqual({ valid_from: null, valid_to: null });
        expect(toLocalInput(fromLocalInput('2026-12-01T08:30'))).toBe('2026-12-01T08:30');
        expect(fromLocalInput('kaputt')).toBeUndefined();
        expect(toLocalInput(null)).toBe('');
    });

    it('shows the usage preview and asks before deactivating; cancel sends nothing', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s1/active': () => ok({}) });
        await c.select('s1');
        expect(await c.requestDeactivate()).toBe(true);
        expect(c.state.confirmDeactivate).toBe(true);
        expect(c.state.usage.openDictations).toBe(4);
        expect(calls(client, 'PUT', '/pool-admin/pools/staff/s1/active')).toHaveLength(0);
        expect(c.onKey({ key: 'Escape' })).toBe(true);
        expect(c.state.confirmDeactivate).toBe(false);
        expect(calls(client, 'PUT', '/pool-admin/pools/staff/s1/active')).toHaveLength(0);
        expect(TEXT.deactivateNote).toContain('nicht automatisch umverteilt');
        expect(TEXT.deactivateNote).toContain('Diktiert von bleibt unverändert');
    });

    it('deactivates after the confirmation and never deletes', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s1/active': () => ok({}) });
        await c.select('s1');
        await c.requestDeactivate();
        expect((await c.confirmDeactivate()).ok).toBe(true);
        expect(calls(client, 'PUT', '/pool-admin/pools/staff/s1/active')[0][1].body).toEqual({ active: false });
        expect(client.request.mock.calls.some((c2) => String((c2[1] && c2[1].method) || '') === 'DELETE')).toBe(false);
        expect(source()).not.toMatch(/DELETE/);
    });

    it('does not offer a confirmation when the usage cannot be loaded', async () => {
        const { c } = await ready({ 'GET /pool-admin/pools/staff/s1/usage': () => fail('network', 'NETWORK') });
        await c.select('s1');
        expect(await c.requestDeactivate()).toBe(false);
        expect(c.state.confirmDeactivate).toBe(false);
        expect(c.state.message).toBe(TEXT.usageUnavailable);
    });

    it('reactivates an inactive pool directly', async () => {
        const { client, c } = await ready({ 'PUT /pool-admin/pools/staff/s2/active': () => ok({}), 'GET /pool-admin/pools/staff/s2/usage': () => ok({ ...USAGE, pool_id: 's2' }) });
        await c.select('s2');
        expect(c.selected().active).toBe(false);
        await c.reactivate();
        expect(calls(client, 'PUT', '/pool-admin/pools/staff/s2/active')[0][1].body).toEqual({ active: true });
    });
});

describe('admin pools view: members by name search', () => {
    it('finds active accounts by name only, without current members', () => {
        const accounts = USERS().users.map((u) => ({ ...u }));
        expect(searchAccounts(accounts, 'ma', [])).toEqual([accounts[2]]);
        expect(searchAccounts(accounts, 'M', [11]).map((a) => a.username)).toEqual(['meier', 'mara']);
        expect(searchAccounts(accounts, '12', [])).toEqual([]);
        expect(searchAccounts(accounts, '   ', [])).toEqual([]);
        expect(searchAccounts(null, 'a', [])).toEqual([]);
    });

    it('adds a found account through the pool store with the persisted revision', async () => {
        const { client, c } = await ready({ 'PUT /pools/staff/s1': () => ok({}) });
        await c.select('s1');
        c.state.search = 'mar';
        expect(c.results().map((a) => a.username)).toEqual(['mara']);
        await c.addMember(13);
        const put = calls(client, 'PUT', '/pools/staff/s1')[0];
        expect(put[1].body).toEqual({ expected_revision: 5, name: 'Schreibdienst Nord', member_user_ids: [11, 12, 13] });
        expect(c.state.search).toBe('');
    });

    it('ignores an account that the search did not offer', async () => {
        const { client, c } = await ready();
        await c.select('s1');
        c.state.search = 'mar';
        expect(await c.addMember(14)).toBeNull();
        expect(calls(client, 'PUT', '/pools/staff/s1')).toHaveLength(0);
    });

    it('needs a visible confirmation before a member is removed', async () => {
        const { client, c } = await ready({ 'PUT /pools/staff/s1': () => ok({}) });
        await c.select('s1');
        expect(c.requestRemoval(11)).toBe(true);
        expect(c.pendingRemovalName()).toBe('mueller');
        c.cancelRemoval();
        expect(calls(client, 'PUT', '/pools/staff/s1')).toHaveLength(0);
        c.requestRemoval(11);
        await c.confirmRemoval();
        expect(calls(client, 'PUT', '/pools/staff/s1')[0][1].body.member_user_ids).toEqual([12]);
    });

    it('keeps the newer state and says so on a version conflict', async () => {
        const { c } = await ready({ 'PUT /pools/staff/s1': () => fail('conflict', 'stale_revision', 409) });
        await c.select('s1');
        c.state.search = 'mar';
        await c.addMember(13);
        expect(c.state.message).toBe(TEXT.stale);
    });

    it('disables member search when the account directory is not available', async () => {
        const { c } = await ready({ 'GET /admin/users': () => fail('permission', 'forbidden', 403) });
        await c.select('s1');
        expect(c.searchAvailable()).toBe(false);
        c.state.search = 'mar';
        expect(await c.addMember(13)).toBeNull();
        expect(ADMIN_POOLS_TEMPLATE).toContain('c.text.searchUnavailable');
    });

    it('can read the directory through a separate client', async () => {
        const client = fakeClient();
        const userClient = fakeClient();
        client.request.mockImplementation(async (path) => (path === '/pool-admin/pools' ? ok(META()) : path === '/pools' ? ok(MEMBERS(1)) : ok({})));
        const c = createAdminPoolsController({ client, userClient });
        await c.load();
        expect(userClient.request).toHaveBeenCalledWith('/admin/users');
        expect(client.request.mock.calls.some((x) => x[0] === '/admin/users')).toBe(false);
    });
});

describe('admin pools view: German labels, accessibility, hygiene', () => {
    it('uses the V8 labels and no numeric id input', () => {
        expect(TEXT.title).toBe('Schreibkräfte-Pools');
        expect(source()).not.toMatch(/Schreibpools/);
        for (const label of ['Schreibkräfte-Pool anlegen', 'Deaktivieren', 'Mitglied entfernen', 'Gültig ab', 'Gültig bis', 'Verwendung']) {
            expect(Object.values(TEXT)).toContain(label);
        }
        expect(ADMIN_POOLS_TEMPLATE).not.toContain('type="number"');
        expect(ADMIN_POOLS_TEMPLATE).not.toContain('inputmode="numeric"');
    });

    it('gives every icon button an accessible name and every input a label', () => {
        const t = ADMIN_POOLS_TEMPLATE;
        expect(t).toContain(':aria-label="c.text.removeMember + \': \' + m.name"');
        expect(t).toContain('&times;');
        for (const id of ['cap-create-name', 'cap-rename', 'cap-valid-from', 'cap-valid-to', 'cap-member-search']) {
            expect(t).toContain('for="' + id + '"');
            expect(t).toContain('id="' + id + '"');
        }
        expect(t).toContain('role="alertdialog"');
        expect(t).toContain('aria-labelledby="cap-title"');
    });

    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        expect(registerAdminPoolsView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-admin-pools-view', AdminPoolsView);
        expect(Object.keys(AdminPoolsView.props)).toEqual(['client', 'userClient', 'vue']);
    });

    it('wires load and dispose to the lifecycle and drops its data on dispose', async () => {
        const hooks = { mounted: [], unmount: [] };
        const Vue = { reactive: (o) => o, onMounted: (f) => hooks.mounted.push(f), onBeforeUnmount: (f) => hooks.unmount.push(f) };
        const client = fakeClient();
        const { c } = AdminPoolsView.setup({ client, userClient: null, vue: Vue });
        await hooks.mounted[0]();
        expect(c.state.status).toBe('ready');
        await c.select('s1');
        hooks.unmount[0]();
        expect(c.rows()).toEqual([]);
        expect(c.state.usage).toBeNull();
        expect(c.state.selectedId).toBeNull();
    });

    it('requires a client and uses no browser storage, no patient data and no hard colours', () => {
        expect(() => createAdminPoolsController({})).toThrow('client is required');
        const src = source();
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(src).not.toMatch(/patient|Patient/);
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(src).not.toMatch(/\bfetch\(/);
    });

    it('imports only existing building blocks for existing functions', () => {
        const imports = source().match(/^import .* from '.*';$/gm);
        expect(imports).toEqual([
            "import { createPoolsStore } from './pools-ui.js';",
            "import { createAccountsAdmin } from './accounts-ui.js';",
            "import { surfaceFor } from './tokens.js';",
        ]);
    });
});
