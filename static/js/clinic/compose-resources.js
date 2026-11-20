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

const VOCABULARY_TEMPLATE = `
<section class="cl-panel space-y-4" aria-labelledby="voc-title" @keydown="onKey">
  <h2 id="voc-title" class="cl-panel__title">{{ label('clinic.vocabulary.title') }}</h2>
  <div class="flex flex-wrap items-end gap-2">
    <button class="btn" :class="state.scope.kind === 'user' ? 'btn--primary' : 'btn--ghost'"
      type="button" :aria-pressed="state.scope.kind === 'user'" @click="store.setScope('user')">
      {{ label('clinic.vocabulary.scopeUser') }}
    </button>
    <form class="flex flex-wrap items-end gap-2 min-w-0" style="flex: 0 1 25rem" @submit.prevent="store.setScope('pool', poolDraft)">
      <div class="min-w-0" style="flex: 1 1 8rem">
        <label class="block text-sm mb-1" for="voc-pool">{{ label('clinic.vocabulary.pool') }}</label>
        <input class="field" id="voc-pool" type="text" autocomplete="off" v-model="poolDraft">
      </div>
      <button class="btn btn--ghost" type="submit">{{ label('clinic.vocabulary.scopePool') }} öffnen</button>
    </form>
  </div>
  <p v-if="state.scope.kind === 'pool'" class="cl-hint">
    {{ label('clinic.vocabulary.scope') }}: {{ label('clinic.vocabulary.scopePool') }} {{ state.scope.poolId }}
  </p>
  <form class="cl-toolbar items-end" @submit.prevent="store.search()">
    <div class="min-w-0" style="flex: 2 1 14rem">
      <label class="block text-sm mb-1" for="voc-q">{{ label('clinic.vocabulary.query') }}</label>
      <input class="field w-full" style="width: 100%" id="voc-q" type="search" data-role="query" autocomplete="off" v-model="state.query.q">
    </div>
    <div class="min-w-0">
      <label class="block text-sm mb-1" for="voc-mode">{{ label('clinic.vocabulary.mode') }}</label>
      <select class="field" id="voc-mode" v-model="state.query.mode">
        <option v-for="mode in modes" :key="mode" :value="mode">{{ enumLabel(mode) }}</option>
      </select>
    </div>
    <div class="min-w-0">
      <label class="block text-sm mb-1" for="voc-filter">{{ label('clinic.vocabulary.filter') }}</label>
      <select class="field" id="voc-filter" v-model="state.query.filter">
        <option v-for="filter in filters" :key="filter" :value="filter">{{ enumLabel(filter) }}</option>
      </select>
    </div>
    <button class="btn btn--primary" type="submit">{{ label('clinic.vocabulary.search') }}</button>
  </form>
  <p v-if="state.status === 'stale'" class="notice notice--warn" role="alert">{{ label('clinic.vocabulary.stale') }}</p>
  <p v-else-if="state.message" class="notice" :class="{ 'notice--danger': state.status === 'error' || state.status === 'denied' }"
    role="status">{{ state.message }}</p>
  <p v-else-if="state.status === 'loading'" class="cl-hint" role="status">Vokabular wird geladen …</p>
  <div v-if="state.entries.length" class="cl-tablewrap">
    <table class="cl-table" role="table" :aria-label="label('clinic.vocabulary.title')">
      <thead role="rowgroup"><tr role="row">
        <th role="columnheader" scope="col">{{ label('clinic.vocabulary.written') }}</th>
        <th role="columnheader" scope="col">{{ label('clinic.vocabulary.spoken') }}</th>
        <th role="columnheader" scope="col">{{ label('clinic.vocabulary.filter') }}</th>
        <th role="columnheader" scope="col"><span class="sr-only">Aktion</span></th>
      </tr></thead>
      <tbody role="rowgroup"><tr v-for="e in state.entries" :key="e.id" role="row" class="cl-row" style="cursor: default">
        <td role="cell" :data-label="label('clinic.vocabulary.written')" style="white-space: normal; overflow-wrap: anywhere">
          <strong>{{ e.written }}</strong>
        </td>
        <td role="cell" :data-label="label('clinic.vocabulary.spoken')" style="white-space: normal; overflow-wrap: anywhere">
          {{ e.spoken || '–' }}
        </td>
        <td role="cell" :data-label="label('clinic.vocabulary.filter')">{{ enumLabel(e.status) }}</td>
        <td role="cell" data-label="Aktion" class="text-right">
          <button class="btn btn--ghost btn--sm" type="button" v-if="e.status === 'active'" @click="store.suppress(e.id)">
            {{ label('clinic.vocabulary.suppress') }}
          </button>
          <button class="btn btn--ghost btn--sm" type="button" v-else @click="store.reactivate(e.id)">
            {{ label('clinic.vocabulary.reactivate') }}
          </button>
        </td>
      </tr></tbody>
    </table>
  </div>
  <div v-else-if="state.status === 'ready'" class="empty" role="status">
    <p class="empty__title">{{ label('clinic.vocabulary.empty') }}</p>
    <p class="empty__body">Für die aktuelle Suche und den gewählten Status gibt es keine Treffer.</p>
  </div>
  <div class="flex flex-wrap gap-2">
    <button class="btn btn--ghost" type="button" @click="store.reload()">{{ label('clinic.vocabulary.reload') }}</button>
    <button class="btn btn--ghost" type="button" v-if="state.failedWrite" @click="store.retry()">{{ label('clinic.vocabulary.retry') }}</button>
    <button class="btn btn--ghost" type="button" @click="addSelected()">{{ label('clinic.vocabulary.addSelection') }}</button>
  </div>
  <form class="space-y-2" @submit.prevent="doImport()">
    <label class="block text-sm" for="voc-import">{{ label('clinic.vocabulary.importLabel') }}</label>
    <textarea class="field block" id="voc-import" rows="4" v-model="importDraft"></textarea>
    <button class="btn btn--ghost" type="submit">{{ label('clinic.vocabulary.import') }}</button>
  </form>
  <div class="flex flex-wrap gap-2">
    <button class="btn btn--ghost" type="button" @click="store.exportList(false)">{{ label('clinic.vocabulary.export') }}</button>
    <button class="btn btn--ghost" type="button" @click="store.exportList(true)">{{ label('clinic.vocabulary.exportEffective') }}</button>
  </div>
  <div v-if="state.exportText" class="space-y-2">
    <label class="block text-sm" for="voc-export">{{ label('clinic.vocabulary.exported') }}</label>
    <textarea class="field block" id="voc-export" rows="4" readonly :value="state.exportText"></textarea>
  </div>
</section>`;

const REVIEW_TEMPLATE = `
<section class="cl-panel space-y-4" role="region" :aria-label="view.title" @keydown="onKey">
  <h2 class="cl-panel__title">{{ view.title }}</h2>
  <div v-if="view.error" class="notice notice--danger flex-wrap" role="alert">
    {{ errorMessage(view.error) }}
    <button class="btn btn--ghost" type="button" v-if="view.error.retryable" @click="controller.retry()">{{ view.labels.retry }}</button>
  </div>
  <p v-else-if="view.empty" class="empty empty__title" role="status">{{ view.empty }}</p>
  <button class="btn btn--ghost" type="button" v-if="view.stale" @click="controller.load()">{{ view.labels.reload }}</button>
  <ul v-if="view.items.length" class="space-y-3" role="list" :aria-label="view.title">
    <li v-for="i in view.items" :key="i.id" :data-id="i.id" tabindex="0" class="cl-panel space-y-2 cl-row break-words">
      <div class="flex flex-wrap items-center gap-2">
        <strong>{{ i.writtenForm }}</strong> <span v-if="i.spokenForm">({{ i.spokenForm }})</span>
        <span class="chip" :style="surfaceStyle(i.surface)">{{ i.statusLabel }}</span>
      </div>
      <p v-if="i.notice" role="status">{{ i.notice }}</p>
      <div class="flex flex-wrap gap-2">
        <button class="btn btn--ghost" type="button" :disabled="!i.actions.acceptUser.enabled" @click="controller.acceptUser(i.id)">{{ view.labels.acceptUser }}</button>
        <button class="btn btn--ghost" type="button" :disabled="!i.actions.acceptPool.enabled" @click="controller.acceptPool(i.id)">{{ view.labels.acceptPool }}</button>
        <button class="btn btn--ghost" type="button" :disabled="!i.actions.reject.enabled" @click="controller.reject(i.id)">{{ view.labels.reject }}</button>
        <button class="btn btn--ghost" type="button" :disabled="!i.actions.defer.enabled" @click="controller.defer(i.id)">{{ view.labels.defer }}</button>
        <button class="btn btn--ghost" type="button" :disabled="!i.actions.undo.enabled" @click="controller.undo(i.id)">{{ view.labels.undo }}</button>
      </div>
    </li>
  </ul>
  <ol role="log" :aria-label="view.labels.history">
    <li v-for="(h, n) in view.history" :key="n" class="break-words">{{ h.itemId }} · {{ enumLabel(h.action) }} · {{ enumLabel(h.status) }}</li>
  </ol>
</section>`;

const COMMANDS_TEMPLATE = `
<section class="cl-panel space-y-4" aria-labelledby="vc-title" @keydown.esc="reset">
  <h2 id="vc-title" class="cl-panel__title">{{ LABELS.title }}</h2>
  <p v-if="c.state.message" class="notice" :class="c.state.status === 'error' ? 'notice--danger' : 'notice--info'"
    role="status">{{ c.state.message }}</p>
  <form class="space-y-3" @submit.prevent="save">
    <div>
      <label class="block text-sm mb-1" for="vc-phrase">{{ LABELS.phrase }}</label>
      <input class="field" id="vc-phrase" v-model="form.phrase" type="text" autocomplete="off" required>
    </div>
    <div>
      <label class="block text-sm mb-1" for="vc-steps">{{ LABELS.steps }}</label>
      <textarea class="field block" id="vc-steps" v-model="form.stepsText" rows="4" aria-describedby="vc-preview"></textarea>
    </div>
    <div id="vc-preview" class="break-words">
      <strong>{{ LABELS.preview }}:</strong>
      <span v-if="!form.stepsText.trim()"> Noch keine Aktionen.</span>
      <span v-else-if="pv.ok"> {{ pv.steps.map(stepLabel).join(', ') }}</span>
      <span v-else role="alert"> {{ enumLabel(pv.error) }}</span>
    </div>
    <div v-if="needsConfirm" class="notice notice--warn flex-wrap">
      {{ LABELS.sensitive }} <button class="btn btn--ghost" type="button" @click="confirm">{{ LABELS.confirm }}</button>
    </div>
    <button class="btn btn--primary" type="submit" :disabled="c.state.status === 'saving'">{{ LABELS.save }}</button>
  </form>
</section>`;

const ASR_TEMPLATE = `
<section class="cl-panel space-y-4" role="form" :aria-label="view.title" @keydown.esc="onKey">
  <h2 class="cl-panel__title">{{ view.title }}</h2>
  <p v-if="view.notice" class="notice notice--info" role="status">{{ view.notice }}</p>
  <div v-if="view.error" class="notice notice--danger flex-wrap" role="alert">
    {{ errorMessage(view.error) }}
    <button class="btn btn--ghost" type="button" v-if="view.error.retryable" @click="controller.load()">{{ view.labels.retry }}</button>
  </div>
  <p class="text-sm" id="res-asr-source">{{ view.labels.source }}</p>
  <div class="flex flex-wrap gap-2" role="radiogroup" aria-labelledby="res-asr-source" @keydown="onSourceKey">
    <button v-for="s in view.sources" :key="s.value" type="button" role="radio" class="btn"
      :class="s.selected ? 'btn--primary' : 'btn--ghost'"
      :aria-checked="s.selected ? 'true' : 'false'" :tabindex="s.selected ? 0 : -1"
      @click="controller.setSource(s.value)">{{ s.value === 'recorder' ? 'Aufnahmegerät' : s.label }}</button>
  </div>
  <div class="grid md:grid-cols-2 gap-3">
    <div class="min-w-0">
      <label class="block text-sm mb-1" for="res-asr-live">{{ view.labels.live }}</label>
      <select class="field" id="res-asr-live" :value="selected(view.live)" @change="controller.setProfile('live', $event.target.value || null)">
        <option value="">Kein Profil</option><option v-for="o in view.live" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
    <div class="min-w-0">
      <label class="block text-sm mb-1" for="res-asr-batch">{{ view.labels.batch }}</label>
      <select class="field" id="res-asr-batch" :value="selected(view.batch)" @change="controller.setProfile('batch', $event.target.value || null)">
        <option value="">Kein Profil</option><option v-for="o in view.batch" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </div>
  </div>
  <template v-if="!view.error">
    <p class="notice" :class="{ 'notice--warn': view.engine.fallback }">
      <span>{{ view.labels.engine }}: {{ view.engine.label }} <span v-if="view.engine.fallback">{{ view.engine.fallbackLabel }}</span></span>
    </p>
    <p>{{ view.labels.prompts }}: {{ view.prompts.label }}</p>
    <ul v-if="view.capabilities.length"><li v-for="c in view.capabilities" :key="c.name">{{ enumLabel(c.name) }}: {{ c.label }}</li></ul>
  </template>
  <div class="flex flex-wrap gap-2">
    <button class="btn btn--primary" type="button" :disabled="!view.dirty || controller.state.busy" @click="controller.save()">{{ view.labels.save }}</button>
    <button class="btn btn--ghost" type="button" :disabled="!view.dirty" @click="controller.cancel()">{{ view.labels.cancel }}</button>
  </div>
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
        return { view, errorStyle: style('error'), surfaceStyle, enumLabel, errorMessage };
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
            const onKey = (e) => controller.onKeydown(e);
            function onSourceKey(e) {
                if (!e.key.startsWith('Arrow') || !onKey(e)) return;
                e.preventDefault();
                const group = e.currentTarget;
                Vue.nextTick(() => group.querySelector('[aria-checked="true"]')?.focus());
            }
            return Object.assign(panel, { controller, selected, onKey, onSourceKey });
        },
    };

    return { review, asr };
}

export const RESOURCES_TEMPLATE = `
<div class="clinic-resources-view cl-view">
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
            vocabulary: () => {
                const child = createVocabularyComponent(Vue, withResourceMessages(props.client));
                return {
                    ...child, template: VOCABULARY_TEMPLATE,
                    setup: () => ({ ...child.setup(), enumLabel }),
                };
            },
            review: () => panels.review,
            commands: () => {
                const child = createVoiceCommandsComponent(Vue);
                const stepLabel = (step) => step.startsWith('text:') ? step.slice(5) : enumLabel(step);
                return {
                    ...child, template: COMMANDS_TEMPLATE,
                    setup: ({ client, scope }) => ({
                        ...child.setup({ client: withResourceMessages(client), scope }), enumLabel, stepLabel,
                    }),
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
