import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClinicApiClient } from '../../../static/js/clinic/api-client.js';

const CORRELATION = '11111111-1111-4111-8111-111111111111';
const LEAK = 'Patientin Muster transcript secret-token';

function http({ status, body, ok }) {
    const text = typeof body === 'string' ? body : JSON.stringify(body);
    return {
        ok: ok ?? (status >= 200 && status < 300),
        status: status,
        text: async () => text,
    };
}

function problem(status, code, extra) {
    return http({
        status: status,
        body: Object.assign({
            type: 'about:blank',
            title: 'Problem',
            status: status,
            code: code,
            correlation_id: CORRELATION,
            detail: LEAK,
        }, extra || {}),
    });
}

describe('clinic api client', () => {
    let fetchMock;
    let writes;
    let originalWindow;
    let originalDocument;
    let originalFetch;

    beforeEach(() => {
        originalWindow = global.window;
        originalDocument = global.document;
        originalFetch = global.fetch;
        writes = [];
        const storage = () => ({
            setItem: (...args) => writes.push(args),
            getItem: () => null,
            removeItem: () => writes.push(['remove']),
            clear: () => writes.push(['clear']),
        });
        global.localStorage = storage();
        global.sessionStorage = storage();
        global.indexedDB = { open: () => { writes.push(['idb']); return {}; } };
        global.document = { querySelector: vi.fn(() => null), cookie: '' };
        fetchMock = vi.fn();
        global.fetch = fetchMock;
        global.window = {
            csrfManager: { refreshToken: vi.fn(async () => 'csrf-one') },
            originalFetch: undefined,
        };
    });

    afterEach(() => {
        global.window = originalWindow;
        global.document = originalDocument;
        global.fetch = originalFetch;
        vi.restoreAllMocks();
    });

    it('loads a clinic list with the browser session and keeps the payload in memory', async () => {
        fetchMock.mockResolvedValue(http({ status: 200, body: { items: [{ id: 'd1', note: LEAK }] } }));
        const client = createClinicApiClient();
        const row = await client.request('/dictations');

        expect(row.outcome).toBe('ok');
        expect(row.data).toEqual({ items: [{ id: 'd1', note: LEAK }] });
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith('/api/clinic/v1/dictations', expect.objectContaining({
            method: 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
        }));
        expect(fetchMock.mock.calls[0][1].headers['X-CSRFToken']).toBeUndefined();
        expect(writes).toEqual([]);
    });

    it('posts a parity command with the existing CSRF transport', async () => {
        fetchMock.mockResolvedValue(http({
            status: 200,
            body: { request_id: CORRELATION, command_id: CORRELATION, status: 'committed', revision: 2 },
        }));
        const client = createClinicApiClient();
        const row = await client.command(
            { command: 'save_filter', request_id: CORRELATION, expected_revision: 1, payload: { name: 'Offen' } },
            { idempotencyKey: 'idem-key-1', ifMatch: '"rev-1"' },
        );

        expect(row.outcome).toBe('ok');
        expect(row.data.revision).toBe(2);
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe('/api/clinic/v1/parity/commands');
        expect(init.method).toBe('POST');
        expect(init.credentials).toBe('same-origin');
        expect(init.headers['X-CSRFToken']).toBe('csrf-one');
        expect(init.headers['X-CSRF-Token']).toBe('csrf-one');
        expect(init.headers['Idempotency-Key']).toBe('idem-key-1');
        expect(init.headers['If-Match']).toBe('"rev-1"');
        expect(JSON.parse(init.body).command).toBe('save_filter');
        expect(writes).toEqual([]);
    });

    it('rejects a parity command without an idempotency key before sending', async () => {
        const client = createClinicApiClient();
        const row = await client.command({ command: 'save_filter' }, {});
        expect(row.outcome).toBe('validation');
        expect(row.code).toBe('INVALID_PAYLOAD');
        expect(row.message).toBe('Die Eingabe ist ungültig.');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('does not call other origins', async () => {
        const client = createClinicApiClient();
        const row = await client.request('https://example.test/api/clinic/v1/dictations');
        expect(row.outcome).toBe('validation');
        expect(fetchMock).not.toHaveBeenCalled();
    });

    it('reports unauthorized as permission and redacts the body', async () => {
        fetchMock.mockResolvedValue(problem(401, 'UNAUTHORIZED'));
        const client = createClinicApiClient();
        const row = await client.request('/api/clinic/v1/dictations/d1');
        expect(row).toEqual({
            outcome: 'permission',
            status: 401,
            code: 'UNAUTHORIZED',
            correlationId: CORRELATION,
            message: 'Keine Berechtigung für diese Aktion.',
        });
        expect(JSON.stringify(row)).not.toContain('Muster');
        expect(writes).toEqual([]);
    });

    it('reports a stale revision as conflict', async () => {
        fetchMock.mockResolvedValue(problem(409, 'REVISION_CONFLICT'));
        const client = createClinicApiClient();
        const row = await client.request('/api/clinic/v1/dictations/d1', {
            method: 'POST',
            body: { title: 'Visite' },
            ifMatch: '"old"',
        });
        expect(row.outcome).toBe('conflict');
        expect(row.code).toBe('REVISION_CONFLICT');
        expect(row.message).toBe('Der Stand ist veraltet.');
        expect(JSON.stringify(row)).not.toContain(LEAK);
    });

    it('reports a missing precondition as conflict', async () => {
        fetchMock.mockResolvedValue(http({ status: 412, body: '<html>' + LEAK + '</html>' }));
        const client = createClinicApiClient();
        const row = await client.request('/dictations/d1', { method: 'POST', body: { title: 'Visite' } });
        expect(row.outcome).toBe('conflict');
        expect(row.code).toBeNull();
        expect(JSON.stringify(row)).not.toContain('Muster');
    });

    it('reports invalid payloads as validation', async () => {
        fetchMock.mockResolvedValue(problem(422, 'INVALID_PAYLOAD'));
        const client = createClinicApiClient();
        const row = await client.request('/dictations', { method: 'POST', body: { title: '' } });
        expect(row.outcome).toBe('validation');
        expect(row.code).toBe('INVALID_PAYLOAD');
        expect(JSON.stringify(row)).not.toContain('secret-token');
    });

    it('retries once after a CSRF rejection and then returns the payload', async () => {
        global.window.csrfManager.refreshToken
            .mockResolvedValueOnce('stale-token')
            .mockResolvedValueOnce('fresh-token');
        fetchMock
            .mockResolvedValueOnce(http({
                status: 403,
                body: { csrf_retryable: true, error: 'csrf expired', detail: LEAK },
            }))
            .mockResolvedValueOnce(http({ status: 200, body: { id: 'd1' } }));
        const client = createClinicApiClient();
        const row = await client.request('/dictations', { method: 'POST', body: { title: 'Visite' } });

        expect(row.outcome).toBe('ok');
        expect(row.data).toEqual({ id: 'd1' });
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(fetchMock.mock.calls[0][1].headers['X-CSRFToken']).toBe('stale-token');
        expect(fetchMock.mock.calls[1][1].headers['X-CSRFToken']).toBe('fresh-token');
        expect(writes).toEqual([]);
    });

    it('stops after a second CSRF rejection without echoing the page', async () => {
        fetchMock.mockResolvedValue(http({ status: 403, body: '<html>csrf ' + LEAK + '</html>' }));
        const client = createClinicApiClient();
        const row = await client.request('/dictations', { method: 'POST', body: { title: 'Visite' } });
        expect(row.outcome).toBe('permission');
        expect(row.code).toBe('FORBIDDEN');
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(JSON.stringify(row)).not.toContain('Muster');
    });

    it('retries one failed GET and reports network when both attempts fail', async () => {
        fetchMock.mockRejectedValue(new TypeError('offline'));
        const client = createClinicApiClient();
        const row = await client.request('/dictations');
        expect(row.outcome).toBe('network');
        expect(row.code).toBe('NETWORK');
        expect(row.message).toBe('Die Verbindung ist fehlgeschlagen.');
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(writes).toEqual([]);
    });

    it('does not retry an unsafe request after a network failure', async () => {
        fetchMock.mockRejectedValueOnce(new TypeError('offline'))
            .mockResolvedValueOnce(http({ status: 200, body: { id: 'should-not-run' } }));
        const client = createClinicApiClient();
        const row = await client.command({ command: 'save_filter' }, { idempotencyKey: 'idem-key-1' });
        expect(row.outcome).toBe('network');
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('aborts a stalled recordings request at its deadline', async () => {
        vi.useFakeTimers();
        try {
            fetchMock.mockImplementation((url, { signal }) => new Promise((resolve, reject) => {
                signal.addEventListener('abort', () => reject(signal.reason), { once: true });
            }));
            const client = createClinicApiClient();
            const pending = client.request('/recordings', { timeoutMs: 12000 });
            await vi.advanceTimersByTimeAsync(12000);
            expect(await pending).toMatchObject({ outcome: 'network', message: 'Die Verbindung ist fehlgeschlagen.' });
            expect(fetchMock).toHaveBeenCalledTimes(1);
        } finally {
            vi.useRealTimers();
        }
    });

    it('invalidates the lists after an uncertain send without retrying the send', async () => {
        fetchMock.mockRejectedValueOnce(new TypeError('response lost'));
        const client = createClinicApiClient();
        const response = await client.request('/dictations/d1/send', { method: 'POST', body: {} });
        expect(response.outcome).toBe('network');
        expect(client.dictationChanges).toBe(1);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('returns UNKNOWN for a capability refusal and drops unsafe codes', async () => {
        fetchMock.mockResolvedValue(problem(422, 'CAPABILITY_UNAVAILABLE', { code: 'CAPABILITY_UNAVAILABLE' }));
        const client = createClinicApiClient();
        const row = await client.request('/dictations/d1');
        expect(row.outcome).toBe('UNKNOWN');
        expect(row.code).toBe('CAPABILITY_UNAVAILABLE');
        expect(row.message).toBe('Das Ergebnis ist unbekannt.');

        fetchMock.mockResolvedValue(problem(500, LEAK));
        const unsafe = await client.request('/dictations/d1');
        expect(unsafe.outcome).toBe('network');
        expect(unsafe.code).toBeNull();
        expect(JSON.stringify(unsafe)).not.toContain('Muster');
    });

    it('cancels in-flight requests when the dictation changes', async () => {
        let signal;
        fetchMock.mockImplementation((url, init) => new Promise((resolve, reject) => {
            signal = init.signal;
            init.signal.addEventListener('abort', () => {
                const error = new Error('aborted');
                error.name = 'AbortError';
                reject(error);
            });
            setTimeout(() => resolve(http({ status: 200, body: { note: LEAK } })), 50);
        }));
        const client = createClinicApiClient();
        client.switchContext({ accountId: 'acct-a', dictationId: 'd1' });
        const pending = client.request('/dictations/d1');
        client.switchContext({ accountId: 'acct-a', dictationId: 'd2' });
        const row = await pending;
        expect(signal.aborted).toBe(true);
        expect(row.outcome).toBe('cancelled');
        expect(row.code).toBe('CANCELLED');
        expect(row.data).toBeUndefined();
        expect(row.message).toBe('Die Anfrage wurde abgebrochen.');
        expect(JSON.stringify(row)).not.toContain('Muster');
    });

    it('drops a late payload after an account switch', async () => {
        let finish;
        fetchMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
        const client = createClinicApiClient();
        client.switchContext({ accountId: 'acct-a', dictationId: 'd1' });
        const pending = client.request('/dictations/d1');
        client.switchContext({ accountId: 'acct-b', dictationId: 'd1' });
        finish(http({ status: 200, body: { note: LEAK, token: 'identity-token' } }));
        const row = await pending;
        expect(row.outcome).toBe('cancelled');
        expect(JSON.stringify(row)).not.toContain('identity-token');
        expect(writes).toEqual([]);
    });

    it('leaves an in-flight request running when the context is unchanged', async () => {
        let signal;
        fetchMock.mockImplementation((url, init) => new Promise((resolve) => {
            signal = init.signal;
            resolve(http({ status: 200, body: { id: 'd1' } }));
        }));
        const client = createClinicApiClient();
        client.switchContext({ accountId: 'acct-a', dictationId: 'd1' });
        const pending = client.request('/dictations/d1');
        client.switchContext({ accountId: 'acct-a', dictationId: 'd1' });
        const row = await pending;
        expect(signal.aborted).toBe(false);
        expect(row.outcome).toBe('ok');
    });

    it('does not name browser persistence APIs', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/api-client.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
    });
});
