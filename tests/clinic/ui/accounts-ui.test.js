import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createAccountsAdmin, changesOf, LABELS, AccountsAdminPanel } from '../../../static/js/clinic/accounts-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const bad = (outcome, status, code) => ({ outcome, status, code: code || null });

function row(id, extra) {
    return Object.assign({
        id, username: 'user' + id, email: 'u' + id + '@example.invalid', is_admin: false,
        is_identity_admin: false, status: 'active', login_policy: 'local_only', version: 3,
    }, extra || {});
}

function fakeClient(users, handler) {
    const calls = [];
    return {
        calls,
        request: vi.fn(async (path, opts) => {
            calls.push([path, opts || {}]);
            if (!opts || !opts.method) return ok({ users });
            return handler ? handler(path, opts) : ok({});
        }),
    };
}

async function loaded(users, handler) {
    const client = fakeClient(users, handler);
    const c = createAccountsAdmin({ client });
    await c.load();
    return { c, client };
}

describe('accounts admin', () => {
    it('loads accounts and shows them', async () => {
        const { c } = await loaded([row(1), row(2)]);
        expect(c.state.accounts.map((a) => a.id)).toEqual([1, 2]);
    });

    it('create posts only identity fields and continues in edit mode', async () => {
        const { c, client } = await loaded([], (path, opts) => ok({ id: 7 }));
        client.request.mockImplementation(async (path, opts) => {
            client.calls.push([path, opts || {}]);
            if (!opts || !opts.method) return ok({ users: [row(7)] });
            return ok({ id: 7 });
        });
        c.startCreate();
        c.state.draft.username = ' neu ';
        c.state.draft.email = 'neu@example.invalid';
        expect(await c.apply()).toBe(true);
        const post = client.calls.find((x) => x[1].method === 'POST');
        expect(post[1].body).toEqual({ username: 'neu', email: 'neu@example.invalid' });
        expect(c.state.draft.mode).toBe('edit');
        expect(c.state.draft.id).toBe(7);
    });

    it('Apply keeps the editor open, OK closes it, Cancel discards edits', async () => {
        const { c, client } = await loaded([row(1)]);
        c.startEdit(1);
        c.state.draft.is_identity_admin = true;
        expect(await c.apply()).toBe(true);
        expect(c.state.draft).not.toBeNull();
        const put = client.calls.find((x) => x[1].method === 'PUT');
        expect(put[1].body).toEqual({ expected_version: 3, is_identity_admin: true });
        c.state.draft.status = 'disabled';
        expect(await c.confirm()).toBe(true);
        expect(c.state.draft).toBeNull();
        c.startEdit(1);
        c.state.draft.username = 'geaendert';
        expect(c.isDirty()).toBe(true);
        c.cancel();
        expect(c.state.draft).toBeNull();
        expect(c.state.accounts[0].username).toBe('user1');
    });

    it('never sends password, roles, pools or grants', async () => {
        const { c, client } = await loaded([row(1)]);
        c.startEdit(1);
        Object.assign(c.state.draft, { password: 'x', roles: ['a'], pools: [1], is_admin: true });
        await c.apply();
        const body = client.calls.find((x) => x[1].method === 'PUT')[1].body;
        expect(Object.keys(body).sort()).toEqual(['expected_version', 'is_admin']);
        expect(changesOf({ ...row(1), password: 'x' }, row(1))).toEqual({});
    });

    it('disables and reactivates with the loaded version', async () => {
        const { c, client } = await loaded([row(1), row(2, { status: 'disabled' })]);
        await c.setStatus(1, 'disabled');
        await c.setStatus(2, 'active');
        const puts = client.calls.filter((x) => x[1].method === 'PUT').map((x) => x[1].body);
        expect(puts).toEqual([
            { expected_version: 3, status: 'disabled' },
            { expected_version: 3, status: 'active' },
        ]);
    });

    it('unauthorized shows a permission message and keeps the draft', async () => {
        const { c } = await loaded([row(1)], () => bad('permission', 403));
        c.startEdit(1);
        c.state.draft.is_admin = true;
        expect(await c.apply()).toBe(false);
        expect(c.state.error).toBe('permission');
        expect(c.state.draft).not.toBeNull();
    });

    it('stale version reloads the account and discards the edit with a message', async () => {
        const { c, client } = await loaded([row(1)], () => bad('conflict', 409));
        client.request.mockImplementation(async (path, opts) => {
            if (!opts || !opts.method) return ok({ users: [row(1, { version: 4, email: 'neu@example.invalid' })] });
            return bad('conflict', 409);
        });
        c.startEdit(1);
        c.state.draft.status = 'disabled';
        expect(await c.confirm()).toBe(false);
        expect(c.state.error).toBe('conflict');
        expect(c.state.draft.version).toBe(4);
        expect(c.state.draft.status).toBe('active');
    });

    it('network failure keeps the draft and a retry succeeds', async () => {
        let n = 0;
        const { c } = await loaded([row(1)], () => (n++ === 0 ? bad('network', null, 'NETWORK') : ok({})));
        c.startEdit(1);
        c.state.draft.is_identity_admin = true;
        expect(await c.apply()).toBe(false);
        expect(c.state.error).toBe('network');
        expect(c.state.draft.is_identity_admin).toBe(true);
        expect(await c.apply()).toBe(true);
        expect(c.state.error).toBeNull();
    });

    it('required fields are validated before any request', async () => {
        const { c, client } = await loaded([]);
        c.startCreate();
        expect(await c.apply()).toBe(false);
        expect(c.state.error).toBe('username_required');
        expect(client.calls.filter((x) => x[1].method).length).toBe(0);
    });

    it('admin recovery is refused with a clear message and no request', async () => {
        const { c, client } = await loaded([row(1, { is_admin: true })]);
        expect(await c.issueRecovery(1)).toBe(false);
        expect(c.state.error).toBe('admin_recovery_forbidden');
        expect(c.state.message).toBe(LABELS.errors.admin_recovery_forbidden);
        expect(client.calls.filter((x) => x[1].method).length).toBe(0);
    });

    it('a server 403 on recovery shows the same message', async () => {
        const { c } = await loaded([row(2)], () => bad('permission', 403));
        expect(await c.issueRecovery(2)).toBe(false);
        expect(c.state.error).toBe('admin_recovery_forbidden');
    });

    it('recovery code is held in memory until dismissed or cancelled', async () => {
        const { c } = await loaded([row(2)], () => ok({ code: 'ABCD-1234', expires_at: 'x' }));
        expect(await c.issueRecovery(2)).toBe(true);
        expect(c.state.recoveryCode).toBe('ABCD-1234');
        c.dismissCode();
        expect(c.state.recoveryCode).toBeNull();
    });

    it('keeps data out of browser storage and hard-codes no colour', () => {
        const src = readFileSync(new URL('../../../static/js/clinic/accounts-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
    });

    it('panel has labelled controls, alert region and Escape handling', () => {
        const t = AccountsAdminPanel.template;
        expect(t).toContain('role="alert"');
        expect(t).toContain('@keydown.esc.prevent');
        expect(t).toContain('<label for="acc-username">');
        expect(t).toContain('scope="col"');
    });
});
