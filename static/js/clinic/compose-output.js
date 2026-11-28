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
 *   POST /dictations/{id}/exports         body { revision_id, format, template and file options? }, Idempotency-Key -> job
 *   GET  /dictations/{id}/exports/{job}   -> job
 *   GET  /dictations/{id}/exports/{job}/download  -> file attachment
 * Only the released revision is offered for export. A browser retry after an
 * unknown result replays the same body and Idempotency-Key. Server placement
 * is blocked for that revision and format until the user can check the target.
 * A disabled KIS adapter (target unavailable, or 503) is shown
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
    subline: 'Wählen Sie Dateiformat und Ablageort für die freigegebene Version.',
    colTarget: 'Dateiformat',
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
    save: 'Ablegen',
    nameMode: 'Datei benennen',
    filename: 'Dateiname ohne Endung',
    patternHint: '%A steht für den Autor, %T für den Titel.',
    destination: 'Wohin soll die Datei?',
    browser: 'Browser-Download',
    named: 'Name eingeben',
    pattern: 'Muster verwenden',
    collisionPolicy: 'Wenn der Name schon vergeben ist',
    catalogFailed: 'Die Ablageziele sind nicht verfügbar. Sie können die Datei herunterladen.',
    placementUnknown: 'Die Ablage ist nicht bestätigt. Prüfen Sie das Ziel, bevor Sie die Datei erneut ablegen.',
    download: 'Herunterladen',
    exportSelected: 'Ausgewählte ausgeben',
    saveDefaults: 'Standardziele speichern',
    savingDefaults: 'Standardziele werden gespeichert …',
    savedDefaults: 'Standardziele gespeichert.',
    defaultsLoadFailed: 'Die Standardziele konnten nicht geladen werden. Bitte laden Sie die Seite neu.',
    defaultsSaveFailed: 'Die Standardziele konnten nicht gespeichert werden.',
    defaultsChanged: 'Die Standardziele wurden bereits geändert. Bitte laden Sie die Seite neu.',
    kisUnavailable: 'Ausgabe ans KIS nicht verfügbar.',
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

const PLACEMENT_ERRORS = Object.freeze({
    NAME_COLLISION: { label: 'Dateiname am Ziel bereits vergeben.', surface: 'warning' },
    DESTINATION_WRITE_FAILED: { label: 'Die Ablage ist fehlgeschlagen: Die Datei konnte am Ziel nicht geschrieben werden.', surface: 'error' },
    DESTINATION_REJECTED: { label: 'Die Ablage wurde abgelehnt. Prüfen Sie Ziel und Dateinamen.', surface: 'error' },
    INVALID_FILENAME: { label: 'Der Dateiname ist ungültig. Prüfen Sie den Namen oder das Muster.', surface: 'error' },
});

const OUTPUT_PREFERENCES = '/preferences/output';
const preferenceId = (id) => id === 'kis' ? 'kis_draft' : id;

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
 * includeAllFileFormats, includeTemplates, fileOptions, includePreferences, newKey / clipboard / ClipboardItem (test seams).
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
    const keys = new Map(); // request body -> Idempotency-Key
    const readyBySelection = new Map(); // request body -> downloadable result
    const inflight = ref(new Set());
    const destination = ref('');
    const destinations = ref([]);
    const catalogFailed = ref(false);
    const nameMode = ref('manual_name');
    const filename = ref('');
    const collisionPolicy = ref('fail');
    const uncertainPlacements = new Set();
    const selected = ref([]);
    const preferences = ref(null);
    const preferenceNotice = ref('');
    const savingDefaults = ref(false);
    const exportingSelected = ref(false);
    let clipboard = null;

    const surfaceOf = (name) => surfaceFor(name);
    const supportsTemplates = (format) => Object.hasOwn(TEMPLATE_MIMES, format);

    function placementUnconfirmed(format) {
        return Boolean(destination.value) && uncertainPlacements.has(releasedRevisionId.value + '|' + format);
    }

    function baseExportBody(format, templateId, mode) {
        const body = { revision_id: releasedRevisionId.value, format };
        if (templateId) Object.assign(body, { template_id: templateId, text_mode: mode });
        return body;
    }

    function exportBody(format) {
        const templateId = includeTemplates && supportsTemplates(format) && selectedTemplates.value[format];
        const body = baseExportBody(format, templateId, selectedModes.value[format]);
        if (!opts.fileOptions || find(format)?.channel !== 'file') return body;
        const name = filename.value.trim();
        if (name) body[nameMode.value] = name;
        if (destination.value) {
            if (!name) body.filename_pattern = '%A - %T';
            body.destination_alias = destination.value;
            body.collision_policy = collisionPolicy.value;
        } else if (name) body.browser_download = true;
        return body;
    }
    const currentSelectionKey = (format) => JSON.stringify(exportBody(format));
    const jobDownloadUrl = (jobId) => (
        '/api/clinic/v1' + base + '/exports/' + encodeURIComponent(jobId) + '/download'
    );
    const resultView = (phase, label, tone, job, extra) => ({
        phase, label, tone, surface: surfaceOf(tone), job: job || null, ...extra,
    });
    const showResult = (format, result) => {
        results.value = { ...results.value, [format]: result };
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
    const showPrevious = (format, job) => {
        previousDownloads.value = {
            ...previousDownloads.value,
            [format]: { url: jobDownloadUrl(job.id), label: TEXT.previousDownload },
        };
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
        const previous = readyBySelection.get(current?.selectionKey);
        if (previous) showPrevious(format, previous.job);
        const restored = readyBySelection.get(currentKey);
        if (restored) {
            showResult(format, restored);
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
        if (opts.fileOptions && offered.some((t) => t.channel === 'file' && t.available)) {
            const catalog = await client.request(base + '/output-destinations');
            catalogFailed.value = catalog.outcome !== 'ok' || !Array.isArray(catalog.data?.destinations);
            destinations.value = catalogFailed.value ? [] : catalog.data.destinations;
            if (!destinations.value.some((row) => row.name === destination.value)) destination.value = '';
        }
        results.value = {};
        readyBySelection.clear();
        previousDownloads.value = {};
        for (const target of offered) {
            if (target.channel === 'file' && target.available && target.status && target.job_id) {
                const key = JSON.stringify(baseExportBody(target.id, target.template_id, target.text_mode));
                applyJob(target.id, {
                    id: target.job_id, kind: 'file', status: target.status,
                    filename: target.filename,
                }, key, true);
            }
        }
        if (opts.includePreferences) {
            if (releasedRevisionId.value && targets.value.some((t) => t.channel === 'file') && !find('kis')) {
                targets.value.push(targetView({ id: 'kis', channel: 'kis', available: false }, releasedRevisionId.value));
            }
            const defaults = await client.request(OUTPUT_PREFERENCES);
            preferences.value = defaults.outcome === 'ok' && Array.isArray(defaults.data?.formats) ? defaults.data : null;
            selected.value = selectableTargets().filter((t) => preferences.value?.formats.includes(preferenceId(t.id))).map((t) => t.id);
            preferenceNotice.value = preferences.value ? '' : TEXT.defaultsLoadFailed;
        }
        clipboard = null;
        phase.value = 'ready';
        return true;
    }

    function find(format) {
        return targets.value.find((t) => t.id === format) || null;
    }

    function selectableTargets() {
        return releasedRevisionId.value ? targets.value.filter((t) => t.channel === 'file' || t.id === 'kis') : [];
    }

    function toggleTarget(id, checked) {
        if (!selectableTargets().some((t) => t.id === id)) return;
        selected.value = checked ? [...new Set([...selected.value, id])] : selected.value.filter((item) => item !== id);
        if (preferenceNotice.value === TEXT.savedDefaults) preferenceNotice.value = '';
    }

    async function saveDefaults() {
        if (!preferences.value || savingDefaults.value || !selectableTargets().length) return;
        const { version, formats, open_after_export } = preferences.value;
        const visible = new Set(selectableTargets().map((t) => preferenceId(t.id)));
        savingDefaults.value = true;
        preferenceNotice.value = TEXT.savingDefaults;
        try {
            const res = await client.request(OUTPUT_PREFERENCES, {
                method: 'PUT', ifMatch: `"${version}"`,
                body: { formats: [...formats.filter((id) => !visible.has(id)), ...selected.value.map(preferenceId)], open_after_export },
            });
            if (res.outcome === 'ok' && Array.isArray(res.data?.formats)) {
                preferences.value = res.data;
                preferenceNotice.value = TEXT.savedDefaults;
            } else {
                preferenceNotice.value = res.status === 412 ? TEXT.defaultsChanged : TEXT.defaultsSaveFailed;
            }
        } finally {
            savingDefaults.value = false;
        }
    }

    async function exportSelected() {
        if (!releasedRevisionId.value || exportingSelected.value) return;
        exportingSelected.value = true;
        try {
            await Promise.all(selected.value.map(async (id) => {
                const target = find(id);
                if (id === 'kis' || !target?.available) {
                    showResult(id, resultView('unavailable', id === 'kis' ? TEXT.kisUnavailable : target?.reason || TEXT.unavailable, 'warning'));
                } else {
                    await exportTo(id);
                }
            }));
        } finally {
            exportingSelected.value = false;
        }
    }

    /** True when the export of this target may be requested now. */
    function canExport(format) {
        const t = find(format);
        if (!t?.available || t.channel === 'clipboard' || inflight.value.has(format)) return false;
        if (opts.fileOptions && t.channel === 'file') {
            if (!destination.value) return !downloadUrl(format);
            return !placementUnconfirmed(format)
                && destinations.value.some((row) => row.name === destination.value);
        }
        return !(t.channel === 'file' && results.value[format]?.phase === 'ready');
    }

    function downloadUrl(format) {
        const target = find(format);
        const result = results.value[format];
        if (!target?.available || target.channel !== 'file' || result?.phase !== 'ready'
                || result.job?.kind !== 'file' || !result.job.id || result.job.destination
                || destination.value || result.selectionKey !== currentSelectionKey(format)) return null;
        return jobDownloadUrl(result.job.id);
    }

    function markDownloadStarted(format) {
        if (!downloadUrl(format)) return;
        const updated = { ...results.value[format], label: TEXT.downloadStarted };
        showResult(format, updated);
        readyBySelection.set(updated.selectionKey, updated);
    }

    function applyJob(format, job, key, showFilename = false) {
        const current = currentSelectionKey(format) === key;
        if (job.destination?.outcome === 'created') {
            if (current) {
                const label = 'Abgelegt: ' + job.destination.filename + ' – ' + job.destination.label;
                showResult(format, resultView('placed', label, 'success', job, { selectionKey: key }));
            }
            return;
        }
        if (job.status === 'failed' && (job.reason === 'TEMPLATE_MARK_ERROR' || job.reason === 'TEMPLATE_MACRO')) {
            const label = job.reason === 'TEMPLATE_MACRO' ? TEMPLATE_ERRORS.macro : TEMPLATE_ERRORS.mark;
            if (current) showResult(format, resultView('failed', label, 'error', job, { alert: true, selectionKey: key }));
            return;
        }
        const known = JOB_STATUS[job.status] || JOB_STATUS.unknown;
        const label = opts.fileOptions && job.status === 'ready' && job.filename && showFilename
            ? known.label + ': ' + job.filename : known.label;
        const result = resultView(job.status, label, known.surface, job, { selectionKey: key });
        if (job.status === 'ready' && job.kind === 'file' && job.id && !job.destination) {
            readyBySelection.set(key, result);
            if (current) clearPrevious(format);
            else showPrevious(format, job);
        }
        if (current) showResult(format, result);
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
        const body = exportBody(format);
        const placing = Boolean(body.destination_alias);
        const keyId = JSON.stringify(body);
        const idempotencyKey = placing ? makeKey() : (keys.get(keyId) || makeKey());
        if (!placing) keys.set(keyId, idempotencyKey);
        inflight.value = new Set([...inflight.value, format]);
        showResult(format, resultView('working', 'Wird ausgegeben …', 'processing', null, { selectionKey: keyId }));
        try {
            const res = await client.request(base + '/exports', {
                method: 'POST', body, idempotencyKey,
            });
            if (res.outcome === 'ok' && res.data) {
                if (opts.fileOptions || res.data.status === 'failed') keys.delete(keyId);
                applyJob(format, res.data, keyId, body.browser_download === true);
            } else {
                let status = res.outcome;
                let failure;
                if (placing && (['network', 'UNKNOWN', 'cancelled'].includes(res.outcome)
                        || res.code === 'DESTINATION_UNCONFIRMED')) {
                    uncertainPlacements.add(body.revision_id + '|' + format);
                    status = 'unknown';
                    failure = { label: TEXT.placementUnknown, surface: 'warning' };
                } else if (Object.hasOwn(PLACEMENT_ERRORS, res.code)) {
                    keys.delete(keyId);
                    status = res.code;
                    failure = PLACEMENT_ERRORS[res.code];
                } else if (res.status === 503 || res.code === 'KIS_UNAVAILABLE') {
                    status = 'unavailable';
                    failure = { label: TEXT.unavailable, surface: 'warning' };
                } else {
                    // A stale or refused request will not be replayed with the same key.
                    if (res.outcome === 'conflict' || res.outcome === 'validation' || (body.template_id && res.status === 404)) {
                        keys.delete(keyId);
                    }
                    failure = failureFor(res, format, body.template_id, body.text_mode);
                }
                if (currentSelectionKey(format) === keyId) {
                    showResult(format, resultView(status, failure.label, failure.surface, null, {
                        alert: failure.alert, selectionKey: keyId,
                    }));
                }
            }
        } catch {
            if (placing) uncertainPlacements.add(body.revision_id + '|' + format);
            if (currentSelectionKey(format) === keyId) {
                showResult(format, resultView(placing ? 'unknown' : 'network',
                    placing ? TEXT.placementUnknown : FAILURES.network.label, 'warning', null, { selectionKey: keyId }));
            }
        } finally {
            inflight.value = new Set([...inflight.value].filter((id) => id !== format));
        }
        return results.value[format];
    }

    /** Reads the status of a started export (reconciliation); never sends a new one. */
    async function refreshStatus(format) {
        const result = results.value[format];
        if (!result?.job?.id) return null;
        const res = await client.request(base + '/exports/' + encodeURIComponent(result.job.id));
        if (res.outcome === 'ok' && res.data) applyJob(format, res.data, result.selectionKey);
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
        destination, destinations, catalogFailed, nameMode, filename, collisionPolicy, placementUnconfirmed,
        busy: () => inflight.value.size > 0,
        selected, toggleTarget, preferences, preferenceNotice, savingDefaults, saveDefaults, exportingSelected, exportSelected,
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
            includeAllFileFormats: true, includeTemplates: true, fileOptions: true, includePreferences: true,
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
        <div v-if="c.targets.value.some((t) => t.channel === 'file' && t.available)" class="field-grid" style="align-items: start">
          <label class="field">
            <span>{{ TEXT.destination }}</span>
            <select v-model="c.destination.value" :disabled="c.busy()" style="min-height: 48px">
              <option value="">{{ TEXT.browser }}</option>
              <option v-for="target in c.destinations.value" :key="target.name" :value="target.name">{{ target.label }}</option>
            </select>
          </label>
          <label v-if="c.destination.value" class="field">
            <span>{{ TEXT.collisionPolicy }}</span>
            <select v-model="c.collisionPolicy.value" :disabled="c.busy()" style="min-height: 48px">
              <option value="fail">Nicht ablegen</option>
              <option value="version">Nummer an den Namen anhängen</option>
              <option value="unique_name">Eindeutigen Zusatz anhängen</option>
            </select>
          </label>
          <p v-if="c.catalogFailed.value" class="note warning full" role="status">{{ TEXT.catalogFailed }}</p>
          <label class="field">
            <span>{{ TEXT.nameMode }}</span>
            <select v-model="c.nameMode.value" :disabled="c.busy()" style="min-height: 48px">
              <option value="manual_name">{{ TEXT.named }}</option>
              <option value="filename_pattern">{{ TEXT.pattern }}</option>
            </select>
          </label>
          <label class="field" style="min-width: 0">
            <span>{{ TEXT.filename }}</span>
            <input v-model="c.filename.value" type="text" autocomplete="off" :disabled="c.busy()"
                   :placeholder="c.nameMode.value === 'filename_pattern' ? '%A - %T' : 'Automatisch'"
                   :aria-describedby="c.nameMode.value === 'filename_pattern' ? 'out-pattern-hint' : null" style="min-height: 48px">
            <small v-if="c.nameMode.value === 'filename_pattern'" id="out-pattern-hint">{{ TEXT.patternHint }}</small>
          </label>
        </div>
        <div v-if="c.releasedRevisionId.value && c.targets.value.some((t) => t.channel === 'file')" class="actions" style="margin-top: var(--sp-3)">
          <button type="button" class="primary" style="min-height: 48px; padding-inline: var(--sp-3)" :disabled="!c.selected.value.length || c.exportingSelected.value" @click="c.exportSelected()">{{ TEXT.exportSelected }}</button>
          <button type="button" class="quiet" style="min-height: 48px; padding-inline: var(--sp-3)" :disabled="!c.preferences.value || c.savingDefaults.value" @click="c.saveDefaults()">{{ TEXT.saveDefaults }}</button>
        </div>
        <p v-if="c.releasedRevisionId.value && c.preferenceNotice.value" class="text-status" role="status">{{ c.preferenceNotice.value }}</p>
        <div v-if="c.targets.value.some((t) => t.channel === 'file')" class="table-wrap" style="margin-top: 14px">
          <table :aria-label="TEXT.targets" style="white-space: normal">
            <thead><tr><th scope="col">{{ TEXT.colTarget }}</th><th scope="col">{{ TEXT.colStatus }}</th><th scope="col">{{ TEXT.colAction }}</th></tr></thead>
            <tbody>
              <tr v-for="t in c.targets.value.filter((x) => x.channel === 'file')" :key="t.id" :data-target="t.id">
                <td>
                  <label v-if="c.releasedRevisionId.value" class="actions" style="flex-wrap: nowrap; min-height: 48px; cursor: pointer">
                    <input type="checkbox" style="flex: none" :checked="c.selected.value.includes(t.id)" @change="c.toggleTarget(t.id, $event.target.checked)">
                    <strong style="overflow-wrap: anywhere">{{ t.label }}</strong>
                  </label>
                  <strong v-else style="overflow-wrap: anywhere">{{ t.label }}</strong>
                  <div v-if="c.includeTemplates && c.supportsTemplates(t.id)" class="out-template-fields">
                    <div class="field">
                      <label :for="'out-tpl-' + t.id">{{ TEXT.template }}</label>
                      <select :id="'out-tpl-' + t.id" :data-testid="'template-select-' + t.id" :disabled="!t.available || (c.destination.value && c.busy())"
                              :value="c.selectedTemplates.value[t.id] || ''" @change="c.setTemplate(t.id, $event.target.value)">
                        <option value="">{{ TEXT.noTemplate }}</option>
                        <option v-for="tpl in c.templatesFor(t.id)" :key="tpl.id" :value="tpl.id">{{ tpl.name }} (v{{ tpl.version }})</option>
                      </select>
                    </div>
                    <div class="field">
                      <label :for="'out-mode-' + t.id">{{ TEXT.textMode }}</label>
                      <select :id="'out-mode-' + t.id" :data-testid="'mode-select-' + t.id" :disabled="!t.available || (c.destination.value && c.busy())"
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
                  <span v-if="c.placementUnconfirmed(t.id)" class="tag wait" role="status" style="white-space: normal">{{ TEXT.placementUnknown }}</span>
                  <div v-else-if="c.results.value[t.id]?.alert" class="note burgundy" role="alert" data-testid="target-error"
                       :data-phase="c.results.value[t.id].phase"
                       :style="{ marginTop: 0, backgroundColor: c.results.value[t.id].surface.background, color: c.results.value[t.id].surface.color, borderColor: c.results.value[t.id].surface.border }">{{ c.results.value[t.id].label }}</div>
                  <span v-else-if="c.results.value[t.id]" class="tag dot" :class="tagClass(c.results.value[t.id])" role="status" aria-live="polite"
                        style="white-space: normal; overflow-wrap: anywhere" :data-phase="c.results.value[t.id].phase">{{ c.results.value[t.id].label }}</span>
                  <p v-if="!c.destination.value && c.previousDownloads.value[t.id]" class="hint" style="margin: 6px 0 0">
                    <a :href="c.previousDownloads.value[t.id].url" download>{{ c.previousDownloads.value[t.id].label }}</a>
                  </p>
                </td>
                <td>
                  <div class="actions">
                    <a v-if="c.downloadUrl(t.id)" :href="c.downloadUrl(t.id)" download class="button small" style="min-height: 48px; overflow-wrap: anywhere"
                       @click="c.markDownloadStarted(t.id)">{{ TEXT.download }}</a>
                    <button v-else type="button" class="small" style="min-height: 48px" :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ c.destination.value ? TEXT.save : TEXT.export }}</button>
                    <button v-if="c.results.value[t.id] && c.results.value[t.id].job && ['pending', 'unknown'].includes(c.results.value[t.id].phase)"
                            type="button" class="small" style="min-height: 48px" @click="c.refreshStatus(t.id)">{{ TEXT.status }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="c.releasedRevisionId.value" class="empty">{{ TEXT.empty }}</p>
        <div v-if="c.releasedRevisionId.value && c.targets.value.some((t) => t.id === 'kis')" class="actions" style="margin-top: 14px">
          <label class="actions" style="min-height: 48px; cursor: pointer">
            <input type="checkbox" :checked="c.selected.value.includes('kis')" @change="c.toggleTarget('kis', $event.target.checked)">
            <strong>KIS</strong>
          </label>
          <span class="tag wait" style="white-space: normal" role="status" :data-phase="c.results.value.kis?.phase">{{ c.results.value.kis?.label || TEXT.kisUnavailable }}</span>
        </div>
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
