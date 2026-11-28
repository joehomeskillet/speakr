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
    multiMembership: 'Mehrere Autorenpools. Die Ressourcen und Sichtregeln aller dieser Pools gelten zusammen.',
    statuses: Object.freeze({ active: 'Aktiv', disabled: 'Deaktiviert', pending_activation: 'Aktivierung ausstehend' }),
    pools: 'Autorenpools und Schreibkräfte-Pools',
    poolsHelp: 'Autorenpools teilen Ressourcen und Sichtregeln. Schreibkräfte-Pools sind Zuständigkeitsgruppen. Beide Listen sind getrennt.',
    rules: 'Hinweise',
    ruleOnePool: 'Ein Benutzer gehört höchstens einem Autorenpool an. Gleichnamige Pools werden nicht zusammengeschlossen.',
    ruleMultiPool: 'Ein Benutzer kann mehreren Autorenpools angehören. Ressourcen und Sichtregeln gelten dann zusammen. Für Empfänger-Vorgaben zählt der erste Pool nach Name. Gleichnamige Pools werden nicht zusammengeschlossen.',
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
    openPool: 'Zum Autorenpool:',
    createTitle: 'Autorenpool anlegen',
    createHelp: 'Der Name muss eindeutig sein. Die Mitglieder werden danach in der Karte des Pools zugeordnet; ein Benutzer kann in mehreren Pools stehen.',
    createName: 'Name des neuen Autorenpools',
    createAction: 'Anlegen',
    nameRequired: 'Bitte einen Namen eingeben.',
    created: 'Autorenpool angelegt:',
    nameTaken: 'Konflikt (Server): Dieser Name ist bereits vergeben. Bitte einen anderen Namen wählen.',
    stale: 'Der Stand ist veraltet. Die Ansicht wurde neu geladen; bitte die Änderung wiederholen.',
    createFailed: 'Der Autorenpool konnte nicht angelegt werden.',
    assignFailed: 'Die Zuordnung konnte nicht gespeichert werden.',
    assignUser: 'Benutzer zuordnen',
    assignPick: 'Benutzer wählen',
    assignAction: 'Zuordnen',
    assigned: 'Zugeordnet.',
    noCandidates: 'Alle Benutzer sind bereits Mitglied.',
    cardRouted: 'Verbundene Schreibpools',
    linkStaff: 'Schreibpool verbinden',
    linkPick: 'Schreibpool wählen',
    linkAction: 'Verbinden',
    linkNoCandidates: 'Alle Schreibpools sind bereits verbunden.',
    linked: 'Verbunden.',
    linkTaken: 'Dieser Schreibpool ist bereits mit dem Autorenpool verbunden.',
    linkInactive: 'Der Schreibpool ist deaktiviert und kann nicht verbunden werden.',
    linkFailed: 'Die Verbindung konnte nicht gespeichert werden.',
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

/**
 * One card per author pool: member names and the staff pools it is routed to (ClinicPoolRoute links,
 * `routes` maps author pool id to staff pool ids; overlapping user memberships do not count).
 */
export function poolCardsOf(accounts, pools, routes) {
    const nameOf = (id) => {
        const account = (accounts || []).find((a) => a.id === id);
        return account ? account.username : '';
    };
    const staffPools = (pools && pools.staff) || [];
    return ((pools && pools.author) || []).map((pool) => ({
        id: pool.id,
        name: pool.name,
        memberIds: pool.memberUserIds.slice(),
        members: pool.memberUserIds.map(nameOf).filter(Boolean),
        staffPoolIds: ((routes && routes[pool.id]) || []).map(String),
        staffPools: staffPools.filter((s) => ((routes && routes[pool.id]) || []).map(String).includes(s.id)).map((s) => s.name),
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
    const state = reactive({
        status: 'idle', rows: [], poolCards: [], accountOptions: [], epoch: 0, message: '', query: '', pool: '',
        createName: '', createBusy: false, createMessage: '', createFailed: false,
        assign: {}, assignBusy: '', assignMessage: '', assignFailed: false,
        staffOptions: [], link: {}, linkBusy: '', linkMessage: '', linkFailed: false,
    });
    let seq = 0;
    let store = createPoolsStore(client);

    // silent keeps the ready view mounted (a write reloads without flashing the loading state).
    async function load(options) {
        const run = ++seq;
        if (!(options && options.silent === true)) state.status = 'loading';
        state.message = '';
        store = createPoolsStore(client);
        const [okAccounts, poolRes] = await Promise.all([accounts.load(), store.load()]);
        if (run !== seq) return false; // a newer load owns the state
        if (okAccounts && poolRes && poolRes.outcome === 'ok') {
            // Saved author pool to staff pool links (ClinicPoolRoute); GET /pools lists them for managers.
            const routes = {};
            for (const p of (poolRes.data && poolRes.data.author_pools) || []) routes[String(p.id)] = p.staff_pool_ids || [];
            state.rows = deriveAuthors(accounts.state.accounts, store.state.pools);
            state.poolCards = poolCardsOf(accounts.state.accounts, store.state.pools, routes);
            state.staffOptions = store.state.pools.staff.map((p) => ({ id: p.id, name: p.name }));
            state.accountOptions = accounts.state.accounts
                .filter((a) => a.status === 'active')
                .map((a) => ({ id: a.id, name: a.username }))
                .sort((x, y) => String(x.name).localeCompare(String(y.name), 'de'));
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

    /** Users that can still be added to the pool card. */
    function candidates(card) {
        return state.accountOptions.filter((a) => !card.memberIds.includes(a.id));
    }

    /** Staff pools the author pool card is not yet routed to. */
    function linkCandidates(card) {
        return state.staffOptions.filter((s) => !card.staffPoolIds.includes(s.id));
    }

    /** Connect an author pool to a staff pool through the pool admin link API (a ClinicPoolRoute). */
    async function linkStaff(card) {
        const staffId = String(state.link[card.id] || '');
        if (state.linkBusy || !staffId) return false;
        state.linkBusy = card.id;
        state.linkFailed = true;
        state.linkMessage = '';
        try {
            const res = await client.request('/pool-admin/pools/author/' + encodeURIComponent(card.id) + '/links', {
                method: 'POST', body: { staff_pool_id: staffId },
            });
            if (!res || res.outcome !== 'ok') {
                if (res && res.code === 'already_linked') state.linkMessage = LABELS.linkTaken;
                else if (res && res.code === 'pool_inactive') state.linkMessage = LABELS.linkInactive;
                else state.linkMessage = res && res.outcome === 'permission' ? LABELS.denied : LABELS.linkFailed;
                return false;
            }
            state.link[card.id] = '';
            state.linkFailed = false;
            state.linkMessage = LABELS.linked + ' ' + card.name;
            await load({ silent: true });
            return true;
        } finally {
            state.linkBusy = '';
        }
    }

    /** Create an author pool through the pool admin API; a duplicate name shows the server conflict. */
    async function createPool() {
        const name = String(state.createName || '').trim();
        if (state.createBusy) return false;
        state.createFailed = true;
        if (!name) {
            state.createMessage = LABELS.nameRequired;
            return false;
        }
        state.createBusy = true;
        state.createMessage = '';
        try {
            const res = await client.request('/pool-admin/pools', { method: 'POST', body: { kind: 'author', name } });
            if (res.outcome !== 'ok') {
                if (res.code === 'name_taken') state.createMessage = LABELS.nameTaken;
                else state.createMessage = res.outcome === 'permission' ? LABELS.denied : LABELS.createFailed;
                return false;
            }
            state.createName = '';
            state.createFailed = false;
            state.createMessage = LABELS.created + ' ' + name;
            await load({ silent: true });
            return true;
        } finally {
            state.createBusy = false;
        }
    }

    /** Add the chosen user to an author pool with the persisted revision; a stale revision reloads. */
    async function assign(card) {
        const userId = Number(state.assign[card.id]);
        if (state.assignBusy || !Number.isInteger(userId) || userId < 1) return false;
        state.assignBusy = card.id;
        state.assignFailed = true;
        state.assignMessage = '';
        try {
            const res = await store.addMember('author', card.id, userId);
            if (!res || res.outcome !== 'ok') {
                state.assignMessage = res && res.outcome === 'conflict' ? LABELS.stale : LABELS.assignFailed;
                if (res && res.outcome === 'conflict') await load({ silent: true });
                return false;
            }
            state.assign[card.id] = '';
            state.assignFailed = false;
            state.assignMessage = LABELS.assigned + ' ' + card.name;
            await load({ silent: true });
            return true;
        } finally {
            state.assignBusy = '';
        }
    }

    return { state, load, retry: load, visible, poolOptions, resetFilter, stats, candidates, createPool, assign, linkCandidates, linkStaff };
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
    <section class="panel" data-testid="author-pool-create" :aria-label="labels.createTitle" style="margin-bottom: 20px">
      <h2>{{ labels.createTitle }}</h2>
      <p class="hint">{{ labels.createHelp }}</p>
      <form class="filterbar" @submit.prevent="controller.createPool()">
        <div class="field search">
          <label for="authors-create-name">{{ labels.createName }}</label>
          <input id="authors-create-name" type="text" autocomplete="off" v-model="state.createName" :aria-invalid="state.createFailed && !!state.createMessage ? 'true' : 'false'" aria-describedby="authors-create-message">
        </div>
        <button type="submit" :disabled="state.createBusy">{{ labels.createAction }}</button>
      </form>
      <p id="authors-create-message" data-testid="author-pool-create-message" :role="state.createFailed ? 'alert' : 'status'" aria-live="polite" :class="state.createFailed ? 'note burgundy' : 'hint'" v-if="state.createMessage">{{ state.createMessage }}</p>
    </section>
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
        <div v-else class="table-wrap cadm-tablewrap--stack authors-table-wrap" role="region" :aria-label="labels.list" tabindex="0">
          <table class="cadm-table--stack" role="table" :aria-label="labels.list">
            <thead role="rowgroup"><tr role="row">
              <th scope="col" role="columnheader">{{ labels.columns.name }}</th><th scope="col" role="columnheader">{{ labels.columns.authorPools }}</th>
              <th scope="col" role="columnheader">{{ labels.columns.staffPools }}</th><th scope="col" role="columnheader">{{ labels.columns.role }}</th>
              <th scope="col" role="columnheader">{{ labels.columns.status }}</th>
            </tr></thead>
            <tbody role="rowgroup">
              <tr v-for="r in controller.visible()" :key="r.id" :data-user-id="r.id" role="row">
                <th scope="row" role="rowheader"><strong>{{ r.name }}</strong><small>{{ labels.columns.id }}: {{ r.id }}</small></th>
                <td class="wrap authors-memberships" role="cell">
                  <span class="authors-cell-label" aria-hidden="true">{{ labels.columns.authorPools }}</span>
                  <div>
                    <button v-for="p in r.authorPools" :key="p.id" type="button" class="authors-pool-action"
                            :aria-label="labels.openPool + ' ' + p.name" :aria-controls="'authors-pool-card-' + p.id"
                            @click="openPoolCard($event)">{{ p.name }}</button>
                    <div v-if="r.multiAuthorPool" role="note" class="hint" style="margin-top: 6px">{{ labels.multiMembership }}</div>
                  </div>
                </td>
                <td class="wrap" role="cell">
                  <span class="authors-cell-label" aria-hidden="true">{{ labels.columns.staffPools }}</span>
                  <div>
                    <div v-for="p in r.staffPools" :key="p.id">{{ p.name }}</div>
                    <span v-if="r.noStaffPool" class="muted">{{ labels.noStaffPool }}</span>
                  </div>
                </td>
                <td role="cell"><span class="authors-cell-label" aria-hidden="true">{{ labels.columns.role }}</span><span>{{ r.roleLabel }}</span></td>
                <td role="cell">
                  <span class="authors-cell-label" aria-hidden="true">{{ labels.columns.status }}</span>
                  <span class="tag" :class="r.surface === 'success' ? 'neutral' : (r.surface === 'error' ? 'error' : 'wait')">{{ r.statusLabel }}</span>
                </td>
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
        <h2 :id="'authors-pool-card-' + p.id" class="authors-pool-target" tabindex="-1">{{ p.name }}</h2>
        <dl class="keyvalues">
          <div><dt>{{ labels.cardMembers }}</dt><dd>{{ p.members.length ? p.members.join(', ') : '–' }}</dd></div>
          <div><dt>{{ labels.cardRouted }}</dt><dd data-testid="author-pool-routes">{{ p.staffPools.length ? p.staffPools.join(', ') : labels.noStaffPool }}</dd></div>
        </dl>
        <form class="filterbar" :data-testid="'assign-' + p.id" @submit.prevent="controller.assign(p)">
          <div class="field">
            <label :for="'authors-assign-' + p.id">{{ labels.assignUser }}</label>
            <select :id="'authors-assign-' + p.id" v-model="state.assign[p.id]" :disabled="!controller.candidates(p).length">
              <option value="">{{ controller.candidates(p).length ? labels.assignPick : labels.noCandidates }}</option>
              <option v-for="a in controller.candidates(p)" :key="a.id" :value="String(a.id)">{{ a.name }}</option>
            </select>
          </div>
          <button type="submit" :disabled="!state.assign[p.id] || !!state.assignBusy">{{ labels.assignAction }}</button>
        </form>
        <form class="filterbar" :data-testid="'link-' + p.id" @submit.prevent="controller.linkStaff(p)">
          <div class="field">
            <label :for="'authors-link-' + p.id">{{ labels.linkStaff }}</label>
            <select :id="'authors-link-' + p.id" v-model="state.link[p.id]" :disabled="!controller.linkCandidates(p).length">
              <option value="">{{ controller.linkCandidates(p).length ? labels.linkPick : labels.linkNoCandidates }}</option>
              <option v-for="s in controller.linkCandidates(p)" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </div>
          <button type="submit" :disabled="!state.link[p.id] || !!state.linkBusy">{{ labels.linkAction }}</button>
        </form>
      </section>
    </div>
    <p v-if="state.assignMessage" data-testid="author-pool-assign-message" :role="state.assignFailed ? 'alert' : 'status'" aria-live="polite" :class="state.assignFailed ? 'note burgundy' : 'hint'">{{ state.assignMessage }}</p>
    <p v-if="state.linkMessage" data-testid="author-pool-link-message" :role="state.linkFailed ? 'alert' : 'status'" aria-live="polite" :class="state.linkFailed ? 'note burgundy' : 'hint'">{{ state.linkMessage }}</p>
    <details class="panel" style="margin-top: 20px" aria-labelledby="authors-pools">
      <summary id="authors-pools" style="cursor: pointer; font-weight: 700">{{ labels.pools }}</summary>
      <p class="hint" style="margin-top: 12px">{{ labels.poolsHelp }}</p>
      <component :is="poolsComponent" :key="state.epoch"></component>
    </details>
    <div class="note" aria-labelledby="authors-rules" role="group">
      <strong id="authors-rules">{{ labels.rules }}</strong>
      <p class="hint">{{ labels.ruleMultiPool }}</p>
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
        function openPoolCard({ currentTarget }) {
            const heading = currentTarget.ownerDocument.getElementById(currentTarget.getAttribute('aria-controls'));
            heading.focus();
        }
        Vue.onMounted(() => controller.load());
        return {
            state: controller.state, controller, labels: LABELS, poolsComponent, openPoolCard,
            errorStyle: style('error'), surfaceStyle,
        };
    },
};

export function registerAuthorsView(app) {
    app.component('clinic-authors-view', AuthorsView);
}
