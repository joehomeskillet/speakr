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

function setup(commandResults, rev = 3) {
    const client = {
        request: vi.fn(async () => SNAP(rev)),
        command: vi.fn(async () => commandResults.shift() || ok({})),
    };
    return { client, store: createPoolsStore(client) };
}

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
        const { store, client } = setup([]);
        await store.load();
        await store.addMember('staff', 's1', 1);
        const body = client.command.mock.calls[0][0];
        expect(body.command).toBe('save_staff_pool');
        expect(body.expected_revision).toBe(3);
        expect(body.payload.member_user_ids).toEqual([2, 1]);
        expect(client.command.mock.calls[0][1].idempotencyKey).toBe(body.request_id);
    });

    it('needs a visible confirmation before removing, and cancel sends nothing', async () => {
        const { store, client } = setup([]);
        await store.load();
        expect(store.requestRemoval('author', 'a1', 2)).toBe(true);
        expect(store.state.pendingRemoval).toEqual({ type: 'author', poolId: 'a1', userId: 2 });
        expect(handleKey(store, { key: 'Escape' })).toBe(true);
        expect(store.state.pendingRemoval).toBeNull();
        expect(client.command).not.toHaveBeenCalled();
        store.requestRemoval('author', 'a1', 2);
        await store.confirmRemoval();
        const body = client.command.mock.calls[0][0];
        expect(body.command).toBe('save_author_pool');
        expect(body.payload.member_user_ids).toEqual([1]);
    });

    it('stale revision keeps newer state and does not overwrite', async () => {
        const { store, client } = setup([bad('conflict')]);
        await store.load();
        store.requestRemoval('author', 'a1', 1);
        await store.confirmRemoval();
        expect(store.state.status).toBe('stale');
        expect(store.state.pools.author[0].memberUserIds).toEqual([1, 2]);
        expect(client.command).toHaveBeenCalledTimes(1);
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
        const { store, client } = setup([bad('network'), ok({})]);
        await store.load();
        await store.addMember('author', 'a1', 9);
        expect(store.state.status).toBe('error');
        await store.retry();
        const [first, second] = client.command.mock.calls;
        expect(second[0].request_id).toBe(first[0].request_id);
        expect(second[1].idempotencyKey).toBe(first[1].idempotencyKey);
        expect(store.state.status).toBe('ready');
    });

    it('ignores duplicate or invalid additions', async () => {
        const { store, client } = setup([]);
        await store.load();
        await store.addMember('author', 'a1', 1);
        await store.addMember('author', 'a1', 0);
        await store.addMember('other', 'a1', 5);
        expect(client.command).not.toHaveBeenCalled();
    });

    it('has german labels, accessible markup, no hex colours and no storage', () => {
        expect(label('clinic.pools.confirmRemove')).toContain('entfernen');
        expect(POOLS_TEMPLATE).toContain('role="alertdialog"');
        expect(POOLS_TEMPLATE).toContain('aria-live="polite"');
        const src = readFileSync(new URL('../../../static/js/clinic/pools-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
