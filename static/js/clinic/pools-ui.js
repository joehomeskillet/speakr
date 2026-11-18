/**
 * Author pool and staff pool management (PAR-ADM-002, PAR-ADM-003).
 *
 * Both pool types are listed and edited separately; a user may belong to
 * several pools of either type. Removal needs a visible confirmation. Every
 * write carries the persisted revision (expected_revision); a conflict keeps
 * the newer server state and never overwrites it. Clinical data and
 * membership lists stay in memory only (no browser storage).
 *
 * Server reads use GET /pools and writes use PUT /pools/<kind>/<pool_id> with
 * the full membership set via the shared api client. Colours come only from the token contract.
 */

import { surfaceFor } from './tokens.js';

export const POOL_TYPES = Object.freeze({
    author: Object.freeze({ kind: 'author', listKey: 'author_pools' }),
    staff: Object.freeze({ kind: 'staff', listKey: 'staff_pools' }),
});

const LABELS = {
    'clinic.pools.title': 'Pools verwalten',
    'clinic.pools.author': 'Autorenpools',
    'clinic.pools.staff': 'Schreibpools',
    'clinic.pools.revision': 'Stand',
    'clinic.pools.members': 'Mitglieder',
    'clinic.pools.add': 'Mitglied hinzufügen',
    'clinic.pools.addLabel': 'Benutzer-ID des neuen Mitglieds',
    'clinic.pools.remove': 'Entfernen',
    'clinic.pools.confirmRemove': 'Mitgliedschaft wirklich entfernen?',
    'clinic.pools.confirm': 'Ja, entfernen',
    'clinic.pools.cancel': 'Abbrechen',
    'clinic.pools.reload': 'Aktuellen Stand laden',
    'clinic.pools.retry': 'Erneut versuchen',
    'clinic.pools.empty': 'Keine Mitglieder',
    'clinic.pools.stale': 'Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.',
};

export function label(key) {
    const i18n = typeof window !== 'undefined' ? window.i18n : undefined;
    if (i18n && typeof i18n.t === 'function') {
        const text = i18n.t(key);
        if (text && text !== key) return text;
    }
    return LABELS[key] || key;
}

function newId() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16));
}

function normalize(list) {
    return (Array.isArray(list) ? list : []).map((pool) => ({
        id: String(pool.id),
        name: String(pool.name || ''),
        memberUserIds: Array.isArray(pool.member_user_ids) ? pool.member_user_ids.slice() : [],
    }));
}

/**
 * @param {{request: Function, command: Function}} client  createClinicApiClient()
 */
export function createPoolsStore(client, onChange) {
    const state = {
        status: 'idle', // idle | loading | ready | denied | stale | error
        revision: null,
        pools: { author: [], staff: [] },
        pendingRemoval: null, // {type, poolId, userId}
        failedWrite: null, // kept for retry with the same idempotency key
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

    async function load() {
        state.status = 'loading';
        state.message = '';
        emit();
        const res = await client.request('/pools');
        if (res.outcome !== 'ok') return fail(res);
        const data = res.data || {};
        state.revision = Number.isInteger(data.revision) ? data.revision : 0;
        state.pools.author = normalize(data[POOL_TYPES.author.listKey]);
        state.pools.staff = normalize(data[POOL_TYPES.staff.listKey]);
        state.pendingRemoval = null;
        state.failedWrite = null;
        state.status = 'ready';
        emit();
        return res;
    }

    function find(type, poolId) {
        if (!Object.prototype.hasOwnProperty.call(POOL_TYPES, type)) return null;
        return state.pools[type].find((p) => p.id === poolId) || null;
    }

    async function send(write) {
        const res = await client.request(write.path, { method: 'PUT', body: write.body, idempotencyKey: write.key });
        if (res.outcome === 'ok') {
            state.failedWrite = null;
            return load();
        }
        // Network/unknown: keep the write so retry() reuses request id and key.
        state.failedWrite = res.outcome === 'network' || res.outcome === 'UNKNOWN' ? write : null;
        return fail(res);
    }

    function save(type, pool, memberUserIds) {
        if (!POOL_TYPES[type] || !pool || state.revision === null) return Promise.resolve(null);
        const requestId = newId();
        return send({
            key: requestId,
            path: '/pools/' + POOL_TYPES[type].kind + '/' + encodeURIComponent(pool.id),
            body: { expected_revision: state.revision, name: pool.name, member_user_ids: memberUserIds },
        });
    }

    return {
        state,
        load,
        reload: load,
        addMember(type, poolId, userId) {
            const pool = find(type, poolId);
            if (!pool || !Number.isInteger(userId) || userId < 1 || pool.memberUserIds.includes(userId)) {
                return Promise.resolve(null);
            }
            return save(type, pool, pool.memberUserIds.concat(userId));
        },
        requestRemoval(type, poolId, userId) {
            const pool = find(type, poolId);
            if (!pool || !pool.memberUserIds.includes(userId)) return false;
            state.pendingRemoval = { type, poolId, userId };
            emit();
            return true;
        },
        cancelRemoval() {
            state.pendingRemoval = null;
            emit();
        },
        confirmRemoval() {
            const p = state.pendingRemoval;
            if (!p) return Promise.resolve(null);
            const pool = find(p.type, p.poolId);
            state.pendingRemoval = null;
            if (!pool) return Promise.resolve(null);
            return save(p.type, pool, pool.memberUserIds.filter((id) => id !== p.userId));
        },
        retry() {
            return state.failedWrite ? send(state.failedWrite) : Promise.resolve(null);
        },
    };
}

/** Keyboard: Escape cancels a pending removal; returns true when handled. */
export function handleKey(store, event) {
    if (event && event.key === 'Escape' && store.state.pendingRemoval) {
        store.cancelRemoval();
        return true;
    }
    return false;
}

export function statusSurface(state) {
    const map = { ready: 'success', loading: 'processing', stale: 'warning', denied: 'error', error: 'error' };
    return surfaceFor(map[state.status] || 'normal');
}

export const POOLS_TEMPLATE = `
<section class="pools-ui" aria-labelledby="pools-title" @keydown="onKey">
  <h2 id="pools-title">{{ label('clinic.pools.title') }}</h2>
  <p role="status" aria-live="polite" :style="surfaceStyle">
    {{ label('clinic.pools.revision') }}: {{ state.revision === null ? '–' : state.revision }}
    <span v-if="state.status === 'stale'">{{ label('clinic.pools.stale') }}</span>
    <span v-else>{{ state.message }}</span>
  </p>
  <button type="button" @click="store.reload()">{{ label('clinic.pools.reload') }}</button>
  <button type="button" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.pools.retry') }}</button>
  <section v-for="type in ['author', 'staff']" :key="type" :aria-labelledby="'pools-' + type">
    <h3 :id="'pools-' + type">{{ label(type === 'author' ? 'clinic.pools.author' : 'clinic.pools.staff') }}</h3>
    <article v-for="pool in state.pools[type]" :key="pool.id">
      <h4>{{ pool.name }}</h4>
      <ul :aria-label="label('clinic.pools.members')">
        <li v-if="!pool.memberUserIds.length">{{ label('clinic.pools.empty') }}</li>
        <li v-for="uid in pool.memberUserIds" :key="uid">
          {{ uid }}
          <button type="button" @click="store.requestRemoval(type, pool.id, uid)">{{ label('clinic.pools.remove') }}</button>
        </li>
      </ul>
      <form @submit.prevent="addFromDraft(type, pool.id)">
        <label :for="'pools-add-' + pool.id">{{ label('clinic.pools.addLabel') }}</label>
        <input :id="'pools-add-' + pool.id" type="number" min="1" step="1" inputmode="numeric" v-model="drafts[pool.id]">
        <button type="submit">{{ label('clinic.pools.add') }}</button>
      </form>
    </article>
  </section>
  <div v-if="state.pendingRemoval" role="alertdialog" aria-modal="false" aria-labelledby="pools-confirm">
    <p id="pools-confirm">{{ label('clinic.pools.confirmRemove') }}</p>
    <button type="button" @click="store.confirmRemoval()">{{ label('clinic.pools.confirm') }}</button>
    <button type="button" @click="store.cancelRemoval()">{{ label('clinic.pools.cancel') }}</button>
  </div>
</section>`;

/** Vue component definition; pass the Vue global (window.Vue) and an api client. */
export function createPoolsComponent(Vue, client) {
    return {
        template: POOLS_TEMPLATE,
        setup() {
            const state = Vue.reactive({ status: 'idle', revision: null, pools: { author: [], staff: [] }, pendingRemoval: null, failedWrite: null, message: '' });
            const store = createPoolsStore(client, (next) => Object.assign(state, next));
            const surfaceStyle = Vue.computed(() => {
                const s = statusSurface(state);
                return { background: s.background, color: s.color, borderColor: s.border };
            });
            const drafts = Vue.reactive({});
            function addFromDraft(type, poolId) {
                const userId = Number(drafts[poolId]);
                drafts[poolId] = '';
                return store.addMember(type, poolId, userId);
            }
            Vue.onMounted(() => store.load());
            return { state, store, label, surfaceStyle, drafts, addFromDraft, onKey: (e) => handleKey(store, e) };
        },
    };
}
