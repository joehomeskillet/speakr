/**
 * Composed clinic output view (V8 P16 Ausgabe: PAR-EXP-001, PAR-EXP-002).
 *
 * Lists the output targets of one dictation, requests the export of the
 * released transcript revision and shows the result per target. Clipboard
 * copy is the existing clipboard-ui module; nothing of it is re-implemented.
 * The view only offers file exports and clipboard copy.
 *
 * Contract (package v8-14, src/clinic/routes_output.py):
 *   GET  /dictations/{id}/output-targets?file_formats=all  -> { released_revision_id, targets }
 *        An available clipboard target includes revision_id, binding and an optional working-copy label.
 *   POST /dictations/{id}/exports         body { revision_id, format }, Idempotency-Key -> job { id, status, format, revision_id, filename? }
 *   GET  /dictations/{id}/exports/{job}   -> job
 *   GET  /dictations/{id}/exports/{job}/download  -> file attachment
 * Only the released revision is offered for file export. One Idempotency-Key is kept
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
    subline: 'Text kopieren oder die freigegebene Version als Datei ausgeben.',
    colTarget: 'Ziel',
    colStatus: 'Status',
    colAction: 'Aktion',
    clipboardHint: 'Kopieren Sie den Text mit oder ohne Formatierung.',
    workingCopy: 'Arbeitskopie',
    workingCopyHint: 'Kopieren gibt den Text nicht frei.',
    releasedCopy: 'Freigegebene Version',
    loading: 'Laden …',
    loadFailed: 'Die Ausgabeziele konnten nicht geladen werden.',
    reload: 'Erneut laden',
    revision: 'Ausgegebene Version',
    notReleased: 'Keine freigegebene Version. Dateiausgaben sind erst nach der Freigabe möglich.',
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
    const clipboardTarget = ref(null);
    const targets = ref([]);
    const results = ref({}); // format -> { phase, label, surface, job }
    const keys = new Map(); // revision|format -> Idempotency-Key
    const inflight = new Set();
    let clipboard = null;

    const surfaceOf = (name) => surfaceFor(name);
    const setResult = (format, status, label, surface, job) => {
        results.value = { ...results.value, [format]: { phase: status, label, tone: surface, surface: surfaceOf(surface), job: job || null } };
    };

    async function load() {
        phase.value = 'loading';
        clipboardTarget.value = null;
        const query = opts.includeAllFileFormats ? '?file_formats=all' : '';
        const res = await client.request(base + '/output-targets' + query);
        if (res.outcome !== 'ok' || !res.data) {
            phase.value = 'failed';
            return false;
        }
        releasedRevisionId.value = res.data.released_revision_id || null;
        const offered = res.data.targets || [];
        const copy = offered.find((t) => t.id === 'clipboard' && t.available === true);
        const hasCopyBinding = typeof copy?.binding === 'string' && Boolean(copy.binding.trim());
        const hasCopyRevision = typeof copy?.revision_id === 'string' && Boolean(copy.revision_id.trim());
        const copyRevision = opts.includeAllFileFormats
            ? (hasCopyRevision && hasCopyBinding ? copy.revision_id : null)
            : (copy?.revision_id || releasedRevisionId.value);
        if (copy && copyRevision) {
            clipboardTarget.value = { revisionId: copyRevision, binding: copy.binding, label: copy.label };
        }
        targets.value = offered.map((t) => targetView(t, t.channel === 'clipboard' ? copyRevision : releasedRevisionId.value));
        results.value = {};
        for (const target of offered) {
            if (target.channel === 'file' && target.available && target.status && target.job_id) {
                applyJob(target.id, { id: target.job_id, kind: 'file', status: target.status });
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
        setResult(format, job.status, known.label, known.surface, job);
    }

    async function exportTo(format) {
        if (!canExport(format)) return results.value[format] || null;
        const revisionId = releasedRevisionId.value;
        const keyId = revisionId + '|' + format;
        if (!keys.has(keyId)) keys.set(keyId, makeKey());
        inflight.add(format);
        setResult(format, 'working', 'Wird ausgegeben …', 'processing');
        try {
            const res = await client.request(base + '/exports', {
                method: 'POST', body: { revision_id: revisionId, format }, idempotencyKey: keys.get(keyId),
            });
            if (res.outcome === 'ok' && res.data) {
                if (res.data.status === 'failed') keys.delete(keyId);
                applyJob(format, res.data);
            } else if (res.status === 503 || res.code === 'KIS_UNAVAILABLE') {
                setResult(format, 'unavailable', TEXT.unavailable, 'warning');
            } else {
                // A stale or refused request will not be replayed with the same key.
                if (res.outcome === 'conflict' || res.outcome === 'validation') keys.delete(keyId);
                const f = FAILURES[res.outcome] || FAILURES.UNKNOWN;
                setResult(format, res.outcome, f.label, f.surface);
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
        if (res.outcome === 'ok' && res.data) applyJob(format, res.data);
        return results.value[format];
    }

    /** Clipboard controller of the existing module, bound to the offered revision. */
    function clipboardCopy() {
        if (!clipboardTarget.value) return null;
        clipboard ||= createClipboardCopy({
            client, dictationId, ...clipboardTarget.value, ref,
            clipboard: opts.clipboard, ClipboardItem: opts.ClipboardItem,
        });
        return clipboard;
    }

    return {
        dictationId, phase, releasedRevisionId, clipboardTarget, targets, results, load, canExport, exportTo,
        downloadUrl, markDownloadStarted, refreshStatus, clipboardCopy, text: TEXT,
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
            client: props.client, dictationId: props.dictationId, vue: props.vue || undefined, includeAllFileFormats: true,
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
    <button type="button" class="!min-h-12" @click="c.load()">{{ TEXT.reload }}</button>
  </div>
  <template v-else>
    <div class="grid equal">
      <section v-if="c.clipboardTarget.value" class="panel" aria-labelledby="out-clip">
        <h2 id="out-clip">{{ TEXT.clipboard }}</h2>
        <div v-if="c.clipboardTarget.value.binding === 'working_copy'" class="note warning">
          <strong>{{ c.clipboardTarget.value.label || TEXT.workingCopy }}</strong>
          <p>{{ TEXT.workingCopyHint }}</p>
        </div>
        <p v-else class="text-status">{{ c.clipboardTarget.value.label || TEXT.releasedCopy }}</p>
        <p class="hint">{{ TEXT.clipboardHint }}</p>
        <ClipboardCopy class="actions [&>button]:min-h-12 [&>button]:min-w-12
                              [&>button:first-child]:!bg-brand-primary [&>button:first-child]:!text-brand-canvas
                              dark:[&>button:first-child]:!bg-brand-accent-darkmode dark:[&>button:first-child]:!text-brand-primary
                              [&>button:first-child:hover:not(:disabled)]:brightness-110
                              [&>button:first-child:active:not(:disabled)]:brightness-90"
                       :key="JSON.stringify([c.clipboardTarget.value.revisionId, c.clipboardTarget.value.binding])"
                       :client="client" :dictation-id="dictationId"
                       :revision-id="c.clipboardTarget.value.revisionId" :binding="c.clipboardTarget.value.binding" />
      </section>
      <section class="panel" aria-labelledby="out-targets" style="min-width: 0">
        <h2 id="out-targets">{{ TEXT.targets }}</h2>
        <p v-if="c.releasedRevisionId.value" class="text-status" style="overflow-wrap: anywhere" data-testid="revision">{{ TEXT.revision }}: {{ c.releasedRevisionId.value }}</p>
        <p v-else class="note warning" data-testid="not-released">{{ TEXT.notReleased }}</p>
        <div v-if="c.targets.value.some((t) => t.channel === 'file')" class="table-wrap" style="margin-top: 14px">
          <table :aria-label="TEXT.targets" style="white-space: normal">
            <thead><tr><th scope="col">{{ TEXT.colTarget }}</th><th scope="col">{{ TEXT.colStatus }}</th><th scope="col">{{ TEXT.colAction }}</th></tr></thead>
            <tbody>
              <tr v-for="t in c.targets.value.filter((x) => x.channel === 'file')" :key="t.id" :data-target="t.id">
                <td><strong style="overflow-wrap: anywhere">{{ t.label }}</strong></td>
                <td>
                  <span v-if="!t.available" class="tag wait" style="white-space: normal" data-testid="unavailable">{{ t.reason }}</span>
                  <span v-if="c.results.value[t.id]" class="tag dot" :class="tagClass(c.results.value[t.id])" role="status" aria-live="polite"
                        style="white-space: normal" :data-phase="c.results.value[t.id].phase">{{ c.results.value[t.id].label }}</span>
                </td>
                <td>
                  <div class="actions">
                    <a v-if="c.downloadUrl(t.id)" :href="c.downloadUrl(t.id)" download class="button small !min-h-12"
                       @click="c.markDownloadStarted(t.id)">{{ TEXT.download }}</a>
                    <button v-else type="button" class="small primary !min-h-12" :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ TEXT.export }}</button>
                    <button v-if="c.results.value[t.id] && c.results.value[t.id].job && ['pending', 'unknown'].includes(c.results.value[t.id].phase)"
                            type="button" class="small !min-h-12" @click="c.refreshStatus(t.id)">{{ TEXT.status }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else-if="c.releasedRevisionId.value" class="empty">{{ TEXT.empty }}</p>
        <p v-if="c.targets.value.some((t) => c.downloadUrl(t.id))" class="hint" style="margin-top: 14px">{{ TEXT.downloadHint }}</p>
      </section>
    </div>
  </template>
</section>`,
};

export function registerOutputView(app) {
    app.component('clinic-output-view', OutputView);
    return app;
}
