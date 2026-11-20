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

/**
 * Utility classes for the foreign child panels (accounts-ui, grants-ui, vocabulary-ui, ...) that are embedded in the
 * composed admin views. They are set on a plain wrapper element, so descendants without a class attribute get table, field and
 * button styling in the look of the admin user list without editing those modules; classed elements keep their own look. Tokens only, no hard colours.
 */
export const CHILD_SCOPE = 'overflow-x-auto [&_h2:not([class])]:text-base [&_h2:not([class])]:font-semibold [&_h2:not([class])]:mb-2 [&_h3:not([class])]:text-sm [&_h3:not([class])]:font-semibold [&_h3:not([class])]:mt-4 [&_h3:not([class])]:mb-2 [&_h4:not([class])]:text-sm [&_h4:not([class])]:font-medium [&_h4:not([class])]:mt-3 [&_h4:not([class])]:mb-1 [&_p:not([class])]:my-2 [&_p:not([class])]:text-sm [&_ul:not([class])]:my-2 [&_ul:not([class])]:space-y-1 [&_ul:not([class])]:text-sm [&_fieldset:not([class])]:my-3 [&_fieldset:not([class])]:rounded-lg [&_fieldset:not([class])]:border [&_fieldset:not([class])]:border-[var(--border-primary)] [&_fieldset:not([class])]:p-3 [&_legend:not([class])]:px-1 [&_legend:not([class])]:text-sm [&_legend:not([class])]:font-medium [&_label:not([class])]:mt-3 [&_label:not([class])]:mb-1 [&_label:not([class])]:block [&_label:not([class])]:text-sm [&_label:not([class])]:font-medium [&_label:not([class])]:text-[var(--text-secondary)] [&_table:not([class])]:w-full [&_table:not([class])]:min-w-[36rem] [&_table:not([class])]:border-collapse [&_table:not([class])]:text-sm [&_th:not([scope=row]):not([class])]:bg-[var(--bg-tertiary)] [&_th:not([scope=row]):not([class])]:px-3 [&_th:not([scope=row]):not([class])]:py-2 [&_th:not([scope=row]):not([class])]:text-left [&_th:not([scope=row]):not([class])]:text-xs [&_th:not([scope=row]):not([class])]:font-medium [&_th:not([scope=row]):not([class])]:uppercase [&_th:not([scope=row]):not([class])]:tracking-wider [&_th:not([scope=row]):not([class])]:text-[var(--text-muted)] [&_th[scope=row]:not([class])]:bg-transparent [&_th[scope=row]:not([class])]:normal-case [&_th[scope=row]:not([class])]:tracking-normal [&_th[scope=row]:not([class])]:border-t [&_th[scope=row]:not([class])]:border-[var(--border-primary)] [&_th[scope=row]:not([class])]:px-3 [&_th[scope=row]:not([class])]:py-2 [&_th[scope=row]:not([class])]:text-left [&_th[scope=row]:not([class])]:text-sm [&_th[scope=row]:not([class])]:font-medium [&_th[scope=row]:not([class])]:text-[var(--text-primary)] [&_td:not([class])]:border-t [&_td:not([class])]:border-[var(--border-primary)] [&_td:not([class])]:px-3 [&_td:not([class])]:py-2 [&_button:not([class])]:my-1 [&_button:not([class])]:mr-1 [&_button:not([class])]:rounded-md [&_button:not([class])]:border [&_button:not([class])]:border-[var(--border-primary)] [&_button:not([class])]:bg-[var(--bg-secondary)] [&_button:not([class])]:px-3 [&_button:not([class])]:py-1.5 [&_button:not([class])]:text-sm [&_button:not([class])]:font-medium [&_button:not([class])]:text-[var(--text-primary)] [&_button:hover:not([class])]:bg-[var(--bg-tertiary)] [&_button:disabled:not([class])]:cursor-not-allowed [&_button:disabled:not([class])]:opacity-50 [&_button:focus-visible:not([class])]:outline-none [&_button:focus-visible:not([class])]:ring-2 [&_button:focus-visible:not([class])]:ring-[var(--border-focus)] [&_input:not([type=checkbox]):not([type=radio]):not([class])]:my-1 [&_input:not([type=checkbox]):not([type=radio]):not([class])]:rounded-md [&_input:not([type=checkbox]):not([type=radio]):not([class])]:border [&_input:not([type=checkbox]):not([type=radio]):not([class])]:border-[var(--border-secondary)] [&_input:not([type=checkbox]):not([type=radio]):not([class])]:bg-[var(--bg-input)] [&_input:not([type=checkbox]):not([type=radio]):not([class])]:px-3 [&_input:not([type=checkbox]):not([type=radio]):not([class])]:py-1.5 [&_input:not([type=checkbox]):not([type=radio]):not([class])]:text-sm [&_input:not([type=checkbox]):not([type=radio]):not([class])]:text-[var(--text-primary)] [&_select:not([class])]:my-1 [&_select:not([class])]:rounded-md [&_select:not([class])]:border [&_select:not([class])]:border-[var(--border-secondary)] [&_select:not([class])]:bg-[var(--bg-input)] [&_select:not([class])]:px-3 [&_select:not([class])]:py-1.5 [&_select:not([class])]:text-sm [&_select:not([class])]:text-[var(--text-primary)] [&_textarea:not([class])]:my-1 [&_textarea:not([class])]:w-full [&_textarea:not([class])]:rounded-md [&_textarea:not([class])]:border [&_textarea:not([class])]:border-[var(--border-secondary)] [&_textarea:not([class])]:bg-[var(--bg-input)] [&_textarea:not([class])]:px-3 [&_textarea:not([class])]:py-2 [&_textarea:not([class])]:text-sm [&_textarea:not([class])]:text-[var(--text-primary)] [&_[role=radio][aria-checked=true]:not([class])]:bg-[var(--bg-tertiary)] [&_[role=radio][aria-checked=true]:not([class])]:border-[var(--border-focus)] [&_[role=radio][aria-checked=true]:not([class])]:font-semibold [&_[aria-selected=true]:not([class])]:bg-[var(--bg-tertiary)] [&_[aria-selected=true]:not([class])]:border-[var(--border-focus)] [&_[aria-selected=true]:not([class])]:font-semibold [&_[aria-pressed=true]:not([class])]:bg-[var(--bg-tertiary)] [&_[aria-pressed=true]:not([class])]:border-[var(--border-focus)] [&_[aria-pressed=true]:not([class])]:font-semibold [&>section:not([class])]:p-4 [&_input:not([type=checkbox]):not([type=radio]):not([class])]:mr-2 [&_select:not([class])]:mr-2';

const ERROR_SURFACE = surfaceFor('error');
const errorStyle = {
    background: ERROR_SURFACE.background, color: ERROR_SURFACE.color, borderColor: ERROR_SURFACE.border,
};

export const POOLS_PANEL_TEMPLATE = `
<section aria-labelledby="usr-pools-title" class="space-y-3">
  <h3 id="usr-pools-title" class="text-base font-semibold text-[var(--text-primary)]">{{ labels.sections_pools }}</h3>
  <p v-if="!userId" role="status" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ labels.pickUser }}</p>
  <p v-if="state.status === 'error' || state.status === 'denied'" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ state.message }}
    <button type="button" class="btn btn--sm btn--ghost ml-2" @click="store.reload()">{{ labels.retry }}</button></p>
  <template v-if="userId && state.status === 'ready'">
    <div class="grid gap-3 md:grid-cols-2">
      <section v-for="g in groups" :key="g.type" :aria-labelledby="'usr-pools-' + g.type" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
        <h4 :id="'usr-pools-' + g.type" class="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ g.title }}</h4>
        <ul v-if="g.items.length" role="list" class="flex flex-wrap gap-2">
          <li v-for="p in g.items" :key="p.id" class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-3 py-1 text-sm text-[var(--text-primary)]">{{ p.name }}</li>
        </ul>
        <p v-else class="text-sm text-[var(--text-muted)]">{{ labels.noPools }}</p>
      </section>
    </div>
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
<div class="clinic-users-view space-y-4">
  <header>
    <h2 class="text-lg font-semibold text-[var(--text-primary)]">{{ labels.title }}</h2>
    <p class="mt-1 text-sm text-[var(--text-muted)]">Konten, Poolbezüge und Fachrechte der Benutzer.</p>
  </header>
  <p v-if="state.denied" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ labels.deniedAnonymous }}</p>
  <p v-else-if="state.forbidden" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ labels.denied }}</p>
  <template v-else>
    <nav :aria-label="labels.sections" class="inline-flex flex-wrap gap-1 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-1">
      <button v-for="s in sections" :key="s.id" type="button" :id="'usr-sec-' + s.id"
        class="rounded-md px-3 py-1.5 text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)] aria-[current=true]:bg-[var(--bg-secondary)] aria-[current=true]:text-[var(--text-primary)] aria-[current=true]:shadow-sm"
        :aria-current="state.active === s.id ? 'true' : 'false'" @click="select(s.id)">{{ s.label }}</button>
    </nav>
    <p v-if="accounts.loading" role="status" aria-live="polite" class="text-sm text-[var(--text-muted)]">{{ labels.loading }}</p>
    <p v-else-if="accounts.loaded && !accounts.accounts.length" role="status" aria-live="polite" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ labels.empty }}</p>
    <p v-if="accounts.error && !accounts.loaded" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ accounts.message }}
      <button type="button" class="btn btn--sm btn--ghost ml-2" @click="load()">{{ labels.retry }}</button></p>
    <div v-if="accounts.loaded && state.active !== 'accounts'" class="flex flex-wrap items-center gap-2">
      <label for="usr-pick" class="text-sm font-medium text-[var(--text-secondary)]">{{ labels.user }}</label>
      <select id="usr-pick" class="field field--sm w-auto min-w-[12rem] max-w-full" :value="state.selectedId === null ? '' : String(state.selectedId)" @change="selectUser($event.target.value)">
        <option value="">-</option>
        <option v-for="a in accounts.accounts" :key="a.id" :value="String(a.id)">{{ a.username }}</option>
      </select>
    </div>
    <div v-if="accounts.loaded" role="region" :aria-labelledby="'usr-sec-' + state.active" class="${CHILD_SCOPE}">
      <component :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active + ':' + state.selectedId"></component>
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
        return {
            state: ctrl.state, accounts: ctrl.accounts.state, sections: SECTIONS, labels: LABELS, errorStyle,
            select: ctrl.select, load: ctrl.load, selectUser: ctrl.selectUser, activeComponent, activeProps,
        };
    },
};

export function registerUsersView(app) {
    app.component('clinic-users-view', UsersView);
}
