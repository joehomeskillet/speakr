import { describe, expect, it, vi } from 'vitest';
import {
    LABELS, createPlaybackHighlighter, useCorrectionAlignment,
} from '../../../static/js/clinic/correction-alignment-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const AUDIO = '33333333-3333-4333-8333-333333333333';
const base = '/dictations/' + D;
const ok = (data) => ({ outcome: 'ok', status: 200, data });

// Synthetic dictation: N words "w0 w1 ...", 400 ms each with a 100 ms pause after every word.
function synthetic(n) {
    const words = Array.from({ length: n }, (_, i) => 'w' + i);
    const text = words.join(' ');
    const links = [];
    let at = 0;
    words.forEach((word, i) => {
        links.push({ id: 'L' + i, origin: 'asr', text_start: at, text_end: at + word.length, audio_start_ms: i * 500, audio_end_ms: i * 500 + 400 });
        at += word.length + 1;
    });
    return { text, links };
}

function mockEditor(text, extra) {
    return {
        text: { value: text },
        textRevisionId: { value: REV },
        audio: { value: { status: 'ready', revisionId: AUDIO } },
        ...(extra || {}),
    };
}

async function make(n, links) {
    const data = synthetic(n);
    const editor = mockEditor(data.text);
    const client = {
        request: vi.fn(async (path) => {
            if (path === base + '/correction') return ok({ dictation_id: D, text_revision_id: REV, text: data.text, links: links || data.links, markers: [] });
            if (path === base + '/audio-revisions/current') return ok({ revision_id: AUDIO, segments: [] });
            return { outcome: 'error', status: 500 };
        }),
    };
    const align = useCorrectionAlignment({ editor, client, dictationId: D, seek: vi.fn() });
    await align.refresh();
    return { align, editor, data };
}

class FakeEl {
    constructor(id) { this.id = id; this.attrs = new Map(); this.writes = 0; this.scrolled = 0; this.isConnected = true; }
    setAttribute(k, v) { this.writes += 1; this.attrs.set(k, v); }
    removeAttribute(k) { this.writes += 1; this.attrs.delete(k); }
    scrollIntoView() { this.scrolled += 1; }
}

class FakeRoot {
    constructor(ids) {
        this.els = new Map(ids.map((id) => [id, new FakeEl(id)]));
        this.listeners = new Map();
        this.ownerDocument = { activeElement: null };
        this.lookups = 0;
    }
    querySelector(sel) { this.lookups += 1; return this.els.get(/data-link="(.*)"/.exec(sel)[1]) || null; }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    removeEventListener(name) { this.listeners.delete(name); }
    active() { return [...this.els.values()].filter((e) => e.attrs.get('data-active') === 'true').map((e) => e.id); }
    totalWrites() { return [...this.els.values()].reduce((sum, e) => sum + e.writes, 0); }
}

class FakeAudio {
    constructor() { this.currentTime = 0; this.paused = true; this.ended = false; this.playbackRate = 1; this.map = new Map(); }
    addEventListener(name, fn) { if (!this.map.has(name)) this.map.set(name, new Set()); this.map.get(name).add(fn); }
    removeEventListener(name, fn) { if (this.map.has(name)) this.map.get(name).delete(fn); }
    emit(name) { for (const fn of this.map.get(name) || []) fn({ type: name }); }
    listenerCount() { return [...this.map.values()].reduce((sum, set) => sum + set.size, 0); }
}

function frames() {
    const queue = new Map();
    let n = 0;
    return {
        requestFrame: (fn) => { n += 1; queue.set(n, fn); return n; },
        cancelFrame: (id) => { queue.delete(id); },
        pending: () => queue.size,
        step() { const [id, fn] = [...queue][0]; queue.delete(id); fn(); },
    };
}

async function rig(n = 5000, extra) {
    const m = await make(n, extra && extra.links);
    const root = new FakeRoot(m.data.links.map((l) => l.id));
    const audio = new FakeAudio();
    const raf = frames();
    const hl = createPlaybackHighlighter({ alignment: m.align, audio, root, ...raf, ...(extra && extra.options) });
    return { ...m, root, audio, raf, hl };
}

describe('cue index with 5000 synthetic cues', () => {
    it('builds the index once and answers lookups by binary search', async () => {
        const { align } = await make(5000);
        expect(align.indexBuilds()).toBe(0);
        expect(align.cueAt(0).id).toBe('L0');
        expect(align.indexBuilds()).toBe(1);
        for (let i = 0; i < 5000; i += 7) {
            expect(align.cueAt(i * 500 + 10).id).toBe('L' + i);
            expect(align.cueAt(i * 500 + 450)).toBeNull(); // silence between words
        }
        expect(align.cueAt(5000 * 500)).toBeNull(); // after the last cue
        expect(align.indexBuilds()).toBe(1);
        // Sanity bound only: the lookup is O(log n) by construction (binary search plus prefix-max stop).
        const started = Date.now();
        for (let i = 0; i < 50000; i += 1) align.cueAt((i % 5000) * 500 + 10);
        expect(Date.now() - started).toBeLessThan(1000);
        expect(align.indexBuilds()).toBe(1);
    });

    it('overlapping cues: latest start wins, shortest on equal start', async () => {
        const text = 'aa bb cc';
        const links = [
            { id: 'A', text_start: 0, text_end: 2, audio_start_ms: 0, audio_end_ms: 5000 },
            { id: 'B', text_start: 3, text_end: 5, audio_start_ms: 1000, audio_end_ms: 2000 },
            { id: 'C', text_start: 6, text_end: 8, audio_start_ms: 1000, audio_end_ms: 1500 },
        ];
        const editor = mockEditor(text);
        const client = { request: vi.fn(async (p) => (p.endsWith('/correction')
            ? ok({ dictation_id: D, text_revision_id: REV, text, links, markers: [] })
            : ok({ revision_id: AUDIO, segments: [] }))) };
        const align = useCorrectionAlignment({ editor, client, dictationId: D });
        await align.refresh();
        expect(align.cueAt(1200).id).toBe('C');
        expect(align.cueAt(1700).id).toBe('B');
        expect(align.cueAt(3000).id).toBe('A'); // long cue still covers after shorter ones ended
        expect(align.cueAt(5000)).toBeNull();
    });

    it('one tick touches at most two elements', async () => {
        const { hl, audio, root, align } = await rig();
        audio.paused = false;
        audio.currentTime = 1000.1; // inside L2000
        audio.emit('play');
        expect(root.active()).toEqual(['L2000']);
        const writesBefore = root.totalWrites();
        const lookupsBefore = root.lookups;
        audio.currentTime = 1000.45; // silence: only the old element is cleared
        audio.emit('timeupdate');
        expect(root.totalWrites() - writesBefore).toBe(1);
        audio.currentTime = 1000.55; // next word
        const before = root.totalWrites();
        audio.emit('timeupdate');
        expect(root.totalWrites() - before).toBe(1);
        audio.currentTime = 1002.1; // jump over a word: old cleared, new set
        const b2 = root.totalWrites();
        audio.emit('seeked');
        expect(root.totalWrites() - b2).toBeLessThanOrEqual(2);
        expect(root.active()).toEqual(['L2004']);
        expect(root.lookups - lookupsBefore).toBeLessThanOrEqual(3);
        // Repeated ticks on the same cue write nothing.
        const b3 = root.totalWrites();
        for (let i = 0; i < 100; i += 1) audio.emit('timeupdate');
        expect(root.totalWrites()).toBe(b3);
        expect(align.indexBuilds()).toBe(1);
        hl.dispose();
    });
});

describe('playback behaviour', () => {
    it('runs a single rAF loop while playing and stops it on pause', async () => {
        const { hl, audio, raf, root } = await rig(50);
        audio.paused = false;
        audio.currentTime = 0.1;
        audio.emit('play');
        audio.emit('playing');
        expect(raf.pending()).toBe(1);
        expect(root.active()).toEqual(['L0']);
        audio.currentTime = 0.6;
        raf.step();
        expect(root.active()).toEqual(['L1']);
        expect(raf.pending()).toBe(1);
        audio.paused = true;
        audio.emit('pause');
        expect(raf.pending()).toBe(0);
        hl.dispose();
    });

    it('pause holds the highlight at the paused time', async () => {
        const { hl, audio, root, raf } = await rig(50);
        audio.paused = false;
        audio.currentTime = 1.1;
        audio.emit('play');
        audio.currentTime = 1.2;
        audio.paused = true;
        audio.emit('pause');
        expect(root.active()).toEqual(['L2']);
        for (let i = 0; i < 5; i += 1) audio.emit('timeupdate');
        expect(root.active()).toEqual(['L2']);
        expect(raf.pending()).toBe(0);
        hl.dispose();
    });

    it('seek updates immediately, also while paused', async () => {
        const { hl, audio, root } = await rig(50);
        audio.currentTime = 10.1;
        audio.emit('seeking');
        expect(root.active()).toEqual(['L20']);
        audio.currentTime = 2.05;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L4']);
        expect(audio.paused).toBe(true);
        hl.dispose();
    });

    it('rate change keeps cue times', async () => {
        const { hl, audio, root, align } = await rig(50);
        audio.currentTime = 3.1;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L6']);
        const cue = { ...align.cueAt(3100) };
        audio.playbackRate = 2;
        audio.emit('ratechange');
        expect(root.active()).toEqual(['L6']);
        expect({ ...align.cueAt(3100) }).toEqual(cue);
        audio.playbackRate = 0.5;
        audio.emit('ratechange');
        expect(root.active()).toEqual(['L6']);
        expect(align.indexBuilds()).toBe(1);
        hl.dispose();
    });

    it('silence has no active cue; end clears', async () => {
        const { hl, audio, root, align } = await rig(50);
        audio.currentTime = 0.45; // pause between L0 and L1
        audio.emit('seeked');
        expect(root.active()).toEqual([]);
        expect(align.currentWord.value).toBeNull();
        audio.currentTime = 24.5;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L49']);
        audio.currentTime = 24.5;
        audio.ended = true;
        audio.emit('ended');
        expect(root.active()).toEqual([]);
        expect(align.currentWord.value).toBeNull();
        expect(hl.activeId()).toBeNull();
        hl.dispose();
    });

    it('source change clears the highlight', async () => {
        const { hl, audio, root } = await rig(10);
        audio.currentTime = 0.1;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L0']);
        audio.emit('emptied');
        expect(root.active()).toEqual([]);
        hl.dispose();
    });

    it('dispose removes listeners, the frame and the mark', async () => {
        const { hl, audio, root, raf } = await rig(10);
        audio.paused = false;
        audio.currentTime = 0.1;
        audio.emit('play');
        expect(audio.listenerCount()).toBeGreaterThan(0);
        hl.dispose();
        expect(audio.listenerCount()).toBe(0);
        expect(raf.pending()).toBe(0);
        expect(root.active()).toEqual([]);
    });
});

describe('binding and invalidation', () => {
    it('a text edit inside a word drops that cue; the index is rebuilt once', async () => {
        const { hl, audio, root, align, editor } = await rig(20);
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L2']);
        const builds = align.indexBuilds();
        editor.text.value = editor.text.value.replace('w2', 'wX2');
        hl.refresh();
        expect(root.active()).toEqual([]);
        expect(align.indexBuilds()).toBe(builds + 1);
        audio.currentTime = 1.6; // L3 shifted by one character but still valid
        audio.emit('seeked');
        expect(root.active()).toEqual(['L3']);
        expect(align.currentWord.value.start).toBe(editor.text.value.indexOf('w3'));
        hl.dispose();
    });

    it('a new text revision makes the binding stale: no highlight, honest notice', async () => {
        const { hl, audio, root, align, editor } = await rig(20);
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(root.active()).toEqual(['L2']);
        expect(hl.state()).toBe('active');
        editor.textRevisionId.value = '44444444-4444-4444-8444-444444444444';
        hl.refresh();
        expect(align.status.value).toBe('stale');
        expect(root.active()).toEqual([]);
        expect(hl.state()).toBe('unaligned');
        expect(hl.notice()).toBe(LABELS.notAligned);
        expect(LABELS.notAligned).toBe('nicht ausgerichtet');
        audio.currentTime = 2.1;
        audio.emit('seeked');
        expect(root.active()).toEqual([]);
        hl.dispose();
    });

    it('a foreign audio revision gives no highlight', async () => {
        const { hl, audio, root, align, editor } = await rig(20);
        editor.audio.value = { status: 'ready', revisionId: '55555555-5555-4555-8555-555555555555' };
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(align.status.value).toBe('stale');
        expect(root.active()).toEqual([]);
        expect(hl.notice()).toBe(LABELS.notAligned);
        hl.dispose();
    });

    it('segment links give a coarser highlight named as such', async () => {
        const text = 'Der Befund ist unauffaellig heute';
        const links = [{ id: 'S0', origin: 'asr', text_start: 0, text_end: 27, audio_start_ms: 0, audio_end_ms: 4000 }];
        const editor = mockEditor(text);
        const client = { request: vi.fn(async (p) => (p.endsWith('/correction')
            ? ok({ dictation_id: D, text_revision_id: REV, text, links, markers: [] })
            : ok({ revision_id: AUDIO, segments: [] }))) };
        const align = useCorrectionAlignment({ editor, client, dictationId: D });
        await align.refresh();
        const audio = new FakeAudio();
        const root = new FakeRoot(['S0']);
        const hl = createPlaybackHighlighter({ alignment: align, audio, root, ...frames() });
        audio.currentTime = 1;
        audio.emit('seeked');
        expect(root.active()).toEqual(['S0']);
        expect(align.granularityOf('S0')).toBe('segment');
        expect(hl.notice()).toBe(LABELS.segmentHighlight);
        hl.dispose();
    });

    it('word links carry no segment notice', async () => {
        const { hl, audio } = await rig(10);
        audio.currentTime = 0.1;
        audio.emit('seeked');
        expect(hl.notice()).toBe('');
        hl.dispose();
    });
});

describe('playback never touches the editor', () => {
    it('focus, selection, caret, text and dirty state stay unchanged', async () => {
        const dirty = { value: false };
        const focus = { name: 'editor-textarea' };
        const doc = { activeElement: focus };
        const selection = { start: 4, end: 9, setSelectionRange: vi.fn() };
        const m = await make(30);
        const editor = { ...m.editor, dirty, setText: vi.fn(), setSelection: vi.fn() };
        const align = useCorrectionAlignment({ editor, client: { request: m.align && (async (p) => (p.endsWith('/correction')
            ? ok({ dictation_id: D, text_revision_id: REV, text: m.data.text, links: m.data.links, markers: [] })
            : ok({ revision_id: AUDIO, segments: [] }))) }, dictationId: D });
        await align.refresh();
        const root = new FakeRoot(m.data.links.map((l) => l.id));
        root.ownerDocument = doc;
        const audio = new FakeAudio();
        const raf = frames();
        const hl = createPlaybackHighlighter({ alignment: align, audio, root, ...raf });
        const textBefore = editor.text.value;
        audio.paused = false;
        for (let i = 0; i < 20; i += 1) {
            audio.currentTime = i * 0.5 + 0.1;
            audio.emit(i % 5 === 0 ? 'seeking' : 'timeupdate');
        }
        audio.paused = true;
        audio.emit('pause');
        audio.ended = true;
        audio.emit('ended');
        expect(doc.activeElement).toBe(focus);
        expect(selection).toEqual({ start: 4, end: 9, setSelectionRange: selection.setSelectionRange });
        expect(selection.setSelectionRange).not.toHaveBeenCalled();
        expect(editor.text.value).toBe(textBefore);
        expect(dirty.value).toBe(false);
        expect(editor.setText).not.toHaveBeenCalled();
        expect(editor.setSelection).not.toHaveBeenCalled();
        expect(align.selection.value).toEqual({ start: 0, end: 0 });
        // Only data-active was ever written.
        for (const el of root.els.values()) expect([...el.attrs.keys()].every((k) => k === 'data-active')).toBe(true);
        hl.dispose();
    });
});

describe('follow-scroll', () => {
    it('scrolls the active word into view', async () => {
        const { hl, audio, root } = await rig(20);
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(root.els.get('L2').scrolled).toBe(1);
        hl.dispose();
    });

    it('stops after a manual scroll until playback restarts', async () => {
        const { hl, audio, root } = await rig(20);
        root.listeners.get('wheel')();
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(root.els.get('L2').scrolled).toBe(0);
        expect(root.active()).toEqual(['L2']); // highlight still follows
        audio.emit('play');
        audio.currentTime = 1.6;
        audio.emit('seeked');
        expect(root.els.get('L3').scrolled).toBe(1);
        hl.dispose();
    });

    it('does not scroll while an editable element has focus', async () => {
        const { hl, audio, root } = await rig(20);
        root.ownerDocument.activeElement = { tagName: 'TEXTAREA' };
        audio.currentTime = 1.1;
        audio.emit('seeked');
        expect(root.els.get('L2').scrolled).toBe(0);
        root.ownerDocument.activeElement = { tagName: 'DIV', isContentEditable: true };
        audio.currentTime = 1.6;
        audio.emit('seeked');
        expect(root.els.get('L3').scrolled).toBe(0);
        root.ownerDocument.activeElement = { tagName: 'BUTTON' };
        audio.currentTime = 2.1;
        audio.emit('seeked');
        expect(root.els.get('L4').scrolled).toBe(1);
        hl.dispose();
    });
});
