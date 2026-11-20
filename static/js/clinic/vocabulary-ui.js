/**
 * Vocabulary management (PAR-VOC-001/002/003/006).
 *
 * Own list or one permitted author pool, with search, word-list import and
 * export, suppression, reactivation and an explicit "add selected text" action.
 * The current scope is always shown. Pool membership is rechecked live before
 * each mutation; a denied recheck sends nothing. Writes carry the entry
 * version; a stale version keeps the newer server state. Word lists and entries
 * stay in memory only (no browser storage). Colours come only from the token
 * contract; calls go through the shared api client.
 */

import { surfaceFor } from './tokens.js';

const PATH = '/vocabulary';
export const MAX_SELECTION = 255;
export const SEARCH_MODES = Object.freeze(['contains', 'prefix', 'suffix', 'exact']);
export const STATUS_FILTERS = Object.freeze(['active', 'suppressed', 'all']);

const LABELS = {
    'clinic.vocabulary.title': 'Vokabular verwalten',
    'clinic.vocabulary.scope': 'Aktueller Bereich',
    'clinic.vocabulary.scopeUser': 'Eigene Liste',
    'clinic.vocabulary.scopePool': 'Autorenpool',
    'clinic.vocabulary.pool': 'Autorenpool wählen',
    'clinic.vocabulary.search': 'Suchen',
    'clinic.vocabulary.query': 'Suchbegriff',
    'clinic.vocabulary.mode': 'Suchmodus',
    'clinic.vocabulary.filter': 'Status',
    'clinic.vocabulary.written': 'Schreibweise',
    'clinic.vocabulary.spoken': 'Gesprochen',
    'clinic.vocabulary.suppress': 'Unterdrücken',
    'clinic.vocabulary.reactivate': 'Reaktivieren',
    'clinic.vocabulary.addSelection': 'Markierten Text hinzufügen',
    'clinic.vocabulary.noSelection': 'Kein gültiger Text markiert.',
    'clinic.vocabulary.import': 'Wortliste importieren',
    'clinic.vocabulary.importLabel': 'Wortliste (Text)',
    'clinic.vocabulary.export': 'Wortliste exportieren',
    'clinic.vocabulary.exportEffective': 'Wirksame Liste exportieren',
    'clinic.vocabulary.exported': 'Exportierte Wortliste',
    'clinic.vocabulary.empty': 'Keine Einträge',
    'clinic.vocabulary.reload': 'Aktuellen Stand laden',
    'clinic.vocabulary.retry': 'Erneut versuchen',
    'clinic.vocabulary.stale': 'Der Eintrag ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
};

export function label(key) {
    const i18n = typeof window !== 'undefined' ? window.i18n : undefined;
    if (i18n && typeof i18n.t === 'function') {
        const text = i18n.t(key);
        if (text && text !== key) return text;
    }
    return LABELS[key] || key;
}

function newId() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16));
}

/** Same bound as the server: NFC, collapsed spaces, 1..255 chars, no control characters. */
export function canonicalSelection(text) {
    if (typeof text !== 'string') return null;
    const value = text.normalize('NFC').split(/\s+/).filter(Boolean).join(' ');
    // eslint-disable-next-line no-control-regex
    if (!value || value.length > MAX_SELECTION || /[\x00-\x1f\x7f]/.test(value)) return null;
    return value;
}

function normalize(list) {
    return (Array.isArray(list) ? list : []).map((e) => ({
        id: String(e.id),
        written: String(e.written || ''),
        spoken: e.spoken == null ? null : String(e.spoken),
        status: String(e.status || ''),
        version: String(e.version || ''),
    }));
}

/**
 * @param {{request: Function}} client  createClinicApiClient()
 */
export function createVocabularyStore(client, onChange) {
    const state = {
        status: 'idle', // idle | loading | ready | denied | stale | error
        scope: { kind: 'user', poolId: null },
        entries: [],
        query: { q: '', mode: 'contains', filter: 'active' },
        exportText: '',
        importResult: null,
        failedWrite: null, // kept for retry with the same idempotency key
        message: '',
    };
    const emit = () => { if (typeof onChange === 'function') onChange(state); };

    function fail(res) {
        state.message = res.message;
        if (res.outcome === 'conflict') state.status = 'stale';
        else if (res.outcome === 'permission') state.status = 'denied';
        else state.status = 'error';
        emit();
        return res;
    }

    const scopeParams = (params) => {
        params.set('scope', state.scope.kind);
        if (state.scope.kind === 'pool') params.set('pool_id', state.scope.poolId);
        return params;
    };
    const scopeBody = () => (state.scope.kind === 'pool'
        ? { scope: 'pool', pool_id: state.scope.poolId }
        : { scope: 'user' });

    async function search(next) {
        if (next) Object.assign(state.query, next);
        const q = state.query;
        if (!SEARCH_MODES.includes(q.mode) || !STATUS_FILTERS.includes(q.filter) || q.q.length > MAX_SELECTION) {
            state.status = 'error';
            emit();
            return null;
        }
        state.status = 'loading';
        state.message = '';
        emit();
        const params = scopeParams(new URLSearchParams({ q: q.q, mode: q.mode, status: q.filter }));
        const res = await client.request(PATH + '/search?' + params.toString());
        if (res.outcome !== 'ok') return fail(res);
        state.entries = normalize((res.data || {}).entries);
        state.failedWrite = null;
        state.status = 'ready';
        emit();
        return res;
    }

    function setScope(kind, poolId) {
        if (kind === 'user') state.scope = { kind: 'user', poolId: null };
        else if (kind === 'pool' && typeof poolId === 'string' && poolId) state.scope = { kind: 'pool', poolId };
        else return Promise.resolve(null);
        state.entries = [];
        state.exportText = '';
        state.importResult = null;
        state.failedWrite = null;
        return search();
    }

    // Live membership recheck before every mutation. Own list needs none.
    async function permitted() {
        if (state.scope.kind !== 'pool') return true;
        const params = scopeParams(new URLSearchParams({ q: '', mode: 'exact', status: 'active' }));
        const res = await client.request(PATH + '/search?' + params.toString());
        if (res.outcome === 'ok') return true;
        state.entries = [];
        state.failedWrite = null;
        fail(res);
        return false;
    }

    async function send(write) {
        if (!(await permitted())) return null;
        const res = await client.request(write.path, { method: 'POST', body: write.body, idempotencyKey: write.key });
        if (res.outcome === 'ok') {
            state.failedWrite = null;
            if (write.keepResult) state.importResult = res.data;
            await search();
            return res;
        }
        state.failedWrite = res.outcome === 'network' || res.outcome === 'UNKNOWN' ? write : null;
        fail(res);
        if (res.outcome === 'conflict') await search();
        if (res.outcome === 'conflict') { state.status = 'stale'; emit(); }
        return res;
    }

    function toggle(entryId, action) {
        const entry = state.entries.find((e) => e.id === entryId);
        if (!entry || state.status === 'loading') return Promise.resolve(null);
        return send({
            key: newId(),
            path: PATH + '/entries/' + encodeURIComponent(entry.id) + '/' + action,
            body: { expected_version: entry.version },
        });
    }

    return {
        state,
        search,
        reload: () => search(),
        setScope,
        suppress: (id) => toggle(id, 'suppress'),
        reactivate: (id) => toggle(id, 'reactivate'),
        /** Explicit action only: the caller passes the user's current text selection. */
        addSelection(text) {
            const written = canonicalSelection(text);
            if (!written) {
                state.message = label('clinic.vocabulary.noSelection');
                emit();
                return Promise.resolve(null);
            }
            return send({ key: newId(), path: PATH + '/additions', body: Object.assign(scopeBody(), { written }) });
        },
        importText(content, duplicateRule) {
            if (typeof content !== 'string' || !content.trim()) return Promise.resolve(null);
            const body = Object.assign(scopeBody(), { content });
            if (duplicateRule) body.duplicate_rule = duplicateRule;
            return send({ key: newId(), path: PATH + '/import', body, keepResult: true });
        },
        async exportList(effective) {
            const params = effective ? new URLSearchParams({ scope: 'effective' }) : scopeParams(new URLSearchParams());
            const res = await client.request(PATH + '/export?' + params.toString());
            if (res.outcome !== 'ok') return fail(res);
            state.exportText = String((res.data || {}).content || '');
            emit();
            return res;
        },
        retry() {
            return state.failedWrite ? send(state.failedWrite) : Promise.resolve(null);
        },
    };
}

/** Keyboard: Enter in the search field searches; returns true when handled. */
export function handleKey(store, event) {
    if (event && event.key === 'Enter' && event.target && event.target.dataset && event.target.dataset.role === 'query') {
        store.search();
        return true;
    }
    return false;
}

export function statusSurface(state) {
    const map = { ready: 'success', loading: 'processing', stale: 'warning', denied: 'error', error: 'error' };
    return surfaceFor(map[state.status] || 'normal');
}

export const VOCABULARY_TEMPLATE = `
<section class="vocabulary-ui" aria-labelledby="voc-title" @keydown="onKey">
  <h2 id="voc-title">{{ label('clinic.vocabulary.title') }}</h2>
  <p role="status" aria-live="polite" :style="surfaceStyle">
    {{ label('clinic.vocabulary.scope') }}:
    <strong>{{ state.scope.kind === 'pool' ? label('clinic.vocabulary.scopePool') + ' ' + state.scope.poolId : label('clinic.vocabulary.scopeUser') }}</strong>
    <span v-if="state.status === 'stale'">{{ label('clinic.vocabulary.stale') }}</span>
    <span v-else>{{ state.message }}</span>
  </p>
  <button type="button" @click="store.setScope('user')">{{ label('clinic.vocabulary.scopeUser') }}</button>
  <form @submit.prevent="store.setScope('pool', poolDraft)">
    <label for="voc-pool">{{ label('clinic.vocabulary.pool') }}</label>
    <input id="voc-pool" type="text" autocomplete="off" v-model="poolDraft">
    <button type="submit">{{ label('clinic.vocabulary.scopePool') }}</button>
  </form>
  <form @submit.prevent="store.search()">
    <label for="voc-q">{{ label('clinic.vocabulary.query') }}</label>
    <input id="voc-q" type="search" data-role="query" autocomplete="off" v-model="state.query.q">
    <label for="voc-mode">{{ label('clinic.vocabulary.mode') }}</label>
    <select id="voc-mode" v-model="state.query.mode"><option v-for="m in modes" :key="m" :value="m">{{ m }}</option></select>
    <label for="voc-filter">{{ label('clinic.vocabulary.filter') }}</label>
    <select id="voc-filter" v-model="state.query.filter"><option v-for="f in filters" :key="f" :value="f">{{ f }}</option></select>
    <button type="submit">{{ label('clinic.vocabulary.search') }}</button>
  </form>
  <button type="button" @click="store.reload()">{{ label('clinic.vocabulary.reload') }}</button>
  <button type="button" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.vocabulary.retry') }}</button>
  <button type="button" @click="addSelected()">{{ label('clinic.vocabulary.addSelection') }}</button>
  <table :aria-label="label('clinic.vocabulary.title')">
    <thead><tr>
      <th scope="col">{{ label('clinic.vocabulary.written') }}</th>
      <th scope="col">{{ label('clinic.vocabulary.spoken') }}</th>
      <th scope="col">{{ label('clinic.vocabulary.filter') }}</th>
      <th scope="col"><span class="sr-only">Aktion</span></th>
    </tr></thead>
    <tbody>
      <tr v-if="!state.entries.length && state.status === 'ready'"><td colspan="4" role="status">
        <slot name="empty" :scope="state.scope" :query="state.query">{{ label('clinic.vocabulary.empty') }}</slot>
      </td></tr>
      <tr v-for="e in state.entries" :key="e.id">
        <td>{{ e.written }}</td><td>{{ e.spoken }}</td><td>{{ e.status }}</td>
        <td>
          <button type="button" v-if="e.status === 'active'" @click="store.suppress(e.id)">{{ label('clinic.vocabulary.suppress') }}</button>
          <button type="button" v-else @click="store.reactivate(e.id)">{{ label('clinic.vocabulary.reactivate') }}</button>
        </td>
      </tr>
    </tbody>
  </table>
  <form @submit.prevent="doImport()">
    <label for="voc-import">{{ label('clinic.vocabulary.importLabel') }}</label>
    <textarea id="voc-import" rows="4" v-model="importDraft"></textarea>
    <button type="submit">{{ label('clinic.vocabulary.import') }}</button>
  </form>
  <button type="button" @click="store.exportList(false)">{{ label('clinic.vocabulary.export') }}</button>
  <button type="button" @click="store.exportList(true)">{{ label('clinic.vocabulary.exportEffective') }}</button>
  <div v-if="state.exportText">
    <label for="voc-export">{{ label('clinic.vocabulary.exported') }}</label>
    <textarea id="voc-export" rows="4" readonly :value="state.exportText"></textarea>
  </div>
</section>`;

/** Vue component definition; pass the Vue global (window.Vue) and an api client. */
export function createVocabularyComponent(Vue, client) {
    return {
        template: VOCABULARY_TEMPLATE,
        setup() {
            const state = Vue.reactive({
                status: 'idle', scope: { kind: 'user', poolId: null }, entries: [],
                query: { q: '', mode: 'contains', filter: 'active' },
                exportText: '', importResult: null, failedWrite: null, message: '',
            });
            const store = createVocabularyStore(client, (next) => Object.assign(state, next));
            const surfaceStyle = Vue.computed(() => {
                const s = statusSurface(state);
                return { background: s.background, color: s.color, borderColor: s.border };
            });
            const poolDraft = Vue.ref('');
            const importDraft = Vue.ref('');
            function addSelected() {
                const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection().toString() : '';
                return store.addSelection(sel);
            }
            async function doImport() {
                const content = importDraft.value;
                importDraft.value = '';
                return store.importText(content);
            }
            Vue.onMounted(() => store.search());
            return {
                state, store, label, surfaceStyle, poolDraft, importDraft, addSelected, doImport,
                modes: SEARCH_MODES, filters: STATUS_FILTERS, onKey: (e) => handleKey(store, e),
            };
        },
    };
}
