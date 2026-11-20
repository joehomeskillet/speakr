/**
 * Composed clinic worklist view (PAR-WF-001, PAR-REC-006).
 *
 * List area: worklist-ui (rows, selection), worklist-refresh-ui (refresh and
 * change polling), worklist-filters-ui (named filters, metadata search) and
 * worklist-layout-ui (columns, sort, grouping). Detail area for the selected
 * dictation: lease, workflow actions, protection, acceptance policy and
 * history. Nothing from those modules is re-implemented here.
 *
 * Owner decision W16: patients are referenced by Fall-ID only. The contract has
 * no Fall-ID field on Dictation yet, so this view shows no Fall-ID column or
 * filter and renders no patient name, title, notes or recipient. The
 * dictation-properties-ui panel is not mounted: it is a validate-only form with
 * title and recipient inputs and is not bound to the selected dictation.
 * Role separation (V8 P02): "Diktiert von", "Schreibkräfte-Pool" and "Übernommen
 * von" are three separate columns fed by the additive row fields dictated_by,
 * staff_pools and lease_holder (staff display names only). The detail panel is
 * rendered only while a row is selected; without a selection no column is reserved.
 * Clinical data stays in memory; the only browser storage is the last filter
 * id kept by worklist-filters-ui. Colours come from tokens.js, requests from
 * api-client.js.
 */

import { TOKEN_CONTRACT_VERSION, TokenContractError, surfaceFor } from './tokens.js';
import { ROLE_AUTHOR, STATUS_LABELS, formatAudioDuration, formatPriority, getOpenMode, resolveStatusSurface } from './worklist-ui.js';
import { formatLastUpdate, useWorklistRefresh } from './worklist-refresh-ui.js';
import { WorklistFiltersPanel, createWorklistFilters } from './worklist-filters-ui.js';
import { WorklistLayoutControls, useWorklistLayout } from './worklist-layout-ui.js';
import { DictationProtectionComponent } from './dictation-protection-ui.js';
import { WORKFLOW_ACTIONS_TEMPLATE, createWorkflowActions } from './workflow-actions-ui.js';
import { acceptancePolicyComponent } from './acceptance-policy-ui.js';
import { createHistoryComponent } from './history-ui.js';
import { createLeaseController, createLeaseStatusComponent } from './lease-ui.js';

export const COMPOSE_WORKLIST_VERSION = 1;

/** V8 look for the hosted lease and workflow components (their own templates are not owned here); scoped to .v8-hosted. */
export const HOSTED_CSS = [
    '.v8-ui .v8-hosted{display:grid;gap:12px;min-width:0}',
    '.v8-ui .v8-hosted .clinic-lease{display:grid;gap:8px;padding:12px 14px;border:1px solid var(--line);background:var(--v8-s1);border-radius:8px;font-size:12px;line-height:1.6}',
    '.v8-ui .v8-hosted .clinic-lease p{margin:0}',
    '.v8-ui .v8-hosted .clinic-lease button,.v8-ui .v8-hosted .clinic-workflow-actions button{background:var(--surface);color:var(--v8-text-2);border:1px solid var(--line);border-radius:8px;padding:9px 11px;font-size:11px;font-weight:750;min-height:40px;cursor:pointer}',
    '.v8-ui .v8-hosted .clinic-lease button:hover:not(:disabled),.v8-ui .v8-hosted .clinic-workflow-actions button:hover:not(:disabled){background:var(--v8-s1)}',
    '.v8-ui .v8-hosted .clinic-lease button:disabled,.v8-ui .v8-hosted .clinic-workflow-actions button:disabled{opacity:.55;cursor:not-allowed}',
    '.v8-ui .v8-hosted .clinic-workflow-actions{display:grid;gap:10px}',
    '.v8-ui .v8-hosted .clinic-workflow-actions h2{margin:0;font-size:12px;font-weight:750;color:var(--v8-text)}',
    '.v8-ui .v8-hosted .clinic-workflow-actions [role="group"]{display:flex;flex-wrap:wrap;gap:8px;margin:0}',
    '.v8-ui .v8-hosted .clinic-workflow-actions h3{flex:1 0 100%;margin:0;font-size:11px}',
    '.v8-ui .v8-hosted .clinic-workflow-actions form{display:grid;gap:8px;margin:0}',
    '.v8-ui .viewcontrols select,.v8-ui .toolbar-right select{font-weight:600;min-height:34px}',
    '@media (min-width:641px){.v8-ui .viewcontrols select,.v8-ui .toolbar-right select{font-size:12px}}',
    '.v8-ui .searchwrap input{font-weight:400}',
    '.v8-ui .v8-empty{border:0;border-radius:0;min-height:260px}',
    '.v8-ui .v8-empty h2{font-weight:750}',
    '.v8-ui .v8-empty p{color:var(--v8-muted)}',
].join('\n');

/** Adds the stylesheet above once per document. */
export function ensureHostedStyles(doc) {
    const d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d || !d.head || typeof d.createElement !== 'function' || d.getElementById('clinic-v8-hosted-css')) return;
    const el = d.createElement('style');
    el.id = 'clinic-v8-hosted-css';
    el.textContent = HOSTED_CSS;
    d.head.appendChild(el);
}
export const PAGE_SIZE = 20;

export const TEXT = Object.freeze({
    title: 'Arbeitsliste',
    refresh: 'Aktualisieren',
    retry: 'Erneut versuchen',
    paused: 'Die automatische Aktualisierung ist pausiert, solange ein Filter aktiv ist oder eine andere Seite angezeigt wird.',
    loading: 'Laden …',
    empty: 'Keine Diktate vorhanden.',
    noMatch: 'Keine Treffer für den gewählten Filter oder die Suche.',
    denied: 'Keine Berechtigung für diese Aktion.',
    failed: 'Die Arbeitsliste konnte nicht geladen werden.',
    prev: 'Vorherige Seite',
    next: 'Nächste Seite',
    keys: 'Tastatur: Pfeil hoch und runter wählt ein Diktat, Eingabetaste öffnet es, Esc schliesst die Details.',
    open: 'Öffnen',
    detail: 'Diktat',
    protection: 'Schutz',
    acceptance: 'Pflichtfreigabe',
    history: 'Verlauf',
    close: 'Details schliessen',
    noPool: 'Kein Pool zugewiesen',
    eyebrow: 'Aufnahmenverwaltung',
    subline: 'Diktate nach Schreibkräfte-Pool und diktierender Person bearbeiten.',
    kpiAll: 'Alle Diktate',
    kpiOpen: 'Offen',
    kpiWorking: 'In Bearbeitung',
    kpiDone: 'Abgeschlossen',
    summary: 'Zusammenfassung',
    listTitle: 'Diktate',
    sortLabel: 'Sortierung',
    sortNone: 'Standardreihenfolge',
    sortBy: 'Sortiert nach',
    dictation: 'Diktat / Aufnahme',
    recording: 'Aufnahme',
    selectAll: 'Alle sichtbaren Einträge auswählen',
    selectRow: 'Eintrag auswählen',
    selected: 'ausgewählt',
    clearSelection: 'Auswahl aufheben',
    pagerFor: 'Seite',
    hintSelect: 'Eintrag auswählen, um Details anzuzeigen',
    updated: 'Aktualisiert',
    refreshing: 'Aktualisiere …',
    chips: 'Statusfilter',
    detailInfo: 'Eckdaten',
});

// Columns the layout may order, sort and group. Title, notes and recipient are
// left out on purpose (W16); no Fall-ID column until the contract provides one.
// The order is the default column order of the V8 worklist (Status, Priorität, Pool, Diktiert von, Quelle, Dauer, ...).
export const LIST_COLUMNS = Object.freeze([
    { key: 'status', label: 'Status' },
    { key: 'priority', label: 'Priorität' },
    { key: 'staff_pools', label: 'Schreibkräfte-Pool' },
    { key: 'dictated_by', label: 'Diktiert von' },
    { key: 'source_kind', label: 'Quelle' },
    { key: 'duration_seconds', label: 'Dauer' },
    { key: 'created_at', label: 'Erstellt' },
    { key: 'deadline_at', label: 'Frist' },
    { key: 'lease_holder', label: 'Übernommen von' },
    { key: 'assignee_user_id', label: 'Zugewiesen an' },
    { key: 'assigned_at', label: 'Zugewiesen' },
].map((c) => Object.freeze(c)));

// Column key to the width class of the V8 worklist table (clinic-v8.css).
const COL_CLASS = Object.freeze({
    status: 'statuscol', priority: 'priocol', staff_pools: 'poolcol', dictated_by: 'dictatorcol',
    source_kind: 'sourcecol', duration_seconds: 'timecol', created_at: 'datecol',
});

// Columns shown by default (the V8 prototype table).
const V8_COLUMNS = Object.freeze(['status', 'priority', 'staff_pools', 'dictated_by', 'source_kind', 'duration_seconds', 'created_at']);

// Contract enum of the recording source (WorklistRow.source_kind) as German labels.
export const SOURCE_LABELS = Object.freeze({
    device: 'Diktiergerät', browser: 'Mikrofon', manual_file: 'Upload', simulator: 'Simulator', audio: 'Audio', document: 'Dokument',
});

export function sourceLabel(value) {
    if (value === null || value === undefined || value === '') return '–';
    return Object.prototype.hasOwnProperty.call(SOURCE_LABELS, value) ? SOURCE_LABELS[value] : String(value);
}

const OPEN_STATUSES = Object.freeze(['aufgabe', 'neu_angelegt', 'vorbereitung_offline', 'vorbereitung_online', 'zu_schreiben']);
const WORKING_STATUSES = Object.freeze(['in_bearbeitung', 'zu_korrigieren', 'abzunehmen']);
const DONE_STATUSES = Object.freeze(['abgenommen', 'fertiggestellt', 'zu_archivieren', 'zu_loeschen']);

/** KPI and chip bucket of a workflow status: open, working or done. An unknown status counts as open. */
export function statusBucket(status) {
    const key = String(status || '').trim().toLowerCase();
    if (WORKING_STATUSES.includes(key)) return 'working';
    if (DONE_STATUSES.includes(key)) return 'done';
    return 'open';
}

/** Pill colour class of the V8 status pill (neu, in-arbeit, wartet, erledigt). */
export function statusTone(status) {
    const key = String(status || '').trim().toLowerCase();
    if (key === 'abzunehmen') return 'wartet';
    const bucket = statusBucket(key);
    return bucket === 'done' ? 'erledigt' : bucket === 'working' ? 'in-arbeit' : 'neu';
}

/** Per-bucket counts of the loaded rows. */
export function countBuckets(rows) {
    const out = { all: 0, open: 0, working: 0, done: 0 };
    for (const row of rows || []) {
        if (!row) continue;
        out.all += 1;
        out[statusBucket(row.status)] += 1;
    }
    return out;
}

// Sort choices of the toolbar select: value is "key:direction" (priority ascending puts Dringend first).
export const SORT_CHOICES = Object.freeze([
    { value: '', label: 'Standardreihenfolge' },
    { value: 'created_at:desc', label: 'Neueste zuerst' },
    { value: 'created_at:asc', label: 'Älteste zuerst' },
    { value: 'priority:asc', label: 'Priorität' },
    { value: 'deadline_at:asc', label: 'Frist' },
    { value: 'duration_seconds:desc', label: 'Längste zuerst' },
]);

export const ROLE_KEYS = Object.freeze(['dictated_by', 'staff_pools', 'lease_holder']);

/** Text of a role value: a person shows the staff display name (else the ID), pools show their names. */
export function roleText(key, value) {
    if (key === 'staff_pools') {
        const names = Array.isArray(value) ? value.map((p) => (p && (p.name || p.id)) || '').filter(Boolean) : [];
        return names.length ? names.join(', ') : TEXT.noPool;
    }
    if (value === null || value === undefined || value === '') return '–';
    if (typeof value === 'object') return String(value.display_name || value.id || '–');
    return String(value);
}

export function rowKey(row) {
    const id = row && (row.dictation_id != null ? row.dictation_id : row.id);
    return id == null ? null : String(id);
}

/** Next row in display order; no wrap-around. An unknown current row gives the first row. */
export function neighborRow(rows, current, delta) {
    if (!rows.length) return null;
    const at = rows.findIndex((r) => rowKey(r) === rowKey(current));
    return at === -1 ? rows[0] : rows[at + delta] || null;
}

function groupLabel(key, value) {
    const name = (LIST_COLUMNS.find((c) => c.key === key) || {}).label || key;
    if (ROLE_KEYS.includes(key)) return name + ': ' + roleText(key, value);
    if (value === null || value === undefined || value === '') return name + ': –';
    if (key === 'status') return name + ': ' + (STATUS_LABELS[value] || value);
    return name + ': ' + (key === 'priority' ? formatPriority(value) : value);
}

/** Flattens layout groups into display items: group headings and rows in order. */
export function flattenGroups(groups, groupBy) {
    const items = [];
    const walk = (list) => {
        for (const group of list) {
            const level = group.path.length - 1;
            if (level >= 0) {
                items.push({ kind: 'group', depth: level, key: 'g:' + JSON.stringify(group.path), label: groupLabel(groupBy[level], group.path[level]) });
            }
            if (group.children.length) walk(group.children);
            else for (const row of group.rows) items.push({ kind: 'row', key: 'r:' + rowKey(row), row });
        }
    };
    walk(groups);
    return items;
}

export function statusBadge(row) {
    try {
        const s = resolveStatusSurface(row.status, { version: TOKEN_CONTRACT_VERSION });
        return { label: s.label, style: { background: s.background, color: s.color, border: '1px solid ' + s.border } };
    } catch (error) {
        if (!(error instanceof TokenContractError)) throw error;
        const s = surfaceFor('normal');
        const key = String(row.status || '').toLowerCase();
        return { label: STATUS_LABELS[key] || key || '–', style: { background: s.background, color: s.color, border: '1px solid ' + s.border } };
    }
}

const DATE_COLUMNS = Object.freeze(['deadline_at', 'created_at', 'assigned_at']);
const dateTimeFormat = typeof Intl !== 'undefined'
    ? new Intl.DateTimeFormat('de-CH', { timeZone: 'Europe/Zurich', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
    : null;

/** UTC instant to "20.11.2026, 07:21" in Europe/Zurich; unparsable input is shown as received. */
export function formatDateTime(value) {
    const at = new Date(value);
    if (!dateTimeFormat || Number.isNaN(at.getTime())) return String(value);
    return dateTimeFormat.format(at);
}

/** Cell text for one layout column. */
export function dictationLabel(row) {
    return row?.created_at ? 'Diktat vom ' + formatDateTime(row.created_at) : 'Diktat';
}

function entryCount(count) {
    return count + (count === 1 ? ' Eintrag' : ' Einträge');
}

export function cellText(row, key) {
    const value = row[key];
    if (DATE_COLUMNS.includes(key) && value !== null && value !== undefined && value !== '') return formatDateTime(value);
    if (key === 'status') return statusBadge(row).label;
    if (key === 'priority') return formatPriority(value);
    if (key === 'duration_seconds') return formatAudioDuration(value);
    if (key === 'source_kind') return sourceLabel(value);
    if (ROLE_KEYS.includes(key)) return roleText(key, value);
    return value === null || value === undefined || value === '' ? '–' : String(value);
}

/**
 * Client for the detail modules. Context switches are ignored so a detail
 * module cannot abort the list requests that share the real client, and a
 * successful write is reported to `onWrite`.
 */
export function createDetailClient(client, onWrite) {
    const after = (res, isWrite) => {
        if (isWrite && res && res.outcome === 'ok' && typeof onWrite === 'function') onWrite(res);
        return res;
    };
    return {
        async request(path, options) {
            const method = String((options && options.method) || 'GET').toUpperCase();
            return after(await client.request(path, options), method !== 'GET' && method !== 'HEAD');
        },
        async command(body, options) {
            return after(await client.command(body, options), true);
        },
        switchContext() {},
    };
}

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

/**
 * Controller behind WorklistView. `vue` supplies ref, reactive, shallowRef,
 * computed and optionally watch; without it plain (non-reactive) holders are
 * used, which is what the unit tests rely on.
 */
export function createWorklistViewController(options) {
    const client = options && options.client;
    const user = (options && options.currentUser) || {};
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    if (user.id === undefined || user.id === null) throw new Error('currentUser.id is required');
    const V = options.vue || (typeof window !== 'undefined' ? window.Vue : null) || null;
    const ref = (V && V.ref) || plainRef;
    const shallowRef = (V && V.shallowRef) || ref;
    const reactive = (V && V.reactive) || ((o) => o);
    const computed = (V && V.computed) || ((fn) => ({ get value() { return fn(); } }));

    const view = reactive({ page: 1, filterId: null, search: '', total: null, chip: 'all' });
    const checked = reactive({}); // dictation id -> true; only the bulk-selection checkboxes, no clinical data
    const panels = reactive({ protection: false, acceptance: false, history: false });
    const selectedId = ref(null);
    const leaseState = shallowRef(null);
    let started = false;
    let disposed = false;
    let detailId = null;
    let detailKey = null; // `${id}|${revision}|${etag}` of the loaded detail
    let lease = null; // { ctrl, unsubscribe }
    let reloadQueued = false;

    // --- list modules -------------------------------------------------------
    let refresh = null;
    const mapRecording = (recording) => {
        // A change-feed recording carries no workflow metadata: keep the row
        // the worklist already has; a new id gets a minimal row and one reload.
        if (recording && recording.dictation_id != null) return recording;
        const id = recording && recording.id;
        const known = refresh.rows.value.find((r) => rowKey(r) === String(id));
        if (known) return known;
        if (!reloadQueued) {
            reloadQueued = true;
            setTimeout(() => { reloadQueued = false; if (!disposed) reload(); }, 0);
        }
        return { dictation_id: id, author_user_id: recording && recording.user_id != null ? recording.user_id : null, status: 'aufgabe' };
    };
    refresh = useWorklistRefresh({
        apiClient: client,
        role: user.role || ROLE_AUTHOR,
        userId: user.id,
        tokenVersion: TOKEN_CONTRACT_VERSION,
        intervalMs: options.intervalMs,
        fetchChanges: options.fetchChanges || null, // v8-148: poll the clinic worklist rows by default.
        mapRecording,
        now: options.now,
        Vue: V || undefined,
        ref,
        computed,
    });
    const worklist = refresh; // the refresh controller spreads the worklist state and actions

    const filters = createWorklistFilters({
        client,
        userId: user.id,
        reactive,
        ...(options.storage !== undefined ? { storage: options.storage } : {}),
        onApply: ({ filterId, query }) => applyFilter(filterId, query),
    });
    const layout = useWorklistLayout({ client, columns: LIST_COLUMNS, reactive });

    // V8 default arrangement: the server's stored default lists every column visible in its own order. As long as
    // the layout is the untouched default (version 0: nothing stored yet, nothing edited), show the eight V8 columns in V8 order.
    // Local only: nothing is saved until the user saves the Ansicht.
    function applyV8Default() {
        if (layout.state.version !== 0 || layout.state.dirty) return;
        const byKey = new Map(layout.state.layout.columns.map((c) => [c.key, c]));
        const cols = LIST_COLUMNS.map(({ key }) => ({ ...(byKey.get(key) || { key, width: 140 }), visible: V8_COLUMNS.includes(key) }));
        layout.state.layout = { ...layout.state.layout, columns: cols };
    }
    // "Neu laden" in the Ansicht popover calls layout.load() too; keep the V8 set while nothing is saved.
    const loadLayout = layout.load;
    layout.load = async (...args) => {
        const result = await loadLayout(...args);
        applyV8Default();
        return result;
    };

    const paused = computed(() => Boolean(view.filterId || view.search) || view.page > 1);

    // --- loading ------------------------------------------------------------
    async function reload() {
        let response;
        if (!paused.value) {
            response = await refresh.refresh();
        } else {
            refresh.stop();
            response = await worklist.loadWorklist({ page: view.page, pageSize: PAGE_SIZE, filterId: view.filterId, search: view.search });
            if (response && response.outcome === 'ok') {
                const at = new Date();
                refresh.lastUpdate.value = at;
                refresh.lastUpdateLabel.value = formatLastUpdate(at);
                refresh.refreshError.value = null;
            } else if (response && !worklist.isPermissionLost.value) {
                refresh.refreshError.value = response.message || TEXT.failed;
            }
        }
        if (response && response.outcome === 'ok') {
            view.total = response.data && Number.isInteger(response.data.total) ? response.data.total : null;
        }
        if (!paused.value && started && !disposed && !refresh.isRunning.value && !worklist.isPermissionLost.value) {
            refresh.start();
        }
        syncDetail();
        return response;
    }

    function applyFilter(filterId, query) {
        const id = filterId || null;
        const search = String(query || '');
        if (id === view.filterId && search === view.search) return null;
        Object.assign(view, { filterId: id, search, page: 1 });
        layout.setFilters({ filter_id: id, search });
        return reload();
    }

    function goto(page) {
        if (!Number.isInteger(page) || page < 1 || page === view.page) return null;
        view.page = page;
        layout.setPage(page);
        return reload();
    }

    const pages = () => (Number.isInteger(view.total) ? Math.max(1, Math.ceil(view.total / PAGE_SIZE)) : null);
    const hasNext = () => (pages() !== null ? view.page < pages() : worklist.rows.value.length >= PAGE_SIZE);

    // --- display ------------------------------------------------------------
    const counts = computed(() => countBuckets(worklist.rows.value));
    // The status chips narrow the loaded page of rows; the KPI cards always count all loaded rows.
    const chipRows = computed(() => (view.chip === 'all'
        ? worklist.rows.value
        : worklist.rows.value.filter((r) => statusBucket(r.status) === view.chip)));
    const displayed = computed(() => flattenGroups(
        layout.applyToRows(chipRows.value),
        layout.state.layout.group_by,
    ));
    const shownRows = () => displayed.value.filter((i) => i.kind === 'row').map((i) => i.row);

    function setChip(chip) {
        view.chip = ['open', 'working', 'done'].includes(chip) ? chip : 'all';
    }

    // Bulk selection by checkbox. Independent of the detail selection (the row click).
    const isChecked = (row) => Boolean(checked[rowKey(row)]);
    function toggleChecked(row) {
        const id = rowKey(row);
        if (id === null) return;
        if (checked[id]) delete checked[id]; else checked[id] = true;
    }
    const allChecked = () => { const list = shownRows(); return list.length > 0 && list.every(isChecked); };
    function toggleAll() {
        const list = shownRows();
        const every = list.length > 0 && list.every(isChecked);
        for (const row of list) {
            const id = rowKey(row);
            if (every) delete checked[id]; else checked[id] = true;
        }
    }
    function clearChecked() { for (const id of Object.keys(checked)) delete checked[id]; }
    const checkedCount = () => worklist.rows.value.filter(isChecked).length;

    // Toolbar sort select over the layout module's sort state.
    const sortValue = () => {
        const first = layout.state.layout.sort[0];
        return first ? first[0] + ':' + first[1] : '';
    };
    function setSort(value) {
        const [key, dir] = String(value || '').split(':');
        layout.setSort(key || null, dir);
    }
    const sortNote = () => {
        const choice = SORT_CHOICES.find((c) => c.value === sortValue());
        if (choice) return choice.value ? TEXT.sortBy + ': ' + choice.label : TEXT.sortNone;
        const first = layout.state.layout.sort[0];
        return TEXT.sortBy + ': ' + layout.labelOf(first[0]);
    };
    const sortMark = (key) => {
        const s = layout.state.layout.sort.find((e) => e[0] === key);
        return s ? (s[1] === 'asc' ? '↑' : '↓') : '';
    };
    // The legacy author column is replaced by the three role columns; a layout that
    // does not list them yet gets them at the author column's place.
    const columns = () => {
        const stored = new Set(layout.state.layout.columns.map((c) => c.key));
        const roles = () => ROLE_KEYS.filter((k) => !stored.has(k))
            .map((key) => ({ key, width: 140, visible: true, label: layout.labelOf(key) }));
        const out = [];
        let placed = false;
        for (const col of layout.orderedColumns()) {
            if (col.key !== 'author_user_id') { out.push(col); continue; }
            out.push(...roles());
            placed = true;
        }
        if (!placed) out.push(...roles());
        return out;
    };
    const headerAttrs = (key) => {
        const { style, ...rest } = layout.headerProps(key); // drop outline:none, the global :focus-visible ring stays
        return rest;
    };

    // --- selection and detail ----------------------------------------------
    function select(row) {
        worklist.selectRow(row || null);
        syncDetail();
    }

    // Open is blocked while the lease is held by someone else or was lost.
    const leaseBlocksOpen = () => {
        const status = leaseState.value && leaseState.value.status;
        return status === 'foreign' || status === 'lost';
    };
    const canOpen = computed(() => Boolean(worklist.selectedRow.value) && !leaseBlocksOpen());

    /** Activates a row (or the current selection) and reports it to the page; selection and lease are unchanged otherwise. */
    function open(row) {
        if (disposed) return false;
        if (row && rowKey(row) !== selectedId.value) select(row);
        const target = worklist.selectedRow.value;
        if (!target || leaseBlocksOpen()) return false;
        if (typeof options.onOpen === 'function') {
            options.onOpen({ dictationId: rowKey(target), mode: getOpenMode(target, user.role) });
        }
        return true;
    }

    function onRowKey(event, row) {
        if (!event) return;
        const list = displayed.value.filter((i) => i.kind === 'row').map((i) => i.row);
        const stop = () => { if (typeof event.preventDefault === 'function') event.preventDefault(); };
        let target;
        if (event.key === 'ArrowDown') target = neighborRow(list, row, 1) || row;
        else if (event.key === 'ArrowUp') target = neighborRow(list, row, -1) || row;
        else if (event.key === ' ') { stop(); select(row); return; }
        else if (event.key === 'Enter') { stop(); open(row); return; }
        else if (event.key === 'Escape') { stop(); select(null); return; }
        else return;
        stop();
        select(target);
        const body = event.currentTarget && event.currentTarget.parentElement;
        const node = body && typeof body.querySelectorAll === 'function'
            ? Array.from(body.querySelectorAll('[data-row]')).find((n) => n.getAttribute('data-id') === rowKey(target)) : null;
        if (node && typeof node.focus === 'function') node.focus();
    }

    const workflow = createWorkflowActions({
        client: createDetailClient(client, () => { if (!disposed) reload(); }),
        reactive,
    });
    const detailClient = createDetailClient(client);

    function loadWorkflow(row) {
        // Only what the API supplies is shown; a row without capabilities offers no action.
        workflow.load({
            dictationId: rowKey(row),
            etag: row.etag || null,
            revision: Number.isInteger(row.revision) ? row.revision : null,
            allowedActions: Array.isArray(row.allowed_actions) ? row.allowed_actions : [],
            transitions: row.transitions || {},
            asrStatus: row.asr_status || null,
            workState: row.status || null,
            binding: row.binding || {},
        });
    }

    function openLease(id) {
        const ctrl = createLeaseController({
            client: detailClient,
            dictationId: id,
            mayTakeOver: user.mayTakeOver === true,
            heartbeatMs: options.heartbeatMs,
        });
        leaseState.value = ctrl.getState();
        lease = { ctrl, unsubscribe: ctrl.subscribe((snap) => { leaseState.value = snap; }) };
    }

    function closeLease() {
        if (!lease) return;
        const { ctrl, unsubscribe } = lease;
        lease = null;
        unsubscribe();
        const done = ctrl.release(); // stops writes and the heartbeat first, then tells the server
        ctrl.dispose();
        Promise.resolve(done).catch(() => {});
        leaseState.value = null;
    }

    function syncDetail() {
        if (disposed) return;
        const row = worklist.selectedRow.value;
        const id = row ? rowKey(row) : null;
        const key = row ? [id, row.revision, row.etag].join('|') : null;
        if (id !== detailId) {
            closeLease();
            workflow.cancel();
            detailId = id;
            selectedId.value = id;
            detailKey = key;
            if (row) { loadWorkflow(row); openLease(id); } else workflow.load(null);
        } else if (row && key !== detailKey) {
            detailKey = key;
            loadWorkflow(row);
        }
    }
    if (V && typeof V.watch === 'function') V.watch(() => worklist.selectedRow.value, syncDetail);

    const leaseRun = {
        acquire: () => (lease ? lease.ctrl.acquire() : null),
        release: () => (lease ? lease.ctrl.release() : null),
        takeOver: () => (lease ? lease.ctrl.takeOver() : null),
        discard: () => (lease ? lease.ctrl.resolveConflict('discard') : null),
        reapply: () => (lease ? lease.ctrl.resolveConflict('reapply') : null),
    };

    // --- lifecycle ----------------------------------------------------------
    async function start() {
        if (started || disposed) return;
        started = true;
        ensureHostedStyles();
        await Promise.all([layout.load(), refresh.start()]);
        applyV8Default();
        syncDetail();
    }

    /** Releases timers, lease heartbeat, requests and all clinical data held in memory. */
    function dispose() {
        if (disposed) return;
        disposed = true;
        closeLease();
        workflow.cancel();
        refresh.logout();
        clearChecked();
        selectedId.value = null;
        detailId = null;
    }

    const priorityToneOf = (row) => (Number(row.priority) === 128 ? 'high' : Number(row.priority) === 0 && row.priority !== '' && row.priority != null ? 'low' : 'normal');

    const surfaceStyle = (name, extra) => {
        const s = surfaceFor(name);
        return { background: s.background, color: s.color, ...extra(s) };
    };

    return {
        vue: V, view, panels, selectedId, selectedRow: worklist.selectedRow, leaseState, leaseRun, paused,
        worklist, refresh, filters, layout, workflow, detailClient, displayed, columns, headerAttrs,
        start, dispose, reload, select, open, canOpen, onRowKey, goto, pages, hasNext, applyFilter,
        key: rowKey, cell: cellText, badge: statusBadge, dictationLabel, entryCount, text: TEXT,
        emptyCell: (row, key) => !ROLE_KEYS.includes(key) && key !== 'status' && cellText(row, key) === '–',
        priorityTone: priorityToneOf,
        pageLabel: () => 'Seite ' + view.page + (pages() === null ? '' : ' von ' + pages()),
        errorStyle: surfaceStyle('error', (s) => ({ border: '1px solid ' + s.border })),
        selectedStyle: surfaceStyle('normal', () => ({ borderLeft: '4px solid var(--border-focus)' })),
        counts, chipRows, setChip, isChecked, toggleChecked, allChecked, toggleAll, clearChecked, checkedCount,
        sortChoices: SORT_CHOICES, sortValue, setSort, sortNote, sortMark, shownRows,
        colClass: (key) => COL_CLASS[key] || '',
        // Columns the stylesheet has no width class for (Frist, Übernommen von, ...) keep the layout width.
        colStyle: (col) => (COL_CLASS[col.key] ? null : { width: (Number(col.width) || 140) + 'px' }),
        statusTone: (row) => statusTone(row.status),
        prioClass: (row) => ({ high: 'hoch', low: 'niedrig', normal: 'mittel' })[priorityToneOf(row)],
        sourceText: (row) => sourceLabel(row.source_kind),
        updatedLabel: () => {
            const at = refresh.lastUpdate.value;
            return at instanceof Date && !Number.isNaN(at.getTime())
                ? TEXT.updated + ' ' + String(at.getHours()).padStart(2, '0') + ':' + String(at.getMinutes()).padStart(2, '0')
                : refresh.lastUpdateLabel.value;
        },
        pagerInfo: () => 'Seite ' + view.page + (pages() === null ? '' : ' von ' + pages()) + ' · '
            + entryCount(view.total ?? worklist.rows.value.length),
    };
}

function buildParts(V, detailClient) {
    return {
        Layout: WorklistLayoutControls(V),
        Lease: createLeaseStatusComponent(V),
        Workflow: { name: 'ClinicWorkflowPanel', props: { wf: { type: Object, required: true } }, template: WORKFLOW_ACTIONS_TEMPLATE },
        Protection: { ...DictationProtectionComponent(V), mounted() { this.model.load(); } },
        Acceptance: acceptancePolicyComponent(V, { client: detailClient }),
        History: createHistoryComponent(V),
    };
}

export const WorklistView = {
    name: 'ClinicWorklistView',
    components: { WorklistFiltersPanel },
    emits: ['open'],
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, required: true },
        vue: { type: Object, default: null },
        fetchChanges: { type: Function, default: null },
        intervalMs: { type: Number, default: undefined },
        autoStart: { type: Boolean, default: true },
    },
    setup(props, ctx) {
        const c = createWorklistViewController({
            onOpen: (payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit('open', payload); },
            client: props.client,
            currentUser: props.currentUser,
            vue: props.vue || undefined,
            fetchChanges: props.fetchChanges || undefined,
            intervalMs: props.intervalMs,
        });
        const V = c.vue;
        if (!V) throw new Error('Vue is required to mount the worklist view');
        const parts = buildParts(V, c.detailClient);
        if (typeof V.onMounted === 'function' && props.autoStart) V.onMounted(() => c.start());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        function rowContext(id) {
            const row = c.worklist.selectedRow.value;
            if (!row || rowKey(row) !== id) return null;
            const visible = c.columns().map((col) => col.key);
            return {
                dictator: visible.includes('dictated_by') && row.dictated_by ? cellText(row, 'dictated_by') : null,
                pool: visible.includes('staff_pools') ? cellText(row, 'staff_pools') : null,
            };
        }
        return { c, parts, rowContext };
    },
    template: `
<section class="v8-page clinic-worklist-view" data-testid="clinic-worklist-view" aria-labelledby="cwv-title">
  <div class="page-head">
    <div>
      <div class="eyebrow">{{ c.text.eyebrow }}</div>
      <div class="hline">
        <h1 id="cwv-title" tabindex="-1">{{ c.text.title }}</h1>
        <span class="count-pill" data-testid="result-count">{{ c.entryCount(c.view.total ?? c.counts.value.all) }}</span>
      </div>
      <p class="subtitle">{{ c.text.subline }}</p>
    </div>
    <div class="page-actions">
      <span role="status" aria-live="polite" class="last-update" data-testid="last-update">{{ c.updatedLabel() }}</span>
      <button type="button" class="icon-btn" data-testid="refresh-button"
              :aria-label="c.text.refresh" :title="c.text.refresh"
              :disabled="c.refresh.isRefreshing.value" :aria-busy="c.refresh.isRefreshing.value ? 'true' : 'false'"
              @click="c.reload()"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.6-4.5M4 4v5h5M4 13a8 8 0 0 0 14.6 4.5M20 20v-5h-5"/></svg>{{ c.text.refresh }}</button>
    </div>
  </div>
  <div v-if="c.refresh.refreshError.value || (c.worklist.errorMessage.value && !c.worklist.isPermissionLost.value)"
       role="alert" class="note burgundy" data-testid="load-error" style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 0 16px">
    <span>{{ c.refresh.refreshError.value || c.worklist.errorMessage.value }}</span>
    <button type="button" class="secondary" @click="c.reload()">{{ c.text.retry }}</button>
  </div>
  <div v-if="c.worklist.isPermissionLost.value" role="alert" class="note burgundy" data-testid="permission-loss-state" style="margin: 0 0 16px">
    {{ c.worklist.errorMessage.value || c.text.denied }}
  </div>
  <template v-else>
    <section class="metrics" :aria-label="c.text.summary" data-testid="status-totals">
      <div class="metric"><div><div class="metric-label">{{ c.text.kpiAll }}</div><div class="metric-number" data-kpi="all">{{ c.counts.value.all }}</div></div>
        <div class="metric-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M4 12h16M4 19h16"/></svg></div></div>
      <div class="metric"><div><div class="metric-label">{{ c.text.kpiOpen }}</div><div class="metric-number" data-kpi="open">{{ c.counts.value.open }}</div></div>
        <div class="metric-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/></svg></div></div>
      <div class="metric"><div><div class="metric-label">{{ c.text.kpiWorking }}</div><div class="metric-number" data-kpi="working">{{ c.counts.value.working }}</div></div>
        <div class="metric-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg></div></div>
      <div class="metric"><div><div class="metric-label">{{ c.text.kpiDone }}</div><div class="metric-number" data-kpi="done">{{ c.counts.value.done }}</div></div>
        <div class="metric-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg></div></div>
    </section>
    <section class="workspace" :aria-label="c.text.title" style="overflow: visible">
      <WorklistFiltersPanel :controller="c.filters">
        <template #start>
          <span class="viewtitle">{{ c.text.listTitle }}</span>
          <span class="viewhelp" data-testid="sort-note">{{ c.paused.value ? c.text.paused : c.sortNote() }}</span>
        </template>
        <template #end>
          <component :is="parts.Layout" :layout="c.layout" />
        </template>
        <template #toolbar>
          <div class="chiprow" role="group" :aria-label="c.text.chips" style="display: contents">
            <button type="button" class="filter-pill" :class="c.view.chip === 'all' ? 'active' : ''" data-chip="all" :aria-pressed="c.view.chip === 'all' ? 'true' : 'false'" @click="c.setChip('all')">Alle <span class="num-circle">{{ c.counts.value.all }}</span></button>
            <button type="button" class="filter-pill" :class="c.view.chip === 'open' ? 'active' : ''" data-chip="open" :aria-pressed="c.view.chip === 'open' ? 'true' : 'false'" @click="c.setChip('open')">{{ c.text.kpiOpen }} <span class="num-circle">{{ c.counts.value.open }}</span></button>
            <button type="button" class="filter-pill" :class="c.view.chip === 'working' ? 'active' : ''" data-chip="working" :aria-pressed="c.view.chip === 'working' ? 'true' : 'false'" @click="c.setChip('working')">{{ c.text.kpiWorking }} <span class="num-circle">{{ c.counts.value.working }}</span></button>
            <button type="button" class="filter-pill" :class="c.view.chip === 'done' ? 'active' : ''" data-chip="done" :aria-pressed="c.view.chip === 'done' ? 'true' : 'false'" @click="c.setChip('done')">{{ c.text.kpiDone }} <span class="num-circle">{{ c.counts.value.done }}</span></button>
          </div>
          <span class="toolbar-right">
            <select class="select" style="width: auto; height: auto; min-height: 34px; appearance: auto" :aria-label="c.text.sortLabel" :value="c.sortValue()" @change="c.setSort($event.target.value)">
              <option v-for="o in c.sortChoices" :key="o.value" :value="o.value">{{ o.label }}</option>
              <option v-if="!c.sortChoices.some((o) => o.value === c.sortValue())" :value="c.sortValue()">{{ c.sortNote() }}</option>
            </select>
          </span>
        </template>
      </WorklistFiltersPanel>
      <p v-if="c.paused.value" class="sr-only cl-sr-only">{{ c.text.paused }}</p>
      <div class="listhead">
        <strong data-testid="list-count">{{ c.shownRows().length }} von {{ c.counts.value.all }} auf dieser Seite ({{ c.worklist.totals.value.formattedTotalDuration }})</strong>
        <span>{{ c.text.hintSelect }}</span>
      </div>
      <p class="sr-only cl-sr-only" id="cwv-keys">{{ c.text.keys }}</p>
      <div class="cl-body" :class="c.selectedId.value ? 'lg:grid-cols-2' : ''" style="display: block">
        <div class="listbody" :class="c.selectedId.value ? 'has-detail' : ''">
          <div class="table-container">
            <div v-if="c.worklist.isLoading.value" class="empty v8-empty" data-layout="cl-state" role="status" aria-live="polite" data-testid="loading-state">
              <div class="empty-icon" aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 3a9 9 0 1 0 9 9"/></svg></div>
              <h2>{{ c.text.loading }}</h2>
            </div>
            <div v-else-if="!c.worklist.rows.value.length" class="empty v8-empty" data-layout="cl-state" data-testid="empty-state">
              <div class="empty-icon" aria-hidden="true"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></div>
              <h2>{{ c.text.empty }}</h2>
              <p v-if="c.view.filterId || c.view.search">{{ c.text.noMatch }}</p>
            </div>
            <table v-else class="worktable" data-layout="cl-table" role="grid" aria-label="Arbeitsliste" aria-describedby="cwv-keys">
              <thead>
                <tr>
                  <th class="check"><input type="checkbox" :checked="c.allChecked()" :aria-label="c.text.selectAll" @change="c.toggleAll()"></th>
                  <th class="titlecol" scope="col">{{ c.text.dictation }}</th>
                  <th v-for="col in c.columns()" :key="col.key" scope="col" v-bind="c.headerAttrs(col.key)"
                      :class="c.colClass(col.key)" :style="c.colStyle(col)" style="cursor: pointer"
                      @keydown="c.layout.onKeydown(col.key, $event)" @click="c.layout.cycleSort(col.key)">{{ col.label }} {{ c.sortMark(col.key) }}</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="item in c.displayed.value" :key="item.key">
                  <tr v-if="item.kind === 'group'" class="group-row">
                    <th scope="rowgroup" :colspan="c.columns().length + 2" style="text-align: left; background: var(--v8-s1); color: var(--v8-text)"
                        :style="{ paddingLeft: (0.75 + item.depth) + 'rem' }">{{ item.label }}</th>
                  </tr>
                  <tr v-else tabindex="0" :class="c.selectedId.value === c.key(item.row) ? 'selected' : ''" data-row :data-id="c.key(item.row)"
                      :aria-selected="c.selectedId.value === c.key(item.row) ? 'true' : 'false'"
                      @click="c.select(item.row)" @dblclick="c.open(item.row)" @keydown="c.onRowKey($event, item.row)">
                    <td class="check" @click.stop><input type="checkbox" class="rowcheck" :checked="c.isChecked(item.row)" :aria-label="c.text.selectRow + ': ' + c.dictationLabel(item.row)" @change="c.toggleChecked(item.row)" @keydown.stop></td>
                    <td>
                      <div class="titlecell">
                        <span class="mini-rec" aria-hidden="true">
                          <svg v-if="item.row.source_kind === 'manual_file' || item.row.source_kind === 'document'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 3h10l4 4v14H5zM15 3v6h5M9 12h6M9 16h6"/></svg>
                          <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4M8 22h8"/></svg>
                        </span>
                        <span class="titletext"><strong>{{ c.dictationLabel(item.row) }}</strong><small>{{ c.text.recording }}</small></span>
                      </div>
                    </td>
                    <td v-for="col in c.columns()" :key="col.key" :data-label="col.label" :class="c.emptyCell(item.row, col.key) ? 'cl-empty' : ''">
                      <span v-if="col.key === 'status'" class="status" data-layout="pill-status" :class="c.statusTone(item.row)">{{ c.badge(item.row).label }}</span>
                      <span v-else-if="col.key === 'priority'" class="prio" :class="c.prioClass(item.row)">{{ c.cell(item.row, col.key) }}</span>
                      <span v-else-if="col.key === 'staff_pools'" class="pool-pill" :title="c.cell(item.row, col.key)">{{ c.cell(item.row, col.key) }}</span>
                      <span v-else-if="col.key === 'dictated_by'" class="entity-name" :title="c.cell(item.row, col.key)">{{ c.cell(item.row, col.key) }}</span>
                      <template v-else>{{ c.cell(item.row, col.key) }}</template>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <aside v-if="c.selectedId.value" class="detail" aria-labelledby="cwv-detail-title" data-testid="detail-panel"
                 @keydown.esc="c.select(null)">
            <div :key="c.selectedId.value">
              <div class="detailhead">
                <div><strong id="cwv-detail-title">{{ c.dictationLabel(c.selectedRow.value) }}</strong><small>{{ c.text.detailInfo }}</small></div>
                <button type="button" class="icon-button closebtn" data-testid="close-detail" :aria-label="c.text.close" :title="c.text.close"
                        @click="c.select(null)"><svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
              </div>
              <div class="detailcontent">
                <div class="detailbadges" v-if="c.selectedRow.value">
                  <span class="status" :class="c.statusTone(c.selectedRow.value)">{{ c.badge(c.selectedRow.value).label }}</span>
                  <span class="prio" :class="c.prioClass(c.selectedRow.value)">{{ c.cell(c.selectedRow.value, 'priority') }}</span>
                </div>
                <div class="detail-info" v-if="c.selectedRow.value">
                  <div class="detail-line"><span>Diktiert von</span><span>{{ c.cell(c.selectedRow.value, 'dictated_by') }}</span></div>
                  <div class="detail-line"><span>Schreibkräfte-Pool</span><span>{{ c.cell(c.selectedRow.value, 'staff_pools') }}</span></div>
                  <div class="detail-line"><span>Übernommen von</span><span>{{ c.cell(c.selectedRow.value, 'lease_holder') }}</span></div>
                  <div class="detail-line"><span>Quelle</span><span>{{ c.cell(c.selectedRow.value, 'source_kind') }}</span></div>
                  <div class="detail-line"><span>Dauer</span><span>{{ c.cell(c.selectedRow.value, 'duration_seconds') }}</span></div>
                  <div class="detail-line"><span>Erstellt</span><span>{{ c.cell(c.selectedRow.value, 'created_at') }}</span></div>
                </div>
                <div class="v8-hosted" style="margin-top: 17px">
                  <div class="detail-bottom" style="margin-top: 0">
                    <button type="button" class="primary" data-testid="open-dictation"
                            :disabled="!c.canOpen.value" @click="c.open()">{{ c.text.open }}</button>
                  </div>
                  <component :is="parts.Lease" v-if="c.leaseState.value" :state="c.leaseState.value" :run="c.leaseRun" />
                  <component :is="parts.Workflow" :wf="c.workflow" />
                </div>
                <details :open="c.panels.protection" @toggle="c.panels.protection = $event.target.open" style="margin-top: 14px; border-top: 1px solid var(--v8-line-2); padding-top: 4px">
                  <summary style="cursor: pointer; min-height: 38px; display: flex; align-items: center; font-size: 12px; font-weight: 750">{{ c.text.protection }}</summary>
                  <component :is="parts.Protection" v-if="c.panels.protection" :client="c.detailClient" :dictation-id="c.selectedId.value" />
                </details>
                <details :open="c.panels.acceptance" @toggle="c.panels.acceptance = $event.target.open" style="border-top: 1px solid var(--v8-line-2); padding-top: 4px">
                  <summary style="cursor: pointer; min-height: 38px; display: flex; align-items: center; font-size: 12px; font-weight: 750">{{ c.text.acceptance }}</summary>
                  <component :is="parts.Acceptance" v-if="c.panels.acceptance" :dictation-id="c.selectedId.value" />
                </details>
                <details :open="c.panels.history" @toggle="c.panels.history = $event.target.open" style="border-top: 1px solid var(--v8-line-2); padding-top: 4px">
                  <summary style="cursor: pointer; min-height: 38px; display: flex; align-items: center; font-size: 12px; font-weight: 750">{{ c.text.history }}</summary>
                  <component :is="parts.History" v-if="c.panels.history" :client="c.detailClient" :dictation-id="c.selectedId.value" />
                </details>
              </div>
            </div>
          </aside>
        </div>
      </div>
      <div v-if="c.checkedCount()" class="quick-actions show" data-testid="bulk-bar">
        <strong>{{ c.checkedCount() }} {{ c.text.selected }}</strong>
        <button type="button" class="secondary" @click="c.clearChecked()">{{ c.text.clearSelection }}</button>
      </div>
      <nav class="tablefooter" data-layout="cl-pager" aria-label="Seiten">
        <span aria-live="polite">{{ c.pagerInfo() }}</span>
        <div class="pagination">
          <button type="button" class="icon-button page-icon" :aria-label="c.text.prev" :title="c.text.prev" :disabled="c.view.page <= 1" @click="c.goto(c.view.page - 1)"><svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m15 5-7 7 7 7"/></svg></button>
          <button type="button" class="icon-button page-icon" :aria-label="c.text.next" :title="c.text.next" :disabled="!c.hasNext()" @click="c.goto(c.view.page + 1)"><svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m9 5 7 7-7 7"/></svg></button>
        </div>
      </nav>
    </section>
  </template>
</section>`,
};

export function registerWorklistView(app) {
    app.component('clinic-worklist-view', WorklistView);
    return app;
}
