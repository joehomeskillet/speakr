import { describe, expect, it, vi } from 'vitest';
import { createContextBinding, isSyntheticCase } from '../../../static/js/clinic/context-bind-ui.js';
import { createDetailViewController } from '../../../static/js/clinic/compose-detail.js';

const ok = (data, status = 200) => ({ outcome: 'ok', status, data });
const denied = (status) => ({ outcome: 'permission', status });
const setup = (response = ok({ case_display: null })) => {
    const client = { request: vi.fn().mockResolvedValue(response) };
    const canWrite = vi.fn(() => true);
    const onBound = vi.fn();
    return { client, canWrite, onBound, c: createContextBinding({ client, dictationId: 'd1', canWrite, onBound }) };
};

describe('staging context binding', () => {
    it.each(['SYN-0001', 'SYN-12345678'])('accepts synthetic reference %s', (value) => expect(isSyntheticCase(value)).toBe(true));
    it.each(['', '0001', 'SYN-123', 'SYN-123456789', 'SYN-0001\n', ' SYN-0001', 'Patient Muster'])('rejects %s', (value) => expect(isSyntheticCase(value)).toBe(false));
    it.each([403, 404, 503])('hides simulation for HTTP %s without posting', async (status) => {
        const { c, client } = setup(denied(status));
        await c.load();
        c.state.input = 'SYN-0001';
        expect(c.state.available).toBe(false);
        expect(await c.bind()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
    });
    it('does not treat an unrelated 200 or network failure as capability', async () => {
        for (const res of [ok({}), { outcome: 'network' }]) {
            const { c } = setup(res);
            await c.load();
            expect(c.state.available).toBeFalsy();
        }
    });
    it('requires a held lease and valid input before posting', async () => {
        const { c, client, canWrite } = setup();
        await c.load();
        canWrite.mockReturnValue(false);
        c.state.input = 'SYN-0001';
        expect(await c.bind()).toBe(false);
        canWrite.mockReturnValue(true);
        c.state.input = 'invalid';
        expect(await c.bind()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
    });
    it('posts only case_reference and reloads confirmed state', async () => {
        const { c, client, onBound } = setup();
        await c.load();
        c.state.input = 'SYN-0001';
        client.request.mockResolvedValue(ok({ case_display: 'SYN-0001' }, 201));
        expect(await c.bind()).toBe(true);
        expect(client.request).toHaveBeenLastCalledWith('/dictations/d1/context', {
            method: 'POST', body: { case_reference: 'SYN-0001' },
        });
        expect(onBound).toHaveBeenCalledTimes(1);
        expect(c.state.input).toBe('');
        expect(c.state.error).toBe(false);
    });
    it.each([409, 423, 403, 503])('never reports a refused bind (%s) as success', async (status) => {
        const { c, client, onBound } = setup();
        await c.load();
        c.state.input = 'SYN-0001';
        client.request.mockResolvedValue(denied(status));
        expect(await c.bind()).toBe(false);
        expect(onBound).not.toHaveBeenCalled();
        expect(c.state.error).toBe(true);
    });
    it('ignores late capability after reset or disposal', async () => {
        const { c, client } = setup();
        let resolve;
        client.request.mockReturnValue(new Promise((r) => { resolve = r; }));
        const pending = c.load();
        c.reset();
        resolve(ok({ case_display: null }));
        await pending;
        expect(c.state.available).toBe(false);
        c.dispose();
        expect(await c.bind()).toBe(false);
    });
    it('blocks an uncertain POST until an explicit refresh', async () => {
        const { c, client, onBound } = setup();
        await c.load();
        c.state.input = 'SYN-0001';
        client.request.mockResolvedValue({ outcome: 'network' });
        expect(await c.bind()).toBe(false);
        expect(c.state.blocked).toBe(true);
        expect(await c.bind()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(2);
        await c.refresh();
        expect(onBound).toHaveBeenCalledTimes(1);
    });
    it('stops writes immediately when the lease is lost after loading', async () => {
        const { c, canWrite, client } = setup();
        await c.load();
        expect(c.writable()).toBe(true);
        canWrite.mockReturnValue(false);
        c.state.input = 'SYN-0001';
        expect(c.writable()).toBe(false);
        expect(await c.bind()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
    });
    it('blocks double submits and late success after disposal', async () => {
        const { c, client, onBound } = setup();
        await c.load();
        let resolve;
        client.request.mockReturnValue(new Promise((r) => { resolve = r; }));
        c.state.input = 'SYN-0001';
        const pending = c.bind();
        expect(await c.bind()).toBe(false);
        c.dispose();
        resolve(ok({ case_display: 'SYN-0001' }, 201));
        expect(await pending).toBe(false);
        expect(onBound).not.toHaveBeenCalled();
    });
});

it('P03 reloads its Fall-ID and workflow revision after binding, then clears on refusal', async () => {
    let bound = false;
    let refused = false;
    const client = { request: vi.fn(async (path, options) => {
        if (path.endsWith('/detail')) return refused ? denied(404) : ok({ dictation_id: 'd1', revision: bound ? 2 : 1,
            case_display: bound ? 'SYN-0001' : null, allowed_actions: [] });
        if (path.endsWith('/context')) {
            if (options?.method === 'POST') bound = true;
            return ok({ case_display: bound ? 'SYN-0001' : null }, options?.method === 'POST' ? 201 : 200);
        }
        return ok({ lease_id: 'l1', generation: 1, expires_at: new Date(Date.now() + 60000).toISOString() });
    }) };
    const c = createDetailViewController({ client, dictationId: 'd1', currentUser: { id: 1 } });
    await c.start();
    expect(c.contextBinding.writable()).toBe(false);
    await c.leaseRun.acquire();
    await c.load();
    c.contextBinding.state.input = 'SYN-0001';
    expect(await c.contextBinding.bind()).toBe(true);
    expect(c.header().caseId).toBe('SYN-0001');
    expect(c.data.value.revision).toBe(2);
    refused = true;
    await c.load();
    expect(c.header()).toBeNull();
    expect(c.contextBinding.state.available).toBe(false);
    c.dispose();
});
