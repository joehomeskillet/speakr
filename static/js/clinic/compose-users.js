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
 * pools-ui), Fachrechte (grants-ui for the selected user, with the admin client so
 * authors and exclusions are chosen by name). Identity roles confer no
 * dictation access; the server decides every write. A 403 on the account list
 * replaces the whole view by the denied state. A change of the current user switches
 * the api client context (aborting in-flight calls) and remounts the children.
 * Nothing is written to browser storage; colours come only from the token contract.
 */

import { createAccountsAdmin, AccountsAdminPanel } from './accounts-ui.js';
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
    statActive: 'Aktive Konten',
    statDisabled: 'Deaktivierte Konten',
    statAdmins: 'Administration',
    rolesNote: 'Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.',
    identityLink: 'Anmelderichtlinie öffnen',
});

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
<section aria-labelledby="usr-pools-title" class="space-y-3">
  <h3 id="usr-pools-title" class="sr-only">{{ labels.sections_pools }}</h3>
  <p v-if="!userId" role="status" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ labels.pickUser }}</p>
  <p v-if="state.status === 'error' || state.status === 'denied'" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ state.message }}
    <button type="button" class="btn btn--sm btn--ghost ml-2" @click="store.reload()">{{ labels.retry }}</button></p>
  <template v-if="userId && state.status === 'ready'">
    <dl class="keyvalues">
      <div v-for="g in groups" :key="g.type">
        <dt :id="'usr-pools-' + g.type">{{ g.title }}</dt>
        <dd>
          <ul v-if="g.items.length" role="list" :aria-labelledby="'usr-pools-' + g.type"><li v-for="p in g.items" :key="p.id">{{ p.name }}</li></ul>
          <template v-else>{{ labels.noPools }}</template>
        </dd>
      </div>
    </dl>
    <p class="text-sm text-[var(--text-muted)]">{{ labels.poolsHint }}</p>
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
    <div v-if="accounts.loaded && !state.denied && !state.forbidden" class="head-actions">
      <button type="button" class="primary" :disabled="accounts.busy" @click="startCreate"><span aria-hidden="true">+</span> Konto anlegen</button>
    </div>
  </div>
  <p v-if="state.denied" role="alert" class="note burgundy">{{ labels.deniedAnonymous }}</p>
  <p v-else-if="state.forbidden" role="alert" class="note burgundy">{{ labels.denied }}</p>
  <template v-else>
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
    <div v-if="accounts.loaded" role="region" :class="{ panel: state.active !== 'accounts' }" :aria-labelledby="'usr-sec-' + state.active">
      <div v-if="state.active !== 'accounts'" class="cadm-subsection-head">
        <div>
          <h2>{{ state.active === 'grants' ? sections[2].label : sections[1].label }}</h2>
        </div>
        <div class="field">
          <label for="usr-pick">{{ labels.user }}</label>
          <select id="usr-pick" :value="state.selectedId === null ? '' : String(state.selectedId)" @change="selectUser($event.target.value)">
            <option value="">Konto wählen</option>
            <option v-for="a in accounts.accounts" :key="a.id" :value="String(a.id)">{{ a.username }}</option>
          </select>
        </div>
      </div>
      <p v-if="state.active === 'grants' && state.selectedId === null" class="hint" role="status">{{ labels.pickUser }}</p>
      <component v-else :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active + ':' + state.selectedId"></component>
      <div v-if="state.active === 'accounts'" class="cadm-login-shortcut">
        <label for="usr-login-account">Anmelderichtlinie für</label>
        <select id="usr-login-account" :value="state.selectedId ?? ''" @change="selectUser($event.target.value)">
          <option value="">Konto wählen</option>
          <option v-for="a in accounts.accounts" :key="a.id" :value="a.id">{{ a.username }}</option>
        </select>
        <button type="button" class="button" :disabled="state.selectedId === null || accounts.busy || (accounts.draft && accounts.draft.id !== state.selectedId)" @click="openLoginPolicy">{{ labels.identityLink }}</button>
      </div>
      <p v-if="state.active === 'grants'" class="hint">{{ labels.rolesNote }}</p>
      <a v-if="state.active === 'accounts' && accounts.draft && accounts.draft.mode === 'edit'"
         :href="'/admin#/system/audit?konto=' + encodeURIComponent(accounts.draft.id)" class="button">
        Systemaktivität dieses Kontos
      </a>
    </div>
    <div v-if="accounts.loaded && state.active === 'accounts'" class="stats cadm-account-summary" data-testid="user-stats">
      <div class="stat"><span>{{ labels.statAccounts }}</span><strong>{{ stats.total }}</strong></div>
      <div class="stat"><span>{{ labels.statActive }}</span><strong>{{ stats.active }}</strong></div>
      <div class="stat"><span>{{ labels.statDisabled }}</span><strong>{{ stats.disabled }}</strong></div>
      <div class="stat"><span>{{ labels.statAdmins }}</span><strong>{{ stats.admins }}</strong></div>
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
        const ctrl = createUsersController({
            client: props.client, adminClient: props.adminClient || undefined, currentUser: props.currentUser,
            reactive: (o) => Vue.reactive(o),
        });
        const accountsPanel = Vue.markRaw(AccountsAdminPanel);
        const poolsPanel = Vue.markRaw(createPoolsPanel(Vue));
        const grantsCache = {};
        const grantsFor = (userId) => {
            const canEdit = props.currentUser?.is_admin === true;
            const key = userId + ':' + canEdit;
            if (!grantsCache[key]) {
                grantsCache[key] = Vue.markRaw(createGrantsComponent(Vue, props.client, {
                    userId, canEdit, adminClient: props.adminClient || null,
                }));
            }
            return grantsCache[key];
        };
        const activeComponent = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'accounts') return accountsPanel;
            if (id === 'pools') return poolsPanel;
            return ctrl.state.selectedId === null ? null : grantsFor(ctrl.state.selectedId);
        });
        const activeProps = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'accounts') return { controller: ctrl.accounts, embedded: true };
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
        return {
            openLoginPolicy() {
                ctrl.select('accounts');
                if (!ctrl.accounts.state.draft) ctrl.accounts.startEdit(ctrl.state.selectedId);
                Vue.nextTick(() => document.getElementById('acc-policy')?.focus());
            },
            startCreate() {
                ctrl.select('accounts');
                ctrl.accounts.startCreate();
                Vue.nextTick(() => document.getElementById('acc-username')?.focus());
            },
            stats, state: ctrl.state, accounts: ctrl.accounts.state, sections: SECTIONS, labels: LABELS, errorStyle,
            select: ctrl.select, load: ctrl.load, selectUser: ctrl.selectUser, activeComponent, activeProps,
        };
    },
};

export function registerUsersView(app) {
    app.component('clinic-users-view', UsersView);
}
