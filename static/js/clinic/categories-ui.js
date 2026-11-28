/**
 * Category set editor and send-form preview (PAR-CFG-003).
 *
 * Sets come from GET /api/clinic/v1/categories. Edits stay in memory as drafts
 * until saved through the categories API with the set version as expected
 * revision for updates. The same match rule as the server (case-insensitive
 * substring, src/clinic/presets.py validate_category_requirement) decides
 * whether a pending selection stays valid, so a removal that would invalidate
 * one is rejected before any request. Clinical selection values are never
 * written to browser storage. Status colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

export const CATEGORIES = '/categories';

export const LABELS = Object.freeze({
    title: 'Kategorien',
    required: 'Pflichtfeld',
    add: 'Kategorie hinzufügen',
    remove: 'Entfernen',
    save: 'Speichern',
    preview: 'Vorschau Sendeformular',
    name: 'Neue Kategorie',
    list: 'Kategorien der Gruppe',
});

const REASONS = Object.freeze({
    empty_category: 'Eine Kategorie darf nicht leer sein.',
    duplicate_category: 'Diese Kategorie existiert bereits.',
    invalid_name: 'Geben Sie einen Namen für das Kategorieset ein (maximal 128 Zeichen).',
    unsaved_set: 'Speichern oder verwerfen Sie zuerst das neue Kategorieset.',
    empty_set: 'Fügen Sie mindestens eine Kategorie hinzu.',
    no_category: 'Eine Pflichtgruppe braucht mindestens eine Kategorie.',
    pending_selection_invalid: 'Eine ausstehende Auswahl wäre danach ungültig.',
    unknown_set: 'Die Kategoriegruppe ist unbekannt.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    failure: 'Das Speichern ist fehlgeschlagen.',
    create_unconfirmed: 'Anlegen nicht bestätigt. Laden Sie die Kategorien neu, bevor Sie es erneut versuchen.',
});

// Keyboard states: Enter adds, Delete/Backspace removes the focused entry.
export const KEYS = Object.freeze({ add: 'Enter', remove: ['Delete', 'Backspace'] });

function matches(categories, value) {
    const text = String(value || '').trim().toLowerCase();
    return text !== '' && categories.some((cat) => text.includes(cat.toLowerCase()));
}

function refuse(code) {
    return { ok: false, code: code, message: REASONS[code] || REASONS.failure };
}

function editableSet(item) {
    return {
        id: item.id,
        name: item.name,
        version: item.version,
        categories: item.categories.slice(),
        required: !!item.required,
        targetField: item.target_field,
        scope: item.scope,
        stale: false,
    };
}

/**
 * @param {{client?: object}} [options]
 */
export function createCategoriesEditor(options) {
    const client = (options && options.client) || createClinicApiClient();
    const wrap = (options && options.reactive) || ((value) => value);
    const state = wrap({
        loaded: false,
        sets: [], // { id, name, version, categories, required, targetField, stale }
        selections: {}, // setId -> pending value (memory only)
        status: 'normal',
    });

    function find(setId) {
        return state.sets.find((row) => row.id === setId) || null;
    }

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    // The draft is always valid for what the caller has pending.
    function pendingBreaks(row, categories, required) {
        const value = state.selections[row.id];
        if (value === undefined || value === '') return false;
        if (!required) return false;
        return !matches(categories, value);
    }

    async function load() {
        setStatus('processing');
        const row = await client.request(CATEGORIES);
        if (row.outcome !== 'ok') {
            setStatus(row.outcome === 'permission' ? 'warning' : 'error');
            return { ok: false, code: row.outcome === 'permission' ? 'permission' : 'failure', message: row.message };
        }
        const sets = (row.data && row.data.sets) || [];
        state.sets = sets.map(editableSet);
        state.loaded = true;
        setStatus('success');
        return { ok: true };
    }

    function select(setId, value) {
        if (typeof value === 'string' && value !== '') state.selections[setId] = value;
        else delete state.selections[setId];
    }

    function createSet(name) {
        const clean = name.trim();
        if (!clean || clean.length > 128) return refuse('invalid_name');
        if (find(null)) return refuse('unsaved_set');
        state.sets.unshift(editableSet({ id: null, name: clean, categories: [], target_field: 'notes' }));
        return { ok: true };
    }

    function cancelCreate() {
        state.sets = state.sets.filter((row) => row.id !== null);
        return { ok: true };
    }

    function addCategory(setId, name) {
        const row = find(setId);
        if (!row) return refuse('unknown_set');
        const clean = typeof name === 'string' ? name.trim() : '';
        if (!clean) return refuse('empty_category');
        if (row.categories.some((cat) => cat.toLowerCase() === clean.toLowerCase())) return refuse('duplicate_category');
        row.categories.push(clean);
        return { ok: true };
    }

    function removeCategory(setId, name) {
        const row = find(setId);
        if (!row) return refuse('unknown_set');
        const next = row.categories.filter((cat) => cat !== name);
        if (next.length === row.categories.length) return { ok: true };
        if (row.required && next.length === 0) return refuse('no_category');
        if (pendingBreaks(row, next, row.required)) return refuse('pending_selection_invalid');
        row.categories = next;
        return { ok: true };
    }

    function setRequired(setId, required) {
        const row = find(setId);
        if (!row) return refuse('unknown_set');
        const flag = !!required;
        if (flag && row.categories.length === 0) return refuse('no_category');
        if (pendingBreaks(row, row.categories, flag)) return refuse('pending_selection_invalid');
        row.required = flag;
        return { ok: true };
    }

    /** Send-form rows as the draft would produce them. */
    function preview() {
        return state.sets.map((row) => {
            const value = state.selections[row.id] || '';
            return {
                setId: row.id,
                label: row.name,
                field: row.targetField,
                required: row.required,
                options: row.categories.slice(),
                satisfied: !row.required || matches(row.categories, value),
                ariaLabel: row.name + (row.required ? ' (' + LABELS.required + ')' : ''),
            };
        });
    }

    async function save(setId) {
        const row = find(setId);
        if (!row) return refuse('unknown_set');
        if (row.stale) return refuse('stale_version');
        if (row.categories.length === 0) return refuse('empty_set');
        setStatus('processing');
        const creating = row.id === null;
        const body = { categories: row.categories.slice(), required: row.required };
        const result = await client.request(CATEGORIES, creating
            ? { method: 'POST', body: { ...body, name: row.name } }
            : { method: 'PUT', body: { ...body, id: row.id, version: row.version } });
        if (result.outcome === 'ok' && result.data?.id && Number.isInteger(result.data.version)) {
            Object.assign(row, editableSet(result.data));
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            row.stale = true;
            setStatus('warning');
            return refuse('stale_version');
        }
        setStatus(result.outcome === 'permission' ? 'warning' : 'error');
        if (creating && !['permission', 'validation'].includes(result.outcome)) {
            row.stale = true;
            return refuse('create_unconfirmed');
        }
        return refuse(result.outcome === 'permission' ? 'permission' : 'failure');
    }

    /** Keyboard handler for the entry input and entry buttons. */
    function onKey(setId, event, value) {
        if (!event) return { ok: false };
        if (event.key === KEYS.add) return addCategory(setId, value);
        if (KEYS.remove.includes(event.key)) return removeCategory(setId, value);
        return { ok: false };
    }

    function reset() {
        state.sets = [];
        state.selections = {};
        state.loaded = false;
        state.status = 'normal';
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, select, createSet, cancelCreate, addCategory, removeCategory, setRequired, preview, save, onKey, reset };
}

/**
 * Vue composable. `vue` supplies { reactive } (the global Vue build or an
 * import). The returned editor shares one reactive state object.
 */
export function useCategoriesEditor(vue, options) {
    const editor = createCategoriesEditor(Object.assign({}, options, { reactive: vue.reactive }));
    return Object.assign(editor, { labels: LABELS, keys: KEYS });
}

/**
 * Component definition for the admin mount (w226). Markup carries labels and
 * roles; the colours are CSS variables from tokens.js.
 */
export function categoriesComponent(vue, options) {
    return {
        name: 'ClinicCategories',
        setup() {
            const editor = useCategoriesEditor(vue, options);
            const canManage = options?.canManage === true;
            const name = vue.ref('');
            const feedback = vue.ref('');
            const report = (result, success = '') => { feedback.value = result.ok ? success : result.message; };
            const load = async () => report(await editor.load());
            void load();
            return {
                editor, canManage, name, feedback, report, load,
                create() {
                    const result = editor.createSet(name.value);
                    report(result);
                    if (result.ok) name.value = '';
                },
                add(row, input) {
                    const result = editor.addCategory(row.id, input.value);
                    report(result);
                    if (result.ok) input.value = '';
                },
                toggle(row, input) {
                    report(editor.setRequired(row.id, input.checked));
                    input.checked = row.required;
                },
                async save(row) { report(await editor.save(row.id), row.name + ' gespeichert.'); },
            };
        },
        template: `
<section role="region" aria-labelledby="clinic-cat-title" :aria-busy="editor.state.status === 'processing'" class="v8-page v8-admin-page">
  <header class="pagehead">
    <div>
      <div class="eyebrow">Verwaltung</div>
      <h1 id="clinic-cat-title">${LABELS.title}</h1>
      <p class="subline">Kategorien je Feld festlegen und die Auswahl im Sendeformular prüfen.</p>
    </div>
  </header>
  <section class="panel" aria-labelledby="clinic-cat-list-title">
    <h2 id="clinic-cat-list-title">Kategoriefelder</h2>
    <form v-if="canManage && editor.state.loaded && !editor.state.sets.some(row => row.id === null)" @submit.prevent="create" class="field">
      <label for="clinic-cat-name">Name des neuen Kategoriesets</label>
      <div class="actions">
        <input id="clinic-cat-name" v-model="name" type="text" maxlength="128" required style="flex: 1 1 14rem; min-width: 0" :disabled="editor.state.status === 'processing'">
        <button type="submit" class="button primary" :disabled="editor.state.status === 'processing'">Kategorieset anlegen</button>
      </div>
    </form>
    <p role="status" class="muted">{{ editor.state.status === 'processing' ? (editor.state.loaded ? 'Wird verarbeitet …' : 'Kategorien werden geladen.') : feedback }}</p>
    <p v-if="editor.state.loaded && !editor.state.sets.length" class="empty">Keine Kategorien vorhanden.</p>
    <button v-if="(!editor.state.loaded && feedback) || editor.state.sets.some(row => row.stale)" type="button" class="button" :disabled="editor.state.status === 'processing'" @click="load">Kategorien neu laden</button>
    <fieldset v-for="row in editor.state.sets" :key="row.id" class="panel mb-3" :disabled="!canManage || row.stale || editor.state.status === 'processing'">
      <legend class="px-1 text-sm font-semibold break-words">{{ row.name }}</legend>
      <label class="inline-label"><input type="checkbox" :checked="row.required" @change="toggle(row, $event.target)"> ${LABELS.required}</label>
      <ul v-if="row.categories.length" role="list" aria-label="${LABELS.list}" class="my-3 space-y-1">
        <li v-for="cat in row.categories" :key="cat" class="actions justify-between">
          <span class="min-w-0 break-words">{{ cat }}</span>
          <button v-if="canManage" type="button" class="button quiet" :aria-label="cat + ' entfernen'" @click="report(editor.removeCategory(row.id, cat))" @keydown.delete.prevent="report(editor.onKey(row.id, $event, cat))" @keydown.backspace.prevent="report(editor.onKey(row.id, $event, cat))">${LABELS.remove}</button>
        </li>
      </ul>
      <template v-if="canManage">
        <form class="actions my-3" @submit.prevent="add(row, $event.target.elements.category)">
          <input name="category" type="text" maxlength="128" required aria-label="${LABELS.name}" placeholder="${LABELS.name}" style="flex: 1 1 12rem; min-width: 0">
          <button type="submit" class="button">${LABELS.add}</button>
        </form>
        <div class="actions">
          <button type="button" class="button primary" @click="save(row)">${LABELS.save}</button>
          <button v-if="row.id === null" type="button" class="button quiet" @click="report(editor.cancelCreate())">Verwerfen</button>
        </div>
      </template>
    </fieldset>
  </section>
  <section v-if="editor.state.sets.length" class="panel">
    <h2>${LABELS.preview}</h2>
    <ul role="list" class="mt-2 space-y-1 text-sm">
      <li v-for="item in editor.preview()" :key="item.setId" :aria-label="item.ariaLabel" class="actions"><span class="min-w-0 break-words">{{ item.label }}</span> <span v-if="item.required" class="tag neutral">${LABELS.required}</span></li>
    </ul>
  </section>
</section>`,
    };
}

export const RequiredCategories = {
    props: { items: Array, disabled: Boolean },
    emits: ['select'],
    template: `
<section class="panel mb-3 text-left" aria-labelledby="crv-categories-title">
  <h2 id="crv-categories-title" class="min-w-0 break-words">Pflichtkategorien</h2>
  <div v-for="item in items" :key="item.id" class="field">
    <label :for="'crv-category-' + item.id" class="break-words">{{ item.name }}</label>
    <select :id="'crv-category-' + item.id" class="!border-ui-text-muted" :value="item.value" required :disabled="disabled"
            @change="$emit('select', item.id, $event.target.value)">
      <option value="">Kategorie wählen</option>
      <option v-for="value in item.categories" :key="value" :value="value">{{ value }}</option>
    </select>
  </div>
</section>`,
};
