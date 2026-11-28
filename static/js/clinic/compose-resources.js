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
import { createVocabularyReviewController, REVIEW_TEMPLATE } from './vocabulary-review-ui.js';
import { createVoiceCommandsComponent } from './voice-commands-ui.js';
import { createAsrProfilesController, asrCapabilityLabel } from './asr-profiles-ui.js';
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
    unavailable: 'Diese Funktion ist derzeit nicht verfügbar.',
});

const ENUM_LABELS = Object.freeze({
    contains: 'Enthält', prefix: 'Beginnt mit', suffix: 'Endet mit', exact: 'Genau gleich',
    active: 'Aktiv', suppressed: 'Unterdrückt', all: 'Alle',
    user: 'Persönlich übernommen', pool: 'In den Pool übernommen',
    reject: 'Abgelehnt', defer: 'Zurückgestellt', undo: 'Rückgängig gemacht',
    PENDING: 'Ausstehend', DEFERRED: 'Zurückgestellt', REJECTED: 'Abgelehnt',
    ACCEPTED_USER: 'Persönlich übernommen', ACCEPTED_POOL: 'In den Pool übernommen',
    batch: 'Stapelverarbeitung', live: 'Live-Erkennung', hotword: 'Wortschatzhinweise',
    focus_editor: 'Editor fokussieren', open_editor: 'Editor öffnen', insert_marker: 'Markierung einfügen',
    next_marker: 'Nächste Markierung', previous_marker: 'Vorherige Markierung',
    send_dictation: 'Diktat senden', release_dictation: 'Diktat freigeben',
    macro_too_deep: 'Das Makro ist zu tief verschachtelt.', macro_cycle: 'Das Makro verweist auf sich selbst.',
    invalid_step: 'Ein Makroschritt ist ungültig.', unknown_action: 'Eine Aktion ist unbekannt.',
    macro_too_long: 'Das Makro ist zu lang.',
});

function enumLabel(value) {
    return Object.hasOwn(ENUM_LABELS, value) ? ENUM_LABELS[value] : 'Unbekannt';
}

function errorMessage({ code, message }) {
    return code === 'CAPABILITY_UNAVAILABLE' || message?.includes('CAPABILITY_UNAVAILABLE')
        ? LABELS.unavailable : message;
}

// These two child components keep the response message but discard its code.
function withResourceMessages(client) {
    const localize = (result) => {
        const message = errorMessage(result);
        return message === result.message ? result : { ...result, message };
    };
    return {
        request: async (...args) => localize(await client.request(...args)),
        command: async (...args) => localize(await client.command(...args)),
    };
}

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

const ASR_TEMPLATE = `
<section class="panel" role="form" :aria-label="view.title" :aria-busy="view.loading || view.busy" @keydown.esc="onKey">
  <h2>Profilkonfiguration</h2>
  <p v-if="view.loading" class="text-status" role="status">Sprachprofile werden geladen …</p>
  <p v-if="view.notice" class="note" role="status">{{ view.notice }}</p>
  <div v-if="view.error" class="note burgundy" role="alert">
    {{ errorMessage(view.error) }}
    <button class="small" type="button" :disabled="view.loading || view.busy" @click="controller.load()">{{ view.labels.reload }}</button>
  </div>
  <p class="hint" id="res-asr-source">{{ view.labels.source }}<template v-if="view.profileMode === 'separate'"> für Live-Erkennung</template></p>
  <div class="actions" role="radiogroup" aria-labelledby="res-asr-source" @keydown="onSourceKey">
    <button v-for="s in view.sources" :key="s.value" type="button" role="radio" class="asr-source"
      :disabled="!view.ready || view.busy"
      :aria-checked="s.selected ? 'true' : 'false'" :tabindex="s.selected ? 0 : -1"
      @click="controller.setSource(s.value)">{{ s.value === 'recorder' ? 'Aufnahmegerät' : s.label }}</button>
  </div>
  <div class="field-grid" style="margin-block-start:var(--sp-3)">
    <div v-if="view.profileMode" class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-mode">{{ view.labels.profileMode }}</label>
      <select id="res-asr-mode" :disabled="!view.ready || view.busy" :value="view.profileMode" @change="controller.setProfileMode($event.target.value)">
        <option v-for="o in view.profileModes" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
    <div v-if="view.profileMode === 'separate'" class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-batch-source">{{ view.labels.batchSource }}</label>
      <select id="res-asr-batch-source" :disabled="!view.ready || view.busy" :value="view.batchSource || ''" @change="controller.setBatchSource($event.target.value)">
        <option disabled value="">Quelle wählen</option>
        <option v-for="o in view.sources" :key="o.value" :value="o.value">{{ o.value === 'recorder' ? 'Aufnahmegerät' : o.label }}</option>
      </select>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-save-rule">{{ view.labels.saveRule }}</label>
      <select id="res-asr-save-rule" :disabled="!view.ready || view.busy" :value="view.saveRule" @change="controller.setSaveRule($event.target.value)">
        <option v-for="o in view.saveRules" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
    <div v-if="!view.profileMode" class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-live">{{ view.labels.live }}</label>
      <select id="res-asr-live" :disabled="!view.ready || view.busy" :value="selected(view.live)" @change="controller.setProfile('live', $event.target.value || null)">
        <option value="">Kein Profil</option><option v-for="o in view.live" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
    <div v-if="!view.profileMode" class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-batch">{{ view.labels.batch }}</label>
      <select id="res-asr-batch" :disabled="!view.ready || view.busy" :value="selected(view.batch)" @change="controller.setProfile('batch', $event.target.value || null)">
        <option value="">Kein Profil</option><option v-for="o in view.batch" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:transparent">
      <label for="res-asr-wait">{{ view.labels.wait }}</label>
      <input id="res-asr-wait" type="number" min="1" inputmode="numeric" autocomplete="off"
        :disabled="!view.ready || view.busy" :value="view.skipWaitMinutes"
        @change="controller.setSkipWaitMinutes($event.target.value)">
    </div>
  </div>
  <template v-if="view.ready && !view.error">
    <h3>Profilstand</h3>
    <dl class="keyvalues">
      <div><dt>{{ view.labels.profile }}</dt><dd>{{ view.profile ? view.profile.name : 'Kein Serverprofil' }}</dd></div>
      <div v-if="view.profile"><dt>Version</dt><dd>{{ view.profile.version }}</dd></div>
      <div><dt>{{ view.labels.engine }}</dt><dd>{{ view.engine.label }} · Lokal</dd></div>
      <div><dt>{{ view.labels.prompts }}</dt><dd>{{ view.prompts.label }}</dd></div>
      <div v-if="view.output"><dt>{{ view.labels.format }}</dt><dd>{{ view.output.container }} · {{ view.output.codec }} · {{ view.output.sampleRate }} Hz</dd></div>
      <div><dt>Cloudumleitung</dt><dd>Nicht vorgesehen</dd></div>
    </dl>
    <p v-if="view.engine.fallback" class="note warning" role="status">{{ view.engine.fallbackLabel }}</p>
    <h3>Fähigkeiten und Nachweise</h3>
    <div v-if="view.capabilities.length" class="table-wrap">
      <table aria-label="Fähigkeiten und Nachweise">
        <thead><tr><th scope="col">Fähigkeit</th><th scope="col">Nachweis / Grenze</th></tr></thead>
        <tbody><tr v-for="c in view.capabilities" :key="c.name"><td>{{ asrCapabilityLabel(c.name) }}</td><td><span class="tag" :class="c.proven ? '' : 'wait'">{{ c.label }}</span></td></tr></tbody>
      </table>
    </div>
    <p v-else class="hint">Keine Fähigkeiten konfiguriert. Es liegt kein Wirkungsnachweis vor.</p>
    <p v-if="view.profileMode" class="hint">Getrennte Quellen verwenden dasselbe Erkennungsmodell. Getrennte Akustikprofile werden nicht unterstützt.</p>
    <p class="note">Eine Wortliste ersetzt kein Akustiktraining. Korrekturtexte benötigen eine bewusste Freigabe und einen geprüften Audioabgleich.</p>
  </template>
  <div class="actions">
    <button class="primary asr-profile-save" style="font-size:13px;padding:9px 13px" type="button" :disabled="!view.canSave" @click="controller.save()">{{ view.labels.save }}</button>
    <button type="button" :disabled="!view.dirty || view.busy" @click="controller.cancel()">{{ view.labels.cancel }}</button>
  </div>
</section>`;

// Panels for the two children that ship a controller but no component.
export function createPanels(Vue) {
    function surfaceStyle(s) {
        return { background: s.background, color: s.color, borderColor: s.border };
    }

    function usePanel(controller, load = () => controller.load()) {
        const view = Vue.shallowRef(controller.view());
        const off = controller.subscribe(() => { view.value = controller.view(); });
        Vue.onMounted(load);
        Vue.onBeforeUnmount(off);
        return { view, errorStyle: style('error'), surfaceStyle, enumLabel, errorMessage };
    }

    const review = {
        name: 'ClinicVocabularyReviewPanel',
        props: { client: { type: Object, required: true }, scope: { type: Object, required: true } },
        template: REVIEW_TEMPLATE,
        setup(props) {
            const controller = createVocabularyReviewController({ client: props.client, scope: props.scope });
            const reviewPools = Vue.ref([]);
            const reviewPoolsFailed = Vue.ref(false);
            const selectedPool = Vue.ref('');
            async function loadReviewPools() {
                reviewPoolsFailed.value = false;
                const res = await props.client.request('/resources/vocabulary/reviews?catalog=author_pools');
                if (res.outcome === 'ok') reviewPools.value = res.data?.author_pools || [];
                else if (res.outcome !== 'cancelled') reviewPoolsFailed.value = true;
            }
            function selectReviewPool(id) {
                if (id === selectedPool.value || controller.state.loading || controller.state.busy) return;
                if (id && !reviewPools.value.some((pool) => pool.id === id)) return;
                selectedPool.value = id;
                return controller.setScope(id ? { type: 'author_pool', id } : props.scope);
            }
            const panel = usePanel(controller, () => Promise.all([controller.load(), loadReviewPools()]));
            function onKey(e) {
                const el = e.target && e.target.closest ? e.target.closest('[data-id]') : null;
                if (!el) return;
                const id = el.dataset.id;
                const next = controller.onKeydown(e, id);
                if (next) e.preventDefault?.();
                if (next && next !== id && e.currentTarget && e.currentTarget.querySelector) {
                    const target = e.currentTarget.querySelector('[data-id="' + next + '"]');
                    if (target) target.focus();
                }
            }
            return Object.assign(panel, { controller, onKey, reviewPools, reviewPoolsFailed, selectedPool, loadReviewPools, selectReviewPool });
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
            const onKey = (e) => controller.onKeydown(e);
            function onSourceKey(e) {
                if (!e.key.startsWith('Arrow') || !onKey(e)) return;
                e.preventDefault();
                const group = e.currentTarget;
                Vue.nextTick(() => group.querySelector('[aria-checked="true"]')?.focus());
            }
            return Object.assign(panel, { controller, selected, onKey, onSourceKey, asrCapabilityLabel });
        },
    };

    return { review, asr };
}

export const RESOURCES_TEMPLATE = `
<div class="clinic-resources-view cl-view v8-page">
  <h1 class="cl-title">${LABELS.tablist}</h1>
  <p v-if="state.denied" role="alert">${LABELS.denied}</p>
  <template v-else>
    <div class="tabs" role="tablist" aria-label="${LABELS.tablist}" @keydown="onKey"
      style="display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 9rem), 1fr)); max-width: 37rem">
      <button v-for="t in tabs" :key="t.id" type="button" role="tab" class="tab justify-center" :id="'res-tab-' + t.id"
        :class="{ 'is-active': state.active === t.id }"
        :aria-selected="state.active === t.id ? 'true' : 'false'" :aria-controls="'res-panel-' + t.id"
        :tabindex="state.active === t.id ? 0 : -1" @click="select(t.id)">{{ t.label }}</button>
    </div>
    <div v-for="t in tabs" :key="t.id" role="tabpanel" :id="'res-panel-' + t.id" :aria-labelledby="'res-tab-' + t.id"
      :hidden="state.active !== t.id" tabindex="0">
      <component v-if="state.active === t.id" :is="activeComponent" v-bind="activeProps" :key="state.epoch + ':' + t.id"></component>
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
            vocabulary: () => createVocabularyComponent(Vue, withResourceMessages(props.client), () => ctrl.state.userId),
            review: () => panels.review,
            commands: () => {
                const child = createVoiceCommandsComponent(Vue);
                return {
                    ...child,
                    setup: ({ client, scope }, context) => child.setup({ client: withResourceMessages(client), scope }, context),
                };
            },
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
