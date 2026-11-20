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
import { CHILD_SCOPE } from './compose-users.js';

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
 * Framework-free state for the list. Status: idle | loading | ready | denied | error.
 * @param {{client: {request: Function}, reactive?: Function}} deps
 */
export function createAuthorsController(deps) {
    const client = deps.client;
    const reactive = deps.reactive || ((x) => x);
    const accounts = createAccountsAdmin({ client, reactive });
    const state = reactive({ status: 'idle', rows: [], epoch: 0, message: '' });
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

    return { state, load, retry: load };
}

export function statusStyle(kind) {
    return style(kind);
}

export const AUTHORS_TEMPLATE = `
<section class="clinic-authors-view space-y-4" aria-labelledby="authors-title">
  <header class="flex flex-wrap items-start justify-between gap-3">
    <div>
      <h2 id="authors-title" class="text-lg font-semibold text-[var(--text-primary)]">{{ labels.title }}</h2>
      <p class="mt-1 text-sm text-[var(--text-muted)]">Diktierende Personen mit Anzeigename, Kontostatus und Poolzugehörigkeit.</p>
    </div>
    <button type="button" class="btn btn--sm btn--ghost" :disabled="state.status === 'loading'" @click="controller.load()">{{ labels.reload }}</button>
  </header>
  <p v-if="state.status === 'loading'" role="status" aria-live="polite" class="text-sm text-[var(--text-muted)]">{{ labels.loading }}</p>
  <p v-if="state.status === 'denied'" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ state.message }}</p>
  <p v-if="state.status === 'error'" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ state.message }}
    <button type="button" class="btn btn--sm btn--ghost ml-2" @click="controller.retry()">{{ labels.retry }}</button></p>
  <template v-if="state.status === 'ready'">
    <p v-if="!state.rows.length" role="status" aria-live="polite" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ labels.empty }}</p>
    <div v-else class="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
      <table :aria-label="labels.list" class="min-w-[44rem] divide-y divide-[var(--border-primary)] text-sm">
        <thead class="bg-[var(--bg-tertiary)]"><tr>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.name }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.id }}</th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.status }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.role }}</th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.authorPools }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ labels.columns.staffPools }}</th>
        </tr></thead>
        <tbody class="divide-y divide-[var(--border-primary)] bg-[var(--bg-secondary)]">
          <tr v-for="r in state.rows" :key="r.id" :data-user-id="r.id" class="hover:bg-[var(--bg-tertiary)]">
            <th scope="row" class="whitespace-nowrap px-4 py-3 text-left text-sm font-medium text-[var(--text-primary)]">{{ r.name }}</th>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.id }}</td>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)]"><span :style="surfaceStyle(r.surface)" class="inline-flex items-center rounded-full border px-2 text-xs font-semibold leading-5">{{ r.statusLabel }}</span></td>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.roleLabel }}</td>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)] min-w-[16rem]">
              <div class="flex flex-wrap gap-1">
                <span v-for="p in r.authorPools" :key="p.id" class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-2.5 py-0.5 text-xs text-[var(--text-primary)]">{{ p.name }}</span>
              </div>
              <span v-if="r.multiAuthorPool" role="note" class="mt-1 block text-xs text-[var(--text-muted)]">{{ labels.multiMembership }}</span>
            </td>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)]">
              <div class="flex flex-wrap gap-1">
                <span v-for="p in r.staffPools" :key="p.id" class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-2.5 py-0.5 text-xs text-[var(--text-primary)]">{{ p.name }}</span>
              </div>
              <span v-if="r.noStaffPool" class="text-[var(--text-muted)]">{{ labels.noStaffPool }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <section aria-labelledby="authors-pools" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
      <h3 id="authors-pools" class="text-base font-semibold text-[var(--text-primary)]">{{ labels.pools }}</h3>
      <p class="mt-1 mb-3 text-sm text-[var(--text-muted)]">{{ labels.poolsHelp }}</p>
      <div class="${CHILD_SCOPE}">
        <component :is="poolsComponent" :key="state.epoch"></component>
      </div>
    </section>
    <section aria-labelledby="authors-rules" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
      <h3 id="authors-rules" class="text-base font-semibold text-[var(--text-primary)]">{{ labels.rules }}</h3>
      <ul class="mt-2 list-disc space-y-1 pl-5 text-sm text-[var(--text-secondary)]"><li>{{ labels.ruleOnePool }}</li><li>{{ labels.ruleNoPerson }}</li></ul>
    </section>
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
