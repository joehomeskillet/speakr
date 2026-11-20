import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createOfflineCrypto } from '../../../static/js/clinic/offline-crypto-ui.js';
import {
    MANAGED_PROFILE,
    OfflineView,
    TEXT,
    createOfflineViewController,
    registerOfflineView,
} from '../../../static/js/clinic/compose-offline.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-offline.js'), 'utf8');

const PASS = 'correct horse battery';
const who = { userId: 'u1', profile: MANAGED_PROFILE };
const SECRET = 'Befund geheim Patient Mustermann';
const grant = (over = {}) => ({
    available: true, version: 1, account_version: 'av1', browser_id: 'b1',
    quota_bytes: 100000, key_policy: 'encrypted_user_unlock', ...over,
});

function fakeClient(initial) {
    const state = { next: initial };
    return {
        state,
        request: vi.fn(async () => (state.next.outcome ? state.next : { outcome: 'ok', status: 200, data: state.next })),
    };
}

function memStorage() {
    const m = new Map();
    return {
        m,
        put: vi.fn(async (k, v) => { m.set(k, v); }),
        get: vi.fn(async (k) => m.get(k)),
        remove: vi.fn(async (k) => { m.delete(k); }),
        keys: vi.fn(async (p) => [...m.keys()].filter((k) => k.startsWith(p))),
    };
}

function link(online = true) {
    const l = { online, fns: [] };
    l.isOnline = () => l.online;
    l.subscribe = (fn) => { l.fns.push(fn); return () => { l.fns = l.fns.filter((f) => f !== fn); }; };
    return l;
}

function setup(over = {}) {
    const client = fakeClient(over.grant === undefined ? grant() : over.grant);
    const crypto = createOfflineCrypto({ client, iterations: 1000 });
    const encrypt = vi.spyOn(crypto, 'encryptRecord');
    const storage = 'storage' in over ? over.storage : memStorage();
    const connection = over.connection || link();
    const c = createOfflineViewController({ crypto, storage, who: over.who || who, connection, onRestore: over.onRestore, onLoggedOut: over.onLoggedOut, onWrapped: over.onWrapped });
    return { client, crypto, encrypt, storage, connection, c };
}

const rec = (n = 0) => ({ dictationId: 'd1', sequence: n });

afterEach(() => { vi.restoreAllMocks(); });

describe('compose-offline: default deny', () => {
    it('stores nothing and says so without an allowing grant', async () => {
        const t = setup({ grant: grant({ available: false }) });
        await t.c.start();
        expect(t.c.state.phase).toBe('denied');
        expect(t.c.state.notice).toBe(TEXT.denied);
        expect(await t.c.saveDraft(rec(), { text: SECRET })).toEqual({ saved: false, code: 'POLICY_DENIED' });
        expect(t.encrypt).not.toHaveBeenCalled();
        expect(t.storage.put).not.toHaveBeenCalled();
        expect(t.storage.m.size).toBe(0);
    });

    it('denies when the policy endpoint fails or has another key policy', async () => {
        for (const g of [{ outcome: 'permission', status: 403 }, grant({ key_policy: 'plain' })]) {
            const t = setup({ grant: g });
            await t.c.start();
            expect(t.c.state.phase).toBe('denied');
            expect((await t.c.saveDraft(rec(), { text: SECRET })).saved).toBe(false);
            expect(t.storage.put).not.toHaveBeenCalled();
        }
    });

    it('denies without a storage adapter and does not ask the server', async () => {
        const t = setup({ storage: null });
        await t.c.start();
        expect(t.c.state.phase).toBe('denied');
        expect(t.c.state.notice).toBe(TEXT.noStorage);
        expect((await t.c.saveDraft(rec(), { text: SECRET })).saved).toBe(false);
    });

    it('denies in every profile except the managed offline profile', async () => {
        for (const profile of ['byod_internal', 'managed_web_online', 'kis_webview', undefined]) {
            const t = setup({ who: { userId: 'u1', profile } });
            await t.c.start();
            expect(t.c.state.phase).toBe('denied');
            expect(t.client.request).not.toHaveBeenCalled();
            expect((await t.c.saveDraft(rec(), { text: SECRET })).saved).toBe(false);
            expect(t.storage.put).not.toHaveBeenCalled();
        }
    });

    it('a revoked grant purges stored drafts', async () => {
        const t = setup();
        await t.c.start();
        t.c.state.passphrase = PASS;
        await t.c.unlock(null);
        await t.c.saveDraft(rec(), { text: SECRET });
        expect(t.storage.m.size).toBe(1);
        t.client.state.next = grant({ available: false });
        await t.c.refresh();
        expect(t.c.state.phase).toBe('denied');
        expect(t.storage.m.size).toBe(0);
        expect(t.c.state.drafts).toEqual([]);
    });
});

describe('compose-offline: unreachable server keeps drafts', () => {
    async function withDraft() {
        const t = setup();
        await t.c.start();
        t.c.state.passphrase = PASS;
        await t.c.unlock(null);
        await t.c.saveDraft(rec(), { text: SECRET });
        expect(t.storage.m.size).toBe(1);
        return t;
    }

    it('keeps drafts on a 503 or network outcome and on a 401 permission outcome', async () => {
        for (const next of [{ outcome: 'network', status: 503 }, { outcome: 'permission', status: 401 }]) {
            const t = await withDraft();
            t.client.state.next = next;
            await t.c.refresh();
            expect(t.storage.m.size).toBe(1);
            expect(t.storage.remove).not.toHaveBeenCalled();
            expect(t.c.state.phase).toBe('locked');
            expect(t.c.state.notice).toBe(TEXT.unreachable);
            expect(t.c.state.drafts).toHaveLength(1);
            expect(t.crypto.unlocked).toBe(false);
        }
    });

    it('unlock and restore also keep drafts when the server cannot be reached', async () => {
        const t = await withDraft();
        t.client.state.next = { outcome: 'network', status: 503 };
        t.c.state.passphrase = PASS;
        expect(await t.c.unlock(null)).toBe(false);
        expect(t.storage.m.size).toBe(1);
        t.client.state.next = grant();
        await t.c.refresh();
        t.c.state.passphrase = PASS;
        await t.c.unlock(null);
        t.client.state.next = { outcome: 'network', status: 503 };
        expect(await t.c.restore('d1', 0)).toBeNull();
        expect(t.storage.m.size).toBe(1);
    });

    it('an explicit available:false still purges', async () => {
        const t = await withDraft();
        t.client.state.next = grant({ available: false });
        await t.c.refresh();
        expect(t.storage.m.size).toBe(0);
        expect(t.c.state.phase).toBe('denied');
    });
});

describe('compose-offline: with policy', () => {
    async function unlocked(over) {
        const t = setup(over);
        await t.c.start();
        t.c.state.passphrase = PASS;
        expect(await t.c.unlock(null)).toBe(true);
        return t;
    }

    it('encrypts drafts through offline-crypto and lists them without content', async () => {
        const wrapped = vi.fn();
        const t = await unlocked({ onWrapped: wrapped });
        expect(wrapped).toHaveBeenCalledTimes(1);
        expect(t.c.state.phase).toBe('unlocked');
        const res = await t.c.saveDraft(rec(3), { text: SECRET });
        expect(res).toEqual({ saved: true, code: null });
        expect(t.encrypt).toHaveBeenCalledTimes(1);
        expect(t.encrypt.mock.calls[0][0]).toMatchObject({ userId: 'u1', profile: MANAGED_PROFILE, dictationId: 'd1', sequence: 3 });
        expect(JSON.stringify([...t.storage.m.values()])).not.toContain('Befund');
        expect(t.c.state.drafts).toHaveLength(1);
        expect(t.c.state.drafts[0]).toMatchObject({ dictationId: 'd1', sequence: 3 });
        expect(JSON.stringify(t.c.state)).not.toContain('Befund');
    });

    it('refuses to save while locked', async () => {
        const t = setup();
        await t.c.start();
        expect(t.c.state.phase).toBe('locked');
        expect(await t.c.saveDraft(rec(), { text: SECRET })).toEqual({ saved: false, code: 'LOCKED' });
        expect(t.encrypt).not.toHaveBeenCalled();
    });

    it('restores a draft to the caller after a fresh grant check', async () => {
        const onRestore = vi.fn();
        const t = await unlocked({ onRestore });
        await t.c.saveDraft(rec(), { text: SECRET });
        const calls = t.client.request.mock.calls.length;
        expect(await t.c.restore('d1', 0)).toBe(true);
        expect(t.client.request.mock.calls.length).toBe(calls + 1);
        expect(onRestore).toHaveBeenCalledWith({ dictationId: 'd1', sequence: 0, value: { text: SECRET } });
        expect(JSON.stringify(t.c.state)).not.toContain('Befund');
    });

    it('restore is not possible while locked or offline', async () => {
        const t = await unlocked();
        await t.c.saveDraft(rec(), { text: SECRET });
        t.c.lock();
        expect(await t.c.restore('d1', 0)).toBeNull();
        t.c.state.passphrase = PASS;
        await t.c.unlock(null);
        t.connection.online = false;
        t.c.state.online = false;
        expect(await t.c.restore('d1', 0)).toBeNull();
    });

    it('discard needs a confirmation and removes only the local draft', async () => {
        const t = await unlocked();
        await t.c.saveDraft(rec(), { text: SECRET });
        const key = t.c.state.drafts[0].key;
        t.c.askDiscard(key);
        expect(t.storage.remove).not.toHaveBeenCalled();
        t.c.cancelDiscard();
        expect(await t.c.confirmDiscard()).toBe(false);
        expect(t.storage.m.size).toBe(1);
        t.c.askDiscard(key);
        expect(await t.c.confirmDiscard()).toBe(true);
        expect(t.storage.remove).toHaveBeenCalledWith(key);
        expect(t.c.state.drafts).toEqual([]);
        expect(t.c.state.notice).toBe(TEXT.discarded);
    });

    it('logout wipes drafts, locks the key and reports it', async () => {
        const onLoggedOut = vi.fn();
        const t = await unlocked({ onLoggedOut });
        await t.c.saveDraft(rec(0), { text: SECRET });
        await t.c.saveDraft(rec(1), { text: SECRET });
        expect(t.storage.m.size).toBe(2);
        await t.c.logout();
        expect(t.storage.remove).toHaveBeenCalledTimes(2);
        expect(t.storage.m.size).toBe(0);
        expect(t.crypto.unlocked).toBe(false);
        expect(t.c.state.drafts).toEqual([]);
        expect(t.c.state.phase).toBe('denied');
        expect(t.c.state.notice).toBe(TEXT.loggedOut);
        expect(onLoggedOut).toHaveBeenCalledTimes(1);
    });

    it('offline: lists drafts, never asks the server and never purges', async () => {
        const t = await unlocked();
        await t.c.saveDraft(rec(), { text: SECRET });
        t.connection.online = false;
        const calls = t.client.request.mock.calls.length;
        await t.c.refresh();
        expect(t.client.request.mock.calls.length).toBe(calls);
        expect(t.c.state.online).toBe(false);
        expect(t.c.state.phase).toBe('unlocked');
        expect(t.c.state.drafts).toHaveLength(1);
        expect(t.storage.m.size).toBe(1);
        t.c.state.passphrase = PASS;
        expect(await t.c.unlock(null)).toBe(false);
    });

    it('offline: an unlocked key stays unlocked and drafts can still be saved', async () => {
        const t = await unlocked();
        t.connection.online = false;
        await t.c.refresh();
        expect(t.c.state.phase).toBe('unlocked');
        expect(t.crypto.unlocked).toBe(true);
        expect(await t.c.saveDraft(rec(2), { text: SECRET })).toEqual({ saved: true, code: null });
        expect(t.encrypt).toHaveBeenCalledTimes(1);
        expect(t.storage.m.size).toBe(1);
        t.c.lock();
        expect(t.crypto.unlocked).toBe(false);
        expect(t.c.state.phase).toBe('locked');
    });

    it('the template shows the lock button when unlocked and the storage usage', () => {
        expect(OfflineView.template).toContain('data-testid="usage"');
        expect(OfflineView.template).toContain('data-testid="lock"');
    });

    it('a connection event re-checks the grant', async () => {
        const t = setup();
        await t.c.start();
        const calls = t.client.request.mock.calls.length;
        t.connection.fns.forEach((f) => f());
        await Promise.resolve();
        await Promise.resolve();
        expect(t.client.request.mock.calls.length).toBeGreaterThan(calls);
        t.c.dispose();
        expect(t.connection.fns).toEqual([]);
    });

    it('shows the device change notice when the key no longer fits', async () => {
        const t = await unlocked();
        const blob = await t.crypto.setup(PASS, who);
        t.c.lock();
        t.client.state.next = grant({ browser_id: 'other-browser' });
        await t.c.refresh();
        t.c.state.passphrase = PASS;
        expect(await t.c.unlock(blob)).toBe(false);
        expect(t.c.state.notice).toBe(TEXT.deviceChanged);
        expect(t.c.state.passphrase).toBe('');
    });

    it('reports an unsaved draft instead of success when storage fails', async () => {
        const t = await unlocked();
        t.storage.put.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'QuotaExceededError' }));
        const res = await t.c.saveDraft(rec(), { text: SECRET });
        expect(res).toEqual({ saved: false, code: 'QUOTA' });
        expect(t.c.state.unsaved).toBe(1);
        expect(t.c.state.notice).toBe(TEXT.unsaved);
    });
});

describe('compose-offline: no transcript text leaks', () => {
    it('writes nothing to the console and keeps content out of messages', async () => {
        const spies = ['log', 'info', 'warn', 'error', 'debug'].map((k) => vi.spyOn(console, k).mockImplementation(() => {}));
        const t = setup();
        await t.c.start();
        t.c.state.passphrase = PASS;
        await t.c.unlock(null);
        await t.c.saveDraft(rec(), { text: SECRET });
        t.c.state.passphrase = 'wrong passphrase 1';
        t.c.lock();
        await t.c.unlock(await t.crypto.setup(PASS, who).catch(() => null));
        await t.c.restore('d1', 9);
        await t.c.logout();
        for (const s of spies) expect(s).not.toHaveBeenCalled();
        expect(t.c.state.notice).not.toContain('Befund');
    });

    it('the source does not use the console or browser storage directly', () => {
        expect(source).not.toMatch(/console\./);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});

describe('compose-offline: registration', () => {
    it('registers the component and mounts the controller lifecycle', async () => {
        const app = { component: vi.fn() };
        expect(registerOfflineView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-offline-view', OfflineView);
        expect(Object.keys(OfflineView.props)).toEqual(expect.arrayContaining(['client', 'who', 'storage', 'getWrapped']));
        const mounted = [];
        const unmounting = [];
        const storage = memStorage();
        const out = OfflineView.setup({
            who, storage, client: fakeClient(grant()),
            vue: { reactive: (x) => x, onMounted: (f) => mounted.push(f), onBeforeUnmount: (f) => unmounting.push(f) },
        });
        mounted.forEach((f) => f());
        await vi.waitFor(() => expect(out.s.phase).toBe('locked'));
        expect(out.s.phase).toBe('locked');
        unmounting.forEach((f) => f());
        expect(out.s.drafts).toEqual([]);
    });

    it('the view carries no patient fields', () => {
        expect(OfflineView.template).not.toMatch(/patient|recipient|notes/i);
    });
});
