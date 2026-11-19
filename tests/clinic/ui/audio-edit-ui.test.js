import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    audioEditActions, boundSelection, createAudioEditComponent, createAudioEditController, useAudioEdit,
    MIN_UNDO_DEPTH,
} from '../../../static/js/clinic/audio-edit-ui.js';

const ID = '22222222-2222-4222-8222-222222222222';
const REV1 = '44444444-4444-4444-8444-444444444441';
const REV2 = '44444444-4444-4444-8444-444444444442';
const SEG = '55555555-5555-4555-8555-555555555555';
const BASE = `/dictations/${ID}`;

const head = (rev = REV1, end = 10_000) => ({
    outcome: 'ok', status: 200,
    data: { revision_id: rev, segments: [{ source_id: SEG, start_ms: 0, end_ms: end }] },
});
const edited = (extra = {}) => ({ outcome: 'ok', status: 200, data: { invalidated_link_ids: [], ...extra } });
const fail = (outcome, status, code) => ({ outcome, status, code, message: 'x' });

function setup({ writable = true } = {}) {
    const client = { request: vi.fn() };
    const lease = { canWrite: () => writable, getState: () => ({ generation: 3 }) };
    let n = 0;
    const controller = createAudioEditController({
        client, dictationId: ID, lease, sampleRate: 1000,
        newKey: () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}`,
    });
    return { client, controller };
}

async function ready(opts) {
    const s = setup(opts);
    s.client.request.mockResolvedValueOnce(head());
    await s.controller.load();
    return s;
}

describe('audio edit ui', () => {
    it('bounds a sample selection to the audio and whole milliseconds', () => {
        expect(boundSelection(-50, 99_999, 1000, 10_000)).toEqual({ startMs: 0, endMs: 10_000 });
        expect(boundSelection(8000, 2000, 16000, 10_000)).toEqual({ startMs: 125, endMs: 500 });
        expect(boundSelection(NaN, 5, 1000, 10_000)).toBeNull();
    });

    it('cuts the selection with revision and lease generation and previews the result', async () => {
        const { client, controller } = await ready();
        controller.select(2000, 3000);
        expect(controller.preview('cut')).toMatchObject({ durationMs: 9000 });
        expect(controller.preview('cut').url).toContain(`audio_revision_id=${REV1}`);
        client.request.mockResolvedValueOnce(edited());
        client.request.mockResolvedValueOnce(head(REV2, 9000));
        const state = await controller.cut();
        expect(client.request).toHaveBeenCalledWith(`${BASE}/audio-edits`, {
            method: 'POST', idempotencyKey: '00000000-0000-4000-8000-000000000001',
            body: { operation: 'cut', audio_revision_id: REV1, lease_generation: 3, start_ms: 2000, end_ms: 3000 },
        });
        expect(state).toMatchObject({ revisionId: REV2, durationMs: 9000, undoDepth: 1, canUndo: true, selection: null });
    });

    it('sends insert, overwrite and trim bodies', async () => {
        const { client, controller } = await ready();
        controller.select(4000, 4000);
        client.request.mockResolvedValue(head());
        client.request.mockResolvedValueOnce(edited());
        await controller.insert({ sourceSegmentId: SEG, durationMs: 500 });
        expect(client.request.mock.calls[1][1].body).toMatchObject({
            operation: 'insert', position_ms: 4000, source_segment_id: SEG, duration_ms: 500,
        });
        controller.select(100, 100);
        client.request.mockResolvedValueOnce(edited());
        await controller.trim();
        expect(client.request.mock.calls[3][1].body).toMatchObject({ operation: 'trim', position_ms: 100 });
        controller.select(0, 0);
        client.request.mockResolvedValueOnce(edited());
        await controller.overwrite({ sourceSegmentId: SEG });
        expect(client.request.mock.calls[5][1].body.operation).toBe('overwrite');
    });

    it('refuses invalid input and does not call the server', async () => {
        const { client, controller } = await ready();
        expect((await controller.cut()).message).toBe('Die Auswahl ist ungültig.');
        controller.select(1, 1);
        expect((await controller.insert({})).message).toBe('Die Auswahl ist ungültig.');
        expect(controller.preview('insert', { durationMs: 0 })).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('is read-only without a lease', async () => {
        const { client, controller } = await ready({ writable: false });
        controller.select(100, 200);
        const state = await controller.cut();
        expect(state).toMatchObject({ writable: false, canUndo: false });
        expect(state.message).toBe('Keine Bearbeitung ohne aktive Bearbeitungssperre.');
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('maps unauthorized and lock errors without changing the head', async () => {
        const { client, controller } = await ready();
        controller.select(100, 200);
        client.request.mockResolvedValueOnce(fail('permission', 403, 'FORBIDDEN'));
        let state = await controller.cut();
        expect(state.revisionId).toBe(REV1);
        expect(state.message).toBe('x');
        expect(state.undoDepth).toBe(0);
        client.request.mockResolvedValueOnce(fail('UNKNOWN', 423, 'LOCKED'));
        state = await controller.cut();
        expect(state.revisionId).toBe(REV1);
    });

    it('handles a stale head: reloads, clears selection, keeps the anchor warning', async () => {
        const { client, controller } = await ready();
        controller.select(100, 200);
        client.request.mockResolvedValueOnce(fail('conflict', 409, 'REVISION_CONFLICT'));
        client.request.mockResolvedValueOnce(head(REV2));
        const state = await controller.cut();
        expect(state).toMatchObject({ status: 'stale', revisionId: REV2, selection: null });
        expect(state.message).toMatch(/veraltet/);
        expect(state.surface.tokens.background).toBe('--bg-danger-light');
        expect(controller.acknowledgeStale().status).toBe('ready');
    });

    it('does not silently keep invalidated text anchors', async () => {
        const { client, controller } = await ready();
        controller.select(100, 200);
        client.request.mockResolvedValueOnce(edited({ invalidated_link_ids: ['a1', 'a2'] }));
        client.request.mockResolvedValueOnce(head(REV2));
        const state = await controller.cut();
        expect(state).toMatchObject({ needsAnchorReview: true, invalidatedLinkIds: ['a1', 'a2'] });
        expect(controller.acknowledgeAnchors().needsAnchorReview).toBe(false);
    });

    it('retries an unknown outcome with the same idempotency key, then uses a new key', async () => {
        const { client, controller } = await ready();
        controller.select(100, 200);
        client.request.mockResolvedValueOnce(fail('network', null, 'NETWORK'));
        await controller.cut();
        client.request.mockResolvedValueOnce(edited());
        client.request.mockResolvedValueOnce(head(REV2));
        await controller.cut();
        const keys = client.request.mock.calls.filter((c) => c[0].endsWith('/audio-edits')).map((c) => c[1].idempotencyKey);
        expect(keys[0]).toBe(keys[1]);
        controller.select(100, 200);
        client.request.mockResolvedValueOnce(edited());
        client.request.mockResolvedValueOnce(head());
        await controller.cut();
        const last = client.request.mock.calls.filter((c) => c[0].endsWith('/audio-edits')).at(-1)[1].idempotencyKey;
        expect(last).not.toBe(keys[0]);
    });

    it('supports at least 16 undo and redo steps', async () => {
        expect(MIN_UNDO_DEPTH).toBe(16);
        const { client, controller } = await ready();
        client.request.mockImplementation(async (path) => (path.endsWith('/audio-edits') ? edited() : head()));
        for (let i = 0; i < 16; i += 1) {
            controller.select(i, i + 1);
            await controller.cut();
        }
        expect(controller.getState().undoDepth).toBe(16);
        for (let i = 0; i < 16; i += 1) await controller.undo();
        expect(controller.getState()).toMatchObject({ undoDepth: 0, redoDepth: 16, canUndo: false, canRedo: true });
        expect(client.request.mock.calls.filter((c) => c[1] && c[1].body && c[1].body.operation === 'undo')).toHaveLength(16);
        for (let i = 0; i < 16; i += 1) await controller.redo();
        expect(controller.getState().undoDepth).toBe(16);
        expect((await controller.redo()).message).toBe('Nichts zum Wiederholen.');
    });

    it('exposes labelled, keyboard-reachable markup and tokens only', async () => {
        const { controller } = await ready();
        controller.select(100, 200);
        const actions = audioEditActions(controller.getState());
        expect(actions.map((a) => a.label)).toEqual([
            'Ausschneiden', 'Kürzen', 'Einfügen', 'Überschreiben', 'Rückgängig', 'Wiederholen']);
        expect(actions.find((a) => a.id === 'cut').disabled).toBe(false);
        const comp = createAudioEditComponent({ computed: (fn) => ({ get value() { return fn(); } }) });
        expect(comp.template).toContain('role="status" aria-live="polite"');
        expect(comp.template).toContain('<button v-for');
        expect(comp.template).toContain('aria-keyshortcuts');
        const source = readFileSync(new URL('../../../static/js/clinic/audio-edit-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage/);
    });

    it('wires into Vue and unsubscribes on unmount', async () => {
        let teardown;
        const V = { shallowRef: (v) => ({ value: v }), onBeforeUnmount: (fn) => { teardown = fn; } };
        const client = { request: vi.fn().mockResolvedValue(head()) };
        const lease = { canWrite: () => true, getState: () => ({ generation: 1 }) };
        const { state, controller } = useAudioEdit({ client, dictationId: ID, lease }, V);
        await controller.load();
        expect(state.value.revisionId).toBe(REV1);
        teardown();
        controller.select(1, 2);
        expect(state.value.selection).toBeNull();
    });
});
