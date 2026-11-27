import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    RECORD_LEVEL_TEMPLATE, createRecordLevelController,
} from '../../../static/js/clinic/record-level-ui.js';

function fakeFrames() {
    const queue = new Map();
    let nextId = 0;
    return {
        requestAnimationFrame: vi.fn((callback) => {
            nextId += 1;
            queue.set(nextId, callback);
            return nextId;
        }),
        cancelAnimationFrame: vi.fn((id) => queue.delete(id)),
        pending: () => queue.size,
        callbacks: () => [...queue.values()],
        step() {
            const next = queue.entries().next().value;
            if (!next) throw new Error('no frame queued');
            const [id, callback] = next;
            queue.delete(id);
            callback();
        },
    };
}

function rig(read = () => 0) {
    const level = { value: 0 };
    const readLevel = vi.fn(read);
    const frames = fakeFrames();
    const controller = createRecordLevelController({
        level,
        readLevel,
        requestAnimationFrame: frames.requestAnimationFrame,
        cancelAnimationFrame: frames.cancelAnimationFrame,
    });
    return { controller, frames, level, readLevel };
}

describe('record level controller', () => {
    it('samples immediately and idempotently, then raises the meter for a speech-like level', () => {
        let input = 0;
        const { controller, frames, level, readLevel } = rig(() => input);
        controller.start();
        controller.start();
        expect(level.value).toBe(0);
        expect(readLevel).toHaveBeenCalledTimes(1);
        expect(frames.pending()).toBe(1);

        input = 0.62;
        frames.step();
        expect(level.value).toBe(0.62);
        expect(frames.pending()).toBe(1);
        controller.stop();
    });

    it('keeps digital silence at exactly zero across multiple frames', () => {
        const { controller, frames, level, readLevel } = rig(() => 0);
        controller.start();
        for (let frame = 0; frame < 4; frame += 1) {
            expect(level.value).toBe(0);
            frames.step();
        }
        expect(level.value).toBe(0);
        expect(readLevel).toHaveBeenCalledTimes(5);
        expect(frames.pending()).toBe(1);
        controller.stop();
    });

    it("sync('paused') drops to zero and stops frames, and recording resumes cleanly", () => {
        let input = 0.8;
        const { controller, frames, level, readLevel } = rig(() => input);
        controller.sync('ready');
        expect(readLevel).not.toHaveBeenCalled();
        expect(frames.pending()).toBe(0);

        controller.sync('recording');
        expect(level.value).toBe(0.8);
        expect(frames.pending()).toBe(1);
        controller.sync('paused');
        expect(level.value).toBe(0);
        expect(frames.pending()).toBe(0);

        input = 0.45;
        controller.sync('recording');
        expect(level.value).toBe(0.45);
        expect(frames.pending()).toBe(1);
        controller.stop();
    });

    it('uses idempotent stop cleanup and blocks late callbacks from sampling again', () => {
        const { controller, frames, level, readLevel } = rig(() => 0.7);
        controller.start();
        const [lateCallback] = frames.callbacks();
        controller.stop();
        controller.stop();
        expect(level.value).toBe(0);
        expect(frames.cancelAnimationFrame).toHaveBeenCalledTimes(1);
        expect(frames.pending()).toBe(0);

        const reads = readLevel.mock.calls.length;
        lateCallback();
        expect(readLevel).toHaveBeenCalledTimes(reads);
        expect(frames.pending()).toBe(0);
        expect(level.value).toBe(0);

        controller.start();
        lateCallback();
        expect(readLevel).toHaveBeenCalledTimes(reads + 1);
        expect(frames.pending()).toBe(1);
        controller.stop();
    });

    it('converts numeric values and clamps invalid or out-of-range samples', () => {
        const samples = [-0.5, 2, NaN, Infinity, -Infinity, '0.35', 'invalid'];
        const expected = [0, 1, 0, 0, 0, 0.35, 0];
        let index = 0;
        const { controller, frames, level } = rig(() => samples[index]);
        controller.start();
        for (let indexUnderTest = 0; indexUnderTest < samples.length; indexUnderTest += 1) {
            if (indexUnderTest > 0) {
                index = indexUnderTest;
                frames.step();
            }
            expect(level.value).toBe(expected[indexUnderTest]);
        }
        controller.stop();
    });
});

describe('record level template', () => {
    it('keeps the German caption, meter semantics, responsive token styling and browser-only source', () => {
        const template = RECORD_LEVEL_TEMPLATE;
        expect(template).toContain('data-testid="record-level"');
        expect(template).toContain('role="meter"');
        expect(template).toContain('aria-label="Browser-Aufnahmepegel"');
        expect(template).toContain('aria-valuemin="0"');
        expect(template).toContain('aria-valuemax="100"');
        expect(template).toContain(':aria-valuenow="Math.round(level * 100)"');
        expect(template).toContain('aria-describedby="record-level-caption"');
        expect(template).toContain('id="record-level-caption"');
        expect(template).toContain('data-testid="record-level-caption"');
        expect(template).toContain('Browser-Aufnahmepegel · nicht der Windows-Mixer');
        expect(template).toContain(":style=\"{ transform: 'scaleX(' + level + ')' }\"");
        expect(template).toContain('width: min(100%, 18rem)');
        for (const token of ['--ui-space-2', '--ui-border', '--brand-accent', '--ui-text-muted', '--ui-radius-sm']) {
            expect(template).toContain(`var(${token})`);
        }
        expect(template).not.toMatch(/<(?:button|input|select|textarea)\b/);

        const source = readFileSync(new URL('../../../static/js/clinic/record-level-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/\btransition\s*:/);
        expect(source).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|\bnavigator\.sendBeacon\b|\bWebSocket\b/);
    });
});
