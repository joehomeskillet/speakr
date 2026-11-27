/**
 * Composed import view (V8 P09): import list with status per item, device
 * inventory reported by the import agent, and the clarification list
 * (Klärliste) for admins and pool leads only (owner decision W13).
 *
 * No UI module for the import list, the device inventory or the clarification
 * list existed before this package, so this file is the thin view over the
 * shared api-client.js; colours come from tokens.js. Server state is
 * authoritative: this view only hides what the actor may not see, the server
 * still decides. Nothing is written to browser storage; clinical data stays in
 * memory. Patients are not shown at all (W16): rows carry file names, source
 * references, status and author ids only.
 *
 * Contract for the mount package:
 *   ImportView                 Vue options object, props client, currentUser, vue (optional)
 *   registerImportView(app)    app.component('clinic-import-view', ImportView)
 *
 * Endpoints are relative to /api/clinic/v1 (see ENDPOINTS).
 * The agent HTTP routes belong to package v8-13.
 */

import { surfaceFor } from './tokens.js';
import { ImportSourceRouting } from './import-source-routing.js';

import { formatMoment } from './compose-archive.js';

export const COMPOSE_IMPORT_VERSION = 1;

/** Byte count as "182 KB" / "18.4 MB"; anything that is not a number is shown as received. */
export function formatSize(value) {
    const n = Number(value);
    if (value === null || value === undefined || value === '' || !Number.isFinite(n) || n < 0) return value === null || value === undefined || value === '' ? '–' : String(value);
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

/** UTC instant as "dd.mm.yyyy hh:mm" in Europe/Zurich; missing or unparsable input is shown as received. */
export function formatWhen(value) {
    if (value === null || value === undefined || value === '') return '–';
    return formatMoment(value) || String(value);
}

/**
 * Root classes of every V8 operations page. The element rules of static/css/clinic-v8.css sit in :where()
 * and load before tailwind.css, so the Tailwind base reset wins over them (headings, buttons) and the shell's
 * .primary (burgundy, 11px) wins over the page button. These variants restore the prototype values at the
 * view root; they can go once the stylesheet outranks the reset.
 */
export const V8_PAGE = 'v8-page text-sm [&_.stack]:grid-cols-[minmax(0,1fr)] [&_.field]:border-0 [&_.field]:bg-transparent [&_.field]:rounded-none [&_.field]:p-0 [&_h1]:text-[30px] [&_h1]:leading-[1.2] [&_h1]:font-bold [&_h1]:tracking-[-0.8px] [&_h1]:mb-[9px] max-[760px]:[&_h1]:text-[26px] [&_h2]:text-base [&_h2]:leading-[1.35] [&_h2]:font-bold [&_h2]:tracking-[-0.2px] [&_h2]:mb-[14px] [&_h3]:text-sm [&_h3]:font-bold [&_h3]:mb-[10px] [&_button]:text-sm [&_button]:font-semibold [&_button]:px-[13px] [&_button]:py-[9px] [&_p]:mb-3 [&_.subline]:mb-0 [&_.subline]:text-[var(--muted)] [&_.panel_.empty]:min-h-0 [&_.panel_.empty]:block max-[760px]:[&_table]:whitespace-normal max-[760px]:[&_.tag]:whitespace-normal max-[760px]:[&_.small]:min-h-[44px] [&_.primary]:bg-[var(--ink)] [&_.primary]:border-[var(--ink)] [&_.primary]:text-white [&_.primary]:text-sm [&_.primary]:font-semibold [&_.primary]:px-[13px] [&_.primary]:py-[9px] [&_.small]:text-xs [&_.small]:px-[9px] [&_.small]:py-[5px]';

export const ENDPOINTS = Object.freeze({
    intents: '/imports/intents',
    inventory: '/imports/inventory',
    unassigned: '/imports/unassigned',
    candidates: (id) => '/imports/unassigned/' + encodeURIComponent(id) + '/candidates',
    assign: (id) => '/imports/unassigned/' + encodeURIComponent(id) + '/assign',
    retry: (id) => '/imports/intents/' + encodeURIComponent(id) + '/retry',
});

// Keep the action column readable on narrow screens, even with long source IDs or error codes.
export const ACTION_TABLE = 'max-md:[&_th]:px-1.5 max-md:[&_td]:px-1.5 max-md:[&_td:first-child]:[overflow-wrap:anywhere]';

export const TEXT = Object.freeze({
    title: 'Dateien und Geräteimport',
    eyebrow: 'Aufnahmen',
    subline: 'Importaufträge, Geräteinventar und Klärliste. Dateien ohne eindeutige Zuordnung bleiben sichtbar, bis Autor und Pool ausgewählt wurden.',
    colSource: 'Quelle',
    colFiles: 'Dateien',
    colStatus: 'Status',
    colCreated: 'Erstellt',
    colFile: 'Datei',
    colAction: 'Aktion',
    refresh: 'Aktualisieren',
    loading: 'Laden …',
    list: 'Importaufträge',
    listEmpty: 'Keine Importaufträge vorhanden.',
    listFailed: 'Die Importaufträge konnten nicht geladen werden.',
    inventory: 'Geräteinventar',
    inventoryEmpty: 'Es liegt keine Meldung eines Importagenten vor. Es wird keine Geräteerkennung angezeigt.',
    inventoryFailed: 'Das Geräteinventar konnte nicht geladen werden.',
    inventoryNote: 'Nur Ansicht. Konfiguration und Firmware-Schreiben sind nicht verfügbar.',
    clarification: 'Klärliste',
    clarificationEmpty: 'Keine Dateien in Klärung.',
    clarificationFailed: 'Die Klärliste konnte nicht geladen werden.',
    clarificationHint: 'Wählen Sie für jede ungeklärte Datei einen Autor und einen Pool.',
    assign: 'Zuweisen',
    confirm: 'Zuweisung bestätigen',
    cancel: 'Abbrechen',
    retry: 'Erneut versuchen',
    denied: 'Keine Berechtigung für diese Aktion.',
    assigned: 'Die Datei wurde zugewiesen.',
    assignFailed: 'Die Zuweisung ist fehlgeschlagen.',
    retryFailed: 'Der Versuch ist fehlgeschlagen. Ein erneuter Versuch verwendet denselben Schlüssel.',
    assignmentTarget: 'Autor und Pool',
    noTargets: 'Keine verfügbaren Zuordnungen',
});

// Intent states of import_centre.py plus FAILED for a delivery that did not complete.
export const STATUS = Object.freeze({
    OPEN: { label: 'Ausstehend', surface: 'pending' },
    FULFILLED: { label: 'Übernommen', surface: 'success' },
    EXPIRED: { label: 'Abgelaufen', surface: 'warning' },
    CANCELLED: { label: 'Abgebrochen', surface: 'normal' },
    FAILED: { label: 'Fehlgeschlagen', surface: 'error' },
});
// Tag modifier of the shared V8 stylesheet per status surface.
const TAG_CLASS = Object.freeze({ pending: 'wait', success: '', warning: 'wait', error: 'error', normal: 'neutral' });
const RETRYABLE = new Set(['FAILED', 'EXPIRED']);

export function statusView(status) {
    const known = STATUS[String(status || '').toUpperCase()];
    const entry = known || { label: status ? String(status) : '–', surface: 'normal' };
    const s = surfaceFor(entry.surface);
    return {
        label: entry.label,
        tag: TAG_CLASS[entry.surface] ?? 'neutral',
        style: { background: s.background, color: s.color, border: '1px solid ' + s.border },
    };
}

/** W13: only active admins and pool leads see the clarification list. */
export function mayClarify(user) {
    const u = user || {};
    return u.role === 'admin' || u.isAdmin === true || u.is_admin === true || u.isPoolLead === true;
}

const idOf = (user) => (user && user.id != null && String(user.id) !== '' ? String(user.id) : null);

let keyCounter = 0;
function defaultKey(prefix) {
    const c = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
    if (c && typeof c.randomUUID === 'function') return prefix + '-' + c.randomUUID();
    keyCounter += 1;
    return prefix + '-' + Date.now().toString(36) + '-' + keyCounter;
}

const plain = (o) => o;
const rows = (res) => (res && res.data && Array.isArray(res.data.items) ? res.data.items : []);

/**
 * Framework-free controller.
 * @param {{client: object, currentUser: object, makeState?: Function, newKey?: Function}} deps
 */
export function createImportController(deps) {
    const client = deps && deps.client;
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const user = deps.currentUser || {};
    const userId = idOf(user);
    if (userId === null) throw new Error('currentUser.id is required');
    const makeState = deps.makeState || plain;
    const newKey = deps.newKey || defaultKey;
    const state = makeState({
        loading: false,
        intents: [], intentsError: null,
        inventory: [], inventoryError: null, inventoryLoaded: false,
        unassigned: [], unassignedError: null, clarificationDenied: false,
        canClarify: mayClarify(user), candidates: null, target: null,
        pending: null, // delivery id awaiting confirmation
        busy: {}, // item id -> true while a write is in flight
        notice: null,
    });
    const keys = new Map(); // item id -> Idempotency-Key reused until the write succeeds
    let disposed = false;
    client.switchContext({ accountId: userId, dictationId: null });

    const keyFor = (action, id) => {
        const k = action + ':' + id;
        if (!keys.has(k)) keys.set(k, newKey(action));
        return keys.get(k);
    };

    async function loadIntents() {
        const res = await client.request(ENDPOINTS.intents);
        if (disposed) return res;
        if (res.outcome === 'ok') { state.intents = rows(res); state.intentsError = null; }
        else state.intentsError = res.outcome === 'permission' ? TEXT.denied : TEXT.listFailed;
        return res;
    }

    async function loadInventory() {
        const res = await client.request(ENDPOINTS.inventory);
        if (disposed) return res;
        if (res.outcome === 'ok') {
            state.inventory = rows(res); state.inventoryError = null; state.inventoryLoaded = true;
            state.canClarify = res.data?.can_clarify ?? mayClarify(user);
        }
        else state.inventoryError = res.outcome === 'permission' ? TEXT.denied : TEXT.inventoryFailed;
        return res;
    }

    // An ordinary actor never requests the clarification list.
    async function loadUnassigned() {
        if (!state.canClarify) return null;
        const res = await client.request(ENDPOINTS.unassigned);
        if (disposed) return res;
        if (res.outcome === 'ok') { state.unassigned = rows(res); state.unassignedError = null; state.clarificationDenied = false; }
        else if (res.outcome === 'permission') { state.unassigned = []; state.clarificationDenied = true; state.unassignedError = null; }
        else state.unassignedError = TEXT.clarificationFailed;
        return res;
    }

    async function load() {
        if (disposed) return;
        state.loading = true;
        try {
            await Promise.all([loadIntents(), loadInventory().then(loadUnassigned)]);
        } finally {
            if (!disposed) state.loading = false;
        }
    }

    function askAssign(deliveryId) {
        if (!state.canClarify || state.clarificationDenied) return false;
        state.pending = String(deliveryId);
        state.candidates = null;
        state.target = null;
        state.notice = null;
        return true;
    }
    const cancelAssign = () => { state.pending = null; };

    async function loadCandidates(deliveryId) {
        const id = String(deliveryId);
        if (state.pending !== id) return;
        const res = await client.request(ENDPOINTS.candidates(id));
        if (disposed || state.pending !== id) return;
        state.candidates = rows(res);
        if (res.outcome !== 'ok') state.notice = TEXT.assignFailed;
    }

    // One request per call; a second call for the same item while one is in flight is ignored.
    async function write(action, id, path, body) {
        if (disposed || state.busy[action + ':' + id]) return null;
        state.busy[action + ':' + id] = true;
        try {
            const res = await client.request(path, { method: 'POST', body, idempotencyKey: keyFor(action, id) });
            if (res.outcome === 'ok') keys.delete(action + ':' + id);
            return res;
        } finally {
            delete state.busy[action + ':' + id];
        }
    }

    /** Assigns the confirmed delivery to a candidate or to an existing import intent. */
    async function confirmAssign(deliveryId, target, snapshotVersion) {
        const id = String(deliveryId);
        if (!state.canClarify || state.pending !== id || target == null) return null;
        const selection = typeof target === 'object'
            ? { target_author_user_id: target.target_author_user_id, target_pool_id: target.target_pool_id }
            : { intent_id: String(target) };
        const res = await write('assign', id, ENDPOINTS.assign(id), {
            ...selection, expected_snapshot_version: snapshotVersion == null ? null : String(snapshotVersion),
        });
        if (!res || disposed) return res;
        if (res.outcome === 'ok') {
            state.pending = null;
            state.notice = TEXT.assigned;
            await Promise.all([loadUnassigned(), loadIntents()]);
        } else if (res.outcome === 'permission') {
            state.clarificationDenied = true;
            state.pending = null;
        } else {
            state.notice = TEXT.assignFailed;
        }
        return res;
    }

    /** Retry of one failed or expired item; every click sends one request with the item's stored key. */
    async function retry(intentId) {
        const id = String(intentId);
        const row = state.intents.find((r) => String(r.id) === id);
        if (!row || !RETRYABLE.has(String(row.status).toUpperCase())) return null;
        const res = await write('retry', id, ENDPOINTS.retry(id), {});
        if (!res || disposed) return res;
        if (res.outcome === 'ok') { state.notice = null; await loadIntents(); }
        else state.notice = TEXT.retryFailed;
        return res;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        keys.clear();
        state.intents = []; state.inventory = []; state.unassigned = []; state.pending = null;
        state.candidates = null; state.target = null;
        client.switchContext({ accountId: null, dictationId: null });
    }

    return {
        state, get canClarify() { return state.canClarify; }, load, loadIntents, loadInventory, loadUnassigned,
        askAssign, loadCandidates, cancelAssign, confirmAssign, retry, dispose,
        isBusy: (action, id) => Boolean(state.busy[action + ':' + id]),
        retryable: (row) => RETRYABLE.has(String(row && row.status).toUpperCase()),
        statusView, text: TEXT, size: formatSize, when: formatWhen,
    };
}

const ICON_REFRESH = '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/></svg>';

export const IMPORT_TEMPLATE = `
<section class="${V8_PAGE} clinic-import-view" data-testid="clinic-import-view" aria-labelledby="civ-title">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ c.text.eyebrow }}</div>
      <h1 id="civ-title">{{ c.text.title }}&nbsp;<span class="count-pill align-middle" data-testid="intent-count">{{ c.state.intents.length }}</span></h1>
      <p class="subline">{{ c.text.subline }}</p>
    </div>
    <div class="head-actions">
      <button type="button" data-testid="reload" :disabled="c.state.loading" @click="c.load()">${ICON_REFRESH}<span>{{ c.text.refresh }}</span></button>
    </div>
  </div>
  <p v-if="c.state.notice" class="note" role="status" aria-live="polite" data-testid="notice">{{ c.state.notice }}</p>
  <p v-if="c.state.loading" class="result-count" role="status" aria-live="polite">{{ c.text.loading }}</p>

  <div class="stack">
    <section class="panel" aria-labelledby="civ-list">
      <h2 id="civ-list">{{ c.text.list }}</h2>
      <p v-if="c.state.intentsError" class="note warning" role="alert" data-testid="intents-error">{{ c.state.intentsError }}</p>
      <p v-else-if="!c.state.intents.length" class="empty" data-testid="intents-empty">{{ c.text.listEmpty }}</p>
      <div v-else class="table-wrap">
        <table class="${ACTION_TABLE}" aria-label="Importaufträge" data-testid="intent-list">
          <thead><tr><th scope="col">{{ c.text.colSource }}</th><th scope="col" class="max-md:hidden">{{ c.text.colFiles }}</th><th scope="col">{{ c.text.colStatus }}</th><th scope="col" class="max-md:hidden">{{ c.text.colCreated }}</th><th scope="col" :aria-label="c.text.colAction"></th></tr></thead>
          <tbody>
            <tr v-for="row in c.state.intents" :key="row.id" data-intent :data-id="row.id">
              <td><strong>{{ row.source_ref }}</strong></td>
              <td class="max-md:hidden">{{ (row.expected_files || []).length }}</td>
              <td><span class="tag dot" :class="c.statusView(row.status).tag">{{ c.statusView(row.status).label }}</span></td>
              <td class="max-md:hidden" :title="row.created_at">{{ c.when(row.created_at) }}</td>
              <td>
                <button v-if="c.retryable(row)" type="button" class="small" data-testid="retry"
                        :disabled="c.isBusy('retry', row.id)" @click="c.retry(row.id)">{{ c.text.retry }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" aria-labelledby="civ-inv">
      <h2 id="civ-inv">{{ c.text.inventory }}</h2>
      <p class="hint">{{ c.text.inventoryNote }}</p>
      <p v-if="c.state.inventoryError" class="note warning" role="alert" data-testid="inventory-error">{{ c.state.inventoryError }}</p>
      <p v-else-if="!c.state.inventory.length" class="empty" data-testid="inventory-empty">{{ c.text.inventoryEmpty }}</p>
      <div v-else class="table-wrap">
        <table aria-label="Geräteinventar" data-testid="inventory-list">
          <thead><tr><th scope="col">Quelle</th><th scope="col">Datei</th><th scope="col">Grösse</th><th scope="col">Aufnahmedatum</th><th scope="col">Stand</th></tr></thead>
          <tbody>
            <tr v-for="(item, i) in c.state.inventory" :key="item.source_id + '|' + item.snapshot_version + '|' + i">
              <td>{{ item.source_id }}</td><td><strong>{{ item.relative_path }}</strong></td><td :title="item.size">{{ c.size(item.size) }}</td>
              <td>{{ c.when(item.mtime_utc) }}</td><td>{{ item.snapshot_version }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <import-source-routing :client="client"></import-source-routing>
    <details class="panel">
      <summary>Geräteautor am Philips LFH9600 oder LFH9620</summary>
      <p>Für ein neues Diktat wählen Sie am Recorder unter „KEYWORD“ das Feld „Author“.
        Die verfügbaren Namen richten Sie mit Philips SpeechExec ein.
        Der Importautor bestimmt die Zuordnung in Speakr.</p>
      <a href="https://www.dictation.philips.com/fileadmin/Products/lfh9600/ifu/lfh96x0_ifu_en.pdf#page=22"
         target="_blank" rel="noopener noreferrer">Philips-Handbuch, Seite 22 (PDF, Englisch)</a>
    </details>
    <section v-if="c.canClarify" class="panel" aria-labelledby="civ-clar" data-testid="clarification">
      <h2 id="civ-clar">{{ c.text.clarification }}</h2>
      <p class="hint">{{ c.text.clarificationHint }}</p>
      <p v-if="c.state.clarificationDenied" class="note warning" role="alert" data-testid="clarification-denied">{{ c.text.denied }}</p>
      <p v-else-if="c.state.unassignedError" class="note warning" role="alert">{{ c.state.unassignedError }}</p>
      <p v-else-if="!c.state.unassigned.length" class="empty" data-testid="clarification-empty">{{ c.text.clarificationEmpty }}</p>
      <div v-else class="table-wrap">
        <table class="${ACTION_TABLE} max-md:table-fixed" aria-label="Klärliste">
          <thead><tr><th scope="col">{{ c.text.colFile }}</th><th scope="col">{{ c.text.colAction }}</th></tr></thead>
          <tbody>
            <template v-for="item in c.state.unassigned" :key="item.id">
              <tr data-unassigned :data-id="item.id">
                <td><strong>{{ item.relative_path }}</strong><small>{{ item.source_id }}</small></td>
                <td>
                  <button v-if="c.state.pending !== item.id" type="button" class="small" data-testid="assign" @click="c.askAssign(item.id) && c.loadCandidates(item.id)">{{ c.text.assign }}</button>
                </td>
              </tr>
              <tr v-if="c.state.pending === item.id">
                <td colspan="2">
                  <div class="actions">
                    <label class="w-full md:w-auto md:flex md:items-center md:gap-2">{{ c.text.assignmentTarget }}
                      <select v-model="c.state.target" :disabled="!c.state.candidates?.length" class="block min-w-0 md:w-auto">
                        <option :value="null" disabled>{{ c.state.candidates === null ? c.text.loading : c.state.candidates.length ? c.text.assignmentTarget : c.text.noTargets }}</option>
                        <option v-for="target in c.state.candidates" :key="target.target_author_user_id + ':' + target.target_pool_id" :value="target">{{ target.author_name }} · {{ target.pool_name }}</option>
                      </select>
                    </label>
                    <button type="button" class="small primary" data-testid="confirm-assign" :disabled="c.isBusy('assign', item.id) || !c.state.target"
                            @click="c.confirmAssign(item.id, c.state.target, item.snapshot_version)">{{ c.text.confirm }}</button>
                    <button type="button" class="small" @click="c.cancelAssign()">{{ c.text.cancel }}</button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</section>`;

export const ImportView = {
    name: 'ClinicImportView',
    components: { ImportSourceRouting },
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, required: true },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        if (!V) throw new Error('Vue is required to mount the import view');
        const c = createImportController({ client: props.client, currentUser: props.currentUser, makeState: V.reactive });
        if (typeof V.onMounted === 'function') V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c };
    },
    template: IMPORT_TEMPLATE,
};

export function registerImportView(app) {
    app.component('clinic-import-view', ImportView);
    return app;
}
