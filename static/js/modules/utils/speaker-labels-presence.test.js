import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { hasSpeakerLabels } from './speaker-labels.js';

describe('hasSpeakerLabels', () => {
    it('is false for segments without a speaker', () => {
        expect(hasSpeakerLabels([{ sentence: 'a' }, { speaker: null, sentence: 'b' }, { speaker: '', sentence: 'c' }])).toBe(false);
    });

    it('is false for the Unknown Speaker placeholder of older transcripts', () => {
        expect(hasSpeakerLabels([{ speaker: 'Unknown Speaker' }, { speaker: 'UNKNOWN_SPEAKER' }])).toBe(false);
    });

    it('is true once a segment has a speaker, generic or named', () => {
        expect(hasSpeakerLabels([{ speaker: null }, { speaker: 'SPEAKER_00' }])).toBe(true);
        expect(hasSpeakerLabels([{ speaker: 'Dr. Meier' }])).toBe(true);
    });

    it('is false for anything that is not a list', () => {
        expect(hasSpeakerLabels(null)).toBe(false);
        expect(hasSpeakerLabels(undefined)).toBe(false);
    });
});

describe('read view timestamps', () => {
    const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
    const panels = [
        '../../../../templates/components/detail/desktop-transcription-panel.html',
        '../../../../templates/components/detail/mobile-transcript-panel.html',
    ];

    it('shows the segment timestamp of a transcript without speakers unconditionally', () => {
        for (const panel of panels) {
            const html = read(panel);
            const span = html.match(/<span[^>]*data-segment-timestamp[^>]*>/);
            expect(span, panel).not.toBeNull();
            expect(span[0]).not.toContain('v-if');
        }
    });
});
