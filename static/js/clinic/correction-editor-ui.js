/**
 * Correction editor for typists (PAR-COR-001, PAR-COR-010).
 *
 * Loads the current transcript text, the validated current audio revision and
 * the selected versioned template of one dictation. Speech recognition is a
 * server capability only: when the text is empty (ASR absent or failed) the
 * view runs in manual mode, so a workstation without any speech engine can
 * still transcribe and correct by ear. Clinical text and lease proof live in
 * memory only; nothing is written to browser storage. No patient data is
 * shown: the view carries the dictation id only. Colours come from tokens.js,
 * requests from the injected api-client.js client.
 *
 * Exports (consumed by w150 alignment, w152/w156 playback, w154 save, w212):
 *   useCorrectionEditor({ client, dictationId, currentUser, vue?, exposeLeaseController? }) returns
 *     state refs  text, textRevisionId, etag, dirty, manual, loading, error,
 *                 lease (snapshot incl. generation), audio, template,
 *                 templates, canEdit, announcement
 *     methods     load({ discard? }), applyTemplate(templateId), setText(text),
 *                 acquire(), release(), takeOver(), dispose(), audioSource()
 *     leaseController  only with the option exposeLeaseController: the single lease controller of
 *                 this editor (lease-ui.js), so the save module shares one proof, one generation
 *                 and one unsaved marker (compose-correction.js passes it for contract 'v8')
 *     `text` is the editable text model, `textRevisionId` / `etag` the
 *     revision the text was read at (send as expected_text_revision_id and
 *     If-Match on save). `lease.generation` is the lease_generation to send.
 *     `audio` is { status, revisionId, etag, src }.
 *   CorrectionEditor: Vue options object, props client, dictationId,
 *     currentUser, vue (optional).
 *   registerCorrectionEditor(app): app.component('clinic-correction-editor', ...).
 *
 * Keyboard: every control is native (Tab, Enter, Space). The audio element is
 * operated with its native keys; the text area is read-only without a lease.
 */

import { createClinicApiClient } from './api-client.js';
import { createLeaseController, leaseActions } from './lease-ui.js';
import { createTemplatesManager } from './templates-ui.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Korrektur',
    text: 'Transkript',
    audio: 'Audio zum Diktat',
    template: 'Vorlage',
    noTemplate: 'Keine Vorlage gewählt',
    retry: 'Erneut versuchen',
    reload: 'Aktuellen Stand laden',
    manual: 'Keine automatische Erkennung vorhanden. Bitte manuell transkribieren.',
    loading: 'Wird geladen …',
    loaded: 'Diktat geladen.',
    denied: 'Keine Berechtigung für dieses Diktat.',
    notFound: 'Das Diktat wurde nicht gefunden oder ist nicht zugänglich.',
    stale: 'Der Stand ist veraltet. Bitte neu laden.',
    failed: 'Das Laden ist fehlgeschlagen.',
    unsaved: 'Nicht gespeicherte Änderungen sind vorhanden.',
    audioMissing: 'Kein Audio verfügbar.',
    audioDenied: 'Keine Berechtigung für das Audio.',
    audioFailed: 'Das Audio konnte nicht geladen werden.',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
    templateFailed: 'Die Vorlagen konnten nicht geladen werden.',
});

const ROOT = '/api/clinic/v1';

function fail(code) {
    return { ok: false, code, message: LABELS[code] || LABELS.failed };
}

function errorFor(res) {
    if (res.status === 404) return fail('notFound');
    if (res.outcome === 'permission') return fail('denied');
    if (res.outcome === 'conflict') return fail('stale');
    return fail('failed');
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    return { ref, shallow, reactive: V && V.reactive ? V.reactive : undefined };
}

/** @param {{client?: object, dictationId: string, currentUser?: object, vue?: object}} options */
export function useCorrectionEditor(options) {
    const opts = options || {};
    const client = opts.client || createClinicApiClient();
    const dictationId = String(opts.dictationId);
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(dictationId);
    const user = opts.currentUser || {};

    const text = refs.ref('');
    const textRevisionId = refs.ref(null);
    const etag = refs.ref(null);
    const dirty = refs.ref(false);
    const manual = refs.ref(true);
    const loading = refs.ref(false);
    const error = refs.shallow(null);
    const audio = refs.shallow({ status: 'idle', revisionId: null, etag: null, src: null, message: '' });
    const template = refs.shallow(null);
    const templatesError = refs.ref('');
    const canEdit = refs.ref(false);
    const announcement = refs.ref('');

    client.switchContext({ accountId: user.id != null ? user.id : null, dictationId });

    const manager = createTemplatesManager({ client, reactive: refs.reactive });
    const lease = createLeaseController({ client, dictationId, mayTakeOver: user.mayTakeOver === true });
    const leaseState = refs.shallow(lease.getState());

    let seq = 0;
    let disposed = false;

    function recompute() {
        canEdit.value = leaseState.value.writable && !loading.value && !error.value;
    }

    lease.subscribe((snap) => { leaseState.value = snap; recompute(); });

    function say(message) {
        announcement.value = message;
    }

    function audioSource(revisionId) {
        const rev = revisionId || audio.value.revisionId;
        if (!rev) return null;
        return ROOT + base + '/audio/derived?format=wav&audio_revision_id=' + encodeURIComponent(rev);
    }

    function readAudio(res) {
        if (res.outcome === 'ok' && res.data && typeof res.data.revision_id === 'string') {
            const id = res.data.revision_id;
            return { status: 'ready', revisionId: id, etag: '"' + id + '"', src: audioSource(id), message: '' };
        }
        const none = { revisionId: null, etag: null, src: null };
        if (res.status === 404) return { status: 'unavailable', ...none, message: LABELS.audioMissing };
        if (res.outcome === 'permission') return { status: 'denied', ...none, message: LABELS.audioDenied };
        return { status: 'error', ...none, message: LABELS.audioFailed };
    }

    async function loadTemplates() {
        const res = await manager.load();
        templatesError.value = res.ok ? '' : res.message;
        return res;
    }

    /** Load text, current audio and the template list. Unsaved edits are kept unless `discard` is set. */
    async function load(loadOptions) {
        if (disposed) return fail('failed');
        if (dirty.value && !(loadOptions && loadOptions.discard)) return fail('unsaved');
        const mine = ++seq;
        loading.value = true;
        error.value = null;
        audio.value = { ...audio.value, status: 'loading', message: '' };
        recompute();
        say(LABELS.loading);
        const [corr, aud] = await Promise.all([
            client.request(base + '/correction'),
            client.request(base + '/audio-revisions/current'),
        ]);
        if (mine !== seq || disposed) return { ok: false, code: 'cancelled', message: '' };
        loading.value = false;
        if (corr.outcome === 'cancelled') return { ok: false, code: 'cancelled', message: '' };
        const data = corr.data;
        if (corr.outcome !== 'ok' || !data || typeof data.text !== 'string'
            || (data.text_revision_id !== null && typeof data.text_revision_id !== 'string')) {
            const problem = corr.outcome === 'ok' ? fail('failed') : errorFor(corr);
            error.value = problem;
            audio.value = { status: 'idle', revisionId: null, etag: null, src: null, message: '' };
            recompute();
            say(problem.message);
            return problem;
        }
        text.value = data.text;
        textRevisionId.value = data.text_revision_id;
        // The server issues the validator (revision + lease generation); an older server only names the revision.
        etag.value = typeof data.etag === 'string' && data.etag
            ? data.etag
            : (data.text_revision_id ? '"' + data.text_revision_id + '"' : null);
        manual.value = data.text === '';
        dirty.value = false;
        audio.value = aud.outcome === 'cancelled' ? { ...audio.value, status: 'idle' } : readAudio(aud);
        recompute();
        await loadTemplates();
        say(manual.value ? LABELS.manual : LABELS.loaded);
        return { ok: true };
    }

    /** Select a versioned template for this view; the server keeps the append-only history. */
    async function applyTemplate(templateId) {
        if (!manager.state.loaded) {
            const loaded = await loadTemplates();
            if (!loaded.ok) return loaded;
        }
        const picked = manager.select(templateId);
        if (!picked.ok) return picked;
        const row = picked.template;
        const issues = manager.issues(templateId);
        template.value = {
            id: row.id, name: row.name, version: row.version, scope: row.scope,
            marker: row.marker, sha256: row.sha256,
            stale: issues.some((i) => i.code === 'stale_version'), issues,
        };
        say(row.name + ', Version ' + row.version);
        return { ok: true, template: template.value };
    }

    /** Edit the text model. Allowed only while the edit lease is held. */
    function setText(next) {
        if (typeof next !== 'string' || !canEdit.value) return false;
        if (next === text.value) return true;
        text.value = next;
        dirty.value = true;
        lease.setUnsaved(next);
        return true;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        lease.dispose();
        client.switchContext({ accountId: null, dictationId: null });
    }

    return {
        text, textRevisionId, etag, dirty, manual, loading, error, audio, template,
        templates: manager.state, templatesError, lease: leaseState, canEdit, announcement,
        load, applyTemplate, setText, dispose, audioSource,
        ...(opts.exposeLeaseController === true ? { leaseController: lease } : {}),
        acquire: lease.acquire, release: lease.release, takeOver: lease.takeOver,
    };
}

/** Vue component. Props: client, dictationId, currentUser, vue. */
/** Re-exported so the composed view reaches the toolbar controller through a module it already imports. */
export { createCorrectionToolbar, FONT_SIZE, vTip } from './correction-toolbar-ui.js';

export const CorrectionEditor = {
    name: 'ClinicCorrectionEditor',
    props: {
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        currentUser: { type: Object, default: () => ({}) },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const editor = useCorrectionEditor({
            client: props.client || undefined, dictationId: props.dictationId,
            currentUser: props.currentUser, vue: props.vue || undefined,
        });
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { editor.load(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(editor.dispose);
        const actions = V && V.computed ? V.computed(() => leaseActions(editor.lease.value)) : { value: [] };
        const surface = V && V.computed
            ? V.computed(() => surfaceFor(editor.error.value ? 'error' : (editor.loading.value ? 'processing' : 'normal')))
            : { value: surfaceFor('normal') };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        const leaseRun = {
            acquire: editor.acquire, release: editor.release, takeOver: editor.takeOver,
        };
        function onText(event) {
            editor.setText(event.target.value);
        }
        function onTemplate(event) {
            if (event.target.value) editor.applyTemplate(event.target.value);
        }
        return { ...editor, LABELS, actions, surface, focusStyle, leaseRun, onText, onTemplate };
    },
    template: `
<section class="clinic-correction panel" :aria-label="LABELS.title" :aria-busy="loading" :data-manual="manual">
  <h2>{{ LABELS.title }}</h2>
  <div v-if="error" role="alert"
    :style="{ background: surface.background, color: surface.color, border: '1px solid ' + surface.border }">
    {{ error.message }}
    <button type="button" :style="focusStyle" @click="load({ discard: error.code === 'stale' })">
      {{ error.code === 'stale' ? LABELS.reload : LABELS.retry }}
    </button>
  </div>
  <p v-if="manual && !loading && !error" role="note">{{ LABELS.manual }}</p>
  <div class="clinic-correction-lease" role="group" aria-label="Bearbeitungsstatus">
    <p role="status" aria-live="polite">{{ lease.message }}</p>
    <button v-for="a in actions" :key="a.id" type="button" class="small" :disabled="a.disabled" :style="focusStyle"
      @click="leaseRun[a.id] && leaseRun[a.id]()">{{ a.label }}</button>
  </div>
  <div class="clinic-correction-audio">
    <audio v-if="audio.status === 'ready'" controls preload="metadata" :src="audio.src" :aria-label="LABELS.audio"></audio>
    <p v-else-if="audio.status !== 'idle' && audio.status !== 'loading'" role="note">{{ audio.message }}</p>
  </div>
  <label>
    {{ LABELS.template }}
    <select :style="focusStyle" :value="template ? template.id : ''" @change="onTemplate">
      <option value="">{{ LABELS.noTemplate }}</option>
      <option v-for="row in templates.items" :key="row.id" :value="row.id">{{ row.name }} (v{{ row.version }})</option>
    </select>
  </label>
  <p v-if="templatesError" role="note">{{ templatesError }}</p>
  <ul v-if="template && template.issues.length" role="note" aria-label="Hinweise zur Vorlage">
    <li v-for="issue in template.issues" :key="issue.code">{{ issue.message }}</li>
  </ul>
  <label>
    {{ LABELS.text }}
    <textarea rows="12" class="editor" :style="focusStyle" :value="text" :readonly="!canEdit"
      :aria-readonly="String(!canEdit)" :aria-describedby="canEdit ? null : 'clinic-correction-readonly'"
      @input="onText"></textarea>
  </label>
  <p v-if="!canEdit && !loading" id="clinic-correction-readonly">{{ LABELS.readOnly }}</p>
  <p v-if="dirty" role="note">{{ LABELS.unsaved }}</p>
  <div role="status" aria-live="polite">{{ announcement }}</div>
</section>`,
};

export function registerCorrectionEditor(app) {
    app.component('clinic-correction-editor', CorrectionEditor);
    return app;
}
