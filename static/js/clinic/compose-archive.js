/**
 * Composed clinic archive view (V8 P08, PAR-WF-013).
 *
 * The archive is a state filter over the recordings list of the same
 * dictation domain, not a second collection: every request is
 * GET /api/clinic/v1/recordings with status=zu_archivieren (the workflow state
 * TO_ARCHIVE in src/clinic/workflow.py), cursor paging and ID-only filters
 * (date range, author, pool). No new "archived" flag exists.
 *
 * Assumptions, to be verified when the v8-01 API and the v8-02 view land:
 *  - recordings-ui.js (v8-02) is not on clinic/main yet, so the few formatters
 *    needed here (Zurich day/time, role cells) are local and small.
 *  - A row may carry optional retention fields next to the v8-01 contract:
 *    retention_state ("active" | "due" | "held" | "deleted", the V4 retention
 *    axis, projected from retention_archived_at / legal hold / due date) and
 *    archived_at (ISO UTC, from retention_archived_at). Without them the row
 *    shows "Aufbewahrung nicht angegeben"; nothing is derived on the client.
 *
 * Read only: there is no delete, reopen or restore control. Opening a row
 * emits the worklist-style open event so the existing detail view (P03) opens.
 * No patient name, title or patient reference is read or rendered. Nothing is
 * written to browser storage; data stays in memory. Colours come from
 * tokens.js, requests from api-client.js.
 */

import { TOKEN_CONTRACT_VERSION, surfaceFor } from './tokens.js';

export const COMPOSE_ARCHIVE_VERSION = 1;
export const PAGE_SIZE = 50;

/** Workflow state the archive filter selects (workflow.TO_ARCHIVE). */
export const ARCHIVE_STATUS = 'zu_archivieren';
export const ZONE = 'Europe/Zurich';

export const TEXT = Object.freeze({
    title: 'Archiv',
    back: 'Zurück zu Aufnahmen',
    removeFilter: 'Archivfilter entfernen',
    loading: 'Laden …',
    emptyArchive: 'Keine archivierten Aufnahmen vorhanden.',
    emptyFilter: 'Keine Treffer für die gewählten Filter.',
    failed: 'Das Archiv konnte nicht geladen werden.',
    stale: 'Der angezeigte Datenstand ist möglicherweise veraltet.',
    denied: 'Keine Berechtigung für diese Aktion.',
    retry: 'Erneut versuchen',
    more: 'Mehr laden',
    open: 'Öffnen',
    apply: 'Filter anwenden',
    reset: 'Filter zurücksetzen',
    from: 'Von',
    to: 'Bis',
    author: 'Diktiert von',
    pool: 'Schreibkräfte-Pool',
    recorded: 'Aufnahmezeit',
    archived: 'Archiviert am',
    retention: 'Aufbewahrung',
    noPool: 'Kein Pool zugewiesen',
    noAuthor: 'Nicht angegeben',
    noValue: '–',
    createdMark: 'erfasst',
    createdHint: 'Keine Aufnahmezeit vorhanden: angezeigt wird der Erfassungszeitpunkt.',
    importedMark: 'importiert',
    importedHint: 'Zeitpunkt aus der importierten Datei.',
    hits: (n) => (n === 1 ? '1 Treffer' : n + ' Treffer'),
});

/** Retention labels as text, never colour only (V4 axis active / due / held / deleted). */
export const RETENTION_LABELS = Object.freeze({
    held: 'Hold',
    due: 'Frist fällig',
    active: 'Aktiv aufbewahrt',
    deleted: 'Löschung bestätigt',
});
export const RETENTION_UNKNOWN = 'Aufbewahrung nicht angegeben';

const RETENTION_SURFACE = Object.freeze({ held: 'warning', due: 'warning', active: 'normal', deleted: 'normal' });

export function retentionLabel(row) {
    const key = row && typeof row.retention_state === 'string' ? row.retention_state : null;
    return key && Object.prototype.hasOwnProperty.call(RETENTION_LABELS, key) ? RETENTION_LABELS[key] : RETENTION_UNKNOWN;
}

export function retentionStyle(row) {
    const key = row && row.retention_state;
    const s = surfaceFor(RETENTION_SURFACE[key] || 'normal', { version: TOKEN_CONTRACT_VERSION });
    return { background: s.background, color: s.color, border: '1px solid ' + s.border };
}

export function rowKey(row) {
    return row && row.dictation_id != null ? String(row.dictation_id) : null;
}

const dateFmt = new Intl.DateTimeFormat('de-CH', { timeZone: ZONE, day: '2-digit', month: '2-digit', year: 'numeric' });
const timeFmt = new Intl.DateTimeFormat('de-CH', { timeZone: ZONE, hour: '2-digit', minute: '2-digit', hour12: false });

function parse(value) {
    if (typeof value !== 'string' || !value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
}

/** "dd.mm.yyyy hh:mm" in Europe/Zurich, or null for a missing or invalid value. */
export function formatMoment(value) {
    const d = parse(value);
    return d ? dateFmt.format(d) + ' ' + timeFmt.format(d) : null;
}

/** Recording time with the marker for a fallback source (never shown as recording time). */
export function recordedCell(row) {
    const text = formatMoment(row && (row.recorded_at || row.sort_time)) || TEXT.noValue;
    const source = row && row.time_source;
    if (source === 'created') return { text, mark: TEXT.createdMark, hint: TEXT.createdHint };
    if (source === 'imported') return { text, mark: TEXT.importedMark, hint: TEXT.importedHint };
    return { text, mark: null, hint: null };
}

export const archivedCell = (row) => formatMoment(row && row.archived_at) || TEXT.noValue;
export const authorCell = (row) => (row && row.dictated_by && row.dictated_by.display_name) || TEXT.noAuthor;
export function poolCell(row) {
    const pools = row && Array.isArray(row.staff_pools) ? row.staff_pools : [];
    return pools.length ? pools.map((p) => p.name).join(', ') : TEXT.noPool;
}

/** Query for one page. Only IDs and dates are sent; the status is always the archive state. */
export function buildQuery(filters, cursor, limit) {
    const params = new URLSearchParams();
    params.set('limit', String(limit || PAGE_SIZE));
    if (cursor) params.set('cursor', cursor);
    const f = filters || {};
    if (f.from) params.set('from', f.from);
    if (f.to) params.set('to', f.to);
    if (f.authorId) params.set('author_id', String(f.authorId));
    if (f.poolId) params.set('pool_id', String(f.poolId));
    params.set('status', ARCHIVE_STATUS);
    return '/recordings?' + params.toString();
}

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

/**
 * Controller behind ArchiveView. `vue` supplies ref and reactive; without it
 * plain holders are used, which the unit tests rely on.
 */
export function createArchiveController(options) {
    const client = options && options.client;
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const V = options.vue || (typeof window !== 'undefined' ? window.Vue : null) || null;
    const ref = (V && V.ref) || plainRef;
    const reactive = (V && V.reactive) || ((o) => o);
    const pageSize = Number.isInteger(options.pageSize) && options.pageSize > 0 ? options.pageSize : PAGE_SIZE;

    const filters = reactive({ from: '', to: '', authorId: '', poolId: '' });
    const rows = ref([]);
    const nextCursor = ref(null);
    const loading = ref(false);
    const error = ref(null);
    const denied = ref(false);
    let generation = 0;
    let disposed = false;

    const filtered = () => Boolean(filters.from || filters.to || filters.authorId || filters.poolId);
    const hasMore = () => nextCursor.value !== null && nextCursor.value !== undefined;

    async function fetchPage(cursor) {
        const seen = (generation += 1);
        loading.value = true;
        const res = await client.request(buildQuery(filters, cursor, pageSize), { method: 'GET' });
        if (disposed || seen !== generation) return null; // superseded by a newer request
        loading.value = false;
        if (res && res.outcome === 'ok') {
            const data = res.data || {};
            const items = Array.isArray(data.items) ? data.items : [];
            const base = cursor ? rows.value : [];
            const have = new Set(base.map(rowKey));
            rows.value = base.concat(items.filter((r) => rowKey(r) !== null && !have.has(rowKey(r))));
            nextCursor.value = data.next_cursor || null;
            error.value = null;
            denied.value = false;
        } else if (res && res.outcome === 'permission') {
            // No titles or people of an archive the user may not read.
            rows.value = [];
            nextCursor.value = null;
            denied.value = true;
            error.value = null;
        } else {
            // Keep the rows already shown, but mark them as stale.
            error.value = (res && res.message) || TEXT.failed;
        }
        return res;
    }

    /** First page with the current filters; the cursor is reset. */
    const load = () => fetchPage(null);
    /** Appends the next page; ignored while loading or without a cursor. */
    const loadMore = () => (hasMore() && !loading.value ? fetchPage(nextCursor.value) : null);

    function setFilters(next) {
        const n = next || {};
        for (const key of ['from', 'to', 'authorId', 'poolId']) {
            if (n[key] !== undefined) filters[key] = n[key] === null ? '' : String(n[key]);
        }
        return load();
    }
    const resetFilters = () => setFilters({ from: '', to: '', authorId: '', poolId: '' });

    /** Opens the dictation in the existing detail view; the archive mark travels with the event. */
    function open(row) {
        const id = rowKey(row);
        if (disposed || id === null) return false;
        if (typeof options.onOpen === 'function') options.onOpen({ dictationId: id, mode: 'view', archived: true });
        return true;
    }

    /** Leaves the archive view. Only the view changes. */
    function back() {
        if (disposed) return false;
        if (typeof options.onBack === 'function') options.onBack();
        return true;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        generation += 1;
        rows.value = [];
        nextCursor.value = null;
    }

    const emptyText = () => (filtered() ? TEXT.emptyFilter : TEXT.emptyArchive);

    return {
        vue: V, filters, rows, nextCursor, loading, error, denied,
        load, loadMore, setFilters, resetFilters, open, back, dispose,
        filtered, hasMore, emptyText, text: TEXT, key: rowKey,
        recorded: recordedCell, archived: archivedCell, author: authorCell, pool: poolCell,
        retention: retentionLabel, retentionStyle,
        hits: () => TEXT.hits(rows.value.length),
    };
}

export const ArchiveView = {
    name: 'ClinicArchiveView',
    emits: ['open', 'back'],
    props: {
        client: { type: Object, required: true },
        vue: { type: Object, default: null },
        autoStart: { type: Boolean, default: true },
    },
    setup(props, ctx) {
        const emit = (name, payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name, payload); };
        const c = createArchiveController({
            client: props.client,
            vue: props.vue || undefined,
            onOpen: (payload) => emit('open', payload),
            onBack: () => emit('back'),
        });
        const V = c.vue;
        if (!V) throw new Error('Vue is required to mount the archive view');
        if (typeof V.onMounted === 'function' && props.autoStart) V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c };
    },
    template: `
<section class="clinic-archive-view" data-testid="clinic-archive-view" aria-labelledby="cav-title">
  <header class="flex flex-wrap items-center gap-2">
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="archive-back" @click="c.back()">{{ c.text.back }}</button>
    <h1 id="cav-title" class="text-lg font-semibold">{{ c.text.title }}</h1>
    <span role="status" aria-live="polite" data-testid="archive-hits">{{ c.hits() }}</span>
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="archive-remove-filter" @click="c.back()">{{ c.text.removeFilter }}</button>
  </header>
  <form class="flex flex-wrap items-end gap-2 mt-3" data-testid="archive-filters" @submit.prevent="c.load()">
    <label class="flex flex-col text-sm">{{ c.text.from }}<input type="date" class="px-2 py-2 border rounded" v-model="c.filters.from" data-testid="filter-from"></label>
    <label class="flex flex-col text-sm">{{ c.text.to }}<input type="date" class="px-2 py-2 border rounded" v-model="c.filters.to" data-testid="filter-to"></label>
    <label class="flex flex-col text-sm">{{ c.text.author }}<input type="text" class="px-2 py-2 border rounded" v-model="c.filters.authorId" data-testid="filter-author" autocomplete="off"></label>
    <label class="flex flex-col text-sm">{{ c.text.pool }}<input type="text" class="px-2 py-2 border rounded" v-model="c.filters.poolId" data-testid="filter-pool" autocomplete="off"></label>
    <button type="submit" class="px-3 py-2 border rounded" style="min-height: 2.75rem">{{ c.text.apply }}</button>
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" @click="c.resetFilters()">{{ c.text.reset }}</button>
  </form>
  <div v-if="c.denied.value" role="alert" class="mt-2 p-2 rounded" data-testid="archive-denied">{{ c.text.denied }}</div>
  <template v-else>
    <div v-if="c.error.value" role="alert" class="mt-2 p-2 rounded" data-testid="archive-error">
      {{ c.error.value }} {{ c.text.stale }}
      <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" @click="c.load()">{{ c.text.retry }}</button>
    </div>
    <div v-if="c.loading.value && !c.rows.value.length" role="status" aria-live="polite" data-testid="archive-loading">{{ c.text.loading }}</div>
    <p v-else-if="!c.rows.value.length && !c.error.value" data-testid="archive-empty">{{ c.emptyText() }}</p>
    <div v-else-if="c.rows.value.length" class="overflow-x-auto mt-3">
      <table class="w-full" aria-label="Archiv">
        <thead>
          <tr>
            <th scope="col">{{ c.text.recorded }}</th>
            <th scope="col">{{ c.text.author }}</th>
            <th scope="col">{{ c.text.pool }}</th>
            <th scope="col">{{ c.text.archived }}</th>
            <th scope="col">{{ c.text.retention }}</th>
            <th scope="col"><span class="sr-only">{{ c.text.open }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in c.rows.value" :key="c.key(row)" data-row :data-id="c.key(row)">
            <td>
              {{ c.recorded(row).text }}
              <span v-if="c.recorded(row).mark" class="text-xs" :title="c.recorded(row).hint" :aria-label="c.recorded(row).mark + ': ' + c.recorded(row).hint">({{ c.recorded(row).mark }})</span>
            </td>
            <td data-cell="author">{{ c.author(row) }}</td>
            <td data-cell="pool">{{ c.pool(row) }}</td>
            <td data-cell="archived">{{ c.archived(row) }}</td>
            <td data-cell="retention"><span class="px-2 py-1 rounded text-xs" :style="c.retentionStyle(row)">{{ c.retention(row) }}</span></td>
            <td><button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="archive-open" @click="c.open(row)">{{ c.text.open }}</button></td>
          </tr>
        </tbody>
      </table>
      <button v-if="c.hasMore()" type="button" class="px-3 py-2 border rounded mt-2" style="min-height: 2.75rem"
              :disabled="c.loading.value" data-testid="archive-more" @click="c.loadMore()">{{ c.text.more }}</button>
    </div>
  </template>
</section>`,
};

export function registerArchiveView(app) {
    app.component('clinic-archive-view', ArchiveView);
    return app;
}
