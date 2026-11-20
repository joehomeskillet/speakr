import { describe, expect, it, vi } from 'vitest';
import { createAssignment } from '../../../static/js/clinic/assign-ui.js';
import { flattenGroups, WorklistView } from '../../../static/js/clinic/compose-worklist.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const candidates = [{ id: 2, display_name: 'qa-visual' }, { id: 3, display_name: 'Leitung' }];
function fixture(result = ok({ etag: '"d:2"' })) {
    const client = { request: vi.fn(async (path, options) => options?.method === 'POST' ? result
        : path.endsWith('/candidates') ? ok(candidates) : ok({ etag: '"d:1"' })) };
    const onAssigned = vi.fn();
    const c = createAssignment({ client, dictationId: 'd', currentUser: { id: 3, role: 'typist' }, onAssigned });
    return { c, client, onAssigned };
}

describe('W13 assignment', () => {
    it('uses real routes, server ETag and one typed recipient; only confirms after success', async () => {
        const { c, client, onAssigned } = fixture();
        expect(c.state.allowed).toBe(false);
        await c.load();
        c.search('QA-VISUAL');
        expect(c.choices()).toEqual([candidates[0]]);
        expect(c.state.selected).toBe('2');
        expect(await c.assign()).toBe(true);
        expect(client.request).toHaveBeenLastCalledWith('/dictations/d/assignment', {
            method: 'POST', body: { assignee_user_id: 2 }, ifMatch: '"d:1"', idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/),
        });
        expect(c.state.message).toBe('Zugewiesen an: qa-visual');
        expect(onAssigned).toHaveBeenCalledOnce();
    });
    it('resolves optional legacy IDs only among permitted candidates and rejects partial names', async () => {
        const { c, client } = fixture();
        await c.load();
        c.search('2');
        expect(c.state.selected).toBe('2');
        c.search('qa');
        expect(c.choices()).toHaveLength(1);
        expect(c.state.selected).toBe('');
        expect(await c.assign('99')).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(2);
    });
    it.each([403, 404, 409, 503])('hides actions on candidate HTTP %i, even with a forged role', async (status) => {
        const client = { request: vi.fn(async () => ({ status, outcome: 'permission' })) };
        const c = createAssignment({ client, dictationId: 'd', currentUser: { id: 3, role: 'lead' } });
        await c.load();
        expect(c.state.allowed).toBe(false);
        expect(c.canClaim()).toBe(false);
        expect(await c.assign(3)).toBe(false);
    });
    it('offers self assignment only to an authorized non-admin candidate', async () => {
        const { c, client } = fixture();
        await c.load();
        expect(c.canClaim()).toBe(true);
        for (const currentUser of [{ id: 99, role: 'typist' }, { id: 3, role: 'admin' }]) {
            const other = createAssignment({ client, dictationId: 'd', currentUser });
            await other.load();
            expect(other.canClaim()).toBe(false);
        }
    });
    it('invalidates stale permission/version and keeps errors visible without success', async () => {
        for (const outcome of ['permission', 'conflict']) {
            const { c, onAssigned } = fixture({ outcome });
            await c.load();
            expect(await c.assign(2)).toBe(false);
            expect(c.state.allowed).toBe(false);
            expect(c.state.etag).toBeNull();
            expect(c.state.error).not.toBe('');
            expect(onAssigned).not.toHaveBeenCalled();
        }
    });
    it('keeps the idempotency receipt on unknown outcome and blocks concurrent submits', async () => {
        const { c, client } = fixture({ outcome: 'network' });
        await c.load();
        const pending = c.assign(2);
        expect(await c.assign(2)).toBe(false);
        await pending;
        await c.assign(2);
        expect(client.request.mock.calls[2][1]).toEqual(client.request.mock.calls[3][1]);
        expect(c.state.message).toBe('');
    });
    it('ignores late reads after unmount and late writes cannot refresh another row', async () => {
        const { c, client, onAssigned } = fixture();
        await c.load();
        let finish;
        client.request.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
        const write = c.assign(2);
        c.dispose();
        finish(ok({ etag: '"d:2"' }));
        await write;
        expect(onAssigned).not.toHaveBeenCalled();
        const reads = [];
        client.request.mockImplementation(() => new Promise((resolve) => reads.push(resolve)));
        const read = c.load();
        c.dispose();
        reads[0](ok(candidates));
        reads[1](ok({ etag: '"d:2"' }));
        await read;
        expect(c.state.allowed).toBe(false);
    });
    it('mounts in the selected detail and calls unassigned groups Klärliste', () => {
        expect(WorklistView.template).toContain(':is="parts.Assignment"');
        expect(WorklistView.template).toContain('@assigned="c.assignmentDone"');
        expect(flattenGroups([{ path: [null], children: [], rows: [] }], ['assignee_user_id'])[0].label).toBe('Klärliste');
    });
});
