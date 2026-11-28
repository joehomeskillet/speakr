import { describe, expect, it, vi } from 'vitest';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';
import { useCorrectionWaveform } from '../../../static/js/clinic/correction-waveform-ui.js';

const TEXT = 'text-1';
const AUDIO = 'audio-1';
const SOURCE = '/api/clinic/v1/dictations/d1/audio-revisions/audio-1/content';
const mark = (kind, extra = {}) => ({
    id: kind, kind, start_ms: 500, end_ms: 825, source: 'synthetic-fixture', run_id: 'run-1',
    text_revision_id: TEXT, audio_revision_id: AUDIO, confidence: null, ...extra,
});

function setup(evidence) {
    const snapshot = {
        text_revision_id: TEXT, audio_revision_id: AUDIO, recognition_evidence: evidence,
        recognition_spans: [{ id: 'legacy', kind: 'uncertain', start_ms: 10, end_ms: 100 }],
    };
    const editor = { textRevisionId: { value: TEXT }, audio: { value: { revisionId: AUDIO, status: 'ready' } } };
    const client = { request: vi.fn(async () => ({ outcome: 'ok', data: snapshot })) };
    const playRange = vi.fn(async () => true);
    const wave = useCorrectionWaveform({
        dictationId: 'd1', recordingId: AUDIO, editor, client, playRange, fetchFn: async () => ({ status: 404 }),
    });
    return { wave, editor, client, snapshot, playRange };
}

describe('sourced recognition marks', () => {
    it('uses explicit categories and ranges even when waveform peaks are absent', async () => {
        const { wave, playRange } = setup([
            mark('uncertain'), mark('command'), mark('unrecognized'), mark('recognition', { confidence: 0 }),
            mark('recognition', { id: 'unknown-confidence' }), mark('command', { id: 'unsourced', source: ' ' }),
            mark('command', { id: 'old', audio_revision_id: 'old' }), mark('command', { id: 'bad', end_ms: 400 }),
        ]);
        expect((await wave.load()).code).toBe('none');
        expect(wave.uncertainSpans.value.map((span) => [span.id, span.kind, span.confidence])).toEqual([
            ['uncertain', 'uncertain', null], ['command', 'command', null], ['unrecognized', 'unrecognized', null],
            ['recognition', 'recognition', 0], ['unknown-confidence', 'recognition', null],
        ]);
        for (const span of wave.uncertainSpans.value) expect(await wave.seekToSpan(span)).toEqual({ ok: true });
        expect(playRange).toHaveBeenCalledTimes(5);
        expect(playRange).toHaveBeenCalledWith(0.5, 0.825);
    });

    it('treats empty current evidence as unknown and ignores the legacy projection', async () => {
        const { wave, playRange } = setup([]);
        await wave.load();
        expect(wave.uncertainSpans.value).toEqual([]);
        expect(wave.uncertaintyKnown.value).toBe(false);
        expect(playRange).not.toHaveBeenCalled();
    });

    it('does not replay a mark superseded by a newer run on the same revisions', async () => {
        const { wave, snapshot, playRange } = setup([mark('command')]);
        await wave.load();
        const previous = wave.uncertainSpans.value[0];
        snapshot.recognition_evidence = [mark('command', { run_id: 'run-2' })];
        expect(await wave.seekToSpan(previous)).toEqual({ ok: false });
        expect(playRange).not.toHaveBeenCalled();
        expect(await wave.seekToSpan(wave.uncertainSpans.value[0])).toEqual({ ok: true });
    });

    it.each(['audio', 'text'])('discards evidence returned after the %s revision changes', async (revision) => {
        const { wave, editor, client, snapshot, playRange } = setup([mark('command')]);
        await wave.load();
        const previous = wave.uncertainSpans.value[0];
        let reply;
        client.request.mockImplementationOnce(() => new Promise((resolve) => { reply = resolve; }));
        const pending = wave.seekToSpan(previous);
        if (revision === 'audio') editor.audio.value = { revisionId: 'audio-2', status: 'ready' };
        else editor.textRevisionId.value = 'text-2';
        reply({ outcome: 'ok', data: snapshot });
        expect(await pending).toEqual({ ok: false });
        expect(wave.uncertainSpans.value).toEqual([]);
        expect(playRange).not.toHaveBeenCalled();
    });
});

describe('marked passage playback', () => {
    it('plays only on the active media and stops at the end of the passage', async () => {
        let now = 0;
        const c = createCorrectionViewController({
            dictationId: 'd1', currentUser: { id: 1 },
            modesOptions: { now: () => now, resumeIdleMs: 10, rewindMs: 2000 },
        });
        const audio = Object.assign(new EventTarget(), {
            nodeType: 1, currentSrc: 'https://fixture.test/old-audio', ownerDocument: { baseURI: 'https://fixture.test/' },
            currentTime: 0, duration: 4, paused: true,
            play() { this.paused = false; return Promise.resolve(); },
            pause() { this.paused = true; this.dispatchEvent(new Event('pause')); },
        });
        c.editor.textRevisionId.value = TEXT;
        c.editor.audio.value = { status: 'ready', revisionId: AUDIO, src: SOURCE };
        c.bindAudio(audio);
        expect(await c.playRange(0.5, 0.825)).toBe(false);
        audio.currentSrc = 'https://fixture.test' + SOURCE;
        expect(await c.playRange(0.5, 9)).toBe(false);
        c.togglePlayback();
        expect(c.modes.onKey()).toEqual({ kind: 'pause' });
        expect(await c.playRange(0.5, 0.825)).toBe(true);
        now = 20;
        expect(c.modes.tick()).toEqual({ kind: 'none' });
        expect(audio.currentTime).toBe(0.5);
        expect(audio.paused).toBe(false);
        audio.currentTime = 0.8;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(audio.paused).toBe(false);
        audio.currentTime = 0.9;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(audio.paused).toBe(true);
        expect(audio.currentTime).toBe(0.825);

        expect(await c.playRange(0.5, 0.825)).toBe(true);
        audio.currentTime = 1.0;
        audio.dispatchEvent(new Event('seeking'));
        audio.dispatchEvent(new Event('timeupdate'));
        expect(audio.paused).toBe(false);
        expect(audio.currentTime).toBe(1.0);

        audio.play = () => Promise.reject(new Error('test playback rejection'));
        expect(await c.playRange(0.5, 0.825)).toBe(false);
        audio.currentTime = 1.0;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(audio.currentTime).toBe(1.0);
        c.dispose();
    });
});
