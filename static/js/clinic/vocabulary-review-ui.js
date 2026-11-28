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
 *          t?: (key: string) => string, status?: 'OPEN'}} deps
 */
export function createVocabularyReviewController(deps) {
    const client = deps.client;
    const translate = typeof deps.t === 'function' ? deps.t : null;
    let scope = deps.scope;
    const session = deps.status === 'OPEN';
    const active = deps.active || (() => true);
    let loaded = false;
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
        if (session && (state.loading || state.busy)) return false;
        loaded = false;
        state.loading = true;
        state.error = null;
        state.stale = false;
        emit();
        const q = new URLSearchParams({ scope_type: scope.type, scope_id: String(scope.id), status: session ? 'OPEN' : 'PENDING' });
        const items = new Map();
        let revision = 0;
        let offset = 0;
        // Changing an OPEN row before finishing the offset scan can skip rows.
        while (true) {
            const res = await client.request(LIST + '?' + q.toString());
            if (!active()) return false;
            if (res.outcome !== 'ok') {
                fail(res);
                break;
            }
            const data = res.data || {};
            const rows = Array.isArray(data.items) ? data.items : [];
            if (session && (!Array.isArray(data.items) || rows.some((i) => !Number.isInteger(i.revision)))) {
                fail({ outcome: 'validation' });
                break;
            }
            if (Number.isInteger(data.revision)) revision = Math.max(revision, data.revision);
            for (const i of rows) items.set(i.id, { ...i, poolDenied: false, stale: false });
            if (!session || data.next_cursor == null) break;
            const next = Number(data.next_cursor);
            if (!Number.isSafeInteger(next) || next <= offset) {
                fail({ outcome: 'validation' });
                break;
            }
            offset = next;
            q.set('cursor', String(offset));
        }
        state.loading = false;
        if (state.error) {
            emit();
            return false;
        }
        loaded = true;
        state.revision = revision;
        state.items = [...items.values()];
        attempts.clear();
        emit();
        return true;
    }

    async function decide(itemId, decision) {
        const item = itemById(itemId);
        if (!item || state.busy || !active()) return false;
        if (session && (!loaded || state.loading || item.stale ||
            (item.status !== 'PENDING' && item.status !== 'DEFERRED') ||
            (decision === 'defer' && item.status !== 'PENDING'))) return false;
        if (decision === 'pool' && (item.poolDenied || (session && item.can_accept_pool !== true))) return false;
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
        record({ itemId, action: decision, status, revision: nextRev, operation: item.operation });
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
                    resourceType: i.resource_type,
                    canAcceptPool: i.can_accept_pool === true,
                    teamName: i.target_pool_name,
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
            ready: loaded,
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

const SESSION_RESULTS = { user: 'Für mich übernommen', pool: 'Für das Team übernommen', defer: 'Für später zurückgestellt' };
const REMOVAL_RESULTS = { user: 'Für mich entfernt', pool: 'Für das Team entfernt' };
const CHANGE_LABELS = { ADD: 'Neu', MODIFY: 'Geändert', DELETE: 'Zum Entfernen vorgeschlagen' };
// Keep the V8 :focus-visible ring, with a higher-contrast theme color.
const REVIEW_BUTTON_STYLE = 'min-height:48px;min-width:48px;outline-color:var(--brand-accent)';

export const SessionVocabularyReview = {
    name: 'ClinicSessionVocabularyReview',
    props: {
        client: { type: Object, required: true },
        userId: { type: [Number, String], required: true },
        vue: { type: Object, default: null },
    },
    emits: ['close'],
    setup(props, { emit }) {
        const vue = props.vue || globalThis.Vue;
        let mounted = true;
        const controller = createVocabularyReviewController({
            client: props.client, scope: { type: 'user', id: props.userId }, status: 'OPEN',
            active: () => mounted,
        });
        const dialog = vue.ref(null);
        const title = vue.ref(null);
        const view = vue.shallowRef(controller.view());
        const unsubscribe = controller.subscribe(() => { view.value = controller.view(); });
        const results = vue.computed(() => new Map(view.value.history.map((h) => [h.itemId,
            (h.operation === 'DELETE' && REMOVAL_RESULTS[h.action]) || SESSION_RESULTS[h.action],
        ])));
        const retryingWrite = vue.computed(() => Boolean(view.value.error?.retryable && view.value.error.itemId));
        const firstOpen = vue.computed(() => view.value.items.find((item) => !results.value.has(item.id))?.id);

        vue.onMounted(() => {
            dialog.value.showModal();
            title.value?.focus();
            return controller.load();
        });
        vue.onBeforeUnmount(() => { mounted = false; unsubscribe(); });

        function dismiss() {
            if (!controller.state.busy && dialog.value?.open) dialog.value.close();
        }

        async function run(action, id) {
            const item = view.value.items.find((entry) => entry.id === id);
            if (item?.operation === 'DELETE' && (action === 'acceptUser' || action === 'acceptPool')) {
                const destination = action === 'acceptUser' ? 'für Sie' : `für das Team «${item.teamName}»`;
                if (!globalThis.confirm(`«${item.writtenForm}» ${destination} entfernen?`)) return false;
            }
            const succeeded = await controller[action](id);
            await vue.nextTick();
            const modal = dialog.value;
            if (modal?.open && !modal.contains(modal.ownerDocument.activeElement)) title.value?.focus();
            return succeeded;
        }

        function errorText(error) {
            if (!error.itemId) return 'Die Vorschläge konnten nicht geladen werden. Bitte laden Sie sie erneut.';
            if (error.outcome === 'conflict') return 'Der Vorschlag hat sich geändert. Bitte laden Sie die Vorschläge neu.';
            if (error.retryable) return 'Die Änderung konnte nicht bestätigt werden. Bitte versuchen Sie es erneut.';
            if (error.outcome === 'permission') return 'Sie dürfen diese Änderung nicht übernehmen.';
            return 'Die Änderung konnte nicht gespeichert werden.';
        }

        return {
            dialog, title, view, results, retryingWrite, firstOpen, dismiss, run, errorText,
            changeLabel: (operation) => CHANGE_LABELS[operation?.toUpperCase()],
            onClose: () => emit('close'),
        };
    },
    template: `
<div class="v8-page">
  <dialog ref="dialog" class="panel" aria-labelledby="session-review-title" aria-describedby="session-review-hint"
          data-testid="session-vocabulary-review" @cancel.prevent="dismiss" @close="onClose" @keydown.stop>
    <div class="dialoghead"><h2 id="session-review-title" ref="title" tabindex="-1" autofocus>Wörter und Sprachbefehle prüfen</h2></div>
    <p id="session-review-hint" class="hint">Nicht bearbeitete Vorschläge bleiben für die nächste Sitzung offen.</p>
    <p v-if="view.loading" role="status">Vorschläge werden geladen …</p>
    <p v-if="view.busy" role="status">Änderung wird gespeichert …</p>
    <p v-if="view.error" class="text-status error" style="color:var(--brand-accent)" role="alert" data-testid="session-review-error">{{ errorText(view.error) }}</p>
    <button v-if="retryingWrite" type="button" style="${REVIEW_BUTTON_STYLE}" data-testid="session-review-retry"
            @click="run('retry')">Erneut versuchen</button>
    <button v-else-if="view.stale || (view.error && !view.error.itemId)" type="button" style="${REVIEW_BUTTON_STYLE}"
            :disabled="view.busy || view.loading" data-testid="session-review-reload" @click="run('load')">Vorschläge neu laden</button>
    <p v-if="view.ready && view.empty" role="status" data-testid="session-review-empty">Keine offenen Vorschläge.</p>
    <ul v-if="view.ready && view.items.length" class="stack" style="list-style:none;padding:0" aria-label="Vorschläge">
      <li v-for="item in view.items" :key="item.id" :data-id="item.id" class="panel" style="overflow-wrap:anywhere" data-testid="session-review-row">
        <p class="hint">{{ item.resourceType === 'voice_command' ? 'Sprachbefehl' : 'Wort' }} · {{ changeLabel(item.operation) }}</p>
        <strong>{{ item.writtenForm }}</strong> <span v-if="item.spokenForm">({{ item.spokenForm }})</span>
        <p v-if="results.has(item.id)" role="status" data-testid="session-review-result">{{ results.get(item.id) }}</p>
        <template v-else>
          <p v-if="item.status === 'DEFERRED'" class="hint">Bereits zurückgestellt</p>
          <p v-if="item.canAcceptPool && item.teamName" class="hint">Team: {{ item.teamName }}</p>
          <p v-if="item.stale" class="hint">Dieser Vorschlag hat sich geändert.</p>
          <p v-else-if="item.notice" class="hint">Für das Team ist keine Freigabe möglich.</p>
          <div class="actions" style="justify-content:flex-start">
            <button type="button" :class="{ primary: item.id === firstOpen }" style="${REVIEW_BUTTON_STYLE}" data-testid="session-review-personal"
                    :disabled="retryingWrite || !item.actions.acceptUser.enabled" @click="run('acceptUser', item.id)">
              {{ item.operation === 'DELETE' ? 'Für mich entfernen' : 'Für mich übernehmen' }}
            </button>
            <button v-if="item.canAcceptPool" type="button" style="${REVIEW_BUTTON_STYLE}" data-testid="session-review-team"
                    :disabled="retryingWrite || !item.actions.acceptPool.enabled" @click="run('acceptPool', item.id)">
              {{ item.operation === 'DELETE' ? 'Für das Team entfernen' : 'Für das Team übernehmen' }}
            </button>
            <button v-if="item.status === 'PENDING'" type="button" style="${REVIEW_BUTTON_STYLE}" data-testid="session-review-defer"
                    :disabled="retryingWrite || !item.actions.defer.enabled" @click="run('defer', item.id)">Zurückstellen</button>
          </div>
        </template>
      </li>
    </ul>
    <div class="actions">
      <button type="button" style="${REVIEW_BUTTON_STYLE}" :class="{ primary: view.ready && !firstOpen }"
              :disabled="view.busy" data-testid="session-review-close"
              @click="dismiss">Schliessen</button>
    </div>
  </dialog>
</div>`,
};
