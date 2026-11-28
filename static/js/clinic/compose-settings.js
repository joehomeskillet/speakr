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
import { createHandmicController } from './handmic-webhid.js';
import { CorrectionModes } from './correction-modes-ui.js';
import { surfaceFor } from './tokens.js';
import {
    CONFIGURABLE_ACTIONS as ACTIONS, TEXT as PEDAL_TEXT, createKeyCapture, loadSettings, normalizeSettings, saveSettings,
} from './pedal-ui.js';
import { createSampleRecorder, createVoiceConsentController } from './voice-consent-ui.js';

const WORKSTATION_PATH = /^\/preferences\/workstations\/[^/]+$/;

export const ORIGIN_LABELS = Object.freeze({
    workstation: 'Persönlich (Arbeitsplatz)',
    central: 'Zentral festgelegt',
    ceiling: 'Zentral begrenzt',
});

export const LABELS = Object.freeze({
    title: 'Einstellungen',
    eyebrow: 'Verwaltung',
    subline: 'Persönliche Darstellung, Arbeitsplatzwerte, Fußpedal und Einwilligung getrennt pflegen.',
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
        hold: 'Halten für Aufnahme oder Wiedergabe',
        rewind: 'Zurückspulen',
        skip: 'Vorspringen',
        recordToggle: 'Aufnahme starten / pausieren',
        stop: 'Stopp in der Aufnahmeansicht',
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

const TAG = Object.freeze({ granted: 'neutral', revoked: 'wait', none: 'neutral' });
const PILL = Object.freeze({ granted: 'pill-status--success', revoked: 'pill-status--warn', none: 'pill-status--pending' });

/**
 * Pedal section: capture a key through pedal-ui, map it to a recording or playback action, set the step seconds. Every change
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
            const recording = Vue.ref(false);
            const recorder = props.recorder !== undefined ? props.recorder : createSampleRecorder();
            const toggleRecord = async () => {
                if (!recorder) return;
                if (recording.value) {
                    recording.value = false;
                    await controller.enroll(await recorder.stop());
                } else {
                    try { await recorder.start(); recording.value = true; } catch (e) { recording.value = false; }
                }
            };
            const upload = async (event) => {
                const file = event.target.files && event.target.files[0];
                event.target.value = '';
                if (file) await controller.enroll(file);
            };
            return {
                controller, view, labels: CONSENT_LABELS, recording, canRecord: !!recorder, toggleRecord, upload, history: historyRows, pill: (s) => PILL[s] || PILL.none, tagClass: (s) => TAG[s] || TAG.none,
                onKeydown: (event) => controller.onKeydown(event),
            };
        },
    };
}

// Class bundles for the V8 look. The app's utility reset (preflight) wins over the element rules of clinic-v8.css, so headings
// get their sizes here; the buttons of the unowned preferences child are skinned from outside.
const HEADINGS = '[&_h1]:font-sans [&_h1]:text-[30px] [&_h1]:max-[760px]:text-[26px] [&_h1]:font-bold [&_h1]:leading-[1.2] [&_h1]:tracking-[-0.8px] [&_h1]:mb-[9px] [&_h1]:text-[color:var(--v8-text)] '
    + '[&_h2]:text-[16px] [&_h2]:font-bold [&_h2]:leading-[1.35] [&_h2]:tracking-[-0.2px] [&_h2]:mb-[14px] [&_h2]:text-[color:var(--v8-text)] [&_button]:px-[13px]';
const DANGER = 'danger max-[760px]:min-h-11 [.dark_&]:!text-[color:var(--v8-text)] [.dark_&]:!border-[color:var(--burgundy)] [[data-theme=dark]_&]:!text-[color:var(--v8-text)] [[data-theme=dark]_&]:!border-[color:var(--burgundy)]';
const NOTE_BURGUNDY = 'note burgundy [.dark_&]:!text-[color:var(--v8-text)] [[data-theme=dark]_&]:!text-[color:var(--v8-text)]';
const NOTE_WARNING = 'note warning [.dark_&]:!bg-[var(--v8-s2)] [.dark_&]:!border-[color:var(--line)] [.dark_&]:!text-[color:var(--v8-text)] '
    + '[[data-theme=dark]_&]:!bg-[var(--v8-s2)] [[data-theme=dark]_&]:!border-[color:var(--line)] [[data-theme=dark]_&]:!text-[color:var(--v8-text)]';
const PRIMARY = 'primary max-[760px]:min-h-11 !bg-[var(--ink)] !border-[color:var(--ink)] !text-[13px] !font-semibold !px-[13px] hover:!bg-[var(--brand2)]';
const CHILD_BUTTONS = '[&_.btn]:!min-h-[38px] [&_.btn]:rounded-lg [&_.btn]:border [&_.btn]:border-[color:var(--line)] [&_.btn]:bg-[var(--surface)] [&_.btn]:px-[13px] [&_.btn]:text-[13px] [&_.btn]:font-semibold [&_.btn]:text-[color:var(--v8-text)] '
    + '[&_button[type=submit]]:!bg-[var(--ink)] [&_button[type=submit]]:!border-[color:var(--ink)] [&_button[type=submit]]:!text-[color:var(--v8-on-ink,var(--ui-surface))]';
const CHOICES = '[&_legend]:mb-2 [&_legend]:text-[12px] [&_legend]:font-bold [&_fieldset]:m-0 [&_fieldset]:mb-4 [&_fieldset]:min-w-0 [&_fieldset]:border-0 [&_fieldset]:p-0 '
    + '[&_label.block]:flex [&_label.block]:items-center [&_label.block]:gap-2 [&_label.block]:text-[13px] [&_label.block]:font-medium';

const PEDAL_TEMPLATE = `
<section class="panel col-span-full" role="region" :aria-label="labels.title" data-testid="clinic-settings-pedal"
         style="--v8-input-line: var(--ui-text-muted)">
  <h2>{{ labels.title }}</h2>
  <p class="hint mb-3">{{ labels.hint }}</p>
  <div class="actions">
    <button type="button" :class="capturing ? '${PRIMARY}' : ''" :aria-pressed="String(capturing)"
            data-testid="clinic-settings-pedal-capture" @click="capturing ? capture.stop() : capture.start()">
      {{ capturing ? text.capturePrompt : text.captureStart }}
    </button>
    <span class="text-status" role="status" aria-live="polite" data-testid="clinic-settings-pedal-message">{{ message }}</span>
  </div>
  <p v-if="!rows().length" class="hint mt-3" role="status" data-testid="clinic-settings-pedal-empty">{{ labels.empty }}</p>
  <div v-else class="table-wrap mt-4">
    <table class="max-[760px]:block" :aria-label="labels.title">
      <thead class="max-[760px]:sr-only"><tr><th scope="col">{{ labels.key }}</th><th scope="col">{{ labels.action }}</th><th scope="col"><span class="sr-only">{{ labels.remove }}</span></th></tr></thead>
      <tbody class="max-[760px]:block">
        <tr v-for="r in rows()" :key="r.code" :data-code="r.code"
            class="max-[760px]:grid max-[760px]:gap-2 max-[760px]:p-3 max-[760px]:border-b max-[760px]:border-[color:var(--line)] max-[760px]:last:border-0 max-[760px]:[&_td]:p-0 max-[760px]:[&_td]:border-0">
          <td><span class="hidden max-[760px]:inline">{{ labels.key }} </span><kbd class="tag neutral">{{ r.code }}</kbd></td>
          <td>
            <span class="hidden max-[760px]:block">{{ labels.action }}</span>
            <select class="w-full min-w-0 !pl-3 !pr-8 !text-[13px]" style="border-color: var(--v8-input-line)"
                    :value="r.action" :aria-label="labels.action + ' ' + r.code" @change="setAction(r.code, $event.target.value)">
              <option v-for="a in actions" :key="a" :value="a">{{ labels.actions[a] }}</option>
            </select>
          </td>
          <td class="text-right"><button type="button" @click="removeKey(r.code)">{{ labels.remove }}</button></td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="grid three mt-4 [&_input]:min-w-0">
    <div class="flex flex-col gap-1.5 mb-4 [&_label]:text-xs [&_label]:font-bold">
      <label for="clinic-settings-pedal-rewind">{{ labels.rewindSeconds }}</label>
      <input id="clinic-settings-pedal-rewind" type="number" min="0" max="60" step="1" :value="settings.rewindSeconds"
             data-testid="clinic-settings-pedal-rewind" @change="setSeconds('rewindSeconds', $event.target.value, $event.target)">
    </div>
    <div class="flex flex-col gap-1.5 mb-4 [&_label]:text-xs [&_label]:font-bold">
      <label for="clinic-settings-pedal-skip">{{ labels.skipSeconds }}</label>
      <input id="clinic-settings-pedal-skip" type="number" min="0" max="60" step="1" :value="settings.skipSeconds"
             data-testid="clinic-settings-pedal-skip" @change="setSeconds('skipSeconds', $event.target.value, $event.target)">
    </div>
    <div class="flex flex-col gap-1.5 mb-4 [&_label]:text-xs [&_label]:font-bold">
      <label for="clinic-settings-pedal-autorewind">{{ labels.autoRewindSeconds }}</label>
      <input id="clinic-settings-pedal-autorewind" type="number" min="0" max="60" step="1" :value="settings.autoRewindSeconds"
             data-testid="clinic-settings-pedal-autorewind" @change="setSeconds('autoRewindSeconds', $event.target.value, $event.target)">
    </div>
  </div>
  <p v-if="saveError" class="${NOTE_WARNING}" role="alert">{{ labels.notSaved }}</p>
</section>`;

const HANDMIC_STATUS = Object.freeze({
    disconnected: 'Kein Handmikrofon verbunden.',
    pairing: 'Verbindung wird hergestellt …',
    unsupported: 'Gerät nicht unterstützt. Kein Handmikrofon verbunden.',
    error: 'Verbindung fehlgeschlagen. Kein Handmikrofon verbunden.',
});

function createHandmicSettingsComponent(Vue, hid) {
    return {
        template: `
<section class="panel col-span-full" aria-labelledby="clinic-settings-handmic-title" data-testid="clinic-settings-handmic">
  <h2 id="clinic-settings-handmic-title">Handmikrofon (WebHID)</h2>
  <p class="hint mb-3">Für unterstützte Geräte im HID-Modus. Alternativ den Tastaturmodus des Geräts verwenden.</p>
  <div class="actions">
    <button v-if="available" type="button" class="max-[760px]:min-h-11"
            :disabled="state.status === 'pairing' || state.status === 'connected'" @click="pair">Handmikrofon koppeln</button>
  </div>
  <div class="mt-3" role="status">
    <p>{{ state.status === 'connected' ? state.name + ' verbunden.' : messages[state.status] }}</p>
    <p v-if="!available" class="hint">WebHID ist in diesem Browser nicht verfügbar.</p>
    <p class="hint">{{ state.ledAvailable ? 'LED-Steuerung verfügbar.' : 'LED-Rückmeldung nicht verfügbar.' }}</p>
  </div>
</section>`,
        setup() {
            const state = Vue.ref({ status: 'disconnected', ledAvailable: false });
            const controller = createHandmicController(hid, { onState: (value) => { state.value = value; } });
            Vue.onBeforeUnmount(() => controller.dispose().catch((error) => console.warn('WebHID close failed', error)));
            return { state, messages: HANDMIC_STATUS, available: Boolean(hid), pair: controller.pair };
        },
    };
}

const CONSENT_TEMPLATE = `
<section class="panel" role="region" :aria-label="view.title" data-testid="clinic-settings-consent"
         :aria-busy="String(view.busy)" @keydown="onKeydown">
  <div class="panel-header">
    <h2>{{ view.title }}</h2>
    <span class="tag dot" :class="tagClass(view.status.value)" role="status" aria-live="polite"
          data-testid="clinic-settings-consent-state" :data-state="view.status.value">{{ view.status.label }}</span>
  </div>
  <p class="hint mb-2">{{ labels.optIn }}</p>
  <dl class="keyvalues">
    <div class="max-[760px]:!grid-cols-1 max-[760px]:!gap-1"><dt>{{ view.statement.label }}</dt><dd data-testid="clinic-settings-consent-statement">{{ view.statement.text }}</dd></div>
    <div><dt>{{ labels.embeddings }}</dt><dd data-testid="clinic-settings-consent-count">{{ view.embeddingCount }}</dd></div>
  </dl>
  <ul v-if="view.history.length" role="list" class="hint mt-3" :aria-label="labels.history">
    <li v-for="h in history(view.history)" :key="h.id" class="flex gap-3">
      <time class="tabular-nums">{{ h.when }}</time><span>{{ h.label }}</span>
    </li>
  </ul>
  <div v-if="view.error" class="${NOTE_BURGUNDY}" role="alert" data-testid="clinic-settings-consent-error">
    <span>{{ view.error.message }}</span>
    <button v-if="view.error.retryable" type="button" class="ml-2" @click="controller.load()">{{ view.labels.retry }}</button>
  </div>
  <p v-if="view.notice" class="note" role="status" aria-live="polite">{{ view.notice }}</p>
  <div v-if="view.confirmingRevoke" class="${NOTE_WARNING}" role="alertdialog" :aria-label="view.labels.confirm">
    <p class="font-semibold">{{ view.labels.confirm }}</p>
    <div class="actions">
      <button type="button" class="${DANGER}" :disabled="view.busy" data-testid="clinic-settings-consent-confirm"
              @click="controller.confirmRevoke()">{{ view.labels.confirm }}</button>
      <button type="button" data-testid="clinic-settings-consent-cancel"
              @click="controller.cancel()">{{ view.labels.cancel }}</button>
    </div>
  </div>
  <div v-if="view.canEnroll && !view.confirmingRevoke" class="mt-4" data-testid="clinic-settings-consent-enroll">
    <p class="hint mb-2">{{ view.labels.enrollHint }}</p>
    <div class="actions">
      <button v-if="canRecord" type="button" :disabled="view.busy" :aria-pressed="String(recording)"
              data-testid="clinic-settings-consent-record" @click="toggleRecord()">{{ recording ? view.labels.stopRecord : view.labels.record }}</button>
      <label class="btn cursor-pointer" for="clinic-settings-consent-upload">{{ view.labels.upload }}</label>
      <input id="clinic-settings-consent-upload" type="file" accept="audio/wav,audio/x-wav,.wav" class="sr-only"
             :disabled="view.busy" data-testid="clinic-settings-consent-upload" @change="upload($event)">
    </div>
    <p v-if="!canRecord" class="hint mt-2">{{ view.labels.unsupported }}</p>
  </div>
  <div v-if="!view.confirmingRevoke" class="actions mt-4">
    <button type="button" class="${PRIMARY}" :disabled="!view.canGrant" data-testid="clinic-settings-consent-grant"
            @click="controller.grant()">{{ view.labels.grant }}</button>
    <button type="button" class="${DANGER}" :disabled="!view.canRevoke" data-testid="clinic-settings-consent-revoke"
            @click="controller.requestRevoke()">{{ view.labels.revoke }}</button>
    <button type="button" :disabled="view.busy" @click="controller.load()">{{ view.labels.reload }}</button>
  </div>
</section>`;

export const SETTINGS_TEMPLATE = `
<div class="clinic-settings-view v8-page ${HEADINGS}">
  <nav class="breadcrumb" aria-label="Brotkrumennavigation">
    <a href="#/aufnahmen">Speakr</a><span aria-hidden="true">›</span><span>{{ labels.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ labels.title }}</span>
  </nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ labels.eyebrow }}</div>
      <h1>{{ labels.title }}</h1>
      <p class="subline">{{ labels.subline }}</p>
    </div>
    <div class="head-actions"></div>
  </div>
  <p v-if="state.denied" class="${NOTE_WARNING}" role="alert">{{ labels.denied }}</p>
  <p v-else-if="state.forbidden" class="${NOTE_BURGUNDY}" role="alert" :style="errorStyle" data-testid="clinic-settings-forbidden">{{ labels.forbidden }}</p>
  <template v-else>
    <div class="grid equal">
      <div class="space-y-5 min-w-0">
        <section class="panel clinic-settings-prefs ${CHILD_BUTTONS} ${CHOICES}"><component :is="preferences" :key="'p:' + state.epoch"></component></section>
        <section role="region" :aria-label="labels.origins" class="panel" data-testid="clinic-settings-origins">
          <h2>{{ labels.origins }}</h2>
          <p v-if="!rows().length" class="hint" role="status" aria-live="polite" data-testid="clinic-settings-empty">{{ labels.empty }}</p>
          <dl v-else class="keyvalues" :aria-label="labels.origins">
            <div v-for="r in rows()" :key="r.key" :data-key="r.key" :data-origin="r.origin">
              <dt>{{ r.key }}</dt>
              <dd><span class="tag" :class="r.locked ? 'burgundy' : 'neutral'">{{ r.label }}</span></dd>
            </div>
          </dl>
        </section>
      </div>
      <div class="space-y-5 min-w-0">
        <section class="panel clinic-settings-correction ${CHOICES} [&_legend]:!text-[16px] [&_legend]:!mb-[14px] [&_legend]:tracking-[-0.2px] [&_[role=alert]]:mt-3 [&_[role=alert]]:rounded-lg [&_[role=alert]]:border [&_[role=alert]]:border-[color:var(--line)] [&_[role=alert]]:bg-[var(--v8-s2)] [&_[role=alert]]:p-3 [&_[role=alert]]:text-xs [&_.clinic-correction-mode]:grid [&_.clinic-correction-mode]:grid-cols-[auto_1fr] [&_.clinic-correction-mode]:gap-x-2 [&_.clinic-correction-mode]:items-baseline [&_.clinic-correction-mode]:text-[13px] [&_.clinic-correction-mode_small]:col-start-2 [&_.clinic-correction-mode_small]:text-[color:var(--v8-muted)]"><component :is="correction" :audio="audio" :client="client" :vue="vue" :key="'c:' + state.epoch"></component></section>
        <component :is="consent" :key="'v:' + state.epoch"></component>
      </div>
      <component :is="pedal"></component>
      <component :is="handmic" :key="'hid:' + state.epoch"></component>
    </div>
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
        const handmic = Vue.markRaw(createHandmicSettingsComponent(Vue, globalThis.navigator?.hid));
        const consent = Vue.markRaw(createConsentComponent(Vue, ctrl.client, { t: props.t }));
        const s = surfaceFor('error');
        const errorStyle = { background: s.background, color: s.color, borderColor: s.border };
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        Vue.onBeforeUnmount(() => ctrl.dispose());
        return {
            state: ctrl.state, labels: LABELS, rows: ctrl.originRows, client: ctrl.client, vue: props.vue,
            audio: NO_AUDIO, preferences, correction, pedal, handmic, consent, errorStyle,
        };
    },
};

export function registerSettingsView(app) {
    app.component('clinic-settings-view', SettingsView);
}
