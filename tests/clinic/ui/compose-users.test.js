import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createPoolsPanel, createUsersController, membershipsOf, registerUsersView, SECTIONS, USERS_TEMPLATE, UsersView,
} from '../../../static/js/clinic/compose-users.js';
import { AccountsAdminPanel } from '../../../static/js/clinic/accounts-ui.js';

const ok = (data) => ({ outcome: 'ok', data });

// Synthetic data in the real API shapes (src/api/admin.py, src/clinic/routes_pools.py).
const USERS = {
    users: [
        { id: 1, username: 'adm', email: 'adm@example.test', is_admin: true, status: 'active', login_policy: 'local_only', is_identity_admin: true, version: 3 },
        { id: 2, username: 'autor1', email: 'a1@example.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
        { id: 3, username: 'schreib1', email: 's1@example.test', is_admin: false, status: 'disabled', login_policy: 'local_only', is_identity_admin: false, version: 2 },
    ],
};
const POOLS = {
    revision: 4,
    author_pools: [{ id: 'ap1', name: 'Autoren Nord', member_user_ids: [2] }],
    staff_pools: [{ id: 'sp1', name: 'Schreibpool A', member_user_ids: [3, 2] }, { id: 'sp2', name: 'Schreibpool B', member_user_ids: [] }],
};

function fakeVue() {
    const hooks = { mounted: [], unmount: [], watchers: [] };
    return {
        hooks,
        reactive: (o) => o,
        ref: (v) => ({ value: v }),
        shallowRef: (v) => ({ value: v }),
        markRaw: (o) => o,
        computed: (fn) => ({ get value() { return fn(); } }),
        watch: (getter, cb) => hooks.watchers.push({ getter, cb }),
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
    };
}

function fakeClient(handler) {
    return {
        request: vi.fn(async (path, opts) => (handler ? handler(path, opts) : ok({}))),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

const routes = (path) => {
    if (path === '/admin/users') return ok(USERS);
    if (path === '/pools') return ok(POOLS);
    return ok({ revision: 1, items: [] });
};
const source = () => readFileSync(new URL('../../../static/js/clinic/compose-users.js', import.meta.url), 'utf8');

describe('users view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerUsersView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-users-view', UsersView);
        expect(Object.keys(UsersView.props)).toEqual(['client', 'adminClient', 'currentUser', 'vue']);
    });

    it('has german sections as in the page spec and no tab roles without arrow keys', () => {
        expect(SECTIONS.map((s) => s.label)).toEqual(['Konten', 'Poolbezüge', 'Fachrechte']);
        expect(USERS_TEMPLATE).toContain('aria-current');
        expect(USERS_TEMPLATE).not.toContain('role="tab"');
    });

    it('renders the account list from the real shape and composes the existing account panel', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const view = UsersView.setup({ client, currentUser: { id: 1 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.accounts.loaded).toBe(true);
        expect(view.accounts.accounts.map((a) => a.username)).toEqual(['adm', 'autor1', 'schreib1']);
        expect(view.accounts.accounts[2].status).toBe('disabled');
        expect(view.activeComponent.value).toBe(AccountsAdminPanel);
        expect(view.activeProps.value.controller.state).toBe(view.accounts);
        expect(client.request.mock.calls[0][0]).toBe('/admin/users');
    });

    it('uses a separate admin client for the account routes when given', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const adminClient = fakeClient(routes);
        UsersView.setup({ client, adminClient, currentUser: { id: 1 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(adminClient.request).toHaveBeenCalledWith('/admin/users');
        expect(client.request).not.toHaveBeenCalled();
    });

    it('shows the 403 state and offers no section', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ({ outcome: 'permission', status: 403, message: 'm' }));
        const view = UsersView.setup({ client, currentUser: { id: 5 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.state.forbidden).toBe(true);
        expect(view.select('pools')).toBe(false);
        expect(view.state.active).toBe('accounts');
        expect(USERS_TEMPLATE).toContain('state.forbidden');
        expect(USERS_TEMPLATE).toContain('role="alert"');
        expect(USERS_TEMPLATE).toContain('v-if="accounts.loaded"');
    });

    it('does not treat a network error as 403', async () => {
        const client = fakeClient(() => ({ outcome: 'network', message: 'm' }));
        const ctrl = createUsersController({ client, currentUser: { id: 5 } });
        expect(await ctrl.load()).toBe(false);
        expect(ctrl.state.forbidden).toBe(false);
        expect(ctrl.accounts.state.error).toBe('network');
    });

    it('shows the empty state for an empty user list', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => (path === '/admin/users' ? ok({ users: [] }) : ok(POOLS)));
        const view = UsersView.setup({ client, currentUser: { id: 1 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.accounts.loaded).toBe(true);
        expect(view.accounts.accounts).toEqual([]);
        expect(view.state.forbidden).toBe(false);
        expect(USERS_TEMPLATE).toContain('accounts.loaded && !accounts.accounts.length');
        expect(USERS_TEMPLATE).toContain('{{ labels.empty }}');
        expect(view.labels.empty).toContain('Keine Benutzer');
    });

    it('refuses an unauthenticated user and loads nothing', async () => {
        const client = fakeClient();
        const ctrl = createUsersController({ client, currentUser: null });
        expect(ctrl.state.denied).toBe(true);
        expect(await ctrl.load()).toBe(false);
        expect(ctrl.select('pools')).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
    });

    it('selects only listed users and switches section', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const view = UsersView.setup({ client, currentUser: { id: 1 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.select('grants')).toBe(true);
        expect(view.activeComponent.value).toBeNull();
        expect(view.selectUser('99')).toBeNull();
        expect(view.selectUser('2')).toBe(2);
        const grants = view.activeComponent.value;
        expect(grants.template).toContain('grants-title');
        expect(view.activeComponent.value).toBe(grants);
        expect(view.select('nope')).toBe(false);
    });

    it('requests editable grant entries only while the viewer is a site admin', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const props = { client, currentUser: { id: 1, is_admin: true }, vue: Vue };
        const view = UsersView.setup(props);
        await Vue.hooks.mounted[0]();
        view.select('grants');
        view.selectUser(2);
        const adminGrants = view.activeComponent.value.setup();
        await adminGrants.reload();
        expect(adminGrants.state.canEdit).toBe(true);
        expect(client.request).toHaveBeenCalledWith('/identity/users/2/grants?entries=1');

        props.currentUser.is_admin = false;
        const readOnlyGrants = view.activeComponent.value.setup();
        await readOnlyGrants.reload();
        expect(readOnlyGrants.state.canEdit).toBe(false);
        expect(client.request).toHaveBeenCalledWith('/identity/users/2/grants');
    });

    it('scopes state to the current user and drops it on user change', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const props = { client, currentUser: { id: 1 }, vue: Vue };
        const view = UsersView.setup(props);
        await Vue.hooks.mounted[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '1', dictationId: null });
        view.select('pools');
        view.selectUser('2');
        const epoch = view.state.epoch;
        props.currentUser = { id: 2 };
        const watcher = Vue.hooks.watchers[0];
        expect(watcher.getter()).toBe('2');
        watcher.cb();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        expect(view.state.active).toBe('accounts');
        expect(view.state.selectedId).toBeNull();
        expect(view.state.epoch).toBe(epoch + 1);
        watcher.cb();
        expect(view.state.epoch).toBe(epoch + 1);
    });

    it('releases in-flight calls on unmount', () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const view = UsersView.setup({ client, currentUser: { id: 3 }, vue: Vue });
        Vue.hooks.unmount[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(view.select('pools')).toBe(false);
        const before = client.switchContext.mock.calls.length;
        Vue.hooks.unmount[0]();
        expect(client.switchContext.mock.calls.length).toBe(before);
    });
});

describe('pool memberships per user', () => {
    const pools = {
        author: [{ id: 'ap1', name: 'Autoren Nord', memberUserIds: [2] }],
        staff: [{ id: 'sp1', name: 'Schreibpool A', memberUserIds: [3, 2] }, { id: 'sp2', name: 'Schreibpool B', memberUserIds: [] }],
    };

    it('derives author and staff memberships separately', () => {
        expect(membershipsOf(pools, 2)).toEqual({
            author: [{ id: 'ap1', name: 'Autoren Nord' }], staff: [{ id: 'sp1', name: 'Schreibpool A' }],
        });
        expect(membershipsOf(pools, 1)).toEqual({ author: [], staff: [] });
        expect(membershipsOf(null, 1)).toEqual({ author: [], staff: [] });
    });

    it('loads GET /pools on mount and groups memberships for the user', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const panel = createPoolsPanel(Vue).setup({ client, userId: 2 });
        await Vue.hooks.mounted[0]();
        expect(client.request).toHaveBeenCalledWith('/pools');
        expect(panel.state.status).toBe('ready');
        expect(panel.groups.value.map((g) => [g.title, g.items.map((p) => p.name)])).toEqual([
            ['Autorenpools', ['Autoren Nord']], ['Schreibkräfte-Pools', ['Schreibpool A']],
        ]);
    });

    it('shows an error with retry when the pool read fails', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ({ outcome: 'network', message: 'Die Verbindung ist fehlgeschlagen.' }));
        const panel = createPoolsPanel(Vue).setup({ client, userId: 2 });
        await Vue.hooks.mounted[0]();
        expect(panel.state.status).toBe('error');
        expect(panel.errorStyle.color).toBe('var(--text-danger-strong)');
    });
});

describe('source rules', () => {
    it('reuses the building blocks and keeps no storage or colours', () => {
        const text = source();
        expect(text).toContain("from './accounts-ui.js'");
        expect(text).toContain("from './pools-ui.js'");
        expect(text).toContain("from './grants-ui.js'");
        expect(text).toContain("from './tokens.js'");
        expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(text).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('gives every icon-only control an accessible name', () => {
        const text = source();
        // No icon-only button exists here; the composed account buttons carry aria-label.
        expect(text).not.toMatch(/<button[^>]*><\/button>/);
        expect(readFileSync(new URL('../../../static/js/clinic/accounts-ui.js', import.meta.url), 'utf8')).toContain(':aria-label="labels.edit');
    });
});
