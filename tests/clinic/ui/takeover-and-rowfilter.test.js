import { afterEach, describe, expect, it, vi } from 'vitest';

import { createLeaseController, leaseActions } from '../../../static/js/clinic/lease-ui.js';
import { useWorklistRefresh } from '../../../static/js/clinic/worklist-refresh-ui.js';

const leases = '/dictations/synthetic/leases';
const ok = (data) => ({ outcome: 'ok', data });

function openLease(mayTakeOver, fallback = false, holder = 2) {
    vi.stubGlobal('document', { getElementById: () => ({ dataset: { currentUserId: '1' } }) });
    const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValue(true);
    vi.stubGlobal('confirm', confirm);
    const request = vi.fn().mockResolvedValueOnce(ok({ held: true, holder_user_id: holder, may_take_over: mayTakeOver }));
    const lease = createLeaseController({
        client: { request }, dictationId: 'synthetic', mayTakeOver: fallback,
        newKey: () => 'synthetic-key', setTimer: () => null,
    });
    lease.subscribe(() => {});
    return { lease, request, confirm };
}

afterEach(() => vi.unstubAllGlobals());

describe('v8-141 lease takeover', () => {
    it('offers takeover on open and posts only after confirmation', async () => {
        const { lease, request, confirm } = openLease(true);
        await vi.waitFor(() => expect(leaseActions(lease.getState())).toContainEqual({
            id: 'takeOver', label: 'Übernehmen', disabled: false,
        }));
        expect(request).toHaveBeenCalledWith(leases);
        expect((await lease.takeOver()).status).toBe('foreign');
        expect(request).toHaveBeenCalledTimes(1);
        request.mockResolvedValueOnce(ok({
            lease_id: 'new-lease', generation: 2, expires_at: new Date(Date.now() + 60_000).toISOString(),
        }));
        expect(await lease.takeOver()).toMatchObject({ holder: 'self', writable: true, generation: 2 });
        expect(confirm).toHaveBeenCalledTimes(2);
        expect(request).toHaveBeenLastCalledWith(`${leases}/takeover`, {
            method: 'POST', body: { confirm: true }, idempotencyKey: 'synthetic-key',
        });
        lease.dispose();
    });

    it('honors an explicit server denial over the caller fallback', async () => {
        const { lease, request } = openLease(false, true);
        await vi.waitFor(() => expect(lease.getState()).toMatchObject({ status: 'foreign', canTakeOver: false }));
        await lease.takeOver();
        expect(request).toHaveBeenCalledTimes(1);
        lease.dispose();
    });

    it('uses the caller fallback when an older status response omits the capability', async () => {
        const { lease, request } = openLease(undefined, true);
        await request.mock.results[0].value;
        expect(lease.getState()).toMatchObject({ status: 'foreign', canTakeOver: true });
        lease.dispose();
    });

    it('does not offer takeover for an existing lease of the signed-in user', async () => {
        const { lease, request } = openLease(true, false, 1);
        await request.mock.results[0].value;
        expect(lease.getState()).toMatchObject({ status: 'idle', canTakeOver: false, writable: false });
        lease.dispose();
    });
});

describe('v8-141 server-scoped worklist', () => {
    const ownAssignedElsewhere = {
        dictation_id: 'synthetic', author_user_id: 1, assignee_user_id: 2, status: 'zu_schreiben',
    };

    it('keeps a typist’s own dictation returned by the server after assignment to another typist', async () => {
        const request = vi.fn().mockResolvedValue(ok({ items: [ownAssignedElsewhere] }));
        const worklist = useWorklistRefresh({ apiClient: { request }, role: 'typist', userId: 1 });
        await worklist.refresh();
        expect(worklist.rows.value).toEqual([ownAssignedElsewhere]);
        expect(worklist.totals.value.totalCount).toBe(1);
    });

    it('resolves upstream recording changes against the clinic rows endpoint', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true, status: 200,
            json: async () => ({
                changes: [{ type: 'upsert', recording: { id: 30, title: 'Upstream recording', status: 'COMPLETED' } }],
                next_cursor: 'new-cursor',
            }),
        }));
        const request = vi.fn().mockResolvedValue(ok({ items: [ownAssignedElsewhere] }));
        const worklist = useWorklistRefresh({
            apiClient: { request }, role: 'typist', userId: 1, initialCursor: 'cursor',
        });
        await worklist.pollChanges();
        expect(request).toHaveBeenCalledWith('/worklist/rows');
        expect(worklist.rows.value).toEqual([ownAssignedElsewhere]);
    });

    it('retries a failed clinic reload even after the change cursor advanced', async () => {
        const feed = vi.fn()
            .mockResolvedValueOnce({ changes: [{ type: 'delete', id: 30 }], next_cursor: 'new-cursor' })
            .mockResolvedValueOnce({ changes: [] });
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: feed }));
        const request = vi.fn()
            .mockResolvedValueOnce({ outcome: 'network' })
            .mockResolvedValueOnce(ok({ items: [ownAssignedElsewhere] }));
        const worklist = useWorklistRefresh({ apiClient: { request }, initialCursor: 'cursor' });
        expect((await worklist.pollChanges()).outcome).toBe('network');
        expect((await worklist.pollChanges()).outcome).toBe('ok');
        expect(request).toHaveBeenCalledTimes(2);
        expect(worklist.rows.value).toEqual([ownAssignedElsewhere]);
    });
});
