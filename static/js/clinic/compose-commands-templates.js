/**
 * Composed clinic view "Befehle und Vorlagen" (V8 P14, PAR-VOC-008, PAR-CFG-002).
 *
 * One Vue view under Verwaltung with three resource kinds: Befehle, Bausteine,
 * Vorlagen. It owns only the kind switch and the permission/empty/error states;
 * the editing surfaces are the existing modules:
 *   - Befehle:   createVoiceCommandsComponent (voice-commands-ui.js)
 *   - Vorlagen:  templatesComponent (templates-ui.js; versions, marker, preview)
 *   - Bausteine: no server resource exists yet, the kind shows the open part.
 * Requests go through the injected api-client.js client, colours through
 * tokens.js. Nothing is written to browser storage. Foreign resources are never
 * loaded: without a current user nothing is requested, a 401/403 answer shows
 * the "no rights" state and mounts no editor.
 *
 * Editor hook (UI-ED-18): the view exposes the named slot "editor-toolbar"
 * (EDITOR_HOOK.slot) above the resource editor. The consumer package that
 * reuses the correction editor toolbar fills it; this module builds no editor.
 *
 * Contract for the mount package:
 *   CommandsTemplatesView           Vue options; props client, currentUser, vue (optional)
 *   registerCommandsTemplatesView(app)  app.component('clinic-commands-templates-view', ...)
 */

import { createVoiceCommandsComponent } from './voice-commands-ui.js';
import { templatesComponent } from './templates-ui.js';
import { surfaceFor } from './tokens.js';

export const COMMANDS_TEMPLATES_VERSION = 1;

export const KINDS = Object.freeze([
    Object.freeze({ id: 'commands', label: 'Befehle', path: '/resources/commands' }),
    Object.freeze({ id: 'blocks', label: 'Bausteine', path: null }),
    Object.freeze({ id: 'templates', label: 'Vorlagen', path: '/templates' }),
]);

export const TEXT = Object.freeze({
    eyebrow: 'Verwaltung',
    title: 'Befehle und Vorlagen',
    subline: 'Erlaubte Webbefehle und editierbare Textbausteine. Schutzbedürftige Aktionen erfordern eine bewusste Bestätigung.',
    kinds: 'Ressourcenwahl',
    reload: 'Neu laden',
    retry: 'Erneut versuchen',
    loading: 'Wird geladen.',
    denied: 'Kein Zugriff. Bitte anmelden.',
    forbidden: 'Keine Berechtigung für diese Ressourcen.',
    failed: 'Die Ressourcen konnten nicht geladen werden.',
    empty: Object.freeze({
        commands: 'Noch keine Sprachbefehle vorhanden.',
        templates: 'Keine Vorlagen vorhanden.',
    }),
    blocksOpen: 'Bausteine sind noch nicht verfügbar: Die Serverressource fehlt. Diese Teilanforderung bleibt offen.',
});

/** Documented hook for the shared correction-editor toolbar (UI-ED-18). */
export const EDITOR_HOOK = Object.freeze({ slot: 'editor-toolbar', consumer: 'UI-ED-18' });

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

function kindOf(id) {
    return KINDS.find((k) => k.id === id) || null;
}

/**
 * Framework-free state: kind switch, per-kind load status, current-user scope.
 * status: idle | loading | ready | empty | forbidden | unavailable | error
 * @param {{client: {request: Function, switchContext: Function}, currentUser: object, makeState?: (o: object) => object}} deps
 */
export function createCommandsTemplatesController(deps) {
    const client = deps.client;
    const state = (deps.makeState || ((o) => o))({
        kind: KINDS[0].id, userId: null, epoch: 0, denied: true, status: 'idle', message: '',
        commands: [], selectedId: null,
    });
    let disposed = false;
    let loadVersion = 0;

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.kind = KINDS[0].id;
        state.status = 'idle';
        state.commands = [];
        state.selectedId = null;
        state.message = '';
        state.epoch += 1;
        client.switchContext({ accountId: id, dictationId: null });
    }

    async function load() {
        if (disposed || state.denied) return { outcome: 'denied' };
        const kind = kindOf(state.kind);
        const epoch = state.epoch;
        const version = ++loadVersion;
        if (!kind.path) {
            state.status = 'unavailable';
            state.message = TEXT.blocksOpen;
            return { outcome: 'unavailable' };
        }
        state.status = 'loading';
        state.message = TEXT.loading;
        const res = await client.request(kind.path);
        if (disposed || epoch !== state.epoch || version !== loadVersion || kind.id !== state.kind) return { outcome: 'stale' };
        if (res.outcome === 'ok') {
            const items = (res.data && res.data.items) || [];
            state.commands = kind.id === 'commands' ? items : [];
            if (!state.commands.some((item) => item.id === state.selectedId)) state.selectedId = null;
            state.status = items.length ? 'ready' : 'empty';
            state.message = items.length ? '' : TEXT.empty[kind.id];
        } else {
            state.commands = [];
            state.selectedId = null;
            state.status = res.outcome === 'permission' ? 'forbidden' : 'error';
            state.message = state.status === 'forbidden' ? TEXT.forbidden : TEXT.failed;
        }
        return res;
    }

    function select(id) {
        if (disposed || state.denied || !kindOf(id)) return false;
        state.kind = id;
        state.status = 'idle';
        state.commands = [];
        state.selectedId = null;
        state.message = '';
        state.epoch += 1;
        return true;
    }

    // Kind keys: arrows wrap, Home/End jump. Returns the newly active id or null.
    function onKeydown(event) {
        const key = event && event.key;
        const at = KINDS.findIndex((k) => k.id === state.kind);
        let next = -1;
        if (key === 'ArrowRight' || key === 'ArrowDown') next = (at + 1) % KINDS.length;
        else if (key === 'ArrowLeft' || key === 'ArrowUp') next = (at - 1 + KINDS.length) % KINDS.length;
        else if (key === 'Home') next = 0;
        else if (key === 'End') next = KINDS.length - 1;
        if (next < 0) return null;
        return select(KINDS[next].id) ? KINDS[next].id : null;
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

export const COMMANDS_TEMPLATES_TEMPLATE = `
<section class="v8-page clinic-commands-templates-view" data-testid="clinic-commands-templates-view" aria-labelledby="cct-title">
  <div class="pagehead">
    <div>
      <div class="eyebrow">${TEXT.eyebrow}</div>
      <h1 id="cct-title">${TEXT.title}</h1>
      <p class="subline">${TEXT.subline}</p>
    </div>
    <div class="head-actions">
      <button type="button" v-if="!state.denied" class="icon-button" data-testid="reload" aria-label="${TEXT.reload}" title="${TEXT.reload}" @click="reload">
        <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 16 16"><path d="M13 8a5 5 0 1 1-1.5-3.5M13 2v3h-3" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      </button>
    </div>
  </div>
  <p v-if="state.denied" role="alert" class="note burgundy">${TEXT.denied}</p>
  <template v-else>
    <div class="tabs" role="tablist" aria-label="${TEXT.kinds}" @keydown="onKey">
      <button v-for="k in kinds" :key="k.id" type="button" role="tab" :id="'cct-tab-' + k.id"
        :aria-selected="state.kind === k.id ? 'true' : 'false'" :aria-controls="'cct-panel-' + k.id"
        :tabindex="state.kind === k.id ? 0 : -1" @click="choose(k.id)">{{ k.label }}</button>
    </div>
    <div role="tabpanel" :id="'cct-panel-' + state.kind" :aria-labelledby="'cct-tab-' + state.kind">
      <slot name="${EDITOR_HOOK.slot}"></slot>
      <p v-if="state.message && state.status !== 'empty'" role="status" aria-live="polite" data-testid="state-message" class="note" :class="noteClass" :style="messageStyle">{{ state.message }}</p>
      <button type="button" class="small" v-if="state.status === 'error'" data-testid="retry" @click="reload">${TEXT.retry}</button>
      <section v-if="showChild && state.kind === 'commands'" class="panel command-catalog" aria-labelledby="cct-overview">
        <h2 id="cct-overview">Befehlsübersicht</h2>
        <p v-if="!state.commands.length" class="hint" role="status">{{ state.message }}</p>
        <div v-else class="table-wrap">
          <table aria-labelledby="cct-overview">
            <thead><tr><th scope="col">Gesprochener Befehl</th><th scope="col">Aktion im Webclient</th></tr></thead>
            <tbody>
              <template v-for="item in state.commands" :key="item.id">
                <tr>
                  <th scope="row"><button type="button" class="command-phrase" :aria-expanded="state.selectedId === item.id"
                    @click="state.selectedId = state.selectedId === item.id ? null : item.id">{{ item.trigger_phrase }}</button></th>
                  <td>{{ actionLabel(item.action_type) }}</td>
                </tr>
                <tr v-if="state.selectedId === item.id"><td colspan="2" class="command-description">{{ item.description || 'Keine Beschreibung hinterlegt.' }}</td></tr>
              </template>
            </tbody>
          </table>
        </div>
      </section>
      <component v-if="showChild" :is="state.kind === 'commands' ? 'details' : 'div'" :key="state.kind"
        :class="{ 'command-editor': state.kind === 'commands' }">
        <summary v-if="state.kind === 'commands'">Befehl anlegen</summary>
        <div class="panel" style="overflow-x:auto">
          <component :is="childComponent" v-bind="childProps" :key="state.epoch + ':' + state.kind" @saved="reload"></component>
        </div>
      </component>
    </div>
  </template>
</section>`;

function messageStyle(status) {
    if (status === 'unavailable') return null; // The warning note supplies its own colours.
    const s = surfaceFor(status === 'error' || status === 'forbidden' ? 'error' : 'normal');
    return { background: s.background, color: s.color, borderColor: s.border };
}

export const CommandsTemplatesView = {
    name: 'ClinicCommandsTemplatesView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: COMMANDS_TEMPLATES_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        if (!Vue) throw new Error('Vue is required to mount the commands and templates view');
        const ctrl = createCommandsTemplatesController({
            client: props.client,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const components = {};
        // Existing building blocks, built on first use of their kind.
        const build = {
            commands: () => createVoiceCommandsComponent(Vue),
            templates: () => templatesComponent(Vue, { client: props.client }),
        };
        const showChild = Vue.computed(() => !ctrl.state.denied && (ctrl.state.status === 'ready' || ctrl.state.status === 'empty'));
        const childComponent = Vue.computed(() => {
            const id = ctrl.state.kind;
            if (!build[id]) return null;
            if (!components[id]) components[id] = Vue.markRaw(build[id]());
            return components[id];
        });
        const childProps = Vue.computed(() => (ctrl.state.kind === 'commands' ? { client: props.client, scope: ctrl.scope() } : {}));
        const actionLabels = { app_command: 'Webclient-Aktion', insert_text: 'Text einfügen', macro: 'Aktionsfolge' };
        const actionLabel = (type) => actionLabels[type] || 'Weitere Aktion';
        const reload = () => ctrl.load();
        const choose = (id) => { if (ctrl.select(id)) ctrl.load(); };
        Vue.watch(() => userIdOf(props.currentUser), () => { ctrl.setUser(props.currentUser); ctrl.load(); });
        Vue.onMounted(() => ctrl.load());
        Vue.onBeforeUnmount(() => ctrl.dispose());
        function onKey(e) {
            const next = ctrl.onKeydown(e);
            if (!next) return;
            if (e.preventDefault) e.preventDefault();
            ctrl.load();
            const tab = e.currentTarget && e.currentTarget.querySelector ? e.currentTarget.querySelector('#cct-tab-' + next) : null;
            if (tab) tab.focus();
        }
        return {
            state: ctrl.state, kinds: KINDS, choose, reload, onKey, actionLabel, showChild, childComponent, childProps,
            messageStyle: Vue.computed(() => messageStyle(ctrl.state.status)),
            noteClass: Vue.computed(() => (ctrl.state.status === 'error' || ctrl.state.status === 'forbidden' ? 'burgundy' : (ctrl.state.status === 'unavailable' ? 'warning' : ''))),
        };
    },
};

export function registerCommandsTemplatesView(app) {
    app.component('clinic-commands-templates-view', CommandsTemplatesView);
    return app;
}
