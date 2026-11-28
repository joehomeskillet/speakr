/**
 * Review of pending and session vocabulary changes (w173).
 *
 * PAR-VOC-004, PAR-VOC-005. Reuses the clinic api client
 * (static/js/clinic/api-client.js) and the status tokens
 * (static/js/clinic/tokens.js). Decisions go through the parity command
 * `review_resource_change`; undo uses the review undo route. Review items and
 * the session history live only in memory: nothing is written to browser
 * storage. A stale revision or a refused promotion never accepts and never
 * drops an item: it stays in the list, flagged, until the user reloads or retries.
 *
 * German labels go through the injected `t` (existing i18n mechanism, keys
 * `clinic.vocabularyReview.*`); the German default applies when a key is missing.
 */

import { surfaceFor } from './tokens.js';

const LIST = '/resources/vocabulary/reviews';

export const DECISIONS = Object.freeze({
    acceptUser: 'user',
    acceptPool: 'pool',
    reject: 'reject',
    defer: 'defer',
});

const STATUS_SURFACE = Object.freeze({
    PENDING: 'pending',
    DEFERRED: 'warning',
    ACCEPTED_USER: 'success',
    ACCEPTED_POOL: 'success',
    REJECTED: 'normal',
});

const LABELS = Object.freeze({
    'clinic.vocabularyReview.title': 'Wortschatz prüfen',
    'clinic.vocabularyReview.acceptUser': 'Für mich übernehmen',
    'clinic.vocabularyReview.acceptAuthor': 'Für den Autor übernehmen',
    'clinic.vocabularyReview.acceptPool': 'Für den Pool übernehmen',
    'clinic.vocabularyReview.reject': 'Ablehnen',
    'clinic.vocabularyReview.defer': 'Zurückstellen',
    'clinic.vocabularyReview.undo': 'Rückgängig',
    'clinic.vocabularyReview.retry': 'Erneut versuchen',
    'clinic.vocabularyReview.reload': 'Neu laden',
    'clinic.vocabularyReview.history': 'Verlauf der Prüfungen',
    'clinic.vocabularyReview.loading': 'Vorschläge werden geladen …',
    'clinic.vocabularyReview.unknown': 'Unbekannter Status',
    'clinic.vocabularyReview.action.user': 'Für mich übernommen',
    'clinic.vocabularyReview.action.author': 'Für den Autor übernommen',
    'clinic.vocabularyReview.action.pool': 'Für den Pool übernommen',
    'clinic.vocabularyReview.action.reject': 'Abgelehnt',
    'clinic.vocabularyReview.action.defer': 'Zurückgestellt',
    'clinic.vocabularyReview.action.undo': 'Rückgängig gemacht',
    'clinic.vocabularyReview.empty': 'Keine Vorschläge zu prüfen.',
    'clinic.vocabularyReview.stale': 'Der Stand ist veraltet. Bitte neu laden.',
    'clinic.vocabularyReview.poolDenied': 'Keine Berechtigung, für den Pool zu übernehmen.',
    'clinic.vocabularyReview.status.PENDING': 'Ausstehend',
    'clinic.vocabularyReview.status.DEFERRED': 'Zurückgestellt',
    'clinic.vocabularyReview.status.ACCEPTED_USER': 'Für mich übernommen',
    'clinic.vocabularyReview.status.ACCEPTED_POOL': 'Für den Pool übernommen',
    'clinic.vocabularyReview.status.REJECTED': 'Abgelehnt',
});

function uuid() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    throw new Error('crypto.randomUUID unavailable');
}

/**
 * @param {{client: {request: Function, command: Function, switchContext: Function},
 *          scope: {type: 'user'|'author_pool', id: string|number},
 *          t?: (key: string) => string}} deps
 */
export function createVocabularyReviewController(deps) {
    const client = deps.client;
    const translate = typeof deps.t === 'function' ? deps.t : null;
    let scope = deps.scope;
    const listeners = new Set();
    // One attempt per item and decision, so a retry replays the same idempotency key.
    const attempts = new Map();

    function label(key) {
        const text = translate ? translate(key) : key;
        return text && text !== key ? text : (LABELS[key] || key);
    }

    const state = {
        loading: false,
        busy: false,
        revision: 0,
        items: [],
        history: [], // append-only, frozen entries of this session
        error: null, // { outcome, code, message, retryable, itemId }
        stale: false,
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function itemById(id) {
        return state.items.find((i) => i.id === id) || null;
    }

    function record(entry) {
        state.history = state.history.concat([Object.freeze({ at: new Date().toISOString(), ...entry })]);
    }

    function fail(res, itemId, extra) {
        state.error = {
            outcome: res.outcome, code: res.code || null, message: res.message,
            retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
            itemId: itemId || null, ...(extra || {}),
        };
    }

    async function load() {
        state.loading = true;
        state.error = null;
        state.stale = false;
        emit();
        const q = new URLSearchParams({ scope_type: scope.type, scope_id: String(scope.id), status: 'PENDING' });
        const res = await client.request(LIST + '?' + q.toString());
        state.loading = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const data = res.data || {};
        state.revision = Number.isInteger(data.revision) ? data.revision : 0;
        state.items = (Array.isArray(data.items) ? data.items : []).map((i) => ({ ...i, poolDenied: false, stale: false }));
        attempts.clear();
        emit();
        return true;
    }

    async function decide(itemId, decision) {
        const item = itemById(itemId);
        if (!item || state.busy) return false;
        if (decision === 'pool' && item.poolDenied) return false;
        const expected = Number.isInteger(item.revision) ? item.revision : (item.status === 'PENDING' ? 1 : state.revision);
        const key = itemId + ':' + decision + ':' + expected;
        let attempt = attempts.get(key);
        if (!attempt) {
            attempt = { requestId: uuid(), idempotencyKey: uuid() };
            attempts.set(key, attempt);
        }
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.command({
            command: 'review_resource_change',
            request_id: attempt.requestId,
            expected_revision: expected,
            payload: { proposal_id: itemId, decision },
        }, { idempotencyKey: attempt.idempotencyKey });
        state.busy = false;
        if (res.outcome !== 'ok') {
            if (res.outcome === 'conflict') {
                item.stale = true;
                state.stale = true;
                attempts.delete(key);
            } else if (res.outcome === 'permission' && decision === 'pool') {
                item.poolDenied = true;
            } else if (res.outcome !== 'network' && res.outcome !== 'UNKNOWN') {
                attempts.delete(key);
            }
            fail(res, itemId, { lastAction: decision });
            emit();
            return false;
        }
        attempts.delete(key);
        const nextRev = res.data && Number.isInteger(res.data.revision) ? res.data.revision : expected + 1;
        const status = { user: 'ACCEPTED_USER', pool: 'ACCEPTED_POOL', reject: 'REJECTED', defer: 'DEFERRED' }[decision];
        item.status = status;
        item.revision = nextRev;
        item.stale = false;
        record({ itemId, action: decision, status, revision: nextRev });
        emit();
        return true;
    }

    async function undo(itemId) {
        const item = itemById(itemId);
        if (!item || state.busy || item.status === 'PENDING') return false;
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.request(LIST + '/' + encodeURIComponent(itemId) + '/undo', {
            method: 'POST',
            body: Number.isInteger(item.revision) ? { expected_revision: item.revision } : {},
        });
        state.busy = false;
        if (res.outcome !== 'ok') {
            if (res.outcome === 'conflict') {
                item.stale = true;
                state.stale = true;
            }
            fail(res, itemId);
            emit();
            return false;
        }
        const d = res.data || {};
        item.status = d.status || 'PENDING';
        item.revision = Number.isInteger(d.revision) ? d.revision : item.revision;
        item.stale = false;
        record({ itemId, action: 'undo', status: item.status, revision: item.revision });
        emit();
        return true;
    }

    // Retry repeats the failed call with the same ids; reload clears a stale state.
    function retry() {
        const err = state.error;
        if (!err || !err.retryable) return Promise.resolve(false);
        return err.itemId && err.lastAction ? decide(err.itemId, err.lastAction) : load();
    }

    function setScope(next) {
        scope = next;
        state.items = [];
        state.history = [];
        state.revision = 0;
        attempts.clear();
        return load();
    }

    function switchContext(next) {
        const n = next || {};
        client.switchContext({ accountId: n.accountId, dictationId: n.dictationId });
        return setScope(n.scope || scope);
    }

    function view() {
        const err = state.error;
        const poolScope = scope.type === 'author_pool';
        const statusLabel = (status) => label('clinic.vocabularyReview.' +
            (poolScope && status === 'ACCEPTED_USER' ? 'action.author' :
                Object.hasOwn(STATUS_SURFACE, status) ? 'status.' + status : 'unknown'));
        return {
            title: label('clinic.vocabularyReview.title'),
            empty: !state.loading && state.items.length === 0 ? label('clinic.vocabularyReview.empty') : '',
            items: state.items.map((i) => {
                const open = i.status === 'PENDING' || i.status === 'DEFERRED';
                return {
                    id: i.id,
                    writtenForm: i.written_form,
                    spokenForm: i.spoken_form || '',
                    operation: i.operation,
                    status: i.status,
                    statusLabel: statusLabel(i.status),
                    surface: surfaceFor(STATUS_SURFACE[i.status] || 'normal'),
                    stale: i.stale,
                    notice: i.stale ? label('clinic.vocabularyReview.stale')
                        : (i.poolDenied ? label('clinic.vocabularyReview.poolDenied') : ''),
                    actions: {
                        acceptUser: { enabled: open && !i.stale && !state.busy },
                        acceptPool: { enabled: open && !i.stale && !i.poolDenied && !state.busy },
                        reject: { enabled: open && !i.stale && !state.busy },
                        defer: { enabled: i.status === 'PENDING' && !i.stale && !state.busy },
                        undo: { enabled: i.status !== 'PENDING' && !state.busy },
                    },
                };
            }),
            history: state.history.map((h) => ({
                ...h,
                writtenForm: itemById(h.itemId)?.written_form || 'Wortschatzeintrag',
                actionLabel: label('clinic.vocabularyReview.action.' + (poolScope && h.action === 'user' ? 'author' : h.action)),
                statusLabel: statusLabel(h.status),
            })),
            loading: state.loading,
            error: err,
            errorSurface: err ? surfaceFor('error') : null,
            stale: state.stale,
            busy: state.busy,
            a11y: {
                role: 'region',
                list: { role: 'list', label: label('clinic.vocabularyReview.title') },
                historyList: { role: 'log', label: label('clinic.vocabularyReview.history') },
                alert: { role: 'alert', live: 'assertive' },
                status: { role: 'status', live: 'polite' },
                keys: { select: ['Enter', ' '], move: ['ArrowUp', 'ArrowDown'], undo: ['Escape'] },
            },
            labels: {
                acceptUser: label(poolScope ? 'clinic.vocabularyReview.acceptAuthor' : 'clinic.vocabularyReview.acceptUser'),
                acceptPool: label('clinic.vocabularyReview.acceptPool'),
                reject: label('clinic.vocabularyReview.reject'),
                defer: label('clinic.vocabularyReview.defer'),
                undo: label('clinic.vocabularyReview.undo'),
                retry: label('clinic.vocabularyReview.retry'),
                reload: label('clinic.vocabularyReview.reload'),
                history: label('clinic.vocabularyReview.history'),
                loading: label('clinic.vocabularyReview.loading'),
            },
        };
    }

    // Arrow keys move focus between items (returns the target id); Escape undoes the focused item.
    function onKeydown(event, focusedId) {
        const key = event && event.key;
        const ids = state.items.map((i) => i.id);
        const at = ids.indexOf(focusedId);
        if (key === 'ArrowDown' || key === 'ArrowUp') {
            if (!ids.length) return null;
            const step = key === 'ArrowUp' ? -1 : 1;
            return ids[(Math.max(at, 0) + (at < 0 ? 0 : step) + ids.length) % ids.length];
        }
        if (key === 'Escape' && at >= 0) {
            undo(focusedId);
            return focusedId;
        }
        return null;
    }

    return {
        state, view, load, undo, retry, setScope, switchContext, onKeydown,
        acceptUser: (id) => decide(id, DECISIONS.acceptUser),
        acceptPool: (id) => decide(id, DECISIONS.acceptPool),
        reject: (id) => decide(id, DECISIONS.reject),
        defer: (id) => decide(id, DECISIONS.defer),
        subscribe(fn) {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
    };
}

/** Vue composable: reactive snapshot of the controller view (needs window.Vue). */
export function useVocabularyReview(deps) {
    const controller = createVocabularyReviewController(deps);
    const Vue = typeof window !== 'undefined' ? window.Vue : null;
    if (!Vue) return { controller, view: null };
    const view = Vue.shallowRef(controller.view());
    controller.subscribe(() => { view.value = controller.view(); });
    return { controller, view };
}

export const REVIEW_TEMPLATE = `
<section class="v8-page stack" :aria-busy="view.loading || view.busy" role="region" :aria-label="view.title" @keydown="onKey">
  <h2 >{{ view.title }}</h2>
  <div v-if="reviewPools.length" class="field">
    <label for="voc-review-scope">Prüfbereich</label>
    <select id="voc-review-scope" class="select" style="appearance:auto" :value="selectedPool" :disabled="view.loading || view.busy"
      @change="selectReviewPool($event.target.value)">
      <option value="">Persönlich</option>
      <optgroup label="Autorenpool">
        <option v-for="pool in reviewPools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
      </optgroup>
    </select>
  </div>
  <div v-else-if="reviewPoolsFailed" class="note" role="status">
    Prüfbare Autorenpools konnten nicht geladen werden.
    <button type="button" class="small" @click="loadReviewPools">Autorenpools erneut laden</button>
  </div>
  <p v-if="view.loading" role="status">{{ view.labels.loading }}</p>
  <div v-if="view.error" class="note burgundy" role="alert">
    {{ errorMessage(view.error) }}
    <button class="small" type="button" v-if="view.error.retryable" @click="controller.retry()">{{ view.labels.retry }}</button>
  </div>
  <p v-else-if="view.empty" class="empty" role="status">{{ view.empty }}</p>
  <button class="small" type="button" v-if="view.stale" @click="controller.load()">{{ view.labels.reload }}</button>
  <ul v-if="view.items.length" class="stack" role="list" :aria-label="view.title">
    <li v-for="i in view.items" :key="i.id" :data-id="i.id" tabindex="0" class="panel stack">
      <div class="actions">
        <strong>{{ i.writtenForm }}</strong> <span v-if="i.spokenForm">({{ i.spokenForm }})</span>
        <span v-if="i.operation === 'DELETE'" class="tag">Unterdrückung</span>
        <span v-else-if="i.operation === 'ADD'" class="tag">Aufnahme</span>
        <span class="tag" :style="surfaceStyle(i.surface)">{{ i.statusLabel }}</span>
      </div>
      <p v-if="i.notice" role="status">{{ i.notice }}</p>
      <div class="actions">
        <button class="small" type="button" :disabled="!i.actions.acceptUser.enabled" @click="controller.acceptUser(i.id)">{{ view.labels.acceptUser }}</button>
        <button class="small" type="button" :disabled="!i.actions.acceptPool.enabled" @click="controller.acceptPool(i.id)">{{ view.labels.acceptPool }}</button>
        <button class="small" type="button" :disabled="!i.actions.reject.enabled" @click="controller.reject(i.id)">{{ view.labels.reject }}</button>
        <button class="small" type="button" :disabled="!i.actions.defer.enabled" @click="controller.defer(i.id)">{{ view.labels.defer }}</button>
        <button class="small" type="button" :disabled="!i.actions.undo.enabled" @click="controller.undo(i.id)">{{ view.labels.undo }}</button>
      </div>
    </li>
  </ul>
  <h3>{{ view.labels.history }}</h3>
  <ol class="timeline" role="log" :aria-label="view.labels.history">
    <li v-for="(h, n) in view.history" :key="n" class="break-words">{{ h.writtenForm }} · {{ h.actionLabel }} · {{ h.statusLabel }}</li>
  </ol>
</section>`;
