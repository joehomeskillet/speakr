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
    save: 'Layout speichern',
    reload: 'Neu laden',
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
 * append columns the server knows but the saved layout lacks (visible).
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
        if (!seen.has(c.key)) columns.push({ key: c.key, width: 120, visible: true });
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
        if (!buckets.has(v)) buckets.set(v, []);
        buckets.get(v).push(row);
    }
    return [...buckets].map(([v, members]) => {
        const here = [...path, v === GROUP_ABSENT ? null : v];
        return { path: here, rows: members, children: rest.length ? groupRows(members, rest, here) : [] };
    });
}

/**
 * @param {object} options
 * @param {{request: Function}} options.client createClinicApiClient() result
 * @param {{key: string, label: string}[]} options.columns columns the server allows
 * @param {(state: object) => object} [options.reactive] e.g. Vue.reactive
 * @param {() => string} [options.newIdempotencyKey]
 */
export function useWorklistLayout(options) {
    const { client, columns: known } = options;
    const makeKey = options.newIdempotencyKey || newKey;
    const labelOf = (key) => known.find((c) => c.key === key)?.label ?? key;
    const state = (options.reactive || ((s) => s))({
        status: 'idle', // idle | loading | ready | saving | stale | denied | error
        message: '',
        version: null,
        layout: normalizeLayout(null, known),
        dirty: false,
        canEdit: true,
        filterSignature: '',
        page: 1,
    });
    let pending = null; // { signature, key } so a retry of the same body reuses its key

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

    function apply(data) {
        const version = Number.isInteger(data?.version) ? data.version : null;
        if (version === null) {
            state.status = 'error';
            state.message = 'Das Ergebnis ist unbekannt.';
            return false;
        }
        state.version = version;
        state.layout = normalizeLayout(data.layout, known);
        state.dirty = false;
        state.status = 'ready';
        state.message = '';
        return true;
    }

    async function load() {
        state.status = 'loading';
        const res = await client.request(LAYOUT_PATH, { method: 'GET' });
        if (res.outcome === 'ok') return apply(res.data);
        setOutcome(res);
        return false;
    }

    async function save() {
        if (!state.canEdit || state.version === null) return false;
        state.status = 'saving';
        const body = normalizeLayout(state.layout, known);
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
            return apply(res.data);
        }
        if (res.outcome === 'conflict') {
            pending = null;
            await load();
            state.status = 'stale';
            state.message = LABELS.stale;
            return false;
        }
        setOutcome(res); // network/validation: keep local edits, retry reuses the key
        return false;
    }

    function touch() {
        state.dirty = true;
        if (state.status === 'stale' || state.status === 'error') state.status = 'ready';
    }
    const index = (key) => state.layout.columns.findIndex((c) => c.key === key);
    const resetPage = () => { state.page = 1; };

    function move(key, delta) {
        if (!state.canEdit) return;
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
        if (!state.canEdit || i < 0) return;
        const cols = state.layout.columns.slice();
        cols[i] = { ...cols[i], width: clamp(cols[i].width + delta) };
        state.layout = { ...state.layout, columns: cols };
        touch();
    }

    function toggleVisible(key) {
        const i = index(key);
        if (!state.canEdit || i < 0) return;
        const cols = state.layout.columns.slice();
        cols[i] = { ...cols[i], visible: !cols[i].visible };
        state.layout = { ...state.layout, columns: cols };
        touch();
    }

    /** asc, then desc, then off. A changed sort restarts paging. */
    function cycleSort(key) {
        if (!state.canEdit || index(key) < 0) return;
        const cur = state.layout.sort.find((s) => s[0] === key);
        const rest = state.layout.sort.filter((s) => s[0] !== key);
        const next = !cur ? [[key, 'asc']] : cur[1] === 'asc' ? [[key, 'desc']] : [];
        state.layout = { ...state.layout, sort: [...next, ...rest] };
        resetPage();
        touch();
    }

    function setGroupBy(keys) {
        if (!state.canEdit) return;
        const valid = keys.filter((k, i) => index(k) >= 0 && keys.indexOf(k) === i).slice(0, MAX_GROUP_DEPTH);
        state.layout = { ...state.layout, group_by: valid };
        resetPage();
        touch();
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
        const kind = state.status === 'stale' ? 'warning' : 'error';
        const s = surfaceFor(kind);
        return { text: state.message, role: kind === 'error' ? 'alert' : 'status', style: { background: s.background, color: s.color, border: `1px solid ${s.border}` } };
    }

    return {
        state, load, save, move, resize, toggleVisible, cycleSort, setGroupBy,
        setFilters, setPage, query, orderedColumns, applyToRows, headerProps,
        onKeydown, banner, labelOf,
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
            return { LABELS, L: props.layout, open };
        },
        template: `
<div class="cl-pop" :aria-label="LABELS.region" role="group" @keydown.esc="open = false">
  <button type="button" class="btn btn--ghost cl-btn" data-testid="view-toggle" aria-controls="wl-view-panel"
          :aria-expanded="open ? 'true' : 'false'" @click="open = !open">
    <i class="fa-solid fa-table-columns" aria-hidden="true"></i>{{ LABELS.view }}
    <span v-if="L.banner()" class="cl-dot" aria-hidden="true"></span>
  </button>
  <div v-show="open" id="wl-view-panel" class="cl-popover" role="group" :aria-label="LABELS.view" data-testid="view-panel">
    <div class="cl-panel__head">
      <h3 class="cl-panel__title">{{ LABELS.view }}</h3>
      <button type="button" class="btn btn--quiet btn--icon" :aria-label="LABELS.close" @click="open = false"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>
    </div>
    <p v-if="L.banner()" :role="L.banner().role" class="cl-note" :style="L.banner().style">{{ L.banner().text }}</p>
    <p class="cl-sr-only">{{ LABELS.keys }}</p>
    <span class="cl-section-label" id="wl-cols-label">{{ LABELS.columns }}</span>
    <ul class="cl-checklist" role="group" aria-labelledby="wl-cols-label">
      <li v-for="c in L.state.layout.columns" :key="c.key">
        <label class="cl-check">
          <input type="checkbox" :checked="c.visible" :disabled="!L.state.canEdit" :aria-label="(c.visible ? LABELS.hide : LABELS.show) + ': ' + L.labelOf(c.key)"
                 @change="L.toggleVisible(c.key)">
          <span>{{ L.labelOf(c.key) }}</span>
        </label>
      </li>
    </ul>
    <label class="cl-section-label" for="wl-group-by">{{ LABELS.groupBy }}</label>
    <select id="wl-group-by" class="field" :disabled="!L.state.canEdit" :value="L.state.layout.group_by[0] || ''"
            @change="L.setGroupBy($event.target.value ? [$event.target.value] : [])">
      <option value="">{{ LABELS.groupNone }}</option>
      <option v-for="c in L.state.layout.columns" :key="c.key" :value="c.key">{{ L.labelOf(c.key) }}</option>
    </select>
    <div class="cl-panel__foot">
      <button type="button" class="btn btn--primary" :disabled="!L.state.canEdit || !L.state.dirty || L.state.status === 'saving'"
              @click="L.save()">{{ LABELS.save }}</button>
      <button type="button" class="btn btn--ghost" @click="L.load()">{{ LABELS.reload }}</button>
    </div>
  </div>
</div>`,
    });
}
