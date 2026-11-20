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
 * Endpoints (relative to /api/clinic/v1; see ENDPOINTS). None of them has an
 * HTTP route at clinic/main yet; the services behind them exist in
 * src/clinic/import_centre.py, import_visibility.py and device_inventory_view.py.
 * The agent HTTP routes belong to package v8-13.
 */

import { surfaceFor } from './tokens.js';

export const COMPOSE_IMPORT_VERSION = 1;

export const ENDPOINTS = Object.freeze({
    intents: '/imports/intents',
    inventory: '/imports/inventory',
    unassigned: '/imports/unassigned',
    assign: (id) => '/imports/unassigned/' + encodeURIComponent(id) + '/assign',
    retry: (id) => '/imports/intents/' + encodeURIComponent(id) + '/retry',
});

export const TEXT = Object.freeze({
    title: 'Dateien und Geräteimport',
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
    clarificationHint: 'Unbekannte oder widersprüchliche Zuordnungen bleiben hier, bis ein Auftrag ausdrücklich zugewiesen wird.',
    assign: 'Zuweisen',
    confirm: 'Zuweisung bestätigen',
    cancel: 'Abbrechen',
    retry: 'Erneut versuchen',
    denied: 'Keine Berechtigung für diese Aktion.',
    assigned: 'Die Datei wurde zugewiesen.',
    assignFailed: 'Die Zuweisung ist fehlgeschlagen.',
    retryFailed: 'Der Versuch ist fehlgeschlagen. Ein erneuter Versuch verwendet denselben Schlüssel.',
    intent: 'Importauftrag',
});

// Intent states of import_centre.py plus FAILED for a delivery that did not complete.
export const STATUS = Object.freeze({
    OPEN: { label: 'Ausstehend', surface: 'pending' },
    FULFILLED: { label: 'Übernommen', surface: 'success' },
    EXPIRED: { label: 'Abgelaufen', surface: 'warning' },
    CANCELLED: { label: 'Abgebrochen', surface: 'normal' },
    FAILED: { label: 'Fehlgeschlagen', surface: 'error' },
});
const RETRYABLE = new Set(['FAILED', 'EXPIRED']);

export function statusView(status) {
    const known = STATUS[String(status || '').toUpperCase()];
    const entry = known || { label: status ? String(status) : '–', surface: 'normal' };
    const s = surfaceFor(entry.surface);
    return { label: entry.label, style: { background: s.background, color: s.color, border: '1px solid ' + s.border } };
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
    const canClarify = mayClarify(user);

    const state = makeState({
        loading: false,
        intents: [], intentsError: null,
        inventory: [], inventoryError: null, inventoryLoaded: false,
        unassigned: [], unassignedError: null, clarificationDenied: false,
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
        if (res.outcome === 'ok') { state.inventory = rows(res); state.inventoryError = null; state.inventoryLoaded = true; }
        else state.inventoryError = res.outcome === 'permission' ? TEXT.denied : TEXT.inventoryFailed;
        return res;
    }

    // An ordinary actor never requests the clarification list.
    async function loadUnassigned() {
        if (!canClarify) return null;
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
            await Promise.all([loadIntents(), loadInventory(), loadUnassigned()]);
        } finally {
            if (!disposed) state.loading = false;
        }
    }

    function askAssign(deliveryId) {
        if (!canClarify || state.clarificationDenied) return false;
        state.pending = String(deliveryId);
        return true;
    }
    const cancelAssign = () => { state.pending = null; };

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

    /** Assigns the confirmed delivery to an open import intent. Needs askAssign first. */
    async function confirmAssign(deliveryId, intentId, snapshotVersion) {
        const id = String(deliveryId);
        if (!canClarify || state.pending !== id || intentId == null) return null;
        const res = await write('assign', id, ENDPOINTS.assign(id), {
            intent_id: String(intentId), expected_snapshot_version: snapshotVersion == null ? null : String(snapshotVersion),
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
        client.switchContext({ accountId: null, dictationId: null });
    }

    return {
        state, canClarify, load, loadIntents, loadInventory, loadUnassigned,
        askAssign, cancelAssign, confirmAssign, retry, dispose,
        isBusy: (action, id) => Boolean(state.busy[action + ':' + id]),
        retryable: (row) => RETRYABLE.has(String(row && row.status).toUpperCase()),
        statusView, text: TEXT,
    };
}

const button = 'class="px-3 py-2 border rounded" style="min-height: 2.75rem"';

export const IMPORT_TEMPLATE = `
<section class="clinic-import-view" data-testid="clinic-import-view" aria-labelledby="civ-title">
  <h1 id="civ-title" class="text-lg font-semibold">{{ c.text.title }}</h1>
  <button type="button" ${button} data-testid="reload" :disabled="c.state.loading" @click="c.load()">{{ c.text.refresh }}</button>
  <p v-if="c.state.notice" role="status" aria-live="polite" data-testid="notice">{{ c.state.notice }}</p>
  <p v-if="c.state.loading" role="status" aria-live="polite">{{ c.text.loading }}</p>

  <h2 class="font-semibold mt-3">{{ c.text.list }}</h2>
  <p v-if="c.state.intentsError" role="alert" data-testid="intents-error">{{ c.state.intentsError }}</p>
  <p v-else-if="!c.state.intents.length" data-testid="intents-empty">{{ c.text.listEmpty }}</p>
  <div v-else class="overflow-x-auto">
    <table class="w-full" aria-label="Importaufträge" data-testid="intent-list">
      <thead><tr><th scope="col">Quelle</th><th scope="col">Dateien</th><th scope="col">Status</th><th scope="col">Erstellt</th><th scope="col"></th></tr></thead>
      <tbody>
        <tr v-for="row in c.state.intents" :key="row.id" data-intent :data-id="row.id">
          <td>{{ row.source_ref }}</td>
          <td>{{ (row.expected_files || []).length }}</td>
          <td><span class="px-2 py-1 rounded text-xs" :style="c.statusView(row.status).style">{{ c.statusView(row.status).label }}</span></td>
          <td>{{ row.created_at }}</td>
          <td>
            <button v-if="c.retryable(row)" type="button" ${button} data-testid="retry"
                    :disabled="c.isBusy('retry', row.id)" @click="c.retry(row.id)">{{ c.text.retry }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <h2 class="font-semibold mt-3">{{ c.text.inventory }}</h2>
  <p class="text-sm" style="color: var(--text-secondary)">{{ c.text.inventoryNote }}</p>
  <p v-if="c.state.inventoryError" role="alert" data-testid="inventory-error">{{ c.state.inventoryError }}</p>
  <p v-else-if="!c.state.inventory.length" data-testid="inventory-empty">{{ c.text.inventoryEmpty }}</p>
  <div v-else class="overflow-x-auto">
    <table class="w-full" aria-label="Geräteinventar" data-testid="inventory-list">
      <thead><tr><th scope="col">Quelle</th><th scope="col">Datei</th><th scope="col">Grösse</th><th scope="col">Aufnahmedatum</th><th scope="col">Stand</th></tr></thead>
      <tbody>
        <tr v-for="(item, i) in c.state.inventory" :key="item.source_id + '|' + item.snapshot_version + '|' + i">
          <td>{{ item.source_id }}</td><td>{{ item.relative_path }}</td><td>{{ item.size }}</td>
          <td>{{ item.mtime_utc || '–' }}</td><td>{{ item.snapshot_version }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <section v-if="c.canClarify" aria-labelledby="civ-clar" data-testid="clarification">
    <h2 id="civ-clar" class="font-semibold mt-3">{{ c.text.clarification }}</h2>
    <p class="text-sm">{{ c.text.clarificationHint }}</p>
    <p v-if="c.state.clarificationDenied" role="alert" data-testid="clarification-denied">{{ c.text.denied }}</p>
    <p v-else-if="c.state.unassignedError" role="alert">{{ c.state.unassignedError }}</p>
    <p v-else-if="!c.state.unassigned.length" data-testid="clarification-empty">{{ c.text.clarificationEmpty }}</p>
    <ul v-else aria-label="Klärliste">
      <li v-for="item in c.state.unassigned" :key="item.id" data-unassigned :data-id="item.id">
        <span>{{ item.relative_path }} ({{ item.source_id }})</span>
        <template v-if="c.state.pending === item.id">
          <label>{{ c.text.intent }}
            <select v-model="choice[item.id]">
              <option v-for="i in c.state.intents.filter((r) => r.status === 'OPEN')" :key="i.id" :value="i.id">{{ i.source_ref }}</option>
            </select>
          </label>
          <button type="button" ${button} data-testid="confirm-assign" :disabled="c.isBusy('assign', item.id) || !choice[item.id]"
                  @click="c.confirmAssign(item.id, choice[item.id], item.snapshot_version)">{{ c.text.confirm }}</button>
          <button type="button" ${button} @click="c.cancelAssign()">{{ c.text.cancel }}</button>
        </template>
        <button v-else type="button" ${button} data-testid="assign" @click="c.askAssign(item.id)">{{ c.text.assign }}</button>
      </li>
    </ul>
  </section>
</section>`;

export const ImportView = {
    name: 'ClinicImportView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, required: true },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        if (!V) throw new Error('Vue is required to mount the import view');
        const c = createImportController({ client: props.client, currentUser: props.currentUser, makeState: V.reactive });
        const choice = V.reactive({});
        if (typeof V.onMounted === 'function') V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c, choice };
    },
    template: IMPORT_TEMPLATE,
};

export function registerImportView(app) {
    app.component('clinic-import-view', ImportView);
    return app;
}
