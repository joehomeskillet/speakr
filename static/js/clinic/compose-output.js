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
    subline: 'Wählen Sie die Ziele für die freigegebene Version.',
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
    start: 'Starten',
    exportSelected: 'Ausgewählte ausgeben',
    saveDefaults: 'Standardziele speichern',
    savingDefaults: 'Standardziele werden gespeichert …',
    savedDefaults: 'Standardziele gespeichert.',
    defaultsLoadFailed: 'Die Standardziele konnten nicht geladen werden. Bitte laden Sie die Seite neu.',
    defaultsSaveFailed: 'Die Standardziele konnten nicht gespeichert werden.',
    defaultsChanged: 'Die Standardziele wurden bereits geändert. Bitte laden Sie die Seite neu.',
    kisUnavailable: 'Ausgabe ans KIS nicht verfügbar.',
    download: 'Laden',
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
    const selected = ref([]);
    const preferences = ref(null);
    const preferenceNotice = ref('');
    const savingDefaults = ref(false);
    const exportingSelected = ref(false);
    const keys = new Map(); // revision|format -> Idempotency-Key
    const inflight = new Set();
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
        results.value = {};
        for (const target of offered) {
            if (target.channel === 'file' && target.available && target.status && target.job_id) {
                applyJob(target.id, { id: target.job_id, kind: 'file', status: target.status });
            }
        }
        if (releasedRevisionId.value && targets.value.some((t) => t.channel === 'file') && !find('kis')) {
            targets.value.push(targetView({ id: 'kis', channel: 'kis', available: false }, releasedRevisionId.value));
        }
        const defaults = await client.request(OUTPUT_PREFERENCES);
        preferences.value = defaults.outcome === 'ok' && Array.isArray(defaults.data?.formats) ? defaults.data : null;
        selected.value = selectableTargets().filter((t) => preferences.value?.formats.includes(preferenceId(t.id))).map((t) => t.id);
        preferenceNotice.value = preferences.value ? '' : TEXT.defaultsLoadFailed;
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
                    setResult(id, 'unavailable', id === 'kis' ? TEXT.kisUnavailable : target?.reason || TEXT.unavailable, 'warning');
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
        } catch {
            setResult(format, 'network', FAILURES.network.label, FAILURES.network.surface);
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
        selected, toggleTarget, preferences, preferenceNotice, savingDefaults, saveDefaults, exportingSelected, exportSelected,
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
    <button type="button" @click="c.load()">{{ TEXT.reload }}</button>
  </div>
  <template v-else>
    <div class="grid">
      <section class="panel" aria-labelledby="out-targets" style="min-width: 0">
        <h2 id="out-targets">{{ TEXT.targets }}</h2>
        <p v-if="c.releasedRevisionId.value" class="text-status" style="overflow-wrap: anywhere" data-testid="revision">{{ TEXT.revision }}: {{ c.releasedRevisionId.value }}</p>
        <p v-else class="note warning" data-testid="not-released">{{ TEXT.notReleased }}</p>
        <div v-if="c.releasedRevisionId.value && c.targets.value.some((t) => t.channel === 'file')" class="actions">
          <button type="button" class="primary" style="min-height: 48px; padding-inline: var(--sp-3)" :disabled="!c.selected.value.length || c.exportingSelected.value" @click="c.exportSelected()">{{ TEXT.exportSelected }}</button>
          <button type="button" class="quiet" style="min-height: 48px; padding-inline: var(--sp-3)" :disabled="!c.preferences.value || c.savingDefaults.value" @click="c.saveDefaults()">{{ TEXT.saveDefaults }}</button>
        </div>
        <p v-if="c.releasedRevisionId.value" class="text-status" role="status">{{ c.preferenceNotice.value }}</p>
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
                </td>
                <td>
                  <span v-if="!t.available" class="tag wait" style="white-space: normal" data-testid="unavailable">{{ t.reason }}</span>
                  <span v-if="c.results.value[t.id]" class="tag dot" :class="tagClass(c.results.value[t.id])" role="status" aria-live="polite"
                        style="white-space: normal" :data-phase="c.results.value[t.id].phase">{{ c.results.value[t.id].label }}</span>
                </td>
                <td>
                  <div class="actions">
                    <a v-if="c.downloadUrl(t.id)" :href="c.downloadUrl(t.id)" download class="button small" style="min-height: 48px; overflow-wrap: anywhere"
                       @click="c.markDownloadStarted(t.id)">{{ TEXT.download }}</a>
                    <button v-else type="button" class="small" style="min-height: 48px" :aria-label="TEXT.export" :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ TEXT.start }}</button>
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
