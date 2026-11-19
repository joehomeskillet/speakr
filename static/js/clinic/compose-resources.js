/**
 * Clinic resources view (w216): one tabbed Vue view composed from the existing
 * child modules vocabulary-ui, vocabulary-review-ui, voice-commands-ui and
 * asr-profiles-ui. PAR-VOC-001, PAR-ASR-002.
 *
 * Contract for the mount package (w217):
 *   ResourcesView                  Vue options object, props client, currentUser, vue (optional)
 *   registerResourcesView(app)     app.component('clinic-resources-view', ResourcesView)
 *
 * Only the active child is mounted. A change of the current user switches the
 * api client context (aborting in-flight calls) and remounts the child, so no
 * state of the previous user survives. Leaving the view releases the review and
 * ASR subscriptions and aborts in-flight calls. Server state is authoritative:
 * each child reloads capabilities and versions itself. Nothing is written to
 * browser storage; colours come only from the token contract.
 */

import { createVocabularyComponent } from './vocabulary-ui.js';
import { createVocabularyReviewController } from './vocabulary-review-ui.js';
import { createVoiceCommandsComponent } from './voice-commands-ui.js';
import { createAsrProfilesController } from './asr-profiles-ui.js';
import { surfaceFor } from './tokens.js';

export const TABS = Object.freeze([
    Object.freeze({ id: 'vocabulary', label: 'Vokabular' }),
    Object.freeze({ id: 'review', label: 'Vokabelprüfung' }),
    Object.freeze({ id: 'commands', label: 'Sprachbefehle' }),
    Object.freeze({ id: 'asr', label: 'ASR-Profile' }),
]);

export const LABELS = Object.freeze({
    tablist: 'Ressourcen',
    denied: 'Kein Zugriff. Bitte anmelden.',
});

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

/**
 * Framework-free state: active tab, keyboard movement, current-user scope and release.
 * @param {{client: {switchContext: Function}, currentUser: object, makeState?: (o: object) => object}} deps
 */
export function createResourcesController(deps) {
    const client = deps.client;
    const state = (deps.makeState || ((o) => o))({ active: TABS[0].id, userId: null, epoch: 0, denied: true });
    let disposed = false;

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.active = TABS[0].id;
        state.epoch += 1;
        client.switchContext({ accountId: id, dictationId: null });
    }

    function select(id) {
        if (disposed || state.denied || !TABS.some((t) => t.id === id)) return false;
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
    return { state, setUser, select, onKeydown, scope, dispose };
}

function style(kind) {
    const s = surfaceFor(kind);
    return { background: s.background, color: s.color, borderColor: s.border };
}

const REVIEW_TEMPLATE = `
<section role="region" :aria-label="view.title" @keydown="onKey">
  <h2>{{ view.title }}</h2>
  <p v-if="view.empty" role="status" aria-live="polite">{{ view.empty }}</p>
  <p v-if="view.error" role="alert" :style="errorStyle">{{ view.error.message }}
    <button type="button" v-if="view.error.retryable" @click="controller.retry()">{{ view.labels.retry }}</button></p>
  <button type="button" v-if="view.stale" @click="controller.load()">{{ view.labels.reload }}</button>
  <ul role="list" :aria-label="view.title">
    <li v-for="i in view.items" :key="i.id" :data-id="i.id" tabindex="0">
      <strong>{{ i.writtenForm }}</strong> <span v-if="i.spokenForm">({{ i.spokenForm }})</span>
      <span :style="surfaceStyle(i.surface)">{{ i.statusLabel }}</span>
      <span v-if="i.notice" role="status">{{ i.notice }}</span>
      <button type="button" :disabled="!i.actions.acceptUser.enabled" @click="controller.acceptUser(i.id)">{{ view.labels.acceptUser }}</button>
      <button type="button" :disabled="!i.actions.acceptPool.enabled" @click="controller.acceptPool(i.id)">{{ view.labels.acceptPool }}</button>
      <button type="button" :disabled="!i.actions.reject.enabled" @click="controller.reject(i.id)">{{ view.labels.reject }}</button>
      <button type="button" :disabled="!i.actions.defer.enabled" @click="controller.defer(i.id)">{{ view.labels.defer }}</button>
      <button type="button" :disabled="!i.actions.undo.enabled" @click="controller.undo(i.id)">{{ view.labels.undo }}</button>
    </li>
  </ul>
  <ol role="log" :aria-label="view.labels.history"><li v-for="(h, n) in view.history" :key="n">{{ h.itemId }} {{ h.action }} {{ h.status }}</li></ol>
</section>`;

const ASR_TEMPLATE = `
<section role="form" :aria-label="view.title" @keydown="onKey">
  <h2>{{ view.title }}</h2>
  <p v-if="view.notice" role="status" aria-live="polite">{{ view.notice }}</p>
  <p v-if="view.error" role="alert" :style="errorStyle">{{ view.error.message }}
    <button type="button" v-if="view.error.retryable" @click="controller.load()">{{ view.labels.retry }}</button></p>
  <div role="radiogroup" :aria-label="view.labels.source">
    <button v-for="s in view.sources" :key="s.value" type="button" role="radio"
      :aria-checked="s.selected ? 'true' : 'false'" :tabindex="s.selected ? 0 : -1"
      @click="controller.setSource(s.value)">{{ s.label }}</button>
  </div>
  <label for="res-asr-live">{{ view.labels.live }}</label>
  <select id="res-asr-live" :value="selected(view.live)" @change="controller.setProfile('live', $event.target.value || null)">
    <option value="">-</option><option v-for="o in view.live" :key="o.value" :value="o.value">{{ o.label }}</option>
  </select>
  <label for="res-asr-batch">{{ view.labels.batch }}</label>
  <select id="res-asr-batch" :value="selected(view.batch)" @change="controller.setProfile('batch', $event.target.value || null)">
    <option value="">-</option><option v-for="o in view.batch" :key="o.value" :value="o.value">{{ o.label }}</option>
  </select>
  <p :style="surfaceStyle(view.engine.surface)">{{ view.labels.engine }}: {{ view.engine.label }} <span v-if="view.engine.fallback">{{ view.engine.fallbackLabel }}</span></p>
  <p>{{ view.labels.prompts }}: {{ view.prompts.label }}</p>
  <ul><li v-for="c in view.capabilities" :key="c.name">{{ c.name }}: {{ c.label }}</li></ul>
  <button type="button" :disabled="!view.dirty || controller.state.busy" @click="controller.save()">{{ view.labels.save }}</button>
  <button type="button" :disabled="!view.dirty" @click="controller.cancel()">{{ view.labels.cancel }}</button>
</section>`;

// Panels for the two children that ship a controller but no component.
export function createPanels(Vue) {
    function surfaceStyle(s) {
        return { background: s.background, color: s.color, borderColor: s.border };
    }

    function usePanel(controller) {
        const view = Vue.shallowRef(controller.view());
        const off = controller.subscribe(() => { view.value = controller.view(); });
        Vue.onMounted(() => controller.load());
        Vue.onBeforeUnmount(off);
        return { view, errorStyle: style('error'), surfaceStyle };
    }

    const review = {
        name: 'ClinicVocabularyReviewPanel',
        props: { client: { type: Object, required: true }, scope: { type: Object, required: true } },
        template: REVIEW_TEMPLATE,
        setup(props) {
            const controller = createVocabularyReviewController({ client: props.client, scope: props.scope });
            const panel = usePanel(controller);
            function onKey(e) {
                const el = e.target && e.target.closest ? e.target.closest('[data-id]') : null;
                const id = el ? el.dataset.id : null;
                const next = controller.onKeydown(e, id);
                if (next && next !== id && e.currentTarget && e.currentTarget.querySelector) {
                    const target = e.currentTarget.querySelector('[data-id="' + next + '"]');
                    if (target) target.focus();
                }
            }
            return Object.assign(panel, { controller, onKey });
        },
    };

    const asr = {
        name: 'ClinicAsrProfilesPanel',
        props: { client: { type: Object, required: true } },
        template: ASR_TEMPLATE,
        setup(props) {
            const controller = createAsrProfilesController({ client: props.client });
            const panel = usePanel(controller);
            const selected = (list) => (list.find((o) => o.selected) || { value: '' }).value;
            return Object.assign(panel, { controller, selected, onKey: (e) => controller.onKeydown(e) });
        },
    };

    return { review, asr };
}

export const RESOURCES_TEMPLATE = `
<div class="clinic-resources-view">
  <p v-if="state.denied" role="alert">${LABELS.denied}</p>
  <template v-else>
    <div role="tablist" aria-label="${LABELS.tablist}" @keydown="onKey">
      <button v-for="t in tabs" :key="t.id" type="button" role="tab" :id="'res-tab-' + t.id"
        :aria-selected="state.active === t.id ? 'true' : 'false'" :aria-controls="'res-panel-' + t.id"
        :tabindex="state.active === t.id ? 0 : -1" @click="select(t.id)">{{ t.label }}</button>
    </div>
    <div role="tabpanel" :id="'res-panel-' + state.active" :aria-labelledby="'res-tab-' + state.active">
      <component :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + state.active"></component>
    </div>
  </template>
</div>`;

export const ResourcesView = {
    name: 'ClinicResourcesView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: RESOURCES_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        const ctrl = createResourcesController({
            client: props.client,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const panels = createPanels(Vue);
        const components = {};
        // Built on first use, so only visited children are ever created.
        const build = {
            vocabulary: () => createVocabularyComponent(Vue, props.client),
            review: () => panels.review,
            commands: () => createVoiceCommandsComponent(Vue),
            asr: () => panels.asr,
        };
        const activeComponent = Vue.computed(() => {
            const id = ctrl.state.active;
            if (!components[id]) components[id] = Vue.markRaw(build[id]());
            return components[id];
        });
        const activeProps = Vue.computed(() => {
            const id = ctrl.state.active;
            if (id === 'vocabulary') return {};
            if (id === 'asr') return { client: props.client };
            return { client: props.client, scope: ctrl.scope() };
        });
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        Vue.onBeforeUnmount(() => ctrl.dispose());
        function onKey(e) {
            const next = ctrl.onKeydown(e);
            if (!next) return;
            if (e.preventDefault) e.preventDefault();
            const tab = e.currentTarget && e.currentTarget.querySelector
                ? e.currentTarget.querySelector('#res-tab-' + next) : null;
            if (tab) tab.focus();
        }
        return { state: ctrl.state, tabs: TABS, select: ctrl.select, onKey, activeComponent, activeProps };
    },
};

export function registerResourcesView(app) {
    app.component('clinic-resources-view', ResourcesView);
}
