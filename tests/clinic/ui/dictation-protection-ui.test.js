import { describe, expect, it, vi } from 'vitest';
import {
    DictationProtectionComponent,
    TEXT,
    createDictationProtectionModel,
} from '../../../static/js/clinic/dictation-protection-ui.js';

function client(...results) {
    const fn = vi.fn();
    for (const r of results) fn.mockResolvedValueOnce(r);
    fn.mockResolvedValue({ outcome: 'ok', data: { protected: true, version: 9, access: false } });
    return { request: fn };
}

function loaded(c, isProtected) {
    const m = createDictationProtectionModel(c, 'd 1');
    m.state.protected = isProtected;
    m.state.version = 4;
    return m;
}

describe('dictation protection ui', () => {
    it('requires explicit confirmation before enabling and sends nothing without it', async () => {
        const c = client();
        const m = loaded(c, false);
        m.state.secret = 'geheim';
        await m.enable();
        expect(c.request).not.toHaveBeenCalled();
        expect(m.state.message).toBe(TEXT.needConfirm);
        expect(m.state.secret).toBe('geheim');
    });

    it('enables via PUT with version, clears the code and the confirmation', async () => {
        const c = client({ outcome: 'ok', status: 200, data: { protected: true, version: 5 } });
        const m = loaded(c, false);
        m.state.secret = 'geheim';
        m.state.confirmed = true;
        await m.enable();
        const [path, opts] = c.request.mock.calls[0];
        expect(path).toBe('/dictations/d%201/protection');
        expect(opts).toEqual({ method: 'PUT', body: { version: 4, secret: 'geheim' } });
        expect(m.state.protected).toBe(true);
        expect(m.state.version).toBe(5);
        expect(m.state.secret).toBe('');
        expect(m.state.confirmed).toBe(false);
    });

    it('disable needs confirmation and sends a null secret', async () => {
        const c = client({ outcome: 'ok', status: 200, data: { protected: false, version: 6 } });
        const m = loaded(c, true);
        await m.disable();
        expect(c.request).not.toHaveBeenCalled();
        m.state.confirmed = true;
        await m.disable();
        expect(c.request.mock.calls[0][1].body).toEqual({ version: 4, secret: null });
        expect(m.state.protected).toBe(false);
    });

    it('unlock stores only the grant expiry and clears the code', async () => {
        const c = client({ outcome: 'ok', status: 200, data: { unlocked: true, expires_at: '2026-11-19T10:10:00+00:00' } });
        const m = loaded(c, true);
        m.state.secret = 'geheim';
        await m.unlock();
        expect(c.request.mock.calls[0][0]).toBe('/dictations/d%201/unlock');
        expect(m.state.grantExpiresAt).toBe('2026-11-19T10:10:00+00:00');
        expect(m.state.secret).toBe('');
        expect(JSON.stringify(m.state)).not.toContain('geheim');
    });

    it('wrong code or no authorization (403) clears the code and says so', async () => {
        const m = loaded(client({ outcome: 'permission', status: 403 }), true);
        m.state.secret = 'falsch';
        await m.unlock();
        expect(m.state.status).toBe('denied');
        expect(m.state.message).toBe(TEXT.wrongCode);
        expect(m.state.secret).toBe('');
        expect(m.state.grantExpiresAt).toBeNull();
        expect(m.surface().tokens.border).toBe('--border-danger');
    });

    it('shows the existing lockout response (429)', async () => {
        const m = loaded(client({ outcome: 'network', status: 429 }), true);
        m.state.secret = 'x';
        await m.unlock();
        expect(m.state.status).toBe('locked');
        expect(m.state.message).toBe(TEXT.locked);
        expect(m.state.secret).toBe('');
    });

    it('reloads the version on a stale-version conflict', async () => {
        const c = client(
            { outcome: 'conflict', status: 409 },
            { outcome: 'ok', data: { protected: true, version: 12, access: true } },
        );
        const m = loaded(c, false);
        m.state.secret = 'geheim';
        m.state.confirmed = true;
        await m.enable();
        await Promise.resolve();
        await Promise.resolve();
        expect(m.state.status).toBe('conflict');
        expect(m.state.version).toBe(12);
        expect(m.state.secret).toBe('');
    });

    it('retries after a network failure and then succeeds', async () => {
        const c = client(
            { outcome: 'network', status: null },
            { outcome: 'ok', status: 200, data: { unlocked: true, expires_at: 'T' } },
        );
        const m = loaded(c, true);
        m.state.secret = 'a';
        await m.unlock();
        expect(m.state.status).toBe('retry');
        m.state.secret = 'a';
        await m.unlock();
        expect(m.state.status).toBe('done');
        expect(c.request).toHaveBeenCalledTimes(2);
    });

    it('load reads the protection state', async () => {
        const c = client({ outcome: 'ok', data: { protected: true, version: 2, access: false } });
        const m = createDictationProtectionModel(c, 'd1');
        await m.load();
        expect(m.state.protected).toBe(true);
        expect(m.state.version).toBe(2);
    });

    it('stores nothing in browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const m = loaded(client({ outcome: 'ok', data: { unlocked: true } }), true);
        m.state.secret = 'a';
        await m.unlock();
        expect(writes).toEqual([]);
    });

    it('component: labelled, keyboard-operable, not an authorization substitute', () => {
        const Vue = { reactive: (o) => o, computed: (fn) => ({ get value() { return fn(); } }) };
        const comp = DictationProtectionComponent(Vue);
        expect(comp.template).toContain('type="submit"');
        expect(comp.template).toContain('type="password"');
        expect(comp.template).toContain('role="status"');
        expect(comp.template).toContain('<label>');
        expect(comp.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(TEXT.notice).toContain('ersetzt keine Zugriffsberechtigung');
        const ctx = comp.setup({ client: client(), dictationId: 'd1' });
        expect(ctx.text).toBe(TEXT);
    });
});
