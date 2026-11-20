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
 * Reads use GET, additions use POST and revocations use DELETE through the
 * shared api client. Every write carries the revision it last read; a conflict
 * keeps the newer server state and never overwrites it. Grant data stays in
 * memory only (no browser storage). Colours come only from the token contract.
 */

import { surfaceFor } from './tokens.js';
import { createPoolsStore } from './pools-ui.js';

export const ACTIONS = Object.freeze(['view', 'play', 'edit', 'export', 'release']);
export const SCOPES = Object.freeze(['own', 'pool', 'all']);
export const EFFECTS = Object.freeze(['allow', 'deny']);

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
    'clinic.grants.action.audio_export': 'Audio exportieren',
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
};

export function label(key) {
    const i18n = typeof window !== 'undefined' ? window.i18n : undefined;
    if (i18n && typeof i18n.t === 'function') {
        const text = i18n.t(key);
        if (text && text !== key) return text;
    }
    return LABELS[key] || key;
}

const DRAFT_KEYS = ['effect', 'scope', 'poolId', 'actions'];

/** Return the account names visible to the admin, including later pages. */
export async function loadGrantUsers(adminClient) {
    const users = [];
    for (let page = 1; ; page++) {
        const res = await adminClient.request('/admin/users?sort=username&per_page=100&page=' + page);
        if (res.outcome !== 'ok') return { users: [], message: res.message };
        for (const { id, username } of res.data.users) users.push({ id, username });
        if (!res.data.pagination?.has_next) return { users, message: '' };
    }
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
    return { ok: errors.length === 0, errors };
}

function normalize(items) {
    return (Array.isArray(items) ? items : []).map((item) => ({
        id: item.id,
        authorNames: item.author_names || [],
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

    async function load(userId) {
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
        state.failedWrite = null;
        state.status = 'ready';
        emit();
        return res;
    }

    async function send(write) {
        state.status = 'loading';
        state.failedWrite = null;
        emit();
        const res = await client.request(write.path, { method: write.method, body: write.body });
        if (res.outcome === 'ok') {
            state.failedWrite = null;
            return load();
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
            const body = {
                expected_revision: state.revision,
                effect: draft.effect,
                scope: draft.scope,
                pool_id: draft.scope === 'pool' ? draft.poolId.trim() : null,
                actions: ACTIONS.filter((a) => draft.actions.includes(a)),
            };
            return send({ path: '/identity/users/' + state.userId + '/grants', method: 'POST', body });
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
  <div class="cadm-section">
    <h3 id="grants-existing" class="cadm-subtitle">{{ label('clinic.grants.existing') }}</h3>
    <ul aria-labelledby="grants-existing" class="cadm-rows" :class="{ 'cadm-rows--empty': !state.items.length }">
      <li v-if="!state.items.length">{{ label('clinic.grants.empty') }}</li>
      <li v-for="(item, i) in state.items" :key="item.id || i">
        <span class="pill-status" :class="item.effect === 'deny' ? 'pill-status--danger' : 'pill-status--success'">{{ label('clinic.grants.effect.' + item.effect) }}</span>
        <span>{{ label('clinic.grants.scope.' + item.scope) }}<span v-if="item.poolId"> ({{ poolNames.get(item.poolId) || 'Poolname nicht verfügbar' }})</span></span>
        <span v-if="item.authorNames.length">Nur für Diktate von {{ item.authorNames.join(', ') }}</span>
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
            const state = Vue.reactive({ status: 'idle', userId: null, revision: null, items: [], canEdit: true, failedWrite: null, message: '' });
            const store = createGrantsStore(client, (next) => Object.assign(state, next), props);
            const draft = Vue.reactive({ effect: 'allow', scope: 'own', poolId: '', actions: [] });
            const authorPools = Vue.ref([]);
            const poolMessage = Vue.ref('');
            const pools = createPoolsStore(client, (next) => {
                authorPools.value = next.pools.author;
                poolMessage.value = next.message;
            });
            const poolNames = Vue.computed(() => new Map(authorPools.value.map((p) => [p.id, p.name])));
            const errors = Vue.ref([]);
            const preview = Vue.computed(() => effectiveActions(state.items, validateDraft(draft).ok ? draft : null));
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
            function reload() {
                pools.load();
                return store.load(props && props.userId);
            }
            Vue.onMounted(reload);
            return {
                state, store, draft, errors, preview, surfaceStyle, submit, label, authorPools, poolNames, poolMessage, reload,
                actions: ACTIONS, scopes: SCOPES, effects: EFFECTS,
            };
        },
    };
}
