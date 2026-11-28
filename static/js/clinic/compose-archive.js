/**
 * Composed clinic archive view (V8 P08, PAR-WF-013).
 *
 * The archive is a state filter over the recordings list of the same
 * dictation domain, not a second collection: every request is
 * GET /api/clinic/v1/recordings with status=zu_archivieren (the workflow state
 * TO_ARCHIVE in src/clinic/workflow.py), cursor paging and ID-only filters
 * (archive month, author, pool). Month and author grouping use the server's
 * archive projection. A missing month is not inferred from the recording time.
 *
 * Opening a row emits the worklist-style open event for the existing detail
 * view (P03). Only rows with server-supplied permission and version data can
 * start the separate workflow to resume work.
 * No patient name, title or patient reference is read or rendered. Nothing is
 * written to browser storage; data stays in memory. Colours come from
 * tokens.js, requests from api-client.js.
 */

import { TOKEN_CONTRACT_VERSION, surfaceFor } from './tokens.js';
import { ARCHIVE_STATUS, collectOptions, optionList } from './recordings-ui.js';
import { createWorkflowActions } from './workflow-actions-ui.js';

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
const TARGET = 'min-height: 48px; min-width: 48px';
const INPUT = 'width: 100%; min-width: 0; max-width: 100%; min-height: 48px !important; padding: 10px 11px; border: 1px solid var(--v8-text-2); border-radius: 7px; background-color: var(--surface); color: var(--v8-text); font: inherit';

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
    month: 'Archivmonat',
    unknownMonth: 'Archivmonat nicht bekannt',
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
    subline: 'Archivierte Diktate nach Monat und diktierender Person finden.',
    listHeading: 'Archivierte Diktate',
    statArchived: 'Geladene Einträge',
    statArchivedNote: 'Nach Archivmonat gruppiert',
    statAuthors: 'Diktierende',
    statAuthorsNote: 'Als eigene Metadaten sichtbar',
    statPools: 'Schreibkräfte-Pools',
    statPoolsNote: 'Gruppenzuständigkeit bleibt erhalten',
    statOldest: 'Ältestes Archivdatum',
    statOldestNote: 'Unter den geladenen Einträgen',
    historyNote: 'Bereits bestätigte Ausgaben bleiben unverändert.',
    resumed: 'Das Diktat kann jetzt wieder bearbeitet werden. Bisherige Freigaben bleiben erhalten.',
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
const monthFmt = new Intl.DateTimeFormat('de-CH', { timeZone: ZONE, month: 'long', year: 'numeric' });
const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

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

function archiveAuthorId(row) {
    // Older projections still identify the person by dictated_by.id.
    const id = row?.archive_author_id ?? row?.dictated_by?.id;
    return id == null ? '' : String(id);
}

function authorOptions(registry, selected) {
    const options = optionList(registry, selected);
    const counts = new Map();
    for (const { label } of options) counts.set(label, (counts.get(label) || 0) + 1);
    return options.map((o) => counts.get(o.label) > 1 ? { ...o, label: `${o.label} (Nr. ${o.value})` } : o);
}

export function archiveGroups(rows, authors) {
    const labels = new Map(authors.map(({ value, label }) => [value, label]));
    const months = new Map();
    for (const row of rows) {
        const month = MONTH.test(row.archive_month) ? row.archive_month : '';
        if (!months.has(month)) months.set(month, new Map());
        const people = months.get(month);
        const id = archiveAuthorId(row);
        if (!people.has(id)) people.set(id, { id, label: labels.get(id) || authorCell(row), rows: [] });
        people.get(id).rows.push(row);
    }
    return [...months.keys()].sort().reverse().map((month) => ({
        month,
        label: month ? monthFmt.format(new Date(`${month}-01T00:00:00Z`)) : TEXT.unknownMonth,
        authors: [...months.get(month).values()].sort((a, b) => a.label.localeCompare(b.label, 'de-CH')),
    }));
}
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
    if (!f.from && !f.to) params.set('archive_month', MONTH.test(f.archiveMonth) ? f.archiveMonth : 'all');
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
        const author = archiveAuthorId(row) || row?.dictated_by?.display_name;
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

    const filters = reactive({ archiveMonth: '', from: '', to: '', authorId: '', poolId: '' });
    const rows = ref([]);
    const nextCursor = ref(null);
    const loading = ref(false);
    const error = ref(null);
    const denied = ref(false);
    const choices = reactive({ authors: [], pools: [] });
    const registry = { authors: new Map(), pools: new Map() };
    let generation = 0;
    let disposed = false;

    const filtered = () => Boolean(MONTH.test(filters.archiveMonth) || filters.from || filters.to || filters.authorId || filters.poolId);
    const hasMore = () => nextCursor.value !== null && nextCursor.value !== undefined;

    async function fetchPage(cursor) {
        const seen = (generation += 1);
        loading.value = true;
        const res = await client.request(buildQuery(filters, cursor, pageSize), { method: 'GET' });
        if (disposed || seen !== generation) return null; // superseded by a newer request
        loading.value = false;
        if (res && res.outcome === 'ok') {
            const data = res.data || {};
            const items = Array.isArray(data.items) ? data.items.filter((row) => rowKey(row) !== null) : [];
            for (const row of items) {
                const id = archiveAuthorId(row);
                const name = row.dictated_by?.display_name;
                if (id && (name || !registry.authors.has(id))) registry.authors.set(id, name || TEXT.noAuthor);
            }
            collectOptions(registry, items.map(({ staff_pools }) => ({ staff_pools })));
            choices.authors = authorOptions(registry.authors, filters.authorId);
            choices.pools = optionList(registry.pools, filters.poolId);
            const base = cursor ? rows.value : [];
            const have = new Set(base.map(rowKey));
            rows.value = base.concat(items.filter((r) => !have.has(rowKey(r))));
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
        if (n.archiveMonth !== undefined) Object.assign(filters, { from: '', to: '' });
        for (const key of ['archiveMonth', 'from', 'to', 'authorId', 'poolId']) {
            if (n[key] !== undefined) filters[key] = n[key] === null ? '' : String(n[key]);
        }
        return load();
    }
    const resetFilters = () => setFilters({ archiveMonth: '', authorId: '', poolId: '' });

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
        groups: () => archiveGroups(rows.value, choices.authors),
    };
}

function resumeVersion(row) {
    const version = row.archive_reopen;
    return version && Number.isInteger(version.revision) && version.revision >= 0
        && typeof version.etag === 'string' && version.etag ? version : null;
}

// The row action owns the confirmation and command separately from viewing the archive.
const ArchiveResumeAction = {
    props: ['row', 'client', 'vue', 'disabled'],
    emits: ['resumed', 'refresh'],
    setup(props, { emit }) {
        const { ref, reactive } = props.vue;
        const workflow = createWorkflowActions({ client: props.client, reactive });
        const dialog = ref(null);
        const submitted = ref(false);
        const error = ref('');
        const failed = 'Die Bearbeitung konnte nicht gestartet werden. Bitte versuchen Sie es erneut.';

        function start() {
            if (props.disabled || submitted.value) return;
            const version = resumeVersion(props.row);
            if (!version) return;
            workflow.load({ dictationId: rowKey(props.row), ...version, allowedActions: ['reopen'] });
            error.value = '';
            if (workflow.request('reopen')) dialog.value.showModal();
        }

        function close() {
            if (workflow.state.busy) return;
            workflow.cancel();
            dialog.value.close();
            if (workflow.state.stale) emit('refresh');
        }

        async function confirm() {
            if (workflow.state.busy || submitted.value) return;
            error.value = '';
            try {
                const res = await workflow.confirm();
                if (res?.outcome === 'ok') {
                    dialog.value?.close();
                    submitted.value = true;
                    emit('resumed');
                } else if (res?.outcome === 'conflict') {
                    error.value = 'Das Diktat wurde inzwischen geändert. Bitte laden Sie die Liste neu.';
                } else if (res?.outcome === 'permission') {
                    error.value = 'Sie dürfen dieses Diktat nicht wieder zur Bearbeitung öffnen.';
                } else if (!workflow.state.error) {
                    error.value = failed;
                }
            } catch {
                error.value = failed;
            }
        }

        return { dialog, submitted, error, state: workflow.state, start, close, confirm };
    },
    template: `
<div style="margin-top: var(--sp-2)">
  <button v-if="!submitted" type="button" class="button small" style="${TARGET}; white-space: nowrap" :disabled="disabled" data-testid="archive-reopen" @click="start">Bearbeiten</button>
  <dialog ref="dialog" class="panel" style="background: var(--surface); margin: auto" aria-label="Diktat weiterbearbeiten" @cancel.prevent="close">
    <div class="dialoghead"><h2 style="${H2}">Diktat wieder öffnen?</h2></div>
    <p>Sie öffnen das Diktat zur Bearbeitung in einer neuen Fassung. Bisherige Freigaben bleiben erhalten.</p>
    <form :aria-busy="state.busy" @submit.prevent="confirm">
      <label style="${FIELD}; margin-top: var(--sp-3)">
        <span style="${LABEL}">Warum soll das Diktat weiterbearbeitet werden?</span>
        <textarea v-model="state.reason" style="${INPUT}" data-testid="archive-reopen-reason" rows="3" maxlength="2000" autofocus required :readonly="state.busy || state.stale"></textarea>
      </label>
      <p v-if="error || state.error" class="note warning" role="alert">{{ error || state.error }}</p>
      <div class="actions">
        <button type="button" class="button" style="${TARGET}" :disabled="state.busy" @click="close">{{ state.stale ? 'Schliessen und Liste neu laden' : 'Abbrechen' }}</button>
        <button type="submit" class="button primary" style="${TARGET}" :disabled="state.busy || state.stale" data-testid="archive-reopen-confirm">{{ state.busy ? 'Bitte warten …' : 'Bearbeitung starten' }}</button>
      </div>
    </form>
  </dialog>
</div>`,
};

export const ArchiveView = {
    name: 'ClinicArchiveView',
    components: { ArchiveResumeAction },
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
        const status = V.ref(null);
        const resumed = V.ref(false);
        async function onResumed() {
            resumed.value = true;
            V.nextTick(() => status.value?.focus());
            await c.load();
        }
        if (typeof V.onMounted === 'function' && props.autoStart) V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c, status, resumed, onResumed, canResume: resumeVersion };
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
      <button type="button" class="button" style="${TARGET}" data-testid="archive-back" @click="c.back()">{{ c.text.back }}</button>
      <button type="button" class="button quiet" style="${TARGET}" data-testid="archive-remove-filter" @click="c.back()">{{ c.text.removeFilter }}</button>
    </div>
  </div>
  <div class="stats" data-testid="archive-stats">
    <div class="stat"><span>{{ c.text.statArchived }}</span><strong>{{ c.summary().count }}</strong><small>{{ c.text.statArchivedNote }}</small></div>
    <div class="stat"><span>{{ c.text.statAuthors }}</span><strong>{{ c.summary().authors }}</strong><small>{{ c.text.statAuthorsNote }}</small></div>
    <div class="stat"><span>{{ c.text.statPools }}</span><strong>{{ c.summary().pools }}</strong><small>{{ c.text.statPoolsNote }}</small></div>
    <div class="stat"><span>{{ c.text.statOldest }}</span><strong>{{ c.summary().oldest === c.text.noValue ? 'Nicht bekannt' : c.summary().oldest }}</strong><small>{{ c.text.statOldestNote }}</small></div>
  </div>
  <section class="panel" :aria-label="c.text.listHeading">
    <h2 style="${H2}">{{ c.text.listHeading }}</h2>
    <p v-if="resumed" ref="status" tabindex="-1" role="status" class="note" data-testid="archive-resumed">{{ c.text.resumed }}</p>
    <form class="filterbar" role="search" :aria-label="c.text.filters" data-testid="archive-filters" @submit.prevent="c.load()">
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-month">{{ c.text.month }}</label><input style="${INPUT}" id="cav-month" type="month" v-model="c.filters.archiveMonth" aria-describedby="cav-month-hint" pattern="[0-9]{4}-(0[1-9]|1[0-2])" placeholder="JJJJ-MM" data-testid="filter-archive-month"></div>
      <div style="${FIELD}; flex: 1 1 12rem; margin: 0"><label style="${LABEL}" for="cav-author">{{ c.text.author }}</label><select style="${INPUT}" id="cav-author" v-model="c.filters.authorId" data-testid="filter-author"><option value="">Alle Diktierenden</option><option v-for="o in c.choices.authors" :key="o.value" :value="o.value">{{ o.label }}</option></select></div>
      <div style="${FIELD}; flex: 1 1 150px; margin: 0"><label style="${LABEL}" for="cav-pool">{{ c.text.pool }}</label><select style="${INPUT}" id="cav-pool" v-model="c.filters.poolId" data-testid="filter-pool"><option value="">Alle Pools</option><option v-for="o in c.choices.pools" :key="o.value" :value="o.value">{{ o.label }}</option></select></div>
      <p id="cav-month-hint" class="hint">Ohne Monat sehen Sie alle Monate. Die Auswahl der Personen stammt aus den geladenen Aufnahmen.</p>
      <div class="actions text-sm">
        <button type="submit" class="button primary" style="${TARGET}">{{ c.text.apply }}</button>
        <button type="button" class="button" style="${TARGET}" @click="c.resetFilters()">{{ c.text.reset }}</button>
      </div>
    </form>
    <div v-if="c.denied.value" role="alert" class="note warning" data-testid="archive-denied">{{ c.text.denied }}</div>
    <template v-else>
      <div v-if="c.error.value" role="alert" class="note warning" data-testid="archive-error">
        {{ c.error.value }} {{ c.text.stale }}
        <button type="button" class="button small" style="${TARGET}; margin-left: 8px" @click="c.load()">{{ c.text.retry }}</button>
      </div>
      <div v-if="c.loading.value && !c.rows.value.length" class="state-box" role="status" aria-live="polite" data-testid="archive-loading">{{ c.text.loading }}</div>
      <p v-else-if="!c.rows.value.length && !c.error.value" class="empty" data-testid="archive-empty">{{ c.emptyText() }}</p>
      <div v-else-if="c.rows.value.length">
        <section v-for="month in c.groups()" :key="month.month" :data-archive-month="month.month" style="margin-bottom: var(--sp-4)">
        <h3 style="${H2}">{{ month.label }}</h3>
        <div class="table-wrap">
          <table :aria-label="month.label" style="white-space: normal">
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
            <tbody v-for="person in month.authors" :key="person.id" :data-author-id="person.id">
              <tr><th colspan="6" scope="rowgroup" style="background: var(--v8-s1); overflow-wrap: anywhere">{{ person.label }}</th></tr>
              <tr v-for="row in person.rows" :key="c.key(row)" data-row :data-id="c.key(row)">
                <td>
                  <strong>{{ c.recorded(row).text }}</strong>
                  <small v-if="c.recorded(row).mark" :title="c.recorded(row).hint" :aria-label="c.recorded(row).mark + ': ' + c.recorded(row).hint">({{ c.recorded(row).mark }})</small>
                </td>
                <td data-cell="author" style="overflow-wrap: anywhere">
                  <span class="hidden md:inline">{{ person.label }}</span>
                  <div class="md:hidden">
                    <small>{{ c.pool(row) }}</small>
                    <div><small style="overflow-wrap: normal">{{ c.retention(row) }}</small></div>
                  </div>
                  <archive-resume-action v-if="canResume(row)" :key="canResume(row).etag" :row="row" :client="client" :vue="c.vue" :disabled="c.loading.value || !!c.error.value" @resumed="onResumed" @refresh="c.load()"></archive-resume-action>
                </td>
                <td data-cell="pool" class="hidden md:table-cell" style="overflow-wrap: anywhere">{{ c.pool(row) }}</td>
                <td data-cell="archived" class="hidden md:table-cell">{{ c.archived(row) }}</td>
                <td data-cell="retention" class="hidden md:table-cell"><span class="tag" :style="c.retentionStyle(row)">{{ c.retention(row) }}</span></td>
                <td><button type="button" class="button small" style="${TARGET}" data-testid="archive-open" @click="c.open(row)">{{ c.text.open }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        </section>
        <div v-if="c.hasMore()" class="actions" style="justify-content: center; margin-top: 14px">
          <button type="button" class="button" style="${TARGET}" :disabled="c.loading.value" data-testid="archive-more" @click="c.loadMore()">{{ c.text.more }}</button>
        </div>
      </div>
      <div class="note">{{ c.text.historyNote }}</div>
    </template>
  </section>
</section>`,
};

export function registerArchiveView(app) {
    app.component('clinic-archive-view', ArchiveView);
    return app;
}
