/**
 * Named worklist filters and metadata search (PAR-WF-003).
 *
 * `createWorklistFilters` is a composable around the clinic API client
 * (api-client.js). Pass Vue's `reactive` to make the state reactive. Filter
 * specs and the search text stay in memory. Browser storage keeps only the
 * id of the last selected filter, per user, and it is restored only when
 * that id is in the current user's server list.
 *
 * Server contract used here (relative to /api/clinic/v1):
 *   GET    /worklist/filters
 *   GET    /worklist/filters/<id>       -> {id, name, version, spec}
 *   POST   /worklist/filters            {name, spec}
 *   PUT    /worklist/filters/<id>       {name, spec}  If-Match: <version>
 *   DELETE /worklist/filters/<id>                     If-Match: <version>
 * Operators, fields and the limit mirror src/clinic/saved_filters.py.
 */

import { surfaceFor, FOCUS_INDICATOR } from './tokens.js';

export const MAX_FILTERS = 20;
export const MAX_NAME_LENGTH = 60;
export const MAX_CONDITIONS = 20;
export const MAX_QUERY_LENGTH = 200;
export const FIELDS = ['status', 'priority', 'author', 'pool', 'title', 'created', 'deadline', 'assignee', 'audio_seconds'];
export const OPERATORS = ['eq', 'ne', 'contains', 'startswith', 'lt', 'le', 'gt', 'ge', 'in', 'is_null'];

const META_KEY = /^[a-z0-9_]{1,40}$/;
const BASE = '/worklist/filters';
const STORAGE_PREFIX = 'clinic.worklist.filter.';

export const LABELS = Object.freeze({
    title: 'Gespeicherte Filter',
    select: 'Filter auswählen',
    none: 'Kein Filter',
    name: 'Name des Filters',
    field: 'Feld',
    op: 'Operator',
    value: 'Wert',
    search: 'Metadatensuche',
    addCondition: 'Bedingung hinzufügen',
    removeCondition: 'Bedingung entfernen',
    save: 'Speichern',
    saveNew: 'Als neuen Filter speichern',
    remove: 'Filter löschen',
    reload: 'Erneut laden',
    count: '{n} von {max} Filtern',
    ops: {
        eq: 'ist gleich', ne: 'ist ungleich', contains: 'enthält', startswith: 'beginnt mit',
        lt: 'kleiner als', le: 'höchstens', gt: 'grösser als', ge: 'mindestens',
        in: 'ist eines von', is_null: 'ist leer',
    },
    fields: {
        status: 'Status', priority: 'Priorität', author: 'Autor', pool: 'Pool', title: 'Titel',
        created: 'Erstellt', deadline: 'Frist', assignee: 'Zuständig', audio_seconds: 'Audiodauer',
    },
    errors: {
        name_required: 'Bitte einen Namen eingeben.',
        name_too_long: 'Der Name darf höchstens 60 Zeichen haben.',
        unknown_field: 'Dieses Feld wird nicht unterstützt.',
        unknown_op: 'Dieser Operator wird nicht unterstützt.',
        bad_value: 'Der Wert ist ungültig.',
        too_many_conditions: 'Es sind höchstens 20 Bedingungen erlaubt.',
        query_too_long: 'Die Suche darf höchstens 200 Zeichen haben.',
        limit_reached: 'Es sind höchstens 20 Filter möglich.',
        conflict: 'Der Filter wurde inzwischen geändert und wurde neu geladen.',
        permission: 'Keine Berechtigung für diese Aktion.',
        network: 'Die Verbindung ist fehlgeschlagen. Bitte erneut versuchen.',
        UNKNOWN: 'Das Ergebnis ist unbekannt.',
        cancelled: 'Die Anfrage wurde abgebrochen.',
        validation: 'Die Eingabe ist ungültig.',
    },
});

function emptyCondition() {
    return { field: 'status', op: 'eq', value: '' };
}

/** Returns an error key or null for one draft condition. */
export function validateCondition(cond) {
    if (!cond || typeof cond !== 'object') return 'bad_value';
    const field = cond.field;
    if (typeof field !== 'string') return 'unknown_field';
    const known = FIELDS.includes(field) || (field.startsWith('meta.') && META_KEY.test(field.slice(5)));
    if (!known) return 'unknown_field';
    if (!OPERATORS.includes(cond.op)) return 'unknown_op';
    if (cond.op === 'is_null') return null;
    const text = String(cond.value ?? '').trim();
    if (!text) return 'bad_value';
    if (cond.op === 'in') {
        const parts = text.split(',').map((p) => p.trim());
        if (parts.some((p) => !p) || parts.length > 50) return 'bad_value';
    }
    return null;
}

/** Returns an error key or null for a whole draft (name, conditions). */
export function validateDraft(draft) {
    const name = String(draft.name ?? '').trim();
    if (!name) return 'name_required';
    if (name.length > MAX_NAME_LENGTH) return 'name_too_long';
    if (draft.conditions.length > MAX_CONDITIONS) return 'too_many_conditions';
    for (const cond of draft.conditions) {
        const bad = validateCondition(cond);
        if (bad) return bad;
    }
    return null;
}

function toValue(cond) {
    if (cond.op === 'is_null') return String(cond.value) !== 'false';
    const text = String(cond.value).trim();
    if (cond.op === 'in') return text.split(',').map((p) => p.trim());
    return text;
}

export function draftToSpec(draft) {
    return { all: draft.conditions.map((c) => ({ field: c.field, op: c.op, value: toValue(c) })), any: [] };
}

function specToConditions(spec) {
    const rows = [...((spec && spec.all) || []), ...((spec && spec.any) || [])];
    return rows.map((c) => ({
        field: c.field,
        op: c.op,
        value: Array.isArray(c.value) ? c.value.join(', ') : c.value === true || c.value === false ? String(c.value) : String(c.value ?? ''),
    }));
}

function storageKey(userId) {
    return STORAGE_PREFIX + String(userId);
}

function readLast(storage, userId) {
    try {
        return storage ? storage.getItem(storageKey(userId)) : null;
    } catch (e) {
        return null;
    }
}

function writeLast(storage, userId, id) {
    try {
        if (!storage) return;
        if (id) storage.setItem(storageKey(userId), id);
        else storage.removeItem(storageKey(userId));
    } catch (e) {
        // Storage may be blocked. The selection then simply is not remembered.
    }
}

/**
 * @param {{client: {request: Function}, userId: number|string, storage?: object,
 *          reactive?: Function, onApply?: Function}} options
 */
export function createWorklistFilters(options) {
    const { client, userId } = options;
    if (!client || typeof client.request !== 'function' || userId == null) {
        throw new Error('client and userId are required');
    }
    const storage = options.storage === undefined ? (typeof localStorage !== 'undefined' ? localStorage : null) : options.storage;
    const reactive = options.reactive || ((x) => x);
    const onApply = options.onApply || (() => {});

    const state = reactive({
        filters: [],
        selectedId: null,
        draft: { name: '', conditions: [] },
        query: '',
        loading: false,
        busy: false,
        loaded: false,
        specLoaded: true,
        error: null,
        message: '',
        limitReached: false,
    });

    function fail(key) {
        state.error = key;
        state.message = LABELS.errors[key] || LABELS.errors.UNKNOWN;
        return false;
    }

    function clearMessage() {
        state.error = null;
        state.message = '';
    }

    function refreshLimit() {
        state.limitReached = state.filters.length >= MAX_FILTERS;
    }

    function selected() {
        return state.filters.find((f) => f.id === state.selectedId) || null;
    }

    function failFromResult(res) {
        if (res.outcome === 'conflict') {
            fail('conflict');
            return 'conflict';
        }
        if (res.code === 'CAPABILITY_UNAVAILABLE') return fail('limit_reached'), 'limit';
        fail(LABELS.errors[res.outcome] ? res.outcome : 'UNKNOWN');
        return res.outcome;
    }

    async function load() {
        state.loading = true;
        clearMessage();
        try {
            const res = await client.request(BASE);
            if (res.outcome !== 'ok') return fail(LABELS.errors[res.outcome] ? res.outcome : 'UNKNOWN');
            const items = res.data && Array.isArray(res.data.items) ? res.data.items : [];
            state.filters = items.map((f) => ({ id: f.id, name: f.name, version: f.version, spec: f.spec || null }));
            state.loaded = true;
            refreshLimit();
            if (!selected()) {
                const last = readLast(storage, userId);
                const known = last && state.filters.some((f) => f.id === last);
                if (known) await select(last, true);
                else {
                    state.selectedId = null;
                    writeLast(storage, userId, null);
                }
            } else {
                await select(state.selectedId, true);
            }
            return true;
        } finally {
            state.loading = false;
        }
    }

    // The list may omit specs. The spec and version used for editing always come
    // from GET /filters/<id>; until it arrives writes stay blocked (specLoaded).
    async function select(id, silent) {
        const found = id ? state.filters.find((f) => f.id === id) : null;
        state.selectedId = found ? found.id : null;
        writeLast(storage, userId, state.selectedId);
        state.draft = { name: found ? found.name : '', conditions: [] };
        state.specLoaded = !found;
        if (!silent) clearMessage();
        onApply({ filterId: state.selectedId, query: state.query });
        if (!found) return true;
        const res = await client.request(BASE + '/' + encodeURIComponent(found.id));
        if (state.selectedId !== found.id) return false; // a newer selection won
        if (res.outcome !== 'ok' || !res.data || typeof res.data.spec !== 'object' || res.data.spec === null) {
            return fail(res.outcome !== 'ok' && LABELS.errors[res.outcome] ? res.outcome : 'UNKNOWN');
        }
        found.name = res.data.name;
        found.version = res.data.version;
        found.spec = res.data.spec;
        state.draft = { name: found.name, conditions: specToConditions(found.spec) };
        state.specLoaded = true;
        return true;
    }

    function setQuery(text) {
        state.query = String(text ?? '');
        if (state.query.length > MAX_QUERY_LENGTH) return fail('query_too_long');
        clearMessage();
        onApply({ filterId: state.selectedId, query: state.query });
        return true;
    }

    function addCondition() {
        if (state.draft.conditions.length >= MAX_CONDITIONS) return fail('too_many_conditions');
        state.draft.conditions.push(emptyCondition());
        return true;
    }

    function removeCondition(index) {
        state.draft.conditions.splice(index, 1);
    }

    async function save(asNew) {
        if (!state.specLoaded) return false;
        const bad = validateDraft(state.draft);
        if (bad) return fail(bad);
        const current = selected();
        const creating = asNew || !current;
        if (creating && state.filters.length >= MAX_FILTERS) return fail('limit_reached');
        state.busy = true;
        clearMessage();
        try {
            const body = { name: state.draft.name.trim(), spec: draftToSpec(state.draft) };
            const res = creating
                ? await client.request(BASE, { method: 'POST', body })
                : await client.request(BASE + '/' + encodeURIComponent(current.id), {
                    method: 'PUT', body, ifMatch: String(current.version),
                });
            if (res.outcome !== 'ok') {
                const kind = failFromResult(res);
                if (kind === 'conflict') {
                    await load();
                    fail('conflict');
                }
                return false;
            }
            const keep = res.data && res.data.id ? res.data.id : current && current.id;
            await load();
            if (keep && state.selectedId !== keep) await select(keep, true);
            return true;
        } finally {
            state.busy = false;
        }
    }

    async function remove() {
        const current = selected();
        if (!current || !state.specLoaded) return false;
        state.busy = true;
        clearMessage();
        try {
            const res = await client.request(BASE + '/' + encodeURIComponent(current.id), {
                method: 'DELETE', ifMatch: String(current.version),
            });
            if (res.outcome !== 'ok') {
                if (failFromResult(res) === 'conflict') {
                    await load();
                    fail('conflict');
                }
                return false;
            }
            state.selectedId = null;
            writeLast(storage, userId, null);
            await load();
            await select(null, true);
            return true;
        } finally {
            state.busy = false;
        }
    }

    return { state, load, select, setQuery, addCondition, removeCondition, save, remove, validateDraft };
}

const ERROR_SURFACE = surfaceFor('error');
const BOX = `border:1px solid var(--border-primary);background:var(--bg-secondary);color:var(--text-primary);`;
const FOCUS_NOTE = FOCUS_INDICATOR.applies; // keyboard ring comes from the global :focus-visible rule.

/**
 * Vue component. `controller` is the result of createWorklistFilters made
 * with Vue's `reactive`. All controls are native elements, so Tab, Enter,
 * Space and arrow keys behave as the browser defines. Errors use role="alert".
 */
export const WorklistFiltersPanel = {
    name: 'WorklistFiltersPanel',
    props: { controller: { type: Object, required: true } },
    data() {
        return { labels: LABELS, fields: FIELDS, operators: OPERATORS, max: MAX_FILTERS, focusNote: FOCUS_NOTE, boxStyle: BOX, errorStyle: `background:${ERROR_SURFACE.background};color:${ERROR_SURFACE.color};border:1px solid ${ERROR_SURFACE.border};` };
    },
    computed: {
        s() { return this.controller.state; },
        countLabel() { return this.labels.count.replace('{n}', this.s.filters.length).replace('{max}', this.max); },
    },
    mounted() { if (!this.s.loaded) this.controller.load(); },
    template: `
<section aria-labelledby="wf-title" :style="boxStyle" :aria-busy="s.loading || s.busy ? 'true' : 'false'">
  <h2 id="wf-title">{{ labels.title }}</h2>
  <p id="wf-count" aria-live="polite">{{ countLabel }}</p>
  <div role="status" aria-live="polite">
    <p v-if="s.message && !s.error">{{ s.message }}</p>
  </div>
  <p v-if="s.error" role="alert" :style="errorStyle">{{ s.message }}
    <button type="button" v-if="s.error === 'network'" @click="controller.load()">{{ labels.reload }}</button>
  </p>
  <label for="wf-select">{{ labels.select }}</label>
  <select id="wf-select" :value="s.selectedId || ''" @change="controller.select($event.target.value || null)">
    <option value="">{{ labels.none }}</option>
    <option v-for="f in s.filters" :key="f.id" :value="f.id">{{ f.name }}</option>
  </select>
  <label for="wf-search">{{ labels.search }}</label>
  <input id="wf-search" type="search" maxlength="200" autocomplete="off" :value="s.query"
         @keydown.enter.prevent="controller.setQuery($event.target.value)"
         @change="controller.setQuery($event.target.value)">
  <form @submit.prevent="controller.save(false)" novalidate>
    <label for="wf-name">{{ labels.name }}</label>
    <input id="wf-name" type="text" maxlength="60" autocomplete="off" v-model="s.draft.name"
           :readonly="!s.specLoaded"
           :aria-invalid="s.error === 'name_required' || s.error === 'name_too_long' ? 'true' : 'false'">
    <fieldset v-for="(c, i) in s.draft.conditions" :key="i">
      <legend>{{ labels.fields[c.field] || c.field }}</legend>
      <label :for="'wf-f' + i">{{ labels.field }}</label>
      <select :id="'wf-f' + i" v-model="c.field">
        <option v-for="f in fields" :key="f" :value="f">{{ labels.fields[f] }}</option>
      </select>
      <label :for="'wf-o' + i">{{ labels.op }}</label>
      <select :id="'wf-o' + i" v-model="c.op">
        <option v-for="o in operators" :key="o" :value="o">{{ labels.ops[o] }}</option>
      </select>
      <template v-if="c.op !== 'is_null'">
        <label :for="'wf-v' + i">{{ labels.value }}</label>
        <input :id="'wf-v' + i" type="text" autocomplete="off" v-model="c.value">
      </template>
      <button type="button" @click="controller.removeCondition(i)">{{ labels.removeCondition }}</button>
    </fieldset>
    <button type="button" @click="controller.addCondition()">{{ labels.addCondition }}</button>
    <button type="submit" :disabled="s.busy || !s.specLoaded">{{ labels.save }}</button>
    <button type="button" :disabled="s.busy || !s.specLoaded || s.limitReached || !s.selectedId" @click="controller.save(true)">{{ labels.saveNew }}</button>
    <button type="button" :disabled="s.busy || !s.specLoaded || !s.selectedId" @click="controller.remove()">{{ labels.remove }}</button>
  </form>
</section>`,
};
