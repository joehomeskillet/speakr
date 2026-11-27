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
import { STATUS_LABELS, formatAudioDuration, formatDictationReference, getOpenMode, resolveStatusSurface } from './worklist-ui.js';

export const RECORDINGS_UI_VERSION = 1;
export const RECORDINGS_PAGE_SIZE = 50;
export const ARCHIVE_STATUS = 'zu_archivieren';
export const FILTER_KEYS = Object.freeze(['author_id', 'pool_id', 'status', 'from', 'to']);
const ACTIVE_STATUS_LABELS = Object.freeze(Object.fromEntries(
    Object.entries(STATUS_LABELS).filter(([status]) => status !== ARCHIVE_STATUS),
));

export const TEXT = Object.freeze({
    title: 'Aufnahmen',
    eyebrow: 'Deine Aufnahmen',
    subtitle: 'Alle Aufnahmen an einem Ort, nach Aufnahmezeit geordnet.',
    searchLabel: 'Aufnahmen durchsuchen',
    searchPlaceholder: 'Person, Gruppe oder Fall-ID suchen …',
    periodAny: 'Gesamter Zeitraum',
    periodToday: 'Heute',
    period7: 'Letzte 7 Tage',
    period30: 'Letzte 30 Tage',
    periodCustom: 'Eigener Zeitraum',
    periodLabel: 'Zeitraum der Aufnahme',
    statusLabel: 'Status der Aufnahme',
    sortLabel: 'Aufnahmedatum',
    sortNewest: 'Neueste zuerst',
    sortSelect: 'Aufnahmen chronologisch sortieren',
    scopeLabel: 'Aufnahmen oder Archiv',
    sortHint: 'Die neuesten Aufnahmen stehen immer zuerst.',
    scopeHint: 'Diese Ansicht zeigt alle Aufnahmen, für die du berechtigt bist.',
    scopeAll: 'Alle Aufnahmen',
    noMatchLoaded: 'Keine Treffer in den geladenen Aufnahmen.',
    searchLoadedHint: 'Die Suche durchsucht nur die bereits geladenen Aufnahmen. Lade weitere Aufnahmen, um mehr zu finden.',
    resultOf: '{n} von {m} Aufnahmen',
    dictation: 'Diktat',
    today: 'Heute',
    yesterday: 'Gestern',
    emptyHint: 'Neue Aufnahmen erscheinen hier, nach Aufnahmezeit geordnet.',
    noMatchHint: 'Passe die Suche oder die Filter an, um Aufnahmen anzuzeigen.',
    loading: 'Laden …',
    loadMore: 'Mehr laden',
    loadingMore: 'Lädt weitere Aufnahmen …',
    refresh: 'Aktualisieren',
    retry: 'Erneut versuchen',
    empty: 'Noch keine Aufnahmen',
    noMatch: 'Keine passenden Aufnahmen',
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
    apply: 'Anwenden',
    reset: 'Zurücksetzen',
    resetFilters: 'Filter zurücksetzen',
    filters: 'Filter',
    roleFilters: 'Gruppe und Diktierende',
    filtersShow: 'Filter anzeigen',
    filtersHide: 'Filter ausblenden',
    authorId: 'Diktiert von',
    poolId: 'Gruppe',
    authorAny: 'Alle Diktierenden',
    poolAny: 'Alle Gruppen',
    statusAny: 'Alle Status',
    range: 'Zeitraum',
    activeFilters: 'Aktive Filter',
    countOne: '1 Aufnahme',
    countMany: '{n} Aufnahmen',
    loadedOnly: 'Auswahl aus den geladenen Aufnahmen',
    from: 'Von',
    to: 'Bis',
    fallbackBadge: 'erfasst',
    fallbackImportedText: 'Importdatum',
    fallbackCreatedText: 'Erstelldatum',
    fallbackImported: 'Ersatzzeit: importiert am. Dies ist nicht die Aufnahmezeit.',
    fallbackCreated: 'Ersatzzeit: erstellt am. Dies ist nicht die Aufnahmezeit.',
    fallbackUnknown: 'Ersatzzeit. Dies ist nicht die Aufnahmezeit.',
});

const WEEKDAYS = Object.freeze(['Sonntag', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag']);
const MONTHS = Object.freeze(['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']);
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "2026-11-20" (Zurich day from the server) to "Freitag, 20.11.2026"; unknown input gives "Ohne Datum". */
export function formatDay(day) {
    const m = DAY.exec(String(day || ''));
    if (!m) return 'Ohne Datum';
    const weekday = WEEKDAYS[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()];
    return weekday + ', ' + m[3] + '.' + m[2] + '.' + m[1];
}

/** "2026-11-20" to "Freitag, 20. November 2026"; unknown input gives "Ohne Datum". */
export function formatLongDay(day) {
    const m = DAY.exec(String(day || ''));
    if (!m) return 'Ohne Datum';
    const weekday = WEEKDAYS[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()];
    return weekday + ', ' + Number(m[3]) + '. ' + MONTHS[+m[2] - 1] + ' ' + m[1];
}

const dayFormat = typeof Intl !== 'undefined'
    ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Zurich', year: 'numeric', month: '2-digit', day: '2-digit' })
    : null;

/** Current Zurich day as "YYYY-MM-DD" (the clock of the browser, never a demo date). */
export function zurichToday(now) {
    const at = now instanceof Date ? now : new Date();
    return dayFormat ? dayFormat.format(at) : at.toISOString().slice(0, 10);
}

/** Shifts a "YYYY-MM-DD" day by whole days. */
export function shiftDay(day, delta) {
    const m = DAY.exec(String(day || ''));
    if (!m) return '';
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + delta)).toISOString().slice(0, 10);
}

/** Period choices of the filter card. The keys "all" and "custom" carry no preset range. */
export const PERIODS = Object.freeze([
    { key: 'all', label: TEXT.periodAny },
    { key: 'today', label: TEXT.periodToday, days: 1 },
    { key: 'week', label: TEXT.period7, days: 7 },
    { key: 'month', label: TEXT.period30, days: 30 },
    { key: 'custom', label: TEXT.periodCustom },
]);

/** { from, to } of a preset period ending today; empty strings for "all" and "custom". */
export function periodRange(key, today) {
    const preset = PERIODS.find((p) => p.key === key && p.days);
    const end = today || zurichToday();
    return preset ? { from: shiftDay(end, 1 - preset.days), to: end } : { from: '', to: '' };
}

/** The period key that describes a from/to pair: "all" without dates, a preset when the range matches, else "custom". */
export function periodOf(from, to, today) {
    if (!from && !to) return 'all';
    const hit = PERIODS.find((p) => {
        if (!p.days) return false;
        const r = periodRange(p.key, today);
        return r.from === from && r.to === to;
    });
    return hit ? hit.key : 'custom';
}

/** Group heading: "Heute" / "Gestern" with the long date as caption, else the long date alone. */
export function dayHeading(day, today) {
    const now = today || zurichToday();
    const long = formatLongDay(day);
    if (day && day === now) return { title: TEXT.today, caption: long };
    if (day && day === shiftDay(now, -1)) return { title: TEXT.yesterday, caption: long };
    return { title: long, caption: '' };
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

export function processingLabel(state) {
    return ({ QUEUED: 'Transkription wartet', PENDING: 'Transkription wartet',
        PROCESSING: 'Transkription läuft', RUNNING: 'Transkription läuft',
        FAILED: 'Transkription fehlgeschlagen', COMPLETED: 'Transkription abgeschlossen',
        SUCCEEDED: 'Transkription abgeschlossen' })[String(state || '').toUpperCase()] || '';
}

const nameOf = (person) => (person && typeof person.display_name === 'string' && person.display_name ? person.display_name : null);

/** Prototype status tint class of a workflow status; unknown statuses keep the neutral first tint. */
const STATUS_TINT = Object.freeze({
    aufgabe: 'neu', neu_angelegt: 'neu',
    vorbereitung_offline: 'in-arbeit', vorbereitung_online: 'in-arbeit', in_bearbeitung: 'in-arbeit',
    zu_schreiben: 'wartet', zu_korrigieren: 'wartet', abzunehmen: 'wartet',
    abgenommen: 'erledigt', fertiggestellt: 'erledigt', zu_archivieren: 'erledigt', zu_loeschen: 'erledigt',
});

/** One contract item to the flat shape the template renders. Reads contract fields only. */
export function toViewRow(item) {
    const source = item && item.time_source;
    const fallback = source !== 'recorded';
    const pools = (Array.isArray(item.staff_pools) ? item.staff_pools : []).map((p) => p && p.name).filter(Boolean);
    const badge = statusBadge(item.workflow_status);
    const seconds = Number.isFinite(item.duration_ms) ? Math.round(item.duration_ms / 1000) : null;
    const time = formatTime(item.sort_time || item.recorded_at);
    const heading = item.case_display ? TEXT.caseId + ' ' + item.case_display : item.capture_label || TEXT.dictation;
    const reference = formatDictationReference(item.dictation_id);
    const authorName = nameOf(item.dictated_by) || TEXT.noAuthor;
    const poolText = pools.length ? pools.join(', ') : TEXT.noPool;
    return {
        key: String(item.dictation_id),
        dictationId: item.dictation_id == null ? '' : String(item.dictation_id),
        reference,
        day: item.day || null,
        time,
        datetime: item.sort_time || item.recorded_at || '',
        statusClass: Object.prototype.hasOwnProperty.call(STATUS_TINT, item.workflow_status) ? STATUS_TINT[item.workflow_status] : 'neu',
        ariaLabel: [heading + (!item.case_display && !item.capture_label && reference ? ' ' + reference : ''), TEXT.author + ' ' + authorName, TEXT.pool + ' ' + poolText, item.day ? formatLongDay(item.day) : '', time, badge.label,
            seconds === null ? '' : formatAudioDuration(seconds)].filter(Boolean).join(', '),
        isFallback: fallback,
        fallbackLabel: fallback ? TEXT.fallbackBadge : '',
        fallbackTip: fallback ? (FALLBACK_TIP[source] || TEXT.fallbackUnknown) : '',
        author: nameOf(item.dictated_by) || TEXT.noAuthor,
        authorId: item.dictated_by && item.dictated_by.id != null ? String(item.dictated_by.id) : '',
        pools,
        poolText: pools.length ? pools.join(', ') : TEXT.noPool,
        leaseHolder: nameOf(item.lease_holder) || TEXT.none,
        status: badge.label,
        statusStyle: badge.style,
        processing: processingLabel(item.processing_status),
        processingActive: ['QUEUED', 'PENDING', 'PROCESSING', 'RUNNING'].includes(String(item.processing_status || '').toUpperCase()),
        duration: seconds === null ? TEXT.none : formatAudioDuration(seconds),
        caseDisplay: item.case_display || '',
        ownTitle: typeof item.own_title === 'string' ? item.own_title.trim() : '',
        workflowStatus: item.workflow_status || null,
        heading,
        captureLabel: item.capture_label || '',
        hasLease: nameOf(item.lease_holder) !== null,
        fallbackText: source === 'imported' ? TEXT.fallbackImportedText : (source === 'created' ? TEXT.fallbackCreatedText : ''),
        searchText: [nameOf(item.dictated_by), pools.join(' '), item.case_display, '#' + item.dictation_id, item.dictation_id].filter(Boolean).join(' ').toLowerCase(),
    };
}

/** Consecutive rows with the same day become one group; server order is kept. */
export function groupByDay(rows, today) {
    const groups = [];
    for (const row of rows) {
        const last = groups[groups.length - 1];
        if (last && last.day === row.day) last.rows.push(row);
        else groups.push({ day: row.day, label: formatDay(row.day), ...dayHeading(row.day, today), rows: [row] });
    }
    for (const g of groups) g.countText = g.rows.length === 1 ? TEXT.countOne : TEXT.countMany.replace('{n}', String(g.rows.length));
    return groups;
}

/** Client-side text search over the loaded rows: every word must occur in author, pool, Fall-ID or ID. */
export function matchesQuery(row, query) {
    const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
    return words.every((w) => row.searchText.includes(w));
}

/**
 * Filter choices by name. No list route of persons or pools is reachable for every role, so
 * the options come from the rows the user may already see (dictated_by and staff_pools of the
 * contract items). The registry only grows while the view lives, so a filter does not make its
 * own choices disappear; the applied value stays in the list.
 */
export function collectOptions(registry, items) {
    for (const item of Array.isArray(items) ? items : []) {
        const person = item && item.dictated_by;
        if (person && person.id != null && nameOf(person)) registry.authors.set(String(person.id), nameOf(person));
        for (const pool of Array.isArray(item && item.staff_pools) ? item.staff_pools : []) {
            if (pool && pool.id != null && pool.name) registry.pools.set(String(pool.id), String(pool.name));
        }
    }
    return registry;
}

const byName = (a, b) => a.label.localeCompare(b.label, 'de-CH');

export function optionList(map, selected) {
    const list = [...map].map(([value, label]) => ({ value, label }));
    const current = String(selected || '').trim();
    if (current && !map.has(current)) list.push({ value: current, label: TEXT.noAuthor + ' (' + current + ')' });
    return list.sort(byName);
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
        error: null, denied: false, stale: false, startNotice: '',
        filters: { ...emptyFilters() }, draft: { ...emptyFilters() }, query: '',
    });
    const registry = { authors: new Map(), pools: new Map() };
    const optionState = reactive({ authors: [], pools: [] });
    const picker = reactive({ author: '', pool: '', period: 'all' });
    const registryOf = (kind) => (kind === 'pool' ? registry.pools : registry.authors);
    const fieldOf = (kind) => (kind === 'pool' ? 'pool_id' : 'author_id');
    // Text shown in a name field: the name of a known ID, else the ID as typed.
    const textOf = (kind, id) => (id ? (registryOf(kind).get(String(id)) || String(id)) : '');
    const refreshPickers = () => {
        picker.author = textOf('author', state.draft.author_id);
        picker.pool = textOf('pool', state.draft.pool_id);
        picker.period = periodOf(state.draft.from, state.draft.to);
    };
    const refreshOptions = () => {
        optionState.authors = optionList(registry.authors, state.filters.author_id);
        optionState.pools = optionList(registry.pools, state.filters.pool_id);
    };
    /** The user typed or picked a name: a known name sets its ID, bare digits are taken as an ID, anything else clears the filter value. */
    function pick(kind, text) {
        const typed = String(text || '').trim();
        picker[kind] = String(text || '');
        const hit = [...registryOf(kind)].find(([, name]) => name === typed);
        state.draft[fieldOf(kind)] = hit ? hit[0] : (/^\d+$/.test(typed) ? typed : '');
    }
    /** The period select was changed: a preset fills the date range, "custom" keeps the dates for the date fields. */
    function setPeriod(key) {
        picker.period = PERIODS.some((p) => p.key === key) ? key : 'all';
        if (picker.period !== 'custom') Object.assign(state.draft, periodRange(picker.period));
    }
    const visibleRows = () => (String(state.query || '').trim() ? state.rows.filter((r) => matchesQuery(r, state.query)) : state.rows);
    let scrollTop = 0;
    let sequence = 0;
    let loadedRevision = client.dictationChanges;
    let loadedPages = 0;
    let disposed = false;

    const hasFilters = () => FILTER_KEYS.some((k) => String(state.filters[k] || '').trim() !== '');

    const hasDraftChanges = () => FILTER_KEYS.some((key) => state.draft[key] !== state.filters[key]);
    const canReset = () => Boolean(hasFilters() || String(state.query || '').trim()
        || picker.pool || picker.author || picker.period !== 'all');

    function persist() {
        memory.snapshot = {
            rows: state.rows.slice(), nextCursor: state.nextCursor, loadedRevision, loadedPages,
            filters: { ...state.filters }, draft: { ...state.draft }, query: state.query, scrollTop,
            options: { authors: [...registry.authors], pools: [...registry.pools] },
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
        const revision = client.dictationChanges;
        const res = await fetchPage(null);
        if (!res) return null;
        state.loading = false;
        state.loaded = true;
        state.loadingMore = false;
        if (res.outcome === 'ok') {
            const data = res.data || {};
            collectOptions(registry, data.items);
            refreshOptions();
            loadedRevision = revision;
            loadedPages = 1;
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
            collectOptions(registry, data.items);
            refreshOptions();
            loadedPages += 1;
            state.rows = mergePage(state.rows, Array.isArray(data.items) ? data.items : []);
            state.nextCursor = data.next_cursor || null;
        } else fail(res);
        persist();
        return res;
    }

    function applyFilters() {
        state.startNotice = '';
        state.filters = { ...state.draft };
        refreshPickers();
        state.nextCursor = null;
        state.rows = [];
        return load();
    }

    function resetFilters() {
        state.query = '';
        state.draft = { ...emptyFilters() };
        refreshPickers();
        return applyFilters();
    }

    async function refreshIfChanged(whileProcessing = null) {
        const revision = client.dictationChanges;
        if (!state.loaded || state.loading || disposed) return null;
        if (whileProcessing ? state.loadingMore || !whileProcessing() : loadedRevision === revision) return null;
        // Rebuild the visible window with fresh cursors. Keep the old rows/scroll until all needed pages arrive.
        if (!whileProcessing) state.loading = true; // A background check keeps the list mounted and focused.
        state.loadingMore = false;
        let rows = [];
        let cursor = null;
        let response;
        let pages = 0;
        do {
            response = await fetchPage(cursor);
            if (!response) return null;
            if (whileProcessing && !whileProcessing() && response.outcome !== 'permission') return null;
            if (response.outcome !== 'ok') {
                state.loading = false;
                fail(response);
                persist();
                return response;
            }
            const data = response.data || {};
            const items = Array.isArray(data.items) ? data.items : [];
            collectOptions(registry, items);
            rows = mergePage(rows, items);
            cursor = data.next_cursor || null;
            pages += 1;
        } while (cursor && pages < loadedPages);
        refreshOptions();
        Object.assign(state, { rows, nextCursor: cursor, loading: false, denied: false, stale: false, error: null });
        loadedRevision = revision;
        loadedPages = pages;
        persist();
        return response;
    }

    /** Restores a remembered state after a re-mount; otherwise loads the first page. */
    async function start() {
        if (disposed) return null;
        const snap = memory.snapshot;
        if (snap) {
            Object.assign(state, {
                rows: snap.rows.slice(), nextCursor: snap.nextCursor, loaded: true,
                filters: { ...snap.filters }, draft: { ...snap.draft }, query: snap.query || '',
            });
            scrollTop = snap.scrollTop || 0;
            if (snap.options) {
                registry.authors = new Map(snap.options.authors);
                registry.pools = new Map(snap.options.pools);
            }
            refreshOptions();
            refreshPickers();
            loadedRevision = snap.loadedRevision;
            loadedPages = snap.loadedPages || 1;
            return refreshIfChanged();
        }
        if (options.startSelection) {
            const selection = await options.startSelection;
            if (disposed || sequence) return null;
            if (selection && !hasDraftChanges()) {
                state.startNotice = selection.notice;
                if (selection.pool) {
                    const { id, name } = selection.pool;
                    registry.pools.set(id, name);
                    state.filters.pool_id = state.draft.pool_id = id;
                    refreshPickers();
                }
            }
        }
        return load();
    }

    function setScroll(value) {
        scrollTop = Number.isFinite(value) && value > 0 ? value : 0;
        if (memory.snapshot) memory.snapshot.scrollTop = scrollTop;
    }

    /** Reports the row to the page with the same payload the worklist uses. */
    function open(row) {
        if (disposed || !row || !row.dictationId) return false;
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
        state, start, refreshIfChanged, load, loadMore, applyFilters, resetFilters, setScroll, open, dispose, options: optionState, picker, pick, setPeriod, visibleRows,
        hasDraftChanges, canReset,
        activeFilterCount: () => FILTER_KEYS.filter((k) => String(state.filters[k] || '').trim() !== '').length,
        countLabel: () => {
            const n = visibleRows().length;
            if (n !== state.rows.length) return TEXT.resultOf.replace('{n}', String(n)).replace('{m}', state.rows.length + (state.nextCursor ? '+' : ''));
            return (n === 1 ? TEXT.countOne : TEXT.countMany.replace('{n}', String(n))) + (state.nextCursor ? '+' : '');
        },
        totalLabel: () => String(state.rows.length) + (state.nextCursor ? '+' : ''),
        hasQuery: () => String(state.query || '').trim() !== '',
        /** A search is active while more pages exist: the search only covers the loaded rows. */
        searchPartial: () => String(state.query || '').trim() !== '' && Boolean(state.nextCursor),
        scrollTop: () => scrollTop, hasFilters, groups: () => groupByDay(visibleRows()),
        text: TEXT, statusOptions: ACTIVE_STATUS_LABELS,
        errorStyle: (() => {
            const s = surfaceFor('error');
            return { background: s.background, color: s.color, border: '1px solid ' + s.border };
        })(),
    };
}
