/**
 * Browser-session client for the clinic API and parity commands.
 *
 * Session cookies travel with credentials: 'same-origin'. CSRF uses the
 * existing helpers in static/js/modules/csrf.js (X-CSRFToken for the clinic
 * API and X-CSRF-Token for parity commands). Response bodies stay in the
 * returned object. Errors keep only outcome, status, a safe code and a
 * correlation id. Switching account or dictation aborts in-flight calls.
 */

import { getUploadCsrfToken, isCsrfRejection } from '../modules/csrf.js';

const ROOT = '/api/clinic/v1';
const PARITY_COMMANDS = ROOT + '/parity/commands';

const MESSAGES = {
    ok: '',
    validation: 'Die Eingabe ist ungültig.',
    conflict: 'Der Stand ist veraltet.',
    permission: 'Keine Berechtigung für diese Aktion.',
    network: 'Die Verbindung ist fehlgeschlagen.',
    UNKNOWN: 'Das Ergebnis ist unbekannt.',
    cancelled: 'Die Anfrage wurde abgebrochen.',
};

const CODE_OUTCOME = {
    UNAUTHORIZED: 'permission',
    FORBIDDEN: 'permission',
    POLICY_DENIED: 'permission',
    REVISION_CONFLICT: 'conflict',
    IDEMPOTENCY_CONFLICT: 'conflict',
    INVALID_PAYLOAD: 'validation',
    CAPABILITY_UNAVAILABLE: 'UNKNOWN',
    RATE_LIMITED: 'network',
    DEPENDENCY_UNAVAILABLE: 'network',
};

const SAFE_CODE = /^(?:[A-Z0-9_]{1,80}|[a-z][a-z0-9_]{0,63})$/;
const SAFE_CORRELATION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function result(outcome, fields) {
    const extra = fields || {};
    const row = {
        outcome: outcome,
        status: Number.isInteger(extra.status) ? extra.status : null,
        code: extra.code || null,
        correlationId: extra.correlationId || null,
        message: MESSAGES[outcome] || MESSAGES.UNKNOWN,
    };
    if (outcome === 'ok') row.data = extra.data === undefined ? null : extra.data;
    return row;
}

function clinicUrl(path) {
    if (typeof path !== 'string') return null;
    if (path.includes('://') || path.startsWith('//') || path.includes('..') || path.includes('\\')) return null;
    const full = path.startsWith(ROOT) ? path : ROOT + (path.startsWith('/') ? path : '/' + path);
    if (full !== ROOT && !full.startsWith(ROOT + '/')) return null;
    return full;
}

function safeCode(code) {
    return typeof code === 'string' && SAFE_CODE.test(code) ? code : null;
}

function safeCorrelation(value) {
    return typeof value === 'string' && SAFE_CORRELATION.test(value) ? value : null;
}

function outcomeFor(status, code) {
    if (code && Object.prototype.hasOwnProperty.call(CODE_OUTCOME, code)) return CODE_OUTCOME[code];
    if (status === 401 || status === 403) return 'permission';
    if (status === 409 || status === 412) return 'conflict';
    if (status === 400 || status === 415 || status === 422 || status === 428) return 'validation';
    if (status === 408 || status === 429 || (status >= 500 && status <= 599)) return 'network';
    return 'UNKNOWN';
}

function problemFields(text) {
    try {
        const data = JSON.parse(text || '');
        if (!data || typeof data !== 'object') return { code: null, correlationId: null };
        return {
            code: safeCode(data.code),
            correlationId: safeCorrelation(data.correlation_id),
        };
    } catch (e) {
        return { code: null, correlationId: null };
    }
}

function classify(response, text) {
    const status = response && Number.isInteger(response.status) ? response.status : null;
    if (text === null) return result('UNKNOWN', { status: status, code: 'UNKNOWN' });
    if (isCsrfRejection(status, text)) return result('permission', { status: status, code: 'FORBIDDEN' });
    if (response.ok) {
        if (!text) return result('ok', { status: status, data: null });
        try {
            return result('ok', { status: status, data: JSON.parse(text) });
        } catch (e) {
            return result('UNKNOWN', { status: status, code: 'UNKNOWN' });
        }
    }
    const fields = problemFields(text);
    const outcome = outcomeFor(status, fields.code);
    return result(outcome, {
        status: status,
        code: fields.code || (outcome === 'UNKNOWN' ? 'UNKNOWN' : null),
        correlationId: fields.correlationId,
    });
}

function browserFetch() {
    if (typeof window !== 'undefined' && typeof window.originalFetch === 'function') {
        return window.originalFetch;
    }
    return globalThis.fetch;
}

function headersFor(method, token, options) {
    const headers = { Accept: 'application/json, application/problem+json' };
    if (token) {
        headers['X-CSRFToken'] = token;
        headers['X-CSRF-Token'] = token;
    }
    if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
    if (options.ifMatch) headers['If-Match'] = options.ifMatch;
    if (options.caseView) headers['X-Clinic-Case-View'] = options.caseView;
    if (options.body !== undefined && method !== 'GET' && method !== 'HEAD') {
        headers['Content-Type'] = 'application/json';
    }
    return headers;
}

/**
 * @returns {{
 *   request: (path: string, options?: object) => Promise<object>,
 *   command: (body: object, options?: object) => Promise<object>,
 *   switchContext: (next: {accountId?: string|null, dictationId?: string|null}) => void,
 * }}
 */
export function createClinicApiClient() {
    let generation = 0;
    let dictationChanges = 0;
    let context = { accountId: null, dictationId: null };
    const inflight = new Set();
    function markDictationChanged() { dictationChanges += 1; }

    function switchContext(next) {
        const accountId = next && next.accountId != null ? String(next.accountId) : null;
        const dictationId = next && next.dictationId != null ? String(next.dictationId) : null;
        if (accountId === context.accountId && dictationId === context.dictationId) return;
        context = { accountId: accountId, dictationId: dictationId };
        generation += 1;
        for (const controller of inflight) controller.abort();
        inflight.clear();
    }

    async function exchange(url, init, idempotent, seen) {
        const attempts = idempotent ? 2 : 1;
        for (let i = 0; i < attempts; i += 1) {
            if (init.signal.aborted || seen !== generation) return { cancelled: true };
            try {
                return { response: await browserFetch()(url, init) };
            } catch (error) {
                if (error && error.name === 'AbortError') return { cancelled: true };
                if (i + 1 < attempts && seen === generation && !init.signal.aborted) continue;
                return { network: true };
            }
        }
        return { network: true };
    }

    async function readText(response) {
        if (!response || typeof response.text !== 'function') return null;
        try {
            return await response.text();
        } catch (e) {
            return null;
        }
    }

    async function request(path, options) {
        const opts = options || {};
        const url = clinicUrl(path);
        if (!url) return result('validation', { code: 'INVALID_PAYLOAD' });
        const method = String(opts.method || 'GET').toUpperCase();
        const seen = generation;
        const controller = new AbortController();
        inflight.add(controller);
        const unsafe = method !== 'GET' && method !== 'HEAD';
        const dictationRequest = url.startsWith(ROOT + '/dictations/');
        const sending = method === 'POST' && dictationRequest && url.endsWith('/send');
        const finish = (response) => {
            if ((unsafe && response.outcome === 'ok' && (dictationRequest || url === PARITY_COMMANDS))
                || (sending && ['network', 'UNKNOWN'].includes(response.outcome))) markDictationChanged();
            return response;
        };
        let timedOut = false;
        const timeout = opts.timeoutMs > 0 ? setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, opts.timeoutMs) : null;
        try {
            const token = unsafe ? await getUploadCsrfToken() : null;
            if (seen !== generation || controller.signal.aborted) return result('cancelled', { code: 'CANCELLED' });
            let body = unsafe ? opts.formData : undefined;
            if (unsafe && opts.body !== undefined) {
                try {
                    body = JSON.stringify(opts.body);
                } catch (e) {
                    return result('validation', { code: 'INVALID_PAYLOAD' });
                }
            }
            const init = {
                method: method,
                credentials: 'same-origin',
                cache: 'no-store',
                headers: headersFor(method, token, opts),
                signal: controller.signal,
            };
            if (body !== undefined) init.body = body;
            let sent = await exchange(url, init, !unsafe, seen);
            if (timedOut) return finish(result('network', { code: 'NETWORK' }));
            if (sent.cancelled) return result('cancelled', { code: 'CANCELLED' });
            if (sent.network) return finish(result('network', { code: 'NETWORK' }));
            if (seen !== generation) return result('cancelled', { code: 'CANCELLED' });
            let text = await readText(sent.response);
            if (seen !== generation) return result('cancelled', { code: 'CANCELLED' });
            if (unsafe && text !== null && isCsrfRejection(sent.response.status, text)) {
                const refreshed = await getUploadCsrfToken();
                if (seen !== generation || controller.signal.aborted) return result('cancelled', { code: 'CANCELLED' });
                const retryInit = {
                    method: init.method,
                    credentials: init.credentials,
                    cache: init.cache,
                    headers: headersFor(method, refreshed, opts),
                    signal: init.signal,
                };
                if (init.body !== undefined) retryInit.body = init.body;
                sent = await exchange(url, retryInit, false, seen);
                if (sent.cancelled) return result('cancelled', { code: 'CANCELLED' });
                if (sent.network) return finish(result('network', { code: 'NETWORK' }));
                if (seen !== generation) return result('cancelled', { code: 'CANCELLED' });
                text = await readText(sent.response);
                if (seen !== generation) return result('cancelled', { code: 'CANCELLED' });
            }
            if (timedOut) return finish(result('network', { code: 'NETWORK' }));
            return finish(classify(sent.response, text));
        } finally {
            clearTimeout(timeout);
            inflight.delete(controller);
        }
    }

    function command(body, options) {
        const opts = options || {};
        const key = opts.idempotencyKey;
        if (typeof key !== 'string' || key.length < 8 || key.length > 128) {
            return Promise.resolve(result('validation', { code: 'INVALID_PAYLOAD' }));
        }
        return request(PARITY_COMMANDS, {
            method: 'POST',
            body: body,
            idempotencyKey: key,
            ifMatch: opts.ifMatch,
        });
    }

    return {
        request: request,
        command: command,
        switchContext: switchContext,
        markDictationChanged,
        get dictationChanges() { return dictationChanges; },
    };
}
