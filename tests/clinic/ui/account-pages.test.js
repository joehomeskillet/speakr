import { describe, expect, it, vi } from 'vitest';
import { AccountsAdminPanel, createAccountsAdmin } from '../../../static/js/clinic/accounts-ui.js';

function fixture(count, write) {
    const users = Array.from({ length: count }, (_, i) => ({
        id: i + 1, username: 'person' + String(i + 1).padStart(4, '0'), email: 'mail' + i + '@example.invalid',
        version: 4, login_policy: 'local_only', is_identity_admin: i === count - 1,
        login_locked: true, group_grants_available: true, monthly_token_budget: 2000,
    }));
    const request = vi.fn(async (path, opts) => {
        if (opts) return write(opts.body);
        const p = new URL(path, 'http://example.invalid').searchParams;
        const filtered = users.filter(u => u.username.includes(p.get('search') || ''));
        if (p.get('order') === 'desc') filtered.reverse();
        const per = Math.min(Number(p.get('per_page') || 15), 1000);
        const pages = Math.max(Math.ceil(filtered.length / per), 1);
        const page = Math.min(Number(p.get('page') || 1), pages);
        return { outcome: 'ok', data: { users: filtered.slice((page - 1) * per, page * per),
            pagination: { page, total_pages: pages, total: filtered.length } } };
    });
    return { c: createAccountsAdmin({ client: { request } }), request };
}

describe('clinic account directory', () => {
    it('preserves full pickers across the API cap, pages the table and searches beyond page one', async () => {
        const { c, request } = fixture(1003);
        await c.load();
        expect(c.state.accounts).toHaveLength(1003);
        expect(c.state.table.users).toHaveLength(15);
        await c.browse(67, {});
        expect(c.state.table.users[0].id).toBe(991);
        expect(c.state.table.page).toBe(67);
        await c.browse(1, { search: 'person100', sort: 'username', order: 'desc' });
        expect(c.state.table.users.map(u => u.id)).toEqual([1003, 1002, 1001, 1000]);
        expect(AccountsAdminPanel.data.call({ controller: c })).toMatchObject({ query: 'person100', sortOrder: 'username:desc' });
        expect(request.mock.calls.at(-1)[0]).toContain('search=person100&sort=username&order=desc');
        await c.browse(1, { role: 'is_identity_admin' });
        expect(c.state.table.users.map(u => u.id)).toEqual([1003]);
    });

    it('opens the account table alphabetically using a paged read and retains its search when reopened', async () => {
        const { c, request } = fixture(32);
        const vm = { controller: c, s: c.state, ...AccountsAdminPanel.data.call({ controller: c }),
            browse: AccountsAdminPanel.methods.browse };
        await AccountsAdminPanel.mounted.call(vm);
        expect(request.mock.calls.at(-1)[0]).toBe('/admin/users?per_page=15&sort=username');
        await c.browse(1, { search: 'person003', sort: 'username', order: 'desc' });
        expect(AccountsAdminPanel.data.call({ controller: c })).toMatchObject({ query: 'person003', sortOrder: 'username:desc' });
        const count = request.mock.calls.length;
        await AccountsAdminPanel.mounted.call(vm);
        expect(request).toHaveBeenCalledTimes(count);
    });

    it('preserves the raw seconds and other unchanged fields and uses a single versioned command', async () => {
        let payload;
        const { c } = fixture(1, body => { payload = body; return { outcome: 'ok', data: {} }; });
        await c.load();
        c.startEdit(1);
        Object.assign(c.state.draft, { name: 'Beispiel', storage_quota_mb: '64', monthly_token_budget: '',
            monthly_transcription_budget: '93', create_groups_granted: true, unlock_login: true });
        expect(await c.confirm()).toBe(true);
        expect(payload).toEqual({ expected_version: 4, name: 'Beispiel', storage_quota_mb: 64, monthly_token_budget: null,
            monthly_transcription_budget: 93, create_groups_granted: true, unlock_login: true });
    });

    it('handles the server reauthentication reason without dropping edits', async () => {
        const { c } = fixture(1, () => ({ outcome: 'permission', status: 403, code: 'reauth_required' }));
        await c.load();
        c.startEdit(1);
        c.state.draft.name = 'Beispiel';
        expect(await c.confirm()).toBe(false);
        expect(c.state.message).toMatch(/erneut anmelden/);
        expect(c.state.draft.name).toBe('Beispiel');
    });
});
