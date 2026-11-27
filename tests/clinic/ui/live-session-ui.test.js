import { describe, expect, it, vi } from 'vitest';
import {
    createLiveSessionComponent, createLiveSessionController, liveActions, phraseView, useLiveSession,
} from '../../../static/js/clinic/live-session-ui.js';

const DID = '22222222-2222-4222-8222-222222222222';
const SID = 'sess-1';
const PHRASES = `/api/clinic/v1/live/sessions/${SID}/phrases`;
const RESUME = `/api/clinic/v1/live/dictations/${DID}/resume`;

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: 'x' });

function setup(overrides = {}) {
    const client = { request: vi.fn() };
    let n = 0;
    const holder = { value: true };
    const controller = createLiveSessionController({
        client, dictationId: DID, newKey: () => `ev-${++n}`, canWrite: () => holder.value, ...overrides,
    });
    controller.attach({ session_id: SID, generation: 1, sequence: 0 });
    return { client, controller, holder };
}

const receipt = (seq, extra = {}) => ok({
    status: 'server_saved', phrase_id: `p${seq}`, session_id: SID, generation: 1, sequence: seq, event_id: `ev-${seq}`, ...extra,
});

describe('live session ui', () => {
    it('distinguishes partial, final_unsaved and server_saved with exact position', async () => {
        const { client, controller } = setup();
        controller.setPartial('Der Pat');
        expect(controller.getState().partial).toBe('Der Pat');
        const phrase = controller.finalize('Der Patient ist stabil.', { audio_b64: 'AAAA', text_hash: 'h' });
        expect(phrase).toMatchObject({ sessionId: SID, generation: 1, sequence: 1, state: 'final_unsaved' });
        let state = controller.getState();
        expect(state.partial).toBeNull();
        expect(state.phrases[0].view).toMatchObject({ label: 'Nicht gespeichert', durable: false, position: 'Satz 1, Generation 1' });
        expect(state.phrases[0].view.surface.tokens.background).toBe('--bg-warn-light');
        client.request.mockResolvedValue(receipt(1));
        state = await controller.save(1);
        expect(client.request).toHaveBeenCalledWith(PHRASES, {
            method: 'POST', idempotencyKey: 'ev-1',
            body: { audio_b64: 'AAAA', text_hash: 'h', generation: 1, sequence: 1, event_id: 'ev-1' },
        });
        expect(state.phrases[0].view).toMatchObject({ label: 'Gespeichert', durable: true });
        expect(state.checkpoint).toBe(1);
        expect(state.hasUnsaved).toBe(false);
    });

    it('does not mark a phrase durable when the receipt names another position', async () => {
        const { client, controller } = setup();
        controller.finalize('A', {});
        client.request.mockResolvedValue(receipt(1, { sequence: 2 }));
        const state = await controller.save(1);
        expect(state.phrases[0].state).toBe('final_unsaved');
        expect(state.checkpoint).toBe(0);
        expect(state.message).toBe('Die Bestätigung passt nicht zu diesem Satz.');
    });

    it('keeps the phrase unsaved on network failure and retries with the same event id', async () => {
        const { client, controller } = setup();
        controller.finalize('A', {});
        client.request.mockResolvedValueOnce(fail('network', null));
        let state = await controller.save(1);
        expect(state.phrases[0].state).toBe('final_unsaved');
        expect(state.message).toBe('Verbindung unterbrochen. Speichern wird wiederholt.');
        client.request.mockResolvedValueOnce(receipt(1));
        state = await controller.save(1);
        expect(client.request.mock.calls[1][1].idempotencyKey).toBe('ev-1');
        expect(state.phrases[0].state).toBe('server_saved');
    });

    it('blocks a stale holder from appending and never shows its text as durable', async () => {
        const { client, controller, holder } = setup();
        controller.finalize('A', {});
        holder.value = false;
        const state = await controller.save(1);
        expect(client.request).not.toHaveBeenCalled();
        expect(state).toMatchObject({ status: 'stale', writable: false });
        expect(state.phrases[0].view.durable).toBe(false);
        expect(controller.finalize('B', {})).toBeNull();
        expect(controller.setPartial('x')).toBe(false);
    });

    it('turns unsaved phrases stale on a 409 or policy denial', async () => {
        const a = setup();
        a.controller.finalize('A', {});
        a.client.request.mockResolvedValue(fail('conflict', 409));
        let state = await a.controller.save(1);
        expect(state.status).toBe('stale');
        expect(state.phrases[0]).toMatchObject({ state: 'stale' });
        expect(state.phrases[0].view.label).toBe('Veraltet');
        const b = setup();
        b.controller.finalize('A', {});
        b.client.request.mockResolvedValue(fail('permission', 403));
        state = await b.controller.save(1);
        expect(state.message).toBe('Keine Berechtigung für diese Live-Sitzung.');
        expect(state.phrases[0].state).toBe('stale');
    });

    it('resumes from the durable checkpoint and adopts stored phrases', async () => {
        const { client, controller } = setup();
        controller.finalize('A', {});
        client.request.mockResolvedValueOnce(receipt(1));
        await controller.save(1);
        controller.finalize('B', {});
        client.request.mockResolvedValueOnce(ok({
            session_id: SID, generation: 1, sequence: 2,
            events: [{ phrase_id: 'p2', generation: 1, sequence: 2, event_id: 'ev-2', status: 'server_saved' }],
        }));
        const state = await controller.resume('tab-1');
        expect(client.request).toHaveBeenLastCalledWith(RESUME, {
            method: 'POST', idempotencyKey: 'ev-3', body: { after_sequence: 1, client_id: 'tab-1' },
        });
        expect(state).toMatchObject({ status: 'live', checkpoint: 2, hasUnsaved: false });
        expect(state.phrases.map((p) => p.state)).toEqual(['server_saved', 'server_saved']);
        expect(state.phrases[1].text).toBe('B');
    });

    it('marks local unsaved phrases stale when resume lands on a new generation', async () => {
        const { client, controller } = setup();
        controller.finalize('A', {});
        client.request.mockResolvedValue(ok({ session_id: SID, generation: 2, sequence: 0, events: [] }));
        const state = await controller.resume();
        expect(state.generation).toBe(2);
        expect(state.phrases[0].state).toBe('stale');
        const next = controller.finalize('C', {});
        expect(next).toMatchObject({ generation: 2, sequence: 1 });
    });

    it('handles resume failures: denied is stale, network keeps the state', async () => {
        const { client, controller } = setup();
        controller.finalize('A', {});
        client.request.mockResolvedValueOnce(fail('network', null));
        let state = await controller.resume();
        expect(state.phrases[0].state).toBe('final_unsaved');
        expect(state.status).toBe('live');
        client.request.mockResolvedValueOnce(fail('permission', 403));
        state = await controller.resume();
        expect(state.status).toBe('stale');
        client.request.mockResolvedValueOnce(ok({ nonsense: true }));
        state = await controller.resume();
        expect(state.status).toBe('stale');
    });

    it('adopts a paused recovery without presenting a writable recording', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValue(ok({
            session_id: SID, generation: 1, sequence: 2, state: 'PAUSED', events: [],
        }));
        const state = await controller.resume();
        expect(state).toMatchObject({ status: 'paused', writable: false, checkpoint: 2, revision: 3 });
        expect(controller.finalize('C', {})).toBeNull();
        expect(controller.setPartial('x')).toBe(false);
        controller.applyServerState({ state: 'RECORDING', sequence: 2 });
        expect(controller.getState().status).toBe('live');
        expect(controller.finalize('C', {})).toMatchObject({ generation: 1, sequence: 3 });
    });

    it('holderLost makes unsaved text stale and clears the partial', () => {
        const { controller } = setup();
        controller.setPartial('t');
        controller.finalize('A', {});
        controller.setPartial('u');
        controller.holderLost();
        const state = controller.getState();
        expect(state).toMatchObject({ status: 'stale', partial: null, hasUnsaved: false });
    });

    it('rejects an unknown phrase state in the view and exposes labelled actions', () => {
        expect(() => phraseView({ state: 'bogus', sequence: 1, generation: 1 })).toThrow();
        expect(liveActions({ status: 'live', hasUnsaved: true, writable: false })).toEqual([
            { id: 'save', label: 'Erneut speichern', disabled: true },
            { id: 'resume', label: 'Ab Stand fortsetzen', disabled: false },
        ]);
    });

    it('component template has live region, labels and native buttons only', () => {
        const comp = createLiveSessionComponent({ computed: (fn) => ({ get value() { return fn(); } }) });
        expect(comp.template).toContain('aria-live="polite"');
        expect(comp.template).toContain('aria-label="Live-Transkription"');
        expect(comp.template).toContain('<button');
        expect(comp.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('composable wires state and disposes on unmount', async () => {
        const client = { request: vi.fn() };
        let unmount;
        const vue = { shallowRef: (v) => ({ value: v }), onBeforeUnmount: (fn) => { unmount = fn; } };
        const { state, controller } = useLiveSession({ client, dictationId: DID, newKey: () => 'ev-1' }, vue);
        controller.attach({ session_id: SID, generation: 1, sequence: 0 });
        expect(state.value.status).toBe('live');
        controller.finalize('A', {});
        client.request.mockResolvedValue(receipt(1));
        await controller.save(1);
        expect(state.value.phrases[0].state).toBe('server_saved');
        unmount();
        expect(controller.getState().phrases).toEqual([]);
    });
});
