/**
 * Personal settings view (v8-58, V8 P19 "Einstellungen"): one Vue view composed from the existing
 * modules preferences-ui (recipient default, workstation values, locked/central markers) and
 * correction-modes-ui (correction playback mode). PAR-CFG-001, PAR-ADM-011, PAR-COR-005.
 *
 * Contract for the mount package:
 *   SettingsView                   Vue options object, props client, currentUser, workstationId, t, vue (optional)
 *   registerSettingsView(app)      app.component('clinic-settings-view', SettingsView)
 *
 * The view adds only what the children do not show: the origin of each workstation value (system
 * default, pool default, personal, local, or centrally fixed). The origins are read from the
 * workstation answer the preferences child already requests (a tap on the shared client, no second
 * request). A refused request (403) hides the whole page content and shows only the denial, so no
 * system area is disclosed. A change of the current user switches the client context (aborting
 * in-flight calls) and remounts the children. Leaving the view aborts in-flight calls. The server is
 * authoritative for every value; nothing is written to browser storage; colours come from tokens.js.
 */

import { createPreferencesComponent } from './preferences-ui.js';
import { CorrectionModes } from './correction-modes-ui.js';
import { surfaceFor } from './tokens.js';

const WORKSTATION_PATH = /^\/preferences\/workstations\/[^/]+$/;

export const ORIGIN_LABELS = Object.freeze({
    system: 'Systemstandard',
    pool: 'Poolvorgabe',
    user: 'Persönlich',
    local: 'Lokal',
    central: 'Zentral festgelegt',
    ceiling: 'Zentral begrenzt',
});

export const LABELS = Object.freeze({
    title: 'Einstellungen',
    denied: 'Kein Zugriff. Bitte anmelden.',
    forbidden: 'Keine Berechtigung für diese Einstellungen.',
    origins: 'Herkunft der Werte',
    empty: 'Keine eigenen Werte. Es gelten die Standardwerte.',
    unknownOrigin: 'Unbekannte Herkunft',
});

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

export function originLabel(origin) {
    return Object.prototype.hasOwnProperty.call(ORIGIN_LABELS, origin) ? ORIGIN_LABELS[origin] : LABELS.unknownOrigin;
}

// Audio adapter for the correction mode child: this page plays nothing.
const NO_AUDIO = Object.freeze({
    play() {}, pause() {}, seek() {}, position: () => 0, isPlaying: () => false,
});

/**
 * Framework-free state: current-user scope, refusal flag and workstation origins.
 * @param {{client: {request: Function, switchContext: Function}, currentUser: object,
 *          makeState?: (o: object) => object}} deps
 */
export function createSettingsController(deps) {
    const client = deps.client;
    const state = (deps.makeState || ((o) => o))({
        userId: null, epoch: 0, denied: true, forbidden: false, origins: {},
    });
    let disposed = false;

    function note(path, options, res) {
        if (disposed || !res) return;
        if (res.outcome === 'permission' && res.status === 403) state.forbidden = true;
        const method = options && options.method ? String(options.method).toUpperCase() : 'GET';
        if (method === 'GET' && WORKSTATION_PATH.test(path) && res.outcome === 'ok') {
            const origins = res.data && typeof res.data.origins === 'object' ? res.data.origins : null;
            state.origins = origins ? Object.assign({}, origins) : {};
        }
    }

    // Same surface as the shared client; every answer is observed once.
    const tapped = {
        request: async (path, options) => {
            const res = await client.request(path, options);
            note(path, options, res);
            return res;
        },
        command: (...args) => client.command(...args),
        switchContext: (...args) => client.switchContext(...args),
    };

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.forbidden = false;
        state.origins = {};
        state.epoch += 1;
        client.switchContext({ accountId: id, dictationId: null });
    }

    function originRows() {
        return Object.keys(state.origins).map((key) => ({
            key,
            origin: state.origins[key],
            label: originLabel(state.origins[key]),
            locked: state.origins[key] === 'central' || state.origins[key] === 'ceiling',
        }));
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        client.switchContext({ accountId: null, dictationId: null });
    }

    setUser(deps.currentUser);
    return { state, client: tapped, setUser, originRows, dispose };
}

export const SETTINGS_TEMPLATE = `
<div class="clinic-settings-view">
  <h2>{{ labels.title }}</h2>
  <p v-if="state.denied" role="alert">{{ labels.denied }}</p>
  <p v-else-if="state.forbidden" role="alert" :style="errorStyle" data-testid="clinic-settings-forbidden">{{ labels.forbidden }}</p>
  <template v-else>
    <component :is="preferences" :key="'p:' + state.epoch"></component>
    <section role="region" :aria-label="labels.origins" data-testid="clinic-settings-origins">
      <h3>{{ labels.origins }}</h3>
      <p v-if="!rows().length" role="status" aria-live="polite" data-testid="clinic-settings-empty">{{ labels.empty }}</p>
      <ul v-else role="list" :aria-label="labels.origins">
        <li v-for="r in rows()" :key="r.key" :data-key="r.key" :data-origin="r.origin">{{ r.key }}: {{ r.label }}</li>
      </ul>
    </section>
    <component :is="correction" :audio="audio" :client="client" :vue="vue" :key="'c:' + state.epoch"></component>
  </template>
</div>`;

export const SettingsView = {
    name: 'ClinicSettingsView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        workstationId: { type: String, default: null },
        t: { type: Function, default: null },
        vue: { type: Object, default: null },
    },
    template: SETTINGS_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        const ctrl = createSettingsController({
            client: props.client,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const preferences = Vue.markRaw(createPreferencesComponent(Vue, ctrl.client, {
            workstationId: props.workstationId, t: props.t || undefined,
        }));
        const correction = Vue.markRaw(CorrectionModes);
        const s = surfaceFor('error');
        const errorStyle = { background: s.background, color: s.color, borderColor: s.border };
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        Vue.onBeforeUnmount(() => ctrl.dispose());
        return {
            state: ctrl.state, labels: LABELS, rows: ctrl.originRows, client: ctrl.client, vue: props.vue,
            audio: NO_AUDIO, preferences, correction, errorStyle,
        };
    },
};

export function registerSettingsView(app) {
    app.component('clinic-settings-view', SettingsView);
}
