import { describe, expect, it, vi } from 'vitest';
import { createVocabularyReviewController, SessionVocabularyReview } from '../../../static/js/clinic/vocabulary-review-ui.js';

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

const WORD = { id: ID, written_form: 'Metformin', operation: 'ADD', status: 'PENDING',
    revision: 3, resource_type: 'vocabulary', can_accept_pool: false };
const COMMAND = { id: '22222222-2222-4222-8222-222222222222', written_form: 'Neuer Absatz',
    operation: 'MODIFY', status: 'DEFERRED', revision: 6, resource_type: 'voice_command', can_accept_pool: true };

function session(client) {
    const hooks = {};
    const emit = vi.fn();
    const vue = {
        ref: (value) => ({ value }), shallowRef: (value) => ({ value }),
        nextTick: () => Promise.resolve(),
        computed: (read) => ({ get value() { return read(); } }),
        onMounted: (fn) => { hooks.mount = fn; },
        onBeforeUnmount: (fn) => { hooks.unmount = fn; },
    };
    const ui = SessionVocabularyReview.setup({ client, userId: 7, vue }, { emit });
    const dialog = {
        open: false,
        ownerDocument: { activeElement: null },
        contains: vi.fn(() => true),
        showModal: vi.fn(() => { dialog.open = true; }),
        close: vi.fn(() => { dialog.open = false; ui.onClose(); }),
    };
    ui.dialog.value = dialog;
    ui.title.value = { focus: vi.fn() };
    return { ui, dialog, hooks, emit };
}

function pending() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

describe('session-end vocabulary review', () => {
    it('opens a native dialog and dismisses (including Escape) without a write', async () => {
        const { client } = setup();
        const { ui, dialog, hooks, emit } = session(client);
        await hooks.mount();
        expect(dialog.showModal).toHaveBeenCalledOnce();
        expect(ui.title.value.focus).toHaveBeenCalledOnce();
        expect(client.request.mock.calls[0][0]).toContain('scope_type=user&scope_id=7&status=OPEN');
        expect(SessionVocabularyReview.template).toContain('@cancel.prevent="dismiss"');
        expect(SessionVocabularyReview.template).toContain('@close="onClose"');
        ui.dismiss();
        ui.dismiss();
        expect(dialog.close).toHaveBeenCalledOnce();
        expect(emit.mock.calls).toEqual([['close']]);
        expect(client.command).not.toHaveBeenCalled();
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('loads every offset page before writes and decides with individual revisions and permissions', async () => {
        const page = pending();
        const { client } = setup({ request: vi.fn()
            .mockResolvedValueOnce(ok({ items: [WORD], revision: 60, next_cursor: '50' }))
            .mockReturnValueOnce(page.promise) });
        const { ui, hooks } = session(client);
        const loading = hooks.mount();
        await Promise.resolve();
        expect(client.request.mock.calls[1][0]).toContain('status=OPEN&cursor=50');
        expect(ui.view.value.items).toEqual([]);
        expect(await ui.run('acceptUser', WORD.id)).toBe(false);
        expect(client.command).not.toHaveBeenCalled();
        page.resolve(ok({ items: [COMMAND], revision: 61, next_cursor: null }));
        await loading;
        expect(ui.view.value.items.map(({ resourceType, canAcceptPool }) => [resourceType, canAcceptPool]))
            .toEqual([['vocabulary', false], ['voice_command', true]]);
        expect(await ui.run('acceptPool', WORD.id)).toBe(false);
        expect(await ui.run('defer', COMMAND.id)).toBe(false);
        expect(await ui.run('acceptUser', WORD.id)).toBe(true);
        expect(await ui.run('acceptPool', COMMAND.id)).toBe(true);
        expect(client.command.mock.calls.map(([body]) => [body.expected_revision, body.payload])).toEqual([
            [3, { proposal_id: WORD.id, decision: 'user' }], [6, { proposal_id: COMMAND.id, decision: 'pool' }],
        ]);
        expect([...ui.results.value.values()]).toEqual(['Für mich übernommen', 'Für das Team übernommen']);
    });

    it('blocks dismiss during a write and offers a deferred row again on the next mount', async () => {
        const write = pending();
        const { client } = setup({
            request: vi.fn().mockResolvedValueOnce(ok({ items: [WORD] }))
                .mockResolvedValueOnce(ok({ items: [{ ...WORD, status: 'DEFERRED', revision: 4 }] })),
            command: vi.fn().mockReturnValueOnce(write.promise),
        });
        const { ui, hooks, dialog, emit } = session(client);
        await hooks.mount();
        const deferring = ui.run('defer', WORD.id);
        ui.dismiss();
        expect(dialog.close).not.toHaveBeenCalled();
        expect(emit).not.toHaveBeenCalled();
        write.resolve(ok({ revision: 4 }));
        expect(await deferring).toBe(true);
        expect(client.command.mock.calls[0][0]).toMatchObject({ expected_revision: 3, payload: { decision: 'defer' } });
        expect(ui.results.value.get(WORD.id)).toBe('Für später zurückgestellt');
        ui.dismiss();
        expect(emit.mock.calls).toEqual([['close']]);
        const next = session(client);
        await next.hooks.mount();
        expect(next.ui.view.value.items[0]).toMatchObject({ id: WORD.id, status: 'DEFERRED' });
        expect(next.ui.results.value.size).toBe(0);
    });

    it('retries the same command after a network failure and reloads a conflict before deciding again', async () => {
        const { client } = setup({
            request: vi.fn().mockResolvedValueOnce(ok({ items: [WORD] }))
                .mockResolvedValueOnce(ok({ items: [{ ...WORD, revision: 8 }] })),
            command: vi.fn().mockResolvedValueOnce(bad('network', 'NETWORK'))
                .mockResolvedValueOnce(bad('conflict', 'REVISION_CONFLICT')).mockResolvedValueOnce(ok({ revision: 9 })),
        });
        const { ui, hooks } = session(client);
        await hooks.mount();
        expect(await ui.run('acceptUser', WORD.id)).toBe(false);
        expect(ui.retryingWrite.value).toBe(true);
        expect(await ui.run('retry')).toBe(false);
        expect(client.command.mock.calls[1]).toEqual(client.command.mock.calls[0]);
        expect(ui.view.value.items[0].stale).toBe(true);
        expect(await ui.run('acceptUser', WORD.id)).toBe(false);
        expect(client.command).toHaveBeenCalledTimes(2);
        expect(await ui.run('load')).toBe(true);
        expect(await ui.run('acceptUser', WORD.id)).toBe(true);
        expect(client.command.mock.calls[2][0].expected_revision).toBe(8);
    });

    it('does not publish a partial page or guess a missing row revision; can reload to empty', async () => {
        const { client } = setup({ request: vi.fn()
            .mockResolvedValueOnce(ok({ items: [WORD], next_cursor: '50' }))
            .mockResolvedValueOnce(ok({ items: [{ ...COMMAND, revision: undefined }], revision: 6 }))
            .mockResolvedValueOnce(ok({ items: [] })) });
        const { ui, hooks } = session(client);
        await hooks.mount();
        expect(ui.view.value.ready).toBe(false);
        expect(ui.view.value.error).toMatchObject({ outcome: 'validation', itemId: null });
        expect(await ui.run('acceptUser', WORD.id)).toBe(false);
        expect(client.command).not.toHaveBeenCalled();
        expect(await ui.run('load')).toBe(true);
        expect(ui.view.value).toMatchObject({ ready: true, items: [], error: null });
    });

    it('stops loading pages after the dialog is unmounted', async () => {
        const first = pending();
        const { client } = setup({ request: vi.fn().mockReturnValue(first.promise) });
        const { hooks } = session(client);
        const loading = hooks.mount();
        hooks.unmount();
        first.resolve(ok({ items: [WORD], next_cursor: '50' }));
        expect(await loading).toBe(false);
        expect(client.request).toHaveBeenCalledOnce();
    });

    it('requires confirmation before removing a team entry and shows the result', async () => {
        const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
        vi.stubGlobal('confirm', confirm);
        try {
            const { client } = setup({ request: vi.fn().mockResolvedValue(ok({ items: [
                { ...COMMAND, operation: 'DELETE', target_pool_name: 'Schreibdienst' },
            ] })) });
            const { ui, hooks } = session(client);
            await hooks.mount();
            expect(await ui.run('acceptPool', COMMAND.id)).toBe(false);
            expect(client.command).not.toHaveBeenCalled();
            expect(await ui.run('acceptPool', COMMAND.id)).toBe(true);
            expect(confirm).toHaveBeenCalledWith('«Neuer Absatz» für das Team «Schreibdienst» entfernen?');
            expect(ui.results.value.get(COMMAND.id)).toBe('Für das Team entfernt');
        } finally {
            vi.unstubAllGlobals();
        }
    });
});
