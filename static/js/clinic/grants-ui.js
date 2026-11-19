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
 * Reads use GET and writes use POST on /identity/users/{id}/grants through the
 * shared api client. Every write carries the revision it last read; a conflict
 * keeps the newer server state and never overwrites it. Grant data stays in
 * memory only (no browser storage). Colours come only from the token contract.
 */

import { surfaceFor } from './tokens.js';

export const ACTIONS = Object.freeze(['view', 'play', 'edit', 'export', 'release']);
export const SCOPES = Object.freeze(['own', 'pool', 'all']);
export const EFFECTS = Object.freeze(['allow', 'deny']);

const LABELS = {
    'clinic.grants.title': 'Berechtigungen',
    'clinic.grants.revision': 'Stand',
    'clinic.grants.scope': 'Geltungsbereich',
    'clinic.grants.scope.own': 'Eigene Diktate',
    'clinic.grants.scope.pool': 'Pool',
    'clinic.grants.scope.all': 'Alle Diktate',
    'clinic.grants.pool': 'Pool-Kennung',
    'clinic.grants.effect': 'Wirkung',
    'clinic.grants.effect.allow': 'Erlauben',
    'clinic.grants.effect.deny': 'Verweigern',
    'clinic.grants.actions': 'Aktionen',
    'clinic.grants.action.view': 'Ansehen',
    'clinic.grants.action.play': 'Abspielen',
    'clinic.grants.action.edit': 'Bearbeiten',
    'clinic.grants.action.export': 'Exportieren',
    'clinic.grants.action.release': 'Freigeben',
    'clinic.grants.effective': 'Wirksames Ergebnis nach dem Speichern',
    'clinic.grants.effective.allowed': 'erlaubt',
    'clinic.grants.effective.denied': 'verweigert',
    'clinic.grants.effective.none': 'nicht erlaubt',
    'clinic.grants.effective.overridden': 'Erlaubnis wird durch eine Verweigerung überschrieben',
    'clinic.grants.existing': 'Bestehende Einträge',
    'clinic.grants.empty': 'Keine Einträge',
    'clinic.grants.save': 'Speichern',
    'clinic.grants.reload': 'Aktuellen Stand laden',
    'clinic.grants.retry': 'Erneut versuchen',
    'clinic.grants.readonly': 'Nur Lesezugriff. Änderungen sind nur durch Administration möglich.',
    'clinic.grants.stale': 'Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
    'clinic.grants.error.scope': 'Dieser Geltungsbereich wird nicht unterstützt.',
    'clinic.grants.error.effect': 'Diese Wirkung wird nicht unterstützt.',
    'clinic.grants.error.actions': 'Mindestens eine unterstützte Aktion wählen.',
    'clinic.grants.error.pool': 'Für den Geltungsbereich Pool ist eine Pool-Kennung nötig.',
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
        effect: item.effect,
        scope: item.scope,
        poolId: item.pool_id == null ? null : String(item.pool_id),
        actions: Array.isArray(item.actions) ? item.actions.filter((a) => ACTIONS.includes(a)) : [],
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
        const res = await client.request('/identity/users/' + state.userId + '/grants');
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
        const res = await client.request('/identity/users/' + state.userId + '/grants', { method: 'POST', body: write });
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
            if (!check.ok || state.revision === null || !state.canEdit) return Promise.resolve(null);
            return send({
                expected_revision: state.revision,
                effect: draft.effect,
                scope: draft.scope,
                pool_id: draft.scope === 'pool' ? draft.poolId.trim() : null,
                actions: ACTIONS.filter((a) => draft.actions.includes(a)),
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
<section class="grants-ui" aria-labelledby="grants-title">
  <h2 id="grants-title">{{ label('clinic.grants.title') }}</h2>
  <p role="status" aria-live="polite" :style="surfaceStyle">
    {{ label('clinic.grants.revision') }}: {{ state.revision === null ? '–' : state.revision }}
    <span v-if="state.status === 'stale'">{{ label('clinic.grants.stale') }}</span>
    <span v-else>{{ state.message }}</span>
  </p>
  <button type="button" @click="store.reload()">{{ label('clinic.grants.reload') }}</button>
  <button type="button" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.grants.retry') }}</button>
  <h3 id="grants-existing">{{ label('clinic.grants.existing') }}</h3>
  <ul aria-labelledby="grants-existing">
    <li v-if="!state.items.length">{{ label('clinic.grants.empty') }}</li>
    <li v-for="(item, i) in state.items" :key="i">
      {{ label('clinic.grants.effect.' + item.effect) }} /
      {{ label('clinic.grants.scope.' + item.scope) }}<span v-if="item.poolId"> ({{ item.poolId }})</span>:
      {{ item.actions.map((a) => label('clinic.grants.action.' + a)).join(', ') }}
    </li>
  </ul>
  <p v-if="!state.canEdit">{{ label('clinic.grants.readonly') }}</p>
  <form v-else @submit.prevent="submit" novalidate>
    <label for="grants-scope">{{ label('clinic.grants.scope') }}</label>
    <select id="grants-scope" v-model="draft.scope">
      <option v-for="s in scopes" :key="s" :value="s">{{ label('clinic.grants.scope.' + s) }}</option>
    </select>
    <label v-if="draft.scope === 'pool'" for="grants-pool">{{ label('clinic.grants.pool') }}</label>
    <input v-if="draft.scope === 'pool'" id="grants-pool" type="text" autocomplete="off" v-model="draft.poolId">
    <label for="grants-effect">{{ label('clinic.grants.effect') }}</label>
    <select id="grants-effect" v-model="draft.effect">
      <option v-for="e in effects" :key="e" :value="e">{{ label('clinic.grants.effect.' + e) }}</option>
    </select>
    <fieldset>
      <legend>{{ label('clinic.grants.actions') }}</legend>
      <label v-for="a in actions" :key="a">
        <input type="checkbox" :value="a" v-model="draft.actions"> {{ label('clinic.grants.action.' + a) }}
      </label>
    </fieldset>
    <div role="group" aria-labelledby="grants-effective">
      <p id="grants-effective">{{ label('clinic.grants.effective') }}</p>
      <ul>
        <li v-for="row in preview" :key="row.action">
          {{ label('clinic.grants.action.' + row.action) }}: {{ label('clinic.grants.effective.' + (row.result === 'allowed' ? 'allowed' : row.result === 'denied' ? 'denied' : 'none')) }}
          <span v-if="row.overridden"> ({{ label('clinic.grants.effective.overridden') }})</span>
        </li>
      </ul>
    </div>
    <ul v-if="errors.length" role="alert"><li v-for="e in errors" :key="e">{{ label(e) }}</li></ul>
    <button type="submit">{{ label('clinic.grants.save') }}</button>
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
            const errors = Vue.ref([]);
            const preview = Vue.computed(() => effectiveActions(state.items, validateDraft(draft).ok ? draft : null));
            const surfaceStyle = Vue.computed(() => {
                const s = statusSurface(state);
                return { background: s.background, color: s.color, borderColor: s.border };
            });
            function submit() {
                const check = validateDraft(draft);
                errors.value = check.errors;
                return check.ok ? store.save(draft) : null;
            }
            Vue.onMounted(() => store.load(props && props.userId));
            return {
                state, store, draft, errors, preview, surfaceStyle, submit, label,
                actions: ACTIONS, scopes: SCOPES, effects: EFFECTS,
            };
        },
    };
}
