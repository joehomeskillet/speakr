/**
 * Background job overview with actionable failures and controlled retries.
 *
 * Data comes from GET /jobs/health (redacted: counts, stuck ids, at most ten
 * failures) through the clinic api client. A retry is offered only when the
 * caller is authorized, the job carries a numeric version and its state is
 * not UNKNOWN. UNKNOWN is routed to reconciliation and never retried blindly.
 * Cancel has no endpoint in the jobs contract, so it is never offered.
 * Everything stays in memory: no storage, no clinical data.
 *
 * Native tray/autostart parity (WG-OPS-004) stays open and is shown as such.
 */

import { surfaceFor } from './tokens.js';

export const TRAY_AUTOSTART_PARITY = Object.freeze({ gap: 'WG-OPS-004', status: 'open' });

export const LABELS = Object.freeze({
    title: 'Verarbeitung',
    progress: 'Fortschritt',
    queued: 'Wartend',
    running: 'In Arbeit',
    failed: 'Fehlgeschlagen',
    stuck: 'Hängend',
    failures: 'Fehlgeschlagene Aufträge',
    none: 'Keine fehlgeschlagenen Aufträge.',
    retry: 'Erneut versuchen',
    reconcile: 'Abgleich prüfen',
    reload: 'Aktualisieren',
    loading: 'Wird geladen …',
    correlation: 'Referenz',
    retried: 'Der Auftrag wurde erneut eingereiht.',
    reconcileHint: 'Das Ergebnis ist unbekannt. Bitte im Abgleich prüfen.',
    staleVersion: 'Der Stand ist veraltet. Die Liste wurde neu geladen.',
    parity: 'Nativer Tray-/Autostart-Betrieb: offen (WG-OPS-004). Der Browser ersetzt ihn nicht.',
});

const MAX_FAILURES = 10;
const MAX_TEXT = 200;
const SAFE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function text(value) {
    return typeof value === 'string' ? value.slice(0, MAX_TEXT) : '';
}

function isUnknown(failure) {
    return String(failure.errorClass).toUpperCase() === 'UNKNOWN' || String(failure.state).toUpperCase() === 'UNKNOWN';
}

function normalizeFailure(row) {
    if (!row || !Number.isInteger(row.id) || row.id < 1) return null;
    const failure = {
        id: row.id,
        jobType: text(row.job_type),
        errorClass: text(row.error_class),
        message: text(row.message),
        finishedAt: typeof row.finished_at === 'string' ? row.finished_at : null,
        state: text(row.state),
        version: Number.isInteger(row.version) && row.version >= 0 ? row.version : null,
    };
    failure.unknown = isUnknown(failure);
    return failure;
}

export function summarizeProgress(counts) {
    const c = counts && typeof counts === 'object' ? counts : {};
    const n = (key) => (Number.isInteger(c[key]) && c[key] > 0 ? c[key] : 0);
    const queued = n('queued') + n('pending');
    const running = n('processing') + n('running');
    const completed = n('completed');
    const failed = n('failed');
    const total = queued + running + completed + failed;
    return {
        queued, running, completed, failed, total,
        percent: total ? Math.round((completed / total) * 100) : 0,
    };
}

function defaultRequestId() {
    return globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function'
        ? globalThis.crypto.randomUUID()
        : null;
}

/**
 * @param {object} options
 * @param {{request: Function}} options.api  client from createClinicApiClient
 * @param {boolean} [options.canRetry]       caller holds the operations role
 * @param {Function} [options.onReconcile]   called with the job id for UNKNOWN
 * @param {Function} [options.newRequestId]  uuid factory (injectable)
 * @param {object} [options.Vue]             optional, uses Vue.ref when given
 */
export function useJobs(options) {
    const opts = options || {};
    const api = opts.api;
    const newRequestId = opts.newRequestId || defaultRequestId;
    const makeRef = opts.Vue && opts.Vue.ref ? opts.Vue.ref : (value) => ({ value });
    const state = makeRef({
        loading: false,
        busy: null,
        health: null,
        failures: [],
        stuckIds: [],
        progress: summarizeProgress(null),
        error: null,
        notice: '',
        authorized: opts.canRetry === true,
    });
    const pendingKeys = new Map();

    function set(patch) {
        state.value = Object.assign({}, state.value, patch);
    }

    function fail(res) {
        return { outcome: res.outcome, code: res.code, correlationId: res.correlationId, message: res.message };
    }

    async function load() {
        set({ loading: true, error: null });
        const res = await api.request('/jobs/health');
        if (res.outcome !== 'ok') {
            // A permission refusal also clears stale rows and the retry offer.
            const denied = res.outcome === 'permission';
            set(Object.assign({ loading: false, error: fail(res) },
                denied ? { failures: [], stuckIds: [], authorized: false, health: null } : {}));
            return res;
        }
        const data = res.data || {};
        const failures = (Array.isArray(data.recent_failures) ? data.recent_failures : [])
            .slice(0, MAX_FAILURES).map(normalizeFailure).filter(Boolean);
        set({
            loading: false,
            health: data,
            failures,
            stuckIds: (Array.isArray(data.stuck_ids) ? data.stuck_ids : []).filter(Number.isInteger),
            progress: summarizeProgress(data.counts),
        });
        return res;
    }

    function canRetryJob(failure) {
        return state.value.authorized === true && !failure.unknown && failure.version !== null;
    }

    async function retry(jobId) {
        const failure = state.value.failures.find((f) => f.id === jobId);
        if (!failure) return { outcome: 'validation', code: 'INVALID_PAYLOAD' };
        if (failure.unknown) {
            set({ notice: LABELS.reconcileHint });
            if (typeof opts.onReconcile === 'function') opts.onReconcile(failure.id);
            return { outcome: 'reconcile' };
        }
        if (!canRetryJob(failure)) return { outcome: 'permission', code: 'FORBIDDEN' };
        if (state.value.busy !== null) return { outcome: 'busy' };

        // The same key is reused after an unclear outcome so the server replays one receipt.
        const slot = failure.id + ':' + failure.version;
        let requestId = pendingKeys.get(slot);
        if (!requestId) {
            requestId = newRequestId();
            if (!requestId || !SAFE_ID.test(requestId)) return { outcome: 'validation', code: 'INVALID_PAYLOAD' };
            pendingKeys.set(slot, requestId);
        }
        set({ busy: failure.id, error: null, notice: '' });
        const res = await api.request('/jobs/' + failure.id + '/retry', {
            method: 'POST',
            body: { request_id: requestId, expected_version: failure.version },
            idempotencyKey: requestId,
            ifMatch: '"' + failure.version + '"',
        });
        set({ busy: null });
        if (res.outcome === 'ok') {
            pendingKeys.delete(slot);
            set({ notice: LABELS.retried });
            await load();
        } else if (res.outcome === 'conflict') {
            pendingKeys.delete(slot);
            await load();
            set({ error: fail(res), notice: LABELS.staleVersion });
        } else if (res.outcome === 'UNKNOWN') {
            set({ error: fail(res), notice: LABELS.reconcileHint });
            if (typeof opts.onReconcile === 'function') opts.onReconcile(failure.id);
        } else {
            // Failure keeps the original row visible and the key for a replay.
            if (res.outcome === 'permission' || res.outcome === 'validation') pendingKeys.delete(slot);
            set({ error: fail(res) });
        }
        return res;
    }

    return { state, load, retry, canRetryJob };
}

function failureSurface(failure) {
    return surfaceFor(failure.unknown ? 'warning' : 'error');
}

function styleOf(surface) {
    return 'background:' + surface.background + ';color:' + surface.color + ';border:1px solid ' + surface.border + ';';
}

/** Vue component built on window.Vue (or the Vue passed in). Render function, no template compiler. */
export function createJobsPanel(Vue, options) {
    const V = Vue || (typeof window !== 'undefined' ? window.Vue : null);
    const h = V.h;
    const jobs = useJobs(Object.assign({}, options, { Vue: V }));

    function renderFailure(failure) {
        const surface = failureSurface(failure);
        const busy = jobs.state.value.busy === failure.id;
        const offer = failure.unknown || jobs.canRetryJob(failure);
        const label = failure.unknown ? LABELS.reconcile : LABELS.retry;
        const cells = [
            h('td', failure.id),
            h('td', failure.jobType),
            h('td', { style: styleOf(surface) }, surface.label + ': ' + failure.errorClass),
            h('td', failure.message),
            h('td', offer
                ? [h('button', {
                    type: 'button',
                    style: 'min-height:44px;min-width:44px;',
                    disabled: busy,
                    'aria-busy': busy ? 'true' : 'false',
                    'aria-label': label + ', Auftrag ' + failure.id,
                    onClick: () => jobs.retry(failure.id),
                }, label)]
                : []),
        ];
        return h('tr', { key: failure.id }, cells);
    }

    const Component = {
        name: 'ClinicJobsPanel',
        setup() {
            if (V.onMounted) V.onMounted(jobs.load);
            return () => {
                const s = jobs.state.value;
                const p = s.progress;
                const err = s.error;
                return h('section', { 'aria-labelledby': 'clinic-jobs-title' }, [
                    h('h2', { id: 'clinic-jobs-title' }, LABELS.title),
                    h('div', {
                        role: 'progressbar',
                        'aria-label': LABELS.progress,
                        'aria-valuemin': 0,
                        'aria-valuemax': 100,
                        'aria-valuenow': p.percent,
                        'aria-valuetext': p.completed + '/' + p.total,
                    }, LABELS.progress + ': ' + p.completed + '/' + p.total),
                    h('p', LABELS.queued + ' ' + p.queued + ' · ' + LABELS.running + ' ' + p.running
                        + ' · ' + LABELS.failed + ' ' + p.failed + ' · ' + LABELS.stuck + ' ' + s.stuckIds.length),
                    h('button', { type: 'button', disabled: s.loading, onClick: jobs.load }, LABELS.reload),
                    h('div', { role: 'status', 'aria-live': 'polite' }, s.loading
                        ? LABELS.loading
                        : [s.notice, err ? err.message + (err.correlationId ? ' (' + LABELS.correlation + ' ' + err.correlationId + ')' : '') : ''].filter(Boolean).join(' ')),
                    s.failures.length
                        ? h('table', { 'aria-label': LABELS.failures }, [
                            h('tbody', s.failures.map(renderFailure)),
                        ])
                        : h('p', LABELS.none),
                    h('p', LABELS.parity),
                ]);
            };
        },
    };
    return { component: Component, jobs };
}
