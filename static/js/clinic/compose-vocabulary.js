/**
 * Clinic vocabulary view (v8-54, V8 page P13 "Wortschatz"): one tabbed Vue view
 * composed from existing modules. PAR-VOC-001 to PAR-VOC-007.
 *
 *   Wörter           vocabulary-ui          own list or one author pool, search, import/export,
 *                                           negative list (suppress/reactivate), selected text
 *   Vorschläge       compose-resources      review panel (vocabulary-review-ui): accept, reject, defer, undo
 *   Aussprache       voice-commands-ui      pronunciation hints; training only with a proven capability
 *   Enginewirkung    compose-resources      ASR profile panel (asr-profiles-ui): proven engine capabilities
 *
 * Contract for the mount package:
 *   VocabularyView                 Vue options object, props client, currentUser, vue (optional)
 *   registerVocabularyView(app)    app.component('clinic-vocabulary-view', VocabularyView)
 *
 * Before any child is mounted the view probes the own list once. A refused probe
 * (403) mounts nothing, so no foreign list is loaded. Only the active child is
 * mounted; a change of the current user switches the api client context and
 * remounts it. Server state is authoritative and nothing is written to browser
 * storage. Colours come only from the token contract, calls only from api-client.
 * Review-marker words (Vorbehaltswortliste, W11, PAR-VOC-004) are maintained in a panel
 * below the review queue of the Vorschläge tab: the global baseline by site admins, one
 * addition list per author pool by authorized pool writers; the server decides every scope.
 */

import { createVocabularyComponent } from './vocabulary-ui.js';
import { createPronunciationHintsComponent } from './voice-commands-ui.js';
import { createPanels } from './compose-resources.js';
import { surfaceFor } from './tokens.js';

export const TABS = Object.freeze([
    Object.freeze({ id: 'words', label: 'Wörter' }),
    Object.freeze({ id: 'proposals', label: 'Vorschläge' }),
    Object.freeze({ id: 'pronunciation', label: 'Aussprache' }),
    Object.freeze({ id: 'engine', label: 'Enginewirkung' }),
]);

const CAUTION = '/vocabulary/caution-words';
export const CAUTION_TEXT = Object.freeze({
    title: 'Vorbehaltswörter',
    hint: 'Treffer verlangen vor dem Abschluss eine Einzelprüfung. Die globale Basisliste pflegt die Administration, Ergänzungen je Autorenpool die berechtigten Pool-Verantwortlichen.',
    list: 'Liste',
    global: 'Globale Basisliste',
    version: 'Version',
    empty: 'Noch keine Vorbehaltswörter in dieser Liste.',
    edit: 'Wörter (ein Wort pro Zeile)',
    publish: 'Veröffentlichen',
    readOnly: 'Diese Liste ist für Sie nur lesbar.',
    stale: 'Die Liste wurde inzwischen geändert. Der aktuelle Stand ist geladen, bitte die Änderung wiederholen.',
    failed: 'Die Vorbehaltswörter konnten nicht geladen werden.',
    previewTitle: 'Treffer prüfen',
    previewText: 'Beispieltext',
    previewRun: 'Treffer prüfen',
    previewNone: 'Keine Treffer im Beispieltext.',
    previewPool: 'Für die Vorschau einen Autorenpool wählen.',
    history: 'Frühere Versionen',
});

/** Framework-free state for the caution-word panel: one list (global or one pool) at a time. */
export function createCautionStore(client, onChange) {
    const state = {
        status: 'idle', // idle | loading | ready | denied | error
        scope: { kind: 'global', poolId: null },
        version: 0, words: [], publishedBy: null, publishedAt: null, canWrite: false, history: [],
        message: '', preview: null,
    };
    let latest = 0;
    const emit = () => { if (typeof onChange === 'function') onChange(state); };
    const query = () => (state.scope.kind === 'pool'
        ? '?scope=pool&pool_id=' + encodeURIComponent(state.scope.poolId) : '?scope=global');

    async function load() {
        const mine = ++latest;
        state.status = 'loading';
        emit();
        const res = await client.request(CAUTION + query());
        if (mine !== latest) return res;
        if (res.outcome !== 'ok') {
            state.status = res.outcome === 'permission' ? 'denied' : 'error';
            state.message = res.message || '';
            emit();
            return res;
        }
        const d = res.data || {};
        Object.assign(state, {
            status: 'ready', message: '', version: d.version || 0, words: Array.isArray(d.words) ? d.words : [],
            publishedBy: d.published_by == null ? null : d.published_by, publishedAt: d.published_at || null,
            canWrite: d.can_write === true, history: Array.isArray(d.history) ? d.history : [],
        });
        emit();
        return res;
    }

    function setScope(kind, poolId) {
        if (kind === 'global') state.scope = { kind: 'global', poolId: null };
        else if (kind === 'pool' && typeof poolId === 'string' && poolId) state.scope = { kind: 'pool', poolId };
        else return Promise.resolve(null);
        state.preview = null;
        state.words = [];
        state.history = [];
        return load();
    }

    async function publish(text) {
        if (!state.canWrite || state.status !== 'ready') return null;
        const words = String(text || '').split(/\r?\n/).map((w) => w.trim()).filter(Boolean);
        const scope = state.scope;
        const body = { words, expected_version: state.version };
        if (scope.kind === 'pool') body.pool_id = scope.poolId;
        const res = await client.request(CAUTION + '/' + scope.kind, { method: 'POST', body });
        if (scope !== state.scope) return null;
        if (res.outcome === 'ok') {
            await load();
        } else if (res.outcome === 'conflict') {
            await load();
            state.message = CAUTION_TEXT.stale;
            emit();
        } else {
            state.message = res.message || '';
            emit();
        }
        return res;
    }

    async function preview(text) {
        if (state.scope.kind !== 'pool' || typeof text !== 'string' || !text.trim()) return null;
        const scope = state.scope;
        const res = await client.request(CAUTION + '/preview', {
            method: 'POST', body: { pool_id: scope.poolId, text },
        });
        if (scope !== state.scope) return null;
        if (res.outcome === 'ok') state.preview = { version: (res.data || {}).version, hits: (res.data || {}).hits || [] };
        else state.message = res.message || '';
        emit();
        return res;
    }

    return { state, load, setScope, publish, preview };
}

export const CAUTION_TEMPLATE = `
<section class="caution-words stack" data-testid="caution-words" aria-labelledby="cw-title">
  <h2 id="cw-title">${CAUTION_TEXT.title}</h2>
  <p class="subline">${CAUTION_TEXT.hint}</p>
  <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
    <label for="cw-list">${CAUTION_TEXT.list}</label>
    <select id="cw-list" class="select" :style="controlStyle" :value="state.scope.poolId || ''" @change="select($event.target.value)">
      <option value="">${CAUTION_TEXT.global}</option>
      <option v-for="pool in pools" :key="pool.id" :value="pool.id">{{ pool.name }}</option>
    </select>
  </div>
  <p v-if="state.status === 'loading'" role="status" class="note">Wird geladen …</p>
  <p v-else-if="state.status === 'denied' || state.status === 'error'" role="alert" class="note burgundy" :style="errorStyle">${CAUTION_TEXT.failed} {{ state.message }}</p>
  <template v-else-if="state.status === 'ready'">
    <p role="status" class="voc-result-count">${CAUTION_TEXT.version} {{ state.version }} · {{ state.words.length }} Wörter</p>
    <p v-if="state.message" role="status" class="note" :style="errorStyle" data-testid="caution-message">{{ state.message }}</p>
    <ul v-if="state.words.length" class="tags" data-testid="caution-list" style="display:flex;flex-wrap:wrap;gap:var(--space-2,0.5rem);padding:0;list-style:none">
      <li v-for="word in state.words" :key="word"><span class="tag">{{ word }}</span></li>
    </ul>
    <p v-else class="empty" role="status" data-testid="caution-empty">${CAUTION_TEXT.empty}</p>
    <form v-if="state.canWrite" @submit.prevent="publish()">
      <label for="cw-edit">${CAUTION_TEXT.edit}</label>
      <textarea id="cw-edit" class="w-full" rows="5" v-model="draft"></textarea>
      <div class="actions"><button type="submit" class="primary" :disabled="busy">${CAUTION_TEXT.publish}</button></div>
    </form>
    <p v-else class="note">${CAUTION_TEXT.readOnly}</p>
    <details v-if="state.history.length > 1">
      <summary>${CAUTION_TEXT.history}</summary>
      <ul><li v-for="h in state.history" :key="h.version">${CAUTION_TEXT.version} {{ h.version }}: {{ h.count }} Wörter, Benutzer {{ h.published_by }}, {{ h.published_at }}</li></ul>
    </details>
    <section aria-labelledby="cw-preview-title">
      <h3 id="cw-preview-title">${CAUTION_TEXT.previewTitle}</h3>
      <p v-if="state.scope.kind !== 'pool'" class="note">${CAUTION_TEXT.previewPool}</p>
      <form v-else @submit.prevent="runPreview()">
        <label for="cw-sample">${CAUTION_TEXT.previewText}</label>
        <textarea id="cw-sample" class="w-full" rows="3" v-model="sample"></textarea>
        <div class="actions"><button type="submit" class="small" :disabled="!sample.trim()">${CAUTION_TEXT.previewRun}</button></div>
      </form>
      <div v-if="state.preview" role="status" data-testid="caution-preview">
        <p v-if="!state.preview.hits.length" class="empty">${CAUTION_TEXT.previewNone}</p>
        <ul v-else><li v-for="hit in state.preview.hits" :key="hit.offset + hit.word"><strong>{{ hit.word }}</strong> (Position {{ hit.offset }})</li></ul>
      </div>
    </section>
  </template>
</section>`;

export function createCautionComponent(Vue, client, currentUserId = () => null) {
    return {
        name: 'ClinicCautionWords',
        template: CAUTION_TEMPLATE,
        setup() {
            const state = Vue.reactive({
                status: 'idle', scope: { kind: 'global', poolId: null }, version: 0, words: [], publishedBy: null,
                publishedAt: null, canWrite: false, history: [], message: '', preview: null,
            });
            const store = createCautionStore(client, (next) => Object.assign(state, next));
            const pools = Vue.ref([]);
            const draft = Vue.ref('');
            const sample = Vue.ref('');
            const busy = Vue.ref(false);
            const control = surfaceFor('normal');
            const controlStyle = { backgroundColor: control.background, color: control.color, borderColor: control.border };
            const errorStyle = box('error');
            async function loadPools() {
                const res = await client.request('/pools');
                if (res.outcome !== 'ok') return;
                const uid = currentUserId();
                pools.value = ((res.data || {}).author_pools || [])
                    .filter((p) => (p.member_user_ids || []).some((id) => String(id) === String(uid)))
                    .map(({ id, name }) => ({ id, name }));
            }
            // The editor always starts from the stored list, so a publish replaces it knowingly.
            const fill = () => { draft.value = state.status === 'ready' ? state.words.join('\n') : ''; };
            async function select(id) {
                draft.value = '';
                await (id ? store.setScope('pool', id) : store.setScope('global'));
                fill();
            }
            async function publish() {
                if (busy.value) return;
                busy.value = true;
                try {
                    await store.publish(draft.value);
                    fill();
                } finally {
                    busy.value = false;
                }
            }
            const runPreview = () => store.preview(sample.value);
            Vue.onMounted(() => Promise.all([store.load().then(fill), loadPools()]));
            return { state, pools, draft, sample, busy, controlStyle, errorStyle, select, publish, runPreview };
        },
    };
}

export const TEXT = Object.freeze({
    eyebrow: 'Verwaltung',
    title: 'Wortschatz',
    subline: 'Persönliche und gemeinsame Einträge prüfen, Vorschläge entscheiden und die Wirkung in der Engine nachvollziehen.',
    tablist: 'Wortschatz-Bereiche',
    loading: 'Scope und Revision werden geladen …',
    denied: 'Kein Zugriff. Bitte anmelden.',
    forbidden: 'Keine Berechtigung für den Wortschatz.',
    failed: 'Der Wortschatz konnte nicht geladen werden.',
    retry: 'Erneut versuchen',
    empty: 'Noch keine Wörter für diese Auswahl.',
});

const PROBE = '/vocabulary/search?scope=user&q=&mode=contains&status=all';

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

/**
 * Framework-free state: probe result, active tab, keyboard movement, user scope and release.
 * @param {{client: {request: Function, switchContext: Function}, currentUser: object, makeState?: (o: object) => object}} deps
 */
export function createVocabularyViewController(deps) {
    const client = deps.client;
    const state = (deps.makeState || ((o) => o))({
        active: TABS[0].id, userId: null, epoch: 0,
        status: 'denied', // denied (no user) | loading | ready | forbidden | error
        empty: false, message: '',
    });
    let disposed = false;
    let token = 0;

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.active = TABS[0].id;
        state.epoch += 1;
        state.empty = false;
        state.message = '';
        state.status = id === null ? 'denied' : 'loading';
        client.switchContext({ accountId: id, dictationId: null });
    }

    async function load() {
        if (disposed || state.userId === null) return null;
        const mine = ++token;
        state.status = 'loading';
        const res = await client.request(PROBE);
        if (disposed || mine !== token) return null;
        if (res.outcome === 'ok') {
            const entries = res.data && Array.isArray(res.data.entries) ? res.data.entries : [];
            state.empty = entries.length === 0;
            state.status = 'ready';
            state.message = '';
        } else {
            state.status = res.outcome === 'permission' ? 'forbidden' : 'error';
            state.message = res.message || '';
        }
        return res;
    }

    function select(id) {
        if (disposed || state.status !== 'ready' || !TABS.some((t) => t.id === id)) return false;
        state.active = id;
        return true;
    }

    // Tablist keys: arrows wrap, Home/End jump. Returns the newly active id or null.
    function onKeydown(event) {
        const key = event && event.key;
        const at = TABS.findIndex((t) => t.id === state.active);
        let next = -1;
        if (key === 'ArrowRight' || key === 'ArrowDown') next = (at + 1) % TABS.length;
        else if (key === 'ArrowLeft' || key === 'ArrowUp') next = (at - 1 + TABS.length) % TABS.length;
        else if (key === 'Home') next = 0;
        else if (key === 'End') next = TABS.length - 1;
        if (next < 0) return null;
        return select(TABS[next].id) ? TABS[next].id : null;
    }

    function scope() {
        return { type: 'user', id: state.userId };
    }

    // Navigation away: no further selection, in-flight calls aborted.
    function dispose() {
        if (disposed) return;
        disposed = true;
        client.switchContext({ accountId: null, dictationId: null });
    }

    setUser(deps.currentUser);
    return { state, setUser, load, select, onKeydown, scope, dispose };
}

function box(kind) {
    const s = surfaceFor(kind);
    return { background: s.background, color: s.color, borderColor: s.border };
}

export const VOCABULARY_VIEW_TEMPLATE = `
<section class="v8-page clinic-vocabulary-view" data-testid="clinic-vocabulary-view" aria-labelledby="cvv-title">
  <div class="pagehead">
    <div>
      <div class="eyebrow">${TEXT.eyebrow}</div>
      <h1 id="cvv-title">${TEXT.title}</h1>
      <p class="subline">${TEXT.subline}</p>
    </div>
    <div class="head-actions"></div>
  </div>
  <p v-if="state.status === 'denied'" role="alert" class="note burgundy" :style="errorStyle">${TEXT.denied}</p>
  <p v-else-if="state.status === 'loading'" role="status" aria-live="polite" class="note">${TEXT.loading}</p>
  <p v-else-if="state.status === 'forbidden'" role="alert" data-testid="forbidden" class="note burgundy" :style="errorStyle">${TEXT.forbidden}</p>
  <p v-else-if="state.status === 'error'" role="alert" data-testid="load-error" class="note burgundy" :style="errorStyle">${TEXT.failed} {{ state.message }}
    <button type="button" class="small" @click="load()">${TEXT.retry}</button></p>
  <template v-else>
    <div class="tabs" role="tablist" style="overflow-x:auto" aria-label="${TEXT.tablist}" @keydown="onKey">
      <button v-for="t in tabs" :key="t.id" type="button" role="tab" :id="'cvv-tab-' + t.id"
        :aria-selected="state.active === t.id ? 'true' : 'false'" :aria-controls="'cvv-panel-' + t.id"
        :tabindex="state.active === t.id ? 0 : -1" @click="select(t.id)">{{ t.label }}</button>
    </div>
    <div role="tabpanel" :id="'cvv-panel-' + state.active" :aria-labelledby="'cvv-tab-' + state.active">
      <div class="panel" style="overflow-x:auto">
        <component :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active">
          <template #empty="{ scope, query }"><span data-testid="empty">{{ query.q
            ? 'Keine passenden Wörter für diese Suche.'
            : scope.kind === 'pool' ? 'Keine passenden Wörter in diesem Autorenpool.' : text.empty }}</span></template>
        </component>
      </div>
      <div v-if="state.active === 'proposals'" class="panel" style="overflow-x:auto" data-testid="caution-panel">
        <component :is="cautionComponent" :key="state.epoch + ':caution'" />
      </div>
    </div>
  </template>
</section>`;

export const VocabularyView = {
    name: 'ClinicVocabularyView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: VOCABULARY_VIEW_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        if (!Vue) throw new Error('Vue is required to mount the vocabulary view');
        const ctrl = createVocabularyViewController({
            client: props.client,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const panels = createPanels(Vue);
        const components = {};
        // Built on first use, so only visited children are ever created.
        const build = {
            words: () => createVocabularyComponent(Vue, props.client, () => ctrl.state.userId),
            proposals: () => panels.review,
            pronunciation: () => createPronunciationHintsComponent(Vue),
            engine: () => panels.asr,
        };
        const activeComponent = Vue.computed(() => {
            const id = ctrl.state.active;
            if (!components[id]) components[id] = Vue.markRaw(build[id]());
            return components[id];
        });
        // Vorbehaltswörter live under Vorschläge; built only when that tab is shown.
        const cautionComponent = Vue.computed(() => {
            if (!components.caution) components.caution = Vue.markRaw(createCautionComponent(Vue, props.client, () => ctrl.state.userId));
            return components.caution;
        });
        const activeProps = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'words') return {};
            if (id === 'engine') return { client: props.client };
            return { client: props.client, scope: ctrl.scope() };
        });
        Vue.watch(() => userIdOf(props.currentUser), () => { ctrl.setUser(props.currentUser); ctrl.load(); });
        Vue.onMounted(() => ctrl.load());
        Vue.onBeforeUnmount(() => ctrl.dispose());
        function onKey(e) {
            const next = ctrl.onKeydown(e);
            if (!next) return;
            if (e.preventDefault) e.preventDefault();
            const tab = e.currentTarget && e.currentTarget.querySelector
                ? e.currentTarget.querySelector('#cvv-tab-' + next) : null;
            if (tab) tab.focus();
        }
        return {
            state: ctrl.state, text: TEXT, tabs: TABS, select: ctrl.select, load: ctrl.load, onKey,
            activeComponent, activeProps, cautionComponent, errorStyle: box('error'),
        };
    },
};

export function registerVocabularyView(app) {
    app.component('clinic-vocabulary-view', VocabularyView);
    return app;
}
