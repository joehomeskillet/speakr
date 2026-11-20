/**
 * ASR and language profiles view (v8-56, V8 P15): one Vue view composed from the
 * existing building blocks. PAR-ASR-002, PAR-ASR-004, PAR-ASR-005, PAR-ASR-006.
 *
 * Reused, not copied: createPanels().asr (compose-resources, over asr-profiles-ui:
 * profile choice, engine, capabilities), createVoiceConsentController
 * (voice-consent-ui: opt-in voice profile), surfaceFor / FOCUS_INDICATOR (tokens).
 * The optimisation part has no earlier client module; it is a thin controller over
 * the admin routes of v8-31 (routes_training.py) and holds no training rule.
 *
 * Contract for the mount package:
 *   AsrProfilesView                 Vue options object, props client, adminClient (optional),
 *                                   currentUser, vue (optional)
 *   registerAsrProfilesView(app)    app.component('clinic-asr-profiles-view', AsrProfilesView)
 *
 * client is the clinic api client. adminClient must reach the admin routes below
 * /api/admin (as for accounts-ui); without it the optimisation section is hidden.
 * Server state is authoritative and lives in memory only: nothing is written to
 * browser storage, colours come only from the token contract. A change of the
 * current user switches the client contexts and remounts all sections. Speech
 * recognition stays local; the view never offers a cloud fallback.
 */

import { createPanels } from './compose-resources.js';
import { createVoiceConsentController } from './voice-consent-ui.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { CHILD_SCOPE } from './compose-users.js';

export const TRAINING_BASE = '/admin/clinic/training';
export const KINDS = Object.freeze(['language_model', 'acoustic']);

export const TEXT = Object.freeze({
    title: 'Sprachprofile',
    denied: 'Kein Zugriff. Bitte anmelden.',
    voiceTitle: 'Eigenes Stimmprofil (freiwillig)',
    voiceHint: 'Das Stimmprofil wird nur mit Ihrer Einwilligung erstellt und kann jederzeit widerrufen werden.',
    capabilityTitle: 'Fähigkeiten',
    capabilityColumns: Object.freeze({ name: 'Fähigkeit', status: 'Status' }),
    pronunciation: 'Aussprachetraining',
    proven: 'Nachgewiesen',
    unavailable: 'Nicht verfügbar (CAPABILITY_UNAVAILABLE)',
    capabilityUnknown: 'Nicht ermittelt',
    trainingTitle: 'Optimierung',
    trainingDenied: 'Keine Rechte für die Optimierung. Nur Administratoren dürfen Läufe vorbereiten und starten.',
    kind: 'Art',
    kindLabels: Object.freeze({ language_model: 'Sprachmodell (nur Text)', acoustic: 'Akustik (Text und Audio)' }),
    prepare: 'Material prüfen',
    start: 'Optimierung starten',
    reload: 'Neu laden',
    retry: 'Erneut versuchen',
    cancelRun: 'Lauf abbrechen',
    runs: 'Läufe',
    noRuns: 'Keine Optimierungsläufe.',
    loading: 'Lädt …',
    manifestItems: 'Freigegebene Korrekturen',
    manifestExcluded: 'Ausgeschlossen',
    manifestCapability: 'Aussprachetraining im Manifest',
    noMaterial: 'Mindestdaten fehlen: Es gibt keine freigegebenen Korrekturen für diese Art.',
    columns: Object.freeze({ id: 'Lauf', kind: 'Art', items: 'Beispiele', state: 'Status' }),
});

const RUN_STATES = Object.freeze({
    queued: 'In Warteschlange', running: 'Läuft', cancelled: 'Abgebrochen',
    completed: 'Abgeschlossen', failed: 'Fehlgeschlagen', none: 'Kein Lauf',
});

const START_NOTICES = Object.freeze({
    scheduled: 'Lauf eingeplant.',
    already_scheduled: 'Für dieses Material läuft bereits ein Lauf.',
    failed: 'Der Lauf konnte nicht eingeplant werden.',
});

const ERROR_MESSAGES = Object.freeze({
    stale_version: 'Der Profilstand ist veraltet. Bitte neu laden.',
    profile_missing: 'Es ist kein Serverprofil vorhanden.',
    unauthorized: TEXT.trainingDenied,
    invalid_payload: 'Die Eingabe ist ungültig.',
    kind_invalid: 'Die Art ist ungültig.',
    scope_invalid: 'Der Bereich ist ungültig.',
    manifest_invalid: 'Das Material ist ungültig. Bitte erneut prüfen.',
    not_found: 'Der Lauf ist nicht mehr vorhanden.',
});

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

function style(kind) {
    const s = surfaceFor(kind);
    return { background: s.background, color: s.color, borderColor: s.border };
}

// A permission outcome is its own state, never a generic failure.
function failure(res) {
    const code = res.code || null;
    return {
        outcome: res.outcome,
        code,
        denied: res.outcome === 'permission',
        message: ERROR_MESSAGES[code] || res.message,
        retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
    };
}

/**
 * Optimisation section state over the v8-31 admin routes.
 * @param {{client: {request: Function, switchContext?: Function}, profileClient?: {request: Function}}} deps
 *   client reaches /api/admin; profileClient (clinic client) supplies the profile version.
 */
export function createTrainingController(deps) {
    const client = deps.client;
    const profileClient = deps.profileClient || null;
    const listeners = new Set();
    const state = {
        loading: false, busy: false, denied: false, error: null, notice: null,
        kind: KINDS[0], runs: [], manifest: null, disposed: false,
    };

    const emit = () => { if (!state.disposed) for (const fn of listeners) fn(state); };

    function adopt(res) {
        const f = failure(res);
        if (f.denied) { state.denied = true; state.error = null; } else state.error = f;
        return false;
    }

    async function load() {
        if (state.disposed) return false;
        state.loading = true;
        state.error = null;
        emit();
        const res = await client.request(TRAINING_BASE + '/schedule');
        state.loading = false;
        if (state.disposed) return false;
        if (res.outcome !== 'ok') {
            adopt(res);
            emit();
            return false;
        }
        state.denied = false;
        const rows = res.data && Array.isArray(res.data.runs) ? res.data.runs : [];
        state.runs = rows.map((r) => ({
            id: r.job_id, kind: r.kind, scope: r.scope, items: r.items, state: String(r.state || 'none'),
        }));
        emit();
        return true;
    }

    function setKind(kind) {
        if (!KINDS.includes(kind) || state.busy) return false;
        state.kind = kind;
        state.manifest = null;
        state.notice = null;
        emit();
        return true;
    }

    // Read-only check of the material; nothing is started.
    async function prepare() {
        if (state.busy || state.denied) return false;
        state.busy = true;
        state.error = null;
        state.notice = null;
        emit();
        const res = await client.request(TRAINING_BASE + '/manifest?kind=' + encodeURIComponent(state.kind) + '&scope=author');
        state.busy = false;
        if (state.disposed) return false;
        if (res.outcome !== 'ok') {
            adopt(res);
            emit();
            return false;
        }
        const d = res.data || {};
        state.manifest = {
            items: Array.isArray(d.items) ? d.items.length : 0,
            excluded: Array.isArray(d.exclusions) ? d.exclusions.length : 0,
            capability: typeof d.capability === 'string' ? d.capability : null,
        };
        if (state.manifest.items === 0) state.notice = TEXT.noMaterial;
        emit();
        return true;
    }

    async function profileVersion() {
        if (!profileClient) return 0;
        const res = await profileClient.request('/asr-profiles');
        const v = res.outcome === 'ok' && res.data && res.data.profile ? res.data.profile.version : 0;
        return Number.isInteger(v) ? v : 0;
    }

    // The server dedups by manifest and profile version: a second start returns the same job.
    async function start() {
        if (state.busy || state.denied) return false;
        state.busy = true;
        state.error = null;
        state.notice = null;
        emit();
        const version = await profileVersion();
        const res = await client.request(TRAINING_BASE + '/runs', {
            method: 'POST', body: { kind: state.kind, scope: 'author', expected_profile_version: version },
        });
        state.busy = false;
        if (state.disposed) return false;
        if (res.outcome !== 'ok') {
            adopt(res);
            emit();
            return false;
        }
        const d = res.data || {};
        if (d.state === 'insufficient_data') {
            state.notice = 'Mindestdaten fehlen: ' + d.utterances + ' Beispiele, ' + d.audio_seconds + ' s Audio.';
        } else if (d.state === 'deferred') {
            state.notice = d.reason === 'outside_window'
                ? 'Ausserhalb des Zeitfensters. Nächster Start: ' + d.next_window_start
                : 'Zurückgestellt: ein anderer Lauf ist aktiv.';
        } else {
            state.notice = START_NOTICES[d.state] || START_NOTICES.failed;
        }
        emit();
        await load();
        return d.state === 'scheduled' || d.state === 'already_scheduled';
    }

    async function cancel(jobId) {
        const row = state.runs.find((r) => r.id === jobId);
        if (!row || state.busy || state.denied || !['queued', 'running'].includes(row.state)) return false;
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.request(TRAINING_BASE + '/runs/' + encodeURIComponent(jobId) + '/cancel', { method: 'POST', body: {} });
        state.busy = false;
        if (state.disposed) return false;
        if (res.outcome !== 'ok') {
            adopt(res);
            emit();
            return false;
        }
        return load();
    }

    function view() {
        return {
            loading: state.loading,
            busy: state.busy,
            denied: state.denied,
            deniedMessage: TEXT.trainingDenied,
            error: state.error,
            notice: state.notice,
            empty: !state.loading && !state.denied && !state.error && state.runs.length === 0,
            kinds: KINDS.map((value) => ({ value, label: TEXT.kindLabels[value], selected: state.kind === value })),
            manifest: state.manifest,
            canStart: !state.busy && !state.denied && !state.loading,
            runs: state.runs.map((r) => ({
                id: r.id,
                kind: TEXT.kindLabels[r.kind] || r.kind,
                items: r.items,
                stateLabel: RUN_STATES[r.state] || r.state,
                canCancel: !state.busy && ['queued', 'running'].includes(r.state),
                cancelLabel: TEXT.cancelRun + ' ' + r.id,
            })),
        };
    }

    function dispose() {
        state.disposed = true;
        listeners.clear();
    }

    return {
        state, load, setKind, prepare, start, cancel, view, dispose,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
}

/**
 * Framework-free page state: current-user scope and release. Both clients are
 * re-scoped on a user change so in-flight calls of the previous user are aborted.
 */
export function createAsrProfilesViewController(deps) {
    const clients = [deps.client, deps.adminClient].filter((c) => c && typeof c.switchContext === 'function');
    const state = (deps.makeState || ((o) => o))({ userId: null, epoch: 0, denied: true });
    let disposed = false;

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.epoch += 1;
        for (const c of clients) c.switchContext({ accountId: id, dictationId: null });
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        for (const c of clients) c.switchContext({ accountId: null, dictationId: null });
    }

    setUser(deps.currentUser);
    return { state, setUser, dispose };
}

const CONSENT_TEMPLATE = `
<section role="region" :aria-label="view.title" data-testid="voice-consent" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
  <h3 class="text-base font-semibold text-[var(--text-primary)]">{{ view.title }}</h3>
  <p class="text-sm text-[var(--text-muted)]">{{ hint }}</p>
  <p v-if="view.notice" role="status" aria-live="polite" class="text-sm text-[var(--text-secondary)]">{{ view.notice }}</p>
  <p v-if="view.error" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ view.error.message }}
    <button type="button" v-if="view.error.retryable" class="btn btn--sm btn--ghost ml-2" @click="controller.load()">{{ text.retry }}</button></p>
  <p data-testid="voice-status"><span :style="surfaceStyle(view.statusSurface)" class="inline-flex items-center rounded-full border px-2 text-xs font-semibold leading-5">{{ view.status.label }}</span></p>
  <p class="text-sm text-[var(--text-secondary)]"><span class="font-medium text-[var(--text-primary)]">{{ view.statement.label }}:</span> {{ view.statement.text }}</p>
  <div class="flex flex-wrap items-center gap-2">
    <button type="button" :disabled="!view.canGrant" class="btn btn--sm btn--primary" @click="controller.grant()">Einwilligen</button>
    <button type="button" v-if="!view.confirmingRevoke" :disabled="!view.canRevoke" class="btn btn--sm btn--ghost" @click="controller.requestRevoke()">Einwilligung widerrufen</button>
    <span v-else role="alertdialog" aria-label="Widerruf bestätigen" class="inline-flex flex-wrap items-center gap-2">
      <button type="button" class="btn btn--sm btn--danger" @click="controller.confirmRevoke()">Widerruf bestätigen</button>
      <button type="button" class="btn btn--sm btn--ghost" @click="controller.cancel()">Abbrechen</button>
    </span>
  </div>
</section>`;

const TRAINING_TEMPLATE = `
<section role="region" :aria-label="text.trainingTitle" data-testid="training" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
  <h3 class="text-base font-semibold text-[var(--text-primary)]">{{ text.trainingTitle }}</h3>
  <p v-if="view.denied" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm" data-testid="training-denied">{{ view.deniedMessage }}</p>
  <template v-else>
    <p v-if="view.loading" role="status" aria-live="polite" class="text-sm text-[var(--text-muted)]">{{ text.loading }}</p>
    <p v-if="view.error" role="alert" :style="errorStyle" class="rounded-lg border p-3 text-sm">{{ view.error.message }}
      <button type="button" v-if="view.error.retryable" class="btn btn--sm btn--ghost ml-2" @click="controller.load()">{{ text.retry }}</button></p>
    <p v-if="view.notice" role="status" aria-live="polite" data-testid="training-notice" class="text-sm text-[var(--text-secondary)]">{{ view.notice }}</p>
    <div role="radiogroup" :aria-label="text.kind" class="inline-flex flex-wrap gap-1 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-1">
      <button v-for="k in view.kinds" :key="k.value" type="button" role="radio"
        class="rounded-md px-3 py-1.5 text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)] aria-[checked=true]:bg-[var(--bg-secondary)] aria-[checked=true]:text-[var(--text-primary)] aria-[checked=true]:shadow-sm"
        :aria-checked="k.selected ? 'true' : 'false'" :tabindex="k.selected ? 0 : -1"
        @click="controller.setKind(k.value)">{{ k.label }}</button>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" :disabled="view.busy" class="btn btn--sm btn--ghost" @click="controller.prepare()">{{ text.prepare }}</button>
      <button type="button" :disabled="!view.canStart" class="btn btn--sm btn--primary" @click="controller.start()">{{ text.start }}</button>
    </div>
    <p v-if="view.manifest" data-testid="manifest" class="text-sm text-[var(--text-secondary)]">{{ text.manifestItems }}: {{ view.manifest.items }}, {{ text.manifestExcluded }}: {{ view.manifest.excluded }}</p>
    <p v-if="view.empty" data-testid="training-empty" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ text.noRuns }}</p>
    <div v-else-if="view.runs.length" class="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
      <table :aria-label="text.runs" class="min-w-full divide-y divide-[var(--border-primary)] text-sm">
        <thead class="bg-[var(--bg-tertiary)]"><tr><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.columns.id }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.columns.kind }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.columns.items }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.columns.state }}</th><th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]"><span class="sr-only">Aktion</span></th></tr></thead>
        <tbody class="divide-y divide-[var(--border-primary)] bg-[var(--bg-secondary)]">
          <tr v-for="r in view.runs" :key="r.id" :data-id="r.id" class="hover:bg-[var(--bg-tertiary)]">
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.id }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.kind }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.items }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ r.stateLabel }}</td>
            <td class="px-4 py-3 text-sm text-[var(--text-secondary)] text-right"><button type="button" :disabled="!r.canCancel" :aria-label="r.cancelLabel" class="btn btn--sm btn--ghost" @click="controller.cancel(r.id)">Abbrechen</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>
</section>`;

const CAPABILITY_TEMPLATE = `
<section role="region" :aria-label="text.capabilityTitle" data-testid="capabilities" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
  <h3 class="text-base font-semibold text-[var(--text-primary)]">{{ text.capabilityTitle }}</h3>
  <div class="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
    <table :aria-label="text.capabilityTitle" class="min-w-full divide-y divide-[var(--border-primary)] text-sm">
      <thead class="bg-[var(--bg-tertiary)]"><tr><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.capabilityColumns.name }}</th><th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ text.capabilityColumns.status }}</th></tr></thead>
      <tbody class="bg-[var(--bg-secondary)]"><tr><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ text.pronunciation }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]"><span :style="surfaceStyle(row.surface)" class="inline-flex items-center rounded-full border px-2 text-xs font-semibold leading-5 whitespace-normal [overflow-wrap:anywhere] py-0.5">{{ row.label }}</span></td></tr></tbody>
    </table>
  </div>
</section>`;

export function createSections(Vue) {
    const surfaceStyle = (s) => ({ background: s.background, color: s.color, borderColor: s.border });

    function usePanel(controller, extra) {
        const view = Vue.shallowRef(controller.view());
        const off = controller.subscribe(() => { view.value = controller.view(); });
        Vue.onMounted(() => controller.load());
        Vue.onBeforeUnmount(() => { off(); if (controller.dispose) controller.dispose(); });
        return { view, controller, errorStyle: style('error'), surfaceStyle, focus: FOCUS_INDICATOR, text: TEXT, ...extra };
    }

    const consent = {
        name: 'ClinicVoiceConsentPanel',
        props: { client: { type: Object, required: true } },
        template: CONSENT_TEMPLATE,
        setup(props) {
            return usePanel(createVoiceConsentController({ client: props.client }), { hint: TEXT.voiceHint });
        },
    };

    const training = {
        name: 'ClinicTrainingPanel',
        props: {
            client: { type: Object, required: true },
            profileClient: { type: Object, default: null },
        },
        template: TRAINING_TEMPLATE,
        setup(props) {
            return usePanel(createTrainingController({ client: props.client, profileClient: props.profileClient }));
        },
    };

    // Aussprachetraining is reported on its own and never as success without proof.
    const capabilities = {
        name: 'ClinicCapabilityPanel',
        props: { client: { type: Object, required: true } },
        template: CAPABILITY_TEMPLATE,
        setup(props) {
            const row = Vue.shallowRef({ label: TEXT.capabilityUnknown, surface: surfaceFor('normal') });
            Vue.onMounted(async () => {
                const res = await props.client.request('/pronunciations/capabilities');
                const proven = res.outcome === 'ok' && res.data && res.data.training_proven === true;
                const known = res.outcome === 'ok' && res.data;
                row.value = proven
                    ? { label: TEXT.proven, surface: surfaceFor('success') }
                    : (known
                        ? { label: TEXT.unavailable, surface: surfaceFor('warning') }
                        : { label: TEXT.capabilityUnknown, surface: surfaceFor('normal') });
            });
            return { row, text: TEXT, surfaceStyle };
        },
    };

    return { consent, training, capabilities };
}

export const ASR_PROFILES_TEMPLATE = `
<div class="clinic-asr-profiles-view space-y-4">
  <header>
    <h2 class="text-lg font-semibold text-[var(--text-primary)]">${TEXT.title}</h2>
    <p class="mt-1 text-sm text-[var(--text-muted)]">Erkennungsprofil, Aussprache-Fähigkeit, Stimm-Einwilligung und Training.</p>
  </header>
  <p v-if="state.denied" role="alert" data-testid="denied" class="rounded-lg border p-3 text-sm border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-primary)]">${TEXT.denied}</p>
  <template v-else>
    <div class="${CHILD_SCOPE} rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
      <component :is="parts.Profile" :key="'p' + state.epoch" :client="client"></component>
    </div>
    <component :is="parts.Capabilities" :key="'c' + state.epoch" :client="client"></component>
    <component :is="parts.Consent" :key="'v' + state.epoch" :client="client"></component>
    <component v-if="adminClient" :is="parts.Training" :key="'t' + state.epoch" :client="adminClient" :profile-client="client"></component>
  </template>
</div>`;

export const AsrProfilesView = {
    name: 'ClinicAsrProfilesView',
    props: {
        client: { type: Object, required: true },
        adminClient: { type: Object, default: null },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    template: ASR_PROFILES_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        const ctrl = createAsrProfilesViewController({
            client: props.client,
            adminClient: props.adminClient,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const sections = createSections(Vue);
        const parts = {
            Profile: Vue.markRaw(createPanels(Vue).asr),
            Capabilities: Vue.markRaw(sections.capabilities),
            Consent: Vue.markRaw(sections.consent),
            Training: Vue.markRaw(sections.training),
        };
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        Vue.onBeforeUnmount(() => ctrl.dispose());
        return { state: ctrl.state, parts };
    },
};

export function registerAsrProfilesView(app) {
    app.component('clinic-asr-profiles-view', AsrProfilesView);
    return app;
}
