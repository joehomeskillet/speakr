import { describe, expect, it } from 'vitest';
import { createOfflineCrypto } from '../../../static/js/clinic/offline-crypto-ui.js';
import {
    OfflineStoreError,
    createOfflineStore,
    offlineStorePanel,
    useOfflineStore,
} from '../../../static/js/clinic/offline-store-ui.js';

const PASS = 'correct horse battery';
const who = { userId: 'u1', profile: 'managed_web_offline' };
const grant = (over = {}) => ({
    available: true, version: 1, account_version: 'av1', browser_id: 'b1',
    quota_bytes: 100000, key_policy: 'encrypted_user_unlock', ...over,
});

function fakeClient(initial) {
    const state = { next: initial };
    return {
        state,
        request: async () => (state.next.outcome ? state.next : { outcome: 'ok', data: state.next }),
    };
}

function memStorage() {
    const m = new Map();
    return {
        m, failWith: null,
        put: async function (k, v) { if (this.failWith) throw this.failWith; m.set(k, v); },
        get: async (k) => m.get(k),
        remove: async (k) => { m.delete(k); },
        keys: async (p) => [...m.keys()].filter((k) => k.startsWith(p)),
    };
}

async function ready(over, user = who) {
    const client = fakeClient(grant(over));
    const crypto = createOfflineCrypto({ client, iterations: 1000 });
    const storage = memStorage();
    const store = createOfflineStore({ crypto, storage, who: user });
    await store.authorize();
    const wrapped = await crypto.setup(PASS, user);
    return { client, crypto, storage, store, wrapped };
}

const rec = (n = 0) => ({ dictationId: 'd1', sequence: n });
const code = async (p) => {
    try { await p; } catch (e) { return e.code; }
    return null;
};

describe('offline-store-ui', () => {
    it('stores only ciphertext in the namespace and reads it back', async () => {
        const { store, storage } = await ready();
        expect(await store.save(rec(), { text: 'Befund Patient' })).toEqual({ saved: true, code: null });
        const [key] = [...storage.m.keys()];
        expect(key.startsWith('clinic-managed-offline/u1/managed_web_offline/')).toBe(true);
        expect(JSON.stringify([...storage.m.values()])).not.toContain('Befund');
        expect(await store.load(rec())).toEqual({ text: 'Befund Patient' });
    });

    it('refuses to store when the grant is revoked and purges existing records', async () => {
        const { store, storage, client } = await ready();
        await store.save(rec(), { a: 1 });
        client.state.next = grant({ available: false });
        expect((await store.authorize()).available).toBe(false);
        expect(storage.m.size).toBe(0);
        const res = await store.save(rec(1), { a: 2 });
        expect(res).toEqual({ saved: false, code: 'UNAUTHORIZED' });
        expect(storage.m.size).toBe(0);
    });

    it('keeps the value unsaved when the server check fails with a network error', async () => {
        const { store, client, storage } = await ready();
        client.state.next = { outcome: 'network', status: null };
        expect((await store.authorize()).available).toBe(false);
        expect((await store.save(rec(), { a: 1 })).saved).toBe(false);
        expect(storage.m.size).toBe(0);
    });

    it('enforces the server quota without evicting stored data', async () => {
        const { store, storage } = await ready({ quota_bytes: 400 });
        expect((await store.save(rec(0), { a: 1 })).saved).toBe(true);
        const res = await store.save(rec(1), { text: 'x'.repeat(500) });
        expect(res).toEqual({ saved: false, code: 'QUOTA' });
        expect(storage.m.size).toBe(1);
        expect(store.unsaved).toBe(1);
        expect(await code(store.load(rec(1)))).toBe('UNSAVED');
        expect(await store.load(rec(0))).toEqual({ a: 1 });
    });

    it('treats a storage QuotaExceededError as unsaved and retry succeeds later', async () => {
        const { store, storage } = await ready();
        storage.failWith = Object.assign(new Error('full'), { name: 'QuotaExceededError' });
        expect(await store.save(rec(), { a: 1 })).toEqual({ saved: false, code: 'QUOTA' });
        expect(await store.retry()).toBe(1);
        storage.failWith = new Error('io');
        expect((await store.save(rec(), { a: 1 })).code).toBe('STORAGE_FAILED');
        storage.failWith = null;
        expect(await store.retry()).toBe(0);
        expect(await store.load(rec())).toEqual({ a: 1 });
    });

    it('has no fallback store: missing adapter means unsaved', async () => {
        const client = fakeClient(grant());
        const crypto = createOfflineCrypto({ client, iterations: 1000 });
        const store = createOfflineStore({ crypto, who });
        await store.authorize();
        await crypto.setup(PASS, who);
        expect(await store.save(rec(), { a: 1 })).toEqual({ saved: false, code: 'NO_STORAGE' });
        expect(await code(store.load(rec()))).toBe('UNSAVED');
    });

    it('reports browser eviction and keeps unsaved separate', async () => {
        const { store, storage } = await ready();
        await store.save(rec(0), { a: 1 });
        storage.m.clear();
        const r = await store.reconcile();
        expect(r).toMatchObject({ stored: 0, lost: 1, unsaved: 0 });
        expect(await code(store.load(rec(0)))).toBe('NOT_FOUND');
    });

    it('logout locks the key and drops plaintext; records stay recoverable after unlock', async () => {
        const { store, storage, crypto, wrapped } = await ready();
        await store.save(rec(), { a: 1 });
        store.release();
        expect(crypto.unlocked).toBe(false);
        expect(storage.m.size).toBe(1);
        const r = await store.reconcile();
        expect(r.stored).toBe(1);
        expect(await code(store.load(rec()))).toBe('LOCKED');
        await crypto.unlock(PASS, wrapped, who);
        expect(await store.load(rec())).toEqual({ a: 1 });
    });

    it('account switch cannot read or write the other account namespace', async () => {
        const { store, storage, crypto } = await ready();
        await store.save(rec(), { a: 1 });
        const bob = { userId: 'u2', profile: who.profile };
        const other = createOfflineStore({ crypto, storage, who: bob });
        expect((await other.reconcile()).stored).toBe(0);
        expect(await code(other.load(rec()))).toBe('NOT_FOUND');
        expect(await other.save(rec(), { b: 2 })).toEqual({ saved: false, code: 'FOREIGN_ACCOUNT' });
        expect(storage.m.size).toBe(1);
    });

    it('stale grant version locks the key: save is unsaved with LOCKED, not stored', async () => {
        const { store, storage, client } = await ready();
        client.state.next = grant({ version: 2 });
        await store.authorize();
        expect(await store.save(rec(), { a: 1 })).toEqual({ saved: false, code: 'LOCKED' });
        expect(storage.m.size).toBe(0);
    });

    it('rejects an invalid configuration', () => {
        expect(() => createOfflineStore({ crypto: {}, who: {} })).toThrow(OfflineStoreError);
    });
});

describe('offline-store-ui composable and panel', () => {
    it('walks idle, saved, quota and unauthorized with matching labels', async () => {
        const { store, client } = await ready({ quota_bytes: 400 });
        const panel = useOfflineStore(store, () => rec(0));
        await panel.refresh();
        expect(panel.s.state).toBe('idle');
        await panel.save({ a: 1 });
        expect(panel.s.state).toBe('saved');
        expect(panel.view().text).toBe('Verschlüsselt gespeichert.');
        expect(panel.view().surface.background).toBe('var(--bg-success-light)');
        const big = useOfflineStore(store, () => rec(1));
        await big.save({ text: 'x'.repeat(500) });
        expect(big.s.state).toBe('quota');
        expect(big.view().canRetry).toBe(true);
        client.state.next = grant({ available: false });
        await panel.refresh();
        expect(panel.s.state).toBe('unauthorized');
        expect(panel.view().canRetry).toBe(false);
    });

    it('panel markup has a live status, labelled heading and native buttons', () => {
        const opts = offlineStorePanel({ s: { state: 'idle' }, view: () => ({}) });
        expect(opts.template).toContain('role="status"');
        expect(opts.template).toContain('aria-live="polite"');
        expect(opts.template).toContain('aria-labelledby="offline-store-title"');
        expect(opts.template).toContain('<button type="button"');
        expect(opts.template).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    });
});
