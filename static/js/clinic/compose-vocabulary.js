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
 * Review-marker words (Vorbehaltswortliste, W11) have no editing service yet; the
 * Vorschläge tab covers the review queue only.
 */

import { createVocabularyComponent } from './vocabulary-ui.js';
import { createVoiceCommandsComponent } from './voice-commands-ui.js';
import { createPanels } from './compose-resources.js';
import { surfaceFor } from './tokens.js';

export const TABS = Object.freeze([
    Object.freeze({ id: 'words', label: 'Wörter' }),
    Object.freeze({ id: 'proposals', label: 'Vorschläge' }),
    Object.freeze({ id: 'pronunciation', label: 'Aussprache' }),
    Object.freeze({ id: 'engine', label: 'Enginewirkung' }),
]);

export const TEXT = Object.freeze({
    title: 'Wortschatz',
    tablist: 'Wortschatz-Bereiche',
    loading: 'Scope und Revision werden geladen …',
    denied: 'Kein Zugriff. Bitte anmelden.',
    forbidden: 'Keine Berechtigung für den Wortschatz.',
    failed: 'Der Wortschatz konnte nicht geladen werden.',
    retry: 'Erneut versuchen',
    empty: 'Noch keine Wörter in Ihrer persönlichen Liste. Wörter können Sie unten hinzufügen oder importieren.',
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
<section class="clinic-vocabulary-view" data-testid="clinic-vocabulary-view" aria-labelledby="cvv-title">
  <h1 id="cvv-title">${TEXT.title}</h1>
  <p v-if="state.status === 'denied'" role="alert" :style="errorStyle">${TEXT.denied}</p>
  <p v-else-if="state.status === 'loading'" role="status" aria-live="polite">${TEXT.loading}</p>
  <p v-else-if="state.status === 'forbidden'" role="alert" data-testid="forbidden" :style="errorStyle">${TEXT.forbidden}</p>
  <p v-else-if="state.status === 'error'" role="alert" data-testid="load-error" :style="errorStyle">${TEXT.failed} {{ state.message }}
    <button type="button" @click="load()">${TEXT.retry}</button></p>
  <template v-else>
    <p v-if="state.empty" role="status" data-testid="empty">${TEXT.empty}</p>
    <div role="tablist" aria-label="${TEXT.tablist}" @keydown="onKey">
      <button v-for="t in tabs" :key="t.id" type="button" role="tab" :id="'cvv-tab-' + t.id"
        :aria-selected="state.active === t.id ? 'true' : 'false'" :aria-controls="'cvv-panel-' + t.id"
        :tabindex="state.active === t.id ? 0 : -1" @click="select(t.id)">{{ t.label }}</button>
    </div>
    <div role="tabpanel" :id="'cvv-panel-' + state.active" :aria-labelledby="'cvv-tab-' + state.active">
      <component :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active"></component>
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
            words: () => createVocabularyComponent(Vue, props.client),
            proposals: () => panels.review,
            pronunciation: () => createVoiceCommandsComponent(Vue),
            engine: () => panels.asr,
        };
        const activeComponent = Vue.computed(() => {
            const id = ctrl.state.active;
            if (!components[id]) components[id] = Vue.markRaw(build[id]());
            return components[id];
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
            state: ctrl.state, tabs: TABS, select: ctrl.select, load: ctrl.load, onKey,
            activeComponent, activeProps, errorStyle: box('error'),
        };
    },
};

export function registerVocabularyView(app) {
    app.component('clinic-vocabulary-view', VocabularyView);
    return app;
}
