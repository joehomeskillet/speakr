/**
 * Text/audio alignment for the correction view (PAR-COR-002, PAR-COR-009).
 *
 * Reads the stored text/audio links of one dictation (offsets are UTF-16 code
 * units, as the browser counts them) and maps text positions to audio times and
 * back. W10: words typed after the last save carry no audio anchor and are shown
 * as unaligned; a save lets the server derive new links, then `refresh()` loads
 * them. Links are stale (visibly unavailable) when the text revision or the audio
 * revision they were read at is no longer the editor's. Clinical text stays in
 * memory only; nothing is written to browser storage. Colours come from
 * tokens.js, requests from the injected api-client.js client.
 *
 * Exports (consumed by w151 waveform, w153 modes, w155 markers, w212, w286):
 *   useCorrectionAlignment({ editor, client, dictationId, seek?, audioElement?, vue? })
 *     `editor` is the useCorrectionEditor() result of w149 (text, textRevisionId,
 *     audio are read from it). `seek(seconds)` or `audioElement.currentTime` moves
 *     the player.
 *     state refs  links, status ('idle'|'loading'|'ready'|'stale'|'error'),
 *                 error, selection { start, end }, currentWord, segments,
 *                 announcement
 *     methods     refresh(), setSelection(start, end), seekToSelection(),
 *                 seekToSegment(segment), highlightAt(timeSeconds), dispose()
 *     `currentWord` is { id, start, end } in editor-text offsets, or null.
 *     seekToSelection / seekToSegment return { ok, code?, message, seconds? };
 *     codes: stale, unaligned, noSeek, plus refresh codes denied, notFound, failed.
 *   CorrectionAlignment: Vue options object, props editor, client, dictationId,
 *     seek, audioElement, vue.
 *   registerCorrectionAlignment(app): app.component('clinic-correction-alignment', ...).
 *
 * Keyboard: aligned words are focusable (Tab) and seek with Enter or Space;
 * unaligned and unavailable text is plain text. Refresh and retry are buttons.
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Text und Audio',
    text: 'Transkript mit Audio-Zuordnung',
    seekSelection: 'Zur Auswahl im Audio springen',
    refresh: 'Zuordnung neu laden',
    retry: 'Erneut versuchen',
    loading: 'Zuordnung wird geladen …',
    ready: 'Zuordnung geladen.',
    stale: 'Die Audio-Zuordnung ist veraltet. Bitte neu laden.',
    unaligned: 'Ohne Audio-Zuordnung.',
    unalignedMark: 'ohne Audio',
    denied: 'Keine Berechtigung für die Audio-Zuordnung.',
    notFound: 'Das Diktat wurde nicht gefunden oder ist nicht zugänglich.',
    failed: 'Die Audio-Zuordnung konnte nicht geladen werden.',
    noSeek: 'Kein Audio-Player verbunden.',
    seeked: 'Audio an der gewählten Stelle.',
    wordAria: 'Wort abspielen',
});

const ZWJ = '‍';
const MARK = /^[\p{M}]/u;

function fail(code, extra) {
    return { ok: false, code, message: LABELS[code] || LABELS.failed, ...(extra || {}) };
}

function errorFor(res) {
    if (res.status === 404) return fail('notFound');
    if (res.outcome === 'permission') return fail('denied');
    if (res.outcome === 'conflict') return fail('stale');
    return fail('failed');
}

function isLow(code) { return code >= 0xDC00 && code <= 0xDFFF; }

// True when `index` sits inside an emoji / combining sequence, so cutting there would split it.
function joinsPrevious(text, index) {
    if (index <= 0 || index >= text.length) return false;
    const unit = text.charCodeAt(index);
    if (isLow(unit)) return true;
    const ch = String.fromCodePoint(text.codePointAt(index));
    if (ch === ZWJ || MARK.test(ch)) return true;
    if (ch.codePointAt(0) >= 0x1F3FB && ch.codePointAt(0) <= 0x1F3FF) return true;
    return text.charAt(index - 1) === ZWJ;
}

function snapDown(text, index) {
    let at = Math.max(0, Math.min(index, text.length));
    while (at > 0 && joinsPrevious(text, at)) at -= 1;
    return at;
}

function snapUp(text, index) {
    let at = Math.max(0, Math.min(index, text.length));
    while (at < text.length && joinsPrevious(text, at)) at += 1;
    return at;
}

// Common prefix/suffix between the text the links were read at and the editor text.
function changedRegion(base, cur) {
    let p = 0;
    const max = Math.min(base.length, cur.length);
    while (p < max && base.charCodeAt(p) === cur.charCodeAt(p)) p += 1;
    p = snapDown(cur, p);
    let s = 0;
    while (s < max - p && base.charCodeAt(base.length - 1 - s) === cur.charCodeAt(cur.length - 1 - s)) s += 1;
    while (s > 0 && joinsPrevious(cur, cur.length - s)) s -= 1;
    return { p, baseEnd: base.length - s, delta: cur.length - base.length };
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    const computed = V && V.computed ? V.computed : (fn) => ({ get value() { return fn(); } });
    return { ref, shallow, computed };
}

/** @param {{editor: object, client?: object, dictationId: string, seek?: Function, audioElement?: object, vue?: object}} options */
export function useCorrectionAlignment(options) {
    const opts = options || {};
    const editor = opts.editor;
    const client = opts.client || createClinicApiClient();
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(String(opts.dictationId));

    const raw = refs.shallow({ links: [], text: '', textRevisionId: null, audioRevisionId: null });
    const loadState = refs.ref('idle');
    const error = refs.shallow(null);
    const selection = refs.shallow({ start: 0, end: 0 });
    const current = refs.shallow(null);
    const announcement = refs.ref('');

    let seq = 0;
    let disposed = false;

    function say(message) { announcement.value = message; }

    const status = refs.computed(() => {
        if (loadState.value !== 'ready') return loadState.value;
        const snap = raw.value;
        const audioRev = editor.audio.value.revisionId;
        if (snap.textRevisionId !== editor.textRevisionId.value) return 'stale';
        if (audioRev && snap.audioRevisionId && audioRev !== snap.audioRevisionId) return 'stale';
        if (editor.audio.value.status === 'ready' && !snap.audioRevisionId) return 'stale';
        return 'ready';
    });

    // Links mapped into the editor text; links touching locally changed text are dropped.
    const links = refs.computed(() => {
        if (status.value !== 'ready') return [];
        const snap = raw.value;
        const cur = editor.text.value;
        const region = snap.text === cur ? null : changedRegion(snap.text, cur);
        const out = [];
        for (const link of snap.links) {
            let start = link.text_start;
            let end = link.text_end;
            if (region) {
                if (end <= region.p) { /* unchanged prefix */ } else if (start >= region.baseEnd) {
                    start += region.delta;
                    end += region.delta;
                } else {
                    continue;
                }
            }
            if (start < 0 || end > cur.length || end <= start) continue;
            out.push({ id: link.id, start, end, audioStartMs: link.audio_start_ms, audioEndMs: link.audio_end_ms });
        }
        return out.sort((a, b) => a.start - b.start);
    });

    // Text cut into aligned words and unaligned spans; whitespace-only gaps are not flagged.
    const segments = refs.computed(() => {
        const cur = editor.text.value;
        const available = status.value === 'ready';
        const out = [];
        let at = 0;
        const gap = (to) => {
            if (to <= at) return;
            const slice = cur.slice(at, to);
            out.push({ start: at, end: to, text: slice, linkId: null, aligned: false, unavailable: !available && slice.trim() !== '', flagged: available && slice.trim() !== '' });
        };
        for (const link of links.value) {
            if (link.start < at) continue;
            gap(link.start);
            out.push({ start: link.start, end: link.end, text: cur.slice(link.start, link.end), linkId: link.id, aligned: true, unavailable: false, flagged: false });
            at = link.end;
        }
        gap(cur.length);
        if (!available) {
            for (const seg of out) seg.unavailable = seg.text.trim() !== '';
        }
        return out;
    });

    const currentWord = refs.computed(() => {
        const hit = current.value;
        if (!hit || status.value !== 'ready') return null;
        const link = links.value.find((l) => l.id === hit);
        return link ? { id: link.id, start: link.start, end: link.end } : null;
    });

    /** Load links of the current text revision and the matching audio revision. */
    async function refresh() {
        if (disposed) return fail('failed');
        const mine = ++seq;
        loadState.value = 'loading';
        error.value = null;
        say(LABELS.loading);
        const [corr, aud] = await Promise.all([
            client.request(base + '/correction'),
            client.request(base + '/audio-revisions/current'),
        ]);
        if (mine !== seq || disposed || corr.outcome === 'cancelled') return { ok: false, code: 'cancelled', message: '' };
        const data = corr.data;
        if (corr.outcome !== 'ok' || !data || typeof data.text !== 'string' || !Array.isArray(data.links)) {
            const problem = corr.outcome === 'ok' ? fail('failed') : errorFor(corr);
            loadState.value = 'error';
            error.value = problem;
            raw.value = { links: [], text: '', textRevisionId: null, audioRevisionId: null };
            say(problem.message);
            return problem;
        }
        const audioRevisionId = aud.outcome === 'ok' && aud.data && typeof aud.data.revision_id === 'string'
            ? aud.data.revision_id : null;
        raw.value = {
            links: data.links.filter((l) => Number.isInteger(l.text_start) && Number.isInteger(l.text_end)
                && Number.isInteger(l.audio_start_ms) && Number.isInteger(l.audio_end_ms)),
            text: data.text,
            textRevisionId: data.text_revision_id,
            audioRevisionId,
        };
        loadState.value = 'ready';
        say(status.value === 'ready' ? LABELS.ready : LABELS.stale);
        return { ok: true };
    }

    /** Record the editor selection in UTF-16 offsets; it never splits an emoji or combining sequence. */
    function setSelection(start, end) {
        const text = editor.text.value;
        const a = snapDown(text, Math.min(start, end));
        const b = snapUp(text, Math.max(start, end));
        selection.value = { start: a, end: b };
        return selection.value;
    }

    function move(seconds) {
        if (typeof opts.seek === 'function') {
            opts.seek(seconds);
            return true;
        }
        if (opts.audioElement && typeof opts.audioElement === 'object') {
            opts.audioElement.currentTime = seconds;
            return true;
        }
        return false;
    }

    function seekToLink(link) {
        if (status.value !== 'ready') return fail(status.value === 'stale' ? 'stale' : 'failed');
        const seconds = link.audioStartMs / 1000;
        if (!move(seconds)) return fail('noSeek');
        current.value = link.id;
        say(LABELS.seeked);
        return { ok: true, seconds, linkId: link.id, audioRevisionId: raw.value.audioRevisionId, message: LABELS.seeked };
    }

    /** Seek to the first aligned word touching the selection. Unaligned text refuses. */
    function seekToSelection() {
        if (status.value !== 'ready') return fail(status.value === 'stale' ? 'stale' : 'failed');
        const { start, end } = selection.value;
        const hit = links.value.find((l) => (end > start ? l.start < end && l.end > start : l.start <= start && start < l.end));
        if (!hit) {
            say(LABELS.unaligned);
            return fail('unaligned');
        }
        return seekToLink(hit);
    }

    function seekToSegment(segment) {
        if (!segment || !segment.aligned) return fail('unaligned');
        const link = links.value.find((l) => l.id === segment.linkId);
        return link ? seekToLink(link) : fail('stale');
    }

    /** Word being played at `timeSeconds`; the latest start wins, then the shortest span. */
    function highlightAt(timeSeconds) {
        if (status.value !== 'ready' || typeof timeSeconds !== 'number' || !Number.isFinite(timeSeconds)) {
            current.value = null;
            return null;
        }
        const ms = Math.round(timeSeconds * 1000);
        let best = null;
        for (const link of links.value) {
            const inside = ms >= link.audioStartMs && (ms < link.audioEndMs || (link.audioStartMs === link.audioEndMs && ms === link.audioEndMs));
            if (!inside) continue;
            if (!best || link.audioStartMs > best.audioStartMs
                || (link.audioStartMs === best.audioStartMs && link.audioEndMs - link.audioStartMs < best.audioEndMs - best.audioStartMs)) {
                best = link;
            }
        }
        current.value = best ? best.id : null;
        return currentWord.value;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        current.value = null;
        raw.value = { links: [], text: '', textRevisionId: null, audioRevisionId: null };
    }

    return {
        links, status, error, selection, currentWord, segments, announcement,
        refresh, setSelection, seekToSelection, seekToSegment, highlightAt, dispose,
    };
}

/** Vue component. Props: editor (useCorrectionEditor result), client, dictationId, seek, audioElement, vue. */
export const CorrectionAlignment = {
    name: 'ClinicCorrectionAlignment',
    props: {
        editor: { type: Object, required: true },
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        seek: { type: Function, default: null },
        audioElement: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const align = useCorrectionAlignment({
            editor: props.editor, client: props.client || undefined, dictationId: props.dictationId,
            seek: props.seek || undefined, audioElement: props.audioElement || undefined, vue: props.vue || undefined,
        });
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { align.refresh(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(align.dispose);
        const surfaces = {
            current: surfaceFor('processing'), unaligned: surfaceFor('warning'), problem: surfaceFor('error'),
        };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        function styleFor(seg) {
            const word = align.currentWord.value;
            if (word && seg.linkId === word.id) {
                return { background: surfaces.current.background, color: surfaces.current.color, border: '1px solid ' + surfaces.current.border };
            }
            if (seg.flagged) {
                return { background: surfaces.unaligned.background, color: surfaces.unaligned.color };
            }
            return {};
        }
        function onSelect() {
            // Offsets come from the data-start of the segment holding each end of the selection.
            const sel = globalThis.getSelection ? globalThis.getSelection() : null;
            if (!sel || sel.rangeCount === 0 || !sel.anchorNode || !sel.focusNode) return false;
            const offsetOf = (node, offset) => {
                const el = node.nodeType === 1 ? node : node.parentElement;
                const host = el && el.closest ? el.closest('[data-start]') : null;
                return host ? Number(host.getAttribute('data-start')) + offset : null;
            };
            const a = offsetOf(sel.anchorNode, sel.anchorOffset);
            const b = offsetOf(sel.focusNode, sel.focusOffset);
            if (a === null || b === null) return false;
            align.setSelection(a, b);
            return true;
        }
        function onWord(seg) {
            const sel = globalThis.getSelection ? globalThis.getSelection() : null;
            if (sel && !sel.isCollapsed) return;
            align.seekToSegment(seg);
        }
        return { ...align, LABELS, surfaces, focusStyle, styleFor, onSelect, onWord };
    },
    template: `
<section class="clinic-correction-alignment" :aria-label="LABELS.title" :aria-busy="status === 'loading'" :data-status="status">
  <h3>{{ LABELS.title }}</h3>
  <div v-if="status === 'error'" role="alert"
    :style="{ background: surfaces.problem.background, color: surfaces.problem.color, border: '1px solid ' + surfaces.problem.border }">
    {{ error && error.message }}
    <button type="button" :style="focusStyle" @click="refresh()">{{ LABELS.retry }}</button>
  </div>
  <div v-else-if="status === 'stale'" role="alert"
    :style="{ background: surfaces.unaligned.background, color: surfaces.unaligned.color, border: '1px solid ' + surfaces.unaligned.border }">
    {{ LABELS.stale }}
    <button type="button" :style="focusStyle" @click="refresh()">{{ LABELS.refresh }}</button>
  </div>
  <p class="clinic-correction-alignment-text" role="group" :aria-label="LABELS.text"
    @mouseup="onSelect" @keyup="onSelect">
    <template v-for="seg in segments" :key="seg.start">
      <span v-if="seg.aligned" role="button" tabindex="0" :data-start="seg.start" :data-link="seg.linkId"
        :aria-label="LABELS.wordAria + ': ' + seg.text" :aria-current="currentWord && currentWord.id === seg.linkId ? 'true' : null"
        :style="{ ...focusStyle, ...styleFor(seg) }"
        @click="onWord(seg)" @keydown.enter.prevent="onWord(seg)" @keydown.space.prevent="onWord(seg)">{{ seg.text }}</span>
      <span v-else :data-start="seg.start" :data-unaligned="seg.flagged || seg.unavailable ? 'true' : null"
        :title="seg.flagged ? LABELS.unaligned : null" :style="styleFor(seg)">{{ seg.text }}</span>
    </template>
  </p>
  <button type="button" :style="focusStyle" :disabled="status !== 'ready'" @click="seekToSelection()">{{ LABELS.seekSelection }}</button>
  <div role="status" aria-live="polite">{{ announcement }}</div>
</section>`,
};

export function registerCorrectionAlignment(app) {
    app.component('clinic-correction-alignment', CorrectionAlignment);
    return app;
}
