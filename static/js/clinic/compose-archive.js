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
 *    archived_at (ISO UTC, from retention_archived_at or the durable manual
 *    archive entry). Without them the row
 *    shows "Aufbewahrung nicht angegeben"; nothing is derived on the client.
 *
 * Read only: there is no delete, reopen or restore control. Opening a row
 * emits the worklist-style open event so the existing detail view (P03) opens.
 * No patient name, title or patient reference is read or rendered. Nothing is
 * written to browser storage; data stays in memory. Colours come from
 * tokens.js, requests from api-client.js.
 */

import { TOKEN_CONTRACT_VERSION, surfaceFor } from './tokens.js';
import { ARCHIVE_STATUS, collectOptions, optionList } from './recordings-ui.js';

export const COMPOSE_ARCHIVE_VERSION = 1;
export const PAGE_SIZE = 50;

/** Workflow state the archive filter selects (workflow.TO_ARCHIVE). */
export { ARCHIVE_STATUS };
export const ZONE = 'Europe/Zurich';

// clinic-v8.css states heading, button and form-control styles with zero-specificity element selectors; the Tailwind
// base layer, loaded after it, resets those. These strings restate them where this page needs them.
const H1 = 'font-size: clamp(26px, 4vw, 30px); line-height: 1.2; font-weight: 700; letter-spacing: -.8px; margin: 0 0 9px';
const H2 = 'font-size: 16px; line-height: 1.35; font-weight: 700; letter-spacing: -.2px; margin: 0 0 14px';
const FIELD = 'display: grid; gap: 6px; margin: 0 0 16px; min-width: 0';
const LABEL = 'font-size: 12px; font-weight: 650';
const INPUT = 'width: 100%; min-width: 0; max-width: 100%; min-height: 40px; padding: 10px 11px; border: 1px solid var(--line); border-radius: 7px; background-color: var(--surface); color: var(--v8-text); font: inherit';

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
    eyebrow: 'Aufnahmen',
    subline: 'Abgeschlossene Diktate chronologisch ansehen. Die diktierende Person und die Poolzuständigkeit bleiben erhalten.',
    listHeading: 'Archivierte Diktate',
    statArchived: 'Geladene Einträge',
    statArchivedNote: 'Nach Aufnahmezeit sortiert',
    statAuthors: 'Diktierende',
    statAuthorsNote: 'Als eigene Metadaten sichtbar',
    statPools: 'Schreibkräfte-Pools',
    statPoolsNote: 'Gruppenzuständigkeit bleibt erhalten',
    statOldest: 'Ältestes Archivdatum',
    statOldestNote: 'Unter den geladenen Einträgen',
    readOnlyNote: 'Das Archiv wird nur gelesen. Bereits bestätigte Ausgaben bleiben unverändert.',
    filters: 'Filter',
    moreLoaded: 'Weitere Einträge sind verfügbar.',
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

/** Figures for the stat cards, derived from the rows that are loaded; nothing is fetched or guessed. */
export function archiveSummary(rows) {
    const list = Array.isArray(rows) ? rows : [];
    const authors = new Set();
    const pools = new Set();
    let oldest = null;
    for (const row of list) {
        const author = row && row.dictated_by && row.dictated_by.display_name;
        if (author) authors.add(author);
        for (const p of row && Array.isArray(row.staff_pools) ? row.staff_pools : []) if (p && p.name) pools.add(p.name);
        const at = parse(row && row.archived_at);
        if (at && (!oldest || at < oldest)) oldest = at;
    }
    return {
        count: list.length,
        authors: authors.size,
        pools: pools.size,
        oldest: oldest ? dateFmt.format(oldest) : TEXT.noValue,
    };
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
    const choices = reactive({ authors: [], pools: [] });
    const registry = { authors: new Map(), pools: new Map() };
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
            collectOptions(registry, items);
            choices.authors = optionList(registry.authors, filters.authorId);
            choices.pools = optionList(registry.pools, filters.poolId);
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
            registry.authors.clear();
            registry.pools.clear();
            choices.authors = [];
            choices.pools = [];
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
        registry.authors.clear();
        registry.pools.clear();
        choices.authors = [];
        choices.pools = [];
    }

    const emptyText = () => (filtered() ? TEXT.emptyFilter : TEXT.emptyArchive);

    return {
        vue: V, filters, rows, nextCursor, loading, error, denied, choices,
        load, loadMore, setFilters, resetFilters, open, back, dispose,
        filtered, hasMore, emptyText, text: TEXT, key: rowKey,
        recorded: recordedCell, archived: archivedCell, author: authorCell, pool: poolCell,
        retention: retentionLabel, retentionStyle,
        hits: () => TEXT.hits(rows.value.length),
        summary: () => archiveSummary(rows.value),
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
<section class="clinic-archive-view v8-page" data-testid="clinic-archive-view" aria-labelledby="cav-title">
  <nav class="breadcrumb" aria-label="Pfad"><span>Speakr</span><span aria-hidden="true">›</span><span>{{ c.text.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ c.text.title }}</span></nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ c.text.eyebrow }}</div>
      <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 4px 12px">
        <h1 id="cav-title" style="${H1}">{{ c.text.title }}</h1>
        <span class="tag neutral" style="margin-bottom: 9px" role="status" aria-live="polite" data-testid="archive-hits">{{ c.hits() }}</span>
      </div>
      <p class="subline">{{ c.text.subline }}</p>
    </div>
    <div class="head-actions">
      <button type="button" class="button" data-testid="archive-back" @click="c.back()">{{ c.text.back }}</button>
      <button type="button" class="button quiet" data-testid="archive-remove-filter" @click="c.back()">{{ c.text.removeFilter }}</button>
    </div>
  </div>
  <div class="stats" data-testid="archive-stats">
    <div class="stat"><span>{{ c.text.statArchived }}</span><strong>{{ c.summary().count }}</strong><small>{{ c.text.statArchivedNote }}</small></div>
    <div class="stat"><span>{{ c.text.statAuthors }}</span><strong>{{ c.summary().authors }}</strong><small>{{ c.text.statAuthorsNote }}</small></div>
    <div class="stat"><span>{{ c.text.statPools }}</span><strong>{{ c.summary().pools }}</strong><small>{{ c.text.statPoolsNote }}</small></div>
    <div class="stat"><span>{{ c.text.statOldest }}</span><strong>{{ c.summary().oldest }}</strong><small>{{ c.text.statOldestNote }}</small></div>
  </div>
  <section class="panel" :aria-label="c.text.listHeading">
    <h2 style="${H2}">{{ c.text.listHeading }}</h2>
    <form class="filterbar" role="search" :aria-label="c.text.filters" data-testid="archive-filters" @submit.prevent="c.load()">
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-from">{{ c.text.from }}</label><input style="${INPUT}" id="cav-from" type="date" v-model="c.filters.from" data-testid="filter-from"></div>
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-to">{{ c.text.to }}</label><input style="${INPUT}" id="cav-to" type="date" v-model="c.filters.to" data-testid="filter-to"></div>
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-author">{{ c.text.author }}</label><select style="${INPUT}" id="cav-author" v-model="c.filters.authorId" data-testid="filter-author"><option value="">Alle Diktierenden</option><option v-for="o in c.choices.authors" :key="o.value" :value="o.value">{{ o.label }}</option></select></div>
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-pool">{{ c.text.pool }}</label><select style="${INPUT}" id="cav-pool" v-model="c.filters.poolId" data-testid="filter-pool"><option value="">Alle Pools</option><option v-for="o in c.choices.pools" :key="o.value" :value="o.value">{{ o.label }}</option></select></div>
      <p class="hint">Auswahl aus den geladenen Archivaufnahmen. Weitere Aufnahmen können unten geladen werden.</p>
      <div class="actions text-sm">
        <button type="submit" class="button" style="background: var(--ink); border-color: var(--ink); color: white">{{ c.text.apply }}</button>
        <button type="button" class="button" @click="c.resetFilters()">{{ c.text.reset }}</button>
      </div>
    </form>
    <div v-if="c.denied.value" role="alert" class="note warning" data-testid="archive-denied">{{ c.text.denied }}</div>
    <template v-else>
      <div v-if="c.error.value" role="alert" class="note warning" data-testid="archive-error">
        {{ c.error.value }} {{ c.text.stale }}
        <button type="button" class="button small" style="margin-left: 8px" @click="c.load()">{{ c.text.retry }}</button>
      </div>
      <div v-if="c.loading.value && !c.rows.value.length" class="state-box" role="status" aria-live="polite" data-testid="archive-loading">{{ c.text.loading }}</div>
      <p v-else-if="!c.rows.value.length && !c.error.value" class="empty" data-testid="archive-empty">{{ c.emptyText() }}</p>
      <div v-else-if="c.rows.value.length">
        <div class="table-wrap" style="position: relative">
          <table aria-label="Archiv" style="white-space: normal">
            <thead>
              <tr>
                <th scope="col">{{ c.text.recorded }}</th>
                <th scope="col">{{ c.text.author }}</th>
                <th scope="col" class="hidden md:table-cell">{{ c.text.pool }}</th>
                <th scope="col" class="hidden md:table-cell">{{ c.text.archived }}</th>
                <th scope="col" class="hidden md:table-cell">{{ c.text.retention }}</th>
                <th scope="col"><span class="sr-only">Aktion</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in c.rows.value" :key="c.key(row)" data-row :data-id="c.key(row)">
                <td>
                  <strong>{{ c.recorded(row).text }}</strong>
                  <small v-if="c.recorded(row).mark" :title="c.recorded(row).hint" :aria-label="c.recorded(row).mark + ': ' + c.recorded(row).hint">({{ c.recorded(row).mark }})</small>
                </td>
                <td data-cell="author" style="overflow-wrap: anywhere">
                  {{ c.author(row) }}
                  <div class="md:hidden">
                    <small>{{ c.pool(row) }}</small>
                    <span class="tag" style="white-space: normal" :style="c.retentionStyle(row)">{{ c.retention(row) }}</span>
                  </div>
                </td>
                <td data-cell="pool" class="hidden md:table-cell" style="overflow-wrap: anywhere">{{ c.pool(row) }}</td>
                <td data-cell="archived" class="hidden md:table-cell">{{ c.archived(row) }}</td>
                <td data-cell="retention" class="hidden md:table-cell"><span class="tag" :style="c.retentionStyle(row)">{{ c.retention(row) }}</span></td>
                <td><button type="button" class="button small" data-testid="archive-open" @click="c.open(row)">{{ c.text.open }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="c.hasMore()" class="actions" style="justify-content: center; margin-top: 14px">
          <button type="button" class="button" :disabled="c.loading.value" data-testid="archive-more" @click="c.loadMore()">{{ c.text.more }}</button>
        </div>
      </div>
      <div class="note">{{ c.text.readOnlyNote }}</div>
    </template>
  </section>
</section>`,
};

export function registerArchiveView(app) {
    app.component('clinic-archive-view', ArchiveView);
    return app;
}
