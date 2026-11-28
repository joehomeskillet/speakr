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
 *                           audio?, seek?, playRange?, fetchFn?, sleep?, vue? })
 *     `editor` is the useCorrectionEditor() result (w149), `alignment` the
 *     useCorrectionAlignment() result (w150). `recordingId` addresses the peaks.
 *     state refs  peaks (parsed peaks or null), status ('idle'|'loading'|'ready'|
 *                 'stale'|'none'|'denied'|'error'), error, currentPhrase,
 *                 uncertainSpans, unalignedSpans, uncertaintyKnown, bars,
 *                 announcement, visible, zoom, viewWindow, positionMs, playheadX
 *     methods     load(), seekToSpan(span), bindAudio(el), setPositionMs(ms),
 *                 zoomIn(), zoomOut(), setZoom(z), resetZoom(),
 *                 setVisible(flag), toggleVisible(), dispose()
 *     `currentPhrase` is { id, start, end, text, startMs, endMs } or null;
 *     `uncertainSpans` entries carry the recognition kind, label text and audio times;
 *     `unalignedSpans` entries are { start, end, text }; `bars` are
 *     { x, y1, y2 } columns (0..1000 by -1..1) of the picked level.
 *     load() returns { ok, code?, message } with codes denied, none, failed.
 *   CorrectionWaveform: Vue options object, props editor, alignment, client,
 *     dictationId, recordingId, audio, seek, playRange, fetchFn, sleep, vue.
 *   registerCorrectionWaveform(app): app.component('clinic-correction-waveform', ...).
 *
 * Keyboard: toggle and zoom buttons are native buttons (Tab, Enter, Space)
 * that keep editor focus on pointer click; each timed mark is a button that
 * plays its passage. Unknown evidence without a time range is static. Retry
 * is a button.
 */

import { createClinicApiClient } from './api-client.js';
import { levelFor, loadPeaks } from '../modules/asr-editor/timeline-peaks.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const ZOOM_LEVELS = Object.freeze([1, 2, 4, 8, 16]);
export const MIN_ZOOM = ZOOM_LEVELS[0];
export const MAX_ZOOM = ZOOM_LEVELS[ZOOM_LEVELS.length - 1];

export const LABELS = Object.freeze({
    title: 'Wellenform',
    waveform: 'Wellenform des gewählten Audios',
    controls: 'Wellenform-Steuerung',
    hide: 'Wellenform ausblenden',
    show: 'Wellenform einblenden',
    hidden: 'Wellenform ausgeblendet.',
    shown: 'Wellenform eingeblendet.',
    zoomOut: 'Wellenform verkleinern',
    zoomOutShort: 'Verkleinern',
    zoomIn: 'Wellenform vergrössern',
    zoomInShort: 'Vergrössern',
    zoomReset: 'Gesamtansicht',
    position: 'Position',
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
const MEDIA_EVENTS = Object.freeze(['timeupdate', 'seeking', 'seeked', 'play', 'playing', 'pause', 'ended', 'durationchange', 'loadedmetadata', 'ratechange', 'emptied']);
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

function clamp(value, lo, hi) {
    return Math.max(lo, Math.min(hi, value));
}

function resolveAudio(candidate) {
    const raw = candidate && typeof candidate === 'object' && 'value' in candidate ? candidate.value : candidate;
    return raw && typeof raw === 'object' && ('currentTime' in raw || typeof raw.addEventListener === 'function') ? raw : null;
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    const computed = V && V.computed ? V.computed : (fn) => ({ get value() { return fn(); } });
    return { ref, shallow, computed };
}

// One min/max column per drawn bucket of the level that fits the width (and optional time window), at most WIDTH columns.
export function barsFor(peaks, columns = WIDTH, windowRange = null) {
    if (!peaks || !peaks.levels.length || !(peaks.duration > 0)) return [];
    if (!windowRange) {
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
    const startSec = clamp(Number(windowRange.startSec) || 0, 0, peaks.duration);
    const endSec = clamp(Number(windowRange.endSec) || peaks.duration, startSec, peaks.duration);
    const spanSec = endSec - startSec;
    if (!(spanSec > 0)) return [];
    const pps = columns / spanSec;
    const level = peaks.levels[levelFor(peaks.levels, pps)];
    const first = Math.max(0, Math.floor(startSec * level.rate));
    const last = Math.min(level.length, Math.ceil(endSec * level.rate));
    if (first >= last) return [];
    const step = Math.max(1, Math.ceil((last - first) / columns));
    const out = [];
    for (let i = first; i < last; i += step) {
        let lo = 0;
        let hi = 0;
        for (let j = i; j < Math.min(last, i + step); j++) {
            lo = Math.min(lo, level.data[j * 2]);
            hi = Math.max(hi, level.data[j * 2 + 1]);
        }
        const x = clamp(Math.round((((i / level.rate) - startSec) / spanSec) * WIDTH), 0, WIDTH);
        out.push({ x, y1: -hi / 127, y2: -lo / 127 });
    }
    return out;
}

/** @param {{editor: object, alignment: object, client?: object, dictationId: string, recordingId?: string, audio?: object, seek?: Function, playRange?: Function, fetchFn?: Function, sleep?: Function, vue?: object}} options */
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
    const visible = refs.ref(true);
    const zoom = refs.ref(MIN_ZOOM);
    const windowStartMs = refs.ref(0);
    const positionMs = refs.ref(null);

    let seq = 0;
    let activation = 0;
    let disposed = false;
    let controller = null;
    let boundAudio = null;
    let frameId = null;
    const audioListeners = [];

    function say(message) { announcement.value = message; }

    const status = refs.computed(() => {
        if (loadState.value !== 'ready') return loadState.value;
        const rev = editor.audio.value.revisionId;
        if (rev && loaded.value.audioRevisionId && rev !== loaded.value.audioRevisionId) return 'stale';
        return 'ready';
    });

    const peaks = refs.computed(() => (status.value === 'ready' ? loaded.value.peaks : null));

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

    function currentAnchorMs(durationMs) {
        if (positionMs.value !== null && Number.isFinite(positionMs.value)) {
            return clamp(positionMs.value, 0, durationMs);
        }
        const phrase = currentPhrase.value;
        if (phrase && Number.isFinite(phrase.startMs)) {
            return clamp(phrase.startMs, 0, durationMs);
        }
        return null;
    }

    function centeredWindowStart(anchor, durationMs, level) {
        const spanMs = durationMs / level;
        const maxStart = Math.max(0, durationMs - spanMs);
        return clamp((anchor ?? 0) - spanMs / 2, 0, maxStart);
    }

    function keepAnchorInView(anchorOverride) {
        const p = peaks.value;
        if (!p || !(p.duration > 0) || zoom.value <= MIN_ZOOM) {
            windowStartMs.value = 0;
            return;
        }
        const durationMs = p.duration * 1000;
        const spanMs = durationMs / zoom.value;
        const maxStart = Math.max(0, durationMs - spanMs);
        const start = clamp(windowStartMs.value, 0, maxStart);
        const anchor = anchorOverride !== undefined ? anchorOverride : currentAnchorMs(durationMs);
        if (anchor !== null && (anchor < start || anchor > start + spanMs)) {
            windowStartMs.value = centeredWindowStart(anchor, durationMs, zoom.value);
        } else if (start !== windowStartMs.value) {
            windowStartMs.value = start;
        }
    }

    const viewWindow = refs.computed(() => {
        const p = peaks.value;
        if (!p || !(p.duration > 0)) return { startMs: 0, endMs: 0, durationMs: 0 };
        const durationMs = p.duration * 1000;
        const spanMs = durationMs / zoom.value;
        if (zoom.value <= MIN_ZOOM || spanMs >= durationMs) {
            return { startMs: 0, endMs: durationMs, durationMs };
        }
        const maxStart = Math.max(0, durationMs - spanMs);
        let startMs = clamp(windowStartMs.value, 0, maxStart);
        const anchor = currentAnchorMs(durationMs);
        if (anchor !== null && (anchor < startMs || anchor > startMs + spanMs)) {
            startMs = clamp(anchor - spanMs / 2, 0, maxStart);
        }
        return { startMs, endMs: Math.min(durationMs, startMs + spanMs), durationMs };
    });

    const playheadX = refs.computed(() => {
        const pos = positionMs.value;
        const { startMs, endMs } = viewWindow.value;
        const spanMs = endMs - startMs;
        if (pos === null || !(spanMs > 0) || pos < startMs || pos > endMs) return null;
        return ((pos - startMs) / spanMs) * WIDTH;
    });

    const bars = refs.computed(() => {
        const p = peaks.value;
        if (!p) return [];
        if (zoom.value <= MIN_ZOOM) return barsFor(p);
        const { startMs, endMs } = viewWindow.value;
        return barsFor(p, WIDTH, { startSec: startMs / 1000, endSec: endMs / 1000 });
    });

    function setPositionMs(ms) {
        if (disposed) return;
        const next = Number.isFinite(ms) ? Math.max(0, Math.round(ms)) : null;
        if (next !== positionMs.value) positionMs.value = next;
        keepAnchorInView(next);
    }

    function stopFrame() {
        if (frameId !== null && typeof globalThis.cancelAnimationFrame === 'function') {
            globalThis.cancelAnimationFrame(frameId);
        }
        frameId = null;
    }

    function syncFromAudio() {
        if (!boundAudio || disposed) return;
        const t = boundAudio.currentTime;
        setPositionMs(Number.isFinite(t) ? t * 1000 : 0);
    }

    function tickFrame() {
        frameId = null;
        if (disposed || !boundAudio || boundAudio.paused || boundAudio.ended) return;
        syncFromAudio();
        if (typeof globalThis.requestAnimationFrame === 'function') {
            frameId = globalThis.requestAnimationFrame(tickFrame);
        }
    }

    function startFrame() {
        if (disposed || !boundAudio || frameId !== null || boundAudio.paused || boundAudio.ended) return;
        if (typeof globalThis.requestAnimationFrame === 'function') {
            frameId = globalThis.requestAnimationFrame(tickFrame);
        }
    }

    function unbindAudio() {
        stopFrame();
        for (const [node, name, fn] of audioListeners.splice(0)) {
            if (typeof node.removeEventListener === 'function') node.removeEventListener(name, fn);
        }
        boundAudio = null;
    }

    function bindAudio(candidate) {
        const el = resolveAudio(candidate);
        if (disposed || el === boundAudio) return;
        unbindAudio();
        boundAudio = el;
        if (!boundAudio) {
            positionMs.value = null;
            return;
        }
        const listen = (name, fn) => {
            if (typeof boundAudio.addEventListener !== 'function') return;
            boundAudio.addEventListener(name, fn);
            audioListeners.push([boundAudio, name, fn]);
        };
        for (const name of MEDIA_EVENTS) {
            if (name === 'play' || name === 'playing') {
                listen(name, () => { syncFromAudio(); startFrame(); });
            } else if (name === 'pause' || name === 'ended' || name === 'emptied') {
                listen(name, () => { stopFrame(); syncFromAudio(); });
            } else {
                listen(name, syncFromAudio);
            }
        }
        syncFromAudio();
        startFrame();
    }

    if (opts.audio) bindAudio(opts.audio);

    function setZoom(nextZoom) {
        if (disposed) return { ok: false, zoom: zoom.value };
        const idx = ZOOM_LEVELS.indexOf(nextZoom);
        const target = idx >= 0 ? nextZoom : clamp(Number(nextZoom) || MIN_ZOOM, MIN_ZOOM, MAX_ZOOM);
        if (target === zoom.value) return { ok: false, zoom: zoom.value };
        zoom.value = target;
        const p = peaks.value;
        if (p && p.duration > 0) {
            const durationMs = p.duration * 1000;
            windowStartMs.value = target <= MIN_ZOOM ? 0 : centeredWindowStart(currentAnchorMs(durationMs), durationMs, target);
        } else {
            windowStartMs.value = 0;
        }
        say(target <= MIN_ZOOM ? `${LABELS.zoomReset}.` : `${LABELS.title} ${target}×.`);
        return { ok: true, zoom: target };
    }

    function zoomIn() {
        const idx = ZOOM_LEVELS.indexOf(zoom.value);
        const next = idx >= 0 ? ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, idx + 1)]
            : (ZOOM_LEVELS.find((z) => z > zoom.value) ?? MAX_ZOOM);
        return setZoom(next);
    }

    function zoomOut() {
        const idx = ZOOM_LEVELS.indexOf(zoom.value);
        const next = idx >= 0 ? ZOOM_LEVELS[Math.max(0, idx - 1)]
            : ([...ZOOM_LEVELS].reverse().find((z) => z < zoom.value) ?? MIN_ZOOM);
        return setZoom(next);
    }

    function resetZoom() {
        return setZoom(MIN_ZOOM);
    }

    function setVisible(next) {
        if (disposed) return visible.value;
        const flag = Boolean(next);
        if (flag === visible.value) return visible.value;
        visible.value = flag;
        if (flag) {
            syncFromAudio();
            keepAnchorInView();
        }
        say(flag ? LABELS.shown : LABELS.hidden);
        return flag;
    }

    function toggleVisible() {
        return setVisible(!visible.value);
    }

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
        const previousRevision = loaded.value.audioRevisionId;
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
        if (previousRevision && revisionId !== previousRevision) {
            zoom.value = MIN_ZOOM;
            windowStartMs.value = 0;
        }
        loaded.value = { peaks: parsed, audioRevisionId: revisionId };
        loadState.value = 'ready';
        keepAnchorInView();
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
        unbindAudio();
        positionMs.value = null;
        loaded.value = { peaks: null, audioRevisionId: null };
        meta.value = EMPTY_EVIDENCE;
    }

    return {
        peaks, status, error, currentPhrase, uncertainSpans, unalignedSpans, uncertaintyKnown, bars, announcement,
        visible, zoom, viewWindow, positionMs, playheadX,
        load, seekToSpan, bindAudio, setPositionMs, zoomIn, zoomOut, setZoom, resetZoom, setVisible, toggleVisible,
        dispose, playbackMessage,
    };
}

/** Vue component. Props: editor, alignment, client, dictationId, recordingId, audio, seek, playRange, fetchFn, sleep, vue. */
export const CorrectionWaveform = {
    name: 'ClinicCorrectionWaveform',
    props: {
        editor: { type: Object, required: true },
        alignment: { type: Object, required: true },
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        recordingId: { type: String, default: null },
        audio: { type: Object, default: null },
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
            audio: props.audio || undefined,
            seek: props.seek || undefined, playRange: props.playRange || undefined,
            fetchFn: props.fetchFn || undefined, sleep: props.sleep || undefined, vue: props.vue || undefined,
        });
        if (V && typeof V.watch === 'function') {
            V.watch(() => props.audio, (next) => { wave.bindAudio(next); });
        }
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { wave.load(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(wave.dispose);
        const surfaces = {
            current: surfaceFor('processing'), uncertain: surfaceFor('warning'), problem: surfaceFor('error'),
        };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        const disabledStyle = { background: 'var(--v8-s1)', color: 'var(--v8-muted)', opacity: '0.7', cursor: 'default' };
        const boxStyle = (s) => ({ background: s.background, color: s.color, border: '1px solid ' + s.border });
        // Range clipped to the active zoom window; null when the span lies outside the window.
        function rect(span) {
            const { startMs, endMs } = wave.viewWindow.value;
            const spanMs = endMs - startMs;
            if (!(spanMs > 0) || !span || !hasRange(span)) return null;
            if (span.endMs <= startMs || span.startMs >= endMs) return null;
            const clippedStart = Math.max(startMs, span.startMs);
            const clippedEnd = Math.min(endMs, span.endMs);
            const x = ((clippedStart - startMs) / spanMs) * WIDTH;
            const w = ((clippedEnd - clippedStart) / spanMs) * WIDTH;
            return w > 0 ? { x, w } : null;
        }
        const windowLabel = () => {
            const { startMs, endMs } = wave.viewWindow.value;
            return `${secondsLabel.format(startMs / 1000)} bis ${secondsLabel.format(endMs / 1000)} s`;
        };
        const positionLabel = () => (wave.positionMs.value === null ? '' : `${secondsLabel.format(wave.positionMs.value / 1000)} s`);
        return {
            ...wave, LABELS, WIDTH, MIN_ZOOM, MAX_ZOOM, MARK_CUES, surfaces, focusStyle, disabledStyle,
            boxStyle, rect, windowLabel, positionLabel,
        };
    },
    template: `
<section class="clinic-correction-waveform" :aria-label="LABELS.title" :aria-busy="status === 'loading'" :data-status="status" :data-zoom="zoom" :data-wave-visible="String(visible)" style="display: grid; gap: 8px">
  <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px">
    <h3 style="margin: 0">{{ LABELS.title }}</h3>
    <div v-if="status === 'ready'" role="group" :aria-label="LABELS.controls" style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; max-width: 100%">
      <button type="button" class="button" :style="focusStyle" style="min-height: 48px; min-width: 48px"
        data-testid="waveform-toggle" :aria-expanded="String(visible)" @mousedown.prevent @click="toggleVisible()">
        {{ visible ? LABELS.hide : LABELS.show }}
      </button>
      <template v-if="visible">
        <button type="button" class="button" :style="{ ...focusStyle, ...(zoom <= MIN_ZOOM ? disabledStyle : {}) }" style="min-height: 48px; min-width: 48px"
          data-testid="waveform-zoom-out" :aria-label="LABELS.zoomOut" :aria-disabled="zoom <= MIN_ZOOM ? 'true' : null" @mousedown.prevent @click="zoomOut()">
          <span aria-hidden="true">−</span> {{ LABELS.zoomOutShort }}
        </button>
        <span class="hint" data-testid="waveform-zoom-level" style="min-width: 3ch; text-align: center; font-variant-numeric: tabular-nums">{{ zoom }}×</span>
        <button type="button" class="button" :style="{ ...focusStyle, ...(zoom >= MAX_ZOOM ? disabledStyle : {}) }" style="min-height: 48px; min-width: 48px"
          data-testid="waveform-zoom-in" :aria-label="LABELS.zoomIn" :aria-disabled="zoom >= MAX_ZOOM ? 'true' : null" @mousedown.prevent @click="zoomIn()">
          <span aria-hidden="true">+</span> {{ LABELS.zoomInShort }}
        </button>
        <button type="button" class="button" :style="{ ...focusStyle, ...(zoom <= MIN_ZOOM ? disabledStyle : {}) }" style="min-height: 48px; min-width: 48px"
          data-testid="waveform-zoom-reset" :aria-disabled="zoom <= MIN_ZOOM ? 'true' : null" @mousedown.prevent @click="resetZoom()">
          {{ LABELS.zoomReset }}
        </button>
      </template>
    </div>
  </div>
  <div v-if="status === 'error' || status === 'denied'" role="alert" :style="boxStyle(surfaces.problem)">
    {{ error && error.message }}
    <button type="button" class="small" :style="focusStyle" style="min-height: 48px; min-width: 48px" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <div v-else-if="status === 'stale'" role="alert" :style="boxStyle(surfaces.uncertain)">
    {{ LABELS.stale }}
    <button type="button" :style="focusStyle" style="min-height: 48px; min-width: 48px" @click="load()">{{ LABELS.retry }}</button>
  </div>
  <p v-else-if="status === 'none'" role="status" class="hint" style="margin: 0">{{ LABELS.none }}</p>
  <template v-if="status === 'ready'">
    <svg v-if="visible" role="img" :aria-label="LABELS.waveform" :viewBox="'0 -1 ' + WIDTH + ' 2'" preserveAspectRatio="none" width="100%" height="64" style="display: block; color: var(--v8-muted); background: var(--v8-s1); border-radius: 8px">
      <line v-for="(bar, idx) in bars" :key="idx" :x1="bar.x" :x2="bar.x" :y1="bar.y1" :y2="bar.y2" stroke="currentColor" stroke-width="1" vector-effect="non-scaling-stroke" />
      <template v-for="span in uncertainSpans" :key="'u' + span.id">
        <rect v-if="rect(span)" :x="rect(span).x" y="-1" :width="rect(span).w" height="2" fill="none"
          stroke="var(--v8-text)" :stroke-dasharray="MARK_CUES[span.kind || 'uncertain'].dash" stroke-width="2"
          vector-effect="non-scaling-stroke" :data-kind="span.kind || 'uncertain'" />
      </template>
      <rect v-if="currentPhrase && rect(currentPhrase)" :x="rect(currentPhrase).x" y="-1" :width="rect(currentPhrase).w" height="2"
        :fill="surfaces.current.background" fill-opacity="0.6" :stroke="surfaces.current.border" stroke-width="2" vector-effect="non-scaling-stroke" data-kind="current" />
      <line v-if="playheadX !== null" data-kind="playhead" :x1="playheadX" :x2="playheadX" y1="-1" y2="1"
        stroke="var(--brand-accent, var(--v8-text))" stroke-width="2" vector-effect="non-scaling-stroke" />
    </svg>
    <p class="hint" data-testid="waveform-window-info" style="margin: 0">
      <template v-if="!visible">{{ LABELS.hidden }}<template v-if="positionLabel()"> · {{ LABELS.position }}: {{ positionLabel() }}</template></template>
      <template v-else>{{ zoom }}× ({{ windowLabel() }})<template v-if="positionLabel()"> · {{ LABELS.position }}: {{ positionLabel() }}</template></template>
    </p>
  </template>
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
