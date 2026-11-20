import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    AUTHORS_TEMPLATE, AuthorsView, LABELS, createAuthorsController, deriveAuthors, registerAuthorsView,
} from '../../../static/js/clinic/compose-authors.js';

const ok = (data) => ({ outcome: 'ok', data });
const denied = { outcome: 'permission', status: 403, message: 'Keine Berechtigung für diese Aktion.' };

// Synthetic data in the real API shapes: GET /admin/users and GET /pools.
const USERS = {
    users: [
        { id: 3, username: 'berta', email: 'b@example.invalid', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
        { id: 1, username: 'anton', email: 'a@example.invalid', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 2 },
        { id: 5, username: 'dora', email: 'd@example.invalid', is_admin: false, status: 'disabled', login_policy: 'local_only', is_identity_admin: false, version: 1 },
        { id: 9, username: 'nur-konto', email: 'n@example.invalid', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
    ],
};
const POOLS = {
    revision: 4,
    author_pools: [
        { id: 'ap-1', name: 'Chirurgie', member_user_ids: [1, 3] },
        { id: 'ap-2', name: 'Innere', member_user_ids: [3, 5] },
    ],
    staff_pools: [{ id: 7, name: 'Schreibdienst', member_user_ids: [1] }],
};

function fakeClient(routes) {
    return {
        request: vi.fn(async (path) => {
            const r = routes[path];
            return typeof r === 'function' ? r() : r;
        }),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

function fakeVue() {
    const hooks = { mounted: [] };
    return {
        hooks,
        reactive: (o) => o,
        markRaw: (o) => o,
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: () => {},
        ref: (v) => ({ value: v }),
        shallowRef: (v) => ({ value: v }),
        computed: (fn) => ({ get value() { return fn(); } }),
    };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-authors.js', import.meta.url), 'utf8');

describe('authors view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerAuthorsView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-authors-view', AuthorsView);
        expect(Object.keys(AuthorsView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('derives authors from accounts and pools, with multi-membership and staff relation', () => {
        const accounts = USERS.users.map((u) => ({ ...u }));
        const pools = {
            author: POOLS.author_pools.map((p) => ({ id: p.id, name: p.name, memberUserIds: p.member_user_ids })),
            staff: POOLS.staff_pools.map((p) => ({ id: String(p.id), name: p.name, memberUserIds: p.member_user_ids })),
        };
        const rows = deriveAuthors(accounts, pools);
        expect(rows.map((r) => r.name)).toEqual(['anton', 'berta', 'dora']);
        const anton = rows[0];
        expect(anton.roleLabel).toBe('Diktierend und Schreibkraft');
        expect(anton.staffPools).toEqual([{ id: '7', name: 'Schreibdienst' }]);
        expect(anton.noStaffPool).toBe(false);
        const berta = rows[1];
        expect(berta.multiAuthorPool).toBe(true);
        expect(berta.authorPools.map((p) => p.name)).toEqual(['Chirurgie', 'Innere']);
        expect(berta.noStaffPool).toBe(true);
        expect(rows[2].statusLabel).toBe('Deaktiviert');
        expect(rows.some((r) => r.id === 9)).toBe(false);
    });

    it('loads users and pools and exposes ready rows', async () => {
        const client = fakeClient({ '/admin/users': ok(USERS), '/pools': ok(POOLS) });
        const ctrl = createAuthorsController({ client });
        expect(ctrl.state.status).toBe('idle');
        const pending = ctrl.load();
        expect(ctrl.state.status).toBe('loading');
        expect(await pending).toBe(true);
        expect(ctrl.state.status).toBe('ready');
        expect(ctrl.state.rows).toHaveLength(3);
        expect(ctrl.state.epoch).toBe(1);
    });

    it('shows an empty state when no user belongs to an author pool', async () => {
        const client = fakeClient({
            '/admin/users': ok(USERS),
            '/pools': ok({ revision: 0, author_pools: [], staff_pools: [] }),
        });
        const ctrl = createAuthorsController({ client });
        await ctrl.load();
        expect(ctrl.state.status).toBe('ready');
        expect(ctrl.state.rows).toEqual([]);
        expect(LABELS.empty).toContain('Keine Diktierenden');
        expect(AUTHORS_TEMPLATE).toContain('!state.rows.length');
    });

    it('shows a 403 state and lists nothing when the account list is refused', async () => {
        const client = fakeClient({ '/admin/users': denied, '/pools': ok(POOLS) });
        const ctrl = createAuthorsController({ client });
        expect(await ctrl.load()).toBe(false);
        expect(ctrl.state.status).toBe('denied');
        expect(ctrl.state.rows).toEqual([]);
        expect(ctrl.state.message).toBe(LABELS.denied);
        expect(AUTHORS_TEMPLATE).toContain("state.status === 'denied'");
    });

    it('shows a 403 state when the pool list is refused', async () => {
        const client = fakeClient({ '/admin/users': ok(USERS), '/pools': denied });
        const ctrl = createAuthorsController({ client });
        await ctrl.load();
        expect(ctrl.state.status).toBe('denied');
    });

    it('keeps an error state with retry for network failures', async () => {
        let up = false;
        const client = fakeClient({
            '/admin/users': () => (up ? ok(USERS) : { outcome: 'network', message: 'x' }),
            '/pools': ok(POOLS),
        });
        const ctrl = createAuthorsController({ client });
        await ctrl.load();
        expect(ctrl.state.status).toBe('error');
        expect(ctrl.state.message).toBe(LABELS.error);
        up = true;
        expect(await ctrl.retry()).toBe(true);
        expect(ctrl.state.status).toBe('ready');
    });

    it('lets only the newest load write the state', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        let first = true;
        const client = fakeClient({
            '/admin/users': async () => { if (first) { first = false; await gate; return denied; } return ok(USERS); },
            '/pools': ok(POOLS),
        });
        const ctrl = createAuthorsController({ client });
        const slow = ctrl.load();
        await ctrl.load();
        release();
        expect(await slow).toBe(false);
        expect(ctrl.state.status).toBe('ready');
    });

    it('sets up the view, loads on mount and mounts the pools component as child', async () => {
        const Vue = fakeVue();
        const client = fakeClient({ '/admin/users': ok(USERS), '/pools': ok(POOLS) });
        const view = AuthorsView.setup({ client, currentUser: { id: 1 }, vue: Vue });
        expect(view.poolsComponent.template).toContain('pools-title');
        expect(view.labels).toBe(LABELS);
        await Vue.hooks.mounted[0]();
        expect(view.state.status).toBe('ready');
        expect(AUTHORS_TEMPLATE).toContain('<component :is="poolsComponent" :key="state.epoch">');
    });

    it('uses german labels, table semantics and the one-pool rule', () => {
        expect(LABELS.columns.name).toBe('Anzeigename');
        expect(LABELS.columns.status).toBe('Kontostatus');
        expect(LABELS.noStaffPool).toBe('Kein Pool zugewiesen');
        expect(LABELS.ruleOnePool).toContain('höchstens einem Autorenpool');
        expect(AUTHORS_TEMPLATE).toContain('scope="col"');
        expect(AUTHORS_TEMPLATE).toContain('scope="row"');
        expect(AUTHORS_TEMPLATE).toContain('role="alert"');
        expect(AUTHORS_TEMPLATE).toContain('aria-live="polite"');
    });

    it('reuses the existing building blocks and does no own I/O or storage', () => {
        const code = source();
        expect(code).toContain("from './accounts-ui.js'");
        expect(code).toContain("from './pools-ui.js'");
        expect(code).toContain("from './tokens.js'");
        expect(code).not.toMatch(/localStorage|sessionStorage|indexedDB|fetch\(/);
        expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('shows no patient or email data', () => {
        expect(AUTHORS_TEMPLATE).not.toMatch(/email|patient|fall/i);
    });
});
