/**
 * Waveform, current phrase and uncertainty display for the correction view
 * (PAR-COR-003).
 *
 * Draws the stored waveform peaks of the selected audio revision (existing
 * loader and level picker of modules/asr-editor/timeline-peaks.js; peaks are
 * fetched from /audio/<recordingId>/peaks) and marks three things apart, each
 * with a text label and a non-colour cue: the phrase being played, text the
 * recognizer flagged as uncertain, and text without an audio anchor. Uncertainty
 * comes only from real server metadata: a text/audio link counts as uncertain
 * when the link itself carries `uncertain: true`. No confidence value is ever
 * computed or guessed here; without such metadata the list stays empty and says
 * so. Peaks are tied to the audio revision they were loaded for and are shown
 * as unavailable when the editor's audio revision moves on. Clinical text stays
 * in memory only. Colours come from tokens.js, requests from api-client.js.
 *
 * Exports (consumed by w212 compose, w286 compare):
 *   useCorrectionWaveform({ editor, alignment, client, dictationId, recordingId,
 *                           fetchFn?, sleep?, vue? })
 *     `editor` is the useCorrectionEditor() result (w149), `alignment` the
 *     useCorrectionAlignment() result (w150). `recordingId` addresses the peaks.
 *     state refs  peaks (parsed peaks or null), status ('idle'|'loading'|'ready'|
 *                 'stale'|'none'|'denied'|'error'), error, currentPhrase,
 *                 uncertainSpans, unalignedSpans, uncertaintyKnown, bars,
 *                 announcement
 *     methods     load(), seekToSpan(span), dispose()
 *     `currentPhrase` is { id, start, end, text, startMs, endMs } or null;
 *     `uncertainSpans` entries are { id, start, end, text, startMs, endMs };
 *     `unalignedSpans` entries are { start, end, text }; `bars` are
 *     { x, y1, y2 } columns (0..1000 by -1..1) of the picked level.
 *     load() returns { ok, code?, message } with codes denied, none, failed.
 *   CorrectionWaveform: Vue options object, props editor, alignment, client,
 *     dictationId, recordingId, fetchFn, sleep, vue.
 *   registerCorrectionWaveform(app): app.component('clinic-correction-waveform', ...).
 *
 * Keyboard: each uncertain span is a button (Tab, Enter, Space) that moves the
 * player to the span through the alignment seek. Retry is a button.
 */

import { createClinicApiClient } from './api-client.js';
import { levelFor, loadPeaks } from '../modules/asr-editor/timeline-peaks.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Wellenform',
    waveform: 'Wellenform des gewählten Audios',
    currentPhrase: 'Aktuelle Phrase',
    noCurrent: 'Keine Phrase aktiv.',
    uncertain: 'Unsicher erkannt',
    noUncertain: 'Keine Unsicherheitsdaten vorhanden.',
    uncertainAria: 'Unsicher erkannte Stelle abspielen',
    unaligned: 'Ohne Audio-Zuordnung',
    noUnaligned: 'Alle Wörter haben eine Audio-Zuordnung.',
    unavailable: 'Zuordnung nicht verfügbar.',
    retry: 'Erneut versuchen',
    loading: 'Wellenform wird geladen …',
    ready: 'Wellenform geladen.',
    stale: 'Die Wellenform gehört zu einem früheren Audiostand. Bitte neu laden.',
    none: 'Keine Wellenform verfügbar.',
    denied: 'Keine Berechtigung für die Wellenform.',
    failed: 'Die Wellenform konnte nicht geladen werden.',
    compute: 'Wellenform konnte nicht berechnet werden.',
    pending: 'Die Wellenform wird berechnet …',
});

const WIDTH = 1000;

function fail(code) {
    return { ok: false, code, message: LABELS[code] || LABELS.failed };
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    const computed = V && V.computed ? V.computed : (fn) => ({ get value() { return fn(); } });
    return { ref, shallow, computed };
}

// One min/max column per drawn bucket of the level that fits the width, at most WIDTH columns.
function barsFor(peaks) {
    if (!peaks || !peaks.levels.length || !(peaks.duration > 0)) return [];
    const pps = WIDTH / peaks.duration;
    const level = peaks.levels[levelFor(peaks.levels, pps)];
    const step = Math.max(1, Math.ceil(level.length / WIDTH));
    const out = [];
    for (let i = 0; i < level.length; i += step) {
        let lo = 0;
        let hi = 0;
        for (let j = i; j < Math.min(level.length, i + step); j++) {
            lo = Math.min(lo, level.data[j * 2]);
            hi = Math.max(hi, level.data[j * 2 + 1]);
        }
        out.push({ x: Math.round((i / level.length) * WIDTH), y1: -hi / 127, y2: -lo / 127 });
    }
    return out;
}

/** @param {{editor: object, alignment: object, client?: object, dictationId: string, recordingId?: string, fetchFn?: Function, sleep?: Function, vue?: object}} options */
export function useCorrectionWaveform(options) {
    const opts = options || {};
    const editor = opts.editor;
    const alignment = opts.alignment;
    const client = opts.client || createClinicApiClient();
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(String(opts.dictationId));

    const loaded = refs.shallow({ peaks: null, audioRevisionId: null });
    const loadState = refs.ref('idle');
    const error = refs.shallow(null);
    const announcement = refs.ref('');
    // Raw link metadata (uncertain flag) of the same text revision the alignment shows.
    const meta = refs.shallow({ textRevisionId: null, flags: new Map() });

    let seq = 0;
    let disposed = false;
    let controller = null;

    function say(message) { announcement.value = message; }

    const status = refs.computed(() => {
        if (loadState.value !== 'ready') return loadState.value;
        const rev = editor.audio.value.revisionId;
        if (rev && loaded.value.audioRevisionId && rev !== loaded.value.audioRevisionId) return 'stale';
        return 'ready';
    });

    const peaks = refs.computed(() => (status.value === 'ready' ? loaded.value.peaks : null));
    const bars = refs.computed(() => barsFor(peaks.value));

    const spanOf = (link) => {
        const text = editor.text.value;
        return {
            id: link.id, start: link.start, end: link.end, text: text.slice(link.start, link.end),
            startMs: link.audioStartMs, endMs: link.audioEndMs,
        };
    };

    const currentPhrase = refs.computed(() => {
        const word = alignment.currentWord.value;
        if (!word) return null;
        const link = alignment.links.value.find((l) => l.id === word.id);
        return link ? spanOf(link) : null;
    });

    // Only links the server flagged, read at the text revision still shown.
    const uncertaintyKnown = refs.computed(() => meta.value.flags.size > 0
        && meta.value.textRevisionId === editor.textRevisionId.value);
    const uncertainSpans = refs.computed(() => {
        if (!uncertaintyKnown.value) return [];
        return alignment.links.value.filter((l) => meta.value.flags.get(l.id) === true).map(spanOf);
    });

    const unalignedSpans = refs.computed(() => alignment.segments.value
        .filter((s) => s.flagged)
        .map((s) => ({ start: s.start, end: s.end, text: s.text })));

    // Link ids and the server's uncertain flag; read once per load, kept apart from alignment internals.
    async function readFlags(mine) {
        const res = await client.request(base + '/correction');
        if (mine !== seq || disposed) return;
        const data = res.outcome === 'ok' ? res.data : null;
        const flags = new Map();
        if (data && Array.isArray(data.links)) {
            for (const l of data.links) if (l && l.uncertain === true && typeof l.id === 'string') flags.set(l.id, true);
        }
        meta.value = { textRevisionId: data ? data.text_revision_id : null, flags };
    }

    /** Load the peaks of the recording for the editor's current audio revision. */
    async function load() {
        if (disposed) return fail('failed');
        if (!opts.recordingId) {
            loadState.value = 'none';
            say(LABELS.none);
            return fail('none');
        }
        const mine = ++seq;
        if (controller) controller.abort();
        controller = typeof AbortController === 'function' ? new AbortController() : null;
        const revisionId = editor.audio.value.revisionId;
        loadState.value = 'loading';
        error.value = null;
        say(LABELS.loading);
        let last = 0;
        const get = opts.fetchFn || ((url, init) => fetch(url, init));
        const [parsed] = await Promise.all([
            loadPeaks(opts.recordingId, {
                signal: controller ? controller.signal : null,
                sleep: opts.sleep || null,
                onPending: () => say(LABELS.pending),
                fetchFn: async (url, init) => { const r = await get(url, init); last = r.peaksFailed ? 'compute' : r.status; return r; },
            }),
            readFlags(mine),
        ]);
        if (mine !== seq || disposed) return { ok: false, code: 'cancelled', message: '' };
        if (!parsed) {
            const code = last === 401 || last === 403 ? 'denied' : (last === 404 ? 'none' : (last === 'compute' ? 'compute' : 'failed'));
            const problem = fail(code);
            loadState.value = code === 'failed' || code === 'compute' ? 'error' : code;
            error.value = problem;
            loaded.value = { peaks: null, audioRevisionId: null };
            say(problem.message);
            return problem;
        }
        loaded.value = { peaks: parsed, audioRevisionId: revisionId };
        loadState.value = 'ready';
        say(status.value === 'ready' ? LABELS.ready : LABELS.stale);
        return { ok: true };
    }

    /** Move the player to an uncertain span through the alignment seek. */
    function seekToSpan(span) {
        const segment = alignment.segments.value.find((s) => s.linkId === (span && span.id));
        return alignment.seekToSegment(segment);
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        if (controller) controller.abort();
        loaded.value = { peaks: null, audioRevisionId: null };
        meta.value = { textRevisionId: null, flags: new Map() };
    }

    return {
        peaks, status, error, currentPhrase, uncertainSpans, unalignedSpans, uncertaintyKnown, bars, announcement,
        load, seekToSpan, dispose,
    };
}

/** Vue component. Props: editor, alignment, client, dictationId, recordingId, fetchFn, sleep, vue. */
export const CorrectionWaveform = {
    name: 'ClinicCorrectionWaveform',
    props: {
        editor: { type: Object, required: true },
        alignment: { type: Object, required: true },
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        recordingId: { type: String, default: null },
        fetchFn: { type: Function, default: null },
        sleep: { type: Function, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const wave = useCorrectionWaveform({
            editor: props.editor, alignment: props.alignment, client: props.client || undefined,
            dictationId: props.dictationId, recordingId: props.recordingId || undefined,
            fetchFn: props.fetchFn || undefined, sleep: props.sleep || undefined, vue: props.vue || undefined,
        });
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { wave.load(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(wave.dispose);
        const surfaces = {
            current: surfaceFor('processing'), uncertain: surfaceFor('warning'), problem: surfaceFor('error'),
        };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        const boxStyle = (s) => ({ background: s.background, color: s.color, border: '1px solid ' + s.border });
        const duration = () => (wave.peaks.value ? wave.peaks.value.duration * 1000 : 0);
        // Range as a share of the drawn width; empty when the span lies outside the peaks.
        function rect(span) {
            const total = duration();
            if (!(total > 0) || !span) return null;
            const x = Math.max(0, Math.min(WIDTH, (span.startMs / total) * WIDTH));
            const w = Math.max(4, Math.min(WIDTH - x, ((span.endMs - span.startMs) / total) * WIDTH));
            return { x, w };
        }
        return { ...wave, LABELS, WIDTH, surfaces, focusStyle, boxStyle, rect };
    },
    template: `
<section class="clinic-correction-waveform" :aria-label="LABELS.title" :aria-busy="status === 'loading'" :data-status="status">
  <h3>{{ LABELS.title }}</h3>
  <div v-if="status === 'error' || status === 'denied'" role="alert" :style="boxStyle(surfaces.problem)">
    {{ error && error.message }}
    <button type="button" :style="focusStyle" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <div v-else-if="status === 'stale'" role="alert" :style="boxStyle(surfaces.uncertain)">
    {{ LABELS.stale }}
    <button type="button" :style="focusStyle" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <p v-else-if="status === 'none'" role="status">{{ LABELS.none }}</p>
  <svg v-if="status === 'ready'" role="img" :aria-label="LABELS.waveform" :viewBox="'0 -1 ' + WIDTH + ' 2'" preserveAspectRatio="none" width="100%" height="64">
    <line v-for="bar in bars" :key="bar.x" :x1="bar.x" :x2="bar.x" :y1="bar.y1" :y2="bar.y2" stroke="currentColor" stroke-width="1" vector-effect="non-scaling-stroke" />
    <template v-for="span in uncertainSpans" :key="'u' + span.id">
      <rect v-if="rect(span)" :x="rect(span).x" y="-1" :width="rect(span).w" height="2" fill="none"
        :stroke="surfaces.uncertain.border" stroke-dasharray="6 3" stroke-width="2" vector-effect="non-scaling-stroke" data-kind="uncertain" />
    </template>
    <rect v-if="currentPhrase && rect(currentPhrase)" :x="rect(currentPhrase).x" y="-1" :width="rect(currentPhrase).w" height="2"
      :fill="surfaces.current.background" fill-opacity="0.6" :stroke="surfaces.current.border" stroke-width="2" vector-effect="non-scaling-stroke" data-kind="current" />
  </svg>
  <p class="clinic-correction-waveform-current" :aria-current="currentPhrase ? 'true' : null"
    :style="currentPhrase ? boxStyle(surfaces.current) : {}">
    <strong>{{ LABELS.currentPhrase }}:</strong> {{ currentPhrase ? currentPhrase.text : LABELS.noCurrent }}
  </p>
  <div class="clinic-correction-waveform-uncertain">
    <strong>{{ LABELS.uncertain }}:</strong>
    <span v-if="!uncertainSpans.length">{{ LABELS.noUncertain }}</span>
    <ul v-else>
      <li v-for="span in uncertainSpans" :key="span.id">
        <button type="button" :style="{ ...focusStyle, ...boxStyle(surfaces.uncertain) }"
          :aria-label="LABELS.uncertainAria + ': ' + span.text" @click="seekToSpan(span)">{{ span.text }}</button>
      </li>
    </ul>
  </div>
  <div class="clinic-correction-waveform-unaligned">
    <strong>{{ LABELS.unaligned }}:</strong>
    <span v-if="!unalignedSpans.length">{{ LABELS.noUnaligned }}</span>
    <ul v-else>
      <li v-for="span in unalignedSpans" :key="span.start">{{ span.text }}</li>
    </ul>
  </div>
  <div role="status" aria-live="polite">{{ announcement }}</div>
</section>`,
};

export function registerCorrectionWaveform(app) {
    app.component('clinic-correction-waveform', CorrectionWaveform);
    return app;
}
