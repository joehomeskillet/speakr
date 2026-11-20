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
 *                 alignmentState ('loading'|'pending'|'succeeded'|'partial'|'failed'|
 *                 'stale'|'unaligned'|'invalid'), activeGranularity ('word'|'segment'|null),
 *                 error, selection { start, end }, currentWord, segments,
 *                 announcement
 *     Contract 2.0: with a valid binding (alignment-binding.js) the cues are words;
 *     otherwise stored links are a named "Segment" fallback (granularity 'segment').
 *     After a confirmed save, refresh() requests the alignment once per binding and
 *     follows a pending job with bounded backoff.
 *     methods     refresh(), setSelection(start, end), seekToSelection(),
 *                 seekToSegment(segment), highlightAt(timeSeconds), cueAt(ms),
 *                 granularityOf(id), indexBuilds(), dispose()
 *     The cue index (sorted by audio start, prefix maximum of the ends) is built
 *     once per binding (links, text, text revision, audio revision); highlightAt
 *     and cueAt are a binary search on it. A stale or foreign binding has no cues.
 *     `currentWord` is { id, start, end } in editor-text offsets, or null.
 *     seekToSelection / seekToSegment return { ok, code?, message, seconds? };
 *     codes: stale, unaligned, noSeek, plus refresh codes denied, notFound, failed.
 *   CorrectionAlignment: Vue options object, props editor, client, dictationId,
 *     seek, audioElement, vue.
 *   registerCorrectionAlignment(app): app.component('clinic-correction-alignment', ...).
 *   createPlaybackHighlighter({ alignment, audio, root?, scroller?, requestFrame?, cancelFrame?, onChange? })
 *     Drives the highlight from the real media position (V8 UI8-006): a single
 *     requestAnimationFrame loop while playing, immediate updates on pause,
 *     seeking, seeked, ratechange, ended and source changes. Per change only the
 *     previous and the new element are touched (data-active). It never moves
 *     focus, selection or caret and never writes editor state. Follow-scroll
 *     stops after a manual scroll and while an editable element has focus.
 *
 * Keyboard: aligned words are focusable (Tab) and seek with Enter or Space;
 * unaligned and unavailable text is plain text. Refresh and retry are buttons.
 */

import { bindAlignment, projectDocument } from './alignment-binding.js';
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
    notAligned: 'nicht ausgerichtet',
    segmentHighlight: 'Segment-Markierung (grob, keine Wortzeit)',
    alignPending: 'Wortzuordnung wird berechnet …',
    alignSucceeded: 'Wortzuordnung aktiv.',
    alignPartial: 'Wortzuordnung teilweise: nicht zugeordnete Wörter bleiben unmarkiert.',
    alignUnaligned: 'Keine Wortzeiten vorhanden. Markierung nur als Segment.',
    alignStale: 'Wortzuordnung veraltet.',
    alignInvalid: 'Wortzuordnung ungültig, nicht aktiviert.',
});

// Poll and retry delays in ms (1, 2, 4, 8, 16, 30 s); the last one repeats up to MAX_ATTEMPTS.
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const MAX_ATTEMPTS = 10;
const ALIGN_ANNOUNCE = {
    pending: 'alignPending', succeeded: 'alignSucceeded', partial: 'alignPartial',
    failed: 'alignUnaligned', unaligned: 'alignUnaligned', stale: 'alignStale', invalid: 'alignInvalid',
};
const EMPTY_RAW = Object.freeze({ cues: [], text: '', textRevisionId: null, audioRevisionId: null });

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    const hex = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    return hex(8) + '-' + hex(4) + '-4' + hex(3) + '-8' + hex(3) + '-' + hex(12);
}

// A links payload is usable only when every link is well formed; one bad link voids the fallback.
function linkCues(list) {
    if (!Array.isArray(list)) return null;
    const out = [];
    for (const l of list) {
        if (l === null || typeof l !== 'object') return null;
        if (!Number.isInteger(l.text_start) || !Number.isInteger(l.text_end)
            || !Number.isInteger(l.audio_start_ms) || !Number.isInteger(l.audio_end_ms)) return null;
        if (l.text_start < 0 || l.text_end < l.text_start || l.audio_start_ms < 0 || l.audio_end_ms < l.audio_start_ms) {
            return null;
        }
        out.push({
            id: l.id, textStart: l.text_start, textEnd: l.text_end,
            audioStartMs: l.audio_start_ms, audioEndMs: l.audio_end_ms,
            granularity: l.granularity === 'segment' || l.granularity === 'word' ? l.granularity : null,
        });
    }
    return out;
}

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

// True when the changed span of the current text abuts the word without whitespace between.
function touchesText(cur, region, side) {
    const changedEnd = region.baseEnd + region.delta;
    const ch = side === 'after' ? cur.charAt(region.p) : cur.charAt(changedEnd - 1);
    return ch !== '' && /\S/u.test(ch);
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

    const raw = refs.shallow(EMPTY_RAW);
    const loadState = refs.ref('idle');
    const error = refs.shallow(null);
    const selection = refs.shallow({ start: 0, end: 0 });
    const current = refs.shallow(null);
    const announcement = refs.ref('');
    const alignState = refs.ref('unaligned');

    let seq = 0;
    let disposed = false;
    let timer = null;
    // Binding keys already requested (one POST per binding); the key stays until a definite answer.
    const requested = new Set();
    const keys = new Map();
    const setTimer = opts.setTimeout || ((fn, ms) => globalThis.setTimeout(fn, ms));
    const clearTimer = opts.clearTimeout || ((id) => globalThis.clearTimeout(id));

    function say(message) { announcement.value = message; }

    // One calm message per state change; a silent reset does not count as announced.
    let announced = null;
    function setAlign(state, announce) {
        alignState.value = state;
        if (announce === false) return;
        if (announced !== state && ALIGN_ANNOUNCE[state]) say(LABELS[ALIGN_ANNOUNCE[state]]);
        announced = state;
    }

    function stopTimer() {
        if (timer !== null) { clearTimer(timer); timer = null; }
    }

    const status = refs.computed(() => {
        if (loadState.value !== 'ready') return loadState.value;
        const snap = raw.value;
        const audioRev = editor.audio.value.revisionId;
        if (snap.textRevisionId !== editor.textRevisionId.value) return 'stale';
        if (audioRev && snap.audioRevisionId && audioRev !== snap.audioRevisionId) return 'stale';
        if (editor.audio.value.status === 'ready' && !snap.audioRevisionId) return 'stale';
        return 'ready';
    });

    const alignmentState = refs.computed(() => {
        if (loadState.value === 'loading') return 'loading';
        if (loadState.value !== 'ready') return 'unaligned';
        return status.value === 'stale' ? 'stale' : alignState.value;
    });

    // Cues mapped into the editor text; cues touching locally changed text are dropped.
    const links = refs.computed(() => {
        if (status.value !== 'ready') return [];
        const snap = raw.value;
        const cur = editor.text.value;
        const region = snap.text === cur ? null : changedRegion(snap.text, cur);
        const out = [];
        for (const link of snap.cues) {
            let start = link.textStart;
            let end = link.textEnd;
            if (region) {
                if (end <= region.p) {
                    // Unchanged prefix; a word the edit has grown into is no longer that word.
                    if (link.granularity === 'word' && end === region.p && touchesText(cur, region, 'after')) continue;
                } else if (start >= region.baseEnd) {
                    if (link.granularity === 'word' && start === region.baseEnd && touchesText(cur, region, 'before')) continue;
                    start += region.delta;
                    end += region.delta;
                } else {
                    continue;
                }
            }
            if (start < 0 || end > cur.length || end <= start) continue;
            out.push({
                id: link.id, start, end, audioStartMs: link.audioStartMs, audioEndMs: link.audioEndMs,
                granularity: link.granularity,
            });
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

    // Cue index, rebuilt only when the binding changes (links, text, text revision, audio revision).
    let memo = null;
    let builds = 0;
    function index() {
        const ready = status.value === 'ready';
        const key = ready
            ? [raw.value, editor.text.value, editor.textRevisionId.value, editor.audio.value.revisionId]
            : null;
        if (memo && memo.ready === ready && (!ready || memo.key.every((part, i) => part === key[i]))) return memo;
        builds += 1;
        const cues = ready ? links.value.slice() : [];
        cues.sort((a, b) => (a.audioStartMs - b.audioStartMs)
            || ((b.audioEndMs - b.audioStartMs) - (a.audioEndMs - a.audioStartMs)));
        const maxEnd = new Array(cues.length);
        const byId = new Map();
        let top = -Infinity;
        for (let i = 0; i < cues.length; i += 1) {
            top = Math.max(top, cues[i].audioEndMs);
            maxEnd[i] = top;
            byId.set(cues[i].id, cues[i]);
        }
        memo = { ready, key, cues, maxEnd, byId };
        return memo;
    }

    /** Cue playing at `ms`: latest start wins, then the shortest span; silence gives null. O(log n). */
    function cueAt(ms) {
        const idx = index();
        const { cues, maxEnd } = idx;
        if (typeof ms !== 'number' || !Number.isFinite(ms) || cues.length === 0) return null;
        let lo = 0;
        let hi = cues.length - 1;
        let at = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (cues[mid].audioStartMs <= ms) { at = mid; lo = mid + 1; } else { hi = mid - 1; }
        }
        for (let i = at; i >= 0 && maxEnd[i] >= ms; i -= 1) {
            const cue = cues[i];
            if (ms < cue.audioEndMs || (cue.audioStartMs === cue.audioEndMs && ms === cue.audioEndMs)) return cue;
        }
        return null;
    }

    const currentWord = refs.computed(() => {
        const hit = current.value;
        if (!hit || status.value !== 'ready') return null;
        const link = index().byId.get(hit);
        return link ? { id: link.id, start: link.start, end: link.end } : null;
    });

    const activeGranularity = refs.computed(() => (current.value === null ? null : granularityOf(current.value)));

    /** 'segment' when the link spans more than one word: its highlight is coarse and named so. */
    function granularityOf(id) {
        const link = index().byId.get(id);
        if (!link) return null;
        if (link.granularity) return link.granularity;
        return /\s/u.test(editor.text.value.slice(link.start, link.end).trim()) ? 'segment' : 'word';
    }

    const canEdit = () => Boolean(editor.canEdit && editor.canEdit.value === true);

    // Validate the token document and the alignment payload; sets the state, returns the binding.
    function evaluate(ctx, document, alignment) {
        const proj = projectDocument(document && document.canonical_document);
        const hash = document && typeof document.document_hash === 'string' ? document.document_hash : null;
        if (!proj.ok || hash === null || proj.text !== ctx.text) {
            setAlign('invalid');
            return null;
        }
        if (alignment && alignment.schema_version === '2.0' && alignment.state === 'stale') {
            setAlign('stale');
            return null;
        }
        const bound = bindAlignment(alignment, {
            transcriptRevisionId: ctx.textRevisionId, audioRevisionId: ctx.audioRevisionId, documentHash: hash,
        }, proj);
        setAlign(bound.state);
        return bound.active ? bound : null;
    }

    function applyBinding(ctx, bound) {
        const cues = bound
            ? bound.cues.map((c) => ({
                id: c.wordId, textStart: c.start, textEnd: c.end,
                audioStartMs: c.startMs, audioEndMs: c.endMs, granularity: 'word',
            }))
            : ctx.fallback;
        raw.value = { cues, text: ctx.text, textRevisionId: ctx.textRevisionId, audioRevisionId: ctx.audioRevisionId };
    }

    const alignPath = (ctx) => base + '/alignment?audio_revision_id=' + encodeURIComponent(ctx.audioRevisionId)
        + '&transcript_revision_id=' + encodeURIComponent(ctx.textRevisionId);

    function bindingKey(ctx) { return ctx.textRevisionId + '|' + ctx.audioRevisionId + '|' + ctx.documentHash; }

    // Read the alignment of this binding; returns { kind: 'ok'|'missing'|'failed', data }.
    async function readAlignment(ctx) {
        const res = await client.request(alignPath(ctx));
        if (res.outcome === 'ok' && res.data && typeof res.data === 'object') return { kind: 'ok', data: res.data };
        if (res.status === 404 && (!res.code || res.code === 'ALIGNMENT_NOT_FOUND')) return { kind: 'missing', data: null };
        return { kind: 'failed', data: null };
    }

    // Follow a pending alignment: poll the operation (when known) or the alignment itself.
    function schedulePoll(ctx, mine, operationId, attempt) {
        if (attempt >= MAX_ATTEMPTS || disposed || mine !== seq) return;
        stopTimer();
        timer = setTimer(async () => {
            timer = null;
            if (disposed || mine !== seq || status.value !== 'ready') return;
            if (operationId) {
                const op = await client.request(base + '/operations/' + encodeURIComponent(operationId));
                if (disposed || mine !== seq) return;
                if (op.outcome === 'ok' && op.data && op.data.state === 'pending') {
                    schedulePoll(ctx, mine, operationId, attempt + 1);
                    return;
                }
            }
            const got = await readAlignment(ctx);
            if (disposed || mine !== seq) return;
            if (got.kind !== 'ok') {
                if (got.kind === 'missing') setAlign('unaligned');
                else schedulePoll(ctx, mine, operationId, attempt + 1);
                return;
            }
            const bound = evaluate(ctx, ctx.document, got.data);
            applyBinding(ctx, bound);
            if (alignState.value === 'pending') schedulePoll(ctx, mine, operationId, attempt + 1);
        }, BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
    }

    // POST /alignment once per binding. The Idempotency-Key is kept until a definite answer.
    async function requestAlignment(ctx, mine, attempt) {
        const bk = bindingKey(ctx);
        if (requested.has(bk)) return;
        if (!keys.has(bk)) keys.set(bk, (opts.newKey || newKey)());
        const res = await client.request(base + '/alignment', {
            method: 'POST',
            idempotencyKey: keys.get(bk),
            body: {
                schema_version: '2.0', audio_revision_id: ctx.audioRevisionId,
                transcript_revision_id: ctx.textRevisionId, document_hash: ctx.documentHash,
            },
        });
        if (disposed || mine !== seq || res.outcome === 'cancelled') return;
        if (res.outcome === 'ok') {
            requested.add(bk);
            keys.delete(bk);
            const op = res.data && typeof res.data === 'object' ? res.data : {};
            if (op.state === 'failed') { setAlign('failed'); return; }
            setAlign('pending');
            if (op.state === 'pending') schedulePoll(ctx, mine, typeof op.operation_id === 'string' ? op.operation_id : null, 0);
            else schedulePoll(ctx, mine, null, 0);
            return;
        }
        if (res.status === 409) {
            requested.add(bk);
            keys.delete(bk);
            refresh();
            return;
        }
        const retryable = res.outcome === 'network' || res.status === 503 || !res.status;
        if (!retryable) { requested.add(bk); keys.delete(bk); return; }
        if (attempt + 1 >= MAX_ATTEMPTS) return;
        stopTimer();
        timer = setTimer(() => {
            timer = null;
            if (disposed || mine !== seq) return;
            requestAlignment(ctx, mine, attempt + 1);
        }, BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
    }

    /** Load links of the current text revision and the matching audio revision, then the word alignment. */
    async function refresh() {
        if (disposed) return fail('failed');
        const mine = ++seq;
        stopTimer();
        announced = null;
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
            raw.value = EMPTY_RAW;
            setAlign('unaligned', false);
            say(problem.message);
            return problem;
        }
        const audioRevisionId = aud.outcome === 'ok' && aud.data && typeof aud.data.revision_id === 'string'
            ? aud.data.revision_id : null;
        const ctx = {
            text: data.text, textRevisionId: data.text_revision_id, audioRevisionId,
            fallback: linkCues(data.links) || [], document: null, documentHash: null,
        };
        applyBinding(ctx, null);
        setAlign('unaligned', false);
        loadState.value = 'ready';
        say(status.value === 'ready' ? LABELS.ready : LABELS.stale);
        if (typeof ctx.textRevisionId !== 'string' || audioRevisionId === null) return { ok: true };

        // Word alignment is additive: any failure below keeps the link fallback.
        const [doc, got] = await Promise.all([
            client.request(base + '/transcript-revisions/' + encodeURIComponent(ctx.textRevisionId) + '/document'),
            readAlignment(ctx),
        ]);
        if (mine !== seq || disposed) return { ok: false, code: 'cancelled', message: '' };
        if (doc.outcome !== 'ok' || !doc.data || typeof doc.data !== 'object' || got.kind === 'failed') {
            return { ok: true };
        }
        ctx.document = doc.data;
        ctx.documentHash = typeof doc.data.document_hash === 'string' ? doc.data.document_hash : null;
        if (got.kind === 'missing') {
            setAlign('unaligned');
        } else {
            applyBinding(ctx, evaluate(ctx, ctx.document, got.data));
        }
        const st = alignState.value;
        if (st === 'pending') schedulePoll(ctx, mine, null, 0);
        else if ((st === 'unaligned' || st === 'stale') && ctx.documentHash !== null && canEdit()
            && status.value === 'ready') await requestAlignment(ctx, mine, 0);
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
            if (current.value !== null) current.value = null;
            return null;
        }
        // Word cues are matched on the unrounded media position; stored links keep their rounding.
        const wordMode = raw.value.cues.length > 0 && raw.value.cues[0].granularity === 'word';
        const best = cueAt(wordMode ? timeSeconds * 1000 : Math.round(timeSeconds * 1000));
        const id = best ? best.id : null;
        if (current.value !== id) current.value = id;
        return currentWord.value;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        stopTimer();
        current.value = null;
        raw.value = EMPTY_RAW;
    }

    return {
        links, status, alignmentState, activeGranularity, error, selection, currentWord, segments, announcement,
        refresh, setSelection, seekToSelection, seekToSegment, highlightAt, cueAt, granularityOf,
        indexBuilds: () => builds, dispose,
    };
}

const EDITABLE = /^(INPUT|TEXTAREA|SELECT)$/;

/** Playback-driven highlight; see the module header. `audio` is the real media element. */
export function createPlaybackHighlighter(options) {
    const o = options || {};
    const alignment = o.alignment;
    const audio = o.audio;
    const raf = o.requestFrame || (typeof globalThis.requestAnimationFrame === 'function'
        ? globalThis.requestAnimationFrame.bind(globalThis) : (fn) => setTimeout(fn, 16));
    const caf = o.cancelFrame || (typeof globalThis.cancelAnimationFrame === 'function'
        ? globalThis.cancelAnimationFrame.bind(globalThis) : clearTimeout);
    let root = o.root || null;
    let active = null;
    let frame = null;
    let touches = 0;
    let manualScroll = false;
    let disposed = false;
    const elements = new Map();
    const guards = [];

    const playing = () => Boolean(audio) && audio.paused === false && audio.ended !== true;

    function elementFor(id) {
        if (!root || typeof root.querySelector !== 'function') return null;
        let el = elements.get(id);
        if (!el || el.isConnected === false) {
            el = root.querySelector('[data-link="' + String(id).replace(/["\\]/g, '\\$&') + '"]');
            if (el) elements.set(id, el); else elements.delete(id);
        }
        return el;
    }

    function mark(id, on) {
        if (id === null) return null;
        const el = elementFor(id);
        if (!el) return null;
        if (on) el.setAttribute('data-active', 'true'); else el.removeAttribute('data-active');
        touches += 1;
        return el;
    }

    function editing() {
        const doc = (root && root.ownerDocument) || globalThis.document;
        const ae = doc && doc.activeElement;
        return Boolean(ae) && (ae.isContentEditable === true || EDITABLE.test(ae.tagName || ''));
    }

    function follow(el) {
        if (!el || manualScroll || o.follow === false || editing() || typeof el.scrollIntoView !== 'function') return;
        el.scrollIntoView({ block: 'nearest' });
    }

    function apply(id) {
        if (id === active) return false;
        const prev = active;
        active = id;
        mark(prev, false);
        follow(mark(id, true));
        if (typeof o.onChange === 'function') o.onChange(prev, id);
        return true;
    }

    /** Read the media position now. `clear` forces "no active cue" (end, source change). */
    function update(clear) {
        if (disposed || !audio) return null;
        const word = clear === true ? alignment.highlightAt(null) : alignment.highlightAt(audio.currentTime);
        apply(word ? word.id : null);
        return word;
    }

    function tick() {
        frame = null;
        update();
        if (playing()) frame = raf(tick);
    }
    function start() { if (!disposed && frame === null && playing()) frame = raf(tick); }
    function stop() { if (frame !== null) { caf(frame); frame = null; } }

    function listen(target, name, fn) {
        if (!target || typeof target.addEventListener !== 'function') return;
        target.addEventListener(name, fn);
        guards.push([target, name, fn]);
    }
    const sync = () => { update(); start(); };
    listen(audio, 'play', () => { manualScroll = false; sync(); });
    listen(audio, 'playing', sync);
    listen(audio, 'pause', () => { stop(); update(); });
    listen(audio, 'seeking', sync);
    listen(audio, 'seeked', sync);
    listen(audio, 'ratechange', sync);
    listen(audio, 'timeupdate', () => update());
    listen(audio, 'ended', () => { stop(); update(true); });
    for (const name of ['emptied', 'loadstart', 'abort', 'error']) listen(audio, name, () => { stop(); update(true); });

    function watchScroll(node) {
        const target = o.scroller || node;
        for (const name of ['wheel', 'touchmove']) listen(target, name, () => { manualScroll = true; });
    }
    if (root) watchScroll(root);
    start();

    return {
        /** Bind the element that holds the words ([data-link] children). */
        setRoot(node) {
            elements.clear();
            root = node || null;
            if (root && !o.scroller) watchScroll(root);
            if (active !== null) { const id = active; active = null; apply(id); }
        },
        /** Re-evaluate after a text or binding change without a media event. */
        refresh: () => update(),
        resumeFollow() { manualScroll = false; },
        activeId: () => active,
        touches: () => touches,
        /** 'idle', 'active' or 'unaligned' (stale, foreign or failed binding). */
        state() {
            const status = alignment.status.value;
            if (status === 'stale' || status === 'error') return 'unaligned';
            return active === null ? 'idle' : 'active';
        },
        notice() {
            const status = alignment.status.value;
            if (status === 'stale' || status === 'error') return LABELS.notAligned;
            if (active !== null && alignment.granularityOf(active) === 'segment') return LABELS.segmentHighlight;
            return '';
        },
        dispose() {
            if (disposed) return;
            stop();
            for (const [target, name, fn] of guards.splice(0)) {
                if (typeof target.removeEventListener === 'function') target.removeEventListener(name, fn);
            }
            if (active !== null) mark(active, false);
            active = null;
            elements.clear();
            disposed = true;
        },
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
<section class="clinic-correction-alignment v8-page" :aria-label="LABELS.title" :aria-busy="status === 'loading'" :data-status="status">
  <h3>{{ LABELS.title }}</h3>
  <div v-if="status === 'error'" class="note" role="alert"
    :style="{ background: surfaces.problem.background, color: surfaces.problem.color, border: '1px solid ' + surfaces.problem.border }">
    {{ error && error.message }}
    <button type="button" :style="focusStyle" @click="refresh()">{{ LABELS.retry }}</button>
  </div>
  <div v-else-if="status === 'stale'" class="note" role="alert"
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
  <div class="clinic-constituent-status" role="status" aria-live="polite">{{ status === 'error' || status === 'stale' ? '' : announcement }}</div>
</section>`,
};

export function registerCorrectionAlignment(app) {
    app.component('clinic-correction-alignment', CorrectionAlignment);
    return app;
}
