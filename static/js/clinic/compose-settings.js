/**
 * Personal settings view (v8-58, V8 P19 "Einstellungen"): one Vue view composed from the existing
 * modules preferences-ui (recipient default, workstation values, locked/central markers) and
 * correction-modes-ui (correction playback mode). PAR-CFG-001, PAR-ADM-011, PAR-COR-005.
 *
 * Contract for the mount package:
 *   SettingsView                   Vue options object, props client, currentUser, workstationId, t, vue (optional)
 *   registerSettingsView(app)      app.component('clinic-settings-view', SettingsView)
 *
 * The view adds only what the children do not show: the origin of each workstation value
 * (personal, centrally fixed, or centrally limited; the API sends workstation, central, ceiling). The origins are
 * read from the workstation answer the preferences child already requests (a tap on the shared client, no second
 * request). A refused request (403) hides the whole page content and shows only the denial, so no
 * system area is disclosed. A change of the current user switches the client context (aborting
 * in-flight calls) and remounts the children. Leaving the view aborts in-flight calls. The server is
 * authoritative for every value; colours come from tokens.js. The only values kept in the browser are the
 * workstation pedal key settings, written exclusively through pedal-ui.js (keys and seconds, no clinical data).
 *
 * Two sections are added by this view (v8-93): the pedal key capture (PAR-OPS-001; pedal-ui createKeyCapture
 * and its settings store) and the own-voice consent (OWN-SPK-001; voice-consent-ui against /voice-consent,
 * which carries expected_consent_id; the client adds X-CSRF-Token and Idempotency-Key).
 */

import { createPreferencesComponent } from './preferences-ui.js';
import { CorrectionModes } from './correction-modes-ui.js';
import { surfaceFor } from './tokens.js';
import {
    ACTIONS, TEXT as PEDAL_TEXT, createKeyCapture, loadSettings, normalizeSettings, saveSettings,
} from './pedal-ui.js';
import { createVoiceConsentController } from './voice-consent-ui.js';

const WORKSTATION_PATH = /^\/preferences\/workstations\/[^/]+$/;

export const ORIGIN_LABELS = Object.freeze({
    workstation: 'Persönlich (Arbeitsplatz)',
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

export const PEDAL_LABELS = Object.freeze({
    title: 'Fußpedal (Tastaturmodus)',
    hint: 'Das Pedal sendet eine Taste. Zugeordnet werden können F2 bis F24 (ohne F5, F11, F12), Medientasten, Pause und Rollen.',
    empty: 'Keine Pedalzuordnung.',
    key: 'Taste',
    action: 'Aktion',
    remove: 'Entfernen',
    rewindSeconds: 'Zurückspulen (Sekunden)',
    skipSeconds: 'Vorspringen (Sekunden)',
    autoRewindSeconds: 'Automatisch zurückspulen beim Loslassen (Sekunden)',
    notSaved: 'Nicht gespeichert: Der Browser-Speicher ist nicht verfügbar.',
    actions: Object.freeze({
        playPause: 'Wiedergabe / Pause',
        hold: 'Halten zum Abspielen',
        rewind: 'Zurückspulen',
        skip: 'Vorspringen',
    }),
});

export const CONSENT_LABELS = Object.freeze({
    embeddings: 'Gespeicherte Stimmprofile',
    history: 'Verlauf',
    loading: 'Lädt …',
    statusGranted: 'Einwilligung erteilt',
    statusRevoked: 'Einwilligung widerrufen',
    statusNone: 'Keine Einwilligung erteilt',
    optIn: 'Ein Stimmprofil wird nur nach Ihrer Einwilligung erstellt.',
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

const HISTORY_STATUS = Object.freeze({ granted: 'Einwilligung erteilt', revoked: 'Einwilligung widerrufen' });
const HISTORY_ROWS = 8;

// Newest first; the server timestamp is shown as sent (date and minute), without a timezone conversion.
function historyRows(history) {
    return history.slice().sort((a, b) => b.id - a.id).slice(0, HISTORY_ROWS).map((h) => ({
        id: h.id,
        when: typeof h.createdAt === 'string' ? h.createdAt.replace('T', ' ').slice(0, 16) : '',
        label: HISTORY_STATUS[h.status] || CONSENT_LABELS.statusNone,
    }));
}

const PILL = Object.freeze({ granted: 'pill-status--success', revoked: 'pill-status--warn', none: 'pill-status--pending' });

/**
 * Pedal section: capture a key through pedal-ui, map it to a playback action, set the step seconds. Every change
 * is stored through saveSettings of pedal-ui, which drops printable keys. No key is stored when capture was refused.
 */
export function createPedalSettingsComponent(Vue, options = {}) {
    const store = options.storage;
    return {
        name: 'ClinicPedalSettings',
        template: PEDAL_TEMPLATE,
        setup() {
            const settings = Vue.ref(loadSettings(store));
            const saveError = Vue.ref(false);

            function persist(next) {
                const clean = normalizeSettings(next);
                const stored = saveSettings(clean, store);
                saveError.value = stored === null;
                settings.value = clean;
                return clean;
            }
            function rows() {
                return Object.keys(settings.value.mapping).sort().map((code) => ({
                    code, action: settings.value.mapping[code], label: PEDAL_LABELS.actions[settings.value.mapping[code]],
                }));
            }
            function onCapture(code) {
                const mapping = Object.assign({}, settings.value.mapping);
                if (!mapping[code]) mapping[code] = ACTIONS[0];
                persist(Object.assign({}, settings.value, { mapping }));
            }
            const capture = createKeyCapture({ vue: Vue, target: options.target, onCapture });
            function setAction(code, action) {
                if (!ACTIONS.includes(action)) return;
                persist(Object.assign({}, settings.value, { mapping: Object.assign({}, settings.value.mapping, { [code]: action }) }));
            }
            function removeKey(code) {
                const mapping = Object.assign({}, settings.value.mapping);
                delete mapping[code];
                persist(Object.assign({}, settings.value, { mapping }));
            }
            function setSeconds(field, raw, input) {
                persist(Object.assign({}, settings.value, { [field]: raw === '' ? NaN : Number(raw) }));
                if (input) input.value = settings.value[field]; // a refused value shows the kept one
            }
            Vue.onBeforeUnmount(() => capture.stop());
            return {
                settings, saveError, rows, capture, capturing: capture.capturing, message: capture.message,
                setAction, removeKey, setSeconds, labels: PEDAL_LABELS, actions: ACTIONS, text: PEDAL_TEXT,
            };
        },
    };
}

/**
 * Consent section: shows the own-voice consent state; grant and revoke (with a second confirmation step) go through
 * the voice-consent controller, which sends expected_consent_id and one Idempotency-Key per action.
 */
export function createConsentComponent(Vue, client, props = {}) {
    return {
        name: 'ClinicVoiceConsentSettings',
        template: CONSENT_TEMPLATE,
        setup() {
            const controller = createVoiceConsentController({ client, t: props.t || undefined, newKey: props.newKey });
            const view = Vue.shallowRef(controller.view());
            const unsubscribe = controller.subscribe(() => { view.value = controller.view(); });
            Vue.onMounted(async () => { await controller.load(); view.value = controller.view(); });
            Vue.onBeforeUnmount(() => unsubscribe());
            return {
                controller, view, labels: CONSENT_LABELS, history: historyRows, pill: (s) => PILL[s] || PILL.none,
                onKeydown: (event) => controller.onKeydown(event),
            };
        },
    };
}

const PEDAL_TEMPLATE = `
<section class="surface-raised p-4 space-y-3" role="region" :aria-label="labels.title" data-testid="clinic-settings-pedal">
  <h3 class="t-subhead">{{ labels.title }}</h3>
  <p class="t-meta">{{ labels.hint }}</p>
  <div class="flex flex-wrap items-center gap-3">
    <button type="button" class="btn btn--sm" :class="capturing ? 'btn--primary' : 'btn--ghost'" :aria-pressed="String(capturing)"
            data-testid="clinic-settings-pedal-capture" @click="capturing ? capture.stop() : capture.start()">
      {{ capturing ? text.capturePrompt : text.captureStart }}
    </button>
    <span class="t-meta" role="status" aria-live="polite" data-testid="clinic-settings-pedal-message">{{ message }}</span>
  </div>
  <p v-if="!rows().length" class="t-body" role="status" data-testid="clinic-settings-pedal-empty">{{ labels.empty }}</p>
  <ul v-else role="list" class="space-y-2" :aria-label="labels.title">
    <li v-for="r in rows()" :key="r.code" class="grid items-center gap-x-3 gap-y-1 grid-cols-[1fr_auto] sm:grid-cols-[4.5rem_1fr_auto]" :data-code="r.code">
      <kbd class="kbd inline-flex items-center justify-center justify-self-start min-w-[3.5rem] min-h-8 px-2 py-1">{{ r.code }}</kbd>
      <label class="order-last col-span-2 flex items-center gap-2 t-body min-w-0 sm:order-none sm:col-span-1">
        <span class="t-meta sr-only sm:not-sr-only">{{ labels.action }}</span>
        <select class="field field--sm min-w-0 flex-1" :value="r.action" :aria-label="labels.action + ' ' + r.code"
                @change="setAction(r.code, $event.target.value)">
          <option v-for="a in actions" :key="a" :value="a">{{ labels.actions[a] }}</option>
        </select>
      </label>
      <button type="button" class="btn btn--sm btn--ghost" @click="removeKey(r.code)">{{ labels.remove }}</button>
    </li>
  </ul>
  <div class="grid gap-3 sm:grid-cols-3 items-end">
    <label class="flex flex-col gap-1 t-body">
      <span class="t-meta">{{ labels.rewindSeconds }}</span>
      <input type="number" min="0" max="60" step="1" class="field field--sm" :value="settings.rewindSeconds"
             data-testid="clinic-settings-pedal-rewind" @change="setSeconds('rewindSeconds', $event.target.value, $event.target)">
    </label>
    <label class="flex flex-col gap-1 t-body">
      <span class="t-meta">{{ labels.skipSeconds }}</span>
      <input type="number" min="0" max="60" step="1" class="field field--sm" :value="settings.skipSeconds"
             data-testid="clinic-settings-pedal-skip" @change="setSeconds('skipSeconds', $event.target.value, $event.target)">
    </label>
    <label class="flex flex-col gap-1 t-body">
      <span class="t-meta">{{ labels.autoRewindSeconds }}</span>
      <input type="number" min="0" max="60" step="1" class="field field--sm" :value="settings.autoRewindSeconds"
             data-testid="clinic-settings-pedal-autorewind" @change="setSeconds('autoRewindSeconds', $event.target.value, $event.target)">
    </label>
  </div>
  <p v-if="saveError" class="notice notice--danger" role="alert">{{ labels.notSaved }}</p>
</section>`;

const CONSENT_TEMPLATE = `
<section class="surface-raised p-4 space-y-3" role="region" :aria-label="view.title" data-testid="clinic-settings-consent"
         :aria-busy="String(view.busy)" @keydown="onKeydown">
  <div class="flex flex-wrap items-center gap-3">
    <h3 class="t-subhead">{{ view.title }}</h3>
    <span class="pill-status" :class="pill(view.status.value)" role="status" aria-live="polite"
          data-testid="clinic-settings-consent-state" :data-state="view.status.value">{{ view.status.label }}</span>
  </div>
  <p class="t-meta">{{ labels.optIn }}</p>
  <div>
    <p class="section-label">{{ view.statement.label }}</p>
    <p class="t-body mt-1" data-testid="clinic-settings-consent-statement">{{ view.statement.text }}</p>
  </div>
  <p class="t-meta" data-testid="clinic-settings-consent-count">{{ labels.embeddings }}: {{ view.embeddingCount }}</p>
  <ul v-if="view.history.length" role="list" class="space-y-1" :aria-label="labels.history">
    <li v-for="h in history(view.history)" :key="h.id" class="flex gap-3 t-meta">
      <time class="tabular-nums">{{ h.when }}</time><span>{{ h.label }}</span>
    </li>
  </ul>
  <div v-if="view.error" class="notice notice--danger" role="alert" data-testid="clinic-settings-consent-error">
    <span class="notice__body">{{ view.error.message }}</span>
    <span class="notice__actions">
      <button v-if="view.error.retryable" type="button" class="btn btn--sm btn--ghost" @click="controller.load()">{{ view.labels.retry }}</button>
    </span>
  </div>
  <p v-if="view.notice" class="notice notice--success t-body" role="status" aria-live="polite">{{ view.notice }}</p>
  <div v-if="view.confirmingRevoke" class="notice notice--warn flex-wrap !items-center justify-between" role="alertdialog" :aria-label="view.labels.confirm">
    <span class="notice__body t-body-strong">{{ view.labels.confirm }}</span>
    <span class="notice__actions flex gap-2">
      <button type="button" class="btn btn--sm btn--danger" :disabled="view.busy" data-testid="clinic-settings-consent-confirm"
              @click="controller.confirmRevoke()">{{ view.labels.confirm }}</button>
      <button type="button" class="btn btn--sm btn--ghost" data-testid="clinic-settings-consent-cancel"
              @click="controller.cancel()">{{ view.labels.cancel }}</button>
    </span>
  </div>
  <div v-else class="flex flex-wrap gap-2">
    <button type="button" class="btn btn--sm btn--primary" :disabled="!view.canGrant" data-testid="clinic-settings-consent-grant"
            @click="controller.grant()">{{ view.labels.grant }}</button>
    <button type="button" class="btn btn--sm btn--danger" :disabled="!view.canRevoke" data-testid="clinic-settings-consent-revoke"
            @click="controller.requestRevoke()">{{ view.labels.revoke }}</button>
    <button type="button" class="btn btn--sm btn--ghost" :disabled="view.busy" @click="controller.load()">{{ view.labels.reload }}</button>
  </div>
</section>`;

export const SETTINGS_TEMPLATE = `
<div class="clinic-settings-view mx-auto w-full max-w-3xl space-y-4 p-4">
  <h2 class="t-heading">{{ labels.title }}</h2>
  <p v-if="state.denied" role="alert">{{ labels.denied }}</p>
  <p v-else-if="state.forbidden" role="alert" :style="errorStyle" data-testid="clinic-settings-forbidden">{{ labels.forbidden }}</p>
  <template v-else>
    <div class="surface-raised p-4"><component :is="preferences" :key="'p:' + state.epoch"></component></div>
    <section role="region" :aria-label="labels.origins" class="surface-raised p-4 space-y-2" data-testid="clinic-settings-origins">
      <h3 class="t-subhead">{{ labels.origins }}</h3>
      <p v-if="!rows().length" role="status" aria-live="polite" data-testid="clinic-settings-empty">{{ labels.empty }}</p>
      <ul v-else role="list" class="space-y-1 t-body" :aria-label="labels.origins">
        <li v-for="r in rows()" :key="r.key" :data-key="r.key" :data-origin="r.origin">{{ r.key }}: {{ r.label }}</li>
      </ul>
    </section>
    <div class="surface-raised p-4 [&_legend]:mb-2 [&_legend]:font-semibold [&_[role=radiogroup]]:space-y-2 [&_.clinic-correction-mode]:grid [&_.clinic-correction-mode]:grid-cols-[auto_1fr] [&_.clinic-correction-mode]:gap-x-2 [&_.clinic-correction-mode]:items-baseline [&_.clinic-correction-mode_small]:col-start-2 [&_.clinic-correction-mode_small]:text-[color:var(--text-muted)] [&_[role=alert]]:notice [&_[role=alert]]:notice--danger"><component :is="correction" :audio="audio" :client="client" :vue="vue" :key="'c:' + state.epoch"></component></div>
    <component :is="pedal"></component>
    <component :is="consent" :key="'v:' + state.epoch"></component>
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
        const pedal = Vue.markRaw(createPedalSettingsComponent(Vue));
        const consent = Vue.markRaw(createConsentComponent(Vue, ctrl.client, { t: props.t }));
        const s = surfaceFor('error');
        const errorStyle = { background: s.background, color: s.color, borderColor: s.border };
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        Vue.onBeforeUnmount(() => ctrl.dispose());
        return {
            state: ctrl.state, labels: LABELS, rows: ctrl.originRows, client: ctrl.client, vue: props.vue,
            audio: NO_AUDIO, preferences, correction, pedal, consent, errorStyle,
        };
    },
};

export function registerSettingsView(app) {
    app.component('clinic-settings-view', SettingsView);
}
