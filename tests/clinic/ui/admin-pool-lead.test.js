import { describe, expect, it, vi } from 'vitest';
import { createAdminPoolsController, TEXT } from '../../../static/js/clinic/compose-admin-pools.js';
import { createAuthorsController } from '../../../static/js/clinic/compose-authors.js';
import { readFileSync } from 'node:fs';

const ok = (data) => ({ outcome: 'ok', data });
async function scene({ admin = true, outcome = 'ok', roles = true } = {}) {
    let role = 'member';
    const client = { request: vi.fn(async (path, options) => {
        if (options?.method === 'PUT') {
            if (outcome === 'ok') role = options.body.role;
            return { outcome };
        }
        if (path === '/pool-admin/pools') return ok({ staff_pools: [{ id: 's1', name: 'Nord' }] });
        if (path === '/pools') return ok({ revision: 3, staff_pools: [{ id: 's1', name: 'Nord', member_user_ids: [2],
            members: roles ? [{ user_id: 2, role }] : undefined }],
            author_pools: [{ id: 'a1', name: 'Autoren', member_user_ids: [2] }] });
        return ok({});
    }) };
    const userClient = { request: vi.fn(async () => ok({ users: [{ id: 2, username: 'Anna', status: 'active' }] })) };
    const c = createAdminPoolsController({ client, userClient, currentUser: { is_admin: admin } });
    await c.load();
    await c.select('s1');
    return { c, client, userClient };
}

describe('v8-142 protected lead role client', () => {
    it('loads names separately, promotes and demotes only through the role endpoint, then reloads', async () => {
        const { c, client, userClient } = await scene();
        expect(c.members()).toEqual([{ id: 2, name: 'Anna' }]);
        expect(userClient.request).toHaveBeenCalledWith('/admin/users');
        expect(c.memberRole(2)).toBe('member');
        expect(await c.setLead(2, true)).toEqual({ ok: true });
        expect(c.memberRole(2)).toBe('lead');
        expect(await c.setLead(2, false)).toEqual({ ok: true });
        expect(c.memberRole(2)).toBe('member');
        const writes = client.request.mock.calls.filter(([, o]) => o?.method === 'PUT');
        expect(writes).toEqual(['lead', 'member'].map((role) => [
            '/pool-admin/pools/staff/s1/members/2/role', { method: 'PUT', body: { role } },
        ]));
    });
    it.each(['permission', 'conflict', 'network', 'not_found'])('keeps confirmed role after %s', async (outcome) => {
        const { c } = await scene({ outcome });
        expect(await c.setLead(2, true)).toEqual({ ok: false });
        expect(c.memberRole(2)).toBe('member');
        expect(c.state.message).toBe(outcome === 'permission' ? TEXT.denied : outcome === 'conflict' ? TEXT.stale : TEXT.roleFailed);
        expect(c.state.busy).toBe(false);
    });
    it.each([{ admin: false }, { roles: false }])('fails closed for %j', async (options) => {
        const { c, client } = await scene(options);
        expect(await c.setLead(2, true)).toEqual({ ok: false });
        expect(client.request.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(false);
    });
    it('refuses foreign members, duplicate writes, stale membership and disposed views', async () => {
        const { c, client } = await scene();
        await c.setLead(99, true);
        c.state.busy = true;
        await c.setLead(2, true);
        c.state.busy = false;
        c.poolsView.status = 'stale';
        await c.setLead(2, true);
        c.dispose();
        await c.setLead(2, true);
        expect(client.request.mock.calls.some(([, o]) => o?.method === 'PUT')).toBe(false);
    });
    it('authors use both real path families through the routing client', async () => {
        const { client, userClient } = await scene();
        const routing = { request: (path, options) => (path.startsWith('/admin/') ? userClient : client).request(path, options) };
        const authors = createAuthorsController({ client: routing });
        expect(await authors.load()).toBe(true);
        expect(authors.state.rows[0].name).toBe('Anna');
        expect(client.request.mock.calls.some(([path]) => path.startsWith('/admin/'))).toBe(false);
    });
    it('both production mounts pass routing and the current site-admin identity', () => {
        const root = new URL('../../../', import.meta.url);
        const admin = readFileSync(new URL('templates/admin.html', root), 'utf8');
        const shell = readFileSync(new URL('static/js/app.modular.js', root), 'utf8');
        expect(admin).toContain('props: { ...v8props, userClient: routingClient }');
        expect(admin).toContain('props: { ...v8props, client: routingClient }');
        expect(shell).toContain("if (entry.view === 'clinic-admin-pools') return { ...base, userClient: clinicAuthorsClient }");
        expect(shell).toContain("if (entry.view === 'clinic-admin-authors') return { ...base, client: clinicAuthorsClient }");
    });
});
