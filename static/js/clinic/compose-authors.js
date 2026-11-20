/**
 * Clinic authors view (v8-52): V8 page P11, "Diktierende und Autorenpools". One Vue view composed
 * from the existing accounts-ui (account list), pools-ui (author and staff pool editing, revisions,
 * conflicts, removal confirmation) and tokens modules. PAR-ADM-001, PAR-ADM-002.
 *
 * Contract for the mount package:
 *   AuthorsView                  Vue options object, props client, currentUser, vue (optional)
 *   registerAuthorsView(app)     app.component('clinic-authors-view', AuthorsView)
 *
 * Diktierende are existing users (no person store): a user is listed when it belongs to at least one
 * author pool. The rows are derived from GET /admin/users and GET /pools. Author pools and staff
 * pools stay separate lists. The server enforces one author pool per user (app.py author_pool_for);
 * a user found in several author pools is shown with that rule, no picker is offered. Pool
 * membership edits are the pools-ui component, mounted as child and remounted after a reload.
 * Nothing is written to browser storage; colours come only from the token contract.
 *
 * Not carried by any route yet (handover): validity intervals of memberships, per-author routing
 * rules and default staff pool, resource references per pool, device mapping (P09).
 */

import { createAccountsAdmin } from './accounts-ui.js';
import { createPoolsComponent, createPoolsStore } from './pools-ui.js';
import { surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Diktierende und Autorenpools',
    reload: 'Aktualisieren',
    retry: 'Erneut versuchen',
    loading: 'Diktierende werden geladen.',
    denied: 'Keine Berechtigung. Autorenverzeichnisse sind nur im zulässigen Bereich sichtbar.',
    empty: 'Keine Diktierenden vorhanden. Eine Person wird Diktierende, indem sie einem Autorenpool zugeordnet wird.',
    error: 'Die Diktierenden konnten nicht geladen werden.',
    list: 'Diktierende',
    columns: Object.freeze({
        name: 'Anzeigename',
        id: 'Benutzer-ID',
        status: 'Kontostatus',
        authorPools: 'Autorenpools',
        staffPools: 'Schreibkräfte-Pools',
        role: 'Diktierrolle',
    }),
    roleAuthor: 'Diktierend',
    roleAlsoTypist: 'Diktierend und Schreibkraft',
    noStaffPool: 'Kein Pool zugewiesen',
    multiMembership: 'Mehrere Autorenpools. Die Regel erlaubt einen Autorenpool je Benutzer; die Mitgliedschaften sind hier ehrlich aufgeführt, eine Auswahl gibt es nicht.',
    statuses: Object.freeze({ active: 'Aktiv', disabled: 'Deaktiviert', pending_activation: 'Aktivierung ausstehend' }),
    pools: 'Autorenpools und Schreibkräfte-Pools',
    poolsHelp: 'Autorenpools teilen Ressourcen und Sichtregeln. Schreibkräfte-Pools sind Zuständigkeitsgruppen. Beide Listen sind getrennt.',
    rules: 'Hinweise',
    ruleOnePool: 'Ein Benutzer gehört höchstens einem Autorenpool an. Gleichnamige Pools werden nicht zusammengeschlossen.',
    ruleNoPerson: 'Diktierende sind bestehende Benutzer. Es gibt keine eigene Personenverwaltung.',
    eyebrow: 'Verwaltung',
    subline: 'Diktierende Personen eindeutig ausweisen und ihre Autorenressourcen organisieren.',
    search: 'Suche',
    searchPlaceholder: 'Name, Benutzer-ID oder Pool …',
    allPools: 'Alle Autorenpools',
    resetFilter: 'Filter zurücksetzen',
    filterEmpty: 'Keine Einträge für diese Auswahl. Filter zurücksetzen oder Suche anpassen.',
    statAuthors: 'Diktierende Personen',
    statAuthorsNote: 'Mit Autorenpool',
    statPools: 'Autorenpools',
    statPoolsNote: 'Mit Mitgliedern',
    statNoStaff: 'Ohne Schreibkräfte-Pool',
    statNoStaffNote: 'Kein Pool zugewiesen',
    statDisabled: 'Deaktivierte Konten',
    statDisabledNote: 'Nicht anmeldefähig',
    cardMembers: 'Mitglieder',
});

const STATUS_SURFACE = Object.freeze({ active: 'success', disabled: 'error', pending_activation: 'warning' });

function style(kind) {
    const s = surfaceFor(kind);
    return { background: s.background, color: s.color, borderColor: s.border };
}

/** Derive the Diktierende rows from account rows (accounts-ui) and pool state (pools-ui store). */
export function deriveAuthors(accounts, pools) {
    const authorPools = (pools && pools.author) || [];
    const staffPools = (pools && pools.staff) || [];
    const rows = [];
    for (const account of accounts || []) {
        const mine = authorPools.filter((p) => p.memberUserIds.includes(account.id));
        if (!mine.length) continue;
        const staff = staffPools.filter((p) => p.memberUserIds.includes(account.id));
        rows.push({
            id: account.id,
            name: account.username,
            status: account.status,
            statusLabel: LABELS.statuses[account.status] || account.status,
            surface: STATUS_SURFACE[account.status] || 'normal',
            authorPools: mine.map((p) => ({ id: p.id, name: p.name })),
            staffPools: staff.map((p) => ({ id: p.id, name: p.name })),
            noStaffPool: staff.length === 0,
            multiAuthorPool: mine.length > 1,
            roleLabel: staff.length ? LABELS.roleAlsoTypist : LABELS.roleAuthor,
        });
    }
    return rows.sort((a, b) => String(a.name).localeCompare(String(b.name), 'de'));
}

/** One card per author pool: member names and the staff pools those members belong to (names only, no ids). */
export function poolCardsOf(accounts, pools) {
    const nameOf = (id) => {
        const account = (accounts || []).find((a) => a.id === id);
        return account ? account.username : '';
    };
    const staffPools = (pools && pools.staff) || [];
    return ((pools && pools.author) || []).map((pool) => ({
        id: pool.id,
        name: pool.name,
        members: pool.memberUserIds.map(nameOf).filter(Boolean),
        staffPools: staffPools.filter((s) => s.memberUserIds.some((id) => pool.memberUserIds.includes(id))).map((s) => s.name),
    }));
}

/**
 * Framework-free state for the list. Status: idle | loading | ready | denied | error.
 * @param {{client: {request: Function}, reactive?: Function}} deps
 */
export function createAuthorsController(deps) {
    const client = deps.client;
    const reactive = deps.reactive || ((x) => x);
    const accounts = createAccountsAdmin({ client, reactive });
    const state = reactive({ status: 'idle', rows: [], poolCards: [], epoch: 0, message: '', query: '', pool: '' });
    let seq = 0;

    async function load() {
        const run = ++seq;
        state.status = 'loading';
        state.message = '';
        const store = createPoolsStore(client);
        const [okAccounts, poolRes] = await Promise.all([accounts.load(), store.load()]);
        if (run !== seq) return false; // a newer load owns the state
        if (okAccounts && poolRes && poolRes.outcome === 'ok') {
            state.rows = deriveAuthors(accounts.state.accounts, store.state.pools);
            state.poolCards = poolCardsOf(accounts.state.accounts, store.state.pools);
            state.status = 'ready';
            state.epoch += 1; // remounts the pools child with the current server state
            return true;
        }
        state.rows = [];
        const denied = accounts.state.error === 'permission' || (poolRes && poolRes.outcome === 'permission');
        state.status = denied ? 'denied' : 'error';
        state.message = denied ? LABELS.denied : LABELS.error;
        return false;
    }

    /** Rows matching the search text (name, user id, pool names) and the chosen author pool. */
    function visible() {
        const needle = String(state.query || '').trim().toLowerCase();
        return state.rows.filter((r) => {
            if (state.pool && !r.authorPools.some((p) => String(p.id) === state.pool)) return false;
            if (!needle) return true;
            const haystack = [r.name, r.id, ...r.authorPools.map((p) => p.name), ...r.staffPools.map((p) => p.name)].join(' ').toLowerCase();
            return haystack.includes(needle);
        });
    }

    function poolOptions() {
        const seen = new Map();
        state.rows.forEach((r) => r.authorPools.forEach((p) => seen.set(String(p.id), p)));
        return [...seen.values()].sort((x, y) => String(x.name).localeCompare(String(y.name), 'de'));
    }

    function resetFilter() {
        state.query = '';
        state.pool = '';
    }

    function stats() {
        return {
            authors: state.rows.length,
            pools: poolOptions().length,
            noStaffPool: state.rows.filter((r) => r.noStaffPool).length,
            disabled: state.rows.filter((r) => r.status === 'disabled').length,
        };
    }

    return { state, load, retry: load, visible, poolOptions, resetFilter, stats };
}

export function statusStyle(kind) {
    return style(kind);
}

// Line icons of the V8 prototype icon set; decorative, the buttons carry the labels.
const ICON = (path) => '<svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="' + path + '"/></svg>';
const ICONS = Object.freeze({
    restart: ICON('M3 11a9 9 0 1 1 2 7M3 4v7h7'),
    reload: ICON('M3 11a9 9 0 1 1 2 7M3 4v7h7'),
});

export const AUTHORS_TEMPLATE = `
<section class="clinic-authors-view v8-page v8-admin-page" data-testid="clinic-authors-view" aria-labelledby="authors-title">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ labels.eyebrow }}</div>
      <h1 id="authors-title">{{ labels.title }}</h1>
      <p class="subline">{{ labels.subline }}</p>
    </div>
    <div class="head-actions">
      <button type="button" :disabled="state.status === 'loading'" @click="controller.load()">${ICONS.reload}<span>{{ labels.reload }}</span></button>
    </div>
  </div>
  <p v-if="state.status === 'loading'" role="status" aria-live="polite" class="hint">{{ labels.loading }}</p>
  <p v-if="state.status === 'denied'" role="alert" class="note burgundy">{{ state.message }}</p>
  <p v-if="state.status === 'error'" role="alert" class="note burgundy">{{ state.message }}
    <button type="button" class="small" @click="controller.retry()">{{ labels.retry }}</button></p>
  <template v-if="state.status === 'ready'">
    <section class="panel" :aria-label="labels.list">
      <h2>{{ labels.list }}</h2>
      <p v-if="!state.rows.length" role="status" aria-live="polite" class="empty">{{ labels.empty }}</p>
      <template v-else>
        <div class="filterbar">
          <div class="field search">
            <label for="authors-search">{{ labels.search }}</label>
            <input id="authors-search" type="search" autocomplete="off" :placeholder="labels.searchPlaceholder" v-model="state.query">
          </div>
          <div class="field">
            <label for="authors-pool">{{ labels.columns.authorPools }}</label>
            <select id="authors-pool" v-model="state.pool">
              <option value="">{{ labels.allPools }}</option>
              <option v-for="p in controller.poolOptions()" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
            </select>
          </div>
          <span class="tool-wrap"><button type="button" class="icon-button" :aria-label="labels.resetFilter" :title="labels.resetFilter" @click="controller.resetFilter()">${ICONS.restart}</button></span>
        </div>
        <p class="result-count" aria-live="polite">{{ controller.visible().length }} von {{ state.rows.length }} {{ labels.list }}</p>
        <p v-if="!controller.visible().length" class="empty">{{ labels.filterEmpty }}</p>
        <div v-else class="table-wrap" role="region" :aria-label="labels.list" tabindex="0">
          <table :aria-label="labels.list">
            <thead><tr>
              <th scope="col">{{ labels.columns.name }}</th><th scope="col">{{ labels.columns.authorPools }}</th>
              <th scope="col">{{ labels.columns.staffPools }}</th><th scope="col">{{ labels.columns.role }}</th>
              <th scope="col">{{ labels.columns.status }}</th>
            </tr></thead>
            <tbody>
              <tr v-for="r in controller.visible()" :key="r.id" :data-user-id="r.id">
                <th scope="row"><strong>{{ r.name }}</strong><small>{{ labels.columns.id }}: {{ r.id }}</small></th>
                <td class="wrap">
                  <span v-for="p in r.authorPools" :key="p.id">{{ p.name }} </span>
                  <span v-if="r.multiAuthorPool" role="note" class="hint">{{ labels.multiMembership }}</span>
                </td>
                <td class="wrap">
                  <span v-for="p in r.staffPools" :key="p.id">{{ p.name }} </span>
                  <span v-if="r.noStaffPool" class="muted">{{ labels.noStaffPool }}</span>
                </td>
                <td>{{ r.roleLabel }}</td>
                <td><span class="tag" :class="r.surface === 'success' ? 'neutral' : (r.surface === 'error' ? 'error' : 'wait')">{{ r.statusLabel }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>
    <div class="stats" data-testid="author-stats">
      <div class="stat"><span>{{ labels.statAuthors }}</span><strong>{{ controller.stats().authors }}</strong><small>{{ labels.statAuthorsNote }}</small></div>
      <div class="stat"><span>{{ labels.statPools }}</span><strong>{{ controller.stats().pools }}</strong><small>{{ labels.statPoolsNote }}</small></div>
      <div class="stat"><span>{{ labels.statNoStaff }}</span><strong>{{ controller.stats().noStaffPool }}</strong><small>{{ labels.statNoStaffNote }}</small></div>
      <div class="stat"><span>{{ labels.statDisabled }}</span><strong>{{ controller.stats().disabled }}</strong><small>{{ labels.statDisabledNote }}</small></div>
    </div>
    <div v-if="state.poolCards.length" class="grid three" style="margin-top: 20px" data-testid="author-pool-cards">
      <section v-for="p in state.poolCards" :key="p.id" class="panel" :aria-label="p.name">
        <h2>{{ p.name }}</h2>
        <dl class="keyvalues">
          <div><dt>{{ labels.cardMembers }}</dt><dd>{{ p.members.length ? p.members.join(', ') : '–' }}</dd></div>
          <div><dt>{{ labels.columns.staffPools }}</dt><dd>{{ p.staffPools.length ? p.staffPools.join(', ') : labels.noStaffPool }}</dd></div>
        </dl>
      </section>
    </div>
    <details class="panel" style="margin-top: 20px" aria-labelledby="authors-pools">
      <summary id="authors-pools" style="cursor: pointer; font-weight: 700">{{ labels.pools }}</summary>
      <p class="hint" style="margin-top: 12px">{{ labels.poolsHelp }}</p>
      <component :is="poolsComponent" :key="state.epoch"></component>
    </details>
    <div class="note" aria-labelledby="authors-rules" role="group">
      <strong id="authors-rules">{{ labels.rules }}</strong>
      <p class="hint">{{ labels.ruleOnePool }}</p>
      <p class="hint">{{ labels.ruleNoPerson }}</p>
    </div>
  </template>
</section>`;

export const AuthorsView = {
    name: 'ClinicAuthorsView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: AUTHORS_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        const controller = createAuthorsController({ client: props.client, reactive: (o) => Vue.reactive(o) });
        const poolsComponent = Vue.markRaw(createPoolsComponent(Vue, props.client));
        const surfaceStyle = (kind) => style(kind);
        Vue.onMounted(() => controller.load());
        return {
            state: controller.state, controller, labels: LABELS, poolsComponent,
            errorStyle: style('error'), surfaceStyle,
        };
    },
};

export function registerAuthorsView(app) {
    app.component('clinic-authors-view', AuthorsView);
}
