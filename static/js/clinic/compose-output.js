/**
 * Composed clinic output view (V8 P16 Ausgabe: PAR-EXP-001, PAR-EXP-002, PAR-EXP-004, PAR-CFG-002, PAR-EXP-005).
 *
 * Lists the output targets of one dictation, requests the export of the
 * released transcript revision and shows the result per target. Clipboard
 * copy is the existing clipboard-ui module; nothing of it is re-implemented.
 * The view only offers file exports and clipboard copy.
 *
 * Contract (package v8-14, P-063, P-071, src/clinic/routes_output.py, src/clinic/routes_templates.py):
 *   GET  /dictations/{id}/output-targets?file_formats=all  -> { released_revision_id, targets: [{ id, channel, available, reason, status?, job_id? }] }
 *   GET  /templates                                        -> { items: [{ id, name, scope, version, mime_type, content_sha256 }] }
 *   GET  /templates/{template_id}                          -> { id, ..., insertion_mark }
 *   POST /dictations/{id}/exports         body { revision_id, format, template_id?, text_mode? }, Idempotency-Key -> job
 *   GET  /dictations/{id}/exports/{job}   -> job
 *   GET  /dictations/{id}/exports/{job}/download  -> file attachment
 * Only the released revision is offered for export. One Idempotency-Key is kept
 * per (revision, format, template_id, text_mode) until a known failure, so a
 * retry after an unknown result replays the same request and never starts a
 * second export. A disabled KIS adapter (target unavailable, or 503) is shown
 * as "nicht verfügbar", never as success. A file job status "ready" means the
 * file was rendered, not saved. 422 template mark errors are reported on the
 * target as a German alert banner without claiming success.
 * The view shows ids only: no personal data of the case. Nothing is written to
 * browser storage. Colours come from tokens.js, requests from api-client.js.
 *
 * Exports:
 *   OutputView  Vue options; props client, dictationId, vue?; emits nothing.
 *   registerOutputView(app)  app.component('clinic-output-view', ...).
 *   createOutputViewController(options)  the controller behind the view.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';
import { V8_PAGE } from './compose-import.js';
import { ClipboardCopy, createClipboardCopy } from './clipboard-ui.js';

export const COMPOSE_OUTPUT_VERSION = 1;

export const TEXT = Object.freeze({
    title: 'Ausgabe',
    eyebrow: 'Arbeitsliste',
    subline: 'Freigegebene Version ausgeben und den Status jedes Ausgabekanals getrennt verfolgen.',
    colTarget: 'Ziel',
    colStatus: 'Status',
    colAction: 'Aktion',
    clipboardHint: 'Explizite Browseraktion. Es wird nichts automatisch übertragen.',
    loading: 'Laden …',
    loadFailed: 'Die Ausgabeziele konnten nicht geladen werden.',
    reload: 'Erneut laden',
    revision: 'Ausgegebene Version',
    notReleased: 'Keine freigegebene Version. Eine Ausgabe ist erst nach der Freigabe möglich.',
    targets: 'Ausgabeziele',
    empty: 'Keine Ausgabeziele verfügbar.',
    export: 'Ausgabe starten',
    download: 'Herunterladen',
    downloadStarted: 'Download gestartet',
    downloadHint: 'Ob die Datei auf diesem Gerät gespeichert wurde, bestätigt der Browser nicht.',
    status: 'Status prüfen',
    unavailable: 'nicht verfügbar',
    clipboard: 'Zwischenablage',
    offline: 'Die Verbindung ist fehlgeschlagen. Das Ergebnis ist unbekannt; ein erneuter Klick sendet dieselbe Anfrage.',
    template: 'Vorlage',
    noTemplate: 'Ohne Vorlage',
    textMode: 'Ausgabemodus',
    modeFormatted: 'Formatiert',
    modePlain: 'Nur Text',
    markLabel: 'Textstelle in der Vorlage',
    markAutoHint: 'wird automatisch mit dem Diktattext ersetzt',
    noMarkHint: 'Hinweis: In dieser Vorlage ist keine Textstelle für das Diktat hinterlegt.',
    loadingDetails: 'Vorlagendetails werden geladen …',
    templatesUnavailable: 'Vorlagen konnten nicht geladen werden. Ausgabe ohne Vorlage weiterhin möglich.',
    previousDownload: 'Vorherige Datei öffnen (Stand davor)',
});

export const TARGET_LABELS = Object.freeze({
    txt: 'Textdatei (TXT)',
    docx: 'Word-Dokument (DOCX)',
    rtf: 'Rich-Text-Dokument (RTF)',
    odt: 'OpenDocument-Text (ODT)',
    kis: 'KIS',
    clipboard: 'Zwischenablage',
});

export const TEMPLATE_MIMES = Object.freeze({
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    odt: 'application/vnd.oasis.opendocument.text',
});

export const REASONS = Object.freeze({
    not_released: 'Keine freigegebene Version.',
    kis_unavailable: 'nicht verfügbar',
});

export const JOB_STATUS = Object.freeze({
    ready: { label: 'Datei erzeugt', surface: 'success' },
    failed: { label: 'Datei konnte nicht erzeugt werden.', surface: 'error' },
    stale: { label: 'Datei bitte neu erzeugen.', surface: 'warning' },
    delivered: { label: 'Zugestellt', surface: 'success' },
    pending: { label: 'In Bearbeitung', surface: 'processing' },
    unknown: { label: 'Unbekannt: Bitte Status abgleichen, nicht erneut senden.', surface: 'warning' },
    rejected: { label: 'Abgelehnt', surface: 'error' },
});

const FAILURES = Object.freeze({
    permission: { label: 'Keine Berechtigung für diese Ausgabe.', surface: 'error' },
    conflict: { label: 'Die Version ist veraltet. Bitte neu laden.', surface: 'warning' },
    validation: { label: 'Die Ausgabe wurde abgelehnt.', surface: 'error' },
    network: { label: TEXT.offline, surface: 'warning' },
    UNKNOWN: { label: 'Das Ergebnis ist unbekannt.', surface: 'warning' },
});

const TEMPLATE_ERRORS = Object.freeze({
    mark: 'Die Vorlage legt nicht eindeutig fest, wo der Diktattext eingefügt wird. Ihr freigegebener Text bleibt unverändert erhalten. Bitte eine andere Vorlage oder «Ohne Vorlage» wählen.',
    odtFormatted: 'Für OpenDocument-Vorlagen wird nur «Nur Text» unterstützt. Ihr freigegebener Text bleibt unverändert erhalten. Bitte «Nur Text» oder «Ohne Vorlage» wählen.',
    macro: 'Die Vorlage enthält ein nicht erlaubtes Makro und kann nicht verwendet werden. Ihr freigegebener Text bleibt unverändert erhalten.',
    mismatch: 'Die gewählte Vorlage passt nicht zu diesem Dateiformat.',
    notFound: 'Die gewählte Vorlage ist nicht mehr verfügbar. Bitte neu laden oder eine andere Vorlage wählen.',
    refused: 'Die Ausgabe mit dieser Vorlage wurde abgelehnt. Ihr freigegebener Text bleibt erhalten. Bitte Vorlage prüfen oder «Ohne Vorlage» wählen.',
});

const LOCAL_CSS_ID = 'clinic-v8-output-local';
const LOCAL_CSS = `
.v8-ui .clinic-output-view :is(select,button,.button){min-height:48px}
.v8-ui .clinic-output-view :is(button,.button){white-space:nowrap}
.v8-ui .clinic-output-view .out-template-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-top:8px;min-width:180px}
.v8-ui .clinic-output-view .out-template-fields .field{margin:0;min-width:0}
@media(max-width:760px){
  .v8-ui .clinic-output-view table,.v8-ui .clinic-output-view tbody,.v8-ui .clinic-output-view tr,.v8-ui .clinic-output-view td{display:block;width:100%}
  .v8-ui .clinic-output-view thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  .v8-ui .clinic-output-view tbody tr{display:grid;gap:12px;padding:12px;border-bottom:1px solid var(--line)}
  .v8-ui .clinic-output-view tbody tr:last-child{border-bottom:0}
  .v8-ui .clinic-output-view td{padding:0;border:0}
  .v8-ui .clinic-output-view .out-template-fields{grid-template-columns:1fr;min-width:0}
  .v8-ui .clinic-output-view .actions>*{flex:1 1 auto}
}
`;

function ensureLocalCss() {
    if (typeof document === 'undefined' || !document.head || document.getElementById(LOCAL_CSS_ID)) return;
    const style = document.createElement('style');
    style.id = LOCAL_CSS_ID;
    style.textContent = LOCAL_CSS;
    document.head.appendChild(style);
}

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};

function newKey() {
    return globalThis.crypto.randomUUID();
}

/** Display state of one target from its availability flags. */
export function targetView(target, releasedRevisionId) {
    const available = target.available === true && Boolean(releasedRevisionId);
    const reason = available ? null : (target.reason && REASONS[target.reason]) || TEXT.unavailable;
    return {
        id: target.id,
        channel: target.channel,
        label: TARGET_LABELS[target.id] || String(target.id),
        available,
        reason,
    };
}

/**
 * Framework-neutral controller. Options: client, dictationId, vue,
 * includeAllFileFormats, includeTemplates, newKey / clipboard / ClipboardItem (test seams).
 */
export function createOutputViewController(options) {
    const opts = options || {};
    const client = opts.client || createClinicApiClient();
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    const dictationId = String(opts.dictationId);
    const V = opts.vue || globalThis.Vue || null;
    const ref = (V && V.ref) || plainRef;
    const makeKey = opts.newKey || newKey;
    const includeTemplates = Boolean(opts.includeTemplates);
    const base = '/dictations/' + encodeURIComponent(dictationId);

    const phase = ref('loading'); // loading | ready | failed
    const releasedRevisionId = ref(null);
    const targets = ref([]);
    const results = ref({}); // format -> { phase, label, tone, surface, job, alert? }
    const templates = ref([]); // [{ id, name, scope, version, mime_type, content_sha256 }]
    const templatesFailed = ref(false);
    const templateDetails = ref({}); // templateId -> detail | { loading: true } | { error: true }
    const selectedTemplates = ref({ docx: '', odt: '' });
    const selectedModes = ref({ docx: 'formatted', odt: 'plain' });
    const previousDownloads = ref({}); // format -> { url, label }
    const keys = new Map(); // revision|format(|template|mode) -> Idempotency-Key
    const readyBySelection = new Map(); // format|template|mode -> result
    const inflight = new Set();
    let clipboard = null;

    const surfaceOf = (name) => surfaceFor(name);
    const supportsTemplates = (format) => Object.hasOwn(TEMPLATE_MIMES, format);
    const selectionKeyOf = (format, templateId, mode) => (
        templateId ? `${format}|${templateId}|${mode || ''}` : `${format}||`
    );
    const currentSelectionKey = (format) => {
        if (!supportsTemplates(format)) return `${format}||`;
        const tplId = selectedTemplates.value[format] || '';
        return selectionKeyOf(format, tplId, tplId ? selectedModes.value[format] : '');
    };
    const jobDownloadUrl = (jobId) => (
        '/api/clinic/v1' + base + '/exports/' + encodeURIComponent(jobId) + '/download'
    );
    const setResult = (format, status, label, surface, job, extra) => {
        results.value = {
            ...results.value,
            [format]: {
                phase: status, label, tone: surface, surface: surfaceOf(surface), job: job || null,
                ...(extra || {}),
            },
        };
    };
    const clearResult = (format) => {
        if (!Object.hasOwn(results.value, format)) return;
        const next = { ...results.value };
        delete next[format];
        results.value = next;
    };
    const clearPrevious = (format) => {
        if (!Object.hasOwn(previousDownloads.value, format)) return;
        const next = { ...previousDownloads.value };
        delete next[format];
        previousDownloads.value = next;
    };

    function templatesFor(format) {
        const mime = TEMPLATE_MIMES[format];
        if (!mime) return [];
        return templates.value.filter((tpl) => tpl && tpl.mime_type === mime);
    }

    function selectedTemplateDetail(format) {
        const id = selectedTemplates.value[format];
        return id ? (templateDetails.value[id] || null) : null;
    }

    function syncSelectionResult(format) {
        const currentKey = currentSelectionKey(format);
        const current = results.value[format];
        if (current && current.selectionKey === currentKey) return;
        if (current && current.phase === 'ready' && current.job?.kind === 'file' && current.job.id) {
            readyBySelection.set(current.selectionKey || `${format}||`, current);
            previousDownloads.value = {
                ...previousDownloads.value,
                [format]: { url: jobDownloadUrl(current.job.id), label: TEXT.previousDownload },
            };
        }
        if (readyBySelection.has(currentKey)) {
            const restored = readyBySelection.get(currentKey);
            results.value = { ...results.value, [format]: restored };
            clearPrevious(format);
            return;
        }
        clearResult(format);
    }

    async function load() {
        phase.value = 'loading';
        const query = opts.includeAllFileFormats ? '?file_formats=all' : '';
        const targetReq = client.request(base + '/output-targets' + query);
        const tplReq = includeTemplates ? client.request('/templates') : null;
        const [res, tplRes] = await Promise.all([targetReq, tplReq]);
        if (res.outcome !== 'ok' || !res.data) {
            phase.value = 'failed';
            return false;
        }
        if (includeTemplates) {
            if (tplRes && tplRes.outcome === 'ok' && tplRes.data && Array.isArray(tplRes.data.items)) {
                templates.value = tplRes.data.items;
                templatesFailed.value = false;
                const validIds = new Set(templates.value.map((t) => t.id));
                const nextSel = { ...selectedTemplates.value };
                for (const fmt of Object.keys(TEMPLATE_MIMES)) {
                    if (nextSel[fmt] && !validIds.has(nextSel[fmt])) nextSel[fmt] = '';
                }
                selectedTemplates.value = nextSel;
            } else {
                templates.value = [];
                templatesFailed.value = true;
            }
        }
        releasedRevisionId.value = res.data.released_revision_id || null;
        const offered = res.data.targets || [];
        targets.value = offered.map((t) => targetView(t, releasedRevisionId.value));
        results.value = {};
        readyBySelection.clear();
        previousDownloads.value = {};
        for (const target of offered) {
            if (target.channel === 'file' && target.available && target.status && target.job_id) {
                const selKey = selectionKeyOf(target.id, target.template_id || '', target.text_mode || '');
                applyJob(target.id, {
                    id: target.job_id, kind: 'file', status: target.status,
                    template_id: target.template_id, text_mode: target.text_mode,
                }, selKey);
            }
        }
        clipboard = null;
        phase.value = 'ready';
        return true;
    }

    function find(format) {
        return targets.value.find((t) => t.id === format) || null;
    }

    /** True when the export of this target may be requested now. */
    function canExport(format) {
        const t = find(format);
        return Boolean(t) && t.channel !== 'clipboard' && t.available && !inflight.has(format)
            && !(t.channel === 'file' && results.value[format]?.phase === 'ready');
    }

    function downloadUrl(format) {
        const target = find(format);
        const result = results.value[format];
        if (!target?.available || target.channel !== 'file' || result?.phase !== 'ready'
                || result.job?.kind !== 'file' || !result.job.id) return null;
        return jobDownloadUrl(result.job.id);
    }

    function markDownloadStarted(format) {
        if (!downloadUrl(format)) return;
        const updated = { ...results.value[format], label: TEXT.downloadStarted };
        results.value = { ...results.value, [format]: updated };
        if (updated.selectionKey) readyBySelection.set(updated.selectionKey, updated);
    }

    function applyJob(format, job, selKey) {
        const key = selKey || selectionKeyOf(format, job.template_id || '', job.text_mode || '');
        if (job.status === 'failed' && (job.reason === 'TEMPLATE_MARK_ERROR' || job.reason === 'TEMPLATE_MACRO')) {
            const label = job.reason === 'TEMPLATE_MACRO' ? TEMPLATE_ERRORS.macro : TEMPLATE_ERRORS.mark;
            setResult(format, 'failed', label, 'error', job, { alert: true, selectionKey: key });
            return;
        }
        const known = JOB_STATUS[job.status] || JOB_STATUS.unknown;
        setResult(format, job.status, known.label, known.surface, job, { selectionKey: key });
        if (job.status === 'ready' && job.kind === 'file' && job.id) {
            readyBySelection.set(key, results.value[format]);
            if (currentSelectionKey(format) === key) clearPrevious(format);
        }
    }

    async function setTemplate(format, templateId) {
        if (!supportsTemplates(format)) return;
        const nextId = templateId ? String(templateId) : '';
        if (nextId !== (selectedTemplates.value[format] || '')) {
            selectedTemplates.value = { ...selectedTemplates.value, [format]: nextId };
            syncSelectionResult(format);
        }
        if (nextId && !templateDetails.value[nextId]) {
            templateDetails.value = { ...templateDetails.value, [nextId]: { loading: true } };
            const res = await client.request('/templates/' + encodeURIComponent(nextId));
            if (res.outcome === 'ok' && res.data) {
                templateDetails.value = { ...templateDetails.value, [nextId]: res.data };
            } else {
                templateDetails.value = { ...templateDetails.value, [nextId]: { error: true } };
            }
        }
    }

    function setTextMode(format, mode) {
        if (!supportsTemplates(format)) return;
        const nextMode = mode === 'plain' ? 'plain' : 'formatted';
        if (nextMode === selectedModes.value[format]) return;
        selectedModes.value = { ...selectedModes.value, [format]: nextMode };
        if (selectedTemplates.value[format]) syncSelectionResult(format);
    }

    function failureFor(res, format, templateId, textMode) {
        if (res.status === 422 || res.code === 'TEMPLATE_MARK_ERROR') {
            const label = format === 'odt' && textMode === 'formatted'
                ? TEMPLATE_ERRORS.odtFormatted
                : TEMPLATE_ERRORS.mark;
            return { label, surface: 'error', alert: true };
        }
        if (res.code === 'TEMPLATE_MACRO') return { label: TEMPLATE_ERRORS.macro, surface: 'error', alert: true };
        if (res.code === 'TEMPLATE_FORMAT_MISMATCH') return { label: TEMPLATE_ERRORS.mismatch, surface: 'error', alert: true };
        if (templateId && (res.status === 404 || res.code === 'TEMPLATE_NOT_FOUND')) {
            return { label: TEMPLATE_ERRORS.notFound, surface: 'error', alert: true };
        }
        if (templateId && res.code === 'EXPORT_REFUSED') {
            return { label: TEMPLATE_ERRORS.refused, surface: 'error', alert: true };
        }
        const f = FAILURES[res.outcome] || FAILURES.UNKNOWN;
        return { label: f.label, surface: f.surface, alert: false };
    }

    async function exportTo(format) {
        if (!canExport(format)) return results.value[format] || null;
        const revisionId = releasedRevisionId.value;
        const useTpl = supportsTemplates(format) ? (selectedTemplates.value[format] || '') : '';
        const mode = useTpl ? selectedModes.value[format] : '';
        const selKey = selectionKeyOf(format, useTpl, mode);
        const keyId = useTpl ? `${revisionId}|${format}|${useTpl}|${mode}` : `${revisionId}|${format}`;
        if (!keys.has(keyId)) keys.set(keyId, makeKey());
        const body = useTpl
            ? { revision_id: revisionId, format, template_id: useTpl, text_mode: mode }
            : { revision_id: revisionId, format };
        inflight.add(format);
        setResult(format, 'working', 'Wird ausgegeben …', 'processing', null, { selectionKey: selKey });
        try {
            const res = await client.request(base + '/exports', {
                method: 'POST', body, idempotencyKey: keys.get(keyId),
            });
            if (res.outcome === 'ok' && res.data) {
                if (res.data.status === 'failed') keys.delete(keyId);
                if (currentSelectionKey(format) === selKey) {
                    applyJob(format, res.data, selKey);
                } else if (res.data.status === 'ready' && res.data.kind === 'file' && res.data.id) {
                    const known = JOB_STATUS.ready;
                    readyBySelection.set(selKey, {
                        phase: 'ready', label: known.label, tone: known.surface,
                        surface: surfaceOf(known.surface), job: res.data, selectionKey: selKey,
                    });
                    previousDownloads.value = {
                        ...previousDownloads.value,
                        [format]: { url: jobDownloadUrl(res.data.id), label: TEXT.previousDownload },
                    };
                }
            } else if (res.status === 503 || res.code === 'KIS_UNAVAILABLE') {
                if (currentSelectionKey(format) === selKey) {
                    setResult(format, 'unavailable', TEXT.unavailable, 'warning', null, { selectionKey: selKey });
                }
            } else {
                // A stale or refused request will not be replayed with the same key.
                if (res.outcome === 'conflict' || res.outcome === 'validation' || (useTpl && res.status === 404)) {
                    keys.delete(keyId);
                }
                const f = failureFor(res, format, useTpl, mode);
                if (currentSelectionKey(format) === selKey) {
                    setResult(format, res.outcome, f.label, f.surface, null, { alert: f.alert, selectionKey: selKey });
                }
            }
        } finally {
            inflight.delete(format);
        }
        return results.value[format];
    }

    /** Reads the status of a started export (reconciliation); never sends a new one. */
    async function refreshStatus(format) {
        const job = results.value[format] && results.value[format].job;
        if (!job || !job.id) return null;
        const res = await client.request(base + '/exports/' + encodeURIComponent(job.id));
        if (res.outcome === 'ok' && res.data) applyJob(format, res.data, results.value[format]?.selectionKey);
        return results.value[format];
    }

    /** Clipboard controller of the existing module, bound to the released revision. */
    function clipboardCopy() {
        if (!releasedRevisionId.value) return null;
        if (!clipboard || clipboard.revisionId !== releasedRevisionId.value) {
            clipboard = {
                revisionId: releasedRevisionId.value,
                copier: createClipboardCopy({
                    client, dictationId, revisionId: releasedRevisionId.value, ref,
                    clipboard: opts.clipboard, ClipboardItem: opts.ClipboardItem,
                }),
            };
        }
        return clipboard.copier;
    }

    return {
        dictationId, phase, releasedRevisionId, targets, results,
        templates, templatesFailed, templateDetails, selectedTemplates, selectedModes,
        previousDownloads, includeTemplates,
        supportsTemplates, templatesFor, selectedTemplateDetail, setTemplate, setTextMode,
        load, canExport, exportTo, downloadUrl, markDownloadStarted, refreshStatus, clipboardCopy,
        text: TEXT,
    };
}

// Tag modifier of the shared V8 stylesheet per status surface name.
const TAG_CLASS = Object.freeze({ pending: 'wait', processing: 'wait', success: '', warning: 'wait', error: 'error', normal: 'neutral' });

export const OutputView = {
    name: 'ClinicOutputView',
    components: { ClipboardCopy },
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        vue: { type: Object, default: null },
    },
    setup(props) {
        ensureLocalCss();
        const c = createOutputViewController({
            client: props.client, dictationId: props.dictationId, vue: props.vue || undefined,
            includeAllFileFormats: true, includeTemplates: true,
        });
        c.load();
        const tagClass = (r) => TAG_CLASS[r && r.tone] || '';
        return { c, TEXT, tagClass };
    },
    template: `
<section class="${V8_PAGE} clinic-output-view" aria-labelledby="out-title" data-testid="output-view">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <h1 id="out-title">{{ TEXT.title }}</h1>
      <p class="subline">{{ TEXT.subline }}</p>
    </div>
  </div>
  <p v-if="c.phase.value === 'loading'" class="result-count" role="status" aria-live="polite">{{ TEXT.loading }}</p>
  <div v-else-if="c.phase.value === 'failed'" class="panel" role="alert">
    <p>{{ TEXT.loadFailed }}</p>
    <button type="button" @click="c.load()">{{ TEXT.reload }}</button>
  </div>
  <template v-else>
    <div class="grid">
      <section class="panel" aria-labelledby="out-targets" style="min-width: 0">
        <h2 id="out-targets">{{ TEXT.targets }}</h2>
        <p v-if="c.releasedRevisionId.value" class="text-status" style="overflow-wrap: anywhere" data-testid="revision">{{ TEXT.revision }}: {{ c.releasedRevisionId.value }}</p>
        <p v-else class="note warning" data-testid="not-released">{{ TEXT.notReleased }}</p>
        <p v-if="c.templatesFailed.value" class="note warning" role="status" data-testid="templates-warning">{{ TEXT.templatesUnavailable }}</p>
        <div v-if="c.targets.value.some((t) => t.channel === 'file')" class="table-wrap" style="margin-top: 14px">
          <table :aria-label="TEXT.targets" style="white-space: normal">
            <thead><tr><th scope="col">{{ TEXT.colTarget }}</th><th scope="col">{{ TEXT.colStatus }}</th><th scope="col">{{ TEXT.colAction }}</th></tr></thead>
            <tbody>
              <tr v-for="t in c.targets.value.filter((x) => x.channel === 'file')" :key="t.id" :data-target="t.id">
                <td>
                  <strong style="overflow-wrap: anywhere">{{ t.label }}</strong>
                  <div v-if="c.includeTemplates && c.supportsTemplates(t.id)" class="out-template-fields">
                    <div class="field">
                      <label :for="'out-tpl-' + t.id">{{ TEXT.template }}</label>
                      <select :id="'out-tpl-' + t.id" :data-testid="'template-select-' + t.id" :disabled="!t.available"
                              :value="c.selectedTemplates.value[t.id] || ''" @change="c.setTemplate(t.id, $event.target.value)">
                        <option value="">{{ TEXT.noTemplate }}</option>
                        <option v-for="tpl in c.templatesFor(t.id)" :key="tpl.id" :value="tpl.id">{{ tpl.name }} (v{{ tpl.version }})</option>
                      </select>
                    </div>
                    <div class="field">
                      <label :for="'out-mode-' + t.id">{{ TEXT.textMode }}</label>
                      <select :id="'out-mode-' + t.id" :data-testid="'mode-select-' + t.id" :disabled="!t.available"
                              :value="c.selectedModes.value[t.id]" @change="c.setTextMode(t.id, $event.target.value)">
                        <option value="formatted">{{ TEXT.modeFormatted }}</option>
                        <option value="plain">{{ TEXT.modePlain }}</option>
                      </select>
                    </div>
                  </div>
                  <p v-if="c.includeTemplates && c.supportsTemplates(t.id) && c.selectedTemplates.value[t.id]"
                     class="hint" style="margin: 6px 0 0" :data-testid="'template-mark-' + t.id">
                    <template v-if="c.selectedTemplateDetail(t.id)?.loading">{{ TEXT.loadingDetails }}</template>
                    <template v-else-if="c.selectedTemplateDetail(t.id)?.insertion_mark">{{ TEXT.markLabel }}: {{ c.selectedTemplateDetail(t.id).insertion_mark }} ({{ TEXT.markAutoHint }})</template>
                    <template v-else-if="c.selectedTemplateDetail(t.id) && !c.selectedTemplateDetail(t.id).error">{{ TEXT.noMarkHint }}</template>
                  </p>
                </td>
                <td>
                  <span v-if="!t.available" class="tag wait" style="white-space: normal" data-testid="unavailable">{{ t.reason }}</span>
                  <div v-if="c.results.value[t.id]?.alert" class="note burgundy" role="alert" data-testid="target-error"
                       :data-phase="c.results.value[t.id].phase"
                       :style="{ marginTop: 0, backgroundColor: c.results.value[t.id].surface.background, color: c.results.value[t.id].surface.color, borderColor: c.results.value[t.id].surface.border }">{{ c.results.value[t.id].label }}</div>
                  <span v-else-if="c.results.value[t.id]" class="tag dot" :class="tagClass(c.results.value[t.id])" role="status" aria-live="polite"
                        style="white-space: normal" :data-phase="c.results.value[t.id].phase">{{ c.results.value[t.id].label }}</span>
                  <p v-if="c.previousDownloads.value[t.id]" class="hint" style="margin: 6px 0 0">
                    <a :href="c.previousDownloads.value[t.id].url" download>{{ c.previousDownloads.value[t.id].label }}</a>
                  </p>
                </td>
                <td>
                  <div class="actions">
                    <a v-if="c.downloadUrl(t.id)" :href="c.downloadUrl(t.id)" download class="button small"
                       @click="c.markDownloadStarted(t.id)">{{ TEXT.download }}</a>
                    <button v-else type="button" class="small primary" :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ TEXT.export }}</button>
                    <button v-if="c.results.value[t.id] && c.results.value[t.id].job && ['pending', 'unknown'].includes(c.results.value[t.id].phase)"
                            type="button" class="small" @click="c.refreshStatus(t.id)">{{ TEXT.status }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="c.releasedRevisionId.value" class="empty">{{ TEXT.empty }}</p>
        <p v-if="c.targets.value.some((t) => c.downloadUrl(t.id))" class="hint" style="margin-top: 14px">{{ TEXT.downloadHint }}</p>
      </section>
      <section v-if="c.releasedRevisionId.value" class="panel" aria-labelledby="out-clip">
        <h2 id="out-clip">{{ TEXT.clipboard }}</h2>
        <p class="hint">{{ TEXT.clipboardHint }}</p>
        <ClipboardCopy class="actions" :client="client" :dictation-id="dictationId" :revision-id="c.releasedRevisionId.value" />
      </section>
    </div>
  </template>
</section>`,
};

export function registerOutputView(app) {
    app.component('clinic-output-view', OutputView);
    return app;
}
