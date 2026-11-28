/**
 * Worklist sorting, grouping and column layout (PAR-WF-004, PAR-CFG-005).
 *
 * Talks to GET/PUT /api/clinic/v1/worklist/layout through the clinic api client.
 * Only the layout (keys, widths, sort, grouping) is held and sent. Worklist
 * rows are passed through in memory and are never stored or sent anywhere.
 * Layout shape and limits mirror src/clinic/list_layout.py.
 */

import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LAYOUT_PATH = '/worklist/layout';
export const MIN_WIDTH = 40;
export const MAX_WIDTH = 800;
export const WIDTH_STEP = 10;
export const MAX_GROUP_DEPTH = 3;
export const TIE_BREAKER = 'id';
const MAX_COLUMNS = 30;

export const LABELS = Object.freeze({
    region: 'Arbeitsliste: Spalten, Sortierung und Gruppierung',
    sortAsc: 'aufsteigend',
    sortDesc: 'absteigend',
    sortNone: 'nicht sortiert',
    moveLeft: 'Spalte nach links',
    moveRight: 'Spalte nach rechts',
    widen: 'Spalte breiter',
    narrow: 'Spalte schmaler',
    show: 'Spalte anzeigen',
    hide: 'Spalte ausblenden',
    view: 'Ansicht',
    close: 'Schliessen',
    columns: 'Spalten',
    groupBy: 'Gruppieren nach',
    groupNone: 'Keine Gruppierung',
    groupLevel: 'Ebene',
    unknownColumn: 'Unbekannte Spalte. Das Layout wurde nicht gespeichert.',
    tooManyColumns: 'Zu viele Spalten für diese Ansicht. Blenden Sie einige Spalten aus und speichern Sie erneut.',
    save: 'Layout speichern',
    saved: 'Ansicht gespeichert.',
    reload: 'Neu laden',
    restore: 'Standardspalten wiederherstellen',
    confirmRestore: 'Standardspalten wiederherstellen? Ihre bisherige Spaltenauswahl, Reihenfolge, Sortierung und Gruppierung werden ersetzt.',
    restored: 'Standardspalten wurden wiederhergestellt.',
    undoRestore: 'Rückgängig',
    undoDone: 'Die vorherige Ansicht wurde wiederhergestellt.',
    loading: 'Layout wird geladen',
    stale: 'Das Layout wurde zwischenzeitlich geändert und wurde neu geladen.',
    readOnly: 'Keine Berechtigung, das Layout zu ändern.',
    keys: 'Tastatur: Enter sortiert, Alt+Pfeil verschiebt, Umschalt+Pfeil ändert die Breite.',
});

const GROUP_ABSENT = Symbol('absent');

function clamp(width) {
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.trunc(width)));
}

function newKey() {
    if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
    const hex = () => Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0');
    return `${hex()}${hex()}-${hex()}-4${hex().slice(1)}-8${hex().slice(1)}-${hex()}${hex()}${hex()}`;
}

/**
 * Keep only valid fields: drop unknown/duplicate columns and bad values, then
 * append new columns with their default visibility.
 */
export function normalizeLayout(raw, known) {
    const keys = new Set(known.map((c) => c.key));
    const src = raw && typeof raw === 'object' ? raw : {};
    const columns = [];
    const seen = new Set();
    for (const c of Array.isArray(src.columns) ? src.columns : []) {
        if (!c || !keys.has(c.key) || seen.has(c.key)) continue;
        seen.add(c.key);
        columns.push({
            key: c.key,
            width: Number.isFinite(c.width) ? clamp(c.width) : 120,
            visible: c.visible !== false,
        });
    }
    for (const c of known) {
        if (!seen.has(c.key)) columns.push({ key: c.key, width: 120, visible: c.visible !== false });
    }
    const sort = [];
    for (const s of Array.isArray(src.sort) ? src.sort : []) {
        if (Array.isArray(s) && keys.has(s[0]) && (s[1] === 'asc' || s[1] === 'desc')
            && !sort.some((e) => e[0] === s[0])) sort.push([s[0], s[1]]);
    }
    const group_by = [];
    for (const g of Array.isArray(src.group_by) ? src.group_by : []) {
        if (keys.has(g) && !group_by.includes(g) && group_by.length < MAX_GROUP_DEPTH) group_by.push(g);
    }
    return { version: 1, columns, sort, group_by };
}

function rankOf(key, value) {
    if (key === 'priority') return { 128: 0, 1: 1, 0: 2 }[value] ?? 3;
    if (key === 'dictated_by' || key === 'lease_holder') value = value?.display_name ?? value?.id ?? '';
    if (key === 'staff_pools') value = (value || []).map((pool) => pool.name ?? pool.id).sort().join(', ');
    if (typeof value === 'string') {
        return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
    }
    return value;
}

/** Stable sort on the supplied page of rows; empty values go last. */
export function sortRows(rows, sort) {
    let out = rows.slice();
    for (const [key, dir] of [...sort].reverse()) {
        const present = out.filter((r) => r[key] !== null && r[key] !== undefined);
        const missing = out.filter((r) => r[key] === null || r[key] === undefined);
        present.sort((a, b) => {
            const x = rankOf(key, a[key]);
            const y = rankOf(key, b[key]);
            const c = x < y ? -1 : x > y ? 1 : 0;
            return dir === 'desc' ? -c : c;
        });
        out = present.concat(missing);
    }
    return out;
}

/** Group by raw values, first appearance first, nested up to three levels. */
export function groupRows(rows, groupBy, path = []) {
    if (!groupBy.length) return [{ path, rows, children: [] }];
    const [key, ...rest] = groupBy;
    const buckets = new Map();
    for (const row of rows) {
        const v = row[key] === undefined ? GROUP_ABSENT : row[key];
        // Role DTOs are separate objects per row; group by identity, retaining the label DTO.
        let identity = v;
        if (key === 'dictated_by' || key === 'lease_holder') identity = v?.id ?? v;
        if (key === 'staff_pools' && Array.isArray(v)) identity = JSON.stringify(v.map((pool) => pool.id).sort());
        if (!buckets.has(identity)) buckets.set(identity, { value: v, members: [] });
        buckets.get(identity).members.push(row);
    }
    return [...buckets.values()].map(({ value: v, members }) => {
        const here = [...path, v === GROUP_ABSENT ? null : v];
        return { path: here, rows: members, children: rest.length ? groupRows(members, rest, here) : [] };
    });
}

/**
 * @param {object} options
 * @param {{request: Function}} options.client createClinicApiClient() result
 * @param {{key: string, label: string}[]} options.columns columns the server allows
 * @param {object} [options.defaults] standard columns and sort for a new or reset layout
 * @param {() => Promise<object>} [options.loadColumns] clinic API result with additional column definitions
 * @param {() => Promise<object>} [options.onRestore] refresh the list after a successful reset
 * @param {(state: object) => object} [options.reactive] e.g. Vue.reactive
 * @param {() => string} [options.newIdempotencyKey]
 */
export function useWorklistLayout(options) {
    const { client, columns, defaults, loadColumns, onRestore } = options;
    let known = columns;
    const makeKey = options.newIdempotencyKey || newKey;
    const columnOf = (key) => known.find((c) => c.key === key);
    const labelOf = (key) => columnOf(key)?.label ?? key;
    const state = (options.reactive || ((s) => s))({
        status: 'idle', // idle | loading | ready | saving | stale | denied | error
        message: '',
        version: null,
        layout: normalizeLayout(defaults, known),
        undoLayout: null,
        dirty: false,
        canEdit: true,
        filterSignature: '',
        page: 1,
    });
    let pending = null; // { signature, key } so a retry of the same body reuses its key
    let savedLayout = null;

    function setOutcome(res) {
        state.message = res.message || '';
        if (res.outcome === 'permission') {
            state.status = 'denied';
            state.canEdit = false;
            state.message = LABELS.readOnly;
        } else if (res.outcome === 'conflict') {
            state.status = 'stale';
            state.message = LABELS.stale;
        } else {
            state.status = 'error';
        }
    }

    function apply(data, message = '') {
        const version = Number.isInteger(data?.version) ? data.version : null;
        if (version === null) {
            state.version = null;
            state.status = 'error';
            state.message = 'Das Ergebnis ist unbekannt.';
            return false;
        }
        state.version = version;
        state.layout = normalizeLayout(version === 0 && defaults ? defaults : data.layout, known);
        savedLayout = state.layout;
        state.undoLayout = null;
        state.dirty = false;
        state.status = 'ready';
        state.message = message;
        return true;
    }

    async function load() {
        if (busy()) return false;
        return loadCurrent();
    }

    async function loadCurrent() {
        state.status = 'loading';
        state.message = '';
        const [res, extra] = await Promise.all([client.request(LAYOUT_PATH, { method: 'GET' }), loadColumns?.()]);
        const failure = res.outcome !== 'ok' ? res : extra && extra.outcome !== 'ok' && extra;
        if (failure) {
            state.version = null;
            setOutcome(failure);
            return false;
        }
        if (extra) known = [...columns, ...extra.data];
        return apply(res.data);
    }

    const busy = () => state.status === 'loading' || state.status === 'saving';
    const canSave = () => state.canEdit && state.version !== null && !busy();

    async function save(next = state.layout, message = LABELS.saved) {
        if (!canSave()) return false;
        const keys = new Set(known.map((c) => c.key));
        const ordered = [...next.sort.map(([key]) => key), ...next.group_by];
        const referenced = [...next.columns.map((c) => c.key), ...ordered];
        if (referenced.some((key) => !keys.has(key))) {
            state.status = 'error';
            state.message = LABELS.unknownColumn;
            return false;
        }
        const body = normalizeLayout(next, known);
        for (let i = body.columns.length - 1; i >= 0 && body.columns.length > MAX_COLUMNS; i--) {
            const col = body.columns[i];
            if (!col.visible && columnOf(col.key)?.visible === false && !ordered.includes(col.key)) body.columns.splice(i, 1);
        }
        if (body.columns.length > MAX_COLUMNS) {
            state.status = 'error';
            state.message = LABELS.tooManyColumns;
            return false;
        }
        state.status = 'saving';
        state.message = '';
        const signature = `${state.version}:${JSON.stringify(body)}`;
        if (!pending || pending.signature !== signature) pending = { signature, key: makeKey() };
        const res = await client.request(LAYOUT_PATH, {
            method: 'PUT',
            body,
            ifMatch: `"${state.version}"`,
            idempotencyKey: pending.key,
        });
        if (res.outcome === 'ok') {
            pending = null;
            return apply(res.data, message);
        }
        if (res.outcome === 'conflict') {
            pending = null;
            if (await loadCurrent()) {
                state.status = 'stale';
                state.message = LABELS.stale;
            }
            return false;
        }
        setOutcome(res); // network/validation: keep local edits, retry reuses the key
        return false;
    }

    async function restoreDefaults() {
        const standard = normalizeLayout(defaults, known);
        const previous = JSON.stringify(savedLayout) === JSON.stringify(standard) ? null : savedLayout;
        if (!await save(standard, LABELS.restored)) return false;
        state.undoLayout = previous;
        resetPage();
        await onRestore?.();
        return true;
    }

    async function undoRestore() {
        if (!state.undoLayout || !await save(state.undoLayout, LABELS.undoDone)) return false;
        resetPage();
        await onRestore?.();
        return true;
    }

    function touch() {
        state.dirty = true;
        state.message = '';
        state.undoLayout = null;
        if (state.status === 'stale' || state.status === 'error') state.status = 'ready';
    }
    const index = (key) => state.layout.columns.findIndex((c) => c.key === key);
    const resetPage = () => { state.page = 1; };

    function move(key, delta) {
        if (!state.canEdit || busy()) return;
        const i = index(key);
        const j = i + delta;
        if (i < 0 || j < 0 || j >= state.layout.columns.length) return;
        const cols = state.layout.columns.slice();
        [cols[i], cols[j]] = [cols[j], cols[i]];
        state.layout = { ...state.layout, columns: cols };
        touch();
    }

    function resize(key, delta) {
        const i = index(key);
        if (!state.canEdit || busy() || i < 0) return;
        const cols = state.layout.columns.slice();
        cols[i] = { ...cols[i], width: clamp(cols[i].width + delta) };
        state.layout = { ...state.layout, columns: cols };
        touch();
    }

    function toggleVisible(key) {
        const i = index(key);
        if (!state.canEdit || busy() || i < 0) return;
        const cols = state.layout.columns.slice();
        cols[i] = { ...cols[i], visible: !cols[i].visible };
        state.layout = { ...state.layout, columns: cols };
        touch();
    }

    /** asc, then desc, then off. A changed sort restarts paging. */
    function cycleSort(key) {
        if (!state.canEdit || busy() || index(key) < 0) return;
        const cur = state.layout.sort.find((s) => s[0] === key);
        const rest = state.layout.sort.filter((s) => s[0] !== key);
        const next = !cur ? [[key, 'asc']] : cur[1] === 'asc' ? [[key, 'desc']] : [];
        state.layout = { ...state.layout, sort: [...next, ...rest] };
        resetPage();
        touch();
    }

    /** Sets a single sort key ("asc" or "desc"); an empty key clears the sort. A changed sort restarts paging. */
    function setSort(key, dir) {
        if (!state.canEdit || busy() || (key && index(key) < 0)) return;
        state.layout = { ...state.layout, sort: key ? [[key, dir === 'desc' ? 'desc' : 'asc']] : [] };
        resetPage();
        touch();
    }

    function setGroupBy(keys) {
        if (!state.canEdit || busy()) return;
        const valid = keys.filter((k, i) => index(k) >= 0 && keys.indexOf(k) === i).slice(0, MAX_GROUP_DEPTH);
        state.layout = { ...state.layout, group_by: valid };
        resetPage();
        touch();
    }

    /** Ordered levels cannot have gaps or duplicate fields; clearing trims deeper levels. */
    function setGroupLevel(level, key) {
        const groups = state.layout.group_by.slice();
        if (!Number.isInteger(level) || level < 0 || level >= MAX_GROUP_DEPTH || level > groups.length) return;
        if (key && (index(key) < 0 || groups.some((value, i) => i !== level && value === key))) return;
        if (key) groups[level] = key;
        else groups.splice(level);
        setGroupBy(groups);
    }

    /** Filters change: reset paging only if the filter content really changed. */
    function setFilters(filters) {
        const signature = JSON.stringify(Object.entries(filters || {}).sort(([a], [b]) => (a < b ? -1 : 1)));
        if (signature !== state.filterSignature) {
            state.filterSignature = signature;
            resetPage();
        }
    }

    function setPage(page) {
        if (Number.isInteger(page) && page >= 1) state.page = page;
    }

    /** Query for the worklist API. The id tie-breaker keeps page boundaries stable. */
    function query(filters) {
        const sort = state.layout.sort.map(([k, d]) => `${k}:${d}`);
        if (!state.layout.sort.some(([k]) => k === TIE_BREAKER)) sort.push(`${TIE_BREAKER}:asc`);
        return { ...(filters || {}), sort: sort.join(','), group_by: state.layout.group_by.join(','), page: state.page };
    }

    const orderedColumns = () => state.layout.columns
        .filter((c) => c.visible)
        .map((c) => ({ ...c, label: labelOf(c.key) }));

    function applyToRows(rows) {
        return groupRows(sortRows(rows, state.layout.sort), state.layout.group_by);
    }

    function sortState(key) {
        const s = state.layout.sort.find((e) => e[0] === key);
        return !s ? 'none' : s[1] === 'asc' ? 'ascending' : 'descending';
    }

    /** Attributes for a header cell; spread onto th (role columnheader). */
    function headerProps(key) {
        const sort = sortState(key);
        const text = sort === 'none' ? LABELS.sortNone : sort === 'ascending' ? LABELS.sortAsc : LABELS.sortDesc;
        return {
            role: 'columnheader',
            tabindex: 0,
            'aria-sort': sort,
            'aria-label': `${labelOf(key)}, ${text}`,
            style: { outline: 'none' },
            'data-focus-ring': FOCUS_INDICATOR.outline,
        };
    }

    /** Enter/Space sort, Alt+Arrow move, Shift+Arrow resize. Returns true if handled. */
    function onKeydown(key, event) {
        const left = event.key === 'ArrowLeft';
        const right = event.key === 'ArrowRight';
        let handled = true;
        if (event.key === 'Enter' || event.key === ' ') cycleSort(key);
        else if ((left || right) && event.altKey) move(key, left ? -1 : 1);
        else if ((left || right) && event.shiftKey) resize(key, left ? -WIDTH_STEP : WIDTH_STEP);
        else handled = false;
        if (handled && typeof event.preventDefault === 'function') event.preventDefault();
        return handled;
    }

    /** Status banner styling from the token contract; no hard-coded colours. */
    function banner() {
        if (!state.message) return null;
        const kind = state.status === 'ready' ? 'success' : state.status === 'stale' ? 'warning' : 'error';
        const s = surfaceFor(kind);
        return { text: state.message, role: kind === 'error' ? 'alert' : 'status', style: { background: s.background, color: s.color, border: `1px solid ${s.border}` } };
    }

    return {
        state, load, save, restoreDefaults, undoRestore, busy, canSave, move, resize, toggleVisible, cycleSort, setSort, setGroupBy, setGroupLevel,
        setFilters, setPage, query, orderedColumns, applyToRows, headerProps,
        onKeydown, banner, columnOf, labelOf,
    };
}

/**
 * Vue 3 (CDN global) component: header row plus group-by select and save bar.
 * Usage: WorklistLayoutControls(Vue) registered as a component with a `layout`
 * prop holding the useWorklistLayout() result.
 */
export function WorklistLayoutControls(Vue) {
    return Vue.defineComponent({
        name: 'WorklistLayoutControls',
        props: { layout: { type: Object, required: true } },
        setup(props) {
            const open = Vue.ref(false);
            const trigger = Vue.ref(null);
            const panelStyle = Vue.ref({});
            function fitPanel() {
                panelStyle.value = {};
                if (globalThis.innerWidth < 768 || !trigger.value) return;
                const anchor = trigger.value.getBoundingClientRect();
                const height = Math.min(620, globalThis.innerHeight - 32);
                panelStyle.value = {
                    position: 'fixed', maxHeight: height + 'px',
                    top: Math.max(16, Math.min(anchor.bottom + 8, globalThis.innerHeight - height - 16)) + 'px',
                    right: Math.max(16, globalThis.innerWidth - anchor.right) + 'px',
                };
            }
            function toggle() { fitPanel(); open.value = !open.value; }
            function restore() {
                if (props.layout.canSave() && globalThis.confirm(LABELS.confirmRestore)) return props.layout.restoreDefaults();
            }
            Vue.onMounted(() => globalThis.addEventListener('resize', fitPanel));
            Vue.onUnmounted(() => globalThis.removeEventListener('resize', fitPanel));
            return { LABELS, MAX_GROUP_DEPTH, L: props.layout, open, trigger, panelStyle, toggle, restore };
        },
        template: `
<div class="cl-pop" :aria-label="LABELS.region" role="group" @keydown.esc="open = false">
  <button ref="trigger" type="button" class="icon-btn" data-testid="view-toggle" aria-controls="wl-view-panel"
          :aria-expanded="open ? 'true' : 'false'" @click="toggle">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/></svg>{{ LABELS.view }}
    <span v-if="L.state.message && L.state.status !== 'ready'" class="cl-dot" aria-hidden="true"></span>
  </button>
  <div v-show="open" id="wl-view-panel" class="cl-popover" role="group" :aria-label="LABELS.view" data-testid="view-panel"
       :style="panelStyle"
       style="background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); color: var(--v8-text)">
    <div class="cl-panel__head">
      <h3 class="cl-panel__title" style="margin: 0">{{ LABELS.view }}</h3>
      <button type="button" class="icon-button closebtn" :aria-label="LABELS.close" @click="open = false"><svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18"/></svg></button>
    </div>
    <p class="cl-sr-only">{{ LABELS.keys }}</p>
    <span class="cl-section-label" id="wl-cols-label">{{ LABELS.columns }}</span>
    <ul class="cl-checklist" role="list" aria-labelledby="wl-cols-label"
        style="grid-template-columns: minmax(0, 1fr); max-height: 10rem; overflow: auto">
      <li v-for="(c, index) in L.state.layout.columns" :key="c.key" :class="{ 'cl-xml-option': L.columnOf(c.key)?.type }"
          style="display: flex; align-items: center; gap: var(--sp-1)">
        <label class="cl-check" style="flex: 1; min-width: 0">
          <input type="checkbox" :checked="c.visible" :disabled="!L.state.canEdit || L.busy()" :aria-label="(c.visible ? LABELS.hide : LABELS.show) + ': ' + L.labelOf(c.key)"
                 @change="L.toggleVisible(c.key)">
          <span style="overflow-wrap: anywhere">{{ L.labelOf(c.key) }}</span>
        </label>
        <button type="button" class="icon-btn" :aria-label="LABELS.moveLeft + ': ' + L.labelOf(c.key)" :title="LABELS.moveLeft"
                :disabled="!L.state.canEdit || L.busy() || index === 0" @click="L.move(c.key, -1)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>
        </button>
        <button type="button" class="icon-btn" :aria-label="LABELS.moveRight + ': ' + L.labelOf(c.key)" :title="LABELS.moveRight"
                :disabled="!L.state.canEdit || L.busy() || index === L.state.layout.columns.length - 1" @click="L.move(c.key, 1)">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg>
        </button>
      </li>
    </ul>
    <div v-for="level in MAX_GROUP_DEPTH" :key="level">
      <label class="cl-section-label" :for="level === 1 ? 'wl-group-by' : 'wl-group-by-' + level">{{ LABELS.groupBy }} · {{ LABELS.groupLevel }} {{ level }}</label>
      <select :id="level === 1 ? 'wl-group-by' : 'wl-group-by-' + level" class="select" style="width: 100%; height: auto; margin: 0; appearance: auto"
              :disabled="!L.state.canEdit || L.busy() || level > L.state.layout.group_by.length + 1" :value="L.state.layout.group_by[level - 1] || ''"
              @change="L.setGroupLevel(level - 1, $event.target.value)">
        <option value="">{{ LABELS.groupNone }}</option>
        <option v-for="c in L.state.layout.columns" :key="c.key" :value="c.key"
                :disabled="L.state.layout.group_by.includes(c.key) && L.state.layout.group_by[level - 1] !== c.key">{{ L.labelOf(c.key) }}</option>
      </select>
    </div>
    <div class="cl-panel__foot cl-layout-actions">
      <p v-if="L.banner()" :role="L.banner().role" class="cl-note" style="flex-basis: 100%; margin: 0"
         :style="L.banner().style">{{ L.banner().text }}</p>
      <button type="button" class="primary" style="flex: none; white-space: nowrap"
              :disabled="!L.canSave() || !L.state.dirty" @click="L.save()">{{ LABELS.save }}</button>
      <button type="button" class="secondary" style="flex: none; white-space: nowrap"
              :disabled="L.busy()" @click="L.load()">{{ LABELS.reload }}</button>
      <button type="button" class="secondary" style="white-space: normal" :disabled="!L.canSave()"
              @click="restore">{{ LABELS.restore }}</button>
      <button v-if="L.state.undoLayout" type="button" class="secondary" :disabled="!L.canSave()"
              @click="L.undoRestore()">{{ LABELS.undoRestore }}</button>
    </div>
  </div>
</div>`,
    });
}
