/**
 * Clinic users and roles view (v8-53, V8 page P12): one Vue view composed from the
 * existing account administration (accounts-ui), pool management (pools-ui) and
 * grant (grants-ui) modules. PAR-ADM-001, PAR-ADM-004, PAR-ADM-005, PAR-ADM-008.
 *
 * Contract for the mount package:
 *   UsersView                  Vue options object, props client, adminClient (optional), currentUser, vue (optional)
 *   registerUsersView(app)     app.component('clinic-users-view', UsersView)
 *
 * `client` reaches /api/clinic/v1 (pools, grants). The account routes sit outside
 * that root (accounts-ui contract), so the mount package may pass an `adminClient`
 * whose request() reaches /admin/users; it defaults to `client`.
 *
 * Sections: Konten (accounts-ui panel, incl. the rule that an admin account gets no
 * recovery code), Poolbezüge (memberships per user, derived from GET /pools via
 * pools-ui), Fachrechte (grants-ui for the selected user). Identity roles confer no
 * dictation access; the server decides every write. A 403 on the account list
 * replaces the whole view by the denied state. A change of the current user switches
 * the api client context (aborting in-flight calls) and remounts the children.
 * Nothing is written to browser storage; colours come only from the token contract.
 */

import { createAccountsAdmin, AccountsAdminPanel, LABELS as ACCOUNT_LABELS } from './accounts-ui.js';
import { createPoolsStore, POOL_TYPES } from './pools-ui.js';
import { createGrantsComponent } from './grants-ui.js';
import { surfaceFor } from './tokens.js';

export const SECTIONS = Object.freeze([
    Object.freeze({ id: 'accounts', label: 'Konten' }),
    Object.freeze({ id: 'pools', label: 'Poolbezüge' }),
    Object.freeze({ id: 'grants', label: 'Fachrechte' }),
]);

export const LABELS = Object.freeze({
    title: 'Benutzer und Rollen',
    sections: 'Bereiche',
    denied: 'Kein Zugriff. Die Benutzerverwaltung ist für dieses Konto nicht freigegeben.',
    deniedAnonymous: 'Kein Zugriff. Bitte anmelden.',
    empty: 'Keine Benutzer vorhanden. Mit «Konto anlegen» kann ein lokaler Benutzer angelegt werden.',
    loading: 'Benutzer werden geladen.',
    user: 'Benutzer',
    pickUser: 'Bitte einen Benutzer wählen.',
    authorPools: 'Autorenpools',
    staffPools: 'Schreibkräfte-Pools',
    noPools: 'Keine Poolzugehörigkeit',
    poolsHint: 'Mitgliedschaften werden unter «Pools verwalten» geändert.',
    reload: 'Erneut laden',
    retry: 'Erneut versuchen',
    eyebrow: 'Verwaltung',
    subline: 'Lokale Benutzerkonten, Poolbezüge und Fachrechte getrennt verwalten.',
    statAccounts: 'Benutzerkonten',
    statAccountsNote: 'Lokale Konten',
    statActive: 'Aktive Konten',
    statActiveNote: 'Anmeldefähig',
    statDisabled: 'Deaktivierte Konten',
    statDisabledNote: 'Nicht anmeldefähig',
    statAdmins: 'Administration',
    statAdminsNote: 'Konten mit Adminrecht',
    rolesTitle: 'Rollen und Zugriff',
    rolesText: 'Rollen erlauben Funktionen. Poolbezüge und Fachrechte werden je Benutzer unter den Bereichen oben geändert.',
    rolesNote: 'Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.',
    identityTitle: 'Anmeldung und Zugriff',
    identityText: 'Die Anmeldung und die Zuordnung externer Identitäten werden getrennt von den Konten verwaltet. Der Zugriff wird immer serverseitig geprüft.',
    identityLink: 'Anmeldung und Zugriff öffnen',
    listTitle: 'Lokale Konten',
    search: 'Suche',
    searchPlaceholder: 'Name oder Anmeldename …',
    roleFilter: 'Rollen',
    statusFilter: 'Status',
    allRoles: 'Alle Rollen',
    allStatuses: 'Alle Status',
    roleAdmin: 'Administration',
    roleIdentity: 'Identitätsadministration',
    roleNone: 'Ohne Adminrolle',
    resetFilter: 'Filter zurücksetzen',
    filterEmpty: 'Keine Einträge für diese Auswahl. Filter zurücksetzen oder Suche anpassen.',
    colPerson: 'Person',
    colRole: 'Rolle',
    colActions: 'Aktionen',
});

// Line icons of the V8 prototype icon set; decorative, the buttons carry the labels.
const ICON = (path) => '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="' + path + '"/></svg>';
const ICONS = Object.freeze({
    plus: ICON('M12 4v16M4 12h16'),
    edit: ICON('m15 4 5 5M4 20l4-1L21 6l-4-4L4 15z'),
    restart: ICON('M3 11a9 9 0 1 1 2 7M3 4v7h7'),
    lock: ICON('M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3'),
    unlock: ICON('M6 11h12v9H6zM8 11V8a4 4 0 0 1 7.5-2'),
    key: ICON('M15 8a4 4 0 1 0-3.5 4L4 19.5V21h3v-2h2v-2h2l2-2'),
});

/** Role label of an account row (pure). */
export function roleLabelOf(account) {
    const parts = [];
    if (account.is_admin) parts.push(LABELS.roleAdmin);
    if (account.is_identity_admin) parts.push(LABELS.roleIdentity);
    return parts.length ? parts.join(', ') : LABELS.roleNone;
}

/** Rows that match the filter bar: name/e-mail search, role and status (pure). */
export function filterAccounts(list, filter) {
    const needle = String(filter.q || '').trim().toLowerCase();
    return (list || []).filter((a) => {
        if (needle && !(String(a.username || '').toLowerCase().includes(needle) || String(a.email || '').toLowerCase().includes(needle))) return false;
        if (filter.role === 'admin' && !a.is_admin) return false;
        if (filter.role === 'identity' && !a.is_identity_admin) return false;
        if (filter.role === 'none' && (a.is_admin || a.is_identity_admin)) return false;
        return filter.status === 'all' || a.status === filter.status;
    });
}

// The page CSS (clinic-v8-admin.css) is shared by the clinic shell and the /admin Klinik tab: link it once next to clinic-v8.css.
function ensureAdminStyles() {
    if (typeof document === 'undefined' || document.querySelector('link[data-clinic-v8-admin]')) return;
    const base = document.querySelector('link[href*="clinic-v8.css"]');
    if (!base) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = base.getAttribute('href').replace('clinic-v8.css', 'clinic-v8-admin.css');
    link.setAttribute('data-clinic-v8-admin', '');
    document.head.appendChild(link);
}

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

/** Pool names per type that list the user as member (pure; server shape of GET /pools). */
export function membershipsOf(pools, userId) {
    const out = { author: [], staff: [] };
    for (const type of Object.keys(POOL_TYPES)) {
        for (const pool of (pools && pools[type]) || []) {
            if (pool.memberUserIds.includes(userId)) out[type].push({ id: pool.id, name: pool.name });
        }
    }
    return out;
}

/**
 * Framework-free state: active section, selected user, current-user scope, 403 state.
 * @param {{client: object, adminClient?: object, currentUser: object, reactive?: Function}} deps
 */
export function createUsersController(deps) {
    const client = deps.client;
    const reactive = deps.reactive || ((o) => o);
    const accounts = createAccountsAdmin({ client: deps.adminClient || client, reactive });
    const state = reactive({
        active: SECTIONS[0].id, userId: null, epoch: 0, denied: true, forbidden: false, selectedId: null,
    });
    let disposed = false;

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.forbidden = false;
        state.active = SECTIONS[0].id;
        state.selectedId = null;
        state.epoch += 1;
        client.switchContext({ accountId: id, dictationId: null });
    }

    function select(id) {
        if (disposed || state.denied || state.forbidden || !SECTIONS.some((s) => s.id === id)) return false;
        state.active = id;
        return true;
    }

    function selectUser(id) {
        const n = Number(id);
        state.selectedId = Number.isInteger(n) && accounts.state.accounts.some((a) => a.id === n) ? n : null;
        return state.selectedId;
    }

    // The account list is the gate: a 403 there means no user administration at all.
    async function load() {
        if (disposed || state.denied) return false;
        const ok = await accounts.load();
        state.forbidden = !ok && accounts.state.error === 'permission';
        return ok;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        client.switchContext({ accountId: null, dictationId: null });
    }

    setUser(deps.currentUser);
    return { state, accounts, setUser, select, selectUser, load, dispose };
}

const ERROR_SURFACE = surfaceFor('error');
const errorStyle = {
    background: ERROR_SURFACE.background, color: ERROR_SURFACE.color, borderColor: ERROR_SURFACE.border,
};

export const POOLS_PANEL_TEMPLATE = `
<section aria-labelledby="usr-pools-title">
  <h3 id="usr-pools-title">{{ labels.sections_pools }}</h3>
  <p v-if="!userId" role="status">{{ labels.pickUser }}</p>
  <p v-if="state.status === 'error' || state.status === 'denied'" role="alert" :style="errorStyle">{{ state.message }}
    <button type="button" @click="store.reload()">{{ labels.retry }}</button></p>
  <template v-if="userId && state.status === 'ready'">
    <section v-for="g in groups" :key="g.type" :aria-labelledby="'usr-pools-' + g.type">
      <h4 :id="'usr-pools-' + g.type">{{ g.title }}</h4>
      <ul v-if="g.items.length" role="list"><li v-for="p in g.items" :key="p.id">{{ p.name }}</li></ul>
      <p v-else>{{ labels.noPools }}</p>
    </section>
    <p>{{ labels.poolsHint }}</p>
  </template>
</section>`;

/** Memberships panel: one read of GET /pools through pools-ui, shown for one user. */
export function createPoolsPanel(Vue) {
    return {
        name: 'ClinicUserPoolsPanel',
        props: { client: { type: Object, required: true }, userId: { type: Number, default: null } },
        template: POOLS_PANEL_TEMPLATE,
        setup(props) {
            const state = Vue.reactive({ status: 'idle', pools: { author: [], staff: [] }, message: '' });
            const store = createPoolsStore(props.client, (next) => {
                state.status = next.status;
                state.pools = next.pools;
                state.message = next.message;
            });
            const groups = Vue.computed(() => {
                const m = membershipsOf(state.pools, props.userId);
                return [
                    { type: 'author', title: LABELS.authorPools, items: m.author },
                    { type: 'staff', title: LABELS.staffPools, items: m.staff },
                ];
            });
            Vue.onMounted(() => store.load());
            return {
                state, store, groups, userId: props.userId, errorStyle,
                labels: Object.assign({ sections_pools: SECTIONS[1].label }, LABELS),
            };
        },
    };
}

export const USERS_TEMPLATE = `
<div class="clinic-users-view v8-page v8-admin-page" data-testid="clinic-users-view">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ labels.eyebrow }}</div>
      <h1>{{ labels.title }}</h1>
      <p class="subline">{{ labels.subline }}</p>
    </div>
    <div v-if="accounts.loaded" class="head-actions">
      <button type="button" class="primary" data-testid="user-create" @click="openEditor()">${ICONS.plus}<span>{{ acc.create }}</span></button>
    </div>
  </div>
  <p v-if="state.denied" role="alert" class="note burgundy">{{ labels.deniedAnonymous }}</p>
  <p v-else-if="state.forbidden" role="alert" class="note burgundy">{{ labels.denied }}</p>
  <template v-else>
    <div v-if="accounts.loaded" class="stats" data-testid="user-stats">
      <div class="stat"><span>{{ labels.statAccounts }}</span><strong>{{ stats.total }}</strong><small>{{ labels.statAccountsNote }}</small></div>
      <div class="stat"><span>{{ labels.statActive }}</span><strong>{{ stats.active }}</strong><small>{{ labels.statActiveNote }}</small></div>
      <div class="stat"><span>{{ labels.statDisabled }}</span><strong>{{ stats.disabled }}</strong><small>{{ labels.statDisabledNote }}</small></div>
      <div class="stat"><span>{{ labels.statAdmins }}</span><strong>{{ stats.admins }}</strong><small>{{ labels.statAdminsNote }}</small></div>
    </div>
    <nav class="tabs" :aria-label="labels.sections">
      <button v-for="s in sections" :key="s.id" type="button" :id="'usr-sec-' + s.id"
        :aria-current="state.active === s.id ? 'true' : 'false'"
        :style="state.active === s.id ? { borderBottomColor: 'var(--burgundy)', color: 'var(--v8-text)' } : null"
        @click="select(s.id)">{{ s.label }}</button>
    </nav>
    <p v-if="accounts.loading" role="status" aria-live="polite" class="hint">{{ labels.loading }}</p>
    <p v-else-if="accounts.loaded && !accounts.accounts.length" role="status" aria-live="polite" class="empty">{{ labels.empty }}</p>
    <p v-if="accounts.error && !accounts.loaded" role="alert" class="note burgundy">{{ accounts.message }}
      <button type="button" class="small" @click="load()">{{ labels.retry }}</button></p>
    <section v-if="accounts.loaded && state.active === 'accounts' && accounts.accounts.length" class="panel" aria-labelledby="usr-list-title" data-testid="user-table-card">
      <h2 id="usr-list-title">{{ labels.listTitle }}</h2>
      <div class="filterbar">
        <div class="field search">
          <label for="usr-search">{{ labels.search }}</label>
          <input id="usr-search" type="search" autocomplete="off" :placeholder="labels.searchPlaceholder" v-model="filter.q" data-testid="user-filter">
        </div>
        <div class="field">
          <label for="usr-role">{{ labels.roleFilter }}</label>
          <select id="usr-role" v-model="filter.role">
            <option value="all">{{ labels.allRoles }}</option>
            <option value="admin">{{ labels.roleAdmin }}</option>
            <option value="identity">{{ labels.roleIdentity }}</option>
            <option value="none">{{ labels.roleNone }}</option>
          </select>
        </div>
        <div class="field">
          <label for="usr-status">{{ labels.statusFilter }}</label>
          <select id="usr-status" v-model="filter.status">
            <option value="all">{{ labels.allStatuses }}</option>
            <option v-for="(text, key) in acc.statuses" :key="key" :value="key">{{ text }}</option>
          </select>
        </div>
        <span class="tool-wrap"><button type="button" class="icon-button" :aria-label="labels.resetFilter" :title="labels.resetFilter" data-testid="user-filter-reset" @click="resetFilter()">${ICONS.restart}</button></span>
      </div>
      <p class="result-count" aria-live="polite" data-testid="user-count">{{ countText }}</p>
      <p v-if="!visible.length" class="empty" data-testid="user-filter-empty">{{ labels.filterEmpty }}</p>
      <div v-else class="table-wrap">
        <table :aria-label="acc.list" data-testid="user-table">
          <thead><tr>
            <th scope="col">{{ labels.colPerson }}</th><th scope="col">{{ labels.colRole }}</th>
            <th scope="col">{{ acc.loginPolicy }}</th><th scope="col">{{ acc.status }}</th><th scope="col">{{ labels.colActions }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="a in visible" :key="a.id" :data-id="a.id">
              <td><strong>{{ a.username }}</strong><small>{{ a.email }}</small></td>
              <td>{{ roleLabel(a) }}</td>
              <td>{{ acc.policies[a.login_policy] || a.login_policy }}</td>
              <td><span class="tag" :class="a.status === 'active' ? 'neutral' : 'wait'">{{ acc.statuses[a.status] || a.status }}</span></td>
              <td class="row-actions">
                <button type="button" class="icon-button" :aria-label="acc.edit + ': ' + a.username" :title="acc.edit" @click="openEditor(a.id)">${ICONS.edit}</button>
                <button type="button" v-if="a.status === 'active'" class="icon-button" :disabled="accounts.busy" :aria-label="acc.disable + ': ' + a.username" :title="acc.disable" @click="accountsCtl.setStatus(a.id, 'disabled')">${ICONS.lock}</button>
                <button type="button" v-if="a.status === 'disabled'" class="icon-button" :disabled="accounts.busy" :aria-label="acc.reactivate + ': ' + a.username" :title="acc.reactivate" @click="accountsCtl.setStatus(a.id, 'active')">${ICONS.unlock}</button>
                <button type="button" class="icon-button" :disabled="accounts.busy" :aria-label="acc.recovery + ': ' + a.username" :title="acc.recovery" @click="accountsCtl.issueRecovery(a.id)">${ICONS.key}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="hint">{{ acc.noImplicitAccess }}</p>
    </section>
    <div v-if="accounts.loaded" role="region" class="panel" :class="{ 'users-form-only': state.active === 'accounts' }" :aria-labelledby="state.active === 'accounts' && accounts.draft ? 'usr-editor-title' : 'usr-sec-' + state.active">
      <div v-if="state.active !== 'accounts'" class="field user-picker">
        <label for="usr-pick">{{ labels.user }}</label>
        <select id="usr-pick" :value="state.selectedId === null ? '' : String(state.selectedId)" @change="selectUser($event.target.value)">
          <option value="">–</option>
          <option v-for="a in accounts.accounts" :key="a.id" :value="String(a.id)">{{ a.username }}</option>
        </select>
      </div>
      <h2 v-if="state.active === 'accounts' && accounts.draft" id="usr-editor-title" ref="editorTitle" tabindex="-1">{{ accounts.base ? 'Konto bearbeiten: ' + accounts.base.username : acc.create }}</h2>
      <p v-if="state.active !== 'accounts' && state.selectedId === null" class="hint" role="status">{{ labels.pickUser }}</p>
      <component v-else :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active + ':' + state.selectedId"></component>
    </div>
    <div v-if="accounts.loaded" class="grid equal" style="margin-top: 20px">
      <section class="panel" aria-labelledby="usr-note-roles">
        <h2 id="usr-note-roles">{{ labels.rolesTitle }}</h2>
        <p class="hint">{{ labels.rolesText }}</p>
        <div class="note">{{ labels.rolesNote }}</div>
      </section>
      <section class="panel" aria-labelledby="usr-note-identity">
        <h2 id="usr-note-identity">{{ labels.identityTitle }}</h2>
        <p class="hint">{{ labels.identityText }}</p>
        <a class="button" href="/admin"><span>{{ labels.identityLink }}</span></a>
      </section>
    </div>
  </template>
</div>`;

export const UsersView = {
    name: 'ClinicUsersView',
    props: {
        client: { type: Object, required: true },
        adminClient: { type: Object, default: null },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: USERS_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        ensureAdminStyles();
        const ctrl = createUsersController({
            client: props.client, adminClient: props.adminClient || undefined, currentUser: props.currentUser,
            reactive: (o) => Vue.reactive(o),
        });
        const accountsPanel = Vue.markRaw(AccountsAdminPanel);
        const poolsPanel = Vue.markRaw(createPoolsPanel(Vue));
        const grantsCache = {};
        const grantsFor = (userId) => {
            if (!grantsCache[userId]) {
                grantsCache[userId] = Vue.markRaw(createGrantsComponent(Vue, props.client, { userId, canEdit: true }));
            }
            return grantsCache[userId];
        };
        const activeComponent = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'accounts') return accountsPanel;
            if (id === 'pools') return poolsPanel;
            return ctrl.state.selectedId === null ? null : grantsFor(ctrl.state.selectedId);
        });
        const activeProps = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'accounts') return { controller: ctrl.accounts };
            if (id === 'pools') return { client: props.client, userId: ctrl.state.selectedId };
            return {};
        });
        Vue.watch(() => userIdOf(props.currentUser), () => {
            ctrl.setUser(props.currentUser);
            ctrl.load();
        });
        Vue.onMounted(() => ctrl.load());
        Vue.onBeforeUnmount(() => ctrl.dispose());
        const stats = Vue.computed(() => {
            const list = ctrl.accounts.state.accounts || [];
            return {
                total: list.length,
                active: list.filter((a) => a.status === 'active').length,
                disabled: list.filter((a) => a.status === 'disabled').length,
                admins: list.filter((a) => a.is_admin).length,
            };
        });
        // Table card after the prototype (benutzer.html): the filter bar narrows the rows, the actions are the ones of accounts-ui.js.
        const filter = Vue.reactive({ q: '', role: 'all', status: 'all' });
        const visible = Vue.computed(() => filterAccounts(ctrl.accounts.state.accounts, filter));
        const countText = Vue.computed(() => visible.value.length + ' von ' + ctrl.accounts.state.accounts.length + ' Konten');
        const resetFilter = () => { filter.q = ''; filter.role = 'all'; filter.status = 'all'; };
        const editorTitle = Vue.ref(null);
        const openEditor = (id) => {
            ctrl.select('accounts');
            if (id == null) ctrl.accounts.startCreate();
            else ctrl.accounts.startEdit(id);
            Vue.nextTick(() => editorTitle.value?.focus());
        };
        return {
            filter, visible, countText, resetFilter, openEditor, editorTitle, roleLabel: roleLabelOf, accountsCtl: ctrl.accounts, acc: ACCOUNT_LABELS, accounts: ctrl.accounts.state,
            stats, state: ctrl.state, sections: SECTIONS, labels: LABELS, errorStyle,
            select: ctrl.select, load: ctrl.load, selectUser: ctrl.selectUser, activeComponent, activeProps,
        };
    },
};

export function registerUsersView(app) {
    app.component('clinic-users-view', UsersView);
}
