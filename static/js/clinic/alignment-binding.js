/**
 * Alignment binding (contract 2.0): pure validation of an alignment payload
 * against its canonical document and binding, plus the word-cue index.
 *
 * Whole payload or nothing: any violation deactivates the entire track. Items
 * are never sorted, repaired or filtered. No DOM, no network, no imports.
 * Offsets are UTF-16 code units (JavaScript string indices).
 */

const REASON_CODE = /^[A-Z][A-Z0-9_]*$/;
const MARKS = new Set(['bold', 'italic', 'underline', 'superscript', 'subscript']);
const BLOCK_TYPES = new Set(['paragraph', 'heading', 'list_item']);
const KNOWN_STATES = new Set(['pending', 'succeeded', 'partial', 'failed', 'stale']);
const ITEM_STATES = new Set(['valid', 'unaligned', 'stale']);
const ITEM_KEYS = [
    'word_id', 'text_node_id', 'token_index', 'displayed_text',
    'state', 'start_ms', 'end_ms', 'reason_code',
];
const QUALITY_KEYS = ['valid_words', 'unaligned_words', 'stale_words', 'coverage', 'warnings'];

// Word tokens of a projection in document order; kept off the public shape.
const WORDS = new WeakMap();

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isId = (v) => typeof v === 'string' && v.length >= 1 && v.length <= 160 && !/\s/.test(v);
const isCount = (v) => Number.isSafeInteger(v) && v >= 0;

function failProjection(reason) {
    return { ok: false, reason, text: '', spans: new Map() };
}

/**
 * Validate a canonical document and project it to plain text.
 * Returns { ok, reason, text, spans }; spans maps token_id to { start, end }.
 */
export function projectDocument(doc) {
    if (!isObject(doc)) return failProjection('DOCUMENT_NOT_OBJECT');
    if (doc.document_schema_version !== '1.0') return failProjection('DOCUMENT_SCHEMA_VERSION');
    if (!Array.isArray(doc.nodes)) return failProjection('DOCUMENT_NODES');
    const nodeIds = new Set();
    const tokenIds = new Set();
    const spans = new Map();
    const words = [];
    const lines = [];
    let offset = 0;
    for (let n = 0; n < doc.nodes.length; n += 1) {
        const node = doc.nodes[n];
        if (!isObject(node)) return failProjection('NODE_NOT_OBJECT');
        if (n > 0) offset += 1; // the "\n" between node texts
        if (node.kind === 'object_reference') {
            // No text of its own: contributes an empty line to the projection.
            if (!isId(node.node_id) || nodeIds.has(node.node_id)) return failProjection('NODE_ID');
            nodeIds.add(node.node_id);
            lines.push('');
            continue;
        }
        if (node.kind !== 'text') return failProjection('NODE_KIND');
        if (!isId(node.text_node_id)) return failProjection('NODE_ID');
        if (nodeIds.has(node.text_node_id)) return failProjection('NODE_ID_DUPLICATE');
        nodeIds.add(node.text_node_id);
        if (!BLOCK_TYPES.has(node.block_type)) return failProjection('NODE_BLOCK_TYPE');
        if (!Array.isArray(node.tokens)) return failProjection('NODE_TOKENS');
        let line = '';
        for (let t = 0; t < node.tokens.length; t += 1) {
            const tok = node.tokens[t];
            if (!isObject(tok)) return failProjection('TOKEN_NOT_OBJECT');
            if (!isId(tok.token_id)) return failProjection('TOKEN_ID');
            if (tokenIds.has(tok.token_id)) return failProjection('TOKEN_ID_DUPLICATE');
            tokenIds.add(tok.token_id);
            if (tok.kind !== 'word' && tok.kind !== 'separator') return failProjection('TOKEN_KIND');
            if (typeof tok.text !== 'string' || tok.text.length === 0) return failProjection('TOKEN_TEXT');
            if (tok.kind === 'word' && /\s/.test(tok.text)) return failProjection('WORD_WHITESPACE');
            if (!Array.isArray(tok.marks) || new Set(tok.marks).size !== tok.marks.length
                || !tok.marks.every((m) => MARKS.has(m))) return failProjection('TOKEN_MARKS');
            const start = offset + line.length;
            line += tok.text;
            spans.set(tok.token_id, { start, end: offset + line.length });
            if (tok.kind === 'word') {
                words.push({ id: tok.token_id, nodeId: node.text_node_id, index: t, text: tok.text });
            }
        }
        offset += line.length;
        lines.push(line);
    }
    const projection = { ok: true, reason: '', text: lines.join('\n'), spans };
    WORDS.set(projection, words);
    return projection;
}

function invalid(reason) {
    return { active: false, state: 'invalid', reason, cues: [] };
}

function checkItems(items, words, duration) {
    if (!Array.isArray(items)) return { reason: 'ITEMS_NOT_ARRAY' };
    const byId = new Map(words.map((w) => [w.id, w]));
    const seen = new Set();
    const counts = { valid: 0, unaligned: 0, stale: 0 };
    const cues = [];
    let lastEnd = 0;
    for (let i = 0; i < items.length; i += 1) {
        const item = items[i];
        if (!isObject(item)) return { reason: 'ITEM_NOT_OBJECT' };
        const keys = Object.keys(item);
        if (keys.length !== ITEM_KEYS.length || !ITEM_KEYS.every((k) => k in item)) {
            return { reason: 'ITEM_KEYS' };
        }
        if (typeof item.word_id !== 'string') return { reason: 'ITEM_WORD_ID' };
        if (seen.has(item.word_id)) return { reason: 'ITEM_WORD_ID_DUPLICATE' };
        seen.add(item.word_id);
        const word = byId.get(item.word_id);
        if (!word) return { reason: 'ITEM_WORD_ID_UNKNOWN' };
        if (i >= words.length || words[i] !== word) return { reason: 'ITEM_ORDER' };
        if (item.text_node_id !== word.nodeId) return { reason: 'ITEM_TEXT_NODE' };
        if (item.token_index !== word.index) return { reason: 'ITEM_TOKEN_INDEX' };
        if (item.displayed_text !== word.text) return { reason: 'ITEM_DISPLAYED_TEXT' };
        if (!ITEM_STATES.has(item.state)) return { reason: 'ITEM_STATE' };
        if (item.state === 'valid') {
            const { start_ms: s, end_ms: e } = item;
            if (!Number.isSafeInteger(s) || !Number.isSafeInteger(e)) return { reason: 'ITEM_TIME_NOT_INTEGER' };
            if (s < 0 || s >= e) return { reason: 'ITEM_TIME_RANGE' };
            if (e > duration) return { reason: 'ITEM_BEYOND_DURATION' };
            if (s < lastEnd) return { reason: 'ITEM_TIME_ORDER' };
            if (item.reason_code !== null) return { reason: 'ITEM_REASON_CODE' };
            lastEnd = e;
            cues.push({ wordId: word.id, startMs: s, endMs: e, start: null, end: null });
        } else {
            if (item.start_ms !== null || item.end_ms !== null) return { reason: 'ITEM_TIME_NOT_NULL' };
            if (typeof item.reason_code !== 'string' || !REASON_CODE.test(item.reason_code)) {
                return { reason: 'ITEM_REASON_CODE' };
            }
        }
        counts[item.state] += 1;
    }
    if (items.length !== words.length) return { reason: 'ITEMS_COUNT' };
    return { counts, cues };
}

function checkQuality(quality, counts) {
    if (!isObject(quality)) return 'QUALITY';
    const keys = Object.keys(quality);
    if (keys.length !== QUALITY_KEYS.length || !QUALITY_KEYS.every((k) => k in quality)) return 'QUALITY_KEYS';
    if (!isCount(quality.valid_words) || !isCount(quality.unaligned_words) || !isCount(quality.stale_words)) {
        return 'QUALITY_COUNTS';
    }
    if (typeof quality.coverage !== 'number' || !Number.isFinite(quality.coverage)
        || quality.coverage < 0 || quality.coverage > 1) return 'QUALITY_COVERAGE';
    if (!Array.isArray(quality.warnings)
        || new Set(quality.warnings).size !== quality.warnings.length
        || !quality.warnings.every((w) => typeof w === 'string' && REASON_CODE.test(w))) return 'QUALITY_WARNINGS';
    if (quality.valid_words !== counts.valid || quality.unaligned_words !== counts.unaligned
        || quality.stale_words !== counts.stale) return 'QUALITY_MISMATCH';
    return null;
}

/**
 * Bind an alignment payload to the expected revisions and document.
 * expected = { transcriptRevisionId, audioRevisionId, documentHash }.
 * Active: { active: true, state, cues }. Anything else has no cues.
 */
export function bindAlignment(payload, expected, projection) {
    if (!projection || projection.ok !== true || !WORDS.has(projection)) return invalid('PROJECTION');
    if (!isObject(expected)) return invalid('EXPECTED');
    if (!isObject(payload)) return invalid('PAYLOAD_NOT_OBJECT');
    if (payload.schema_version !== '2.0') return invalid('SCHEMA_VERSION');
    if (payload.time_unit !== 'ms') return invalid('TIME_UNIT');
    if (!isId(expected.transcriptRevisionId) || payload.transcript_revision_id !== expected.transcriptRevisionId) {
        return invalid('TRANSCRIPT_REVISION');
    }
    if (!isId(expected.audioRevisionId) || payload.audio_revision_id !== expected.audioRevisionId) {
        return invalid('AUDIO_REVISION');
    }
    if (typeof expected.documentHash !== 'string' || payload.document_hash !== expected.documentHash) {
        return invalid('DOCUMENT_HASH');
    }
    const { state } = payload;
    if (typeof state !== 'string' || !KNOWN_STATES.has(state)) return invalid('STATE');
    if (state === 'stale') return { active: false, state, reason: '', cues: [] };
    if (state === 'pending' || state === 'failed') {
        if (!Array.isArray(payload.items) || payload.items.length !== 0) return invalid('ITEMS_NOT_EMPTY');
        return { active: false, state, reason: '', cues: [] };
    }
    const duration = payload.audio_duration_ms;
    if (!Number.isSafeInteger(duration) || duration < 1) return invalid('AUDIO_DURATION');
    const words = WORDS.get(projection);
    const checked = checkItems(payload.items, words, duration);
    if (checked.reason) return invalid(checked.reason);
    const { counts, cues } = checked;
    if (state === 'succeeded' && (counts.valid < 1 || counts.unaligned + counts.stale > 0)) {
        return invalid('SUCCEEDED_NOT_ALL_VALID');
    }
    if (state === 'partial' && (counts.valid < 1 || counts.unaligned + counts.stale < 1)) {
        return invalid('PARTIAL_COUNTS');
    }
    const quality = checkQuality(payload.quality, counts);
    if (quality) return invalid(quality);
    for (const cue of cues) {
        const span = projection.spans.get(cue.wordId);
        cue.start = span.start;
        cue.end = span.end;
    }
    return { active: true, state, cues };
}

/**
 * Cue playing at ms (half-open [startMs, endMs)); null in silence. O(log n).
 * Callers pass media.currentTime * 1000 unrounded.
 */
export function cueAt(cues, ms) {
    if (!Array.isArray(cues) || typeof ms !== 'number' || !Number.isFinite(ms)) return null;
    let lo = 0;
    let hi = cues.length - 1;
    let at = -1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (cues[mid].startMs <= ms) { at = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return at >= 0 && ms < cues[at].endMs ? cues[at] : null;
}
