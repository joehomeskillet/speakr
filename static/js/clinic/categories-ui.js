/**
 * Category set editor and send-form preview (PAR-CFG-003).
 *
 * Sets come from GET /api/clinic/v1/categories. Edits stay in memory as drafts
 * until saved through the parity command channel with the set version as
 * expected revision. The same match rule as the server (case-insensitive
 * substring, src/clinic/presets.py validate_category_requirement) decides
 * whether a pending selection stays valid, so a removal that would invalidate
 * one is rejected before any request. Clinical selection values are never
 * written to browser storage. Status colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

const CATEGORIES = '/categories';

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
    no_category: 'Eine Pflichtgruppe braucht mindestens eine Kategorie.',
    pending_selection_invalid: 'Eine ausstehende Auswahl wäre danach ungültig.',
    unknown_set: 'Die Kategoriegruppe ist unbekannt.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    failure: 'Das Speichern ist fehlgeschlagen.',
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

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'cat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
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
        state.sets = sets.map((item) => ({
            id: item.id,
            name: item.name,
            version: item.version,
            categories: item.categories.slice(),
            required: !!item.required,
            targetField: item.target_field,
            scope: item.scope,
            stale: false,
        }));
        state.loaded = true;
        setStatus('success');
        return { ok: true };
    }

    function select(setId, value) {
        if (typeof value === 'string' && value !== '') state.selections[setId] = value;
        else delete state.selections[setId];
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
        setStatus('processing');
        const key = newKey();
        const result = await client.command(
            {
                command: 'save_category_set',
                request_id: key,
                expected_revision: row.version,
                payload: { set_id: row.id, categories: row.categories.slice(), required: row.required },
            },
            { idempotencyKey: key, ifMatch: '"' + row.version + '"' },
        );
        if (result.outcome === 'ok') {
            const data = result.data || {};
            if (Number.isInteger(data.revision)) row.version = data.revision;
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            row.stale = true;
            setStatus('warning');
            return refuse('stale_version');
        }
        setStatus(result.outcome === 'permission' ? 'warning' : 'error');
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

    return { state, load, select, addCategory, removeCategory, setRequired, preview, save, onKey, reset };
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
            editor.load();
            return { editor };
        },
        template: `
<section role="region" aria-labelledby="clinic-cat-title" :aria-busy="editor.state.status === 'processing'" class="space-y-4">
  <header>
    <h2 id="clinic-cat-title" class="text-lg font-semibold text-[var(--text-primary)]">${LABELS.title}</h2>
    <p class="mt-1 text-sm text-[var(--text-muted)]">Kategorien je Feld festlegen und die Auswahl im Sendeformular prüfen.</p>
  </header>
  <p v-if="editor.state.status === 'processing'" role="status" class="text-sm text-[var(--text-muted)]">{{ editor.state.loaded ? 'Wird verarbeitet …' : 'Kategorien werden geladen.' }}</p>
  <p v-else-if="editor.state.loaded && !editor.state.sets.length" role="status" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">Keine Kategorien vorhanden.</p>
  <fieldset v-for="row in editor.state.sets" :key="row.id" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
    <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ row.name }}</legend>
    <label class="inline-flex items-center gap-2 text-sm text-[var(--text-secondary)]"><input type="checkbox" class="h-4 w-4" :checked="row.required" @change="editor.setRequired(row.id, $event.target.checked)"> ${LABELS.required}</label>
    <ul role="list" aria-label="${LABELS.list}" class="divide-y divide-[var(--border-primary)] rounded-lg border border-[var(--border-primary)] empty:hidden">
      <li v-for="cat in row.categories" :key="cat" tabindex="0" class="flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)]" @keydown="editor.onKey(row.id, $event, cat)">
        <span>{{ cat }}</span> <button type="button" class="btn btn--sm btn--ghost" @click="editor.removeCategory(row.id, cat)">${LABELS.remove}</button>
      </li>
    </ul>
    <div class="flex flex-wrap items-center gap-2">
      <input type="text" class="field min-w-0 flex-1" aria-label="${LABELS.name}" placeholder="${LABELS.name}" @keydown.enter="editor.addCategory(row.id, $event.target.value); $event.target.value = ''">
      <button type="button" class="btn btn--primary" @click="editor.save(row.id)">${LABELS.save}</button>
    </div>
  </fieldset>
  <section v-if="editor.state.sets.length" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
    <h3 class="text-base font-semibold text-[var(--text-primary)]">${LABELS.preview}</h3>
    <ul role="list" class="mt-2 space-y-1 text-sm text-[var(--text-secondary)]">
      <li v-for="item in editor.preview()" :key="item.setId" :aria-label="item.ariaLabel">{{ item.label }} <span class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-2.5 py-0.5 text-xs text-[var(--text-primary)]">{{ item.field }}</span></li>
    </ul>
  </section>
</section>`,
    };
}
