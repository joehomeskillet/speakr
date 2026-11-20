/**
 * Composed clinic output view (V8 P16 Ausgabe: PAR-EXP-001, PAR-EXP-002).
 *
 * Lists the output targets of one dictation, requests the export of the
 * released transcript revision and shows the result per target. Clipboard
 * copy is the existing clipboard-ui module; nothing of it is re-implemented.
 *
 * Contract (package v8-14, src/clinic/routes_output.py):
 *   GET  /dictations/{id}/output-targets  -> { released_revision_id, targets: [{ id, channel, available, reason }] }
 *   POST /dictations/{id}/exports         body { revision_id, format }, Idempotency-Key -> job { id, status, format, revision_id, filename? }
 *   GET  /dictations/{id}/exports/{job}   -> job
 * Only the released revision is offered for export. One Idempotency-Key is kept
 * per (revision, format), so a repeated click or a retry after an unknown result
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
import { ClipboardCopy, createClipboardCopy } from './clipboard-ui.js';

export const COMPOSE_OUTPUT_VERSION = 1;

export const TEXT = Object.freeze({
    title: 'Ausgabe',
    loading: 'Laden …',
    loadFailed: 'Die Ausgabeziele konnten nicht geladen werden.',
    reload: 'Erneut laden',
    revision: 'Ausgegebene Version',
    notReleased: 'Keine freigegebene Version. Eine Ausgabe ist erst nach der Freigabe möglich.',
    targets: 'Ausgabeziele',
    export: 'Ausgabe starten',
    status: 'Status prüfen',
    unavailable: 'nicht verfügbar',
    clipboard: 'Zwischenablage',
    offline: 'Die Verbindung ist fehlgeschlagen. Das Ergebnis ist unbekannt; ein erneuter Klick sendet dieselbe Anfrage.',
});

export const TARGET_LABELS = Object.freeze({
    txt: 'Textdatei (TXT)',
    docx: 'Word-Dokument (DOCX)',
    kis: 'KIS',
    clipboard: 'Zwischenablage',
});

export const REASONS = Object.freeze({
    not_released: 'Keine freigegebene Version.',
    kis_unavailable: 'nicht verfügbar',
});

export const JOB_STATUS = Object.freeze({
    ready: { label: 'Datei erzeugt (noch nicht gespeichert)', surface: 'success' },
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
 * Framework-neutral controller. Options: client, dictationId, vue (optional),
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
    const keys = new Map(); // revision|format -> Idempotency-Key
    const inflight = new Set();
    let clipboard = null;

    const surfaceOf = (name) => surfaceFor(name);
    const setResult = (format, status, label, surface, job) => {
        results.value = { ...results.value, [format]: { phase: status, label, surface: surfaceOf(surface), job: job || null } };
    };

    async function load() {
        phase.value = 'loading';
        const res = await client.request(base + '/output-targets');
        if (res.outcome !== 'ok' || !res.data) {
            phase.value = 'failed';
            return false;
        }
        releasedRevisionId.value = res.data.released_revision_id || null;
        targets.value = (res.data.targets || []).map((t) => targetView(t, releasedRevisionId.value));
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
        return Boolean(t) && t.channel !== 'clipboard' && t.available && !inflight.has(format);
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
        refreshStatus, clipboardCopy, text: TEXT,
    };
}

export const OutputView = {
    name: 'ClinicOutputView',
    components: { ClipboardCopy },
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const c = createOutputViewController({ client: props.client, dictationId: props.dictationId, vue: props.vue || undefined });
        c.load();
        const style = (s) => (s ? { background: s.background, color: s.color, borderColor: s.border } : null);
        return { c, TEXT, style };
    },
    template: `
<section aria-labelledby="out-title" data-testid="output-view">
  <h2 id="out-title">{{ TEXT.title }}</h2>
  <p v-if="c.phase.value === 'loading'" role="status" aria-live="polite">{{ TEXT.loading }}</p>
  <div v-else-if="c.phase.value === 'failed'" role="alert">
    <p>{{ TEXT.loadFailed }}</p>
    <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" @click="c.load()">{{ TEXT.reload }}</button>
  </div>
  <template v-else>
    <p v-if="c.releasedRevisionId.value" data-testid="revision">{{ TEXT.revision }}: {{ c.releasedRevisionId.value }}</p>
    <p v-else data-testid="not-released">{{ TEXT.notReleased }}</p>
    <ul :aria-label="TEXT.targets">
      <li v-for="t in c.targets.value.filter((x) => x.channel !== 'clipboard')" :key="t.id" :data-target="t.id">
        <span>{{ t.label }}</span>
        <button type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem"
                :disabled="!c.canExport(t.id)" @click="c.exportTo(t.id)">{{ TEXT.export }}</button>
        <span v-if="!t.available" data-testid="unavailable">{{ t.reason }}</span>
        <span v-if="c.results.value[t.id]" role="status" aria-live="polite" :data-phase="c.results.value[t.id].phase"
              :style="style(c.results.value[t.id].surface)">{{ c.results.value[t.id].label }}</span>
        <button v-if="c.results.value[t.id] && c.results.value[t.id].job && ['pending', 'unknown'].includes(c.results.value[t.id].phase)"
                type="button" class="px-3 py-2 border rounded" style="min-height: 2.75rem" @click="c.refreshStatus(t.id)">{{ TEXT.status }}</button>
      </li>
    </ul>
    <ClipboardCopy v-if="c.releasedRevisionId.value" :client="client" :dictation-id="dictationId" :revision-id="c.releasedRevisionId.value" />
  </template>
</section>`,
};

export function registerOutputView(app) {
    app.component('clinic-output-view', OutputView);
    return app;
}
