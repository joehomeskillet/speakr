import { describe, expect, it, vi } from 'vitest';
import {
    createVoiceConsentController, STATEMENT_TEXT, STATEMENT_VERSION,
} from '../../../static/js/clinic/voice-consent-ui.js';

function ok(data) {
    return { outcome: 'ok', status: 200, data, message: '' };
}
function bad(outcome, code, status) {
    return { outcome, status, code, message: 'msg ' + outcome };
}

function makeClient(routes) {
    const calls = [];
    return {
        calls,
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ path, method, body: options && options.body, key: options && options.idempotencyKey });
            const handler = routes[method + ' ' + path];
            return handler ? handler(options) : bad('UNKNOWN', 'UNKNOWN', null);
        }),
    };
}

const NONE = { state: 'none', latest_consent_id: 0, embedding_count: 0, granted_at: null, revoked_at: null, history: [] };
const GRANTED = {
    state: 'granted', latest_consent_id: 1, embedding_count: 3, granted_at: '2026-11-19T08:00:00', revoked_at: null,
    history: [{ id: 1, status: 'granted', statement_hash: 'h', purpose: 'p', created_at: '2026-11-19T08:00:00' }],
};
const REVOKED = {
    state: 'revoked', latest_consent_id: 2, embedding_count: 0, granted_at: '2026-11-19T08:00:00',
    revoked_at: '2026-11-19T09:00:00',
    history: GRANTED.history.concat([{ id: 2, status: 'revoked', statement_hash: null, purpose: 'p', created_at: '2026-11-19T09:00:00' }]),
};

describe('voice consent ui', () => {
    it('shows the versioned statement and grants with the expected id (positive)', async () => {
        let current = NONE;
        const client = makeClient({
            'GET /voice-consent': () => ok(current),
            'POST /voice-consent/grant': () => { current = GRANTED; return ok({ state: 'granted', latest_consent_id: 1, embedding_count: 0 }); },
        });
        const c = createVoiceConsentController({ client, newKey: () => 'key-00000001' });
        expect(await c.load()).toBe(true);
        let v = c.view();
        expect(v.statement).toMatchObject({ version: STATEMENT_VERSION, text: STATEMENT_TEXT });
        expect(v.status.label).toBe('Keine Einwilligung erteilt');
        expect(v.canGrant).toBe(true);
        expect(v.canRevoke).toBe(false);
        expect(await c.grant()).toBe(true);
        const post = client.calls.find((x) => x.method === 'POST');
        expect(post.body).toEqual({ expected_consent_id: 0, statement_text: STATEMENT_TEXT });
        expect(post.key).toBe('key-00000001');
        expect(Object.keys(post.body)).not.toContain('user_id');
        v = c.view();
        expect(v.status.value).toBe('granted');
        expect(v.labels.grant).toBe('Einwilligen');
        expect(c.state.notice).toBe('Einwilligung gespeichert.');
        expect(v.a11y.status.role).toBe('status');
    });

    it('needs a confirmation step to revoke and clears suggestions and enrollment', async () => {
        let current = GRANTED;
        const client = makeClient({
            'GET /voice-consent': () => ok(current),
            'POST /voice-consent/revoke': () => { current = REVOKED; return ok({ state: 'revoked', latest_consent_id: 2, embedding_count: 0 }); },
        });
        const c = createVoiceConsentController({ client });
        await c.load();
        expect(c.setSuggestions([{ name: 'A' }])).toBe(true);
        expect(await c.confirmRevoke()).toBe(false);
        expect(client.calls.some((x) => x.method === 'POST')).toBe(false);
        expect(c.requestRevoke()).toBe(true);
        expect(c.view().a11y.confirm.role).toBe('alertdialog');
        expect(await c.confirmRevoke()).toBe(true);
        expect(client.calls.find((x) => x.method === 'POST').body).toEqual({ expected_consent_id: 1 });
        const v = c.view();
        expect(v.status.value).toBe('revoked');
        expect(v.embeddingCount).toBe(0);
        expect(v.suggestionCount).toBe(0);
        expect(v.confirmingRevoke).toBe(false);
        expect(c.setSuggestions([{ name: 'A' }])).toBe(false);
        expect(c.state.notice).toContain('widerrufen');
        expect(JSON.stringify(v)).not.toMatch(/embedding_bytes|patient/i);
    });

    it('cancels the revoke confirmation with Escape', async () => {
        const c = createVoiceConsentController({ client: makeClient({ 'GET /voice-consent': () => ok(GRANTED) }) });
        await c.load();
        c.requestRevoke();
        expect(c.onKeydown({ key: 'Escape' })).toBe(true);
        expect(c.state.confirmingRevoke).toBe(false);
        expect(c.onKeydown({ key: 'a' })).toBe(false);
    });

    it('reports unauthorized without retry and without state change', async () => {
        const client = makeClient({
            'GET /voice-consent': () => ok(NONE),
            'POST /voice-consent/grant': () => bad('permission', null, 403),
        });
        const c = createVoiceConsentController({ client });
        await c.load();
        expect(await c.grant()).toBe(false);
        expect(c.state.error).toMatchObject({ outcome: 'permission', retryable: false });
        expect(c.view().errorSurface.status).toBe('error');
        expect(c.state.consent.state).toBe('none');
    });

    it('reloads on a stale version conflict', async () => {
        let current = NONE;
        const client = makeClient({
            'GET /voice-consent': () => ok(current),
            'POST /voice-consent/grant': () => { current = GRANTED; return bad('conflict', null, 409); },
        });
        const c = createVoiceConsentController({ client });
        await c.load();
        expect(await c.grant()).toBe(false);
        expect(c.state.consent.state).toBe('granted');
        expect(c.state.consent.latestId).toBe(1);
        expect(c.state.error).toBe(null);
        expect(c.state.notice).toContain('veraltet');
        expect(c.view().canGrant).toBe(false);
    });

    it('retries after a network failure with the same idempotency key', async () => {
        let fail = true;
        let n = 0;
        const client = makeClient({
            'GET /voice-consent': () => ok(NONE),
            'POST /voice-consent/grant': () => {
                if (fail) { fail = false; return bad('network', 'NETWORK', null); }
                return ok(GRANTED);
            },
        });
        const c = createVoiceConsentController({ client, newKey: () => 'key-' + (++n) + '-000000' });
        await c.load();
        expect(await c.grant()).toBe(false);
        expect(c.state.error.retryable).toBe(true);
        expect(await c.grant()).toBe(true);
        const posts = client.calls.filter((x) => x.method === 'POST');
        expect(posts[0].key).toBe(posts[1].key);
        expect(c.state.consent.state).toBe('granted');
    });

    it('drops all state on account switch and stores nothing in the browser', async () => {
        const writes = [];
        const spy = { setItem: (...a) => writes.push(a), getItem: () => null, removeItem: () => {}, clear: () => {} };
        global.localStorage = spy;
        global.sessionStorage = spy;
        let current = GRANTED;
        const client = makeClient({ 'GET /voice-consent': () => ok(current) });
        const c = createVoiceConsentController({ client });
        await c.load();
        c.setSuggestions([{ name: 'A' }]);
        current = NONE;
        await c.switchContext({ accountId: 'u2' });
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: 'u2', dictationId: undefined });
        expect(c.state.suggestions).toEqual([]);
        expect(c.state.consent.state).toBe('none');
        expect(writes).toEqual([]);
    });

    it('uses the injected translation', async () => {
        const t = (k) => (k === 'clinic.voiceConsent.grant' ? 'Zustimmen' : k);
        const c = createVoiceConsentController({ client: makeClient({ 'GET /voice-consent': () => ok(NONE) }), t });
        await c.load();
        expect(c.view().labels.grant).toBe('Zustimmen');
        expect(c.view().labels.cancel).toBe('Abbrechen');
    });
});
