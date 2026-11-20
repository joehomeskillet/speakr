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
});

// Columns the layout may order, sort and group. Title, notes and recipient are
// left out on purpose (W16); no Fall-ID column until the contract provides one.
export const LIST_COLUMNS = Object.freeze([
    { key: 'status', label: 'Status' },
    { key: 'priority', label: 'Priorität' },
    { key: 'deadline_at', label: 'Frist' },
    { key: 'dictated_by', label: 'Diktiert von' },
    { key: 'staff_pools', label: 'Schreibkräfte-Pool' },
    { key: 'lease_holder', label: 'Übernommen von' },
    { key: 'assignee_user_id', label: 'Zugewiesen an' },
    { key: 'assigned_at', label: 'Zugewiesen' },
    { key: 'created_at', label: 'Erstellt' },
    { key: 'duration_seconds', label: 'Dauer' },
    { key: 'source_kind', label: 'Quelle' },
].map((c) => Object.freeze(c)));

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
export function cellText(row, key) {
    const value = row[key];
    if (DATE_COLUMNS.includes(key) && value !== null && value !== undefined && value !== '') return formatDateTime(value);
    if (key === 'status') return statusBadge(row).label;
    if (key === 'priority') return formatPriority(value);
    if (key === 'duration_seconds') return formatAudioDuration(value);
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

    const view = reactive({ page: 1, filterId: null, search: '', total: null });
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
    const displayed = computed(() => flattenGroups(
        layout.applyToRows(worklist.rows.value),
        layout.state.layout.group_by,
    ));
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
        await Promise.all([layout.load(), refresh.start()]);
        syncDetail();
    }

    /** Releases timers, lease heartbeat, requests and all clinical data held in memory. */
    function dispose() {
        if (disposed) return;
        disposed = true;
        closeLease();
        workflow.cancel();
        refresh.logout();
        selectedId.value = null;
        detailId = null;
    }

    const surfaceStyle = (name, extra) => {
        const s = surfaceFor(name);
        return { background: s.background, color: s.color, ...extra(s) };
    };

    return {
        vue: V, view, panels, selectedId, selectedRow: worklist.selectedRow, leaseState, leaseRun, paused,
        worklist, refresh, filters, layout, workflow, detailClient, displayed, columns, headerAttrs,
        start, dispose, reload, select, open, canOpen, onRowKey, goto, pages, hasNext, applyFilter,
        key: rowKey, cell: cellText, badge: statusBadge, text: TEXT,
        emptyCell: (row, key) => !ROLE_KEYS.includes(key) && key !== 'status' && cellText(row, key) === '–',
        priorityTone: (row) => (Number(row.priority) === 128 ? 'high' : Number(row.priority) === 0 && row.priority !== '' && row.priority != null ? 'low' : 'normal'),
        pageLabel: () => 'Seite ' + view.page + (pages() === null ? '' : ' von ' + pages()),
        errorStyle: surfaceStyle('error', (s) => ({ border: '1px solid ' + s.border })),
        selectedStyle: surfaceStyle('normal', () => ({ borderLeft: '4px solid var(--border-focus)' })),
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
        return { c, parts };
    },
    template: `
<section class="clinic-worklist-view cl-view" data-testid="clinic-worklist-view" aria-labelledby="cwv-title">
  <div class="cl-head">
    <h1 id="cwv-title" class="cl-title">{{ c.text.title }}</h1>
    <span class="cl-sub" data-testid="result-count">{{ c.worklist.totals.value.totalCount }} Diktate ({{ c.worklist.totals.value.formattedTotalDuration }})</span>
  </div>
  <div v-if="c.refresh.refreshError.value || (c.worklist.errorMessage.value && !c.worklist.isPermissionLost.value)"
       role="alert" class="cl-alert" :style="c.errorStyle" data-testid="load-error">
    <span>{{ c.refresh.refreshError.value || c.worklist.errorMessage.value }}</span>
    <button type="button" class="btn btn--ghost btn--sm" @click="c.reload()">{{ c.text.retry }}</button>
  </div>
  <div v-if="c.worklist.isPermissionLost.value" role="alert" class="cl-alert" :style="c.errorStyle" data-testid="permission-loss-state">
    {{ c.worklist.errorMessage.value || c.text.denied }}
  </div>
  <template v-else>
    <WorklistFiltersPanel :controller="c.filters">
      <template #start>
        <button type="button" class="btn btn--ghost btn--icon cl-btn" data-testid="refresh-button"
                :aria-label="c.text.refresh" :title="c.text.refresh"
                :disabled="c.refresh.isRefreshing.value" :aria-busy="c.refresh.isRefreshing.value ? 'true' : 'false'"
                @click="c.reload()"><i class="fa-solid fa-rotate" :class="c.refresh.isRefreshing.value ? 'cl-spin' : ''" aria-hidden="true"></i></button>
        <span role="status" aria-live="polite" class="cl-last-update" data-testid="last-update">{{ c.refresh.lastUpdateLabel.value }}</span>
      </template>
      <template #end>
        <component :is="parts.Layout" :layout="c.layout" />
      </template>
    </WorklistFiltersPanel>
    <p v-if="c.paused.value" class="cl-hint">{{ c.text.paused }}</p>
    <header class="cl-totals" role="region" aria-label="Statusübersicht" data-testid="status-totals">
      <span v-for="g in c.worklist.totals.value.groups" :key="g.status" class="cl-chip"
            :style="{ background: g.surface.background, color: g.surface.color, border: '1px solid ' + g.surface.border }">
        {{ g.label }}: {{ g.count }} ({{ g.formattedDuration }})
      </span>
    </header>
    <div class="cl-body" :class="c.selectedId.value ? 'lg:grid-cols-2' : ''">
      <div class="cl-list">
        <p class="sr-only" id="cwv-keys">{{ c.text.keys }}</p>
        <div v-if="c.worklist.isLoading.value" class="cl-state" role="status" aria-live="polite" data-testid="loading-state">
          <span class="cl-state__icon"><i class="fa-solid fa-spinner cl-spin" aria-hidden="true"></i></span>
          <p class="cl-state__body">{{ c.text.loading }}</p>
        </div>
        <div v-else-if="!c.worklist.rows.value.length" class="cl-state" data-testid="empty-state">
          <span class="cl-state__icon"><i class="fa-regular fa-folder-open" aria-hidden="true"></i></span>
          <p class="cl-state__title">{{ c.text.empty }}</p>
          <p v-if="c.view.filterId || c.view.search" class="cl-state__body">{{ c.text.noMatch }}</p>
        </div>
        <div v-else class="cl-tablewrap">
          <table class="cl-table w-full" role="grid" aria-label="Arbeitsliste" aria-describedby="cwv-keys">
            <thead>
              <tr>
                <th v-for="col in c.columns()" :key="col.key" scope="col" v-bind="c.headerAttrs(col.key)"
                    :class="col.key === 'duration_seconds' ? 'cl-num' : ''"
                    @keydown="c.layout.onKeydown(col.key, $event)" @click="c.layout.cycleSort(col.key)">{{ col.label }}</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="item in c.displayed.value" :key="item.key">
                <tr v-if="item.kind === 'group'" class="cl-group">
                  <th scope="rowgroup" :colspan="c.columns().length" style="text-align: left"
                      :style="{ paddingLeft: (0.75 + item.depth) + 'rem' }">{{ item.label }}</th>
                </tr>
                <tr v-else tabindex="0" class="cl-row" data-row :data-id="c.key(item.row)"
                    :aria-selected="c.selectedId.value === c.key(item.row) ? 'true' : 'false'"
                    @click="c.select(item.row)" @dblclick="c.open(item.row)" @keydown="c.onRowKey($event, item.row)">
                  <td v-for="col in c.columns()" :key="col.key" :data-label="col.label"
                      :class="[col.key === 'duration_seconds' ? 'cl-num' : (col.key === 'deadline_at' || col.key === 'created_at' || col.key === 'assigned_at') ? 'cl-nowrap' : '', c.emptyCell(item.row, col.key) ? 'cl-empty' : '']">
                    <span v-if="col.key === 'status'" class="pill-status cl-pill" :style="c.badge(item.row).style">{{ c.badge(item.row).label }}</span>
                    <span v-else-if="col.key === 'priority'" class="cl-prio" :class="'cl-prio--' + c.priorityTone(item.row)">{{ c.cell(item.row, col.key) }}</span>
                    <template v-else>{{ c.cell(item.row, col.key) }}</template>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
          <nav class="cl-pager" aria-label="Seiten">
            <button type="button" class="btn btn--ghost" :disabled="c.view.page <= 1" @click="c.goto(c.view.page - 1)">{{ c.text.prev }}</button>
            <span aria-live="polite">{{ c.pageLabel() }}</span>
            <button type="button" class="btn btn--ghost" :disabled="!c.hasNext()" @click="c.goto(c.view.page + 1)">{{ c.text.next }}</button>
          </nav>
        </div>
      </div>
      <aside v-if="c.selectedId.value" class="cl-detail" aria-labelledby="cwv-detail-title" data-testid="detail-panel"
             @keydown.esc="c.select(null)">
        <div :key="c.selectedId.value">
          <h2 id="cwv-detail-title" class="cl-panel__title">{{ c.text.detail }}</h2>
          <div class="cl-detail__actions">
            <button type="button" class="btn btn--primary" data-testid="open-dictation"
                    :disabled="!c.canOpen.value" @click="c.open()">{{ c.text.open }}</button>
            <button type="button" class="btn btn--ghost" data-testid="close-detail"
                    @click="c.select(null)">{{ c.text.close }}</button>
          </div>
          <component :is="parts.Lease" v-if="c.leaseState.value" :state="c.leaseState.value" :run="c.leaseRun" />
          <component :is="parts.Workflow" :wf="c.workflow" />
          <details :open="c.panels.protection" @toggle="c.panels.protection = $event.target.open">
            <summary>{{ c.text.protection }}</summary>
            <component :is="parts.Protection" v-if="c.panels.protection" :client="c.detailClient" :dictation-id="c.selectedId.value" />
          </details>
          <details :open="c.panels.acceptance" @toggle="c.panels.acceptance = $event.target.open">
            <summary>{{ c.text.acceptance }}</summary>
            <component :is="parts.Acceptance" v-if="c.panels.acceptance" :dictation-id="c.selectedId.value" />
          </details>
          <details :open="c.panels.history" @toggle="c.panels.history = $event.target.open">
            <summary>{{ c.text.history }}</summary>
            <component :is="parts.History" v-if="c.panels.history" :client="c.detailClient" :dictation-id="c.selectedId.value" />
          </details>
        </div>
      </aside>
    </div>
  </template>
</section>`,
};

export function registerWorklistView(app) {
    app.component('clinic-worklist-view', WorklistView);
    return app;
}
