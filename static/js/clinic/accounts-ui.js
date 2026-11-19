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

import { surfaceFor } from './tokens.js';

export const LOGIN_POLICIES = ['local_only', 'local_and_entra', 'entra_only'];
export const SELECTABLE_POLICIES = ['local_only'];
const BASE = '/admin/users';

export const LABELS = Object.freeze({
    title: 'Konten der Autoren und Schreibkräfte',
    list: 'Konten',
    create: 'Konto anlegen',
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
    policies: { local_only: 'Nur lokal', local_and_entra: 'Lokal und Entra', entra_only: 'Nur Entra' },
    errors: {
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
        is_admin: !!row.is_admin,
        is_identity_admin: !!row.is_identity_admin,
        status: row.status || 'active',
        login_policy: row.login_policy || 'local_only',
        version: row.version,
    };
}

const EDITABLE = ['username', 'email', 'status', 'login_policy', 'is_admin', 'is_identity_admin'];

/** Returns an error key or null for one draft. */
export function validateDraft(draft) {
    if (!String(draft.username ?? '').trim()) return 'username_required';
    if (!String(draft.email ?? '').trim()) return 'email_required';
    return null;
}

/** Only the fields that differ from the loaded account; never a password or clinical role. */
export function changesOf(draft, base) {
    const out = {};
    for (const key of EDITABLE) {
        const now = typeof draft[key] === 'string' ? draft[key].trim() : draft[key];
        if (now !== base[key]) out[key] = now;
    }
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
        if (recovery && res.status === 403) return fail('admin_recovery_forbidden');
        return fail(LABELS.errors[res.outcome] ? res.outcome : 'UNKNOWN');
    }

    function account(id) {
        return state.accounts.find((a) => a.id === id) || null;
    }

    function isDirty() {
        if (!state.draft) return false;
        if (state.draft.mode === 'create') {
            return !!(state.draft.username || state.draft.email || state.draft.name);
        }
        return Object.keys(changesOf(state.draft, state.base)).length > 0;
    }

    async function load() {
        state.loading = true;
        clearMessage();
        try {
            const res = await client.request(BASE);
            if (res.outcome !== 'ok') return failFromResult(res);
            const users = res.data && Array.isArray(res.data.users) ? res.data.users : [];
            state.accounts = users.map(fromRow);
            state.loaded = true;
            return true;
        } finally {
            state.loading = false;
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
        await load();
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
                if (String(draft.name ?? '').trim()) body.name = draft.name.trim();
                const res = await client.request(BASE, { method: 'POST', body });
                if (res.outcome !== 'ok') {
                    if (res.outcome === 'conflict') {
                        await load();
                        return fail('conflict');
                    }
                    return failFromResult(res);
                }
                await load();
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
                if (res.outcome === 'conflict') await conflictReload(draft.id);
                else failFromResult(res);
                return false;
            }
            await load();
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
                if (res.outcome === 'conflict') await conflictReload(state.draft && state.draft.id === id ? id : null);
                else failFromResult(res);
                return false;
            }
            await load();
            return true;
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
                if (res.outcome === 'conflict') await conflictReload(state.draft && state.draft.id === id ? id : null);
                else failFromResult(res, true);
                return false;
            }
            state.recoveryCode = res.data && res.data.code ? String(res.data.code) : null;
            await load();
            return true;
        } finally {
            state.busy = false;
        }
    }

    return { state, load, startCreate, startEdit, cancel, apply, confirm, setStatus, issueRecovery, dismissCode, isDirty };
}

const ERROR_SURFACE = surfaceFor('error');
const BOX = 'border:1px solid var(--border-primary);background:var(--bg-secondary);color:var(--text-primary);';

/**
 * Vue component. `controller` is the result of createAccountsAdmin made with
 * Vue's `reactive`. All controls are native elements, so Tab, Enter and Space
 * behave as the browser defines; Escape in the editor cancels. Errors use
 * role="alert", status changes are announced through a polite live region.
 */
export const AccountsAdminPanel = {
    name: 'AccountsAdminPanel',
    props: { controller: { type: Object, required: true } },
    data() {
        return {
            labels: LABELS,
            policies: LOGIN_POLICIES,
            selectable: SELECTABLE_POLICIES,
            boxStyle: BOX,
            errorStyle: `background:${ERROR_SURFACE.background};color:${ERROR_SURFACE.color};border:1px solid ${ERROR_SURFACE.border};`,
        };
    },
    computed: {
        s() { return this.controller.state; },
    },
    mounted() { if (!this.s.loaded) this.controller.load(); },
    template: `
<section aria-labelledby="acc-title" :style="boxStyle" :aria-busy="s.loading || s.busy ? 'true' : 'false'">
  <h2 id="acc-title">{{ labels.title }}</h2>
  <p>{{ labels.noImplicitAccess }}</p>
  <p v-if="s.error" role="alert" :style="errorStyle">{{ s.message }}
    <button type="button" v-if="s.error === 'network' || s.error === 'UNKNOWN'" @click="controller.load()">{{ labels.reload }}</button>
  </p>
  <div v-if="s.recoveryCode" role="status">
    <p>{{ labels.recoveryCode }}</p>
    <output><code>{{ s.recoveryCode }}</code></output>
    <button type="button" @click="controller.dismissCode()">{{ labels.dismissCode }}</button>
  </div>
  <button type="button" :disabled="s.busy" @click="controller.startCreate()">{{ labels.create }}</button>
  <table :aria-label="labels.list">
    <thead><tr>
      <th scope="col">{{ labels.username }}</th><th scope="col">{{ labels.email }}</th>
      <th scope="col">{{ labels.status }}</th><th scope="col">{{ labels.loginPolicy }}</th>
      <th scope="col"><span class="sr-only">{{ labels.edit }}</span></th>
    </tr></thead>
    <tbody>
      <tr v-for="a in s.accounts" :key="a.id">
        <th scope="row">{{ a.username }}</th>
        <td>{{ a.email }}</td>
        <td>{{ labels.statuses[a.status] || a.status }}</td>
        <td>{{ labels.policies[a.login_policy] || a.login_policy }}</td>
        <td>
          <button type="button" :aria-label="labels.edit + ': ' + a.username" @click="controller.startEdit(a.id)">{{ labels.edit }}</button>
          <button type="button" v-if="a.status === 'active'" :disabled="s.busy" :aria-label="labels.disable + ': ' + a.username" @click="controller.setStatus(a.id, 'disabled')">{{ labels.disable }}</button>
          <button type="button" v-if="a.status === 'disabled'" :disabled="s.busy" :aria-label="labels.reactivate + ': ' + a.username" @click="controller.setStatus(a.id, 'active')">{{ labels.reactivate }}</button>
          <button type="button" :disabled="s.busy" :aria-label="labels.recovery + ': ' + a.username" @click="controller.issueRecovery(a.id)">{{ labels.recovery }}</button>
        </td>
      </tr>
    </tbody>
  </table>
  <form v-if="s.draft" @submit.prevent="controller.confirm()" @keydown.esc.prevent="controller.cancel()" novalidate>
    <label for="acc-username">{{ labels.username }}</label>
    <input id="acc-username" type="text" autocomplete="off" maxlength="20" v-model="s.draft.username"
           :aria-invalid="s.error === 'username_required' ? 'true' : 'false'">
    <label for="acc-email">{{ labels.email }}</label>
    <input id="acc-email" type="email" autocomplete="off" inputmode="email" v-model="s.draft.email"
           :aria-invalid="s.error === 'email_required' ? 'true' : 'false'">
    <template v-if="s.draft.mode === 'create'">
      <label for="acc-name">{{ labels.name }}</label>
      <input id="acc-name" type="text" autocomplete="off" maxlength="100" v-model="s.draft.name">
    </template>
    <template v-else>
      <label for="acc-policy">{{ labels.loginPolicy }}</label>
      <select id="acc-policy" v-model="s.draft.login_policy" aria-describedby="acc-entra">
        <option v-for="p in policies" :key="p" :value="p" :disabled="!selectable.includes(p) && p !== s.base.login_policy">{{ labels.policies[p] }}</option>
      </select>
      <p id="acc-entra">{{ labels.entraNote }}</p>
      <label><input type="checkbox" v-model="s.draft.is_admin"> {{ labels.isAdmin }}</label>
      <label><input type="checkbox" v-model="s.draft.is_identity_admin"> {{ labels.isIdentityAdmin }}</label>
    </template>
    <button type="button" :disabled="s.busy" @click="controller.apply()">{{ labels.apply }}</button>
    <button type="submit" :disabled="s.busy">{{ labels.ok }}</button>
    <button type="button" @click="controller.cancel()">{{ labels.cancel }}</button>
  </form>
</section>`,
};
