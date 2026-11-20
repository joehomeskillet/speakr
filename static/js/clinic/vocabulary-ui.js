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
    'clinic.vocabulary.title': 'Wörter und Vorschläge',
    'clinic.vocabulary.scope': 'Aktueller Bereich',
    'clinic.vocabulary.scopeUser': 'Persönlich',
    'clinic.vocabulary.ownList': 'Eigene Liste',
    'clinic.vocabulary.scopePool': 'Autorenpool',
    'clinic.vocabulary.pool': 'Autorenpool wählen',
    'clinic.vocabulary.search': 'Suchen',
    'clinic.vocabulary.query': 'Suchbegriff',
    'clinic.vocabulary.mode': 'Suchmodus',
    'clinic.vocabulary.contains': 'Enthält',
    'clinic.vocabulary.prefix': 'Beginnt mit',
    'clinic.vocabulary.suffix': 'Endet mit',
    'clinic.vocabulary.exact': 'Genau gleich',
    'clinic.vocabulary.active': 'Aktiv',
    'clinic.vocabulary.suppressed': 'Unterdrückt',
    'clinic.vocabulary.all': 'Alle',
    'clinic.vocabulary.unknown': 'Unbekannt',
    'clinic.vocabulary.loading': 'Wortschatz wird geladen …',
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
    let latestSearch = 0;
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
        const version = ++latestSearch;
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
        if (version !== latestSearch) return res;
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
<section class="vocabulary-ui v8-page stack" aria-labelledby="voc-title" :aria-busy="state.status === 'loading'">
  <h2 id="voc-title">{{ label('clinic.vocabulary.title') }}</h2>
  <div class="filterbar">
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-pool">Wortliste</label>
      <select id="voc-pool" class="select" :style="controlStyle" :value="state.scope.poolId || ''"
        @change="selectList($event.target.value)">
        <option value="">{{ label('clinic.vocabulary.scopeUser') }}</option>
        <option v-for="pool in pools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
      </select>
    </div>
    <span v-if="poolStatus === 'empty'" class="note" role="status">Keine zugeordneten Autorenpools.</span>
    <template v-if="poolStatus === 'error'">
      <span class="note" role="status">Autorenpools konnten nicht geladen werden.</span>
      <button type="button" class="small" @click="loadPools">Autorenpools erneut laden</button>
    </template>
  </div>
  <p role="status" aria-live="polite" class="note" :style="surfaceStyle">
    {{ label('clinic.vocabulary.scope') }}:
    <strong>{{ state.scope.kind === 'pool' ? selectedPoolName : label('clinic.vocabulary.ownList') }}</strong>
    <span v-if="state.status === 'loading'">{{ label('clinic.vocabulary.loading') }}</span>
    <span v-else-if="state.status === 'stale'">{{ label('clinic.vocabulary.stale') }}</span>
    <span v-else>{{ state.message }}</span>
  </p>
  <form class="filterbar" @submit.prevent="store.search()">
    <div class="field search" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-q">{{ label('clinic.vocabulary.query') }}</label>
      <input id="voc-q" type="search" data-role="query" autocomplete="off" v-model="state.query.q">
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-mode">{{ label('clinic.vocabulary.mode') }}</label>
      <select class="select" :style="controlStyle" id="voc-mode" v-model="state.query.mode"><option v-for="mode in modes" :key="mode" :value="mode">{{ enumLabel(mode) }}</option></select>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-filter">{{ label('clinic.vocabulary.filter') }}</label>
      <select class="select" :style="controlStyle" id="voc-filter" v-model="state.query.filter"><option v-for="filter in filters" :key="filter" :value="filter">{{ enumLabel(filter) }}</option></select>
    </div>
    <button type="submit" class="primary">{{ label('clinic.vocabulary.search') }}</button>
  </form>
  <div class="table-wrap" v-if="state.entries.length">
    <table :aria-label="label('clinic.vocabulary.title')">
      <thead><tr>
        <th scope="col">{{ label('clinic.vocabulary.written') }}</th>
        <th scope="col">{{ label('clinic.vocabulary.spoken') }}</th>
        <th scope="col">{{ label('clinic.vocabulary.filter') }}</th>
        <th scope="col">Aktion</th>
      </tr></thead>
      <tbody><tr v-for="e in state.entries" :key="e.id">
        <td class="wrap"><strong>{{ e.written }}</strong></td><td class="wrap">{{ e.spoken || '–' }}</td>
        <td><span class="tag" :class="{ error: e.status === 'suppressed' }">{{ enumLabel(e.status) }}</span></td>
        <td>
          <button type="button" class="small" v-if="e.status === 'active'" @click="store.suppress(e.id)">{{ label('clinic.vocabulary.suppress') }}</button>
          <button type="button" class="small" v-else @click="store.reactivate(e.id)">{{ label('clinic.vocabulary.reactivate') }}</button>
        </td>
      </tr></tbody>
    </table>
  </div>
  <p v-else-if="state.status === 'ready'" class="empty" role="status">
    <slot name="empty" :scope="state.scope" :query="state.query">{{ label('clinic.vocabulary.empty') }} für diese Auswahl. Suche oder Status anpassen.</slot>
  </p>
  <div class="actions">
    <button type="button" class="small" @click="store.reload()">{{ label('clinic.vocabulary.reload') }}</button>
    <button type="button" class="small" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.vocabulary.retry') }}</button>
    <button type="button" class="small" @click="addSelected()">{{ label('clinic.vocabulary.addSelection') }}</button>
  </div>
  <form @submit.prevent="doImport()">
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-import">{{ label('clinic.vocabulary.importLabel') }}</label>
      <textarea id="voc-import" rows="4" v-model="importDraft"></textarea>
    </div>
    <button type="submit" class="small">{{ label('clinic.vocabulary.import') }}</button>
  </form>
  <div class="actions">
    <button type="button" class="small" @click="store.exportList(false)">{{ label('clinic.vocabulary.export') }}</button>
    <button type="button" class="small" @click="store.exportList(true)">{{ label('clinic.vocabulary.exportEffective') }}</button>
  </div>
  <div v-if="state.exportText" class="field" style="border:0;padding:0;box-shadow:none;background:none">
    <label for="voc-export">{{ label('clinic.vocabulary.exported') }}</label>
    <textarea id="voc-export" rows="4" readonly :value="state.exportText"></textarea>
  </div>
  <p class="note">Eine aktive Wortliste ist keine garantierte Enginewirkung. Fehlende Fähigkeiten müssen gesondert nachgewiesen werden.</p>
</section>`;

export function vocabularyEnumLabel(value) {
    return label('clinic.vocabulary.' + ([...SEARCH_MODES, ...STATUS_FILTERS].includes(value) ? value : 'unknown'));
}

/** Vue component definition; pass the Vue global (window.Vue) and an api client. */
export function createVocabularyComponent(Vue, client, currentUserId = () => null) {
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
            const control = surfaceFor('normal');
            const controlStyle = { backgroundColor: control.background, color: control.color, borderColor: control.border };
            const pools = Vue.ref([]);
            const poolStatus = Vue.ref('loading');
            const selectedPoolName = Vue.computed(() => pools.value.find((pool) => pool.id === state.scope.poolId)?.name || label('clinic.vocabulary.scopePool'));
            async function loadPools() {
                poolStatus.value = 'loading';
                const result = await client.request('/pools');
                if (result.outcome !== 'ok') {
                    poolStatus.value = 'error';
                    return;
                }
                const userId = currentUserId();
                pools.value = (result.data?.author_pools || [])
                    .filter((pool) => pool.member_user_ids?.some((id) => String(id) === String(userId)))
                    .map(({ id, name }) => ({ id, name }));
                poolStatus.value = pools.value.length ? 'ready' : 'empty';
            }
            function selectList(id) {
                return id ? store.setScope('pool', id) : store.setScope('user');
            }
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
            Vue.onMounted(() => Promise.all([store.search(), loadPools()]));
            return {
                state, store, label, enumLabel: vocabularyEnumLabel, surfaceStyle, controlStyle, pools, poolStatus, selectedPoolName, selectList, loadPools, importDraft, addSelected, doImport,
                modes: SEARCH_MODES, filters: STATUS_FILTERS, onKey: (e) => handleKey(store, e),
            };
        },
    };
}
