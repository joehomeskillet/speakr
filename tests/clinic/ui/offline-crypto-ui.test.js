import { describe, expect, it } from 'vitest';
import {
    OfflineCryptoError,
    createOfflineCrypto,
    offlineCryptoPanel,
    useOfflineCrypto,
} from '../../../static/js/clinic/offline-crypto-ui.js';

const ITER = 1000; // low cost for tests; the production default is 600000
const PASS = 'correct horse battery';
const TOKEN = 'session-token-SECRET-123';
const alice = { userId: 'u1', profile: 'managed_web_offline' };
const grant = (over = {}) => ({
    available: true, version: 1, account_version: 'av1', browser_id: 'b1',
    quota_bytes: 1024, key_policy: 'encrypted_user_unlock', ...over,
});

function fakeClient(initial) {
    const state = { next: initial, calls: [] };
    return {
        state,
        request: async (path) => {
            state.calls.push(path);
            const n = state.next;
            return n.outcome ? n : { outcome: 'ok', data: n };
        },
    };
}

async function ready(over) {
    const client = fakeClient(grant(over));
    const c = createOfflineCrypto({ client, iterations: ITER });
    await c.refreshAuthorization();
    return { client, c };
}

const bind = (over = {}) => ({ ...alice, dictationId: 'd1', sequence: 0, ...over });
const code = async (p) => {
    try { await p; } catch (e) { expect(e).toBeInstanceOf(OfflineCryptoError); return e.code; }
    return null;
};

describe('offline-crypto-ui', () => {
    it('round-trips a record and never exposes plaintext or tokens', async () => {
        const { c } = await ready();
        const wrapped = await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { text: 'Befund', t: TOKEN.length });
        expect(JSON.stringify(rec)).not.toContain('Befund');
        expect(JSON.stringify([wrapped, rec])).not.toContain(TOKEN);
        expect(await c.decryptRecord(bind(), rec)).toEqual({ text: 'Befund', t: TOKEN.length });
        const again = await c.encryptRecord(bind(), { text: 'Befund' });
        expect(again.iv).not.toBe(rec.iv);
    });

    it('unlocks a persisted wrapped key after lock; wrong passphrase fails', async () => {
        const { c } = await ready();
        const wrapped = await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        c.lock();
        expect(c.unlocked).toBe(false);
        expect(await code(c.decryptRecord(bind(), rec))).toBe('LOCKED');
        expect(await code(c.unlock('wrong passphrase', wrapped, alice))).toBe('UNLOCK_FAILED');
        await c.unlock(PASS, wrapped, alice);
        expect(await c.decryptRecord(bind(), rec)).toEqual({ a: 1 });
    });

    it('rejects tampered ciphertext', async () => {
        const { c } = await ready();
        await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        const bytes = Uint8Array.from(atob(rec.ct), (ch) => ch.charCodeAt(0));
        bytes[0] ^= 1;
        const bad = { ...rec, ct: btoa(String.fromCharCode(...bytes)) };
        expect(await code(c.decryptRecord(bind(), bad))).toBe('DECRYPT_FAILED');
    });

    it('binds dictation and sequence', async () => {
        const { c } = await ready();
        await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        expect(await code(c.decryptRecord(bind({ dictationId: 'd2' }), rec))).toBe('DECRYPT_FAILED');
        expect(await code(c.decryptRecord(bind({ sequence: 1 }), rec))).toBe('DECRYPT_FAILED');
        expect(await code(c.encryptRecord(bind({ sequence: -1 }), {}))).toBe('INVALID_BINDING');
    });

    it('refuses another account or profile', async () => {
        const { c } = await ready();
        const wrapped = await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        expect(await code(c.decryptRecord(bind({ userId: 'u2' }), rec))).toBe('FOREIGN_ACCOUNT');
        c.lock();
        expect(await code(c.unlock(PASS, wrapped, { ...alice, userId: 'u2' }))).toBe('UNLOCK_FAILED');
        const other = await ready({ account_version: 'av2' });
        expect(await code(other.c.unlock(PASS, wrapped, alice))).toBe('STALE_VERSION');
    });

    it('locks when authorization is revoked or unavailable', async () => {
        const { client, c } = await ready();
        await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        client.state.next = { available: false, message: 'x' };
        expect(await c.refreshAuthorization()).toMatchObject({ available: false, code: 'REVOKED' });
        expect(c.unlocked).toBe(false);
        expect(await code(c.decryptRecord(bind(), rec))).toBe('UNAUTHORIZED');
        client.state.next = { outcome: 'network', code: 'NETWORK' };
        expect(await c.refreshAuthorization()).toMatchObject({ available: false, code: 'network' });
        expect(await code(c.setup(PASS, alice))).toBe('UNAUTHORIZED');
    });

    it('treats a new grant version as stale and keeps the old record unreadable', async () => {
        const { client, c } = await ready();
        const wrapped = await c.setup(PASS, alice);
        const rec = await c.encryptRecord(bind(), { a: 1 });
        client.state.next = grant({ version: 2 });
        await c.refreshAuthorization();
        expect(c.unlocked).toBe(false);
        expect(await code(c.unlock(PASS, wrapped, alice))).toBe('STALE_VERSION');
        expect(await code(c.decryptRecord(bind(), rec))).toBe('LOCKED');
    });

    it('retry after a failed refresh recovers once the grant returns', async () => {
        const client = fakeClient({ outcome: 'network', code: 'NETWORK' });
        const c = createOfflineCrypto({ client, iterations: ITER });
        expect((await c.refreshAuthorization()).available).toBe(false);
        client.state.next = grant();
        expect((await c.refreshAuthorization()).available).toBe(true);
        await c.setup(PASS, alice);
        expect(c.unlocked).toBe(true);
    });

    it('rejects a weak passphrase and a non-matching key policy', async () => {
        const { c } = await ready();
        expect(await code(c.setup('short', alice))).toBe('WEAK_PASSPHRASE');
        const client = fakeClient(grant({ key_policy: 'other' }));
        const d = createOfflineCrypto({ client, iterations: ITER });
        expect((await d.refreshAuthorization()).available).toBe(false);
    });

    it('composable and component expose labels, keyboard handlers and token surfaces', async () => {
        const { c } = await ready();
        const panel = useOfflineCrypto(c, alice);
        expect(panel.view().surface.background).toMatch(/^var\(--/);
        panel.s.passphrase = PASS;
        const wrapped = await panel.submit(null);
        expect(wrapped.key).toBeTruthy();
        expect(panel.s.state).toBe('unlocked');
        expect(panel.s.passphrase).toBe('');
        panel.lock();
        expect(panel.s.state).toBe('locked');
        panel.s.passphrase = 'wrong passphrase';
        await panel.submit(wrapped);
        expect(panel.s.state).toBe('failed');
        const comp = offlineCryptoPanel(panel, () => wrapped);
        expect(comp.template).toContain('<label for="offline-crypto-pass">');
        expect(comp.template).toContain('@keydown.enter.prevent="submit"');
        expect(comp.template).toContain('@keydown.esc="lock"');
        expect(comp.template).toContain('aria-live="polite"');
        expect(comp.template).not.toMatch(/#[0-9a-f]{3,6}\b/i);
    });

    it('unauthorized panel refuses to submit', async () => {
        const client = fakeClient({ available: false, message: 'x' });
        const c = createOfflineCrypto({ client, iterations: ITER });
        const panel = useOfflineCrypto(c, alice);
        await panel.refresh();
        expect(panel.s.state).toBe('unauthorized');
        panel.s.passphrase = PASS;
        expect(await panel.submit(null)).toBeNull();
        expect(c.unlocked).toBe(false);
    });
});
