/**
 * Recordings view model (V8 P01 Aufnahmen): request building, paging, day
 * grouping, row formatting and the in-memory return state.
 *
 * Data comes from GET /api/clinic/v1/recordings (contract of package v8-01).
 * The server sorts by recording time descending; this module keeps that order,
 * groups consecutive rows by the Zurich day the server supplies and never
 * re-sorts. Only the fields named in the contract are read: no patient name,
 * title or notes. Nothing is written to browser storage or to URLs; filter,
 * loaded pages and scroll offset live in a memory object handed in by the
 * page (or the module default) so they survive a re-mount.
 */

import { TOKEN_CONTRACT_VERSION, TokenContractError, surfaceFor } from './tokens.js';
import { STATUS_LABELS, formatAudioDuration, getOpenMode, resolveStatusSurface } from './worklist-ui.js';

export const RECORDINGS_UI_VERSION = 1;
export const RECORDINGS_PAGE_SIZE = 50;
export const FILTER_KEYS = Object.freeze(['author_id', 'pool_id', 'status', 'from', 'to']);

export const TEXT = Object.freeze({
    title: 'Aufnahmen',
    loading: 'Laden …',
    loadMore: 'Mehr laden',
    loadingMore: 'Lädt weitere Aufnahmen …',
    refresh: 'Aktualisieren',
    retry: 'Erneut versuchen',
    empty: 'Noch keine Aufnahmen vorhanden.',
    noMatch: 'Keine Treffer für die gewählten Filter.',
    failed: 'Die Aufnahmen konnten nicht geladen werden.',
    stale: 'Die angezeigten Daten sind möglicherweise veraltet.',
    denied: 'Keine Berechtigung für diese Ansicht.',
    open: 'Öffnen',
    time: 'Aufnahmezeit',
    author: 'Diktiert von',
    pool: 'Schreibkräfte-Pool',
    lease: 'Übernahme',
    status: 'Status',
    duration: 'Dauer',
    caseId: 'Fall-ID',
    noPool: 'Kein Pool zugewiesen',
    noAuthor: 'Unbekannt',
    none: '–',
    apply: 'Filter anwenden',
    reset: 'Filter zurücksetzen',
    filters: 'Filter',
    authorId: 'Diktierende (ID)',
    poolId: 'Pool (ID)',
    statusAny: 'Alle Status',
    from: 'Von',
    to: 'Bis',
    fallbackBadge: 'erfasst',
    fallbackImported: 'Ersatzzeit: importiert am. Dies ist nicht die Aufnahmezeit.',
    fallbackCreated: 'Ersatzzeit: erstellt am. Dies ist nicht die Aufnahmezeit.',
    fallbackUnknown: 'Ersatzzeit. Dies ist nicht die Aufnahmezeit.',
});

const WEEKDAYS = Object.freeze(['Sonntag', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag']);
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "2026-11-20" (Zurich day from the server) to "Freitag, 20.11.2026"; unknown input gives "Ohne Datum". */
export function formatDay(day) {
    const m = DAY.exec(String(day || ''));
    if (!m) return 'Ohne Datum';
    const weekday = WEEKDAYS[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()];
    return weekday + ', ' + m[3] + '.' + m[2] + '.' + m[1];
}

const timeFormat = typeof Intl !== 'undefined'
    ? new Intl.DateTimeFormat('de-CH', { timeZone: 'Europe/Zurich', hour: '2-digit', minute: '2-digit', hour12: false })
    : null;

/** UTC instant to "HH:MM" in Europe/Zurich; unparsable input gives "–". */
export function formatTime(value) {
    const at = new Date(value);
    if (!value || Number.isNaN(at.getTime()) || !timeFormat) return TEXT.none;
    return timeFormat.format(at);
}

const FALLBACK_TIP = Object.freeze({ imported: TEXT.fallbackImported, created: TEXT.fallbackCreated });

export function statusBadge(status) {
    try {
        const s = resolveStatusSurface(status, { version: TOKEN_CONTRACT_VERSION });
        return { label: s.label, style: { background: s.background, color: s.color, border: '1px solid ' + s.border } };
    } catch (error) {
        if (!(error instanceof TokenContractError)) throw error;
        const s = surfaceFor('normal');
        const key = String(status || '').toLowerCase();
        return { label: STATUS_LABELS[key] || key || TEXT.none, style: { background: s.background, color: s.color, border: '1px solid ' + s.border } };
    }
}

const nameOf = (person) => (person && typeof person.display_name === 'string' && person.display_name ? person.display_name : null);

/** One contract item to the flat shape the template renders. Reads contract fields only. */
export function toViewRow(item) {
    const source = item && item.time_source;
    const fallback = source !== 'recorded';
    const pools = (Array.isArray(item.staff_pools) ? item.staff_pools : []).map((p) => p && p.name).filter(Boolean);
    const badge = statusBadge(item.workflow_status);
    const seconds = Number.isFinite(item.duration_ms) ? Math.round(item.duration_ms / 1000) : null;
    return {
        key: String(item.dictation_id),
        dictationId: String(item.dictation_id),
        day: item.day || null,
        time: formatTime(item.sort_time || item.recorded_at),
        isFallback: fallback,
        fallbackLabel: fallback ? TEXT.fallbackBadge : '',
        fallbackTip: fallback ? (FALLBACK_TIP[source] || TEXT.fallbackUnknown) : '',
        author: nameOf(item.dictated_by) || TEXT.noAuthor,
        pools,
        poolText: pools.length ? pools.join(', ') : TEXT.noPool,
        leaseHolder: nameOf(item.lease_holder) || TEXT.none,
        status: badge.label,
        statusStyle: badge.style,
        processing: item.processing_status || '',
        duration: seconds === null ? TEXT.none : formatAudioDuration(seconds),
        caseDisplay: item.case_display || '',
        workflowStatus: item.workflow_status || null,
    };
}

/** Consecutive rows with the same day become one group; server order is kept. */
export function groupByDay(rows) {
    const groups = [];
    for (const row of rows) {
        const last = groups[groups.length - 1];
        if (last && last.day === row.day) last.rows.push(row);
        else groups.push({ day: row.day, label: formatDay(row.day), rows: [row] });
    }
    return groups;
}

/** Query path for one page; empty filters are left out. */
export function buildPath(filters, cursor, limit) {
    const params = new URLSearchParams();
    params.set('limit', String(limit || RECORDINGS_PAGE_SIZE));
    if (cursor) params.set('cursor', cursor);
    for (const key of FILTER_KEYS) {
        const value = filters && filters[key] != null ? String(filters[key]).trim() : '';
        if (value) params.set(key, value);
    }
    return '/recordings?' + params.toString();
}

/** Appends a page and skips dictations that are already loaded. */
export function mergePage(existing, incoming) {
    const seen = new Set(existing.map((r) => r.key));
    const next = existing.slice();
    for (const item of incoming) {
        if (!item || item.dictation_id == null) continue;
        const row = toViewRow(item);
        if (seen.has(row.key)) continue;
        seen.add(row.key);
        next.push(row);
    }
    return next;
}

export const emptyFilters = () => Object.freeze({ author_id: '', pool_id: '', status: '', from: '', to: '' });

/** Return state holder: lives in memory only, never in browser storage. */
export function createRecordingsMemory() {
    return { snapshot: null };
}
const defaultMemory = createRecordingsMemory();

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

/**
 * Controller behind the recordings view. `vue` is optional; without it plain
 * holders are used (unit tests).
 */
export function createRecordingsController(options) {
    const client = options && options.client;
    const user = (options && options.currentUser) || {};
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const V = options.vue || (typeof window !== 'undefined' ? window.Vue : null) || null;
    const reactive = (V && V.reactive) || ((o) => o);
    const memory = options.memory || defaultMemory;
    const pageSize = options.pageSize || RECORDINGS_PAGE_SIZE;

    const state = reactive({
        rows: [], nextCursor: null, loaded: false, loading: false, loadingMore: false,
        error: null, denied: false, stale: false,
        filters: { ...emptyFilters() }, draft: { ...emptyFilters() },
    });
    let scrollTop = 0;
    let sequence = 0;
    let disposed = false;

    const hasFilters = () => FILTER_KEYS.some((k) => String(state.filters[k] || '').trim() !== '');

    function persist() {
        memory.snapshot = {
            rows: state.rows.slice(), nextCursor: state.nextCursor,
            filters: { ...state.filters }, draft: { ...state.draft }, scrollTop,
        };
    }

    function fail(res) {
        if (res && res.outcome === 'permission') {
            Object.assign(state, { denied: true, rows: [], nextCursor: null, error: null });
        } else {
            state.error = (res && res.message) || TEXT.failed;
            state.stale = state.rows.length > 0;
        }
    }

    async function fetchPage(cursor) {
        const mine = ++sequence;
        const res = await client.request(buildPath(state.filters, cursor, pageSize), { method: 'GET' });
        if (disposed || mine !== sequence) return null; // superseded by a newer request
        return res;
    }

    /** Loads the first page for the applied filters; replaces rows and cursor. */
    async function load() {
        Object.assign(state, { loading: true, error: null, denied: false });
        const res = await fetchPage(null);
        if (!res) return null;
        state.loading = false;
        state.loaded = true;
        state.loadingMore = false;
        if (res.outcome === 'ok') {
            const data = res.data || {};
            state.rows = mergePage([], Array.isArray(data.items) ? data.items : []);
            state.nextCursor = data.next_cursor || null;
            state.stale = false;
        } else fail(res);
        scrollTop = 0;
        persist();
        return res;
    }

    /** Appends the page behind next_cursor. */
    async function loadMore() {
        if (!state.nextCursor || state.loadingMore || state.loading) return null;
        state.loadingMore = true;
        state.error = null;
        const res = await fetchPage(state.nextCursor);
        if (!res) return null;
        state.loadingMore = false;
        if (res.outcome === 'ok') {
            const data = res.data || {};
            state.rows = mergePage(state.rows, Array.isArray(data.items) ? data.items : []);
            state.nextCursor = data.next_cursor || null;
        } else fail(res);
        persist();
        return res;
    }

    function applyFilters() {
        state.filters = { ...state.draft };
        state.nextCursor = null;
        state.rows = [];
        return load();
    }

    function resetFilters() {
        state.draft = { ...emptyFilters() };
        return applyFilters();
    }

    /** Restores a remembered state after a re-mount; otherwise loads the first page. */
    async function start() {
        if (disposed) return null;
        const snap = memory.snapshot;
        if (snap) {
            Object.assign(state, {
                rows: snap.rows.slice(), nextCursor: snap.nextCursor, loaded: true,
                filters: { ...snap.filters }, draft: { ...snap.draft },
            });
            scrollTop = snap.scrollTop || 0;
            return null;
        }
        return load();
    }

    function setScroll(value) {
        scrollTop = Number.isFinite(value) && value > 0 ? value : 0;
        if (memory.snapshot) memory.snapshot.scrollTop = scrollTop;
    }

    /** Reports the row to the page with the same payload the worklist uses. */
    function open(row) {
        if (disposed || !row) return false;
        persist();
        if (typeof options.onOpen === 'function') {
            options.onOpen({ dictationId: row.dictationId, mode: getOpenMode({ status: row.workflowStatus }, user.role) });
        }
        return true;
    }

    function dispose() {
        if (disposed) return;
        persist();
        disposed = true;
        sequence += 1;
    }

    return {
        state, start, load, loadMore, applyFilters, resetFilters, setScroll, open, dispose,
        scrollTop: () => scrollTop, hasFilters, groups: () => groupByDay(state.rows),
        text: TEXT, statusOptions: STATUS_LABELS,
    };
}
