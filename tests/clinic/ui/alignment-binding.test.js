import { describe, expect, it } from 'vitest';
import { bindAlignment, cueAt, projectDocument } from '../../../static/js/clinic/alignment-binding.js';

// Synthetic contract 2.0 examples, copied as literals.
const DOC = {
    "document_schema_version": "1.0",
    "nodes": [
        {
            "text_node_id": "node-1",
            "kind": "text",
            "block_type": "paragraph",
            "tokens": [
                {
                    "token_id": "token-1",
                    "kind": "word",
                    "text": "Die",
                    "marks": []
                },
                {
                    "token_id": "token-2",
                    "kind": "separator",
                    "text": " ",
                    "marks": []
                },
                {
                    "token_id": "token-3",
                    "kind": "word",
                    "text": "Unterlagen",
                    "marks": []
                },
                {
                    "token_id": "token-4",
                    "kind": "separator",
                    "text": " ",
                    "marks": []
                },
                {
                    "token_id": "token-5",
                    "kind": "word",
                    "text": "sind",
                    "marks": []
                },
                {
                    "token_id": "token-6",
                    "kind": "separator",
                    "text": " ",
                    "marks": []
                },
                {
                    "token_id": "token-7",
                    "kind": "word",
                    "text": "bereit",
                    "marks": []
                },
                {
                    "token_id": "token-8",
                    "kind": "separator",
                    "text": ".",
                    "marks": []
                }
            ]
        }
    ]
};
const SUCCEEDED = {
    "schema_version": "2.0",
    "recording_id": "4524",
    "audio_revision_id": "AR-DEMO-3",
    "transcript_revision_id": "TR-DEMO-5",
    "alignment_revision_id": "ALIGN-DEMO-5",
    "document_hash": "00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5",
    "time_unit": "ms",
    "audio_duration_ms": 2500,
    "state": "succeeded",
    "source": {
        "kind": "demo",
        "engine": "synthetic-fixture",
        "model_revision": "fixture-1",
        "tokenization_version": "canonical-token-1"
    },
    "created_at": "2026-11-19T17:00:01Z",
    "items": [
        {
            "word_id": "token-1",
            "text_node_id": "node-1",
            "token_index": 0,
            "displayed_text": "Die",
            "state": "valid",
            "start_ms": 200,
            "end_ms": 430,
            "reason_code": null
        },
        {
            "word_id": "token-3",
            "text_node_id": "node-1",
            "token_index": 2,
            "displayed_text": "Unterlagen",
            "state": "valid",
            "start_ms": 480,
            "end_ms": 1150,
            "reason_code": null
        },
        {
            "word_id": "token-5",
            "text_node_id": "node-1",
            "token_index": 4,
            "displayed_text": "sind",
            "state": "valid",
            "start_ms": 1200,
            "end_ms": 1450,
            "reason_code": null
        },
        {
            "word_id": "token-7",
            "text_node_id": "node-1",
            "token_index": 6,
            "displayed_text": "bereit",
            "state": "valid",
            "start_ms": 1500,
            "end_ms": 2010,
            "reason_code": null
        }
    ],
    "quality": {
        "valid_words": 4,
        "unaligned_words": 0,
        "stale_words": 0,
        "coverage": 1,
        "warnings": []
    }
};
const PARTIAL = {
    "schema_version": "2.0",
    "recording_id": "4524",
    "audio_revision_id": "AR-DEMO-3",
    "transcript_revision_id": "TR-DEMO-5",
    "alignment_revision_id": "ALIGN-DEMO-5-PARTIAL",
    "document_hash": "00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5",
    "time_unit": "ms",
    "audio_duration_ms": 2500,
    "state": "partial",
    "source": {
        "kind": "demo",
        "engine": "synthetic-fixture",
        "model_revision": "fixture-1",
        "tokenization_version": "canonical-token-1"
    },
    "created_at": "2026-11-19T17:00:01Z",
    "items": [
        {
            "word_id": "token-1",
            "text_node_id": "node-1",
            "token_index": 0,
            "displayed_text": "Die",
            "state": "valid",
            "start_ms": 200,
            "end_ms": 430,
            "reason_code": null
        },
        {
            "word_id": "token-3",
            "text_node_id": "node-1",
            "token_index": 2,
            "displayed_text": "Unterlagen",
            "state": "valid",
            "start_ms": 480,
            "end_ms": 1150,
            "reason_code": null
        },
        {
            "word_id": "token-5",
            "text_node_id": "node-1",
            "token_index": 4,
            "displayed_text": "sind",
            "state": "valid",
            "start_ms": 1200,
            "end_ms": 1450,
            "reason_code": null
        },
        {
            "word_id": "token-7",
            "text_node_id": "node-1",
            "token_index": 6,
            "displayed_text": "bereit",
            "state": "unaligned",
            "start_ms": null,
            "end_ms": null,
            "reason_code": "NO_VERIFIED_TIMING"
        }
    ],
    "quality": {
        "valid_words": 3,
        "unaligned_words": 1,
        "stale_words": 0,
        "coverage": 0.75,
        "warnings": [
            "PARTIAL_ALIGNMENT"
        ]
    }
};
const PENDING = {
    "schema_version": "2.0",
    "recording_id": "4524",
    "audio_revision_id": "AR-DEMO-3",
    "transcript_revision_id": "TR-DEMO-5",
    "alignment_revision_id": "ALIGN-DEMO-5-PENDING",
    "document_hash": "00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5",
    "time_unit": "ms",
    "audio_duration_ms": 2500,
    "state": "pending",
    "source": {
        "kind": "demo",
        "engine": "synthetic-fixture",
        "model_revision": "fixture-1",
        "tokenization_version": "canonical-token-1"
    },
    "created_at": "2026-11-19T17:00:01Z",
    "items": [],
    "quality": {
        "valid_words": 0,
        "unaligned_words": 0,
        "stale_words": 0,
        "coverage": 0,
        "warnings": []
    }
};

const EXPECTED = {
    transcriptRevisionId: 'TR-DEMO-5',
    audioRevisionId: 'AR-DEMO-3',
    documentHash: '00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5',
};
const clone = (v) => JSON.parse(JSON.stringify(v));
const projection = () => projectDocument(clone(DOC));
const bind = (payload) => bindAlignment(payload, EXPECTED, projection());
const withItem = (i, patch, base = SUCCEEDED) => {
    const p = clone(base);
    Object.assign(p.items[i], patch);
    return p;
};

function textDoc(tokenLists) {
    return {
        document_schema_version: '1.0',
        nodes: tokenLists.map((tokens, n) => ({
            text_node_id: 'n' + n,
            kind: 'text',
            block_type: 'paragraph',
            tokens: tokens.map(([kind, text], t) => ({ token_id: 'n' + n + 't' + t, kind, text, marks: [] })),
        })),
    };
}

describe('projectDocument', () => {
    it('projects the example with exact spans', () => {
        const p = projection();
        expect(p.ok).toBe(true);
        expect(p.text).toBe('Die Unterlagen sind bereit.');
        expect(p.spans.get('token-1')).toEqual({ start: 0, end: 3 });
        expect(p.spans.get('token-7')).toEqual({ start: 20, end: 26 });
        expect(p.text.slice(20, 26)).toBe('bereit');
    });

    it('counts UTF-16 code units with emoji and surrogate pairs, across nodes', () => {
        const doc = textDoc([
            [['word', 'a\u{1F600}b'], ['separator', ' '], ['word', '\u{1D11E}']],
            [['word', 'x']],
        ]);
        const p = projectDocument(doc);
        expect(p.ok).toBe(true);
        expect(p.text).toBe('a\u{1F600}b \u{1D11E}\nx');
        expect(p.spans.get('n0t0')).toEqual({ start: 0, end: 4 });
        expect(p.spans.get('n0t2')).toEqual({ start: 5, end: 7 });
        expect(p.spans.get('n1t0')).toEqual({ start: 8, end: 9 });
        for (const id of ['n0t0', 'n0t2', 'n1t0']) {
            const { start, end } = p.spans.get(id);
            expect(p.text.slice(start, end)).toBe(id === 'n0t0' ? 'a\u{1F600}b' : id === 'n0t2' ? '\u{1D11E}' : 'x');
        }
    });

    it('rejects duplicate token ids and duplicate node ids', () => {
        const dupToken = clone(DOC);
        dupToken.nodes[0].tokens[2].token_id = 'token-1';
        expect(projectDocument(dupToken).ok).toBe(false);
        const dupNode = textDoc([[['word', 'a']], [['word', 'b']]]);
        dupNode.nodes[1].text_node_id = 'n0';
        expect(projectDocument(dupNode).ok).toBe(false);
    });

    it('rejects bad version, kinds, whitespace in words and non-objects', () => {
        const bad = (mut) => { const d = clone(DOC); mut(d); return projectDocument(d).ok; };
        expect(bad((d) => { d.document_schema_version = '2.0'; })).toBe(false);
        expect(bad((d) => { d.nodes[0].kind = 'table'; })).toBe(false);
        expect(bad((d) => { d.nodes[0].tokens[0].kind = 'emoji'; })).toBe(false);
        expect(bad((d) => { d.nodes[0].tokens[0].text = 'a b'; })).toBe(false);
        expect(bad((d) => { d.nodes[0].tokens[0].text = ''; })).toBe(false);
        expect(projectDocument(null).ok).toBe(false);
        expect(projectDocument(null).spans.size).toBe(0);
    });
});

describe('bindAlignment active', () => {
    it('binds the succeeded example', () => {
        const r = bind(SUCCEEDED);
        expect(r.active).toBe(true);
        expect(r.state).toBe('succeeded');
        expect(r.cues).toHaveLength(4);
        expect(r.cues[1]).toEqual({ wordId: 'token-3', startMs: 480, endMs: 1150, start: 4, end: 14 });
    });

    it('binds the partial example with three cues and none for "bereit"', () => {
        const r = bind(PARTIAL);
        expect(r.active).toBe(true);
        expect(r.state).toBe('partial');
        expect(r.cues.map((c) => c.wordId)).toEqual(['token-1', 'token-3', 'token-5']);
    });

    it('keeps the pending example inactive', () => {
        const r = bind(PENDING);
        expect(r).toMatchObject({ active: false, state: 'pending', cues: [] });
    });

    it('keeps failed and stale inactive', () => {
        const failed = { ...clone(PENDING), state: 'failed' };
        expect(bind(failed)).toMatchObject({ active: false, state: 'failed', cues: [] });
        const stale = { ...clone(SUCCEEDED), state: 'stale' };
        expect(bind(stale)).toMatchObject({ active: false, state: 'stale', cues: [] });
    });

    it('accepts touching intervals', () => {
        const p = withItem(1, { start_ms: 430, end_ms: 480 });
        p.items[2].start_ms = 480;
        expect(bind(p).active).toBe(true);
    });
});

describe('bindAlignment invalid', () => {
    const cases = {
        'wrong transcript revision id': () => ({ ...clone(SUCCEEDED), transcript_revision_id: 'TR-OTHER' }),
        'wrong audio revision id': () => ({ ...clone(SUCCEEDED), audio_revision_id: 'AR-OTHER' }),
        'wrong hash': () => ({ ...clone(SUCCEEDED), document_hash: 'f'.repeat(64) }),
        'wrong schema version': () => ({ ...clone(SUCCEEDED), schema_version: '1.0' }),
        'wrong time unit': () => ({ ...clone(SUCCEEDED), time_unit: 's' }),
        'unknown state': () => ({ ...clone(SUCCEEDED), state: 'done' }),
        'duplicate word_id': () => withItem(1, { word_id: 'token-1' }),
        'unknown word_id': () => withItem(1, { word_id: 'token-99' }),
        'separator referenced': () => withItem(1, { word_id: 'token-2' }),
        'wrong token_index': () => withItem(1, { token_index: 3 }),
        'wrong text_node_id': () => withItem(1, { text_node_id: 'node-2' }),
        'displayed_text mismatch': () => withItem(1, { displayed_text: 'Unterlage' }),
        overlap: () => withItem(1, { start_ms: 400 }),
        'unordered items': () => {
            const p = clone(SUCCEEDED);
            [p.items[1], p.items[2]] = [p.items[2], p.items[1]];
            return p;
        },
        'missing item': () => { const p = clone(SUCCEEDED); p.items.pop(); return p; },
        'end beyond duration': () => withItem(3, { end_ms: 2501 }),
        'start equals end': () => withItem(1, { start_ms: 480, end_ms: 480 }),
        'start after end': () => withItem(1, { start_ms: 500, end_ms: 480 }),
        'negative start': () => withItem(0, { start_ms: -1 }),
        'float ms': () => withItem(1, { start_ms: 480.5 }),
        'valid with null times': () => withItem(1, { start_ms: null, end_ms: null }),
        'valid with reason code': () => withItem(1, { reason_code: 'X' }),
        'unaligned with times': () => withItem(3, { start_ms: 1500, end_ms: 2010 }, PARTIAL),
        'unaligned with lower-case reason': () => withItem(3, { reason_code: 'no_timing' }, PARTIAL),
        'unaligned without reason': () => withItem(3, { reason_code: null }, PARTIAL),
        'succeeded with a non-valid item': () => {
            const p = clone(PARTIAL);
            p.state = 'succeeded';
            return p;
        },
        'partial with only valid items': () => ({ ...clone(SUCCEEDED), state: 'partial' }),
        'partial without a valid item': () => {
            const p = clone(PARTIAL);
            for (const it of p.items) Object.assign(it, { state: 'unaligned', start_ms: null, end_ms: null, reason_code: 'X' });
            p.quality = { ...p.quality, valid_words: 0, unaligned_words: 4, coverage: 0 };
            return p;
        },
        'quality mismatch': () => { const p = clone(SUCCEEDED); p.quality.valid_words = 3; return p; },
        'quality mismatch partial': () => { const p = clone(PARTIAL); p.quality.unaligned_words = 0; return p; },
        'pending with items': () => ({ ...clone(PENDING), items: clone(SUCCEEDED).items }),
        'failed with items': () => ({ ...clone(PENDING), state: 'failed', items: clone(SUCCEEDED).items }),
        'items not an array': () => ({ ...clone(SUCCEEDED), items: null }),
        'payload null': () => null,
        'extra item key': () => withItem(0, { extra: 1 }),
    };
    for (const [name, make] of Object.entries(cases)) {
        it(name, () => {
            const r = bind(make());
            expect(r.active).toBe(false);
            expect(r.state).toBe('invalid');
            expect(r.cues).toEqual([]);
            expect(r.reason).not.toBe('');
        });
    }

    it('never sorts or repairs the payload', () => {
        const p = clone(SUCCEEDED);
        [p.items[0], p.items[1]] = [p.items[1], p.items[0]];
        const before = JSON.stringify(p);
        expect(bind(p).active).toBe(false);
        expect(JSON.stringify(p)).toBe(before);
    });

    it('rejects a failed projection or mismatching expected ids', () => {
        const badProj = projectDocument({ document_schema_version: '9' });
        expect(bindAlignment(SUCCEEDED, EXPECTED, badProj)).toMatchObject({ active: false, state: 'invalid' });
        expect(bindAlignment(SUCCEEDED, EXPECTED, { ok: true, text: '', spans: new Map() }).active).toBe(false);
        expect(bindAlignment(SUCCEEDED, { ...EXPECTED, audioRevisionId: '' }, projection()).active).toBe(false);
    });
});

describe('cueAt', () => {
    const cues = bind(SUCCEEDED).cues;

    it('hits at start, misses at end, gives null in silence', () => {
        expect(cueAt(cues, 200).wordId).toBe('token-1');
        expect(cueAt(cues, 429.9).wordId).toBe('token-1');
        expect(cueAt(cues, 430)).toBeNull();
        expect(cueAt(cues, 0)).toBeNull();
        expect(cueAt(cues, 199.999)).toBeNull();
        expect(cueAt(cues, 1150)).toBeNull();
        expect(cueAt(cues, 1200).wordId).toBe('token-5');
        expect(cueAt(cues, 2010)).toBeNull();
        expect(cueAt(cues, 99999)).toBeNull();
    });

    it('handles empty, non-finite and fractional input', () => {
        expect(cueAt([], 10)).toBeNull();
        expect(cueAt(cues, NaN)).toBeNull();
        expect(cueAt(cues, Infinity)).toBeNull();
        expect(cueAt(cues, '200')).toBeNull();
        expect(cueAt(cues, 1.2 * 1000 + 0.4).wordId).toBe('token-5');
    });

    it('stays logarithmic for 10 000 cues and 100 000 lookups', () => {
        const big = [];
        for (let i = 0; i < 10000; i += 1) big.push({ wordId: 'w' + i, startMs: i * 10, endMs: i * 10 + 8, start: 0, end: 0 });
        const t0 = performance.now();
        let hits = 0;
        for (let i = 0; i < 100000; i += 1) {
            const ms = (i * 7919) % 100000 + 0.5;
            const cue = cueAt(big, ms);
            if (cue) { hits += 1; expect(ms >= cue.startMs && ms < cue.endMs).toBe(true); }
            else expect(ms % 10 >= 8).toBe(true);
        }
        expect(hits).toBeGreaterThan(0);
        expect(performance.now() - t0).toBeLessThan(2000);
    });
});
