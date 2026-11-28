/**
 * Composed clinic dictation detail view (V8 P03, PAR-WF-005..008, PAR-WF-012).
 *
 * One full-width view for one dictation. The header is read from
 * GET /dictations/{id}/detail (routes_dictation_detail.py): "Diktiert von",
 * the typist pool, the current takeover ("Übernahme"), the recording time with a
 * marker when it is only a fallback, the Fall-ID when the context is verified,
 * and four separate status axes (processing, workflow, acceptance, output).
 * Lease, workflow actions and history come from lease-ui, workflow-actions-ui
 * and history-ui; the detail client is the one compose-worklist.js already
 * defines. Nothing of those modules is re-implemented here.
 *
 * Return path (UI-V6-D04): the view never reads or writes the origin. It takes
 * an opaque `origin` (filter, page, scroll, focus of the list) from the caller
 * and hands the same object back with the `back` event, so the list can restore
 * its state. Nothing is written to browser storage; W16: no patient name, title
 * or notes, the Fall-ID is the only case reference. A dictation the caller may
 * not read (403 or 404) gets one neutral message without existence details.
 *
 * Exports:
 *   DetailView  Vue options; props client, dictationId, currentUser, vue, origin;
 *               emits `back` ({ dictationId, origin }) and `open-editor` ({ dictationId }).
 *   registerDetailView(app)  app.component('clinic-detail-view', ...).
 *   createDetailViewController(options)  the controller behind the view.
 */

import { TokenContractError, surfaceFor } from './tokens.js';
import { STATUS_LABELS, formatAudioDuration, formatPriority } from './worklist-ui.js';
import { createDetailClient, ensureHostedStyles, statusTone } from './compose-worklist.js';
import { WORKFLOW_ACTIONS_TEMPLATE, createWorkflowActions } from './workflow-actions-ui.js';
import { DictationSummary } from './dictation-summary-ui.js';
import { DetailPlayback } from './detail-playback-ui.js';
import { readAudioRevision } from './correction-editor-ui.js';
import { createHistoryComponent } from './history-ui.js';
import { createLeaseController, createLeaseStatusComponent } from './lease-ui.js';
import { createContextBinding, CONTEXT_BIND_TEMPLATE } from './context-bind-ui.js';

export const COMPOSE_DETAIL_VERSION = 1;

export const TEXT = Object.freeze({
    title: 'Diktatdetails',
    back: 'Zurück',
    loading: 'Diktat wird geladen …',
    denied: 'Dieses Diktat ist nicht verfügbar. Bitte zur Übersicht zurückkehren.',
    failed: 'Das Diktat konnte nicht geladen werden.',
    offline: 'Keine Verbindung. Angezeigt wird der zuletzt bestätigte Stand; serverpflichtige Aktionen sind gesperrt.',
    retry: 'Erneut versuchen',
    reload: 'Aktuellen Stand laden',
    mismatch: 'Der Stand passt nicht zum angeforderten Diktat. Bitte neu laden.',
    author: 'Diktiert von',
    pool: 'Schreibdienst',
    takeover: 'Übernahme',
    target: 'Versandziel',
    noPool: 'Kein Schreibdienst zugeordnet',
    noTakeover: 'Nicht übernommen',
    noTarget: 'Kein Versandziel',
    unknownPerson: 'Unbekannt',
    metadata: 'Metadaten',
    recordingTime: 'Aufnahmezeit',
    fallbackMark: 'Ersatzzeit: Zeitpunkt der Anlage, keine Aufnahmezeit vorhanden',
    duration: 'Dauer',
    priority: 'Priorität',
    deadline: 'Frist',
    noDeadline: 'Keine Frist',
    caseId: 'Fallnummer',
    noCaseId: 'Nicht zugeordnet',
    listen: 'Diktat anhören',
    audioLoading: 'Audio wird geladen …',
    noPlayback: 'Für dieses Diktat ist keine Wiedergabe verfügbar.',
    mediaFailed: 'Die Audiodatei lässt sich nicht abspielen.',
    axes: 'Statusdetails',
    axisProcessing: 'Verarbeitung',
    axisWorkflow: 'Arbeitsstand',
    axisAcceptance: 'Abnahme',
    axisOutput: 'Ausgabe',
    actions: 'Aktionen',
    openEditor: 'Im Schreibplatz öffnen',
    openBlocked: 'Das Diktat wird von einer anderen Person bearbeitet.',
    history: 'Verlauf',
    lease: 'Bearbeitungsstatus',
    notAvailable: '–',
});

export function routingLabel(target) {
    if (!target || typeof target.label !== 'string' || !target.label) return TEXT.noTarget;
    return target.label;
}

const AXIS_LABELS = Object.freeze({
    processing: Object.freeze({
        PENDING: 'Erkennung ausstehend',
        RUNNING: 'Erkennung läuft',
        SUCCEEDED: 'Erkennung abgeschlossen',
        FAILED: 'Erkennung fehlgeschlagen',
        SKIPPED: 'Ohne Erkennung',
    }),
    acceptance: Object.freeze({
        QUEUED: 'Wartet auf Prüfung',
        IN_REVIEW: 'In Prüfung',
        CORRECTED: 'Korrigiert',
        APPROVAL_REQUIRED: 'Abnahme erforderlich',
        APPROVED: 'Abgenommen',
    }),
    output: Object.freeze({
        NOT_RELEASED: 'Nicht freigegeben',
        RELEASED: 'Freigegeben',
    }),
});

const AXIS_SURFACE = Object.freeze({
    PENDING: 'pending', RUNNING: 'processing', SUCCEEDED: 'success', FAILED: 'error', SKIPPED: 'normal',
    QUEUED: 'pending', IN_REVIEW: 'processing', CORRECTED: 'normal', APPROVAL_REQUIRED: 'warning', APPROVED: 'success',
    NOT_RELEASED: 'normal', RELEASED: 'success',
    in_bearbeitung: 'processing', abgenommen: 'success', fertiggestellt: 'success',
    zu_archivieren: 'warning', zu_loeschen: 'warning',
});

const surfaceOrNormal = (name) => {
    try {
        return surfaceFor(name);
    } catch (error) {
        if (error instanceof TokenContractError) return surfaceFor('normal');
        throw error;
    }
};

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

/** The four status axes of the header, each with its own label and surface. Unknown values stay visible as given. */
export function statusAxes(data) {
    const axes = (data && data.status_axes) || {};
    const row = (key, label, value, text) => ({
        key,
        label,
        value: value === null || value === undefined ? null : String(value),
        text: value === null || value === undefined ? TEXT.notAvailable : (text[value] || String(value)),
        style: (() => {
            const s = surfaceOrNormal(AXIS_SURFACE[value] || 'normal');
            return { background: s.background, color: s.color, borderColor: s.border };
        })(),
    });
    return [
        row('processing', TEXT.axisProcessing, axes.processing, AXIS_LABELS.processing),
        row('workflow', TEXT.axisWorkflow, axes.workflow, STATUS_LABELS),
        row('acceptance', TEXT.axisAcceptance, axes.acceptance, AXIS_LABELS.acceptance),
        row('output', TEXT.axisOutput, axes.output, AXIS_LABELS.output),
    ];
}

/** Recording time in Swiss format; `fallback` is true when the server could only offer the creation time. */
export function formatRecordingTime(time) {
    if (!time || !time.at) return { text: TEXT.notAvailable, fallback: false };
    const date = new Date(time.at);
    if (Number.isNaN(date.getTime())) return { text: TEXT.notAvailable, fallback: false };
    const text = date.toLocaleString('de-CH', {
        timeZone: 'Europe/Zurich', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
    return { text, fallback: time.source === 'fallback' };
}

const personText = (person) => (person ? (person.display_name || TEXT.unknownPerson) : null);

/**
 * Controller behind DetailView. `vue` supplies ref, shallowRef, reactive and
 * optionally watch; without it plain (non-reactive) holders are used, which is
 * what the unit tests rely on.
 */
export function createDetailViewController(options) {
    const opts = options || {};
    const client = opts.client;
    const user = opts.currentUser || {};
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    if (user.id === undefined || user.id === null) throw new Error('currentUser.id is required');
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    const dictationId = String(opts.dictationId);
    const V = opts.vue || (typeof window !== 'undefined' ? window.Vue : null) || null;
    const ref = (V && V.ref) || plainRef;
    const shallowRef = (V && V.shallowRef) || ref;
    const reactive = (V && V.reactive) || ((o) => o);

    const status = ref('idle'); // idle | loading | ready | denied | error | offline
    const message = ref('');
    const data = shallowRef(null); // last confirmed header; cleared on a refusal, kept on a network failure
    const leaseState = shallowRef(null);
    const confirmedAt = ref(null);
    const audio = shallowRef(null);
    let lease = null; // { ctrl, unsubscribe }
    let handedOff = false;
    let disposed = false;
    let seq = 0;

    const detailClient = createDetailClient(client, () => { if (!disposed) load(); });
    const workflow = createWorkflowActions({ client: detailClient, reactive });

    // --- lease --------------------------------------------------------------
    const ctrl = createLeaseController({
        client: detailClient,
        dictationId,
        mayTakeOver: user.mayTakeOver === true,
        heartbeatMs: opts.heartbeatMs,
    });
    leaseState.value = ctrl.getState();
    lease = { ctrl, unsubscribe: ctrl.subscribe((snap) => { leaseState.value = snap; }) };
    const leaseRun = {
        acquire: () => ctrl.acquire(),
        release: () => ctrl.release(),
        takeOver: () => ctrl.takeOver(),
        discard: () => ctrl.resolveConflict('discard'),
        reapply: () => ctrl.resolveConflict('reapply'),
    };
    const contextBinding = createContextBinding({
        client, dictationId, reactive,
        canWrite: () => status.value === 'ready' && leaseState.value?.writable === true && ctrl.canWrite(),
        onBound: () => load(),
    });

    // --- loading ------------------------------------------------------------
    function applyWorkflow(next) {
        // Only what the API supplies is shown; a header without capabilities offers no action.
        workflow.load(next ? {
            dictationId,
            etag: next.etag || null,
            revision: Number.isInteger(next.revision) ? next.revision : null,
            allowedActions: Array.isArray(next.allowed_actions) ? next.allowed_actions : [],
            transitions: next.transitions || {},
            binding: next.binding || {},
        } : null);
    }

    async function loadAudio(mine) {
        const response = await detailClient.request('/dictations/' + encodeURIComponent(dictationId) + '/audio-revisions/current');
        if (!disposed && mine === seq && response.outcome !== 'cancelled') {
            audio.value = readAudioRevision(dictationId, response);
        }
    }

    function mediaFailed(event) {
        if (audio.value?.src && event.currentTarget.getAttribute('src') === audio.value.src) {
            audio.value = { status: 'error', src: null, message: TEXT.mediaFailed };
        }
    }

    async function load() {
        if (disposed) return null;
        const mine = (seq += 1);
        status.value = 'loading';
        audio.value = null;
        contextBinding.reset();
        message.value = '';
        const res = await detailClient.request('/dictations/' + encodeURIComponent(dictationId) + '/detail');
        if (disposed || mine !== seq || res.outcome === 'cancelled') return res;
        if (res.outcome === 'ok' && res.data && res.data.dictation_id === dictationId) {
            data.value = res.data;
            confirmedAt.value = Date.now();
            status.value = 'ready';
            applyWorkflow(res.data);
            await Promise.all([contextBinding.load(), loadAudio(mine)]);
            return res;
        }
        if (res.outcome === 'network') {
            // Keep the last confirmed header, marked as such; workflow actions stay locked.
            status.value = 'offline';
            message.value = TEXT.offline;
            if (data.value) applyWorkflow(null);
            return res;
        }
        // Never keep clinical data after a refusal or a mismatching answer.
        data.value = null;
        applyWorkflow(null);
        if (res.outcome === 'permission' || res.status === 404) {
            status.value = 'denied';
            message.value = TEXT.denied;
        } else if (res.outcome === 'ok') {
            status.value = 'error';
            message.value = TEXT.mismatch;
        } else {
            status.value = 'error';
            message.value = TEXT.failed;
        }
        return res;
    }

    // --- derived display ----------------------------------------------------
    const header = () => {
        const d = data.value;
        if (!d) return null;
        const time = formatRecordingTime(d.recording_time);
        const pools = Array.isArray(d.staff_pools) ? d.staff_pools.map((p) => p.name).filter(Boolean) : [];
        return {
            author: personText(d.author) || TEXT.unknownPerson,
            pool: pools.length ? pools.join(', ') : TEXT.noPool,
            takeover: personText(d.takeover) || TEXT.noTakeover,
            target: routingLabel(d.routing_target),
            recordingTime: time.text,
            recordingFallback: time.fallback,
            duration: d.duration_seconds === null || d.duration_seconds === undefined ? TEXT.notAvailable : formatAudioDuration(d.duration_seconds),
            priority: formatPriority(d.priority),
            deadline: d.deadline_at ? formatRecordingTime({ at: d.deadline_at }).text : TEXT.noDeadline,
            caseId: d.case_display || null,
            axes: statusAxes(d),
        };
    };

    const leaseBlocksOpen = () => {
        const s = leaseState.value && leaseState.value.status;
        return s === 'foreign' || s === 'lost';
    };
    const offline = () => status.value === 'offline';
    const canOpenEditor = () => status.value === 'ready' && !leaseBlocksOpen();

    // --- actions ------------------------------------------------------------
    /** Requests the lease (idempotent for the holder) and tells the page to open the editor; the editor holds the lease from there. */
    async function openEditor() {
        if (disposed || !canOpenEditor()) return false;
        const snap = await ctrl.acquire();
        if (disposed || !snap || snap.status !== 'held') return false;
        handedOff = true; // the editor re-acquires as holder; releasing here would drop what it is about to use
        if (typeof opts.onOpenEditor === 'function') opts.onOpenEditor({ dictationId });
        return true;
    }

    /** Returns the caller's own origin object unchanged so the list can restore filter, page, scroll and focus. */
    function back() {
        if (disposed) return false;
        if (typeof opts.onBack === 'function') opts.onBack({ dictationId, origin: opts.origin === undefined ? null : opts.origin });
        return true;
    }

    function start() { return load(); }

    /** Stops the heartbeat and requests, releases a held lease (unless handed to the editor) and clears clinical data. */
    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        workflow.cancel();
        contextBinding.dispose();
        if (lease) {
            const { ctrl: c, unsubscribe } = lease;
            lease = null;
            unsubscribe();
            const done = handedOff ? null : c.release(); // writes stop first, then the server is told
            c.dispose();
            Promise.resolve(done).catch(() => {});
        }
        data.value = null;
        applyWorkflow(null);
        leaseState.value = null;
    }

    return {
        vue: V, status, message, data, leaseState, confirmedAt, leaseRun, workflow, detailClient,
        header, offline, leaseBlocksOpen, canOpenEditor, contextBinding, audio, mediaFailed,
        start, load, openEditor, back, dispose,
        text: TEXT,
    };
}

function buildParts(V) {
    return {
        Lease: createLeaseStatusComponent(V),
        Workflow: { name: 'ClinicWorkflowPanel', props: { wf: { type: Object, required: true } }, template: WORKFLOW_ACTIONS_TEMPLATE },
        History: createHistoryComponent(V),
        ContextBinding: { props: { binding: { type: Object, required: true } }, template: CONTEXT_BIND_TEMPLATE },
    };
}

const BODY_PROPS = {
    client: { type: Object, required: true },
    dictationId: { type: String, required: true },
    currentUser: { type: Object, required: true },
    vue: { type: Object, default: null },
    origin: { type: null, default: null },
    heartbeatMs: { type: Number, default: undefined },
};

/** Inner body: one instance per dictation. The outer view re-keys it, which disposes the lease and clears all data. */
const DetailBody = {
    name: 'ClinicDetailBody',
    components: { DetailPlayback, DictationSummary },
    props: BODY_PROPS,
    emits: ['back', 'open-editor'],
    setup(props, ctx) {
        const emit = (name, payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name, payload); };
        const c = createDetailViewController({
            client: props.client,
            dictationId: props.dictationId,
            currentUser: props.currentUser,
            vue: props.vue || undefined,
            origin: props.origin,
            heartbeatMs: props.heartbeatMs,
            onBack: (payload) => emit('back', payload),
            onOpenEditor: (payload) => emit('open-editor', payload),
        });
        const V = c.vue;
        if (!V) throw new Error('Vue is required to mount the detail view');
        const parts = buildParts(V);
        ensureHostedStyles();
        // The shell mount supplies its own "Zurück" control; then the package's link would be a second one.
        const root = V.ref(null);
        const shellBack = V.ref(false);
        if (typeof V.onMounted === 'function') {
            V.onMounted(() => {
                const host = root.value && typeof root.value.closest === 'function' ? root.value.closest('[data-clinic-shell-view]') : null;
                shellBack.value = !!(host && host.querySelector('[data-testid="clinic-dictation-back"]'));
                c.start();
            });
        }
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        const box = (name) => {
            const s = surfaceOrNormal(name);
            return { background: s.background, color: s.color, border: '1px solid ' + s.border };
        };
        const workflowAxis = (axes) => (axes || []).find((a) => a.key === 'workflow') || {};
        // Header pill: the workflow status axis in the colour of the V8 status pills.
        const tone = (axes) => statusTone(workflowAxis(axes).value);
        const axisText = (axes) => workflowAxis(axes).text || TEXT.notAvailable;
        return { c, parts, TEXT, box, tone, axisText, root, shellBack, status: c.status, message: c.message, leaseState: c.leaseState };
    },
    template: `
<section ref="root" class="v8-page clinic-detail-view" data-testid="clinic-detail-view" aria-labelledby="cdv-title" :aria-busy="status === 'loading' ? 'true' : 'false'">
  <div v-if="!shellBack" class="record-backbar">
    <button type="button" class="back-link" data-testid="back" @click="c.back()">← {{ TEXT.back }}</button>
  </div>

  <p v-if="status === 'loading' && !c.data.value" role="status" aria-live="polite" class="note" data-testid="loading-state">{{ TEXT.loading }}</p>
  <div v-if="status === 'denied'" role="alert" class="note" :style="box('error')" data-testid="denied-state">{{ message }}</div>
  <div v-else-if="status === 'error'" role="alert" class="note" :style="box('error')" data-testid="load-error" style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">
    {{ message }}
    <button type="button" class="secondary" @click="c.load()">{{ TEXT.retry }}</button>
  </div>
  <div v-else-if="status === 'offline'" role="status" class="note warning" data-testid="offline-state" style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">
    {{ message }}
    <button type="button" class="secondary" @click="c.load()">{{ TEXT.retry }}</button>
  </div>

  <template v-if="c.header()">
    <header class="record-detail-heading" data-testid="detail-header">
      <div>
        <div class="eyebrow">{{ c.header().recordingTime }}</div>
        <h1 id="cdv-title" tabindex="-1" style="font-weight: 700">{{ TEXT.title }}</h1>
        <p>{{ TEXT.author }}: {{ c.header().author }} · {{ TEXT.pool }}: {{ c.header().pool }}</p>
        <div class="head-actions">
          <button type="button" class="primary" data-testid="open-editor"
                  :disabled="!c.canOpenEditor()" @click="c.openEditor()">{{ TEXT.openEditor }}</button>
        </div>
      </div>
      <span class="status" :class="tone(c.header().axes)">{{ axisText(c.header().axes) }}</span>
    </header>

    <section v-if="status === 'ready'" class="record-player" :aria-label="TEXT.listen" data-testid="detail-playback">
      <DetailPlayback v-if="c.audio.value?.src" :key="c.audio.value.src" :audio="c.audio.value" :vue="c.vue"
                      :dictation-id="dictationId" @error="c.mediaFailed" />
      <p v-else role="status" class="hint">{{ !c.audio.value ? TEXT.audioLoading :
        (c.audio.value.status === 'unavailable' ? TEXT.noPlayback : c.audio.value.message) }}</p>
    </section>

    <div class="record-detail-grid">
      <div style="display: grid; gap: 22px; align-content: start; min-width: 0">
        <DictationSummary v-if="status === 'ready'" :client="client" :dictation-id="dictationId" expanded />
        <section class="panel" :aria-label="TEXT.axes" data-testid="status-axes">
          <h2 style="font-weight: 700">{{ TEXT.axes }}</h2>
          <ul class="chiprow" style="list-style: none; margin: 0; padding: 0">
            <li v-for="axis in c.header().axes" :key="axis.key" :data-axis="axis.key" :data-value="axis.value" class="tag" :style="axis.style">
              <span>{{ axis.label }}:</span> {{ axis.text }}
            </li>
          </ul>
        </section>

        <section class="panel v8-hosted" :aria-label="TEXT.actions" data-testid="actions">
          <h2 class="cl-sr-only">{{ TEXT.actions }}</h2>
          <p v-if="c.leaseBlocksOpen()" role="note" class="hint" style="margin: 0">{{ TEXT.openBlocked }}</p>
          <component :is="parts.Lease" v-if="leaseState" :state="leaseState" :run="c.leaseRun" />
          <component :is="parts.Workflow" v-if="!c.offline()" :wf="c.workflow" />
          <button v-if="c.workflow.state.stale && !c.offline()" type="button" class="secondary"
                  :disabled="status === 'loading'" @click="c.load()">{{ TEXT.reload }}</button>
        </section>

        <details class="panel" data-testid="history-panel">
          <summary style="cursor: pointer; font-weight: 700">{{ TEXT.history }}</summary>
          <component :is="parts.History" :client="c.detailClient" :dictation-id="dictationId" />
        </details>
      </div>

      <section class="record-metadata" :aria-label="TEXT.metadata" data-testid="metadata">
        <h2>{{ TEXT.metadata }}</h2>
        <dl>
          <div class="metadata-pair"><dt>{{ TEXT.author }}</dt><dd data-testid="author">{{ c.header().author }}</dd></div>
          <div class="metadata-pair"><dt>{{ TEXT.pool }}</dt><dd data-testid="pool">{{ c.header().pool }}</dd></div>
          <div class="metadata-pair"><dt>{{ TEXT.takeover }}</dt><dd data-testid="takeover">{{ c.header().takeover }}</dd></div>
          <div class="metadata-pair"><dt>{{ TEXT.target }}</dt><dd data-testid="routing-target">{{ c.header().target }}</dd></div>
          <div class="metadata-pair">
            <dt>{{ TEXT.recordingTime }}</dt>
            <dd data-testid="recording-time" style="min-width: 0; max-width: 100%">{{ c.header().recordingTime }}
              <span v-if="c.header().recordingFallback" class="tag wait" data-testid="fallback-mark"
                    style="display: block; white-space: normal; max-width: 100%; width: fit-content; margin: 6px 0 0 auto; text-align: left">{{ TEXT.fallbackMark }}</span>
            </dd>
          </div>
          <div class="metadata-pair"><dt>{{ TEXT.duration }}</dt><dd>{{ c.header().duration }}</dd></div>
          <div class="metadata-pair"><dt>{{ TEXT.priority }}</dt><dd>{{ c.header().priority }}</dd></div>
          <div class="metadata-pair"><dt>{{ TEXT.deadline }}</dt><dd>{{ c.header().deadline }}</dd></div>
          <div v-if="c.header().caseId" class="metadata-pair"><dt>{{ TEXT.caseId }}</dt><dd data-testid="case-id">{{ c.header().caseId }}</dd></div>
        </dl>
        <component :is="parts.ContextBinding" :binding="c.contextBinding" />
      </section>
    </div>
  </template>
</section>`,
};

/** Registered view. Re-keying the body on `dictationId` disposes the lease and clears the data of the previous dictation. */
export const DetailView = {
    name: 'ClinicDetailView',
    components: { DetailBody },
    props: BODY_PROPS,
    emits: ['back', 'open-editor'],
    template: `<DetailBody :key="dictationId" :client="client" :dictation-id="dictationId" :current-user="currentUser"
        :vue="vue" :origin="origin" :heartbeat-ms="heartbeatMs" @back="$emit('back', $event)" @open-editor="$emit('open-editor', $event)" />`,
};

export function registerDetailView(app) {
    app.component('clinic-detail-view', DetailView);
    return app;
}
