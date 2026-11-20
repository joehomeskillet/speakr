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
 *   AdminPoolsView                 Vue options object, props client, currentUser, userClient
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
    lead: 'Leitung',
    roleUnavailable: 'Rolleninformation nicht verfügbar.',
    roleFailed: 'Die Leitungsrolle konnte nicht gespeichert werden. Aktuellen Stand laden und erneut versuchen.',
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
    eyebrow: 'Verwaltung',
    subline: 'Zuständige Gruppen und ihre Mitglieder verwalten. Die Zuweisung eines Diktats bezieht sich auf einen Schreibkräfte-Pool.',
    addPool: 'Pool hinzufügen',
    listTitle: 'Pools und Mitglieder',
    search: 'Suche',
    searchPlaceholder: 'Pool oder Mitglied …',
    resetFilter: 'Filter zurücksetzen',
    filterEmpty: 'Keine Einträge für diese Auswahl. Filter zurücksetzen oder Suche anpassen.',
    colPool: 'Schreibkräfte-Pool',
    colStatus: 'Status',
    colAction: 'Aktion',
    groupNote: 'Verantwortliche Gruppe',
    noMembersShort: 'Keine Mitglieder',
    edit: 'Schreibkräfte-Pool bearbeiten',
    statPools: 'Schreibkräfte-Pools',
    statPoolsNote: 'Gruppenzuständigkeit',
    statActive: 'Aktive Pools',
    statActiveNote: 'Nehmen neue Diktate an',
    statMembers: 'Schreibkräfte',
    statMembersNote: 'Mitglieder in Pools',
    statEmpty: 'Ohne Mitglieder',
    statEmptyNote: 'Nicht besetzt',
    howTitle: 'Zugriff und Bearbeitung',
    steps: Object.freeze([
        Object.freeze({ title: 'Diktat dem Pool zuweisen', text: 'Die Gruppe trägt die Zuständigkeit und erhält den erlaubten Zugriff.' }),
        Object.freeze({ title: 'Schreibkraft übernimmt', text: 'Die einzelne Bearbeitung wird zusätzlich angezeigt; der Pool bleibt sichtbar.' }),
        Object.freeze({ title: 'Zurückgeben oder umverteilen', text: 'Poolwechsel werden protokolliert. Die diktierende Person wird nicht geändert.' }),
    ]),
    delimitTitle: 'Abgrenzung zu Autorenpools',
    delimitText: 'Autorenpools organisieren diktierende Personen und ihre Ressourcen. Schreibkräfte-Pools organisieren die Zuständigkeit für die Bearbeitung.',
    delimitLink: 'Diktierende und Autorenpools',
    delimitNote: 'Berechtigungen werden serverseitig geprüft. Eine persönliche Arbeitsübernahme bleibt ein separates Merkmal am Diktat.',
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
        filter: '',
        createOpen: false,
    });
    const poolsView = reactive({ status: 'idle', revision: null, pools: { author: [], staff: [] }, pendingRemoval: null, failedWrite: null, message: '' });
    const store = createPoolsStore(client, (next) => Object.assign(poolsView, next));
    const accounts = createAccountsAdmin({ client: options.userClient || client, reactive });
    const canManageRoles = () => options.currentUser?.is_admin === true;
    // Expected v8-133 listing contract: members [{user_id, role: 'lead' | 'member'}].
    // An absent role is unknown, never an implicit demotion.
    const memberRole = (userId) => selected()?.members.find((m) => Number(m.user_id) === Number(userId))?.role;

    async function setLead(userId, enabled) {
        if (!canManageRoles() || disposed || state.busy || state.status !== 'ready'
            || poolsView.status !== 'ready' || !membersOf(state.selectedId).includes(userId)
            || !['lead', 'member'].includes(memberRole(userId))) return { ok: false };
        state.busy = true;
        state.message = '';
        const poolId = state.selectedId;
        try {
            const res = await client.request(ADMIN_BASE + '/pools/staff/' + encodeURIComponent(poolId)
                + '/members/' + encodeURIComponent(userId) + '/role', {
                method: 'PUT', body: { role: enabled ? 'lead' : 'member' },
            });
            if (disposed) return { ok: false };
            if (res.outcome !== 'ok') {
                state.message = res.outcome === 'permission' ? TEXT.denied
                    : res.outcome === 'conflict' ? TEXT.stale : TEXT.roleFailed;
                return { ok: false };
            }
            await load({ silent: true });
            return { ok: state.status === 'ready' };
        } finally {
            state.busy = false;
        }
    }

    async function changeLead(userId, event) {
        const input = event.target;
        try {
            await setLead(userId, input.checked);
        } finally {
            input.checked = memberRole(userId) === 'lead';
        }
    }

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

    const memberNames = (poolId) => membersOf(poolId).map((id) => nameOf(id));

    /** Rows whose pool name or member name contains the filter text (page-local, case-insensitive). */
    function visibleRows() {
        const needle = state.filter.trim().toLowerCase();
        const all = rows();
        if (!needle) return all;
        return all.filter((p) => p.name.toLowerCase().includes(needle) || memberNames(p.id).some((n) => String(n).toLowerCase().includes(needle)));
    }

    /** Counts for the KPI cards: pools, active pools, distinct members, pools without members. */
    function stats() {
        const all = rows();
        const people = new Set();
        all.forEach((p) => p.memberUserIds.forEach((id) => people.add(Number(id))));
        return { active: all.filter((p) => p.status.key === 'active').length, members: people.size, empty: all.filter((p) => !p.memberUserIds.length).length };
    }

    const countText = () => visibleRows().length + ' von ' + state.meta.length + ' Schreibkräfte-Pools';

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
            members: Array.isArray(p.members) ? p.members : [],
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
            state.createOpen = false;
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
        if (state.busy || !state.selectedId || !searchAvailable()) return null;
        if (!results().some((a) => Number(a.id) === Number(userId))) return null;
        const res = await store.addMember(KIND, state.selectedId, Number(userId));
        state.search = '';
        syncFromStore();
        if (res?.outcome === 'ok') await load({ silent: true });
        await loadUsage();
        return res;
    }

    function requestRemoval(userId) {
        if (state.busy) return false;
        return store.requestRemoval(KIND, state.selectedId, userId);
    }

    async function confirmRemoval() {
        if (state.busy) return null;
        const res = await store.confirmRemoval();
        syncFromStore();
        if (res?.outcome === 'ok') await load({ silent: true });
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
        canManageRoles, memberRole, setLead, changeLead,
        rows, members, results, selected, searchAvailable, memberNames, visibleRows, stats, countText,
        pendingRemovalName: () => (poolsView.pendingRemoval ? nameOf(poolsView.pendingRemoval.userId) : ''),
        retryWrite: () => store.retry(),
        errorStyle: styleOf('error'),
        warningStyle: styleOf('warning'),
        badgeStyle: (pool) => styleOf(pool.status.surface),
    };
}

// Line icons of the V8 prototype (03_Prototyp/assets/ui.css icon set); decorative, the buttons carry the labels.
const ICON = (path) => '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="' + path + '"/></svg>';
const ICONS = Object.freeze({
    plus: ICON('M12 4v16M4 12h16'),
    edit: ICON('m15 4 5 5M4 20l4-1L21 6l-4-4L4 15z'),
    restart: ICON('M3 11a9 9 0 1 1 2 7M3 4v7h7'),
    arrow: ICON('M4 12h16m-6-6 6 6-6 6'),
});

export const ADMIN_POOLS_TEMPLATE = `
<section class="clinic-admin-pools-view v8-page v8-admin-page" data-testid="clinic-admin-pools-view" aria-labelledby="cap-title" @keydown="c.onKey($event)">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ c.text.eyebrow }}</div>
      <h1 id="cap-title">{{ c.text.title }}</h1>
      <p class="subline">{{ c.text.subline }}</p>
    </div>
    <div v-if="c.state.status === 'ready'" class="head-actions">
      <button type="button" class="primary" data-testid="create-open" :aria-expanded="c.state.createOpen ? 'true' : 'false'" @click="c.state.createOpen = !c.state.createOpen">${ICONS.plus}<span>{{ c.text.addPool }}</span></button>
    </div>
  </div>
  <p v-if="c.state.status === 'loading' || c.state.status === 'idle'" role="status" aria-live="polite" class="hint" data-testid="loading-state">{{ c.text.loading }}</p>
  <p v-else-if="c.state.status === 'denied'" role="alert" class="note burgundy" data-testid="denied-state">{{ c.text.denied }}</p>
  <div v-else-if="c.state.status === 'error'" role="alert" class="note burgundy" data-testid="load-error">
    {{ c.state.message }}
    <button type="button" class="small" @click="c.load()">{{ c.text.retry }}</button>
  </div>
  <template v-else>
    <p class="sr-only">{{ c.text.keys }}</p>
    <div v-if="c.state.message" role="alert" class="note warning" data-testid="message">
      {{ c.state.message }}
      <button type="button" class="small" @click="c.load()">{{ c.text.reload }}</button>
    </div>
    <div class="stats" data-testid="pool-stats">
      <div class="stat"><span>{{ c.text.statPools }}</span><strong>{{ c.rows().length }}</strong><small>{{ c.text.statPoolsNote }}</small></div>
      <div class="stat"><span>{{ c.text.statActive }}</span><strong>{{ c.stats().active }}</strong><small>{{ c.text.statActiveNote }}</small></div>
      <div class="stat"><span>{{ c.text.statMembers }}</span><strong>{{ c.stats().members }}</strong><small>{{ c.text.statMembersNote }}</small></div>
      <div class="stat"><span>{{ c.text.statEmpty }}</span><strong>{{ c.stats().empty }}</strong><small>{{ c.text.statEmptyNote }}</small></div>
    </div>
    <section v-if="c.state.createOpen" class="panel" style="margin-bottom: 20px" aria-labelledby="cap-create-title" data-testid="create-panel">
      <h2 id="cap-create-title">{{ c.text.create }}</h2>
      <form @submit.prevent="c.create()">
        <div class="field">
          <label for="cap-create-name">{{ c.text.createName }}</label>
          <input id="cap-create-name" type="text" maxlength="100" autocomplete="off" v-model="c.state.createName">
        </div>
        <div class="actions">
          <button type="button" @click="c.state.createOpen = false">{{ c.text.cancel }}</button>
          <button type="submit" class="primary" :disabled="c.state.busy">{{ c.text.create }}</button>
        </div>
      </form>
    </section>
    <section class="panel" aria-labelledby="cap-list-title">
      <h2 id="cap-list-title">{{ c.text.listTitle }}</h2>
      <div class="filterbar">
        <div class="field search">
          <label for="cap-filter">{{ c.text.search }}</label>
          <input id="cap-filter" type="search" autocomplete="off" :placeholder="c.text.searchPlaceholder" v-model="c.state.filter" data-testid="pool-filter">
        </div>
        <span class="tool-wrap"><button type="button" class="icon-button" :aria-label="c.text.resetFilter" :title="c.text.resetFilter" data-testid="pool-filter-reset" @click="c.state.filter = ''">${ICONS.restart}</button></span>
      </div>
      <p class="result-count" aria-live="polite" data-testid="pool-count">{{ c.countText() }}</p>
      <p v-if="!c.rows().length" class="empty" data-testid="empty-state">{{ c.text.empty }}</p>
      <p v-else-if="!c.visibleRows().length" class="empty" data-testid="filter-empty">{{ c.text.filterEmpty }}</p>
      <div v-else class="table-wrap">
        <table :aria-label="c.text.listLabel" data-testid="pool-list">
          <thead><tr>
            <th scope="col">{{ c.text.colPool }}</th><th scope="col">{{ c.text.members }}</th>
            <th scope="col">{{ c.text.colStatus }}</th><th scope="col">{{ c.text.colAction }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="p in c.visibleRows()" :key="p.id" :data-id="p.id" :class="{ 'is-selected': c.state.selectedId === p.id }">
              <td><strong>{{ p.name }}</strong><small>{{ c.text.groupNote }}</small></td>
              <td class="wrap">
                <span v-if="p.memberUserIds.length">{{ c.memberNames(p.id).join(', ') }}</span>
                <span v-else class="muted">{{ c.text.noMembersShort }}</span>
              </td>
              <td><span class="tag" :class="p.status.key === 'active' ? 'neutral' : 'wait'">{{ p.status.label }}</span></td>
              <td>
                <span class="tool-wrap"><button type="button" class="icon-button" :aria-label="c.text.edit + ': ' + p.name" :title="c.text.edit"
                        :aria-current="c.state.selectedId === p.id ? 'true' : 'false'" :data-id="p.id" @click="c.select(p.id)">${ICONS.edit}</button></span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <p v-if="!c.selected()" id="cap-detail-title" class="hint" data-testid="detail-panel">{{ c.text.noSelection }}</p>
    <aside v-else class="grid equal" aria-labelledby="cap-detail-title" data-testid="detail-panel">
      <section class="panel" :key="c.state.selectedId">
        <h2 id="cap-detail-title">{{ c.text.detail }}: {{ c.selected().name }}</h2>
        <form class="field-grid" @submit.prevent="c.rename()">
          <div class="field full">
            <label for="cap-rename">{{ c.text.nameLabel }}</label>
            <input id="cap-rename" type="text" maxlength="100" autocomplete="off" v-model="c.state.renameName">
          </div>
          <div class="actions full"><button type="submit" :disabled="c.state.busy">{{ c.text.rename }}</button></div>
        </form>
        <fieldset style="border: 0; padding: 0; margin: 18px 0 0">
          <legend><h3>{{ c.text.validity }}</h3></legend>
          <div class="field-grid">
            <div class="field">
              <label for="cap-valid-from">{{ c.text.validFrom }}</label>
              <input id="cap-valid-from" type="datetime-local" v-model="c.state.validFrom">
            </div>
            <div class="field">
              <label for="cap-valid-to">{{ c.text.validTo }}</label>
              <input id="cap-valid-to" type="datetime-local" v-model="c.state.validTo">
            </div>
          </div>
          <div class="actions"><button type="button" :disabled="c.state.busy" @click="c.saveValidity()">{{ c.text.saveValidity }}</button></div>
        </fieldset>
        <section style="margin-top: 18px" aria-labelledby="cap-usage-title" data-testid="usage">
          <h3 id="cap-usage-title">{{ c.text.usage }}</h3>
          <p v-if="c.state.usageFailed" class="hint">{{ c.text.usageUnavailable }}</p>
          <dl v-else-if="c.state.usage" class="keyvalues">
            <div><dt>{{ c.text.usageOpen }}</dt><dd>{{ c.state.usage.openDictations }}</dd></div>
            <div><dt>{{ c.text.usageMembers }}</dt><dd>{{ c.state.usage.members }}</dd></div>
            <div><dt>{{ c.text.usageLinks }}</dt><dd>{{ c.state.usage.links }}</dd></div>
          </dl>
        </section>
        <div class="actions" style="margin-top: 14px">
          <button v-if="c.selected().active" type="button" class="danger" :disabled="c.state.busy" data-testid="deactivate" @click="c.requestDeactivate()">{{ c.text.deactivate }}</button>
          <button v-else type="button" :disabled="c.state.busy" data-testid="reactivate" @click="c.reactivate()">{{ c.text.reactivate }}</button>
        </div>
        <div v-if="c.state.confirmDeactivate" role="alertdialog" aria-modal="false" aria-labelledby="cap-deact-title" class="note warning" data-testid="deactivate-confirm">
          <p id="cap-deact-title"><strong>{{ c.text.confirmDeactivate }}</strong></p>
          <p>{{ c.text.usageOpen }}: {{ c.state.usage ? c.state.usage.openDictations : 0 }}</p>
          <p>{{ c.text.deactivateNote }}</p>
          <div class="actions">
            <button type="button" class="small" @click="c.cancelDeactivate()">{{ c.text.cancel }}</button>
            <button type="button" class="small primary" @click="c.confirmDeactivate()">{{ c.text.deactivateYes }}</button>
          </div>
        </div>
      </section>
      <section class="panel" aria-labelledby="cap-members-title">
        <h2 id="cap-members-title">{{ c.text.members }}</h2>
        <ul :aria-label="c.text.members" data-testid="member-list" style="list-style: none; margin: 0; padding: 0">
          <li v-if="!c.members().length" class="hint">{{ c.text.noMembers }}</li>
          <li v-for="m in c.members()" :key="m.id" style="display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 0">
            <span>{{ m.name }}</span>
            <label v-if="c.canManageRoles()" class="inline-label">
              <input type="checkbox" :checked="c.memberRole(m.id) === 'lead'"
                :disabled="c.state.busy || c.poolsView.status !== 'ready' || !['lead', 'member'].includes(c.memberRole(m.id))"
                :aria-label="c.text.lead + ': ' + m.name"
                @change="c.changeLead(m.id, $event)">
              {{ c.text.lead }}
              <small v-if="!['lead', 'member'].includes(c.memberRole(m.id))">{{ c.text.roleUnavailable }}</small>
            </label>
            <span v-else-if="c.memberRole(m.id) === 'lead'" class="tag neutral">{{ c.text.lead }}</span>
            <button type="button" class="small" :disabled="c.state.busy" :aria-label="c.text.removeMember + ': ' + m.name" :title="c.text.removeMember" @click="c.requestRemoval(m.id)">&times;</button>
          </li>
        </ul>
        <div v-if="c.poolsView.pendingRemoval" role="alertdialog" aria-modal="false" aria-labelledby="cap-rm-title" class="note warning" data-testid="remove-confirm">
          <p id="cap-rm-title"><strong>{{ c.text.confirmRemove }} {{ c.pendingRemovalName() }}</strong></p>
          <div class="actions">
            <button type="button" class="small" @click="c.cancelRemoval()">{{ c.text.cancel }}</button>
            <button type="button" class="small primary" @click="c.confirmRemoval()">{{ c.text.confirmRemoveYes }}</button>
          </div>
        </div>
        <p v-if="!c.searchAvailable()" role="status" class="hint">{{ c.text.searchUnavailable }}</p>
        <div v-else class="field" style="margin-top: 14px">
          <label for="cap-member-search">{{ c.text.memberSearch }}</label>
          <input id="cap-member-search" type="search" autocomplete="off" v-model="c.state.search">
          <ul v-if="c.state.search.trim()" :aria-label="c.text.memberSearch" data-testid="search-results" style="list-style: none; margin: 6px 0 0; padding: 0">
            <li v-if="!c.results().length" class="hint">{{ c.text.searchNone }}</li>
            <li v-for="a in c.results()" :key="a.id" style="display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 0">
              <span>{{ a.username }}</span>
              <button type="button" class="small" :disabled="c.state.busy" :aria-label="c.text.addMember + ': ' + a.username" @click="c.addMember(a.id)">{{ c.text.addMember }}</button>
            </li>
          </ul>
        </div>
      </section>
    </aside>
    <div class="grid equal" style="margin-top: 20px">
      <section class="panel" aria-labelledby="cap-how-title">
        <h2 id="cap-how-title">{{ c.text.howTitle }}</h2>
        <div v-for="(s, i) in c.text.steps" :key="i" class="step"><span>{{ i + 1 }}</span><div><strong>{{ s.title }}</strong><p>{{ s.text }}</p></div></div>
      </section>
      <section class="panel" aria-labelledby="cap-delimit-title">
        <h2 id="cap-delimit-title">{{ c.text.delimitTitle }}</h2>
        <p class="hint">{{ c.text.delimitText }}</p>
        <a class="button" href="/#/verwaltung/diktierende">${ICONS.arrow}<span>{{ c.text.delimitLink }}</span></a>
        <div class="note">{{ c.text.delimitNote }}</div>
      </section>
    </div>
  </template>
</section>`;

export const AdminPoolsView = {
    name: 'ClinicAdminPoolsView',
    props: {
        client: { type: Object, required: true },
        userClient: { type: Object, default: null },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: ADMIN_POOLS_TEMPLATE,
    setup(props) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the admin pools view');
        const c = createAdminPoolsController({ client: props.client, userClient: props.userClient || undefined, currentUser: props.currentUser, reactive: V.reactive });
        if (typeof V.onMounted === 'function') V.onMounted(() => c.load());
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c };
    },
};

export function registerAdminPoolsView(app) {
    app.component('clinic-admin-pools-view', AdminPoolsView);
    return app;
}
