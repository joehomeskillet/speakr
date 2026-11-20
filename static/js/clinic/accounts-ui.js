/**
 * Author and typist account administration (PAR-ADM-001, PAR-ADM-008).
 *
 * `createAccountsAdmin` is a composable around the clinic API client
 * (api-client.js). Pass Vue's `reactive` to make the state reactive. Account
 * rows, drafts and one-time recovery codes stay in memory only; nothing is
 * written to browser storage.
 *
 * Server contract used here (src/api/admin.py, CLINIC_MODE):
 *   GET  /admin/users                 -> {users: [{id, username, email, is_admin,
 *                                          status, login_policy, is_identity_admin, version}]}
 *   POST /admin/users                 {username, email, name?}
 *   PUT  /admin/users/<id>            {expected_version, status?, login_policy?,
 *                                      is_admin?, is_identity_admin?, username?, email?}
 *   PUT  /admin/users/<id>            {expected_version, issue_recovery: true} -> {code, expires_at}
 * The admin routes sit outside /api/clinic/v1, so the mounting package
 * supplies a `client` whose request() reaches them. The module never sends
 * a password, a clinical role, a pool or a grant: identity roles confer no
 * implicit dictation access.
 */

import { MISSING_POLICY_FILTER, PAGE_SIZE, readAccountPage, readDirectory } from './account-pages.js';

export const LOGIN_POLICIES = ['local_only', 'local_and_entra', 'entra_only'];
export const SELECTABLE_POLICIES = ['local_only'];
const BASE = '/admin/users';

export const LABELS = Object.freeze({
    title: 'Konten der Autoren und Schreibkräfte',
    description: 'Anmeldekonten anlegen, bearbeiten, sperren und Einmalcodes ausstellen. Diktate und Pools werden hier nicht vergeben.',
    list: 'Konten',
    create: 'Konto anlegen',
    createTitle: 'Neues Konto',
    editTitle: 'Konto bearbeiten',
    empty: 'Noch keine Konten vorhanden.',
    loading: 'Konten werden geladen …',
    roles: 'Rollen',
    roleAdmin: 'Administration',
    roleIdentity: 'Identitätsadministration',
    noRole: 'Keine',
    edit: 'Bearbeiten',
    disable: 'Deaktivieren',
    reactivate: 'Reaktivieren',
    recovery: 'Einmalcode ausstellen',
    recoveryCode: 'Einmalcode (wird nur jetzt angezeigt)',
    dismissCode: 'Code ausblenden',
    username: 'Benutzername',
    email: 'E-Mail-Adresse',
    name: 'Anzeigename',
    status: 'Status',
    loginPolicy: 'Anmelderichtlinie',
    isAdmin: 'Administrationsrolle',
    isIdentityAdmin: 'Identitätsadministration',
    apply: 'Übernehmen',
    ok: 'OK',
    cancel: 'Abbrechen',
    reload: 'Erneut laden',
    retry: 'Erneut versuchen',
    noImplicitAccess: 'Identitätsrollen geben keinen Zugriff auf Diktate. Fachrechte werden separat erteilt.',
    entraNote: 'Entra ist vorbereitet, aber noch nicht aktiviert.',
    statuses: { active: 'Aktiv', disabled: 'Deaktiviert', pending_activation: 'Aktivierung ausstehend' },
    policies: { local_only: 'Nur lokal', local_and_entra: 'Lokal und Entra', entra_only: 'Nur Entra',
        device_only: 'Nur Gerät', [MISSING_POLICY_FILTER]: 'Keine Anmelderichtlinie' },
    errors: {
        duplicate: 'Benutzername oder E-Mail-Adresse ist bereits vergeben.',
        last_local_identity_admin: 'Zuerst eine weitere lokale Identitätsadministration aktivieren.',
        reauth_required: 'Bitte erneut anmelden und die Änderung nochmals ausführen.',
        recovery_not_permitted: 'Für dieses Konto ist kein lokaler Einmalcode möglich.',
        no_account_policy: 'Für dieses Konto fehlt eine Anmelderichtlinie.',
        invalid_profile: 'Bitte die Profilwerte prüfen. Grenzen müssen ganze positive Zahlen sein.',
        login_policy_not_permitted: 'Diese Anmelderichtlinie ist für das Konto nicht verfügbar.',
        username_required: 'Bitte einen Benutzernamen eingeben.',
        email_required: 'Bitte eine E-Mail-Adresse eingeben.',
        admin_recovery_forbidden: 'Administratorkonten werden nur über die Serverkonsole wiederhergestellt. Ein Einmalcode kann hier nicht ausgestellt werden.',
        conflict: 'Das Konto wurde inzwischen geändert oder die Änderung ist nicht möglich. Der aktuelle Stand wurde geladen; Ihre Eingaben wurden verworfen.',
        permission: 'Keine Berechtigung für diese Aktion.',
        network: 'Die Verbindung ist fehlgeschlagen. Bitte erneut versuchen.',
        UNKNOWN: 'Das Ergebnis ist unbekannt. Bitte Konten neu laden und prüfen.',
        cancelled: 'Die Anfrage wurde abgebrochen.',
        validation: 'Die Eingabe ist ungültig.',
    },
});

function fromRow(row) {
    return {
        id: row.id,
        username: row.username || '',
        email: row.email || '',
        name: row.name || '',
        storage_quota_mb: row.storage_quota_mb ?? null,
        monthly_token_budget: row.monthly_token_budget ?? null,
        monthly_transcription_budget: row.monthly_transcription_budget ?? null,
        group_grants_available: row.group_grants_available === true,
        create_groups_granted: !!row.create_groups_granted,
        login_locked: !!row.login_locked,
        last_seen_at: row.last_seen_at || null,
        recordings_count: row.recordings_count ?? 0,
        storage_used: row.storage_used ?? 0,
        current_token_usage: row.current_token_usage ?? 0,
        current_transcription_usage_minutes: row.current_transcription_usage_minutes ?? 0,
        is_admin: !!row.is_admin,
        is_identity_admin: !!row.is_identity_admin,
        status: row.status || 'active',
        login_policy: row.login_policy || null,
        version: row.version,
    };
}

const LIMITS = [
    { key: 'storage_quota_mb', label: 'Speicherlimit (MB)' },
    { key: 'monthly_token_budget', label: 'Tokenlimit pro Monat' },
    { key: 'monthly_transcription_budget', label: 'Transkriptionslimit pro Monat (Sekunden)' },
];
const EDITABLE = ['username', 'email', 'name', 'status', 'login_policy', 'is_admin', 'is_identity_admin'];

/** Returns an error key or null for one draft. */
export function validateDraft(draft) {
    if (!String(draft.username ?? '').trim()) return 'username_required';
    if (!String(draft.email ?? '').trim()) return 'email_required';
    for (const { key } of LIMITS) {
        const raw = draft[key];
        if (raw == null || raw === '') continue;
        const n = Number(raw);
        if (!Number.isSafeInteger(n) || n < 1 || n > 2147483647) return 'invalid_profile';
    }
    return null;
}

/** Only the fields that differ from the loaded account; never a password or clinical role. */
export function changesOf(draft, base) {
    const out = {};
    for (const key of EDITABLE) {
        const now = typeof draft[key] === 'string' ? draft[key].trim() : draft[key];
        if (now !== base[key]) out[key] = now;
    }
    for (const { key } of LIMITS) {
        const value = draft[key] == null || draft[key] === '' ? null : Number(draft[key]);
        if (value !== (base[key] ?? null)) out[key] = value;
    }
    if (base.group_grants_available && !draft.is_admin && draft.create_groups_granted !== base.create_groups_granted) {
        out.create_groups_granted = draft.create_groups_granted;
    }
    if (base.login_locked && draft.unlock_login) out.unlock_login = true;
    return out;
}

/**
 * @param {{client: {request: Function}, reactive?: Function}} options
 */
export function createAccountsAdmin(options) {
    const client = options && options.client;
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    const reactive = options.reactive || ((x) => x);

    const state = reactive({
        accounts: [],
        table: { users: [], page: 1, pages: 1, total: 0 },
        tableLoading: false,
        filters: {},
        draft: null, // {mode: 'create'|'edit', id, version, username, ...}
        base: null, // loaded server values of the draft, for dirty tracking
        loading: false,
        busy: false,
        loaded: false,
        error: null,
        message: '',
        recoveryCode: null,
    });

    function fail(key) {
        state.error = key;
        state.message = LABELS.errors[key] || LABELS.errors.UNKNOWN;
        return false;
    }

    function clearMessage() {
        state.error = null;
        state.message = '';
    }

    function failFromResult(res, recovery) {
        if (res.code && Object.hasOwn(LABELS.errors, res.code)) return fail(res.code);
        if (recovery && !res.code && res.status === 403) return fail('admin_recovery_forbidden');
        return fail(LABELS.errors[res.outcome] ? res.outcome : 'UNKNOWN');
    }

    function account(id) {
        return state.table.users.find((a) => a.id === id) || state.accounts.find((a) => a.id === id) || null;
    }

    function isDirty() {
        if (!state.draft) return false;
        if (state.draft.mode === 'create') {
            return !!(state.draft.username || state.draft.email || state.draft.name || LIMITS.some(({ key }) => state.draft[key]));
        }
        return Object.keys(changesOf(state.draft, state.base)).length > 0;
    }

    let loadId = 0;
    let pageId = 0;
    let requestedPage = 1;

    async function browse(page = 1, nextFilters = state.filters) {
        state.filters = nextFilters;
        requestedPage = page;
        const id = ++pageId;
        state.tableLoading = true;
        const res = await readAccountPage(client, page, nextFilters);
        if (id !== pageId) return false;
        state.tableLoading = false;
        clearMessage();
        if (res.outcome !== 'ok') {
            state.table = { users: [], page: 1, pages: 1, total: 0 };
            return failFromResult(res);
        }
        state.table = { ...res.data, users: res.data.users.map(fromRow) };
        requestedPage = state.table.page;
        return true;
    }

    async function load() {
        const id = ++loadId;
        state.loading = true;
        clearMessage();
        try {
            const res = await readDirectory(client);
            if (id !== loadId) return false;
            if (res.outcome !== 'ok') {
                state.loaded = false;
                state.accounts = [];
                state.table = { users: [], page: 1, pages: 1, total: 0 };
                return failFromResult(res);
            }
            state.accounts = res.data.users.map(fromRow);
            if (!Object.values(state.filters).some(Boolean) && requestedPage === 1) {
                const total = state.accounts.length;
                state.table = { users: state.accounts.slice(0, PAGE_SIZE), page: 1,
                    pages: Math.max(1, Math.ceil(total / PAGE_SIZE)), total };
            } else if (!await browse(requestedPage)) {
                return false;
            }
            state.loaded = true;
            return true;
        } finally {
            if (id === loadId) state.loading = false;
        }
    }

    function startCreate() {
        clearMessage();
        state.recoveryCode = null;
        state.base = null;
        state.draft = { mode: 'create', id: null, version: null, username: '', email: '', name: '' };
    }

    function startEdit(id) {
        const row = account(id);
        if (!row) return false;
        clearMessage();
        state.recoveryCode = null;
        state.base = { ...row };
        state.draft = { mode: 'edit', ...row };
        return true;
    }

    /** Cancel discards every unsaved edit and the shown one-time code. */
    function cancel() {
        state.draft = null;
        state.base = null;
        state.recoveryCode = null;
        clearMessage();
    }

    function dismissCode() {
        state.recoveryCode = null;
    }

    async function conflictReload(id) {
        if (!await load()) return false;
        const row = id == null ? null : account(id);
        if (row) {
            state.base = { ...row };
            state.draft = { mode: 'edit', ...row };
        }
        fail('conflict');
    }

    async function save() {
        const draft = state.draft;
        if (!draft) return false;
        const bad = validateDraft(draft);
        if (bad) return fail(bad);
        state.busy = true;
        clearMessage();
        try {
            if (draft.mode === 'create') {
                const body = { username: draft.username.trim(), email: draft.email.trim() };
                for (const { key } of LIMITS) {
                    if (draft[key] != null && draft[key] !== '') body[key] = Number(draft[key]);
                }
                if (String(draft.name ?? '').trim()) body.name = draft.name.trim();
                const res = await client.request(BASE, { method: 'POST', body });
                if (res.outcome !== 'ok') {
                    if (res.outcome === 'conflict' && (!res.code || res.code === 'version_conflict')) {
                        await load();
                        return fail('conflict');
                    }
                    return failFromResult(res);
                }
                if (!await load()) { state.draft = null; return false; }
                const created = res.data && account(res.data.id);
                if (created) {
                    state.base = { ...created };
                    state.draft = { mode: 'edit', ...created };
                }
                return true;
            }
            const changes = changesOf(draft, state.base);
            if (!Object.keys(changes).length) return true;
            const res = await client.request(BASE + '/' + encodeURIComponent(draft.id), {
                method: 'PUT', body: { expected_version: state.base.version, ...changes },
            });
            if (res.outcome !== 'ok') {
                if (res.outcome === 'conflict' && (!res.code || res.code === 'version_conflict')) await conflictReload(draft.id);
                else failFromResult(res);
                return false;
            }
            if (!await load()) { state.draft = null; return false; }
            const row = account(draft.id);
            if (row) {
                state.base = { ...row };
                state.draft = { mode: 'edit', ...row };
            }
            return true;
        } finally {
            state.busy = false;
        }
    }

    /** Apply: save and stay open. */
    async function apply() {
        return save();
    }

    /** OK: save and close; stays open when saving fails. */
    async function confirm() {
        const saved = await save();
        if (saved) cancel();
        return saved;
    }

    /** Disable or reactivate a listed account in one step. */
    async function setStatus(id, status) {
        const row = account(id);
        if (!row || !['active', 'disabled'].includes(status)) return false;
        state.busy = true;
        clearMessage();
        try {
            const res = await client.request(BASE + '/' + encodeURIComponent(id), {
                method: 'PUT', body: { expected_version: row.version, status },
            });
            if (res.outcome !== 'ok') {
                if (res.outcome === 'conflict' && (!res.code || res.code === 'version_conflict')) await conflictReload(state.draft && state.draft.id === id ? id : null);
                else failFromResult(res);
                return false;
            }
            return await load();
        } finally {
            state.busy = false;
        }
    }

    /** One-time recovery code. An admin account is refused here and by the server (403). */
    async function issueRecovery(id) {
        const row = account(id);
        if (!row) return false;
        state.recoveryCode = null;
        if (row.is_admin) return fail('admin_recovery_forbidden');
        state.busy = true;
        clearMessage();
        try {
            const res = await client.request(BASE + '/' + encodeURIComponent(id), {
                method: 'PUT', body: { expected_version: row.version, issue_recovery: true },
            });
            if (res.outcome !== 'ok') {
                if (res.outcome === 'conflict' && (!res.code || res.code === 'version_conflict')) await conflictReload(state.draft && state.draft.id === id ? id : null);
                else failFromResult(res, true);
                return false;
            }
            state.recoveryCode = res.data && res.data.code ? String(res.data.code) : null;
            return await load();
        } finally {
            state.busy = false;
        }
    }

    return { state, load, browse, startCreate, startEdit, cancel, apply, confirm, setStatus, issueRecovery, dismissCode, isDirty };
}

/**
 * Vue component. `controller` is the result of createAccountsAdmin made with
 * Vue's `reactive`. All controls are native elements, so Tab, Enter and Space
 * behave as the browser defines; Escape in the editor cancels. Errors use
 * role="alert", status changes are announced through a polite live region.
 * Layout classes (cadm-*) live in static/css/clinic-admin.css; controls use the primitives .btn and .field.
 */
export const AccountsAdminPanel = {
    name: 'AccountsAdminPanel',
    props: { controller: { type: Object, required: true }, embedded: Boolean },
    data() {
        const filters = this.controller.state.filters;
        return {
            query: filters.search || '',
            role: filters.role || '',
            status: filters.status || '',
            sortOrder: (filters.sort || 'username') + ':' + (filters.order || 'asc'),
            opener: null,
            filtersOpen: false,
            expandedAccount: null,
            limits: LIMITS,
            source: filters.source || '',
            labels: LABELS,
            policies: LOGIN_POLICIES,
            selectable: SELECTABLE_POLICIES,
            statusClass: { active: 'pill-status--success', disabled: 'pill-status--warn', pending_activation: 'pill-status--pending' },
        };
    },
    computed: {
        filterActive() { return Boolean(this.query || this.role || this.status || this.source || this.sortOrder !== 'username:asc'); },
        availablePolicies() {
            const values = this.s.accounts.map(account => account.login_policy || MISSING_POLICY_FILTER);
            if (this.source) values.push(this.source);
            return [...new Set(values)];
        },
        s() { return this.controller.state; },
        rows() { return this.s.table.users; },
    },
    watch: {
        's.draft'(draft) {
            if (draft || !this.opener) return;
            const opener = this.opener;
            this.opener = null;
            this.$nextTick(() => { if (opener.isConnected) opener.focus(); });
        },
    },
    methods: {
        openEditor(event, id) {
            if (id == null) this.controller.startCreate();
            else if (!this.controller.startEdit(id)) return;
            this.opener = event.currentTarget;
            this.$nextTick(() => this.$refs.username?.focus());
        },
        browse(page = 1) {
            const [sort, order] = this.sortOrder.split(':');
            return this.controller.browse(page, {
                search: this.query.trim(), role: this.role, status: this.status, source: this.source, sort, order,
            });
        },
        resetFilters() {
            this.query = this.role = this.status = this.source = '';
            this.sortOrder = 'username:asc';
            return this.browse();
        },
        policyLabel(policy) { return LABELS.policies[policy || MISSING_POLICY_FILTER] || policy; },
        seen(value) { return value ? new Date(value).toLocaleString('de-CH') : 'Noch nie'; },
    },
    async mounted() {
        if (!this.s.loaded && !await this.controller.load()) return;
        if (!this.s.filters.sort) await this.browse();
    },
    template: `
<section class="cadm-panel" aria-labelledby="acc-title" :aria-busy="s.loading || s.busy ? 'true' : 'false'">
  <header class="cadm-panel__head">
    <div>
      <h2 id="acc-title" class="cadm-title">{{ embedded ? 'Lokale Konten' : labels.title }}</h2>
      <p v-if="!embedded" class="cadm-desc">{{ labels.description }}</p>
    </div>
    <div v-if="!embedded" class="cadm-panel__tools">
      <button type="button" class="btn btn--primary" :disabled="s.busy" @click="openEditor($event)">
        <i class="fas fa-user-plus" aria-hidden="true"></i><span>{{ labels.create }}</span>
      </button>
    </div>
  </header>
  <p v-if="!embedded" class="banner banner--info"><i class="fas fa-info-circle" aria-hidden="true"></i><span>{{ labels.noImplicitAccess }}</span></p>
  <div v-if="s.error" role="alert" class="banner banner--danger">
    <i class="fas fa-exclamation-triangle" aria-hidden="true"></i><span>{{ s.message }}</span>
    <button type="button" class="btn btn--sm btn--ghost" v-if="s.error === 'network' || s.error === 'UNKNOWN'" @click="controller.load()">{{ labels.reload }}</button>
  </div>
  <div v-if="s.recoveryCode" role="status" class="cadm-code">
    <p>{{ labels.recoveryCode }}</p>
    <output><code>{{ s.recoveryCode }}</code></output>
    <button type="button" class="btn btn--sm btn--ghost" @click="controller.dismissCode()">{{ labels.dismissCode }}</button>
  </div>
  <div v-if="s.loaded" class="filterbar cadm-account-filters" :class="{ 'filters-open': filtersOpen }">
    <label class="field search"><span>Suche</span><input type="search" :value="query" @input="query = $event.target.value; browse()" placeholder="Anmeldename oder E-Mail"></label>
    <button type="button" class="btn btn--ghost cadm-mobile-filter" :aria-expanded="filtersOpen"
            aria-controls="acc-role-filter acc-status-filter acc-source-filter acc-sort-filter" @click="filtersOpen = !filtersOpen">
      <i class="fas fa-sliders" aria-hidden="true"></i>Filter und Sortierung<span v-if="role || status || source || sortOrder !== 'username:asc'"> · aktiv</span>
    </button>
    <label id="acc-role-filter" class="field"><span id="acc-role-label">Rolle</span><select aria-labelledby="acc-role-label" v-model="role" @change="browse()">
      <option value="">Alle Rollen</option><option value="is_admin">{{ labels.roleAdmin }}</option>
      <option value="is_identity_admin">{{ labels.roleIdentity }}</option><option value="none">Keine Administrationsrolle</option>
    </select></label>
    <label id="acc-status-filter" class="field"><span id="acc-status-label">{{ labels.status }}</span><select aria-labelledby="acc-status-label" v-model="status" @change="browse()">
      <option value="">Alle Status</option><option v-for="(title, key) in labels.statuses" :key="key" :value="key">{{ title }}</option>
    </select></label>
    <label id="acc-source-filter" class="field"><span id="acc-source-label">Anmelderichtlinie</span><select aria-labelledby="acc-source-label" v-model="source" @change="browse()">
      <option value="">Alle Richtlinien</option><option v-for="policy in availablePolicies" :key="policy" :value="policy">{{ policyLabel(policy) }}</option>
    </select></label>
    <label id="acc-sort-filter" class="field"><span id="acc-sort-label">Sortierung</span><select aria-labelledby="acc-sort-label" v-model="sortOrder" @change="browse()">
      <optgroup label="Benutzername"><option value="username:asc">Benutzername A–Z</option><option value="username:desc">Benutzername Z–A</option></optgroup>
      <optgroup label="E-Mail"><option value="email:asc">E-Mail A–Z</option><option value="email:desc">E-Mail Z–A</option></optgroup>
      <optgroup label="Kontonummer"><option value="id:asc">Niedrigste Kontonummer</option><option value="id:desc">Höchste Kontonummer</option></optgroup>
      <optgroup label="Administration"><option value="is_admin:desc">Administratoren zuerst</option><option value="is_admin:asc">Administratoren zuletzt</option></optgroup>
      <optgroup label="Aktivität"><option value="last_active:desc">Zuletzt aktiv</option><option value="last_active:asc">Längste Inaktivität</option></optgroup>
      <optgroup label="Diktate"><option value="recordings_count:desc">Meiste Diktate</option><option value="recordings_count:asc">Wenigste Diktate</option></optgroup>
      <optgroup label="Speicher"><option value="storage_used:desc">Grösster Speicherverbrauch</option><option value="storage_used:asc">Geringster Speicherverbrauch</option></optgroup>
    </select></label>
  </div>
  <button v-if="s.loaded && filterActive" type="button" class="btn btn--sm btn--ghost" @click="resetFilters">Filter zurücksetzen</button>
  <p v-if="s.loaded" role="status" class="hint">{{ s.table.total }} {{ s.table.total === 1 ? 'Konto' : 'Konten' }}</p>
  <div v-if="rows.length" class="cadm-tablewrap cadm-tablewrap--stack">
    <table class="cadm-table cadm-table--stack cadm-account-table" :aria-label="labels.list">
      <thead><tr>
        <th scope="col">{{ labels.username }}</th><th scope="col">{{ labels.email }}</th>
        <th scope="col">{{ labels.roles }}</th>
        <th scope="col">{{ labels.status }}</th><th scope="col">{{ labels.loginPolicy }}</th>
        <th scope="col" class="cadm-col-actions"><span class="sr-only">{{ labels.edit }}</span></th>
      </tr></thead>
      <tbody>
        <tr v-for="a in rows" :key="a.id" :class="{ 'account-open': expandedAccount === a.id }">
          <th scope="row">
            <strong class="cadm-account-name">{{ a.username }}</strong>
            <button type="button" class="cadm-account-disclosure" :aria-label="'Kontodetails: ' + a.username"
                    :aria-expanded="expandedAccount === a.id"
                    :aria-controls="['email','roles','policy','actions'].map(key => 'acc-' + a.id + '-' + key).join(' ')"
                    @click="expandedAccount = expandedAccount === a.id ? null : a.id">
              <i class="fas fa-chevron-right" aria-hidden="true"></i><strong>{{ a.username }}</strong>
            </button>
            <small v-if="a.name && a.name !== a.username">{{ a.name }}</small>
          </th>
          <td :id="'acc-' + a.id + '-email'" class="cadm-cell--wide" :data-label="labels.email">{{ a.email }}</td>
          <td :id="'acc-' + a.id + '-roles'" :data-label="labels.roles">
            <span class="cadm-badges">
              <span v-if="a.is_admin" class="pill-status pill-status--info">{{ labels.roleAdmin }}</span>
              <span v-if="a.is_identity_admin" class="pill-status pill-status--processing">{{ labels.roleIdentity }}</span>
              <span v-if="!a.is_admin && !a.is_identity_admin" class="cadm-muted">{{ labels.noRole }}</span>
            </span>
          </td>
          <td class="cadm-account-status" :data-label="labels.status"><span class="pill-status" :class="statusClass[a.status] || 'pill-status--pending'">{{ labels.statuses[a.status] || a.status }}</span></td>
          <td :id="'acc-' + a.id + '-policy'" :data-label="labels.loginPolicy">{{ policyLabel(a.login_policy) }}</td>
          <td :id="'acc-' + a.id + '-actions'" class="cadm-col-actions">
            <span class="cadm-actions">
              <button type="button" class="btn btn--sm btn--ghost btn--icon" :title="labels.edit" :aria-label="labels.edit + ': ' + a.username" @click="openEditor($event, a.id)"><i class="fas fa-edit" aria-hidden="true"></i></button>
              <button type="button" class="btn btn--sm btn--ghost btn--icon" v-if="a.status === 'active'" :disabled="s.busy" :title="labels.disable" :aria-label="labels.disable + ': ' + a.username" @click="controller.setStatus(a.id, 'disabled')"><i class="fas fa-user-slash" aria-hidden="true"></i></button>
              <button type="button" class="btn btn--sm btn--ghost btn--icon" v-if="a.status === 'disabled'" :disabled="s.busy" :title="labels.reactivate" :aria-label="labels.reactivate + ': ' + a.username" @click="controller.setStatus(a.id, 'active')"><i class="fas fa-user-check" aria-hidden="true"></i></button>
              <button type="button" class="btn btn--sm btn--ghost btn--icon" :disabled="s.busy" :title="labels.recovery" :aria-label="labels.recovery + ': ' + a.username" @click="controller.issueRecovery(a.id)"><i class="fas fa-key" aria-hidden="true"></i></button>
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-else-if="s.loaded" class="cadm-empty">{{ s.accounts.length ? 'Keine passenden Konten.' : labels.empty }}</p>
  <p v-else-if="s.loading" role="status" class="cadm-empty">{{ labels.loading }}</p>
  <nav v-if="s.table.pages > 1" class="cadm-actions" aria-label="Kontenseiten">
    <button type="button" class="btn btn--sm btn--ghost" :disabled="s.tableLoading || s.table.page === 1" @click="browse(s.table.page - 1)">Zurück</button>
    <span aria-live="polite">Seite {{ s.table.page }} von {{ s.table.pages }}</span>
    <button type="button" class="btn btn--sm btn--ghost" :disabled="s.tableLoading || s.table.page >= s.table.pages" @click="browse(s.table.page + 1)">Weiter</button>
  </nav>
  <form v-if="s.draft" class="cadm-editor" aria-labelledby="acc-editor-title" @submit.prevent="controller.confirm()" @keydown.esc.prevent="controller.cancel()" novalidate>
    <h3 id="acc-editor-title" class="cadm-subtitle">{{ s.draft.mode === 'create' ? labels.createTitle : labels.editTitle + ': ' + s.base.username }}</h3>
    <div class="cadm-form">
      <div class="cadm-field">
        <label for="acc-username">{{ labels.username }}</label>
        <input id="acc-username" ref="username" class="field" type="text" autocomplete="off" maxlength="20" v-model="s.draft.username"
               :aria-invalid="s.error === 'username_required' ? 'true' : 'false'">
      </div>
      <div class="cadm-field">
        <label for="acc-email">{{ labels.email }}</label>
        <input id="acc-email" class="field" type="email" autocomplete="off" inputmode="email" v-model="s.draft.email"
               :aria-invalid="s.error === 'email_required' ? 'true' : 'false'">
      </div>
        <div class="cadm-field">
          <label for="acc-name">{{ labels.name }}</label>
          <input id="acc-name" class="field" type="text" autocomplete="off" maxlength="100" v-model="s.draft.name">
        </div>
      <div v-for="limit in limits" :key="limit.key" class="cadm-field">
        <label :for="'acc-' + limit.key">{{ limit.label }}</label>
        <input :id="'acc-' + limit.key" class="field" type="number" min="1" max="2147483647" step="1"
          :placeholder="s.draft.mode === 'create' && limit.key === 'storage_quota_mb' ? 'Systemstandard' : 'Unbegrenzt'" v-model="s.draft[limit.key]">
      </div>
      <template v-if="s.draft.mode === 'edit'">
        <div class="cadm-field cadm-field--wide">
          <p class="cadm-hint">Zuletzt aktiv: {{ seen(s.base.last_seen_at) }} · Diktate: {{ s.base.recordings_count }} · Speicher: {{ (s.base.storage_used / 1048576).toLocaleString('de-CH', { maximumFractionDigits: 1 }) }} MB</p>
          <p class="cadm-hint">Diesen Monat: {{ s.base.current_token_usage }} Token · {{ s.base.current_transcription_usage_minutes }} Minuten transkribiert</p>
          <label v-if="s.base.login_locked" class="cadm-check"><input type="checkbox" v-model="s.draft.unlock_login"> Anmeldesperre aufheben</label>
        </div>
        <div class="cadm-field">
          <label for="acc-policy">{{ labels.loginPolicy }}</label>
          <select id="acc-policy" class="field" v-model="s.draft.login_policy" :disabled="!s.base.login_policy" aria-describedby="acc-entra">
            <option v-if="!policies.includes(s.base.login_policy)" :value="s.base.login_policy" disabled>{{ policyLabel(s.base.login_policy) }}</option>
            <option v-for="p in policies" :key="p" :value="p" :disabled="!selectable.includes(p) && p !== s.base.login_policy">{{ labels.policies[p] }}</option>
          </select>
          <p id="acc-entra" class="cadm-hint">{{ labels.entraNote }}</p>
        </div>
        <fieldset class="cadm-fieldset cadm-field--wide">
          <legend>{{ labels.roles }}</legend>
          <label v-if="s.base.group_grants_available && !s.draft.is_admin" class="cadm-check"><input type="checkbox" v-model="s.draft.create_groups_granted"> Gruppen erstellen</label>
          <label class="cadm-check"><input type="checkbox" v-model="s.draft.is_admin"> {{ labels.isAdmin }}</label>
          <label class="cadm-check"><input type="checkbox" v-model="s.draft.is_identity_admin"> {{ labels.isIdentityAdmin }}</label>
        </fieldset>
      </template>
      <div class="cadm-form__actions">
        <button type="submit" class="btn btn--primary" :disabled="s.busy">{{ embedded ? 'Speichern und schließen' : labels.ok }}</button>
        <button type="button" class="btn btn--ghost" :disabled="s.busy" @click="controller.apply()">{{ labels.apply }}</button>
        <button type="button" class="btn btn--quiet" @click="controller.cancel()">{{ labels.cancel }}</button>
      </div>
    </div>
  </form>
</section>`,
};
