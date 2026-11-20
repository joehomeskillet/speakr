/**
 * Composed clinic processing view (V8 P17, PAR-OPS-008).
 *
 * Builds on jobs-ui (GET /jobs/health, retry with idempotency key and version
 * guard, UNKNOWN routed to reconciliation). This module adds the view: health
 * summary, job list, a filter by state, a permission message and the
 * registered component. Nothing from jobs-ui is re-implemented here.
 *
 * Only redacted job data is shown: id, type, state, error class, finish time.
 * The free-text failure message is not rendered, so no transcript text, audio
 * path or patient data can reach the page. Everything stays in memory; there
 * is no browser storage. Colours come from tokens.js, requests from the
 * shared api client handed in as `client`.
 */

import { surfaceFor } from './tokens.js';
import { LABELS as JOB_LABELS, TRAY_AUTOSTART_PARITY, useJobs } from './jobs-ui.js';

export { TRAY_AUTOSTART_PARITY };

export const COMPOSE_JOBS_VERSION = 1;
export const FILTER_ALL = 'all';

export const TEXT = Object.freeze({
    title: 'Verarbeitung',
    health: 'Zustand der Verarbeitung',
    refresh: JOB_LABELS.reload,
    retry: JOB_LABELS.retry,
    reconcile: JOB_LABELS.reconcile,
    loading: JOB_LABELS.loading,
    list: JOB_LABELS.failures,
    empty: JOB_LABELS.none,
    emptyFiltered: 'Keine Aufträge in diesem Zustand.',
    filter: 'Zustand',
    filterAll: 'Alle',
    unknown: 'Unbekannt',
    denied: 'Keine Berechtigung für die Verarbeitungsübersicht oder diese Aktion.',
    failed: 'Der Verarbeitungsstatus konnte nicht geladen werden.',
    reference: JOB_LABELS.correlation,
    stuckIds: 'Hängende Aufträge',
    scopedTitle: 'Verarbeitung dieses Diktats',
    scopedEmpty: 'Für dieses Diktat liegen keine Aufträge vor.',
    colJob: 'Auftrag',
    colType: 'Art',
    colState: 'Zustand',
    colAction: 'Aktion',
});

/** Filter key of a failure row: UNKNOWN stays separate, otherwise its own state, else "failed". */
export function stateKey(failure) {
    if (failure.unknown) return 'unknown';
    return failure.state ? failure.state.toLowerCase() : 'failed';
}

/** Filter options for the rows currently loaded: "all" first, then each present state once. */
export function filterOptions(failures) {
    const seen = [];
    for (const f of failures) {
        const key = stateKey(f);
        if (!seen.includes(key)) seen.push(key);
    }
    return [FILTER_ALL, ...seen.sort()];
}

export function filterLabel(key) {
    if (key === FILTER_ALL) return TEXT.filterAll;
    if (key === 'unknown') return TEXT.unknown;
    if (key === 'failed') return JOB_LABELS.failed;
    if (key === 'queued') return JOB_LABELS.queued;
    if (key === 'running' || key === 'processing') return JOB_LABELS.running;
    return key;
}

/**
 * Rows of one dictation. The jobs route does not yet name the dictation of a failure, so a row
 * counts only when its raw payload carries a matching `dictation_id`; without one it is dropped,
 * never shown from the global list.
 */
export function scopeToDictation(failures, rawRows, dictationId) {
    const id = String(dictationId || '');
    if (!id) return failures;
    const own = new Set((Array.isArray(rawRows) ? rawRows : [])
        .filter((r) => r && r.dictation_id !== undefined && r.dictation_id !== null && String(r.dictation_id) === id)
        .map((r) => r.id));
    return failures.filter((f) => own.has(f.id));
}

export function applyFilter(failures, filter) {
    return filter === FILTER_ALL ? failures : failures.filter((f) => stateKey(f) === filter);
}

/** Text for a failed request: a 403 gets the permission message, everything else the load error. */
export function errorText(error) {
    if (!error) return '';
    if (error.outcome === 'permission') return TEXT.denied;
    const base = error.message || TEXT.failed;
    return error.correlationId ? base + ' (' + TEXT.reference + ' ' + error.correlationId + ')' : base;
}

export function rowSurface(failure) {
    const s = surfaceFor(failure.unknown ? 'warning' : 'error');
    return { background: s.background, color: s.color, border: '1px solid ' + s.border, label: s.label };
}

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

/**
 * Controller behind JobsView. `vue` is optional: without it plain holders are
 * used (unit tests). One retry request per click: a click while a retry is
 * running is ignored by jobs-ui (busy), and the idempotency key is reused after
 * an unclear outcome.
 */
export function createJobsViewController(options) {
    const opts = options || {};
    const client = opts.client;
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const V = opts.vue || (typeof window !== 'undefined' ? window.Vue : null) || null;
    const ref = (V && V.ref) || plainRef;
    const computed = (V && V.computed) || ((fn) => ({ get value() { return fn(); } }));
    const jobs = useJobs({
        api: client,
        canRetry: opts.canRetry === true,
        onReconcile: opts.onReconcile,
        newRequestId: opts.newRequestId,
        Vue: V && V.ref ? V : undefined,
    });
    const filter = ref(FILTER_ALL);
    let disposed = false;
    const scope = () => (typeof opts.dictationId === 'function' ? opts.dictationId() : opts.dictationId) || '';
    const scoped = () => {
        const s = jobs.state.value;
        return scopeToDictation(s.failures, s.health && s.health.recent_failures, scope());
    };

    const options_ = computed(() => filterOptions(scoped()));
    const rows = computed(() => {
        const list = scoped();
        // A filter whose state no longer occurs falls back to all rows.
        const active = filterOptions(list).includes(filter.value) ? filter.value : FILTER_ALL;
        return applyFilter(list, active);
    });
    const denied = computed(() => {
        const err = jobs.state.value.error;
        return Boolean(err && err.outcome === 'permission');
    });

    async function load() {
        if (disposed) return null;
        return jobs.load();
    }

    function setFilter(key) {
        filter.value = typeof key === 'string' && key ? key : FILTER_ALL;
    }

    function retry(jobId) {
        if (disposed) return null;
        return jobs.retry(jobId);
    }

    /** Drops rows held in memory; later calls are ignored. */
    function dispose() { disposed = true; }

    return {
        vue: V, jobs, state: jobs.state, filter, filterOptions: options_, rows, denied, scoped: () => Boolean(scope()),
        load, retry, setFilter, dispose, canRetryJob: jobs.canRetryJob,
        text: TEXT, surface: rowSurface, filterLabel, errorText: () => errorText(jobs.state.value.error),
    };
}

export const JobsView = {
    name: 'ClinicJobsView',
    props: {
        client: { type: Object, required: true },
        canRetry: { type: Boolean, default: false },
        vue: { type: Object, default: null },
        onReconcile: { type: Function, default: null },
        autoStart: { type: Boolean, default: true },
        dictationId: { type: String, default: '' },
    },
    setup(props) {
        const c = createJobsViewController({
            client: props.client,
            canRetry: props.canRetry,
            vue: props.vue || undefined,
            onReconcile: props.onReconcile || undefined,
            dictationId: () => props.dictationId,
        });
        const V = c.vue;
        if (!V) throw new Error('Vue is required to mount the jobs view');
        if (typeof V.onMounted === 'function' && props.autoStart) V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c };
    },
    template: `
<section class="clinic-jobs-view surface flex flex-col gap-4 p-4 md:p-6" data-testid="clinic-jobs-view" aria-labelledby="cjv-title"
         :data-scoped="c.scoped() ? 'true' : 'false'">
  <header class="flex flex-wrap items-center justify-between gap-3">
    <h1 id="cjv-title" class="t-heading">{{ c.scoped() ? c.text.scopedTitle : c.text.title }}</h1>
    <div class="flex flex-wrap items-center gap-3">
      <label class="t-meta flex items-center gap-2">{{ c.text.filter }}
        <select class="field field--sm" data-testid="state-filter" :value="c.filter.value" @change="c.setFilter($event.target.value)">
          <option v-for="key in c.filterOptions.value" :key="key" :value="key">{{ c.filterLabel(key) }}</option>
        </select>
      </label>
      <button type="button" class="btn btn--ghost"
              :disabled="c.state.value.loading" :aria-busy="c.state.value.loading ? 'true' : 'false'"
              data-testid="reload" @click="c.load()">{{ c.text.refresh }}</button>
    </div>
  </header>
  <div role="status" aria-live="polite" class="t-meta" data-testid="status">{{ c.state.value.loading ? c.text.loading : c.state.value.notice }}</div>
  <div v-if="c.state.value.error" role="alert" class="pill-status pill-status--danger" data-testid="load-error"
       :data-denied="c.denied.value ? 'true' : 'false'">{{ c.errorText() }}</div>
  <template v-if="!c.denied.value">
    <p v-if="!c.scoped()" data-testid="health" class="t-body" role="region" :aria-label="c.text.health">
      {{ c.state.value.progress.completed }}/{{ c.state.value.progress.total }}
      · {{ c.text.stuckIds }} {{ c.state.value.stuckIds.length }}
    </p>
    <p v-if="!c.rows.value.length" data-testid="empty-state" class="t-body">{{ c.scoped() ? c.text.scopedEmpty : (c.state.value.failures.length ? c.text.emptyFiltered : c.text.empty) }}</p>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-left table-auto" :aria-label="c.text.list" data-testid="job-list">
        <thead>
          <tr class="t-label">
            <th scope="col" class="py-2 pr-2 md:pr-3">{{ c.text.colJob }}</th>
            <th scope="col" class="py-2 pr-3 hidden md:table-cell">{{ c.text.colType }}</th>
            <th scope="col" class="py-2 pr-3">{{ c.text.colState }}</th>
            <th scope="col" class="py-2 text-right">{{ c.text.colAction }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in c.rows.value" :key="f.id" :data-id="f.id" class="t-body border-t border-[var(--border-primary)]">
            <td class="py-2 pr-2 md:pr-3 align-top">{{ f.id }}<span class="t-meta block md:hidden">{{ f.jobType }}</span></td>
            <td class="py-2 pr-3 hidden md:table-cell">{{ f.jobType }}</td>
            <td class="py-2 pr-2 md:pr-3">
              <span class="pill-status whitespace-normal break-words" :class="f.unknown ? 'pill-status--warn' : 'pill-status--danger'">{{ c.surface(f).label }}: {{ f.errorClass }}</span>
            </td>
            <td class="py-2 text-right">
              <button v-if="f.unknown || c.canRetryJob(f)" type="button" class="btn btn--ghost btn--sm max-w-[7rem] whitespace-normal leading-tight"
                      data-testid="retry" :disabled="c.state.value.busy === f.id"
                      :aria-label="(f.unknown ? c.text.reconcile : c.text.retry) + ', Auftrag ' + f.id"
                      @click="c.retry(f.id)">{{ f.unknown ? c.text.reconcile : c.text.retry }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</section>`,
};

export function registerJobsView(app) {
    app.component('clinic-jobs-view', JobsView);
    return app;
}
