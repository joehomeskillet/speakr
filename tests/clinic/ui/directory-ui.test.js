import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createDirectoryReview, useDirectoryReview, DirectoryReview, registerDirectoryReview, groupPreview, LABELS,
} from '../../../static/js/clinic/directory-ui.js';

const PREVIEW = {
    source: 'ldap', version: 'v7', hash: 'h1', changed_fields: [], ambiguous: 0, unmatchable: 0,
    actions: [
        { kind: 'create_pending_user', object_id: 'o1', local_user_id: null, detail: 'Neu Person' },
        { kind: 'update_display_name', object_id: 'o2', local_user_id: 2, detail: 'Anna' },
        { kind: 'deactivate', object_id: 'o3', local_user_id: 3, detail: 'Bernd' },
    ],
    membership_changes: [{ kind: 'add', subject_id: 'o2', user_id: 2, pool_kind: 'author', pool_id: 'p1' }],
};

function ok(data) {
    return { outcome: 'ok', status: 200, code: null, data: data, message: '' };
}

function problem(outcome, code, status) {
    return { outcome: outcome, status: status, code: code, message: 'x' };
}

function fakeClient(apply) {
    return {
        request: vi.fn(async (path) => {
            if (path.endsWith('/capability')) return ok({ directory_available: true, local_windows_accounts: 'unavailable' });
            if (path.endsWith('/preview')) return ok(PREVIEW);
            return apply ? apply() : ok({ ok: true, applied_actions: 2, applied_memberships: 1 });
        }),
        switchContext: vi.fn(),
    };
}

async function loaded(apply) {
    const client = fakeClient(apply);
    const review = createDirectoryReview({ client: client });
    await review.load();
    return { client, review };
}

describe('directory ui', () => {
    it('groups additions, modifications and disabled identities', async () => {
        const { review } = await loaded();
        const g = groupPreview(review.state.preview);
        expect(g.additions).toHaveLength(1);
        expect(g.modifications).toHaveLength(1);
        expect(g.disabled).toHaveLength(1);
        expect(g.memberships).toHaveLength(1);
        expect(review.state.status).toBe('success');
        expect(review.state.capability.local_windows_accounts).toBe('unavailable');
    });

    it('requires explicit confirmation bound to version and hash, and never selects links', async () => {
        const { client, review } = await loaded();
        expect((await review.apply()).code).toBe('not_confirmed');
        expect(review.review().ok).toBe(true);
        expect((await review.apply()).ok).toBe(true);
        const call = client.request.mock.calls.find((c) => c[0].endsWith('/apply'));
        expect(call[1].body).toMatchObject({ version: 'v7', hash: 'h1' });
        expect(call[1].body.actions).toEqual([
            { kind: 'update_display_name', object_id: 'o2' },
            { kind: 'deactivate', object_id: 'o3' },
        ]);
        expect(call[1].body.memberships).toEqual([{ kind: 'add', subject_id: 'o2', pool_kind: 'author', pool_id: 'p1' }]);
        expect(review.state.preview).toBeNull();
    });

    it('refuses an empty selection and cancels confirmation on change', async () => {
        const { review } = await loaded();
        for (const key of Object.keys(review.state.selected)) review.toggle(key);
        expect(review.review().code).toBe('nothing_selected');
        review.toggle('a:deactivate:o3');
        expect(review.review().ok).toBe(true);
        review.toggle('a:deactivate:o3');
        expect(review.state.confirming).toBe(false);
    });

    it('forces a reload after a stale version and never applies silently', async () => {
        const { client, review } = await loaded(() => problem('conflict', 'stale_version', 409));
        review.review();
        expect((await review.apply()).code).toBe('stale');
        expect(review.state.stale).toBe(true);
        expect(review.state.confirming).toBe(false);
        expect(review.review().code).toBe('stale');
        expect((await review.apply()).code).toBe('stale');
        expect(client.request.mock.calls.filter((c) => c[0].endsWith('/apply'))).toHaveLength(1);
        await review.load();
        expect(review.state.stale).toBe(false);
    });

    it('reports unauthorized (403) on load and apply', async () => {
        const denied = createDirectoryReview({ client: { request: vi.fn(async () => problem('permission', 'unauthorized', 403)), switchContext: vi.fn() } });
        expect((await denied.load()).code).toBe('permission');
        expect(denied.state.status).toBe('warning');
        const { review } = await loaded(() => problem('permission', 'unauthorized', 403));
        review.review();
        expect((await review.apply()).code).toBe('permission');
    });

    it('keeps the confirmation after a failure and allows retry', async () => {
        let calls = 0;
        const { review } = await loaded(() => (++calls === 1 ? problem('network', 'NETWORK', null) : ok({ ok: true })));
        review.review();
        const first = await review.apply();
        expect(first.code).toBe('failure');
        expect(review.state.status).toBe('error');
        expect(review.state.confirming).toBe(true);
        expect((await review.apply()).ok).toBe(true);
    });

    it('maps an unavailable directory', async () => {
        const down = createDirectoryReview({ client: { request: vi.fn(async () => problem('network', 'ldap_capability_unavailable', 503)), switchContext: vi.fn() } });
        expect((await down.load()).code).toBe('unavailable');
    });

    it('handles keyboard states', async () => {
        const { review } = await loaded();
        expect(review.onKey('a:deactivate:o3', { key: ' ' }).ok).toBe(true);
        expect(review.state.selected['a:deactivate:o3']).toBeUndefined();
        review.toggle('a:deactivate:o3');
        review.review();
        expect(review.onKey('x', { key: 'Escape' }).ok).toBe(true);
        expect(review.state.confirming).toBe(false);
        expect(review.onKey('x', { key: 'a' }).ok).toBe(false);
    });

    it('keeps directory data out of browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const { review } = await loaded();
        review.reset();
        expect(writes).toEqual([]);
        expect(review.state.preview).toBeNull();
    });

    it('exports a Vue component and registration with labels, roles and unresolved notice', () => {
        const reactive = vi.fn((v) => v);
        expect(useDirectoryReview({ reactive }, { client: fakeClient() }).labels.title).toBe('Verzeichnisabgleich');
        expect(reactive).toHaveBeenCalled();
        expect(DirectoryReview.template).toContain('role="region"');
        expect(DirectoryReview.template).toContain('aria-label');
        expect(DirectoryReview.template).toContain(LABELS.local);
        expect(LABELS.unresolved).toContain('nicht verifiziert');
        expect(typeof DirectoryReview.setup).toBe('function');
        expect(Object.keys(DirectoryReview.props)).toEqual(expect.arrayContaining(['client', 'currentUser', 'vue']));
        const app = { component: vi.fn() };
        registerDirectoryReview(app);
        expect(app.component).toHaveBeenCalledWith('clinic-directory-review', DirectoryReview);
    });

    it('does not load for a non-admin current user', () => {
        const client = fakeClient();
        const ctx = DirectoryReview.setup({ client: client, currentUser: { is_admin: false }, vue: { reactive: (v) => v }, managedPools: [] });
        expect(client.request).not.toHaveBeenCalled();
        expect(ctx.state.message).toContain('Keine Berechtigung');
    });

    it('uses no hard colours', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/directory-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
