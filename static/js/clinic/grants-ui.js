/**
 * Action-scoped grants and deny exceptions (PAR-ADM-004/005/006/008).
 *
 * Scope (own, pool, all) and per-action allow/deny are shown separately. A
 * deny always wins over an allow, so the effective result of a draft change is
 * previewed before saving. Only the supported scopes, effects and actions are
 * accepted; anything else (for example detailed W7 role expansions or
 * statistics rights) is rejected locally. The existing team-member read and
 * team-admin edit semantics are not changed here: whether the actor may edit is
 * decided by the server and by the `canEdit` option, and the detailed owner
 * matrix stays pending.
 *
 * An allow for all dictations can name authors ("nur Alpha"). A pool allow can
 * name exclusions ("A ausser Beta"): the editor saves the pool allow and a
 * deny limited to those authors. Both are chosen by account name. The draft
 * stays on screen when the revision conflicts.
 *
 * Reads use GET, additions use POST, recipient choice uses PUT and revocations
 * use DELETE through the shared api client. Every write carries the revision it last read; a conflict
 * keeps the newer server state and never overwrites it. Grant data stays in
 * memory only (no browser storage). Colours come only from the token contract.
 */

import { surfaceFor } from './tokens.js';
import { createPoolsStore } from './pools-ui.js';

export const ACTIONS = Object.freeze(['view', 'play', 'edit', 'export', 'audio_export', 'release', 'archive']);
export const SCOPES = Object.freeze(['own', 'pool', 'all']);
export const EFFECTS = Object.freeze(['allow', 'deny']);
const RECIPIENT_CHOICES = Object.freeze(['default_only', 'staff_pools_only', 'open']);

const LABELS = {
    'clinic.grants.title': 'Berechtigungen',
    'clinic.grants.description': 'Fachrechte eines Benutzers: erlauben oder verweigern, je Geltungsbereich und Aktion. Eine Verweigerung gewinnt immer.',
    'clinic.grants.revision': 'Stand',
    'clinic.grants.scope': 'Geltungsbereich',
    'clinic.grants.scope.own': 'Eigene Diktate',
    'clinic.grants.scope.pool': 'Pool',
    'clinic.grants.scope.all': 'Alle Diktate',
    'clinic.grants.authorPool': 'Autorenpool',
    'clinic.grants.effect': 'Wirkung',
    'clinic.grants.effect.allow': 'Erlauben',
    'clinic.grants.effect.deny': 'Verweigern',
    'clinic.grants.actions': 'Aktionen',
    'clinic.grants.action.view': 'Ansehen',
    'clinic.grants.action.play': 'Abspielen',
    'clinic.grants.action.edit': 'Bearbeiten',
    'clinic.grants.action.export': 'Exportieren',
    'clinic.grants.action.release': 'Freigeben',
    'clinic.grants.action.delete': 'Löschen',
    'clinic.grants.action.archive': 'Archivieren',
    'clinic.grants.action.rename': 'Umbenennen',
    'clinic.grants.action.priority': 'Priorität ändern',
    'clinic.grants.action.audio_export': 'Originalaudio exportieren',
    'clinic.grants.action.reassign': 'Neu zuordnen',
    'clinic.grants.action.reopen': 'Wieder öffnen',
    'clinic.grants.effective': 'Wirksames Ergebnis nach dem Speichern',
    'clinic.grants.effective.allowed': 'erlaubt',
    'clinic.grants.effective.denied': 'verweigert',
    'clinic.grants.effective.none': 'nicht erlaubt',
    'clinic.grants.effective.overridden': 'Erlaubnis wird durch eine Verweigerung überschrieben',
    'clinic.grants.existing': 'Bestehende Einträge',
    'clinic.grants.empty': 'Keine Einträge',
    'clinic.grants.save': 'Speichern',
    'clinic.grants.recipientChoice': 'Empfängerwahl beim Versand',
    'clinic.grants.recipientChoice.default_only': 'Nur der Standardempfänger',
    'clinic.grants.recipientChoice.staff_pools_only': 'Nur Schreibteams',
    'clinic.grants.recipientChoice.open': 'Personen und Schreibteams',
    'clinic.grants.recipientChoice.save': 'Empfängerwahl übernehmen',
    'clinic.grants.recipientChoice.saved': 'Empfängerwahl gespeichert.',
    'clinic.grants.revoke': 'Entziehen',
    'clinic.grants.reload': 'Aktuellen Stand laden',
    'clinic.grants.retry': 'Erneut versuchen',
    'clinic.grants.readonly': 'Nur Lesezugriff. Änderungen sind nur durch Administration möglich.',
    'clinic.grants.stale': 'Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
    'clinic.grants.error.scope': 'Dieser Geltungsbereich wird nicht unterstützt.',
    'clinic.grants.error.effect': 'Diese Wirkung wird nicht unterstützt.',
    'clinic.grants.error.actions': 'Mindestens eine unterstützte Aktion wählen.',
    'clinic.grants.error.pool': 'Bitte einen Autorenpool wählen.',
    'clinic.grants.error.unsupported': 'Nicht unterstützte Angabe.',
    'clinic.grants.authors': 'Nur diese Autoren',
    'clinic.grants.authors.hint': 'Keine Auswahl gilt für alle Autoren.',
    'clinic.grants.authors.empty': 'Keine Benutzer für die Auswahl.',
    'clinic.grants.exceptions': 'Ausser diesen Autoren',
    'clinic.grants.exceptions.hint': 'Diese Autoren werden von der Pool-Erlaubnis ausgenommen.',
    'clinic.grants.only': 'Nur',
    'clinic.grants.except': 'Ausser',
    'clinic.grants.error.authors': 'Autoren nur bei «Alle Diktate» und Wirkung «Erlauben» wählen.',
    'clinic.grants.error.exceptions': 'Ausnahmen nur für einen Autorenpool wählen.',
    'clinic.grants.error.users': 'Die Benutzerliste konnte nicht geladen werden.',
};

export function label(key) {
    const i18n = typeof window !== 'undefined' ? window.i18n : undefined;
    if (i18n && typeof i18n.t === 'function') {
        const text = i18n.t(key);
        if (text && text !== key) return text;
    }
    return LABELS[key] || key;
}

const DRAFT_KEYS = ['effect', 'scope', 'poolId', 'actions', 'authorIds', 'exceptIds'];

/** Positive user ids. Numeric strings from a control are accepted; anything else is invalid. */
export function idList(value) {
    if (!Array.isArray(value)) return null;
    const ids = [];
    for (const raw of value) {
        const id = typeof raw === 'number' ? raw : (typeof raw === 'string' && /^[1-9]\d*$/.test(raw) ? Number(raw) : NaN);
        if (!Number.isInteger(id) || id < 1 || ids.includes(id)) return null;
        ids.push(id);
    }
    return ids;
}

/** Return the account names visible to the admin, including later pages. */
export async function loadGrantUsers(adminClient) {
    const users = [];
    const seen = new Set();
    if (!adminClient || typeof adminClient.request !== 'function') {
        return { users, message: label('clinic.grants.error.users') };
    }
    for (let page = 1; page <= 20; page++) {
        const res = await adminClient.request('/admin/users?sort=username&per_page=100&page=' + page);
        if (!res || res.outcome !== 'ok' || !Array.isArray(res.data?.users)) {
            return { users: [], message: (res && res.message) || label('clinic.grants.error.users') };
        }
        for (const row of res.data.users) {
            if (!row || !Number.isInteger(row.id) || row.id < 1 || typeof row.username !== 'string' || !row.username.trim() || seen.has(row.id)) continue;
            seen.add(row.id);
            users.push({ id: row.id, username: row.username });
        }
        if (!res.data.pagination?.has_next) break;
    }
    users.sort((a, b) => a.username.localeCompare(b.username, 'de'));
    return { users, message: '' };
}

/**
 * Validate a draft change. Returns {ok, errors} with label keys; nothing is
 * sent when it is not ok. Unknown draft keys are unsupported expansions.
 */
export function validateDraft(draft) {
    const errors = [];
    const d = draft && typeof draft === 'object' ? draft : {};
    if (Object.keys(d).some((k) => !DRAFT_KEYS.includes(k))) errors.push('clinic.grants.error.unsupported');
    if (!SCOPES.includes(d.scope)) errors.push('clinic.grants.error.scope');
    if (!EFFECTS.includes(d.effect)) errors.push('clinic.grants.error.effect');
    const actions = Array.isArray(d.actions) ? d.actions : [];
    if (!actions.length || !actions.every((a) => ACTIONS.includes(a))) errors.push('clinic.grants.error.actions');
    if (d.scope === 'pool' && (typeof d.poolId !== 'string' || !d.poolId.trim())) errors.push('clinic.grants.error.pool');
    const authors = d.authorIds == null ? [] : idList(d.authorIds);
    const exceptions = d.exceptIds == null ? [] : idList(d.exceptIds);
    if (authors === null || (authors.length && (d.effect !== 'allow' || d.scope !== 'all'))) errors.push('clinic.grants.error.authors');
    if (exceptions === null || (exceptions.length && d.scope !== 'pool')) errors.push('clinic.grants.error.exceptions');
    return { ok: errors.length === 0, errors };
}

function changeBody(draft, revision, actions, poolId) {
    const body = { expected_revision: revision, effect: draft.effect, scope: draft.scope, pool_id: poolId, actions };
    if (draft.effect === 'allow' && draft.scope === 'all') {
        const ids = idList(draft.authorIds) || [];
        if (ids.length) body.author_ids = ids;
    }
    if (draft.effect === 'deny' && draft.scope === 'pool') {
        const ids = idList(draft.exceptIds) || [];
        if (ids.length) body.author_ids = ids;
    }
    return body;
}

function normalize(items) {
    return (Array.isArray(items) ? items : []).map((item) => ({
        id: item.id,
        authorNames: Array.isArray(item.author_names) ? item.author_names : [],
        authorIds: Array.isArray(item.author_ids) ? item.author_ids.filter((id) => Number.isInteger(id) && id > 0) : [],
        effect: item.effect,
        scope: item.scope,
        poolId: item.pool_id == null ? null : String(item.pool_id),
        actions: Array.isArray(item.actions) ? item.actions : [],
    }));
}

/**
 * Effective result per action for the stored items, optionally with a draft
 * added. A deny wins over any allow, whatever the scope. Own view/play defaults
 * are server-side and not shown here.
 */
export function effectiveActions(items, draft) {
    const all = draft ? items.concat([{ effect: draft.effect, actions: draft.actions || [] }]) : items;
    return ACTIONS.map((action) => {
        const hits = all.filter((g) => g.actions.includes(action));
        const denied = hits.some((g) => g.effect === 'deny');
        const allowed = hits.some((g) => g.effect === 'allow');
        let result = 'none';
        if (denied) result = 'denied';
        else if (allowed) result = 'allowed';
        return { action, result, overridden: denied && allowed };
    });
}

/**
 * @param {{request: Function}} client  createClinicApiClient()
 * @param {{canEdit?: boolean}} options
 */
export function createGrantsStore(client, onChange, options) {
    const canEdit = !options || options.canEdit !== false;
    const state = {
        status: 'idle', // idle | loading | ready | denied | stale | error
        userId: null,
        revision: null,
        items: [],
        recipientChoice: null, // no explicit grant; effective choice is open
        canEdit,
        failedWrite: null, // kept for retry with the same body and revision
        message: '',
    };
    const emit = () => { if (typeof onChange === 'function') onChange(state); };

    function fail(res) {
        state.message = res.message;
        if (res.outcome === 'conflict') state.status = 'stale';
        else if (res.outcome === 'permission') state.status = 'denied';
        else state.status = 'error';
        emit();
        return res;
    }

    async function load(userId, message = '') {
        if (userId !== undefined) state.userId = userId;
        if (!Number.isInteger(state.userId) || state.userId < 1) return null;
        state.status = 'loading';
        state.message = '';
        emit();
        const path = '/identity/users/' + state.userId + '/grants';
        const res = await client.request(options?.canEdit ? path + '?entries=1' : path);
        if (res.outcome !== 'ok') return fail(res);
        const data = res.data || {};
        state.revision = Number.isInteger(data.revision) ? data.revision : 0;
        state.items = normalize(data.items);
        state.recipientChoice = data.recipient_choice ?? null;
        state.failedWrite = null;
        state.message = message;
        state.status = 'ready';
        emit();
        return res;
    }

    function covers(item, effect, scope, poolId, actions, authors) {
        if (item.effect !== effect || item.scope !== scope || (item.poolId || null) !== (poolId || null)) return false;
        if (item.actions.length !== actions.length || actions.some((action) => !item.actions.includes(action))) return false;
        const have = item.authorIds || [];
        return have.length === authors.length && authors.every((id) => have.includes(id));
    }

    async function savePoolException(path, poolId, actions, exceptions) {
        const allowBody = { expected_revision: state.revision, effect: 'allow', scope: 'pool', pool_id: poolId, actions };
        if (!state.items.some((item) => covers(item, 'allow', 'pool', poolId, actions, []))) {
            const opened = await send({ path, method: 'POST', body: allowBody });
            if (!opened || opened.outcome !== 'ok' || state.status !== 'ready') return opened;
        }
        if (state.items.some((item) => covers(item, 'deny', 'pool', poolId, actions, exceptions))) return { outcome: 'ok' };
        return send({
            path, method: 'POST',
            body: {
                expected_revision: state.revision, effect: 'deny', scope: 'pool', pool_id: poolId,
                actions, author_ids: exceptions,
            },
        });
    }

    async function send(write) {
        state.status = 'loading';
        state.failedWrite = null;
        emit();
        const res = await client.request(write.path, { method: write.method, body: write.body });
        if (res.outcome === 'ok') {
            state.failedWrite = null;
            return load(undefined, write.method === 'PUT' ? label('clinic.grants.recipientChoice.saved') : '');
        }
        // Network/unknown: keep the write so retry() resends the identical change.
        state.failedWrite = res.outcome === 'network' || res.outcome === 'UNKNOWN' ? write : null;
        return fail(res);
    }

    return {
        state,
        load,
        reload: () => load(),
        preview(draft) {
            return effectiveActions(state.items, validateDraft(draft).ok ? draft : null);
        },
        save(draft) {
            const check = validateDraft(draft);
            if (!check.ok || state.status !== 'ready' || !state.canEdit) return Promise.resolve(null);
            const path = '/identity/users/' + state.userId + '/grants';
            const actions = ACTIONS.filter((a) => draft.actions.includes(a));
            const poolId = draft.scope === 'pool' ? String(draft.poolId).trim() : null;
            const exceptions = draft.effect === 'allow' && draft.scope === 'pool' ? idList(draft.exceptIds) || [] : [];
            if (exceptions.length) return savePoolException(path, poolId, actions, exceptions);
            return send({ path, method: 'POST', body: changeBody(draft, state.revision, actions, poolId) });
        },
        saveRecipientChoice(choice) {
            if (state.status !== 'ready' || !state.canEdit || !RECIPIENT_CHOICES.includes(choice)) return Promise.resolve(null);
            return send({
                path: '/identity/users/' + state.userId + '/grants', method: 'PUT',
                body: { expected_revision: state.revision, recipient_choice: choice },
            });
        },
        revoke(item) {
            if (!item.id || state.status !== 'ready' || !state.canEdit) return Promise.resolve(null);
            return send({
                path: '/identity/users/' + state.userId + '/grants/' + encodeURIComponent(item.id),
                method: 'DELETE', body: { expected_revision: state.revision },
            });
        },
        retry() {
            return state.failedWrite ? send(state.failedWrite) : Promise.resolve(null);
        },
    };
}

export function statusSurface(state) {
    const map = { ready: 'success', loading: 'processing', stale: 'warning', denied: 'error', error: 'error' };
    return surfaceFor(map[state.status] || 'normal');
}

export const GRANTS_TEMPLATE = `
<section class="grants-ui cadm-panel" aria-labelledby="grants-title">
  <header class="cadm-panel__head">
    <div>
      <h2 id="grants-title" class="cadm-title">{{ label('clinic.grants.title') }}</h2>
      <p class="cadm-desc">{{ label('clinic.grants.description') }}</p>
    </div>
    <div class="cadm-panel__tools">
      <button type="button" class="btn btn--ghost" :disabled="state.status === 'loading'" @click="reload"><i class="fas fa-sync-alt" aria-hidden="true"></i><span>{{ label('clinic.grants.reload') }}</span></button>
      <button type="button" class="btn btn--primary" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.grants.retry') }}</button>
    </div>
  </header>
  <p role="status" aria-live="polite" class="cadm-status" :style="surfaceStyle">
    <span>{{ label('clinic.grants.revision') }}: {{ state.revision === null ? '–' : state.revision }}</span>
    <span v-if="state.status === 'stale'">{{ label('clinic.grants.stale') }}</span>
    <span v-else>{{ state.message }}</span>
  </p>
  <form v-if="state.revision !== null" class="cadm-editor" @submit.prevent="store.saveRecipientChoice(recipientChoice)">
    <div class="cadm-form">
      <div class="cadm-field cadm-field--wide">
        <label for="grants-recipient-choice">{{ label('clinic.grants.recipientChoice') }}</label>
        <select id="grants-recipient-choice" class="field" style="min-height: var(--sp-7); border-color: var(--ui-text-muted)" v-model="recipientChoice" :disabled="!state.canEdit || state.status !== 'ready'">
          <option v-for="choice in recipientChoices" :key="choice" :value="choice">{{ label('clinic.grants.recipientChoice.' + choice) }}</option>
        </select>
      </div>
      <div v-if="state.canEdit" class="cadm-form__actions">
        <button type="submit" class="btn btn--primary" style="min-height: var(--sp-7)" :disabled="state.status !== 'ready' || recipientChoice === state.recipientChoice">{{ label('clinic.grants.recipientChoice.save') }}</button>
      </div>
    </div>
  </form>
  <div class="cadm-section">
    <h3 id="grants-existing" class="cadm-subtitle">{{ label('clinic.grants.existing') }}</h3>
    <ul aria-labelledby="grants-existing" class="cadm-rows" :class="{ 'cadm-rows--empty': !state.items.length }">
      <li v-if="!state.items.length">{{ label('clinic.grants.empty') }}</li>
      <li v-for="(item, i) in state.items" :key="item.id || i">
        <span class="pill-status" :class="item.effect === 'deny' ? 'pill-status--danger' : 'pill-status--success'">{{ label('clinic.grants.effect.' + item.effect) }}</span>
        <span>{{ label('clinic.grants.scope.' + item.scope) }}<span v-if="item.poolId"> ({{ poolNames.get(item.poolId) || 'Poolname nicht verfügbar' }})</span></span>
        <span v-if="authorText(item)">{{ authorText(item) }}</span>
        <span class="cadm-badges">
          <span v-for="a in item.actions" :key="a" class="pill-status pill-status--info">{{ label('clinic.grants.action.' + a) }}</span>
        </span>
        <button v-if="state.canEdit && item.id" type="button" class="btn btn--ghost" :disabled="state.status !== 'ready'" @click="store.revoke(item)">{{ label('clinic.grants.revoke') }}</button>
      </li>
    </ul>
  </div>
  <p v-if="!state.canEdit" class="banner banner--info"><i class="fas fa-eye" aria-hidden="true"></i><span>{{ label('clinic.grants.readonly') }}</span></p>
  <form v-else class="cadm-editor" @submit.prevent="submit" novalidate>
    <div class="cadm-form">
      <div class="cadm-field">
        <label for="grants-scope">{{ label('clinic.grants.scope') }}</label>
        <select id="grants-scope" class="field" v-model="draft.scope">
          <option v-for="s in scopes" :key="s" :value="s">{{ label('clinic.grants.scope.' + s) }}</option>
        </select>
      </div>
      <div class="cadm-field" v-if="draft.scope === 'pool'">
        <label for="grants-pool">{{ label('clinic.grants.authorPool') }}</label>
        <select id="grants-pool" class="field" v-model="draft.poolId">
          <option disabled value="">Autorenpool wählen</option>
          <option v-for="pool in authorPools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
        </select>
        <span v-if="poolMessage" role="alert">{{ poolMessage }}</span>
      </div>
      <p v-if="authorsMessage" role="alert" class="banner banner--danger cadm-field--wide">{{ authorsMessage }}</p>
      <fieldset v-if="showAuthors && authors.length">
        <legend id="grants-authors">{{ label('clinic.grants.authors') }}</legend>
        <label v-for="user in authors" :key="'author-' + user.id" class="cadm-check">
          <input type="checkbox" :value="user.id" v-model="draft.authorIds"> {{ user.username }}
        </label>
      </fieldset>
      <p v-if="showAuthors" class="cadm-hint cadm-field--wide">{{ label('clinic.grants.authors.hint') }}</p>
      <fieldset v-if="showExceptions && authors.length">
        <legend id="grants-exceptions">{{ label('clinic.grants.exceptions') }}</legend>
        <label v-for="user in authors" :key="'except-' + user.id" class="cadm-check">
          <input type="checkbox" :value="user.id" v-model="draft.exceptIds"> {{ user.username }}
        </label>
      </fieldset>
      <p v-if="showExceptions" class="cadm-hint cadm-field--wide">{{ label('clinic.grants.exceptions.hint') }}</p>
      <p v-if="(showAuthors || showExceptions) && !authors.length && !authorsMessage" role="status" class="cadm-empty cadm-field--wide">{{ label('clinic.grants.authors.empty') }}</p>
      <div class="cadm-field">
        <label for="grants-effect">{{ label('clinic.grants.effect') }}</label>
        <select id="grants-effect" class="field" v-model="draft.effect">
          <option v-for="e in effects" :key="e" :value="e">{{ label('clinic.grants.effect.' + e) }}</option>
        </select>
      </div>
      <fieldset>
        <legend>{{ label('clinic.grants.actions') }}</legend>
        <label v-for="a in actions" :key="a" class="cadm-check">
          <input type="checkbox" :value="a" v-model="draft.actions"> {{ label('clinic.grants.action.' + a) }}
        </label>
      </fieldset>
      <div role="group" aria-labelledby="grants-effective" class="cadm-field cadm-field--wide">
        <p id="grants-effective" class="cadm-subtitle">{{ label('clinic.grants.effective') }}</p>
        <ul class="cadm-rows cadm-rows--grid">
          <li v-for="row in preview" :key="row.action">
            <span>{{ label('clinic.grants.action.' + row.action) }}:</span>
            <span class="pill-status" :class="row.result === 'allowed' ? 'pill-status--success' : row.result === 'denied' ? 'pill-status--danger' : 'pill-status--pending'">{{ label('clinic.grants.effective.' + (row.result === 'allowed' ? 'allowed' : row.result === 'denied' ? 'denied' : 'none')) }}</span>
            <span v-if="row.overridden" class="cadm-hint">({{ label('clinic.grants.effective.overridden') }})</span>
          </li>
        </ul>
      </div>
      <ul v-if="errors.length" role="alert" class="banner banner--danger cadm-errors cadm-field--wide"><li v-for="e in errors" :key="e">{{ label(e) }}</li></ul>
      <div class="cadm-form__actions">
        <button type="submit" class="btn btn--primary" :disabled="state.status !== 'ready'">{{ label('clinic.grants.save') }}</button>
      </div>
    </div>
  </form>
</section>`;

/** Vue component definition; pass the Vue global (window.Vue), an api client and {userId, canEdit}. */
export function createGrantsComponent(Vue, client, props) {
    return {
        template: GRANTS_TEMPLATE,
        setup() {
            const recipientDraft = Vue.ref(null);
            const store = createGrantsStore(client, (next) => {
                Object.assign(state, next);
                if (next.status === 'ready' && recipientDraft.value === next.recipientChoice) recipientDraft.value = null;
            }, props);
            const state = Vue.reactive({ ...store.state });
            const recipientChoice = Vue.computed({
                get: () => recipientDraft.value ?? state.recipientChoice ?? 'open',
                set: (choice) => { recipientDraft.value = choice; },
            });
            const draft = Vue.reactive({ effect: 'allow', scope: 'own', poolId: '', actions: [], authorIds: [], exceptIds: [] });
            const authorPools = Vue.ref([]);
            const authors = Vue.ref([]);
            const authorsMessage = Vue.ref('');
            const poolMessage = Vue.ref('');
            const pools = createPoolsStore(client, (next) => {
                authorPools.value = next.pools.author;
                poolMessage.value = next.message;
            });
            const poolNames = Vue.computed(() => new Map(authorPools.value.map((p) => [p.id, p.name])));
            const errors = Vue.ref([]);
            const showAuthors = Vue.computed(() => draft.effect === 'allow' && draft.scope === 'all');
            const showExceptions = Vue.computed(() => draft.scope === 'pool');
            const preview = Vue.computed(() => effectiveActions(state.items, validateDraft(draft).ok ? draft : null));
            if (typeof Vue.watch === 'function') {
                Vue.watch(() => draft.scope + '\0' + draft.effect, () => {
                    if (!(draft.effect === 'allow' && draft.scope === 'all')) draft.authorIds = [];
                    if (draft.scope !== 'pool') draft.exceptIds = [];
                });
            }
            function authorText(item) {
                const names = item.authorNames.length
                    ? item.authorNames
                    : item.authorIds.map((id) => authors.value.find((user) => user.id === id)?.username).filter(Boolean);
                if (!names.length) return '';
                return label(item.effect === 'deny' ? 'clinic.grants.except' : 'clinic.grants.only') + ' ' + names.join(', ');
            }
            const surfaceStyle = Vue.computed(() => {
                if (state.status === 'ready') return {};
                const s = statusSurface(state);
                return { background: s.background, color: s.color, borderColor: s.border };
            });
            function submit() {
                const check = validateDraft(draft);
                errors.value = check.errors;
                return check.ok ? store.save(draft) : null;
            }
            async function reload() {
                recipientDraft.value = null;
                const listed = props && props.adminClient
                    ? loadGrantUsers(props.adminClient) : Promise.resolve({ users: [], message: '' });
                const poolsReady = pools.load();
                const grantsReady = store.load(props && props.userId);
                const directory = await listed;
                authors.value = directory.users;
                authorsMessage.value = directory.message;
                await poolsReady;
                return grantsReady;
            }
            Vue.onMounted(reload);
            return {
                state, store, draft, errors, preview, surfaceStyle, submit, label, authorPools, poolNames, poolMessage, reload,
                authors, authorsMessage, showAuthors, showExceptions, authorText,
                recipientChoice, recipientChoices: RECIPIENT_CHOICES,
                actions: ACTIONS, scopes: SCOPES, effects: EFFECTS,
            };
        },
    };
}
