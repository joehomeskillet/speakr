import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createGrantsStore, effectiveActions, validateDraft, label, statusSurface, GRANTS_TEMPLATE,
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
        const src = readFileSync(new URL('../../../static/js/clinic/grants-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
