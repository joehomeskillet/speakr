import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClinicApiClient } from '../../../static/js/clinic/api-client.js';
import {
    createLeaseController, createLeaseStatusComponent, leaseActions, useLease,
} from '../../../static/js/clinic/lease-ui.js';

const ID = '22222222-2222-4222-8222-222222222222';
const LEASE = '33333333-3333-4333-8333-333333333333';
const BASE = `/dictations/${ID}/leases`;

const ok = (generation = 1, expires = 1_000_000 + 60_000) => ({
    outcome: 'ok', status: 200,
    data: { dictation_id: ID, lease_id: LEASE, generation, expires_at: new Date(expires).toISOString() },
});
const fail = (outcome, status, code) => ({ outcome, status, code, message: 'x' });

function setup(overrides = {}) {
    let clock = 1_000_000;
    const timers = [];
    const client = { request: vi.fn() };
    let n = 0;
    const controller = createLeaseController({
        client, dictationId: ID, now: () => clock,
        newKey: () => `key-${++n}`,
        setTimer: (fn) => { timers.push(fn); return timers.length; },
        clearTimer: () => {},
        ...overrides,
    });
    return { client, controller, timers, advance: (ms) => { clock += ms; } };
}

describe('lease ui', () => {
    it('acquires, shows self as holder with expiry, and allows writes', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValue(ok());
        const state = await controller.acquire();
        expect(client.request).toHaveBeenCalledWith(BASE, { method: 'POST', idempotencyKey: 'key-1' });
        expect(state).toMatchObject({ status: 'held', holder: 'self', writable: true, generation: 1 });
        expect(state.message).toMatch(/^Sie bearbeiten dieses Diktat bis \d{2}:\d{2} Uhr\.$/);
        expect(state.surface.tokens.background).toBe('--bg-success-light');
    });

    it('renews with lease proof and slides the expiry', async () => {
        const { client, controller, timers, advance } = setup();
        client.request.mockResolvedValueOnce(ok(1, 1_060_000));
        await controller.acquire();
        advance(30_000);
        client.request.mockResolvedValueOnce(ok(1, 1_090_000));
        await timers.at(-1)();
        expect(client.request).toHaveBeenLastCalledWith(`${BASE}/renew`, {
            method: 'POST', idempotencyKey: 'key-2', body: { lease_id: LEASE, generation: 1 },
        });
        advance(40_000);
        expect(controller.canWrite()).toBe(true);
    });

    it('disables writes immediately on 409 and keeps unsaved edits for explicit resolution', async () => {
        const { client, controller, timers } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        expect(controller.setUnsaved('Entwurf')).toBe(true);
        client.request.mockResolvedValueOnce(fail('conflict', 409, 'REVISION_CONFLICT'));
        await timers.at(-1)();
        const state = controller.getState();
        expect(state).toMatchObject({ status: 'lost', writable: false, hasUnsaved: true, conflict: true });
        expect(controller.setUnsaved('weiter')).toBe(false);
        expect(await controller.resolveConflict('discard')).toBeNull();
        expect(controller.getState().hasUnsaved).toBe(false);
    });

    it('re-applies kept edits only after the lease is held again', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        controller.setUnsaved('Entwurf');
        controller.notifyGeneration(2);
        expect(controller.canWrite()).toBe(false);
        client.request.mockResolvedValueOnce(fail('UNKNOWN', 423, 'LOCKED'));
        expect(await controller.resolveConflict('reapply')).toBeNull();
        expect(controller.getState()).toMatchObject({ status: 'foreign', hasUnsaved: true });
        client.request.mockResolvedValueOnce(ok(2));
        controller.getState();
        const taken = await controller.acquire();
        expect(taken.status).toBe('held');
        expect(await controller.resolveConflict('reapply')).toBeNull();
    });

    it('returns the edits on reapply when acquire succeeds', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        controller.setUnsaved('Entwurf');
        controller.notifyGeneration(5);
        client.request.mockResolvedValueOnce(ok(6));
        expect(await controller.resolveConflict('reapply')).toBe('Entwurf');
        expect(controller.getState().hasUnsaved).toBe(false);
    });

    it('treats a foreign holder (423) as read-only; takeover only when permitted', async () => {
        const denied = setup();
        denied.client.request.mockResolvedValue(fail('UNKNOWN', 423, 'LOCKED'));
        const state = await denied.controller.acquire();
        expect(state).toMatchObject({ status: 'foreign', holder: 'other', writable: false, canTakeOver: false });
        const calls = denied.client.request.mock.calls.length;
        await denied.controller.takeOver();
        expect(denied.client.request.mock.calls.length).toBe(calls);
        expect(denied.controller.getState().message).toBe('Übernahme ist nicht erlaubt.');

        const allowed = setup({ mayTakeOver: true });
        allowed.client.request.mockResolvedValueOnce(fail('UNKNOWN', 423, 'LOCKED'));
        await allowed.controller.acquire();
        expect(allowed.controller.getState().canTakeOver).toBe(true);
        allowed.client.request.mockResolvedValueOnce(ok(2));
        expect((await allowed.controller.takeOver()).status).toBe('held');
    });

    it('unauthorized renewal loses the lease', async () => {
        const { client, controller, timers } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        client.request.mockResolvedValueOnce(fail('permission', 403, 'FORBIDDEN'));
        await timers.at(-1)();
        expect(controller.getState()).toMatchObject({ status: 'lost', writable: false });
    });

    it('survives a network failure until local expiry, then loses the lease', async () => {
        const { client, controller, timers, advance } = setup();
        client.request.mockResolvedValueOnce(ok(1, 1_060_000));
        await controller.acquire();
        advance(30_000);
        client.request.mockResolvedValueOnce(fail('network', null, 'NETWORK'));
        await timers.at(-1)();
        expect(controller.getState()).toMatchObject({ status: 'held', writable: true, message: expect.stringMatching(/wiederholt/) });
        advance(31_000);
        expect(controller.canWrite()).toBe(false);
        await timers.at(-1)();
        expect(controller.getState().status).toBe('lost');
    });

    it('reuses the idempotency key after an unknown outcome and a stale generation from renew loses', async () => {
        const { client, controller, timers } = setup();
        client.request.mockResolvedValueOnce(fail('network', null, 'NETWORK'));
        await controller.acquire();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        expect(client.request.mock.calls[0][1].idempotencyKey).toBe('key-1');
        expect(client.request.mock.calls[1][1].idempotencyKey).toBe('key-1');
        client.request.mockResolvedValueOnce(ok(2));
        await timers.at(-1)();
        expect(controller.getState().status).toBe('lost');
    });

    it('releases: writes stop first, then the release call carries the proof', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        client.request.mockImplementationOnce(async () => {
            expect(controller.canWrite()).toBe(false);
            return { outcome: 'ok', status: 200, data: {} };
        });
        await controller.release();
        expect(client.request).toHaveBeenLastCalledWith(`${BASE}/release`, {
            method: 'POST', idempotencyKey: 'key-2', body: { lease_id: LEASE, generation: 1 },
        });
        expect(controller.getState().status).toBe('idle');
    });

    it.each(['ok', 'network', 'reject'])('blocks reacquisition until a deferred release settles: %s', async (outcome) => {
        const { client, controller, timers } = setup();
        client.request.mockResolvedValueOnce(ok());
        await controller.acquire();
        controller.setUnsaved('Entwurf');
        const states = [];
        controller.subscribe((state) => states.push(state));
        let settle;
        client.request.mockImplementationOnce(() => new Promise((resolve, reject) => {
            settle = () => outcome === 'reject'
                ? reject(new Error('release failed'))
                : resolve({ outcome, status: outcome === 'ok' ? 200 : null });
        }));
        const release = controller.release();
        const completed = outcome === 'reject'
            ? expect(release).rejects.toThrow('release failed') : release;
        expect(controller.getState()).toMatchObject({ status: 'releasing', writable: false, generation: null, hasUnsaved: true });
        expect(leaseActions(controller.getState())).toContainEqual({ id: 'acquire', label: 'Bearbeiten', disabled: true });
        expect(leaseActions(controller.getState()).find((action) => action.id === 'reapply').disabled).toBe(true);
        expect(controller.setUnsaved('weiter')).toBe(false);
        await controller.acquire();
        expect(await controller.resolveConflict('reapply')).toBeNull();
        await controller.release();
        await timers.at(-1)();
        expect(client.request.mock.calls.map(([path]) => path)).toEqual([BASE, `${BASE}/release`]);
        expect(client.request).toHaveBeenLastCalledWith(`${BASE}/release`, {
            method: 'POST', idempotencyKey: 'key-2', body: { lease_id: LEASE, generation: 1 },
        });
        settle();
        await completed;
        expect(states.map((state) => state.status)).toEqual(['releasing', 'idle']);
        expect(controller.getState()).toMatchObject({ status: 'idle', writable: false, hasUnsaved: true });
        expect(leaseActions(controller.getState())).toContainEqual({ id: 'acquire', label: 'Bearbeiten', disabled: false });
        client.request.mockResolvedValueOnce(ok(2));
        expect(await controller.resolveConflict('reapply')).toBe('Entwurf');
        expect(controller.getState()).toMatchObject({ status: 'held', writable: true, generation: 2 });
        expect(client.request.mock.calls.map(([path]) => path)).toEqual([BASE, `${BASE}/release`, BASE]);
        controller.dispose();
    });

    it('rejects a malformed grant', async () => {
        const { client, controller } = setup();
        client.request.mockResolvedValue({ outcome: 'ok', status: 200, data: { generation: 'x' } });
        expect((await controller.acquire()).status).toBe('error');
    });

    it('offers keyboard-operable native buttons per state', () => {
        const base = { status: 'idle', canTakeOver: false, conflict: false };
        expect(leaseActions(base).map((a) => a.id)).toEqual(['acquire']);
        expect(leaseActions({ ...base, status: 'held' }).map((a) => a.id)).toEqual(['release']);
        expect(leaseActions({ ...base, status: 'foreign', canTakeOver: true }).map((a) => a.id)).toEqual(['takeOver']);
        expect(leaseActions({ ...base, status: 'lost', conflict: true }).map((a) => a.id)).toEqual(['reapply', 'discard']);
        expect(leaseActions({ ...base, status: 'acquiring' })[0].disabled).toBe(true);
    });

    it('exports a composable and component with a live region and token-only styling', () => {
        const shallowRef = (value) => ({ value });
        const { state, run } = useLease({ client: { request: vi.fn() }, dictationId: ID }, { shallowRef });
        expect(state.value.status).toBe('idle');
        expect(typeof run.acquire).toBe('function');
        const component = createLeaseStatusComponent({ computed: (fn) => ({ get value() { return fn(); } }) });
        expect(component.template).toContain('aria-live="polite"');
        expect(component.template).toContain('type="button"');
        expect(component.template).toContain('role="alert"');
        const source = readFileSync(new URL('../../../static/js/clinic/lease-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});

describe('lease ui with the real api client', () => {
    let original;
    beforeEach(() => {
        original = { window: global.window, document: global.document, fetch: global.fetch };
        global.document = { querySelector: vi.fn(() => null), cookie: '' };
        global.window = { csrfManager: { refreshToken: vi.fn(async () => 'csrf-one') }, originalFetch: undefined };
    });
    afterEach(() => {
        Object.assign(global, original);
        vi.restoreAllMocks();
    });

    it('maps a 423 LOCKED problem to foreign and a grant to held', async () => {
        const locked = { ok: false, status: 423, text: async () => JSON.stringify({ code: 'LOCKED', status: 423 }) };
        const grant = { ok: true, status: 200, text: async () => JSON.stringify(ok().data) };
        global.fetch = vi.fn().mockResolvedValueOnce(locked).mockResolvedValueOnce(grant);
        const controller = createLeaseController({
            client: createClinicApiClient(), dictationId: ID, now: () => 1_000_000,
            newKey: () => '44444444-4444-4444-8444-444444444444', setTimer: () => 1, clearTimer: () => {},
        });
        expect((await controller.acquire()).status).toBe('foreign');
        expect((await controller.acquire()).status).toBe('held');
        const [url, init] = global.fetch.mock.calls[1];
        expect(url).toBe(`/api/clinic/v1/dictations/${ID}/leases`);
        expect(init.headers['Idempotency-Key']).toBe('44444444-4444-4444-8444-444444444444');
    });
});
