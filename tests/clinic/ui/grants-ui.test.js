import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createGrantsStore, effectiveActions, idList, loadGrantUsers, validateDraft, label, statusSurface, GRANTS_TEMPLATE,
} from '../../../static/js/clinic/grants-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const bad = (outcome) => ({ outcome, message: 'm' });
const SNAP = (rev) => ok({
    user_id: 7,
    revision: rev,
    items: [
        { effect: 'allow', scope: 'all', actions: ['view', 'edit'] },
        { effect: 'deny', scope: 'pool', pool_id: 'p1', actions: ['edit'] },
    ],
});
const DRAFT = { effect: 'allow', scope: 'pool', poolId: ' p2 ', actions: ['export', 'view'] };

function setup(writeResults, options, rev = 2) {
    const client = {
        request: vi.fn(async (path, opts) => (opts && opts.method === 'POST'
            ? (writeResults.shift() || ok({}))
            : SNAP(rev))),
    };
    return { client, store: createGrantsStore(client, null, options) };
}

describe('grants ui', () => {
    it('can grant archive on its own without also granting release or delete', async () => {
        const { store, client } = setup([ok({ revision: 3 })]);
        await store.load(7);
        await store.save({ effect: 'allow', scope: 'own', poolId: '', actions: ['archive'] });
        expect(client.request.mock.calls[1][1].body.actions).toEqual(['archive']);
        expect(label('clinic.grants.action.archive')).toBe('Archivieren');
    });

    it('shows scope and per-action effect separately with the revision', async () => {
        const { store, client } = setup([]);
        await store.load(7);
        expect(client.request.mock.calls[0][0]).toBe('/identity/users/7/grants');
        expect(store.state.revision).toBe(2);
        expect(store.state.items.map((i) => [i.effect, i.scope, i.poolId])).toEqual([
            ['allow', 'all', null], ['deny', 'pool', 'p1'],
        ]);
    });

    it('previews effective denial before save: deny wins over allow', async () => {
        const { store } = setup([]);
        await store.load(7);
        const rows = Object.fromEntries(store.preview(null).map((r) => [r.action, r]));
        expect(rows.view.result).toBe('allowed');
        expect(rows.edit.result).toBe('denied');
        expect(rows.edit.overridden).toBe(true);
        const after = Object.fromEntries(store.preview({ effect: 'deny', scope: 'all', actions: ['view'] }).map((r) => [r.action, r]));
        expect(after.view.result).toBe('denied');
        expect(effectiveActions([], null).every((r) => r.result === 'none')).toBe(true);
    });

    it('rejects unsupported expansions locally and sends nothing', async () => {
        const { store, client } = setup([]);
        await store.load(7);
        const bads = [
            { ...DRAFT, scope: 'team' },
            { ...DRAFT, effect: 'maybe' },
            { ...DRAFT, actions: ['statistics'] },
            { ...DRAFT, actions: [] },
            { ...DRAFT, role: 'team_admin' },
            { ...DRAFT, poolId: ' ' },
        ];
        for (const d of bads) {
            expect(validateDraft(d).ok).toBe(false);
            await store.save(d);
        }
        expect(client.request).toHaveBeenCalledTimes(1);
        expect(validateDraft(DRAFT).ok).toBe(true);
    });

    it('saves with the read revision and reloads', async () => {
        const { store, client } = setup([]);
        await store.load(7);
        await store.save(DRAFT);
        const [path, opts] = client.request.mock.calls[1];
        expect(path).toBe('/identity/users/7/grants');
        expect(opts.method).toBe('POST');
        expect(opts.body).toEqual({
            expected_revision: 2, effect: 'allow', scope: 'pool', pool_id: 'p2', actions: ['view', 'export'],
        });
        expect(client.request).toHaveBeenCalledTimes(3);
        expect(store.state.status).toBe('ready');
    });

    it('stale revision keeps newer state and does not retry', async () => {
        const { store, client } = setup([bad('conflict')]);
        await store.load(7);
        await store.save(DRAFT);
        expect(store.state.status).toBe('stale');
        expect(store.state.items).toHaveLength(2);
        expect(store.state.failedWrite).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(2);
        expect(statusSurface(store.state).status).toBe('warning');
    });

    it('unauthorized write is denied without retry state; read-only never sends', async () => {
        const denied = setup([bad('permission')]);
        await denied.store.load(7);
        await denied.store.save(DRAFT);
        expect(denied.store.state.status).toBe('denied');
        expect(denied.store.state.failedWrite).toBeNull();
        const ro = setup([], { canEdit: false });
        await ro.store.load(7);
        await ro.store.save(DRAFT);
        expect(ro.client.request).toHaveBeenCalledTimes(1);
        expect(ro.store.state.canEdit).toBe(false);
    });

    it('network failure retries the identical change', async () => {
        const { store, client } = setup([bad('network'), ok({})]);
        await store.load(7);
        await store.save(DRAFT);
        expect(store.state.status).toBe('error');
        await store.retry();
        const [, first] = client.request.mock.calls[1];
        const [, second] = client.request.mock.calls[2];
        expect(second.body).toEqual(first.body);
        expect(store.state.status).toBe('ready');
    });

    it('has german labels, accessible markup, no hex colours and no storage', () => {
        expect(label('clinic.grants.effective.overridden')).toContain('Verweigerung');
        expect(GRANTS_TEMPLATE).toContain('aria-live="polite"');
        expect(GRANTS_TEMPLATE).toContain('<fieldset>');
        expect(GRANTS_TEMPLATE).toContain('role="alert"');
        expect(GRANTS_TEMPLATE).toMatch(/<label for="grants-scope">/);
        expect(GRANTS_TEMPLATE).toContain('grants-authors');
        expect(GRANTS_TEMPLATE).toContain('grants-exceptions');
        expect(label('clinic.grants.only')).toBe('Nur');
        expect(label('clinic.grants.except')).toBe('Ausser');
        const src = readFileSync(new URL('../../../static/js/clinic/grants-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });

    it('loads every page of account names and rejects a broken directory', async () => {
        const pages = [
            ok({ users: [{ id: 2, username: 'Beta' }, { id: 2, username: 'Beta' }], pagination: { has_next: true } }),
            ok({ users: [{ id: 1, username: 'Alpha' }, { id: 'x', username: 'no' }], pagination: { has_next: false } }),
        ];
        const client = { request: vi.fn(async () => pages.shift()) };
        await expect(loadGrantUsers(client)).resolves.toEqual({
            users: [{ id: 1, username: 'Alpha' }, { id: 2, username: 'Beta' }], message: '',
        });
        const broken = await loadGrantUsers({ request: vi.fn(async () => ok({ revision: 1 })) });
        expect(broken.users).toEqual([]);
        expect(broken.message).toContain('Benutzerliste');
        expect(idList(['12', 11])).toEqual([12, 11]);
        expect(idList([11, 11])).toBeNull();
    });

    it('saves only the named authors on an allow for every dictation', async () => {
        const { store, client } = setup([]);
        await store.load(7);
        await store.save({ effect: 'allow', scope: 'all', poolId: '', actions: ['view'], authorIds: [11] });
        expect(client.request.mock.calls[1][1].body).toEqual({
            expected_revision: 2, effect: 'allow', scope: 'all', pool_id: null, actions: ['view'], author_ids: [11],
        });
        expect(validateDraft({ effect: 'allow', scope: 'own', actions: ['view'], authorIds: [11] }).ok).toBe(false);
        expect(validateDraft({ effect: 'allow', scope: 'all', actions: ['view'], exceptIds: [12] }).ok).toBe(false);
    });

    it('saves pool A except Beta as an allow plus a deny for that author', async () => {
        let revision = 2;
        const posts = [];
        const client = {
            request: vi.fn(async (path, opts) => {
                if (opts && opts.method === 'POST') {
                    posts.push(opts.body);
                    if (opts.body.expected_revision !== revision) return bad('conflict');
                    revision += 1;
                    return ok({ revision, items: [] });
                }
                return ok({
                    user_id: 7, revision, items: posts.map((body, index) => ({
                        id: 'g' + index, effect: body.effect, scope: body.scope, pool_id: body.pool_id,
                        actions: body.actions, author_ids: body.author_ids, author_names: body.author_ids ? ['Beta'] : [],
                    })),
                });
            }),
        };
        const store = createGrantsStore(client);
        await store.load(7);
        await store.save({ effect: 'allow', scope: 'pool', poolId: 'A', actions: ['view'], exceptIds: [12] });
        expect(posts).toEqual([
            { expected_revision: 2, effect: 'allow', scope: 'pool', pool_id: 'A', actions: ['view'] },
            { expected_revision: 3, effect: 'deny', scope: 'pool', pool_id: 'A', actions: ['view'], author_ids: [12] },
        ]);
        expect(store.state.items.map((item) => [item.effect, item.authorNames])).toEqual([['allow', []], ['deny', ['Beta']]]);
        await store.save({ effect: 'allow', scope: 'pool', poolId: 'A', actions: ['view'], exceptIds: [12] });
        expect(posts).toHaveLength(2);
    });

    it('keeps stored rows when the pool allow conflicts and does not send the exception', async () => {
        const posts = [];
        const client = {
            request: vi.fn(async (path, opts) => {
                if (opts && opts.method === 'POST') {
                    posts.push(opts.body);
                    return bad('conflict');
                }
                return SNAP(2);
            }),
        };
        const store = createGrantsStore(client);
        await store.load(7);
        await store.save({ effect: 'allow', scope: 'pool', poolId: 'A', actions: ['view'], exceptIds: [12] });
        expect(posts).toHaveLength(1);
        expect(store.state.status).toBe('stale');
        expect(store.state.items).toHaveLength(2);
        expect(store.state.failedWrite).toBeNull();
    });

    it('does not send a named grant for a read-only viewer', async () => {
        const ro = setup([], { canEdit: false });
        await ro.store.load(7);
        await ro.store.save({ effect: 'allow', scope: 'all', actions: ['view'], authorIds: [11] });
        expect(ro.client.request).toHaveBeenCalledTimes(1);
        expect(ro.client.request.mock.calls[0][0]).toBe('/identity/users/7/grants');
    });
});
