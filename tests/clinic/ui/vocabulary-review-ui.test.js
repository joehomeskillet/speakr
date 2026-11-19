import { describe, expect, it, vi } from 'vitest';
import { createVocabularyReviewController } from '../../../static/js/clinic/vocabulary-review-ui.js';

const ID = '11111111-1111-4111-8111-111111111111';
const ok = (data) => ({ outcome: 'ok', status: 200, data });
const bad = (outcome, code) => ({ outcome, status: 0, code, message: outcome });

function setup(overrides) {
    const writes = [];
    global.localStorage = { setItem: (...a) => writes.push(a) };
    global.sessionStorage = { setItem: (...a) => writes.push(a) };
    const client = {
        request: vi.fn(async () => ok({ revision: 1, items: [{ id: ID, written_form: 'Metformin', spoken_form: 'metformin', operation: 'add', status: 'PENDING', revision: 1 }] })),
        command: vi.fn(async () => ok({ status: 'committed', revision: 2 })),
        switchContext: vi.fn(),
        ...overrides,
    };
    const c = createVocabularyReviewController({ client, scope: { type: 'user', id: 7 } });
    return { c, client, writes };
}

describe('vocabulary review ui', () => {
    it('loads pending items and accepts for user via the parity command', async () => {
        const { c, client, writes } = setup();
        await c.load();
        expect(await c.acceptUser(ID)).toBe(true);
        const [body, opts] = client.command.mock.calls[0];
        expect(body).toMatchObject({ command: 'review_resource_change', expected_revision: 1, payload: { proposal_id: ID, decision: 'user' } });
        expect(opts.idempotencyKey.length).toBeGreaterThanOrEqual(8);
        expect(c.view().items[0].status).toBe('ACCEPTED_USER');
        expect(c.state.history).toHaveLength(1);
        expect(Object.isFrozen(c.state.history[0])).toBe(true);
        expect(writes).toEqual([]);
    });

    it('maps reject, defer and pool decisions', async () => {
        for (const [fn, decision] of [['reject', 'reject'], ['defer', 'defer'], ['acceptPool', 'pool']]) {
            const { c, client } = setup();
            await c.load();
            await c[fn](ID);
            expect(client.command.mock.calls[0][0].payload.decision).toBe(decision);
        }
    });

    it('keeps the item and blocks only pool promotion when unauthorized', async () => {
        const { c, client } = setup({ command: vi.fn(async () => bad('permission', 'POLICY_DENIED')) });
        await c.load();
        expect(await c.acceptPool(ID)).toBe(false);
        const v = c.view();
        expect(v.items).toHaveLength(1);
        expect(v.items[0].status).toBe('PENDING');
        expect(v.items[0].actions.acceptPool.enabled).toBe(false);
        expect(v.items[0].actions.acceptUser.enabled).toBe(true);
        expect(v.items[0].notice).toContain('Pool');
        expect(await c.acceptPool(ID)).toBe(false);
        expect(client.command).toHaveBeenCalledTimes(1);
    });

    it('flags a stale revision without accepting or dropping the item', async () => {
        const { c } = setup({ command: vi.fn(async () => bad('conflict', 'REVISION_CONFLICT')) });
        await c.load();
        expect(await c.acceptUser(ID)).toBe(false);
        const v = c.view();
        expect(v.stale).toBe(true);
        expect(v.items[0].status).toBe('PENDING');
        expect(v.items[0].stale).toBe(true);
        expect(v.items[0].actions.acceptUser.enabled).toBe(false);
        expect(c.state.history).toHaveLength(0);
    });

    it('retries a network failure with the same request and idempotency key', async () => {
        const command = vi.fn()
            .mockResolvedValueOnce(bad('network', 'NETWORK'))
            .mockResolvedValueOnce(ok({ status: 'committed', revision: 2 }));
        const { c } = setup({ command });
        await c.load();
        expect(await c.reject(ID)).toBe(false);
        expect(c.view().error.retryable).toBe(true);
        expect(await c.retry()).toBe(true);
        const [a, b] = command.mock.calls;
        expect(b[0].request_id).toBe(a[0].request_id);
        expect(b[1].idempotencyKey).toBe(a[1].idempotencyKey);
        expect(c.view().items[0].status).toBe('REJECTED');
    });

    it('undoes a decision and records it in the history', async () => {
        const request = vi.fn()
            .mockResolvedValueOnce(ok({ revision: 1, items: [{ id: ID, written_form: 'a', status: 'PENDING', revision: 1 }] }))
            .mockResolvedValueOnce(ok({ id: ID, status: 'PENDING', revision: 3 }));
        const { c } = setup({ request });
        await c.load();
        expect(await c.undo(ID)).toBe(false); // pending: nothing to undo
        await c.defer(ID);
        expect(await c.undo(ID)).toBe(true);
        expect(request.mock.calls[1][0]).toContain('/undo');
        expect(request.mock.calls[1][1].body).toEqual({ expected_revision: 2 });
        expect(c.state.history.map((h) => h.action)).toEqual(['defer', 'undo']);
        expect(c.view().items[0].status).toBe('PENDING');
    });

    it('exposes labels, roles and keyboard navigation', async () => {
        const { c } = setup();
        await c.load();
        const v = c.view();
        expect(v.labels.acceptPool).toBe('Für den Pool übernehmen');
        expect(v.a11y.alert.live).toBe('assertive');
        expect(c.onKeydown({ key: 'ArrowDown' }, ID)).toBe(ID);
        expect(v.items[0].surface.status).toBe('pending');
    });

    it('drops state and reloads on context switch', async () => {
        const { c, client } = setup();
        await c.load();
        await c.defer(ID);
        await c.switchContext({ accountId: 'b' });
        expect(client.switchContext).toHaveBeenCalled();
        expect(c.state.history).toHaveLength(0);
    });
});
