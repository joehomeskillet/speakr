import { describe, expect, it, vi } from 'vitest';
import {
    CorrectionAlignment, LABELS, registerCorrectionAlignment, useCorrectionAlignment,
} from '../../../static/js/clinic/correction-alignment-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const AUDIO = '33333333-3333-4333-8333-333333333333';
const base = '/dictations/' + D;

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: '' });

// Synthetic text: ASCII, emoji (surrogate pair), combining mark (e + U+0301).
const TEXT = 'Befund \u{1F600} unauffällig heute';
function wordLinks(text) {
    const out = [];
    const re = /\S+/gu;
    let m;
    let t = 0;
    while ((m = re.exec(text)) !== null) {
        out.push({ id: 'L' + out.length, origin: 'asr', text_start: m.index, text_end: m.index + m[0].length, audio_start_ms: t, audio_end_ms: t + 500 });
        t += 500;
    }
    return out;
}

function mockEditor(text = TEXT, extra) {
    return {
        text: { value: text },
        textRevisionId: { value: REV },
        audio: { value: { status: 'ready', revisionId: AUDIO } },
        ...(extra || {}),
    };
}

function mockClient(routes) {
    const calls = [];
    const client = {
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push(method + ' ' + path);
            const hit = routes[method + ' ' + path];
            const value = typeof hit === 'function' ? hit(options) : hit;
            return value || fail('UNKNOWN', 500);
        }),
    };
    return { client, calls };
}

function happy(extra) {
    return {
        ['GET ' + base + '/correction']: ok({ dictation_id: D, text_revision_id: REV, text: TEXT, links: wordLinks(TEXT), markers: [] }),
        ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: AUDIO, segments: [] }),
        ...(extra || {}),
    };
}

async function make(routes, editor = mockEditor()) {
    const mocked = mockClient(routes || happy());
    const seek = vi.fn();
    const align = useCorrectionAlignment({ editor, client: mocked.client, dictationId: D, seek });
    return { ...mocked, editor, seek, align };
}

describe('correction alignment ui', () => {
    it('loads links and seeks to the word at the selection', async () => {
        const { align, seek, calls } = await make();
        expect((await align.refresh()).ok).toBe(true);
        expect(align.status.value).toBe('ready');
        expect(calls).toContain('GET ' + base + '/correction');
        const start = TEXT.indexOf('unauffa');
        align.setSelection(start + 2, start + 2);
        const res = align.seekToSelection();
        expect(res.ok).toBe(true);
        expect(res.audioRevisionId).toBe(AUDIO);
        expect(seek).toHaveBeenCalledWith(1);
        expect(align.currentWord.value).toEqual({ id: 'L2', start, end: start + 'unauffällig'.length });
    });

    it('click-to-seek and playback-to-highlight agree for emoji and combining marks', async () => {
        const { align, seek } = await make();
        await align.refresh();
        for (const seg of align.segments.value.filter((s) => s.aligned)) {
            const res = align.seekToSegment(seg);
            expect(res.ok).toBe(true);
            const seconds = seek.mock.calls[seek.mock.calls.length - 1][0];
            const word = align.highlightAt(seconds);
            expect(word).toEqual({ id: seg.linkId, start: seg.start, end: seg.end });
            expect(TEXT.slice(word.start, word.end)).toBe(seg.text);
        }
        expect(align.highlightAt(0.25).start).toBe(0);
        expect(align.highlightAt(99)).toBeNull();
    });

    it('never splits an emoji or combining sequence when selecting', async () => {
        const { align } = await make();
        await align.refresh();
        const emoji = TEXT.indexOf('\u{1F600}');
        expect(align.setSelection(emoji + 1, emoji + 1)).toEqual({ start: emoji, end: emoji + 2 });
        const mark = TEXT.indexOf(String.fromCharCode(0x308));
        expect(align.setSelection(mark, mark)).toEqual({ start: mark - 1, end: mark + 1 });
        expect(align.seekToSelection().ok).toBe(true);
    });

    it('shows newly typed text as unaligned and shifts later anchors', async () => {
        const editor = mockEditor();
        const { align, seek } = await make(happy(), editor);
        await align.refresh();
        editor.text.value = TEXT.replace('heute', 'heute abends');
        const typed = align.segments.value.find((s) => s.text.includes('abends'));
        expect(typed.aligned).toBe(false);
        expect(typed.flagged).toBe(true);
        align.setSelection(editor.text.value.indexOf('abends'), editor.text.value.indexOf('abends') + 2);
        const refused = align.seekToSelection();
        expect(refused).toMatchObject({ ok: false, code: 'unaligned' });
        expect(seek).not.toHaveBeenCalled();

        editor.text.value = 'Neu ' + TEXT;
        const shifted = align.segments.value.find((s) => s.text === 'Befund');
        expect(shifted.aligned).toBe(true);
        expect(shifted.start).toBe(4);
        expect(align.segments.value[0]).toMatchObject({ aligned: false, flagged: true, text: 'Neu ' });
        // A word edited in place loses its anchor until the next save.
        editor.text.value = TEXT.replace('heute', 'heutx');
        expect(align.segments.value.find((s) => s.text.includes('heutx')).aligned).toBe(false);
    });

    it('marks anchors unavailable when the audio or text revision moved on', async () => {
        const editor = mockEditor();
        const { align } = await make(happy(), editor);
        await align.refresh();
        editor.audio.value = { status: 'ready', revisionId: '44444444-4444-4444-8444-444444444444' };
        expect(align.status.value).toBe('stale');
        expect(align.links.value).toEqual([]);
        expect(align.segments.value.every((s) => !s.aligned)).toBe(true);
        expect(align.segments.value.some((s) => s.unavailable)).toBe(true);
        expect(align.seekToSelection()).toMatchObject({ ok: false, code: 'stale' });
        expect(align.highlightAt(0.1)).toBeNull();
        editor.audio.value = { status: 'ready', revisionId: AUDIO };
        expect(align.status.value).toBe('ready');
        editor.textRevisionId.value = '55555555-5555-4555-8555-555555555555';
        expect(align.status.value).toBe('stale');
    });

    it('maps 403, 404 and 409 to visible states and recovers on retry', async () => {
        for (const [outcome, status, code] of [['permission', 403, 'denied'], ['unknown', 404, 'notFound'], ['conflict', 409, 'stale']]) {
            const routes = happy({ ['GET ' + base + '/correction']: fail(outcome, status) });
            const { align } = await make(routes);
            const res = await align.refresh();
            expect(res).toMatchObject({ ok: false, code, message: LABELS[code] });
            expect(align.status.value).toBe('error');
            expect(align.error.value.code).toBe(code);
            expect(align.seekToSelection().ok).toBe(false);
        }
        let n = 0;
        const flaky = happy({ ['GET ' + base + '/correction']: () => (++n === 1 ? fail('network', 0) : ok({ dictation_id: D, text_revision_id: REV, text: TEXT, links: wordLinks(TEXT) })) });
        const { align } = await make(flaky);
        expect((await align.refresh()).code).toBe('failed');
        expect(align.status.value).toBe('error');
        expect((await align.refresh()).ok).toBe(true);
        expect(align.status.value).toBe('ready');
        expect(align.error.value).toBeNull();
    });

    it('reports a missing player and ignores a superseded load after dispose', async () => {
        const mocked = mockClient(happy());
        const align = useCorrectionAlignment({ editor: mockEditor(), client: mocked.client, dictationId: D });
        await align.refresh();
        align.setSelection(0, 0);
        expect(align.seekToSelection()).toMatchObject({ ok: false, code: 'noSeek' });
        const element = { currentTime: 0 };
        const withElement = useCorrectionAlignment({ editor: mockEditor(), client: mocked.client, dictationId: D, audioElement: element });
        await withElement.refresh();
        withElement.setSelection(0, 0);
        expect(withElement.seekToSelection().ok).toBe(true);
        expect(element.currentTime).toBe(0);
        const pending = withElement.refresh();
        withElement.dispose();
        expect(await pending).toMatchObject({ ok: false, code: 'cancelled' });
        expect(withElement.currentWord.value).toBeNull();
    });

    it('exports a component with labelled, keyboard-operable markup', () => {
        expect(CorrectionAlignment.name).toBe('ClinicCorrectionAlignment');
        expect(CorrectionAlignment.template).toContain('role="button" tabindex="0"');
        expect(CorrectionAlignment.template).toContain('@keydown.enter.prevent');
        expect(CorrectionAlignment.template).toContain('aria-live="polite"');
        expect(CorrectionAlignment.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        const app = { component: vi.fn() };
        expect(registerCorrectionAlignment(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-alignment', CorrectionAlignment);
    });
});
