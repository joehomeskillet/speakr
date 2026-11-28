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
const SEARCH_FIELDS = { written: 'Schreibweise', spoken: 'Sprechform', all: 'Beide Formen' };
const MAX_BULK = 100;
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
    'clinic.vocabulary.asrSuppressionUnavailable': 'ASR-Wortunterdrückung nicht verfügbar',
    'clinic.vocabulary.asrSuppressionDetail': '„Unterdrückt“ ist nur ein Status in der Wortliste. Die lokale Spracherkennung kann diese Wörter weiterhin erkennen.',
    'clinic.vocabulary.all': 'Alle',
    'clinic.vocabulary.unknown': 'Unbekannt',
    'clinic.vocabulary.loading': 'Wortschatz wird geladen …',
    'clinic.vocabulary.filter': 'Status',
    'clinic.vocabulary.written': 'Schreibweise',
    'clinic.vocabulary.spoken': 'Gesprochen',
    'clinic.vocabulary.suppress': 'Unterdrücken',
    'clinic.vocabulary.reactivate': 'Reaktivieren',
    'clinic.vocabulary.addSelection': 'Markierten Text hinzufügen',
    'clinic.vocabulary.propose': 'Für Pool vorschlagen',
    'clinic.vocabulary.proposeSuppression': 'Unterdrückung vorschlagen',
    'clinic.vocabulary.proposed': 'Vorschlag zur Prüfung eingereicht.',
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
export function createVocabularyStore(client, onChange, makeState = (value) => value) {
    const state = makeState({
        status: 'idle', // idle | loading | ready | denied | stale | error
        scope: { kind: 'user', poolId: null },
        entries: [],
        canWrite: true,
        proposalBusy: false,
        query: { q: '', mode: 'contains', filter: 'active', field: 'written' },
        exportText: '',
        importResult: null,
        failedWrite: null, // kept for retry with the same idempotency key
        message: '',
    });
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
        if (!SEARCH_MODES.includes(q.mode) || !STATUS_FILTERS.includes(q.filter)
            || !Object.hasOwn(SEARCH_FIELDS, q.field) || q.q.length > MAX_SELECTION) {
            state.status = 'error';
            emit();
            return null;
        }
        state.status = 'loading';
        state.message = '';
        emit();
        const hasQuery = Boolean(q.q.trim());
        const params = scopeParams(new URLSearchParams({ q: hasQuery ? q.q : '', mode: hasQuery ? q.mode : 'contains', status: q.filter }));
        if (hasQuery && q.field !== 'written') params.set('field', q.field);
        const res = await client.request(PATH + '/search?' + params.toString());
        if (version !== latestSearch) return res;
        if (res.outcome !== 'ok') return fail(res);
        state.entries = normalize((res.data || {}).entries);
        state.canWrite = state.scope.kind === 'user' ? true : res.data?.can_write ?? null;
        if (!state.failedWrite?.command) state.failedWrite = null;
        state.status = 'ready';
        emit();
        return res;
    }

    function setScope(kind, poolId) {
        if (kind === 'user') state.scope = { kind: 'user', poolId: null };
        else if (kind === 'pool' && typeof poolId === 'string' && poolId) state.scope = { kind: 'pool', poolId };
        else return Promise.resolve(null);
        state.entries = [];
        state.canWrite = kind === 'user' ? true : null;
        state.exportText = '';
        state.importResult = null;
        state.failedWrite = null;
        return search();
    }

    // Live membership recheck before every mutation. Own list needs none.
    async function permitted(write = false) {
        if (state.scope.kind !== 'pool') return true;
        const scope = state.scope;
        const params = scopeParams(new URLSearchParams({ q: '', mode: 'exact', status: 'active' }));
        const res = await client.request(PATH + '/search?' + params.toString());
        if (scope !== state.scope) return false;
        if (res.outcome === 'ok') {
            if (!write || res.data?.can_write !== false) return true;
            state.canWrite = false;
            state.failedWrite = null;
            state.message = 'Diese Wortliste kann nicht bearbeitet werden.';
            emit();
            return false;
        }
        state.entries = [];
        state.failedWrite = null;
        fail(res);
        return false;
    }

    async function send(write) {
        const scope = state.scope;
        if (state.canWrite === false || !(await permitted(true)) || scope !== state.scope) return null;
        const res = await client.request(write.path, { method: write.method || 'POST', body: write.body, idempotencyKey: write.key });
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
        if (!write.body.dry_run) state.failedWrite = !write.isEntry && (res.outcome === 'network' || res.outcome === 'UNKNOWN') ? write : null;
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
        if (scope === state.scope && res.outcome === 'conflict') { state.status = 'stale'; emit(); }
        return res;
    }

    async function sendProposal(write) {
        const scope = state.scope;
        if (scope.kind !== 'pool' || state.canWrite !== false || state.status !== 'ready' || state.proposalBusy) return null;
        state.proposalBusy = true;
        state.message = '';
        emit();
        try {
            if (!(await permitted()) || scope !== state.scope) return null;
            const res = await client.command(write.command, { idempotencyKey: write.key });
            if (scope !== state.scope) return null;
            state.failedWrite = res.outcome === 'network' || res.outcome === 'UNKNOWN' ? write : null;
            state.message = res.outcome === 'ok' ? label('clinic.vocabulary.proposed')
                : res.message || 'Der Vorschlag konnte nicht eingereicht werden.';
            return res;
        } finally {
            state.proposalBusy = false;
            emit();
        }
    }

    function propose(text, operation = 'add', spoken) {
        if (state.failedWrite?.command) return Promise.resolve(null);
        const written = canonicalSelection(text);
        if (!written) {
            state.message = 'Bitte eine gültige Schreibweise eingeben.';
            emit();
            return Promise.resolve(null);
        }
        const key = newId();
        const payload = { scope_type: 'author_pool', scope_id: state.scope.poolId, operation, written };
        if (spoken) payload.spoken = spoken;
        const command = { command: 'propose_vocabulary_change', request_id: key, expected_revision: 0, payload };
        return sendProposal({ key, command });
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
        async deleteEntries(entries) {
            if (!entries.length || entries.length > MAX_BULK) return null;
            const scope = state.scope;
            const res = await send({
                key: newId(), isEntry: true, path: PATH + '/entries/bulk',
                body: { action: 'delete', confirm: true, entries: entries.map(({ id, version }) => ({ id, expected_version: version })) },
            });
            if (scope === state.scope && (res?.status === 404 || ['network', 'UNKNOWN', 'permission'].includes(res?.outcome))) await search();
            return res;
        },
        saveEntry(draft) {
            const written = canonicalSelection(draft.written);
            const spoken = draft.spoken.trim() ? canonicalSelection(draft.spoken) : null;
            if (!written || (draft.spoken.trim() && !spoken)) {
                return Promise.resolve({ outcome: 'validation', message: 'Bitte Schreibweise und Sprechform prüfen.' });
            }
            const entry = draft.entry;
            return send({
                key: newId(), isEntry: true,
                method: entry ? 'PUT' : 'POST',
                path: entry ? PATH + '/entries/' + encodeURIComponent(entry.id) : PATH + '/additions',
                body: entry ? { expected_version: entry.version, written, spoken }
                    : { ...scopeBody(), create_only: true, written, spoken },
            });
        },
        propose,
        proposeSuppression(id) {
            const entry = state.entries.find((row) => row.id === id && row.status === 'active');
            return entry ? propose(entry.written, 'negative', entry.spoken) : Promise.resolve(null);
        },
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
            if (state.failedWrite?.command) return sendProposal(state.failedWrite);
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

// Both callers use the same draft, validation, modal and save affordances.
export function useVocabularyEntryDialog(Vue, submit, entryDialogPrefix, restoreFocus) {
    const entryDialog = Vue.ref(null);
    const entryWritten = Vue.ref(null);
    const entryDraft = Vue.ref(null);
    const entryBusy = Vue.ref(false);
    const entryBlocked = Vue.ref(false);
    const entryError = Vue.ref('');
    const entryNotice = Vue.ref('');
    const error = surfaceFor('error');
    const entryErrorStyle = { background: error.background, color: error.color, borderColor: error.border };
    async function openEntry(entry = null, written = entry?.written || '') {
        if (entryDraft.value) return;
        entryNotice.value = '';
        entryError.value = '';
        entryBlocked.value = false;
        entryDraft.value = { entry, written, spoken: entry?.spoken || '' };
        await Vue.nextTick();
        if (!entryDraft.value) return;
        entryDialog.value?.showModal();
        entryWritten.value?.focus();
    }
    function openSelectedEntry(text) {
        const written = canonicalSelection(text);
        if (!written) {
            entryNotice.value = label('clinic.vocabulary.noSelection');
            return;
        }
        return openEntry(null, written);
    }
    function closeEntry(saved = false) {
        if (entryBusy.value || !entryDraft.value) return;
        entryDialog.value?.close();
        entryDraft.value = null;
        entryNotice.value = saved ? 'Wort gespeichert.' : 'Änderungen verworfen.';
        restoreFocus?.();
    }
    async function saveEntry() {
        if (!entryDraft.value || entryBusy.value || entryBlocked.value) return;
        entryBusy.value = true;
        entryError.value = '';
        let res;
        try {
            res = await submit(entryDraft.value);
        } finally {
            entryBusy.value = false;
        }
        if (res?.outcome === 'ok') return closeEntry(true);
        if (res?.code === 'duplicate_entry') {
            entryError.value = 'Diese Schreibweise gibt es in dieser Wortliste bereits.';
            entryWritten.value?.focus();
        } else if (res?.outcome === 'conflict') {
            entryError.value = 'Das Wort wurde inzwischen geändert. Bitte abbrechen und neu öffnen.';
            entryBlocked.value = true;
        } else {
            entryError.value = res?.message || 'Das Wort konnte nicht gespeichert werden.';
        }
    }
    return {
        entryDialog, entryWritten, entryDraft, entryBusy, entryBlocked, entryError, entryNotice, entryErrorStyle,
        entryDialogPrefix, openEntry, openSelectedEntry, closeEntry, saveEntry, maxWordLength: MAX_SELECTION,
    };
}

export const VOCABULARY_ENTRY_DIALOG = `
  <dialog ref="entryDialog" class="voc-entry-dialog" :aria-labelledby="entryDialogPrefix + '-entry-title'"
    @cancel.prevent="closeEntry()" @keydown.stop>
    <form v-if="entryDraft" class="stack" @submit.prevent="saveEntry">
      <div class="dialoghead"><h2 :id="entryDialogPrefix + '-entry-title'">{{ entryDraft.entry ? 'Wort bearbeiten' : 'Wort hinzufügen' }}</h2></div>
      <p style="overflow-wrap:anywhere">Wortliste: <strong>{{ entryListName }}</strong></p>
      <div>
        <label :for="entryDialogPrefix + '-entry-written'">Schreibweise</label>
        <input :id="entryDialogPrefix + '-entry-written'" ref="entryWritten" class="w-full" autocomplete="off" required
          :maxlength="maxWordLength" v-model="entryDraft.written" :readonly="entryBusy" autofocus>
      </div>
      <div>
        <label :for="entryDialogPrefix + '-entry-spoken'">Sprechform (optional)</label>
        <input :id="entryDialogPrefix + '-entry-spoken'" class="w-full" autocomplete="off"
          :maxlength="maxWordLength" v-model="entryDraft.spoken" :readonly="entryBusy">
      </div>
      <p v-if="entryError" role="alert" class="note" :style="entryErrorStyle">{{ entryError }}</p>
      <div class="actions">
        <button type="button" :disabled="entryBusy" @click="closeEntry()">Abbrechen</button>
        <button type="submit" class="primary" :disabled="entryBusy || entryBlocked">{{ entryBusy ? 'Wird gespeichert …' : 'Speichern' }}</button>
      </div>
    </form>
  </dialog>`;

export const VOCABULARY_TEMPLATE = `
<section class="vocabulary-ui v8-page stack" aria-labelledby="voc-title" :aria-busy="state.status === 'loading'">
  <h2 id="voc-title">{{ label('clinic.vocabulary.title') }}</h2>
  <form class="filterbar voc-filters" @submit.prevent="store.search()">
    <div class="field voc-list" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-pool">Wortliste</label>
      <select id="voc-pool" class="select" :style="controlStyle" :value="state.scope.poolId || ''" :disabled="importBusy || state.proposalBusy || !!entryDraft || !!deleteDraft.length"
        @change="selectList($event.target.value)">
        <option value="">{{ label('clinic.vocabulary.scopeUser') }}</option>
        <option v-for="pool in pools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
      </select>
    </div>
    <div class="field voc-search-field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-field">Suchen nach</label>
      <select class="select" id="voc-field" v-model="state.query.field">
        <option v-for="(text, field) in searchFields" :key="field" :value="field">{{ text }}</option>
      </select>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-mode">{{ label('clinic.vocabulary.mode') }}</label>
      <select class="select" :style="controlStyle" id="voc-mode" v-model="state.query.mode"><option v-for="mode in modes" :key="mode" :value="mode">{{ enumLabel(mode) }}</option></select>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-filter">{{ label('clinic.vocabulary.filter') }}</label>
      <select class="select" :style="controlStyle" id="voc-filter" aria-describedby="voc-asr-suppression" v-model="state.query.filter"><option v-for="filter in filters" :key="filter" :value="filter">{{ enumLabel(filter) }}</option></select>
    </div>
    <div class="field search" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="voc-q">{{ label('clinic.vocabulary.query') }}</label>
      <input id="voc-q" type="search" data-role="query" autocomplete="off" v-model="state.query.q">
    </div>
    <button type="submit">{{ label('clinic.vocabulary.search') }}</button>
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
      <strong>{{ state.scope.kind === 'pool' ? selectedPoolName : label('clinic.vocabulary.ownList') }}</strong> ·
    </span>
    <span v-if="state.status === 'loading'">{{ label('clinic.vocabulary.loading') }}</span>
    <span v-else-if="state.status === 'stale'">{{ label('clinic.vocabulary.stale') }}</span>
    <span v-else-if="state.message">{{ state.message }}</span>
    <span v-else-if="state.status === 'error' || state.status === 'denied'">Wortschatz konnte nicht geladen werden.</span>
    <span v-else-if="state.status === 'ready'">{{ state.entries.length }} Einträge geladen.</span>
  </p>
  <div v-if="state.canWrite && selectedIds.length" class="actions">
    <button type="button" style="min-height:48px" aria-haspopup="dialog" @click="openDelete()">Auswahl löschen ({{ selectedIds.length }})</button>
  </div>
  <form v-if="canPropose" class="field" @submit.prevent="submitProposal()">
    <label for="voc-proposal">{{ label('clinic.vocabulary.written') }}</label>
    <input id="voc-proposal" type="text" maxlength="255" required autocomplete="off" class="w-full"
      :disabled="state.proposalBusy || !!state.failedWrite" v-model="proposalDraft">
    <div class="actions">
      <button type="submit" class="primary" :disabled="state.proposalBusy || !!state.failedWrite">{{ label('clinic.vocabulary.propose') }}</button>
    </div>
  </form>
  <p v-if="state.status === 'ready'" class="voc-result-count">{{ state.entries.length }} Einträge</p>
  <div class="table-wrap" :class="{ 'voc-proposal-table': canPropose }" v-if="state.entries.length">
    <table :class="{ 'voc-entry-table': state.canWrite }" :aria-label="label('clinic.vocabulary.title')">
      <thead><tr>
        <th scope="col">{{ label('clinic.vocabulary.written') }}</th>
        <th scope="col">{{ label('clinic.vocabulary.spoken') }}</th>
        <th scope="col">{{ label('clinic.vocabulary.filter') }}</th>
        <th scope="col">Aktion</th>
      </tr></thead>
      <tbody><tr v-for="e in state.entries" :key="e.id">
        <td class="wrap"><label v-if="state.canWrite" class="voc-select-entry">
          <input type="checkbox" :value="e.id" v-model="selectedIds" :aria-label="'Auswählen: ' + e.written"
            :disabled="state.status === 'loading' || (!selectedIds.includes(e.id) && selectedIds.length >= maxBulk)">
          <strong>{{ e.written }}</strong>
        </label><strong v-else>{{ e.written }}</strong></td><td class="wrap">{{ e.spoken || '–' }}</td>
        <td><span class="tag" :class="{ error: e.status === 'suppressed' }">{{ enumLabel(e.status) }}</span></td>
        <template v-if="state.canWrite">
        <td>
          <div class="actions">
            <button type="button" class="small" :aria-label="'Bearbeiten: ' + e.written" @click="openEntry(e)">Bearbeiten</button>
            <button type="button" class="small" v-if="e.status === 'active'" aria-describedby="voc-asr-suppression" @click="store.suppress(e.id)">{{ label('clinic.vocabulary.suppress') }}</button>
            <button type="button" class="small" v-else @click="store.reactivate(e.id)">{{ label('clinic.vocabulary.reactivate') }}</button>
          </div>
        </td>
        </template>
        <td v-else>
          <button v-if="canPropose && e.status === 'active'" type="button" class="small"
            :disabled="state.proposalBusy || !!state.failedWrite" @click="store.proposeSuppression(e.id)">{{ label('clinic.vocabulary.proposeSuppression') }}</button>
        </td>
      </tr></tbody>
    </table>
  </div>
  <p v-else-if="state.status === 'ready'" class="empty" :class="{ 'voc-proposal-empty': canPropose }" role="status">
    <slot name="empty" :scope="state.scope" :query="state.query">{{ label('clinic.vocabulary.empty') }} für diese Auswahl. Suche oder Status anpassen.</slot>
  </p>
  <div v-if="state.canWrite" class="actions">
    <button ref="addWordButton" type="button" class="primary" style="min-height:48px" aria-haspopup="dialog" @click="openEntry()">Wort hinzufügen</button>
    <button type="button" class="small" style="min-height:48px" aria-haspopup="dialog" @mousedown.prevent @click="addSelected">{{ label('clinic.vocabulary.addSelection') }}</button>
  </div>
  <span :class="{ 'sr-only': !entryNotice }" role="status">{{ entryNotice }}</span>
  <span :class="{ 'sr-only': !deleteNotice }" role="status">{{ deleteNotice }}</span>
  ${VOCABULARY_ENTRY_DIALOG}
  <dialog ref="deleteDialog" class="voc-entry-dialog" aria-labelledby="voc-delete-title"
    aria-describedby="voc-delete-description" @cancel.prevent="closeDelete()">
    <form v-if="deleteDraft.length" class="stack" @submit.prevent="confirmDelete()">
      <div class="dialoghead"><h2 id="voc-delete-title">{{ deleteDraft.length === 1 ? 'Eintrag löschen?' : deleteDraft.length + ' Einträge löschen?' }}</h2></div>
      <p id="voc-delete-description">Die Auswahl wird dauerhaft aus der Wortliste «{{ entryListName }}» gelöscht.</p>
      <ul class="voc-delete-entries">
        <li v-for="entry in deleteDraft" :key="entry.id">{{ entry.written }}</li>
      </ul>
      <div class="actions">
        <button type="button" autofocus :disabled="deleteBusy" @click="closeDelete()">Abbrechen</button>
        <button type="submit" class="primary" :disabled="deleteBusy">{{ deleteBusy ? 'Wird gelöscht …' : 'Dauerhaft löschen' }}</button>
      </div>
    </form>
  </dialog>
  <p id="voc-asr-suppression" class="note">
    <strong>{{ label('clinic.vocabulary.asrSuppressionUnavailable') }}</strong>.
    {{ label('clinic.vocabulary.asrSuppressionDetail') }}
  </p>
  <section class="voc-editor" aria-labelledby="voc-editor-title">
    <header class="voc-editor-head">
      <h3 id="voc-editor-title">{{ state.canWrite ? 'Wortliste bearbeiten' : 'Wortlistenaktionen' }}</h3>
      <div class="actions">
        <button type="button" class="small" @click="store.reload()">{{ label('clinic.vocabulary.reload') }}</button>
        <button type="button" class="small" v-if="state.failedWrite?.command" :disabled="!canPropose || state.proposalBusy" @click="submitProposal(true)">{{ label('clinic.vocabulary.retry') }}</button>
        <button type="button" class="small" v-else-if="state.failedWrite" :disabled="importBusy" @click="runImport(() => store.retry())">{{ label('clinic.vocabulary.retry') }}</button>
      </div>
    </header>
    <div class="voc-editor-body">
      <form v-if="state.canWrite" @submit.prevent="doImport(false)">
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
            const selectedIds = Vue.ref([]);
            const store = createVocabularyStore(client, (next) => {
                if (next.status === 'loading' || !next.canWrite || !next.entries.length) selectedIds.value = [];
            }, Vue.reactive);
            const { state } = store;
            const surfaceStyle = Vue.computed(() => {
                const s = statusSurface(state);
                return { background: s.background, color: s.color, borderColor: s.border };
            });
            const control = surfaceFor('normal');
            const controlStyle = { backgroundColor: control.background, color: control.color, borderColor: control.border };
            const pools = Vue.ref([]);
            const poolStatus = Vue.ref('loading');
            const selectedPoolName = Vue.computed(() => pools.value.find((pool) => pool.id === state.scope.poolId)?.name || label('clinic.vocabulary.scopePool'));
            const entryListName = Vue.computed(() => state.scope.kind === 'pool' ? selectedPoolName.value : label('clinic.vocabulary.ownList'));
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
                if (entryModal.entryDraft.value || deleteDraft.value.length) return;
                entryModal.entryNotice.value = '';
                deleteNotice.value = '';
                importError.value = '';
                proposalDraft.value = '';
                return id ? store.setScope('pool', id) : store.setScope('user');
            }
            const addWordButton = Vue.ref(null);
            const entryModal = useVocabularyEntryDialog(Vue, async (draft) => (
                await store.saveEntry(draft) || { message: state.message }
            ), 'voc', () => {
                if (typeof document !== 'undefined' && document.activeElement === document.body) addWordButton.value?.focus();
            });
            const deleteDialog = Vue.ref(null);
            const deleteDraft = Vue.shallowRef([]);
            const deleteBusy = Vue.ref(false);
            const deleteNotice = Vue.ref('');
            async function openDelete() {
                if (!state.canWrite || deleteDraft.value.length || state.status === 'loading') return;
                deleteNotice.value = '';
                deleteDraft.value = state.entries.filter(({ id }) => selectedIds.value.includes(id));
                if (!deleteDraft.value.length) return;
                await Vue.nextTick();
                if (deleteDraft.value.length) deleteDialog.value?.showModal();
            }
            function closeDelete() {
                if (deleteBusy.value) return;
                deleteDialog.value?.close();
                deleteDraft.value = [];
            }
            async function confirmDelete() {
                if (!deleteDraft.value.length || deleteBusy.value) return;
                deleteBusy.value = true;
                let res;
                try {
                    res = await store.deleteEntries(deleteDraft.value);
                } finally {
                    deleteBusy.value = false;
                }
                closeDelete();
                selectedIds.value = [];
                if (res?.outcome === 'ok') {
                    deleteNotice.value = res.data.deleted === 1 ? '1 Eintrag gelöscht.' : `${res.data.deleted} Einträge gelöscht.`;
                } else if (res?.outcome === 'conflict') {
                    deleteNotice.value = 'Einträge wurden geändert. Bitte erneut auswählen und das Löschen bestätigen.';
                } else if (res?.outcome === 'network' || res?.outcome === 'UNKNOWN') {
                    deleteNotice.value = 'Löschen konnte nicht bestätigt werden. Bitte prüfen Sie die Wortliste.';
                } else {
                    deleteNotice.value = 'Die Auswahl konnte nicht gelöscht werden. Bitte prüfen Sie die Wortliste.';
                }
                await Vue.nextTick();
                addWordButton.value?.focus();
            }
            const proposalDraft = Vue.ref('');
            const canPropose = Vue.computed(() => state.scope.kind === 'pool' && state.canWrite === false && state.status === 'ready');
            async function submitProposal(retry = false) {
                const addition = !retry || state.failedWrite?.command?.payload.operation === 'add';
                const res = await (retry ? store.retry() : store.propose(proposalDraft.value));
                if (res?.outcome === 'ok' && addition) proposalDraft.value = '';
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
            function addSelected(event) {
                const text = globalThis.getSelection?.()?.toString() || '';
                event.currentTarget.focus();
                return entryModal.openSelectedEntry(text);
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
            Vue.onMounted(() => Promise.all([store.search(), loadPools()]));
            return {
                state, store, label, enumLabel: vocabularyEnumLabel, surfaceStyle, controlStyle, pools, poolStatus, selectedPoolName, selectList, loadPools, addSelected,
                proposalDraft, canPropose, submitProposal,
                importDraft, importFile, fileInput, encoding, duplicateRule, importBusy, importError, importErrorStyle, exportBusy, canImport, importReport,
                pickFile, runImport, doImport, downloadList,
                ...entryModal, entryListName, addWordButton, selectedIds, deleteDialog, deleteDraft, deleteBusy, deleteNotice,
                openDelete, closeDelete, confirmDelete, maxBulk: MAX_BULK, searchFields: SEARCH_FIELDS,
                encodings: ENCODINGS, duplicateRules: DUPLICATE_RULES, modes: SEARCH_MODES, filters: STATUS_FILTERS, onKey: (e) => handleKey(store, e),
            };
        },
    };
}
