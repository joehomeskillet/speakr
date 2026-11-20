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
import { ACTION_TABLE, V8_PAGE } from './compose-import.js';
import { LABELS as JOB_LABELS, TRAY_AUTOSTART_PARITY, jobTypeLabel, useJobs } from './jobs-ui.js';

export { TRAY_AUTOSTART_PARITY };

const ICON_REFRESH = '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/></svg>';

export const COMPOSE_JOBS_VERSION = 1;
export const FILTER_ALL = 'all';

export const TEXT = Object.freeze({
    title: 'Verarbeitung',
    eyebrow: 'Verwaltung',
    eyebrowScoped: 'Diktat',
    subline: 'Aufträge, Fehler und unklare Ausgabestände nachvollziehen. Nur bereinigte Auftragsdaten werden angezeigt.',
    sublineScoped: 'Aufträge dieses Diktats und ihr aktueller Zustand.',
    statDone: 'Abgeschlossen',
    statDoneNote: 'Aufträge insgesamt',
    statFailedNote: 'Manueller Versuch möglich',
    statFailedNoteReadOnly: 'Nur Anzeige',
    statStuckNote: 'Ohne Fortschritt',
    statFailed: 'Fehlgeschlagen',
    statShown: 'Angezeigt',
    statShownNote: 'Nach Filter',
    rulesTitle: 'Wiederholungsregeln',
    rulesCorrected: 'Bereits korrigierte Textrevisionen dürfen durch eine erneute Spracherkennung nicht still ersetzt werden.',
    rulesUnknown: 'Ein unklarer Zustand ist kein gewöhnlicher Fehler und erlaubt keinen blinden Neuversand. Zuerst abgleichen.',
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
        load, retry, setFilter, dispose, canRetry: opts.canRetry === true, canRetryJob: jobs.canRetryJob,
        text: TEXT, surface: rowSurface, jobTypeLabel, filterLabel, errorText: () => errorText(jobs.state.value.error),
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
<section class="${V8_PAGE} clinic-jobs-view" data-testid="clinic-jobs-view" aria-labelledby="cjv-title"
         :data-scoped="c.scoped() ? 'true' : 'false'">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ c.scoped() ? c.text.eyebrowScoped : c.text.eyebrow }}</div>
      <h1 id="cjv-title">{{ c.scoped() ? c.text.scopedTitle : c.text.title }}&nbsp;<span class="count-pill align-middle" data-testid="job-count">{{ c.rows.value.length }}</span></h1>
      <p class="subline">{{ c.scoped() ? c.text.sublineScoped : c.text.subline }}</p>
    </div>
    <div class="head-actions">
      <button type="button" :disabled="c.state.value.loading" :aria-busy="c.state.value.loading ? 'true' : 'false'"
              data-testid="reload" @click="c.load()">${ICON_REFRESH}<span>{{ c.text.refresh }}</span></button>
    </div>
  </div>
  <div role="status" aria-live="polite" class="result-count" data-testid="status">{{ c.state.value.loading ? c.text.loading : c.state.value.notice }}</div>
  <div v-if="c.state.value.error" role="alert" class="note burgundy" data-testid="load-error"
       :data-denied="c.denied.value ? 'true' : 'false'">{{ c.errorText() }}</div>
  <template v-if="!c.denied.value">
    <div v-if="!c.scoped()" class="stats" data-testid="health" role="region" :aria-label="c.text.health">
      <div class="stat"><span>{{ c.text.statDone }}</span><strong>{{ c.state.value.progress.completed }}/{{ c.state.value.progress.total }}</strong><small>{{ c.text.statDoneNote }}: {{ c.state.value.progress.total }}</small></div>
      <div class="stat"><span>{{ c.text.statFailed }}</span><strong>{{ c.state.value.progress.failed }}</strong><small>{{ c.canRetry ? c.text.statFailedNote : c.text.statFailedNoteReadOnly }}</small></div>
      <div class="stat"><span>{{ c.text.stuckIds }}</span><strong>{{ c.state.value.stuckIds.length }}</strong><small>{{ c.text.statStuckNote }}</small></div>
      <div class="stat"><span>{{ c.text.statShown }}</span><strong>{{ c.rows.value.length }}</strong><small>{{ c.text.statShownNote }}</small></div>
    </div>
    <section class="panel" aria-labelledby="cjv-list">
      <h2 id="cjv-list">{{ c.text.list }}</h2>
      <div class="filterbar">
        <div class="field">
          <label for="cjv-filter">{{ c.text.filter }}</label>
          <select id="cjv-filter" data-testid="state-filter" :value="c.filter.value" @change="c.setFilter($event.target.value)">
            <option v-for="key in c.filterOptions.value" :key="key" :value="key">{{ c.filterLabel(key) }}</option>
          </select>
        </div>
      </div>
      <p v-if="!c.rows.value.length" class="empty" data-testid="empty-state">{{ c.scoped() ? c.text.scopedEmpty : (c.state.value.failures.length ? c.text.emptyFiltered : c.text.empty) }}</p>
      <div v-else class="table-wrap">
        <table class="${ACTION_TABLE}" :aria-label="c.text.list" data-testid="job-list">
          <thead>
            <tr>
              <th scope="col">{{ c.text.colJob }}</th>
              <th scope="col" class="max-md:hidden">{{ c.text.colType }}</th>
              <th scope="col">{{ c.text.colState }}</th>
              <th scope="col">{{ c.text.colAction }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in c.rows.value" :key="f.id" :data-id="f.id">
              <td><strong>{{ f.id }}</strong></td>
              <td class="max-md:hidden">{{ c.jobTypeLabel(f.jobType) }}</td>
              <td><span class="tag dot max-md:[overflow-wrap:anywhere]" :class="f.unknown ? 'wait' : 'error'">{{ c.surface(f).label }}: {{ f.errorClass }}</span></td>
              <td>
                <button v-if="f.unknown || c.canRetryJob(f)" type="button" class="small"
                        data-testid="retry" :disabled="c.state.value.busy === f.id"
                        :aria-label="(f.unknown ? c.text.reconcile : c.text.retry) + ', Auftrag ' + f.id"
                        @click="c.retry(f.id)">{{ f.unknown ? c.text.reconcile : c.text.retry }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section v-if="!c.scoped()" class="panel" aria-labelledby="cjv-rules" style="margin-top: 20px">
      <h2 id="cjv-rules">{{ c.text.rulesTitle }}</h2>
      <p class="hint">{{ c.text.rulesCorrected }}</p>
      <p class="hint">{{ c.text.rulesUnknown }}</p>
    </section>
  </template>
</section>`,
};

export function registerJobsView(app) {
    app.component('clinic-jobs-view', JobsView);
    return app;
}
