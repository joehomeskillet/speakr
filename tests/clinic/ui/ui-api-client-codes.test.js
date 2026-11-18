import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClinicApiClient } from '../../../static/js/clinic/api-client.js';

const CORRELATION = '11111111-1111-4111-8111-111111111111';

function problem(status, code) {
    return {
        ok: false,
        status: status,
        text: async () => JSON.stringify({
            type: 'about:blank', title: 'Problem', status: status, code: code,
            correlation_id: CORRELATION, detail: 'Patientin Muster secret',
        }),
    };
}

describe('clinic api client error codes', () => {
    let originalWindow;
    let originalDocument;
    let originalFetch;
    let fetchMock;

    beforeEach(() => {
        originalWindow = global.window;
        originalDocument = global.document;
        originalFetch = global.fetch;
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

    async function codeFor(status, code) {
        fetchMock.mockResolvedValue(problem(status, code));
        return createClinicApiClient().request('/dictations');
    }

    it.each(['missing_category', 'stale_version', 'stale_revision', 'forbidden', 'not_found'])(
        'passes lowercase code %s through',
        async (code) => {
            const row = await codeFor(409, code);
            expect(row.code).toBe(code);
            expect(row.status).toBe(409);
        },
    );

    it('keeps the status-based outcome for lowercase codes', async () => {
        const row = await codeFor(409, 'stale_version');
        expect(row.outcome).toBe('conflict');
    });

    it('still passes uppercase codes through', async () => {
        const row = await codeFor(403, 'POLICY_DENIED');
        expect(row.code).toBe('POLICY_DENIED');
        expect(row.outcome).toBe('permission');
    });

    it('accepts a 64 character lowercase code and rejects 65', async () => {
        const ok = 'a'.repeat(64);
        expect((await codeFor(400, ok)).code).toBe(ok);
        expect((await codeFor(400, 'a'.repeat(65))).code).toBeNull();
    });

    it.each([
        'has space',
        '<script>alert(1)</script>',
        '<b>x</b>',
        '1starts_with_digit',
        'Mixed_Case',
        'dash-ed',
        '',
        'Patientin Muster secret',
    ])('replaces invalid code %j by the generic value', async (code) => {
        const row = await codeFor(400, code);
        expect(row.code).toBeNull();
        expect(row.outcome).toBe('validation');
    });

    it('does not let prototype keys pick the outcome', async () => {
        const row = await codeFor(400, 'constructor');
        expect(row.code).toBe('constructor');
        expect(row.outcome).toBe('validation');
    });
});
