/**
 * Composed clinic output view (V8 P16 Ausgabe: PAR-EXP-001, PAR-EXP-002).
 *
 * Lists the output targets of one dictation, requests the export of the
 * released transcript revision and shows the result per target. Clipboard
 * copy is the existing clipboard-ui module; nothing of it is re-implemented.
 * The view only offers file exports and clipboard copy.
 *
 * Contract (package v8-14, src/clinic/routes_output.py):
 *   GET  /dictations/{id}/output-targets?file_formats=all  -> { released_revision_id, targets: [{ id, channel, available, reason, status?, job_id? }] }
 *   POST /dictations/{id}/exports         body { revision_id, format }, Idempotency-Key -> job { id, status, format, revision_id, filename? }
 *   GET  /dictations/{id}/exports/{job}   -> job
 *   GET  /dictations/{id}/exports/{job}/download  -> file attachment
 * Only the released revision is offered for export. One Idempotency-Key is kept
 * per (revision, format) until a known failure, so a retry after an unknown result
 * replays the same request and never starts a second export. A disabled KIS
 * adapter (target unavailable, or 503) is shown as "nicht verfügbar", never as
 * success. A file job status "ready" means the file was rendered, not saved.
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
    downloadStarted: 'Download gestartet',
    downloadHint: 'Ob die Datei auf diesem Gerät gespeichert wurde, bestätigt der Browser nicht.',
    status: 'Status prüfen',
    unavailable: 'nicht verfügbar',
    clipboard: 'Zwischenablage',
    offline: 'Die Verbindung ist fehlgeschlagen. Das Ergebnis ist unbekannt; ein erneuter Klick sendet dieselbe Anfrage.',
});

export const TARGET_LABELS = Object.freeze({
    txt: 'Textdatei (TXT)',
    docx: 'Word-Dokument (DOCX)',
    rtf: 'Rich-Text-Dokument (RTF)',
    odt: 'OpenDocument-Text (ODT)',
    kis: 'KIS',
    clipboard: 'Zwischenablage',
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

const PLACEMENT_ERRORS = Object.freeze({
    NAME_COLLISION: { label: 'Dateiname am Ziel bereits vergeben.', surface: 'warning' },
    DESTINATION_WRITE_FAILED: { label: 'Die Ablage ist fehlgeschlagen: Die Datei konnte am Ziel nicht geschrieben werden.', surface: 'error' },
    DESTINATION_REJECTED: { label: 'Die Ablage wurde abgelehnt. Prüfen Sie Ziel und Dateinamen.', surface: 'error' },
    INVALID_FILENAME: { label: 'Der Dateiname ist ungültig. Prüfen Sie den Namen oder das Muster.', surface: 'error' },
});

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
 * Framework-neutral controller. Options: client, dictationId, vue, includeAllFileFormats,
 * newKey / clipboard / ClipboardItem (test seams).
 */
export function createOutputViewController(options) {
    const opts = options || {};
    const client = opts.client || createClinicApiClient();
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    const dictationId = String(opts.dictationId);
    const V = opts.vue || globalThis.Vue || null;
    const ref = (V && V.ref) || plainRef;
    const makeKey = opts.newKey || newKey;
    const base = '/dictations/' + encodeURIComponent(dictationId);

    const phase = ref('loading'); // loading | ready | failed
    const releasedRevisionId = ref(null);
    const targets = ref([]);
    const results = ref({}); // format -> { phase, label, surface, job }
    const keys = new Map();
    const inflight = ref(new Set());
    const destination = ref('');
    const destinations = ref([]);
    const catalogFailed = ref(false);
    const nameMode = ref('manual_name');
    const filename = ref('');
    const collisionPolicy = ref('fail');
    const uncertainPlacements = new Set();
    const fileBodies = new Map();

    function placementUnconfirmed(format) {
        return Boolean(destination.value) && uncertainPlacements.has(releasedRevisionId.value + '|' + format);
    }

    function exportBody(format) {
        const body = { revision_id: releasedRevisionId.value, format };
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
    let clipboard = null;

    const surfaceOf = (name) => surfaceFor(name);
    const setResult = (format, status, label, surface, job) => {
        results.value = { ...results.value, [format]: { phase: status, label, tone: surface, surface: surfaceOf(surface), job: job || null } };
    };

    async function load() {
        phase.value = 'loading';
        const query = opts.includeAllFileFormats ? '?file_formats=all' : '';
        const res = await client.request(base + '/output-targets' + query);
        if (res.outcome !== 'ok' || !res.data) {
            phase.value = 'failed';
            return false;
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
        fileBodies.clear();
        for (const target of offered) {
            if (target.channel === 'file' && target.available && target.status && target.job_id) {
                applyJob(target.id, { id: target.job_id, kind: 'file', status: target.status, filename: target.filename });
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
                || result.job?.kind !== 'file' || !result.job.id) return null;
        if (opts.fileOptions && (destination.value || result.job.destination
                || (fileBodies.has(format)
                    ? fileBodies.get(format) !== JSON.stringify(exportBody(format))
                    : Boolean(filename.value.trim())))) return null;
        return '/api/clinic/v1' + base + '/exports/' + encodeURIComponent(result.job.id) + '/download';
    }

    function markDownloadStarted(format) {
        if (!downloadUrl(format)) return;
        results.value = {
            ...results.value,
            [format]: { ...results.value[format], label: TEXT.downloadStarted },
        };
    }

    function applyJob(format, job) {
        const known = JOB_STATUS[job.status] || JOB_STATUS.unknown;
        if (job.destination?.outcome === 'created') {
            setResult(format, 'placed', 'Abgelegt: ' + job.destination.filename + ' – ' + job.destination.label, 'success', job);
        } else {
            const label = opts.fileOptions && job.status === 'ready' && job.filename ? known.label + ': ' + job.filename : known.label;
            setResult(format, job.status, label, known.surface, job);
        }
    }

    async function exportTo(format) {
        if (!canExport(format)) return results.value[format] || null;
        const body = exportBody(format);
        const placing = Boolean(body.destination_alias);
        const keyId = JSON.stringify(body);
        const idempotencyKey = placing ? makeKey() : (keys.get(keyId) || makeKey());
        if (!placing) keys.set(keyId, idempotencyKey);
        inflight.value = new Set([...inflight.value, format]);
        fileBodies.set(format, keyId);
        setResult(format, 'working', 'Wird ausgegeben …', 'processing');
        try {
            const res = await client.request(base + '/exports', {
                method: 'POST', body, idempotencyKey,
            });
            if (res.outcome === 'ok' && res.data) {
                if (opts.fileOptions || res.data.status === 'failed') keys.delete(keyId);
                applyJob(format, res.data);
            } else if (placing && (['network', 'UNKNOWN', 'cancelled'].includes(res.outcome)
                    || res.code === 'DESTINATION_UNCONFIRMED')) {
                uncertainPlacements.add(body.revision_id + '|' + format);
                setResult(format, 'unknown', TEXT.placementUnknown, 'warning');
            } else if (Object.hasOwn(PLACEMENT_ERRORS, res.code)) {
                keys.delete(keyId);
                const failure = PLACEMENT_ERRORS[res.code];
                setResult(format, res.code, failure.label, failure.surface);
            } else if (res.status === 503 || res.code === 'KIS_UNAVAILABLE') {
                setResult(format, 'unavailable', TEXT.unavailable, 'warning');
            } else {
                // A stale or refused request will not be replayed with the same key.
                if (res.outcome === 'conflict' || res.outcome === 'validation') keys.delete(keyId);
                const f = FAILURES[res.outcome] || FAILURES.UNKNOWN;
                setResult(format, res.outcome, f.label, f.surface);
            }
        } finally {
            inflight.value = new Set([...inflight.value].filter((id) => id !== format));
        }
        return results.value[format];
    }

    /** Reads the status of a started export (reconciliation); never sends a new one. */
    async function refreshStatus(format) {
        const job = results.value[format] && results.value[format].job;
        if (!job || !job.id) return null;
        const res = await client.request(base + '/exports/' + encodeURIComponent(job.id));
        if (res.outcome === 'ok' && res.data) applyJob(format, res.data);
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
        dictationId, phase, releasedRevisionId, targets, results, load, canExport, exportTo,
        downloadUrl, markDownloadStarted, refreshStatus, clipboardCopy, text: TEXT,
        destination, destinations, catalogFailed, nameMode, filename, collisionPolicy, placementUnconfirmed,
        busy: () => inflight.value.size > 0,
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
        const c = createOutputViewController({
            client: props.client, dictationId: props.dictationId, vue: props.vue || undefined, includeAllFileFormats: true, fileOptions: true,
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
        <div v-if="c.targets.value.some((t) => t.channel === 'file')" class="table-wrap" style="margin-top: 14px">
          <table :aria-label="TEXT.targets" style="white-space: normal">
            <thead><tr><th scope="col">{{ TEXT.colTarget }}</th><th scope="col">{{ TEXT.colStatus }}</th><th scope="col">{{ TEXT.colAction }}</th></tr></thead>
            <tbody>
              <tr v-for="t in c.targets.value.filter((x) => x.channel === 'file')" :key="t.id" :data-target="t.id">
                <td><strong style="overflow-wrap: anywhere">{{ t.label }}</strong></td>
                <td>
                  <span v-if="!t.available" class="tag wait" style="white-space: normal" data-testid="unavailable">{{ t.reason }}</span>
                  <span v-if="c.placementUnconfirmed(t.id)" class="tag wait" role="status" style="white-space: normal">{{ TEXT.placementUnknown }}</span>
                  <span v-else-if="c.results.value[t.id]" class="tag dot" :class="tagClass(c.results.value[t.id])" role="status" aria-live="polite"
                        style="white-space: normal; overflow-wrap: anywhere" :data-phase="c.results.value[t.id].phase">{{ c.results.value[t.id].label }}</span>
                </td>
                <td>
                  <div class="actions">
                    <a v-if="c.downloadUrl(t.id)" :href="c.downloadUrl(t.id)" download class="button small" style="min-height: 48px"
                       @click="c.markDownloadStarted(t.id)">{{ TEXT.download }}</a>
                    <button v-else type="button" class="small primary" style="min-height: 48px" :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ c.destination.value ? TEXT.save : TEXT.export }}</button>
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
