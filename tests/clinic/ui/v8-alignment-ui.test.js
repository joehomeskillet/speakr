import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LABELS, useCorrectionAlignment } from '../../../static/js/clinic/correction-alignment-ui.js';

// Synthetic fixtures; document, hash and alignment follow the contract 2.0 examples.
const D = '11111111-1111-4111-8111-111111111111';
const TR = 'TR-DEMO-5';
const AR = 'AR-DEMO-3';
const HASH = '00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5';
const TEXT = 'Die Unterlagen sind bereit.';
const base = '/dictations/' + D;
const ALIGN = 'GET ' + base + '/alignment?audio_revision_id=' + AR + '&transcript_revision_id=' + TR;
const DOC = 'GET ' + base + '/transcript-revisions/' + TR + '/document';
const POST = 'POST ' + base + '/alignment';

const ok = (data, status = 200) => ({ outcome: 'ok', status, data });
const fail = (outcome, status, code = null) => ({ outcome, status, code, message: '' });

const word = (id, index, text) => ({ id, index, text });
const WORDS = [word('token-1', 0, 'Die'), word('token-3', 2, 'Unterlagen'), word('token-5', 4, 'sind'), word('token-7', 6, 'bereit')];
const TIMES = [[200, 430], [480, 1150], [1200, 1450], [1500, 2010]];

function documentBody() {
    const tokens = [];
    const ids = [['token-1', 'Die'], ['token-3', 'Unterlagen'], ['token-5', 'sind'], ['token-7', 'bereit']];
    ids.forEach(([id, text], i) => {
        if (i > 0) tokens.push({ token_id: 'sep-' + i, kind: 'separator', text: ' ', marks: [] });
        tokens.push({ token_id: id, kind: 'word', text, marks: [] });
    });
    tokens.push({ token_id: 'token-8', kind: 'separator', text: '.', marks: [] });
    return {
        schema_version: '2.0', transcript_revision_id: TR, tokenization_version: 'canonical-token-1', document_hash: HASH,
        canonical_document: { document_schema_version: '1.0', nodes: [{ text_node_id: 'node-1', kind: 'text', block_type: 'paragraph', tokens }] },
    };
}

// Index of a word token inside its node: tokens alternate word, separator.
function alignment(state, unalignedIds = []) {
    const items = WORDS.map((w, i) => {
        const bad = unalignedIds.includes(w.id);
        return {
            word_id: w.id, text_node_id: 'node-1', token_index: i * 2, displayed_text: w.text,
            state: bad ? 'unaligned' : 'valid', start_ms: bad ? null : TIMES[i][0], end_ms: bad ? null : TIMES[i][1],
            reason_code: bad ? 'NO_WORD_TIMING' : null,
        };
    });
    const valid = items.filter((i) => i.state === 'valid').length;
    const pending = state === 'pending' || state === 'failed';
    return {
        schema_version: '2.0', recording_id: D, audio_revision_id: AR, transcript_revision_id: TR,
        alignment_revision_id: 'ALIGN-1', document_hash: HASH, time_unit: 'ms', audio_duration_ms: 2500, state,
        source: { kind: 'demo', engine: 'synthetic-fixture', model_revision: 'fixture-1', tokenization_version: 'canonical-token-1' },
        created_at: '2026-11-19T17:00:01Z', items: pending ? [] : items,
        quality: { valid_words: pending ? 0 : valid, unaligned_words: pending ? 0 : items.length - valid, stale_words: 0, coverage: pending ? 0 : valid / items.length, warnings: [] },
    };
}

const SEGMENT = { id: 'S0', origin: 'asr', text_start: 0, text_end: TEXT.length, audio_start_ms: 0, audio_end_ms: 2500, granularity: 'segment' };

function mockEditor(text = TEXT, canEdit = true) {
    return {
        text: { value: text }, textRevisionId: { value: TR },
        audio: { value: { status: 'ready', revisionId: AR } }, canEdit: { value: canEdit },
    };
}

function mockClient(routes) {
    const calls = [];
    const client = {
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ key: method + ' ' + path, options });
            const hit = routes[method + ' ' + path];
            const value = typeof hit === 'function' ? await hit(options, calls) : hit;
            return value || fail('UNKNOWN', 500);
        }),
    };
    const count = (key) => calls.filter((c) => c.key === key).length;
    return { client, calls, count };
}

function routes(extra) {
    return {
        ['GET ' + base + '/correction']: ok({ dictation_id: D, text_revision_id: TR, text: TEXT, links: [SEGMENT], audio_revision_id: AR, markers: [] }),
        ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: AR, segments: [] }),
        [DOC]: ok(documentBody()),
        [ALIGN]: ok(alignment('succeeded')),
        ...(extra || {}),
    };
}

function make(table, editor = mockEditor(), extra) {
    const mocked = mockClient(table || routes());
    const align = useCorrectionAlignment({ editor, client: mocked.client, dictationId: D, seek: vi.fn(), ...(extra || {}) });
    return { ...mocked, editor, align };
}

const settle = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('word alignment from a valid contract 2.0 binding', () => {
    it('succeeded gives word cues with granularity word and one calm message', async () => {
        const { align, count } = make();
        expect((await align.refresh()).ok).toBe(true);
        expect(align.status.value).toBe('ready');
        expect(align.alignmentState.value).toBe('succeeded');
        expect(align.announcement.value).toBe(LABELS.alignSucceeded);
        expect(align.links.value.map((l) => TEXT.slice(l.start, l.end))).toEqual(['Die', 'Unterlagen', 'sind', 'bereit']);
        expect(align.links.value.every((l) => l.granularity === 'word')).toBe(true);
        const hit = align.highlightAt(0.25);
        expect(hit).toEqual({ id: 'token-1', start: 0, end: 3 });
        expect(align.activeGranularity.value).toBe('word');
        expect(align.granularityOf('token-1')).toBe('word');
        expect(align.highlightAt(0.455)).toBeNull(); // silence between words
        expect(align.highlightAt(1.2).id).toBe('token-5');
        expect(count(POST)).toBe(0);
    });

    it('partial never activates the unaligned word', async () => {
        const table = routes({ [ALIGN]: ok(alignment('partial', ['token-5'])) });
        const { align } = make(table);
        await align.refresh();
        expect(align.alignmentState.value).toBe('partial');
        expect(align.announcement.value).toBe(LABELS.alignPartial);
        expect(align.highlightAt(1.3)).toBeNull();
        expect(align.cueAt(1300)).toBeNull();
        expect(align.highlightAt(0.3).id).toBe('token-1');
        expect(align.links.value.map((l) => TEXT.slice(l.start, l.end))).not.toContain('sind');
        const gap = align.segments.value.find((s) => s.text.includes('sind'));
        expect(gap).toMatchObject({ aligned: false, flagged: true });
    });

    it('an invalid payload gives no word cue and keeps the Segment fallback', async () => {
        const broken = alignment('succeeded');
        broken.items[1].end_ms = 100; // end before start
        const { align } = make(routes({ [ALIGN]: ok(broken) }));
        await align.refresh();
        expect(align.alignmentState.value).toBe('invalid');
        expect(align.announcement.value).toBe(LABELS.alignInvalid);
        expect(align.links.value.map((l) => l.id)).toEqual(['S0']);
        expect(align.highlightAt(0.3).id).toBe('S0');
        expect(align.granularityOf('S0')).toBe('segment');

        const foreign = alignment('succeeded');
        foreign.document_hash = 'f'.repeat(64);
        const second = make(routes({ [ALIGN]: ok(foreign) }));
        await second.align.refresh();
        expect(second.align.alignmentState.value).toBe('invalid');
        expect(second.align.links.value.map((l) => l.id)).toEqual(['S0']);
    });
});

describe('Segment fallback and playhead only', () => {
    it('failed or missing word timings use the named Segment fallback', async () => {
        for (const reply of [ok(alignment('failed')), fail('UNKNOWN', 404, 'ALIGNMENT_NOT_FOUND')]) {
            const { align } = make(routes({ [ALIGN]: reply }), mockEditor(TEXT, false));
            await align.refresh();
            expect(['failed', 'unaligned']).toContain(align.alignmentState.value);
            expect(align.highlightAt(1).id).toBe('S0');
            expect(align.activeGranularity.value).toBe('segment');
            expect(align.announcement.value).toBe(LABELS.alignUnaligned);
        }
    });

    it('a failed document or alignment request leaves today link behaviour', async () => {
        const { align, count } = make(routes({ [DOC]: fail('network', 503) }));
        await align.refresh();
        expect(align.status.value).toBe('ready');
        expect(align.alignmentState.value).toBe('unaligned');
        expect(align.announcement.value).toBe(LABELS.ready);
        expect(align.highlightAt(1).id).toBe('S0');
        expect(count(POST)).toBe(0);
    });

    it('without links there is the playhead only; one malformed link voids all links', async () => {
        const corr = (links) => ok({ dictation_id: D, text_revision_id: TR, text: TEXT, links, markers: [] });
        const none = make(routes({ ['GET ' + base + '/correction']: corr([]), [ALIGN]: fail('UNKNOWN', 404, 'ALIGNMENT_NOT_FOUND') }), mockEditor(TEXT, false));
        await none.align.refresh();
        expect(none.align.links.value).toEqual([]);
        expect(none.align.highlightAt(1)).toBeNull();

        const bad = { ...SEGMENT, id: 'S1', audio_end_ms: 'x' };
        const mixed = make(routes({ ['GET ' + base + '/correction']: corr([SEGMENT, bad]), [ALIGN]: fail('UNKNOWN', 404, 'ALIGNMENT_NOT_FOUND') }), mockEditor(TEXT, false));
        await mixed.align.refresh();
        expect(mixed.align.links.value).toEqual([]);
        expect(mixed.align.highlightAt(1)).toBeNull();
    });

    it('granularityOf uses the server field; the whitespace guess only covers links without it', async () => {
        const plain = { ...SEGMENT, id: 'S2', granularity: undefined };
        const word = { id: 'W1', origin: 'asr', text_start: 0, text_end: 3, audio_start_ms: 0, audio_end_ms: 500 };
        const corr = ok({ dictation_id: D, text_revision_id: TR, text: TEXT, links: [{ ...word, granularity: 'segment' }, { ...plain, text_start: 4 }], markers: [] });
        const { align } = make(routes({ ['GET ' + base + '/correction']: corr, [ALIGN]: fail('UNKNOWN', 404, 'ALIGNMENT_NOT_FOUND') }), mockEditor(TEXT, false));
        await align.refresh();
        expect(align.highlightAt(0.2).id).toBe('W1');
        expect(align.granularityOf('W1')).toBe('segment'); // server field wins over a one-word span
        expect(align.granularityOf('S2')).toBe('segment'); // guessed: spans several words
    });
});

describe('pending job and late responses', () => {
    it('pending shows no word cue, polls with backoff and stops after success', async () => {
        let n = 0;
        const table = routes({ [ALIGN]: () => (++n >= 3 ? ok(alignment('succeeded')) : ok(alignment('pending'))) });
        const { align, count } = make(table);
        await align.refresh();
        expect(align.alignmentState.value).toBe('pending');
        expect(align.announcement.value).toBe(LABELS.alignPending);
        expect(align.highlightAt(0.3).id).toBe('S0'); // Segment fallback while pending
        expect(count(ALIGN)).toBe(1);
        await vi.advanceTimersByTimeAsync(999);
        expect(count(ALIGN)).toBe(1);
        await vi.advanceTimersByTimeAsync(1);
        expect(count(ALIGN)).toBe(2);
        expect(align.alignmentState.value).toBe('pending');
        await vi.advanceTimersByTimeAsync(2000);
        expect(count(ALIGN)).toBe(3);
        expect(align.alignmentState.value).toBe('succeeded');
        expect(align.highlightAt(0.3).id).toBe('token-1');
        await vi.advanceTimersByTimeAsync(120000);
        expect(count(ALIGN)).toBe(3);
    });

    it('polling is bounded to ten attempts and stops on dispose', async () => {
        const capped = make(routes({ [ALIGN]: ok(alignment('pending')) }));
        await capped.align.refresh();
        await vi.advanceTimersByTimeAsync(600000);
        expect(capped.count(ALIGN)).toBe(1 + 10);

        const disposed = make(routes({ [ALIGN]: ok(alignment('pending')) }));
        await disposed.align.refresh();
        await vi.advanceTimersByTimeAsync(1000);
        expect(disposed.count(ALIGN)).toBe(2);
        disposed.align.dispose();
        await vi.advanceTimersByTimeAsync(600000);
        expect(disposed.count(ALIGN)).toBe(2);
    });

    it('a late response for an old binding is ignored', async () => {
        let release;
        const gate = new Promise((resolve) => { release = resolve; });
        let n = 0;
        const table = routes({
            [ALIGN]: async () => {
                n += 1;
                if (n === 1) { await gate; return ok(alignment('succeeded')); }
                return ok(alignment('partial', ['token-5']));
            },
        });
        const { align } = make(table);
        const first = align.refresh();
        await settle();
        const second = await align.refresh();
        expect(second.ok).toBe(true);
        expect(align.alignmentState.value).toBe('partial');
        release();
        expect(await first).toMatchObject({ ok: false, code: 'cancelled' });
        expect(align.alignmentState.value).toBe('partial');
        expect(align.highlightAt(1.3)).toBeNull();
    });

    it('a stale alignment row is reported as stale', async () => {
        const stale = { ...alignment('pending'), state: 'stale', transcript_revision_id: 'TR-OLD' };
        const { align } = make(routes({ [ALIGN]: ok(stale), [POST]: ok({ operation_id: 'OP1', state: 'pending' }, 202) }), mockEditor(TEXT, false));
        await align.refresh();
        expect(align.alignmentState.value).toBe('stale');
        expect(align.announcement.value).toBe(LABELS.alignStale);
    });
});

describe('alignment request after a confirmed save', () => {
    const missing = fail('UNKNOWN', 404, 'ALIGNMENT_NOT_FOUND');

    it('posts exactly once per binding and then follows the operation', async () => {
        let state = 'pending';
        let aligned = false;
        const table = routes({
            [ALIGN]: () => (aligned ? ok(alignment('succeeded')) : missing),
            [POST]: ok({ operation_id: 'OP1', state: 'pending' }, 202),
            ['GET ' + base + '/operations/OP1']: () => { if (state === 'succeeded') aligned = true; return ok({ operation_id: 'OP1', state }); },
        });
        const { align, count, calls } = make(table, mockEditor(), { newKey: () => '0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f' });
        await align.refresh();
        expect(count(POST)).toBe(1);
        const sent = calls.find((c) => c.key === POST).options;
        expect(sent.idempotencyKey).toBe('0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f');
        expect(sent.body).toEqual({ schema_version: '2.0', audio_revision_id: AR, transcript_revision_id: TR, document_hash: HASH });
        expect(align.alignmentState.value).toBe('pending');
        await vi.advanceTimersByTimeAsync(1000);
        expect(count('GET ' + base + '/operations/OP1')).toBe(1);
        expect(align.alignmentState.value).toBe('pending');
        state = 'succeeded';
        await vi.advanceTimersByTimeAsync(2000);
        expect(align.alignmentState.value).toBe('succeeded');
        expect(align.highlightAt(0.3).id).toBe('token-1');
        expect(count(POST)).toBe(1);
        await align.refresh();
        expect(count(POST)).toBe(1);
    });

    it('a second refresh of the same binding does not post again', async () => {
        const { align, count } = make(routes({ [ALIGN]: missing, [POST]: ok({ operation_id: 'OP3', state: 'pending' }, 202) }));
        await align.refresh();
        await align.refresh();
        await align.refresh();
        expect(count(POST)).toBe(1);
        align.dispose();
    });

    it('retries after 503 with the same Idempotency-Key', async () => {
        let n = 0;
        const table = routes({
            [ALIGN]: missing,
            [POST]: () => (++n === 1 ? fail('network', 503, 'DEPENDENCY_UNAVAILABLE') : ok({ operation_id: 'OP2', state: 'pending' }, 202)),
        });
        const { align, calls, count } = make(table);
        await align.refresh();
        expect(count(POST)).toBe(1);
        await vi.advanceTimersByTimeAsync(1000);
        expect(count(POST)).toBe(2);
        const keys = calls.filter((c) => c.key === POST).map((c) => c.options.idempotencyKey);
        expect(keys[0]).toBe(keys[1]);
        expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
        expect(align.alignmentState.value).toBe('pending');
        align.dispose();
    });

    it('409 BASE_CONFLICT reloads once and does not post again for the same binding', async () => {
        const table = routes({ [ALIGN]: missing, [POST]: fail('conflict', 409, 'BASE_CONFLICT') });
        const { align, count } = make(table);
        await align.refresh();
        await vi.advanceTimersByTimeAsync(0);
        expect(count(POST)).toBe(1);
        expect(count('GET ' + base + '/correction')).toBe(2);
        expect(align.status.value).toBe('ready');
    });

    it('never posts without edit permission or without a confirmed binding', async () => {
        const viewer = make(routes({ [ALIGN]: missing }), mockEditor(TEXT, false));
        await viewer.align.refresh();
        expect(viewer.count(POST)).toBe(0);
        const noAudio = make(routes({ [ALIGN]: missing, ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: null }) }));
        await noAudio.align.refresh();
        expect(noAudio.count(POST)).toBe(0);
    });
});

describe('local unsaved edits', () => {
    it('typing removes word cues in the changed region at once; undo restores them', async () => {
        const { align, editor } = make();
        await align.refresh();
        expect(align.links.value.map((l) => l.id)).toEqual(['token-1', 'token-3', 'token-5', 'token-7']);

        editor.text.value = TEXT.replace('Unterlagen', 'Unterlagx');
        expect(align.links.value.map((l) => l.id)).toEqual(['token-1', 'token-5', 'token-7']);
        expect(align.cueAt(600)).toBeNull();
        const shifted = align.links.value.find((l) => l.id === 'token-5');
        expect(editor.text.value.slice(shifted.start, shifted.end)).toBe('sind');

        editor.text.value = TEXT.replace('sind', 'sind sehr');
        expect(align.links.value.map((l) => l.id)).toEqual(['token-1', 'token-3', 'token-5', 'token-7']);
        // A whole word typed between two words leaves all cues in place; the new word has none.
        // Growing the word itself ("sind" to "sinds") drops it, the others keep their place.
        editor.text.value = TEXT.replace('sind', 'sinds');
        expect(align.links.value.map((l) => l.id)).toEqual(['token-1', 'token-3', 'token-7']);
        const after = align.links.value.find((l) => l.id === 'token-7');
        expect(editor.text.value.slice(after.start, after.end)).toBe('bereit');

        editor.text.value = TEXT;
        expect(align.links.value.map((l) => l.id)).toEqual(['token-1', 'token-3', 'token-5', 'token-7']);
        expect(align.highlightAt(0.3).id).toBe('token-1');
    });
});
