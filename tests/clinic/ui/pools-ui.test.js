import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createPoolsStore, handleKey, label, statusSurface, POOLS_TEMPLATE } from '../../../static/js/clinic/pools-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const bad = (outcome) => ({ outcome, message: 'm' });
const SNAP = (rev) => ok({
    revision: rev,
    author_pools: [{ id: 'a1', name: 'Autoren A', member_user_ids: [1, 2] }],
    staff_pools: [{ id: 's1', name: 'Schreibdienst', member_user_ids: [2] }],
});

function setup(writeResults, rev = 3) {
    const client = {
        request: vi.fn(async (path, opts) => (opts && opts.method === 'PUT'
            ? (writeResults.shift() || ok({}))
            : SNAP(rev))),
        command: vi.fn(),
    };
    const writes = () => client.request.mock.calls.filter((c) => c[1] && c[1].method === 'PUT');
    return { client, writes, store: createPoolsStore(client) };
}

const src0 = () => readFileSync(new URL('../../../static/js/clinic/pools-ui.js', import.meta.url), 'utf8');

describe('pools ui', () => {
    it('lists both pool types separately with the persisted revision', async () => {
        const { store } = setup([]);
        await store.load();
        expect(store.state.revision).toBe(3);
        expect(store.state.pools.author.map((p) => p.id)).toEqual(['a1']);
        expect(store.state.pools.staff.map((p) => p.id)).toEqual(['s1']);
        expect(store.state.pools.author[0].memberUserIds).toEqual([1, 2]);
    });

    it('allows the same user in several pools and adds a membership', async () => {
        const { store, client, writes } = setup([]);
        await store.load();
        await store.addMember('staff', 's1', 1);
        const [path, opts] = writes()[0];
        expect(path).toBe('/pools/staff/s1');
        expect(opts.body).toEqual({ expected_revision: 3, name: 'Schreibdienst', member_user_ids: [2, 1] });
        expect(typeof opts.idempotencyKey).toBe('string');
        expect(client.command).not.toHaveBeenCalled();
    });

    it('needs a visible confirmation before removing, and cancel sends nothing', async () => {
        const { store, writes } = setup([]);
        await store.load();
        expect(store.requestRemoval('author', 'a1', 2)).toBe(true);
        expect(store.state.pendingRemoval).toEqual({ type: 'author', poolId: 'a1', userId: 2 });
        expect(handleKey(store, { key: 'Escape' })).toBe(true);
        expect(store.state.pendingRemoval).toBeNull();
        expect(writes()).toHaveLength(0);
        store.requestRemoval('author', 'a1', 2);
        await store.confirmRemoval();
        const [path, opts] = writes()[0];
        expect(path).toBe('/pools/author/a1');
        expect(opts.body.member_user_ids).toEqual([1]);
    });

    it('stale revision keeps newer state and does not overwrite', async () => {
        const { store, writes } = setup([bad('conflict')]);
        await store.load();
        store.requestRemoval('author', 'a1', 1);
        await store.confirmRemoval();
        expect(store.state.status).toBe('stale');
        expect(store.state.pools.author[0].memberUserIds).toEqual([1, 2]);
        expect(writes()).toHaveLength(1);
        expect(store.state.failedWrite).toBeNull();
        expect(statusSurface(store.state).status).toBe('warning');
    });

    it('unauthorized write is denied without retry state', async () => {
        const { store } = setup([bad('permission')]);
        await store.load();
        await store.addMember('author', 'a1', 9);
        expect(store.state.status).toBe('denied');
        expect(store.state.failedWrite).toBeNull();
    });

    it('network failure retries with the same request id', async () => {
        const { store, writes } = setup([bad('network'), ok({})]);
        await store.load();
        await store.addMember('author', 'a1', 9);
        expect(store.state.status).toBe('error');
        await store.retry();
        const [first, second] = writes();
        expect(second[0]).toBe(first[0]);
        expect(second[1].body).toEqual(first[1].body);
        expect(second[1].idempotencyKey).toBe(first[1].idempotencyKey);
        expect(store.state.status).toBe('ready');
    });

    it('ignores duplicate or invalid additions', async () => {
        const { store, writes } = setup([]);
        await store.load();
        await store.addMember('author', 'a1', 1);
        await store.addMember('author', 'a1', 0);
        await store.addMember('other', 'a1', 5);
        expect(writes()).toHaveLength(0);
    });

    it('has german labels, accessible markup, no hex colours and no storage', () => {
        expect(label('clinic.pools.confirmRemove')).toContain('entfernen');
        expect(POOLS_TEMPLATE).toContain('role="alertdialog"');
        expect(POOLS_TEMPLATE).toContain('aria-live="polite"');
        expect(POOLS_TEMPLATE).toContain('addFromDraft');
        expect(POOLS_TEMPLATE).toContain('<label :for="\'pools-add-\'');
        expect(src0()).not.toMatch(/save_(author|staff)_pool/);
        const src = readFileSync(new URL('../../../static/js/clinic/pools-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
