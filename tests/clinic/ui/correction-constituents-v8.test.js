import { describe, expect, it, vi } from 'vitest';
import { CorrectionModes } from '../../../static/js/clinic/correction-modes-ui.js';
import { AudioMarkers } from '../../../static/js/clinic/audio-markers-ui.js';
import { CorrectionAlignment } from '../../../static/js/clinic/correction-alignment-ui.js';

describe('V8 correction constituents', () => {
    it('isolates radio names and hint IDs across simultaneously mounted settings views', async () => {
        const client = { request: vi.fn(async () => ({ outcome: 'ok', data: {} })) };
        const audio = { play: vi.fn(), pause: vi.fn(), seek: vi.fn(), position: () => 0, isPlaying: () => false };
        const first = CorrectionModes.setup({ audio, client });
        const second = CorrectionModes.setup({ audio, client });
        expect(first.groupId).not.toBe(second.groupId);
        await first.choose('type_while_playing');
        expect(first.mode.value).toBe('type_while_playing');
        expect(second.mode.value).toBe('stop_on_typing');
        expect(client.request).toHaveBeenCalledWith('/preferences/correction-mode', {
            method: 'PUT', body: { mode: 'type_while_playing' },
        });
        first.dispose();
        second.dispose();
    });

    it('uses scoped V8 surfaces while retaining read-only and keyboard contracts', () => {
        for (const component of [AudioMarkers, CorrectionModes, CorrectionAlignment]) {
            expect(component.template).toContain('v8-page');
            expect(component.template).toContain('aria-live="polite"');
            expect(component.template).not.toContain('v-html');
            expect(component.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        }
        expect(AudioMarkers.template).toContain('v-if="!m.historical"');
        expect(AudioMarkers.template).toContain(':disabled="busy || !writable"');
        expect(AudioMarkers.template).toContain('class="clinic-marker-label">{{ m.label }}');
        expect(CorrectionAlignment.template).toContain('@keydown.enter.prevent="onWord(seg)"');
        expect(CorrectionAlignment.template).toContain('@keydown.space.prevent="onWord(seg)"');
    });
});
