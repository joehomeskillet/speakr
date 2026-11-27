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
const IMPORT_LIMIT = 256 * 1024;
const ENCODINGS = { 'utf-8': 'UTF-8', cp1252: 'Windows-1252', 'latin-1': 'ISO-8859-1' };
const DUPLICATE_RULES = { replace: 'Aktualisieren', skip: 'Überspringen', error: 'Import abbrechen' };

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
        const scope = state.scope;
        const params = scopeParams(new URLSearchParams({ q: '', mode: 'exact', status: 'active' }));
        const res = await client.request(PATH + '/search?' + params.toString());
        if (scope !== state.scope) return false;
        if (res.outcome === 'ok') return true;
        state.entries = [];
        state.failedWrite = null;
        fail(res);
        return false;
    }

    async function send(write) {
        const scope = state.scope;
        if (!(await permitted()) || scope !== state.scope) return null;
        const res = await client.request(write.path, { method: 'POST', body: write.body, idempotencyKey: write.key });
        if (scope !== state.scope) return null;
        if (res.outcome === 'ok') {
            if (write.keepResult) state.importResult = res.data;
            if (write.body.dry_run) emit();
            else {
                state.failedWrite = null;
                await search();
            }
            return res;
        }
        if (!write.body.dry_run) state.failedWrite = res.outcome === 'network' || res.outcome === 'UNKNOWN' ? write : null;
        if (res.code === 'duplicate_entry') {
            emit();
            return { ...res, message: 'Doppelter Eintrag. Keine Änderungen übernommen.' };
        }
        if (write.keepResult) {
            emit();
            return res;
        }
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

    function importPayload(payload, duplicateRule, dryRun) {
        const body = { ...scopeBody(), ...payload };
        if (duplicateRule) body.duplicate_rule = duplicateRule;
        if (dryRun) body.dry_run = true;
        if (new TextEncoder().encode(JSON.stringify(body)).length > IMPORT_LIMIT) {
            return Promise.resolve({ outcome: 'validation', message: 'Die Wortliste ist zu gross.' });
        }
        return send({ key: newId(), path: PATH + '/import', body, keepResult: true });
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
        importText(content, duplicateRule, dryRun) {
            if (typeof content !== 'string' || !content.trim()) return Promise.resolve(null);
            return importPayload({ content }, duplicateRule, dryRun);
        },
        async importFile(file, encoding, duplicateRule, dryRun) {
            if (!/\.txt$/i.test(file.name) || file.size === 0 || file.size > IMPORT_LIMIT * 3 / 4) {
                return { outcome: 'validation', message: 'Bitte eine nicht leere .txt-Datei bis 192 KiB wählen.' };
            }
            const scope = state.scope;
            let bytes;
            try {
                bytes = new Uint8Array(await file.arrayBuffer());
            } catch {
                return scope === state.scope ? { outcome: 'validation', message: 'Die Datei konnte nicht gelesen werden.' } : null;
            }
            if (scope !== state.scope) return null;
            const content_base64 = btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''));
            return importPayload({ content_base64, encoding }, duplicateRule, dryRun);
        },
        async exportList(effective) {
            const scope = state.scope;
            const params = effective ? new URLSearchParams({ scope: 'effective' }) : scopeParams(new URLSearchParams());
            const res = await client.request(PATH + '/export?' + params.toString());
            if (scope !== state.scope) return null;
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
  <form class="filterbar voc-filters" @submit.prevent="store.search()">
    <div class="field voc-list" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-pool">Wortliste</label>
      <select id="voc-pool" class="select" style="min-height: 48px !important" :style="listStyle" :value="state.scope.poolId || ''" :disabled="importBusy || poolStatus === 'loading'"
        @change="selectList($event.target.value)">
        <option value="">{{ label('clinic.vocabulary.scopeUser') }}</option>
        <option v-for="pool in pools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
      </select>
    </div>
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
    <div v-if="poolStatus === 'empty' || poolStatus === 'error'" class="voc-pool-feedback">
      <span v-if="poolStatus === 'empty'" role="status">Keine zugeordneten Autorenpools.</span>
      <template v-else>
        <span role="status">Autorenpools konnten nicht geladen werden.</span>
        <button type="button" class="small" @click="loadPools">Autorenpools erneut laden</button>
      </template>
    </div>
  </form>
  <p role="status" aria-live="polite" aria-atomic="true" class="note voc-feedback"
    :class="{ 'voc-quiet': !state.message && !['error', 'denied', 'stale'].includes(state.status) }" :style="surfaceStyle">
    <span class="voc-scope">{{ label('clinic.vocabulary.scope') }}:
      <strong>{{ state.scope.kind === 'pool' ? selectedPoolName : label('clinic.vocabulary.scopeUser') }}</strong> ·
    </span>
    <span v-if="state.status === 'loading'">{{ label('clinic.vocabulary.loading') }}</span>
    <span v-else-if="state.status === 'stale'">{{ label('clinic.vocabulary.stale') }}</span>
    <span v-else-if="state.message">{{ state.message }}</span>
    <span v-else-if="state.status === 'error' || state.status === 'denied'">Wortschatz konnte nicht geladen werden.</span>
    <span v-else-if="state.status === 'ready'">{{ state.entries.length }} Einträge geladen.</span>
  </p>
  <p v-if="poolStatus !== 'loading' && state.status === 'ready'" class="voc-result-count">{{ state.entries.length }} Einträge</p>
  <div class="table-wrap" v-if="poolStatus !== 'loading' && state.entries.length">
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
  <p v-else-if="poolStatus !== 'loading' && state.status === 'ready'" class="empty" role="status">
    <slot name="empty" :scope="state.scope" :query="state.query">{{ label('clinic.vocabulary.empty') }} für diese Auswahl. Suche oder Status anpassen.</slot>
  </p>
  <section class="voc-editor" aria-labelledby="voc-editor-title">
    <header class="voc-editor-head">
      <h3 id="voc-editor-title">Wortliste bearbeiten</h3>
      <div class="actions">
        <button type="button" class="small" @click="store.reload()">{{ label('clinic.vocabulary.reload') }}</button>
        <button type="button" class="small" v-if="state.failedWrite" :disabled="importBusy" @click="runImport(() => store.retry())">{{ label('clinic.vocabulary.retry') }}</button>
        <button type="button" class="small" @click="addSelected()">{{ label('clinic.vocabulary.addSelection') }}</button>
      </div>
    </header>
    <div class="voc-editor-body">
      <form @submit.prevent="doImport(false)">
        <h4>Import</h4>
        <input ref="fileInput" type="file" accept=".txt" hidden @change="pickFile">
        <div class="actions">
          <button type="button" class="small" :disabled="importBusy" @click="fileInput.click()">.txt-Datei auswählen</button>
          <button v-if="importFile" type="button" class="small" :disabled="importBusy" @click="importFile = null">Text eingeben</button>
        </div>
        <template v-if="importFile">
          <p class="min-w-0" style="overflow-wrap:anywhere">{{ importFile.name }}</p>
          <label for="voc-encoding">Kodierung</label>
          <select id="voc-encoding" class="select" :style="controlStyle" :disabled="importBusy" aria-describedby="voc-import-format" v-model="encoding">
            <option v-for="(text, value) in encodings" :key="value" :value="value">{{ text }}</option>
          </select>
        </template>
        <template v-else>
          <label for="voc-import">{{ label('clinic.vocabulary.importLabel') }}</label>
          <textarea id="voc-import" class="w-full" rows="4" aria-describedby="voc-import-format" :disabled="importBusy" v-model="importDraft"></textarea>
        </template>
        <small id="voc-import-format">Ein Eintrag pro Zeile. Eine optionale Aussprache mit Tabulator abtrennen.</small>
        <label for="voc-duplicate">Bei doppelten Einträgen</label>
        <select id="voc-duplicate" class="select" :style="controlStyle" :disabled="importBusy" v-model="duplicateRule">
          <option v-for="(text, value) in duplicateRules" :key="value" :value="value">{{ text }}</option>
        </select>
        <div class="actions">
          <button type="button" class="small" :disabled="!canImport" @click="doImport(true)">Vorschau</button>
          <button type="submit" class="primary" :disabled="!canImport">{{ label('clinic.vocabulary.import') }}</button>
        </div>
        <div role="status" aria-live="polite" aria-atomic="true">
          <div v-if="importError" class="note" :style="importErrorStyle">{{ importError }}</div>
          <div v-else-if="importReport" class="note">
            <strong>{{ importReport.dry_run ? 'Vorschau (nicht gespeichert)' : 'Importbericht' }}</strong>
            <div>Hinzugefügt: {{ importReport.added }} · Aktualisiert: {{ importReport.updated }} · Übersprungen: {{ importReport.skipped }}</div>
            <div v-if="importReport.errors?.length" style="overflow-wrap:anywhere">Fehlerhafte Zeilen: {{ importReport.errors.map(error => error.line).join(', ') }}</div>
          </div>
        </div>
      </form>
      <div class="voc-editor-export">
        <h4>Export</h4>
        <div class="actions">
          <button type="button" class="small" :disabled="exportBusy" @click="downloadList(false)">{{ label('clinic.vocabulary.export') }}</button>
          <button type="button" class="small" :disabled="exportBusy" @click="downloadList(true)">{{ label('clinic.vocabulary.exportEffective') }}</button>
        </div>
        <template v-if="state.exportText">
          <label for="voc-export">{{ label('clinic.vocabulary.exported') }}</label>
          <textarea id="voc-export" rows="4" readonly :value="state.exportText"></textarea>
        </template>
      </div>
    </div>
  </section>
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
            const listStyle = { ...controlStyle, borderColor: 'var(--v8-muted)' };
            const pools = Vue.ref([]);
            const poolStatus = Vue.ref('loading');
            const selectedPoolName = Vue.computed(() => pools.value.find((pool) => pool.id === state.scope.poolId)?.name || label('clinic.vocabulary.scopePool'));
            let generation = 0;
            async function loadPools() {
                const mine = ++generation;
                poolStatus.value = 'loading';
                const userId = currentUserId();
                const [result, active] = await Promise.all([
                    client.request('/pools'), client.request('/preferences/active-pool'),
                ]);
                if (mine !== generation || userId !== currentUserId()) return;
                pools.value = result.outcome === 'ok' ? (result.data?.author_pools || [])
                    .filter((pool) => pool.member_user_ids?.some((id) => String(id) === String(userId)))
                    .map(({ id, name }) => ({ id, name })) : [];
                poolStatus.value = result.outcome !== 'ok' || active.outcome !== 'ok'
                    ? 'error' : pools.value.length ? 'ready' : 'empty';
                const activeId = active.outcome === 'ok' ? active.data?.pool_id : null;
                return selectList(pools.value.some(({ id }) => id === activeId) ? activeId : null);
            }
            function selectList(id) {
                importError.value = '';
                return id ? store.setScope('pool', id) : store.setScope('user');
            }
            const importDraft = Vue.ref('');
            const importFile = Vue.shallowRef(null);
            const fileInput = Vue.ref(null);
            const encoding = Vue.ref('utf-8');
            const duplicateRule = Vue.ref('replace');
            const importBusy = Vue.ref(false);
            const importError = Vue.ref('');
            const error = surfaceFor('error');
            const importErrorStyle = { background: error.background, color: error.color, borderColor: error.border };
            const exportBusy = Vue.ref(false);
            const previewDraft = Vue.shallowRef(null);
            const draft = Vue.computed(() => ({ content: importFile.value || importDraft.value, encoding: encoding.value, rule: duplicateRule.value }));
            const canImport = Vue.computed(() => !importBusy.value && Boolean(importFile.value || importDraft.value.trim()));
            const importReport = Vue.computed(() => state.importResult?.dry_run && previewDraft.value !== draft.value ? null : state.importResult);
            function pickFile(event) {
                if (event.target.files[0]) {
                    importFile.value = event.target.files[0];
                    encoding.value = 'utf-8';
                }
                event.target.value = '';
            }
            function addSelected() {
                const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection().toString() : '';
                return store.addSelection(sel);
            }
            async function runImport(action) {
                if (importBusy.value) return;
                importBusy.value = true;
                importError.value = '';
                try {
                    const res = await action();
                    if (res && res.outcome !== 'ok') importError.value = res.message || 'Die Wortliste konnte nicht importiert werden.';
                    if (res?.outcome === 'ok' && res.data?.dry_run === false) {
                        importDraft.value = '';
                        importFile.value = null;
                        previewDraft.value = null;
                    }
                } finally {
                    importBusy.value = false;
                }
            }
            function doImport(dryRun) {
                if (!canImport.value) return;
                previewDraft.value = draft.value;
                return runImport(() => importFile.value
                    ? store.importFile(importFile.value, encoding.value, duplicateRule.value, dryRun)
                    : store.importText(importDraft.value, duplicateRule.value, dryRun));
            }
            async function downloadList(effective) {
                if (exportBusy.value) return;
                exportBusy.value = true;
                try {
                    const res = await store.exportList(effective);
                    if (res?.outcome !== 'ok') return;
                    const link = document.createElement('a');
                    const url = URL.createObjectURL(new Blob([res.data.content], { type: 'text/plain;charset=utf-8' }));
                    link.href = url;
                    link.download = effective ? 'wortliste-wirksam.txt' : 'wortliste.txt';
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                } finally {
                    exportBusy.value = false;
                }
            }
            Vue.onMounted(loadPools);
            Vue.onActivated(() => { if (poolStatus.value !== 'loading') return loadPools(); });
            Vue.onDeactivated(() => { generation += 1; poolStatus.value = 'idle'; });
            Vue.onBeforeUnmount(() => { generation += 1; });
            return {
                state, store, label, enumLabel: vocabularyEnumLabel, surfaceStyle, controlStyle, listStyle, pools, poolStatus, selectedPoolName, selectList, loadPools, addSelected,
                importDraft, importFile, fileInput, encoding, duplicateRule, importBusy, importError, importErrorStyle, exportBusy, canImport, importReport,
                pickFile, runImport, doImport, downloadList,
                encodings: ENCODINGS, duplicateRules: DUPLICATE_RULES, modes: SEARCH_MODES, filters: STATUS_FILTERS, onKey: (e) => handleKey(store, e),
            };
        },
    };
}
