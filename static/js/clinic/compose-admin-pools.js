/**
 * Composed admin view "Schreibkräfte-Pools" (V8 P10, PAR-ADM-003).
 *
 * Staff pools (Schreibkräfte-Pools) are managed as their own groups, separate
 * from author pools. Existing building blocks carry the work and are not
 * re-implemented here:
 *   - pools-ui.js         createPoolsStore: membership read (GET /pools), member
 *                         add and remove with the persisted revision, the visible
 *                         removal confirmation and the version-conflict state.
 *   - accounts-ui.js      createAccountsAdmin: the account directory that backs
 *                         the member search by name (GET /admin/users).
 *   - tokens.js           status colours. api-client.js supplies the clients.
 * This module adds the pool administration calls of package v8-29 (create,
 * rename, deactivate or reactivate, validity window, usage preview) under
 * /pool-admin and the page composition.
 *
 * Rules from the page specification: users are picked by name search, never by
 * a numeric id; pools are deactivated, never deleted, and the usage preview is
 * shown before a deactivation; a pool without members is not shown as staffed;
 * a 403 shows no admin form. Deactivation never moves open dictations and never
 * changes the author of a dictation. Only counts are shown for usage; no
 * clinical data and no dictation titles. Nothing is written to browser storage.
 *
 * Contract for the mount package:
 *   AdminPoolsView                 Vue options object, props client, userClient
 *                                  (optional, defaults to client), vue (optional)
 *   registerAdminPoolsView(app)    app.component('clinic-admin-pools-view', AdminPoolsView)
 * The account directory lives outside /api/clinic/v1 (/admin/users); the mount
 * package passes a userClient that reaches it, as for accounts-ui.js.
 */

import { createPoolsStore } from './pools-ui.js';
import { createAccountsAdmin } from './accounts-ui.js';
import { surfaceFor } from './tokens.js';

export const COMPOSE_ADMIN_POOLS_VERSION = 1;
export const ADMIN_BASE = '/pool-admin';
export const SEARCH_LIMIT = 8;
const KIND = 'staff';

export const TEXT = Object.freeze({
    title: 'Schreibkräfte-Pools',
    loading: 'Laden …',
    empty: 'Noch keine Schreibkräfte-Pools.',
    denied: 'Keine Berechtigung für die Verwaltung der Schreibkräfte-Pools.',
    failed: 'Die Schreibkräfte-Pools konnten nicht geladen werden.',
    retry: 'Erneut versuchen',
    reload: 'Aktuellen Stand laden',
    stale: 'Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
    listLabel: 'Schreibkräfte-Pools',
    create: 'Schreibkräfte-Pool anlegen',
    createName: 'Name des neuen Schreibkräfte-Pools',
    nameLabel: 'Name',
    rename: 'Umbenennen',
    detail: 'Details',
    noSelection: 'Wählen Sie einen Schreibkräfte-Pool aus der Liste.',
    statusActive: 'Aktiv',
    statusInactive: 'Deaktiviert',
    statusOutside: 'Aktiv, aktuell ausserhalb der Gültigkeit',
    validity: 'Gültigkeit',
    validFrom: 'Gültig ab',
    validTo: 'Gültig bis',
    saveValidity: 'Gültigkeit speichern',
    members: 'Mitglieder',
    noMembers: 'Keine Mitglieder. Der Pool ist nicht besetzt; neue Diktate an diesen Pool können nicht bearbeitet werden.',
    unknownMember: 'Unbekanntes Konto',
    memberSearch: 'Benutzer nach Name suchen',
    searchNone: 'Kein passendes aktives Konto gefunden.',
    searchUnavailable: 'Die Kontensuche ist nicht verfügbar. Mitglieder können jetzt nicht hinzugefügt werden.',
    addMember: 'Hinzufügen',
    removeMember: 'Mitglied entfernen',
    confirmRemove: 'Mitgliedschaft wirklich entfernen?',
    confirmRemoveYes: 'Ja, entfernen',
    cancel: 'Abbrechen',
    usage: 'Verwendung',
    usageOpen: 'Offene Diktate',
    usageMembers: 'Mitglieder',
    usageLinks: 'Verknüpfte Autorenpools',
    usageUnavailable: 'Die Verwendung konnte nicht geladen werden.',
    deactivate: 'Deaktivieren',
    reactivate: 'Reaktivieren',
    confirmDeactivate: 'Schreibkräfte-Pool deaktivieren?',
    deactivateNote: 'Neue Zuweisungen an diesen Pool werden abgelehnt. Vorhandene Diktate und der Verlauf bleiben erhalten, offene Diktate werden nicht automatisch umverteilt. Diktiert von bleibt unverändert.',
    deactivateYes: 'Ja, deaktivieren',
    nameRequired: 'Bitte einen Namen eingeben.',
    validityOrder: 'Das Ende der Gültigkeit muss nach dem Beginn liegen.',
    keys: 'Tastatur: Esc bricht eine offene Rückfrage ab.',
});

const ERROR_TEXT = Object.freeze({
    name_taken: 'Dieser Name ist bereits vergeben.',
    invalid_validity: 'Die Gültigkeit ist ungültig.',
    naive_datetime: 'Die Gültigkeit ist ungültig.',
    invalid_name: 'Der Name ist ungültig.',
    pool_inactive: 'Der Schreibkräfte-Pool ist deaktiviert.',
    not_staff_pool: 'Der Schreibkräfte-Pool wurde nicht gefunden.',
    conflict: 'Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
});

function errorText(res) {
    return (res.code && ERROR_TEXT[res.code]) || res.message || TEXT.failed;
}

/** Active accounts whose name matches, minus the excluded ids; at most SEARCH_LIMIT. Never matches on an id. */
export function searchAccounts(accounts, query, excludeIds) {
    const needle = String(query || '').trim().toLowerCase();
    if (!needle) return [];
    const skip = new Set((excludeIds || []).map(Number));
    return (Array.isArray(accounts) ? accounts : [])
        .filter((a) => a.status === 'active' && !skip.has(Number(a.id)) && String(a.username || '').toLowerCase().includes(needle))
        .slice(0, SEARCH_LIMIT);
}

const pad = (n) => String(n).padStart(2, '0');

/** ISO instant to the local value of a datetime-local input; empty for none. */
export function toLocalInput(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

/** Local datetime-local value to an ISO instant with offset; null for empty, undefined for unparsable. */
export function fromLocalInput(value) {
    const text = String(value || '').trim();
    if (!text) return null;
    const d = new Date(text);
    return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

/** Status of a pool row for display. */
export function poolStatus(pool) {
    if (!pool.active) return { key: 'inactive', label: TEXT.statusInactive, surface: 'warning' };
    if (!pool.routable) return { key: 'outside', label: TEXT.statusOutside, surface: 'warning' };
    return { key: 'active', label: TEXT.statusActive, surface: 'success' };
}

const styleOf = (name) => {
    const s = surfaceFor(name);
    return { background: s.background, color: s.color, border: '1px solid ' + s.border };
};

/**
 * @param {{client: {request: Function}, userClient?: {request: Function}, reactive?: Function}} options
 */
export function createAdminPoolsController(options) {
    const client = options && options.client;
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const reactive = options.reactive || ((o) => o);
    let disposed = false;

    const state = reactive({
        status: 'idle', // idle | loading | ready | denied | error
        meta: [], // pool rows from /pool-admin/pools
        selectedId: null,
        usage: null, // counts of the selected pool
        usageFailed: false,
        confirmDeactivate: false,
        message: '',
        busy: false,
        createName: '',
        renameName: '',
        validFrom: '',
        validTo: '',
        search: '',
    });
    const poolsView = reactive({ status: 'idle', revision: null, pools: { author: [], staff: [] }, pendingRemoval: null, failedWrite: null, message: '' });
    const store = createPoolsStore(client, (next) => Object.assign(poolsView, next));
    const accounts = createAccountsAdmin({ client: options.userClient || client, reactive });

    const membersOf = (id) => {
        const p = poolsView.pools.staff.find((x) => x.id === id);
        return p ? p.memberUserIds : [];
    };
    const selected = () => state.meta.find((p) => p.id === state.selectedId) || null;
    const nameOf = (userId) => {
        const a = accounts.state.accounts.find((x) => Number(x.id) === Number(userId));
        return a ? a.username : TEXT.unknownMember;
    };

    function rows() {
        return state.meta.map((p) => ({ ...p, memberUserIds: membersOf(p.id), status: poolStatus(p) }));
    }

    function members() {
        return membersOf(state.selectedId).map((id) => ({ id, name: nameOf(id) }));
    }

    function results() {
        return searchAccounts(accounts.state.accounts, state.search, membersOf(state.selectedId));
    }

    const searchAvailable = () => accounts.state.loaded;

    function fillForm() {
        const p = selected();
        state.renameName = p ? p.name : '';
        state.validFrom = p ? toLocalInput(p.valid_from) : '';
        state.validTo = p ? toLocalInput(p.valid_to) : '';
    }

    async function loadUsage() {
        const id = state.selectedId;
        state.usage = null;
        state.usageFailed = false;
        if (!id) return null;
        const res = await client.request(ADMIN_BASE + '/pools/' + KIND + '/' + encodeURIComponent(id) + '/usage');
        if (disposed || id !== state.selectedId) return null;
        if (res.outcome !== 'ok' || !res.data) {
            state.usageFailed = true;
            return null;
        }
        state.usage = {
            openDictations: Number(res.data.open_dictations) || 0,
            members: Number(res.data.members) || 0,
            links: Number(res.data.links) || 0,
        };
        return state.usage;
    }

    async function select(id) {
        const next = id == null ? null : String(id);
        if (next !== null && !state.meta.some((p) => p.id === next)) return false;
        state.selectedId = next;
        state.confirmDeactivate = false;
        state.search = '';
        store.cancelRemoval();
        fillForm();
        await loadUsage();
        return true;
    }

    async function load(opts) {
        // A reload after a write keeps the page on screen instead of showing the loading state.
        if (!(opts && opts.silent && state.status === 'ready')) state.status = 'loading';
        state.message = '';
        const res = await client.request(ADMIN_BASE + '/pools');
        if (disposed) return res;
        if (res.outcome !== 'ok') {
            state.meta = [];
            state.selectedId = null;
            state.status = res.outcome === 'permission' ? 'denied' : 'error';
            state.message = res.outcome === 'permission' ? TEXT.denied : TEXT.failed;
            return res;
        }
        const list = res.data && Array.isArray(res.data.staff_pools) ? res.data.staff_pools : [];
        state.meta = list.map((p) => ({
            id: String(p.id), name: String(p.name || ''), active: p.active !== false, routable: p.routable !== false,
            valid_from: p.valid_from || null, valid_to: p.valid_to || null,
        }));
        const loaded = await store.load();
        if (disposed) return res;
        if (poolsView.status === 'denied') {
            state.meta = [];
            state.status = 'denied';
            state.message = TEXT.denied;
            return loaded;
        }
        if (poolsView.status !== 'ready') {
            state.status = 'error';
            state.message = TEXT.failed;
            return loaded;
        }
        if (!accounts.state.loaded) await accounts.load();
        if (state.selectedId && !state.meta.some((p) => p.id === state.selectedId)) state.selectedId = null;
        state.status = 'ready';
        fillForm();
        return res;
    }

    function refuse(message) {
        state.message = message;
        return { ok: false, message };
    }

    // One pool-admin write; reloads server state on success, keeps the form on failure.
    async function write(path, method, body) {
        if (state.busy || state.status !== 'ready') return refuse(state.message);
        state.busy = true;
        state.message = '';
        try {
            const res = await client.request(ADMIN_BASE + path, { method, body });
            if (disposed) return { ok: false };
            if (res.outcome !== 'ok') return refuse(errorText(res));
            const keep = state.selectedId;
            await load({ silent: true });
            if (keep && state.meta.some((p) => p.id === keep)) {
                state.selectedId = keep;
                fillForm();
                await loadUsage();
            }
            return { ok: true, data: res.data };
        } finally {
            state.busy = false;
        }
    }

    const poolPath = (suffix) => '/pools/' + KIND + '/' + encodeURIComponent(state.selectedId) + suffix;

    async function create() {
        const name = state.createName.trim();
        if (!name) return refuse(TEXT.nameRequired);
        const done = await write('/pools', 'POST', { kind: KIND, name });
        if (done.ok) {
            state.createName = '';
            if (done.data && done.data.id) await select(done.data.id);
        }
        return done;
    }

    async function rename() {
        const p = selected();
        const name = state.renameName.trim();
        if (!p) return refuse('');
        if (!name) return refuse(TEXT.nameRequired);
        if (name === p.name) return { ok: true, unchanged: true };
        return write(poolPath('/name'), 'PUT', { name });
    }

    async function saveValidity() {
        if (!selected()) return refuse('');
        const from = fromLocalInput(state.validFrom);
        const to = fromLocalInput(state.validTo);
        if (from === undefined || to === undefined) return refuse(ERROR_TEXT.invalid_validity);
        if (from && to && new Date(from) >= new Date(to)) return refuse(TEXT.validityOrder);
        return write(poolPath('/validity'), 'PUT', { valid_from: from, valid_to: to });
    }

    /** First step: load the usage and ask for confirmation. Nothing is changed. */
    async function requestDeactivate() {
        const p = selected();
        if (!p || !p.active) return false;
        const usage = await loadUsage();
        if (!usage) {
            refuse(TEXT.usageUnavailable);
            return false;
        }
        state.confirmDeactivate = true;
        return true;
    }

    function cancelDeactivate() {
        state.confirmDeactivate = false;
    }

    async function confirmDeactivate() {
        if (!state.confirmDeactivate) return refuse('');
        state.confirmDeactivate = false;
        return write(poolPath('/active'), 'PUT', { active: false });
    }

    async function reactivate() {
        const p = selected();
        if (!p || p.active) return refuse('');
        return write(poolPath('/active'), 'PUT', { active: true });
    }

    function syncFromStore() {
        if (poolsView.status === 'stale') state.message = TEXT.stale;
        else if (poolsView.status === 'denied') state.message = TEXT.denied;
        else if (poolsView.status === 'error') state.message = poolsView.message || TEXT.failed;
        else state.message = '';
    }

    async function addMember(userId) {
        if (!state.selectedId || !searchAvailable()) return null;
        if (!results().some((a) => Number(a.id) === Number(userId))) return null;
        const res = await store.addMember(KIND, state.selectedId, Number(userId));
        state.search = '';
        syncFromStore();
        await loadUsage();
        return res;
    }

    function requestRemoval(userId) {
        return store.requestRemoval(KIND, state.selectedId, userId);
    }

    async function confirmRemoval() {
        const res = await store.confirmRemoval();
        syncFromStore();
        await loadUsage();
        return res;
    }

    function cancelRemoval() {
        store.cancelRemoval();
    }

    /** Esc cancels an open confirmation; returns true when handled. */
    function onKey(event) {
        if (!event || event.key !== 'Escape') return false;
        if (state.confirmDeactivate) { cancelDeactivate(); return true; }
        if (poolsView.pendingRemoval) { cancelRemoval(); return true; }
        return false;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        state.meta = [];
        state.usage = null;
        state.selectedId = null;
        state.confirmDeactivate = false;
        store.cancelRemoval();
    }

    return {
        state, poolsView, store, accounts, text: TEXT,
        load: () => load(), select, create, rename, saveValidity, requestDeactivate, cancelDeactivate, confirmDeactivate, reactivate,
        addMember, requestRemoval, confirmRemoval, cancelRemoval, onKey, dispose,
        rows, members, results, selected, searchAvailable,
        pendingRemovalName: () => (poolsView.pendingRemoval ? nameOf(poolsView.pendingRemoval.userId) : ''),
        retryWrite: () => store.retry(),
        errorStyle: styleOf('error'),
        warningStyle: styleOf('warning'),
        badgeStyle: (pool) => styleOf(pool.status.surface),
    };
}

export const ADMIN_POOLS_TEMPLATE = `
<section class="clinic-admin-pools-view space-y-4" data-testid="clinic-admin-pools-view" aria-labelledby="cap-title" @keydown="c.onKey($event)">
  <header>
    <h1 id="cap-title" class="text-lg font-semibold text-[var(--text-primary)]">{{ c.text.title }}</h1>
    <p class="mt-1 text-sm text-[var(--text-muted)]">Schreibkräfte-Pools anlegen, Zeiträume festlegen und Mitglieder verwalten.</p>
  </header>
  <p v-if="c.state.status === 'loading' || c.state.status === 'idle'" role="status" aria-live="polite" data-testid="loading-state" class="text-sm text-[var(--text-muted)]">{{ c.text.loading }}</p>
  <p v-else-if="c.state.status === 'denied'" role="alert" class="rounded-lg border p-3 text-sm" :style="c.errorStyle" data-testid="denied-state">{{ c.text.denied }}</p>
  <div v-else-if="c.state.status === 'error'" role="alert" class="rounded-lg border p-3 text-sm" :style="c.errorStyle" data-testid="load-error">
    {{ c.state.message }}
    <button type="button" class="btn btn--sm btn--ghost ml-2" @click="c.load()">{{ c.text.retry }}</button>
  </div>
  <template v-else>
    <p class="sr-only">{{ c.text.keys }}</p>
    <p v-if="c.state.message" role="alert" class="rounded-lg border p-3 text-sm" :style="c.warningStyle" data-testid="message">
      {{ c.state.message }}
      <button type="button" class="btn btn--sm btn--ghost ml-2" @click="c.load()">{{ c.text.reload }}</button>
    </p>
    <div class="grid gap-4 lg:grid-cols-2">
      <div class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
        <form class="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end" @submit.prevent="c.create()">
          <div class="grid min-w-0 gap-1">
            <label for="cap-create-name" class="text-sm font-medium text-[var(--text-secondary)]">{{ c.text.createName }}</label>
            <input id="cap-create-name" type="text" maxlength="100" autocomplete="off" v-model="c.state.createName" class="field">
          </div>
          <button type="submit" class="btn btn--primary" :disabled="c.state.busy">{{ c.text.create }}</button>
        </form>
        <p v-if="!c.rows().length" class="mt-3 rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]" data-testid="empty-state">{{ c.text.empty }}</p>
        <ul v-else class="mt-3 grid gap-2" :aria-label="c.text.listLabel" data-testid="pool-list">
          <li v-for="p in c.rows()" :key="p.id">
            <button type="button" class="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-left text-sm hover:bg-[var(--bg-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)] aria-[current=true]:border-[var(--border-focus)] aria-[current=true]:bg-[var(--bg-tertiary)]"
                    :aria-current="c.state.selectedId === p.id ? 'true' : 'false'" :data-id="p.id" @click="c.select(p.id)">
              <span class="font-medium text-[var(--text-primary)]">{{ p.name }}</span>
              <span class="inline-flex items-center rounded-full border px-2 text-xs font-semibold leading-5" :style="c.badgeStyle(p)">{{ p.status.label }}</span>
              <span class="ml-auto text-xs text-[var(--text-muted)]">{{ c.text.members }}: {{ p.memberUserIds.length }}</span>
              <span v-if="!p.memberUserIds.length" class="w-full text-xs text-[var(--text-muted)]">{{ c.text.noMembers }}</span>
            </button>
          </li>
        </ul>
      </div>
      <aside aria-labelledby="cap-detail-title" data-testid="detail-panel" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
        <p v-if="!c.selected()" id="cap-detail-title" class="text-sm text-[var(--text-muted)]">{{ c.text.noSelection }}</p>
        <div v-else :key="c.state.selectedId" class="space-y-4">
          <h2 id="cap-detail-title" class="text-base font-semibold text-[var(--text-primary)]">{{ c.text.detail }}: {{ c.selected().name }}</h2>
          <form class="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end" @submit.prevent="c.rename()">
            <div class="grid min-w-0 gap-1">
              <label for="cap-rename" class="text-sm font-medium text-[var(--text-secondary)]">{{ c.text.nameLabel }}</label>
              <input id="cap-rename" type="text" maxlength="100" autocomplete="off" v-model="c.state.renameName" class="field">
            </div>
            <button type="submit" class="btn btn--sm btn--ghost" :disabled="c.state.busy">{{ c.text.rename }}</button>
          </form>
          <fieldset class="rounded-lg border border-[var(--border-primary)] p-3">
            <legend class="px-1 text-sm font-medium text-[var(--text-secondary)]">{{ c.text.validity }}</legend>
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="grid gap-1">
                <label for="cap-valid-from" class="text-sm text-[var(--text-secondary)]">{{ c.text.validFrom }}</label>
                <input id="cap-valid-from" type="datetime-local" v-model="c.state.validFrom" class="field">
              </div>
              <div class="grid gap-1">
                <label for="cap-valid-to" class="text-sm text-[var(--text-secondary)]">{{ c.text.validTo }}</label>
                <input id="cap-valid-to" type="datetime-local" v-model="c.state.validTo" class="field">
              </div>
            </div>
            <button type="button" class="btn btn--sm btn--ghost mt-3" :disabled="c.state.busy" @click="c.saveValidity()">{{ c.text.saveValidity }}</button>
          </fieldset>
          <section aria-labelledby="cap-usage-title" data-testid="usage">
            <h3 id="cap-usage-title" class="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ c.text.usage }}</h3>
            <p v-if="c.state.usageFailed" class="text-sm text-[var(--text-muted)]">{{ c.text.usageUnavailable }}</p>
            <ul v-else-if="c.state.usage" class="grid grid-cols-1 gap-2 sm:grid-cols-3 text-sm text-[var(--text-secondary)]">
              <li class="rounded-lg bg-[var(--bg-tertiary)] p-2">{{ c.text.usageOpen }}: <strong class="text-[var(--text-primary)]">{{ c.state.usage.openDictations }}</strong></li>
              <li class="rounded-lg bg-[var(--bg-tertiary)] p-2">{{ c.text.usageMembers }}: <strong class="text-[var(--text-primary)]">{{ c.state.usage.members }}</strong></li>
              <li class="rounded-lg bg-[var(--bg-tertiary)] p-2">{{ c.text.usageLinks }}: <strong class="text-[var(--text-primary)]">{{ c.state.usage.links }}</strong></li>
            </ul>
          </section>
          <div>
            <button v-if="c.selected().active" type="button" class="btn btn--sm btn--ghost" :disabled="c.state.busy" data-testid="deactivate" @click="c.requestDeactivate()">{{ c.text.deactivate }}</button>
            <button v-else type="button" class="btn btn--sm btn--ghost" :disabled="c.state.busy" data-testid="reactivate" @click="c.reactivate()">{{ c.text.reactivate }}</button>
          </div>
          <div v-if="c.state.confirmDeactivate" role="alertdialog" aria-modal="false" aria-labelledby="cap-deact-title" class="rounded-lg border p-3 text-sm" :style="c.warningStyle" data-testid="deactivate-confirm">
            <p id="cap-deact-title" class="font-medium">{{ c.text.confirmDeactivate }}</p>
            <p>{{ c.text.usageOpen }}: {{ c.state.usage ? c.state.usage.openDictations : 0 }}</p>
            <p>{{ c.text.deactivateNote }}</p>
            <div class="mt-2 flex flex-wrap gap-2">
              <button type="button" class="btn btn--sm btn--danger" @click="c.confirmDeactivate()">{{ c.text.deactivateYes }}</button>
              <button type="button" class="btn btn--sm btn--ghost" @click="c.cancelDeactivate()">{{ c.text.cancel }}</button>
            </div>
          </div>
          <section aria-labelledby="cap-members-title">
            <h3 id="cap-members-title" class="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ c.text.members }}</h3>
            <ul :aria-label="c.text.members" data-testid="member-list" class="divide-y divide-[var(--border-primary)] rounded-lg border border-[var(--border-primary)]">
              <li v-if="!c.members().length" class="px-3 py-2 text-sm text-[var(--text-muted)]">{{ c.text.noMembers }}</li>
              <li v-for="m in c.members()" :key="m.id" class="flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-[var(--text-primary)]">
                <span>{{ m.name }}</span>
                <button type="button" class="btn btn--sm btn--quiet btn--icon"
                        :aria-label="c.text.removeMember + ': ' + m.name" :title="c.text.removeMember" @click="c.requestRemoval(m.id)">&times;</button>
              </li>
            </ul>
            <div v-if="c.poolsView.pendingRemoval" role="alertdialog" aria-modal="false" aria-labelledby="cap-rm-title" class="mt-2 rounded-lg border p-3 text-sm" :style="c.warningStyle" data-testid="remove-confirm">
              <p id="cap-rm-title" class="font-medium">{{ c.text.confirmRemove }} {{ c.pendingRemovalName() }}</p>
              <div class="mt-2 flex flex-wrap gap-2">
                <button type="button" class="btn btn--sm btn--danger" @click="c.confirmRemoval()">{{ c.text.confirmRemoveYes }}</button>
                <button type="button" class="btn btn--sm btn--ghost" @click="c.cancelRemoval()">{{ c.text.cancel }}</button>
              </div>
            </div>
            <p v-if="!c.searchAvailable()" role="status" class="mt-2 text-sm text-[var(--text-muted)]">{{ c.text.searchUnavailable }}</p>
            <div v-else class="mt-3 grid gap-1">
              <label for="cap-member-search" class="text-sm font-medium text-[var(--text-secondary)]">{{ c.text.memberSearch }}</label>
              <input id="cap-member-search" type="search" autocomplete="off" v-model="c.state.search" class="field">
              <ul v-if="c.state.search.trim()" :aria-label="c.text.memberSearch" data-testid="search-results" class="mt-1 divide-y divide-[var(--border-primary)] rounded-lg border border-[var(--border-primary)]">
                <li v-if="!c.results().length" class="px-3 py-2 text-sm text-[var(--text-muted)]">{{ c.text.searchNone }}</li>
                <li v-for="a in c.results()" :key="a.id" class="flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-[var(--text-primary)]">
                  <span>{{ a.username }}</span>
                  <button type="button" class="btn btn--sm btn--ghost"
                          :aria-label="c.text.addMember + ': ' + a.username" @click="c.addMember(a.id)">{{ c.text.addMember }}</button>
                </li>
              </ul>
            </div>
          </section>
        </div>
      </aside>
    </div>
  </template>
</section>`;

export const AdminPoolsView = {
    name: 'ClinicAdminPoolsView',
    props: {
        client: { type: Object, required: true },
        userClient: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: ADMIN_POOLS_TEMPLATE,
    setup(props) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the admin pools view');
        const c = createAdminPoolsController({ client: props.client, userClient: props.userClient || undefined, reactive: V.reactive });
        if (typeof V.onMounted === 'function') V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c };
    },
};

export function registerAdminPoolsView(app) {
    app.component('clinic-admin-pools-view', AdminPoolsView);
    return app;
}
