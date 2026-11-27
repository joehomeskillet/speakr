/**
 * Waveform, current phrase and uncertainty display for the correction view
 * (PAR-COR-003).
 *
 * Draws the stored waveform peaks of the selected audio revision (existing
 * loader and level picker of modules/asr-editor/timeline-peaks.js; peaks are
 * fetched from /audio/<recordingId>/peaks) and marks three things apart, each
 * with a text label and a non-colour cue: the phrase being played, stored
 * recognition evidence, and text without an audio anchor. Evidence is bound to
 * the current audio and transcript revisions. Older snapshots may explicitly flag a text/audio link.
 * Peaks are tied to the audio revision they were loaded for and are shown
 * as unavailable when the editor's audio revision moves on. Clinical text stays
 * in memory only. Colours come from tokens.js, requests from api-client.js.
 *
 * Exports (consumed by w212 compose, w286 compare):
 *   useCorrectionWaveform({ editor, alignment, client, dictationId, recordingId,
 *                           seek?, playRange?, fetchFn?, sleep?, vue? })
 *     `editor` is the useCorrectionEditor() result (w149), `alignment` the
 *     useCorrectionAlignment() result (w150). `recordingId` addresses the peaks.
 *     state refs  peaks (parsed peaks or null), status ('idle'|'loading'|'ready'|
 *                 'stale'|'none'|'denied'|'error'), error, currentPhrase,
 *                 uncertainSpans, unalignedSpans, uncertaintyKnown, bars,
 *                 announcement
 *     methods     load(), seekToSpan(span), dispose()
 *     `currentPhrase` is { id, start, end, text, startMs, endMs } or null;
 *     `uncertainSpans` entries carry the recognition kind, label text and audio times;
 *     `unalignedSpans` entries are { start, end, text }; `bars` are
 *     { x, y1, y2 } columns (0..1000 by -1..1) of the picked level.
 *     load() returns { ok, code?, message } with codes denied, none, failed.
 *   CorrectionWaveform: Vue options object, props editor, alignment, client,
 *     dictationId, recordingId, fetchFn, sleep, vue.
 *   registerCorrectionWaveform(app): app.component('clinic-correction-waveform', ...).
 *
 * Keyboard: each timed mark is a button (Tab, Enter, Space) that plays its
 * passage. Unknown evidence without a time range is static. Retry is a button.
 */

import { createClinicApiClient } from './api-client.js';
import { levelFor, loadPeaks } from '../modules/asr-editor/timeline-peaks.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Wellenform',
    waveform: 'Wellenform des gewählten Audios',
    currentPhrase: 'Aktuelle Phrase',
    noCurrent: 'Keine Phrase aktiv.',
    recognitionMarks: 'Erkennungsmarken',
    uncertain: 'Unsicher erkannt',
    command: 'Befehl erkannt',
    unrecognized: 'Nicht erkannt',
    recognition: 'Erkannt',
    unknown: 'Erkennungsdaten unbekannt.',
    unknownConfidence: 'Sicherheit unbekannt',
    playPassage: 'Stelle abspielen',
    playingPassage: 'Wiedergabe gestartet.',
    passageUnavailable: 'Diese Stelle ist nicht abspielbar.',
    marksChanged: 'Die Markierungen wurden aktualisiert. Bitte wählen Sie erneut.',
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
const EMPTY_EVIDENCE = { textRevisionId: null, audioRevisionId: null, spans: null, flags: new Set() };
const MARK_CUES = Object.freeze({
    uncertain: { glyph: '≈', dash: '6 3' },
    command: { glyph: '◇', dash: null },
    unrecognized: { glyph: '⋯', dash: '1 3' },
    recognition: { glyph: '✓', dash: '10 3 1 3' },
    unknown: { glyph: '?' },
});
const secondsLabel = new Intl.NumberFormat('de', { maximumFractionDigits: 3 });

function hasRange(span) {
    return Number.isSafeInteger(span.startMs) && Number.isSafeInteger(span.endMs)
        && span.startMs >= 0 && span.startMs < span.endMs;
}

function sameMark(left, right) {
    return left.id === right.id && left.kind === right.kind && left.runId === right.runId
        && left.source === right.source && left.startMs === right.startMs && left.endMs === right.endMs
        && left.confidence === right.confidence;
}

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
export function barsFor(peaks, columns = WIDTH) {
    if (!peaks || !peaks.levels.length || !(peaks.duration > 0)) return [];
    const pps = columns / peaks.duration;
    const level = peaks.levels[levelFor(peaks.levels, pps)];
    const step = Math.max(1, Math.ceil(level.length / columns));
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
    const playbackMessage = refs.ref('');
    const meta = refs.shallow(EMPTY_EVIDENCE);

    let seq = 0;
    let activation = 0;
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

    const currentEvidence = ({ textRevisionId, audioRevisionId }) => Boolean(textRevisionId)
        && textRevisionId === editor.textRevisionId.value && editor.audio.value.status === 'ready'
        && (audioRevisionId ? audioRevisionId === editor.audio.value.revisionId : !opts.playRange);
    const uncertainSpans = refs.computed(() => {
        const { audioRevisionId, spans, flags } = meta.value;
        if (!currentEvidence(meta.value)) return [];
        if (spans !== null) return audioRevisionId ? spans : [];
        if (!flags.size) return [];
        return alignment.links.value.filter((l) => flags.has(l.id)).map(spanOf);
    });
    const uncertaintyKnown = refs.computed(() => uncertainSpans.value.length > 0);

    const unalignedSpans = refs.computed(() => alignment.segments.value
        .filter((s) => s.flagged)
        .map((s) => ({ start: s.start, end: s.end, text: s.text })));

    async function readEvidence() {
        const res = await client.request(base + '/correction');
        if (res.outcome !== 'ok' || !res.data) return EMPTY_EVIDENCE;
        const data = res.data;
        const evidence = { ...EMPTY_EVIDENCE, textRevisionId: data.text_revision_id, audioRevisionId: data.audio_revision_id };
        const sourced = Object.hasOwn(data, 'recognition_evidence');
        const rows = sourced ? (data.recognition_evidence ?? []) : data.recognition_spans;
        if (sourced || Array.isArray(rows)) {
            evidence.spans = [];
            for (const row of Array.isArray(rows) ? rows : []) {
                if (!row || typeof row.id !== 'string' || !Object.hasOwn(MARK_CUES, row.kind) || row.kind === 'unknown') continue;
                if (sourced ? (row.text_revision_id !== evidence.textRevisionId || row.audio_revision_id !== evidence.audioRevisionId
                    || typeof row.source !== 'string' || !row.source.trim() || typeof row.run_id !== 'string' || !row.run_id.trim())
                    : (row.kind !== 'uncertain' && row.kind !== 'unrecognized')) continue;
                const { id, kind, start_ms: startMs, end_ms: endMs, run_id: runId, source, confidence = null } = row;
                if (!hasRange({ startMs, endMs })) continue;
                const label = kind === 'recognition' && confidence === null
                    ? `${LABELS[kind]} · ${LABELS.unknownConfidence}` : LABELS[kind];
                const from = secondsLabel.format(startMs / 1000);
                const to = secondsLabel.format(endMs / 1000);
                evidence.spans.push({ id, kind, startMs, endMs, runId, source, confidence, text: `${label} (${from}–${to} s)` });
            }
        } else if (Array.isArray(data.links)) {
            evidence.flags = new Set(data.links.filter((link) => link?.uncertain === true && typeof link.id === 'string').map((link) => link.id));
        }
        return evidence;
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
        meta.value = EMPTY_EVIDENCE;
        playbackMessage.value = '';
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
            readEvidence().then((evidence) => { if (mine === seq && !disposed) meta.value = evidence; }),
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

    /** Play the saved passage on the shared player, or preserve standalone legacy seeking. */
    function seekToSpan(span) {
        const current = uncertainSpans.value.find((item) => item.kind ? item === span : span && sameMark(item, span));
        if (!current || !hasRange(current)) return { ok: false };
        if (opts.playRange) return playMarkedSpan(current);
        if (span?.kind) {
            if (typeof opts.seek !== 'function') return { ok: false };
            opts.seek(span.startMs / 1000);
            return { ok: true };
        }
        const segment = alignment.segments.value.find((s) => s.linkId === span.id);
        return alignment.seekToSegment(segment);
    }

    async function playMarkedSpan(span) {
        const mine = ++activation;
        const initial = meta.value;
        playbackMessage.value = '';
        if (span.runId) {
            const latest = await readEvidence();
            if (disposed || mine !== activation || initial !== meta.value || !currentEvidence(initial)) return { ok: false };
            if (!currentEvidence(latest) || !latest.spans?.some((item) => sameMark(item, span))) {
                meta.value = latest;
                playbackMessage.value = latest === EMPTY_EVIDENCE ? LABELS.passageUnavailable : LABELS.marksChanged;
                return { ok: false };
            }
        }
        const ok = await opts.playRange(span.startMs / 1000, span.endMs / 1000);
        if (!disposed && mine === activation && initial === meta.value && currentEvidence(initial)) {
            playbackMessage.value = ok ? LABELS.playingPassage : LABELS.passageUnavailable;
        }
        return { ok };
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        if (controller) controller.abort();
        loaded.value = { peaks: null, audioRevisionId: null };
        meta.value = EMPTY_EVIDENCE;
    }

    return {
        peaks, status, error, currentPhrase, uncertainSpans, unalignedSpans, uncertaintyKnown, bars, announcement,
        load, seekToSpan, dispose, playbackMessage,
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
        seek: { type: Function, default: null },
        playRange: { type: Function, default: null },
        fetchFn: { type: Function, default: null },
        sleep: { type: Function, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const wave = useCorrectionWaveform({
            editor: props.editor, alignment: props.alignment, client: props.client || undefined,
            dictationId: props.dictationId, recordingId: props.recordingId || undefined,
            seek: props.seek || undefined, playRange: props.playRange || undefined,
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
            if (!(total > 0) || !span || span.startMs >= total) return null;
            const x = Math.max(0, Math.min(WIDTH, (span.startMs / total) * WIDTH));
            const w = Math.min(WIDTH - x, ((span.endMs - span.startMs) / total) * WIDTH);
            return { x, w };
        }
        return { ...wave, LABELS, WIDTH, MARK_CUES, surfaces, focusStyle, boxStyle, rect };
    },
    template: `
<section class="clinic-correction-waveform" :aria-label="LABELS.title" :aria-busy="status === 'loading'" :data-status="status" style="display: grid; gap: 8px">
  <h3 style="margin: 0">{{ LABELS.title }}</h3>
  <div v-if="status === 'error' || status === 'denied'" role="alert" :style="boxStyle(surfaces.problem)">
    {{ error && error.message }}
    <button type="button" class="small" :style="focusStyle" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <div v-else-if="status === 'stale'" role="alert" :style="boxStyle(surfaces.uncertain)">
    {{ LABELS.stale }}
    <button type="button" :style="focusStyle" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <p v-else-if="status === 'none'" role="status" class="hint" style="margin: 0">{{ LABELS.none }}</p>
  <svg v-if="status === 'ready'" role="img" :aria-label="LABELS.waveform" :viewBox="'0 -1 ' + WIDTH + ' 2'" preserveAspectRatio="none" width="100%" height="64" style="color: var(--v8-muted); background: var(--v8-s1); border-radius: 8px">
    <line v-for="bar in bars" :key="bar.x" :x1="bar.x" :x2="bar.x" :y1="bar.y1" :y2="bar.y2" stroke="currentColor" stroke-width="1" vector-effect="non-scaling-stroke" />
    <template v-for="span in uncertainSpans" :key="'u' + span.id">
      <rect v-if="rect(span)" :x="rect(span).x" y="-1" :width="rect(span).w" height="2" fill="none"
        stroke="var(--v8-text)" :stroke-dasharray="MARK_CUES[span.kind || 'uncertain'].dash" stroke-width="2"
        vector-effect="non-scaling-stroke" :data-kind="span.kind || 'uncertain'" />
    </template>
    <rect v-if="currentPhrase && rect(currentPhrase)" :x="rect(currentPhrase).x" y="-1" :width="rect(currentPhrase).w" height="2"
      :fill="surfaces.current.background" fill-opacity="0.6" :stroke="surfaces.current.border" stroke-width="2" vector-effect="non-scaling-stroke" data-kind="current" />
  </svg>
  <p class="clinic-correction-waveform-current hint" style="margin: 0" :aria-current="currentPhrase ? 'true' : null"
    :style="currentPhrase ? boxStyle(surfaces.current) : {}">
    <strong>{{ LABELS.currentPhrase }}:</strong> {{ currentPhrase ? currentPhrase.text : LABELS.noCurrent }}
  </p>
  <div class="clinic-correction-waveform-uncertain hint">
    <strong>{{ LABELS.recognitionMarks }}:</strong>{{ ' ' }}
    <span v-if="!uncertainSpans.length && status !== 'loading'" data-kind="unknown"><span aria-hidden="true">{{ MARK_CUES.unknown.glyph }}</span> {{ LABELS.unknown }}</span>
    <ul v-if="uncertainSpans.length" class="chiprow" style="list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px">
      <li v-for="span in uncertainSpans" :key="span.id" style="min-width: 0; max-width: 100%">
        <button type="button" class="button" :style="focusStyle" style="min-height: 48px; min-width: 48px; max-width: 100%; white-space: normal; text-align: start"
          :data-kind="span.kind || 'uncertain'" :aria-label="LABELS.playPassage + ': ' + span.text" @click="seekToSpan(span)">
          <span aria-hidden="true">{{ MARK_CUES[span.kind || 'uncertain'].glyph }}</span> {{ span.text }}
        </button>
      </li>
    </ul>
    <p role="status" style="margin: 4px 0 0" v-show="playbackMessage">{{ playbackMessage }}</p>
  </div>
  <div class="clinic-correction-waveform-unaligned hint">
    <strong>{{ LABELS.unaligned }}:</strong>{{ ' ' }}
    <span v-if="!unalignedSpans.length">{{ LABELS.noUnaligned }}</span>
    <ul v-else>
      <li v-for="span in unalignedSpans" :key="span.start">{{ span.text }}</li>
    </ul>
  </div>
  <div role="status" aria-live="polite" class="sr-only">{{ announcement }}</div>
</section>`,
};

export function registerCorrectionWaveform(app) {
    app.component('clinic-correction-waveform', CorrectionWaveform);
    return app;
}
