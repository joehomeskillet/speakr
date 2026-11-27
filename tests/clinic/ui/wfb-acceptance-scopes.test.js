import { describe, expect, it, vi } from 'vitest';
import { createAcceptancePolicyEditor } from '../../../static/js/clinic/acceptance-policy-ui.js';

const POOL = '11111111-1111-4111-8111-111111111111';
const ok = (data) => ({ outcome: 'ok', data });

function fixture(scopes = {}) {
    let stored = { version: 1, acceptance_required: true, approver_ids: [], ...scopes };
    const put = vi.fn(async (_path, options) => {
        stored = { ...stored, ...options.body, version: stored.version + 1 };
        for (const key of ['approver_pool_ids', 'deny_user_ids']) {
            if (!stored[key]?.length) delete stored[key];
        }
        return ok(stored);
    });
    const editor = createAcceptancePolicyEditor({ client: {
        switchContext: vi.fn(),
        request: async (path, options) => options ? put(path, options) : ok(stored),
    } });
    return { editor, put };
}

describe('acceptance policy scopes', () => {
    it('publishes a pool-only grant with an exclusion, reloads it, then explicitly clears both scopes', async () => {
        const { editor, put } = fixture();
        await editor.load('d1');
        editor.state.approverPoolIds.push(POOL);
        editor.state.denyUserIds.push(3);
        expect(await editor.save()).toEqual({ ok: true });
        expect(put).toHaveBeenLastCalledWith('/dictations/d1/acceptance-policy', {
            method: 'PUT', ifMatch: '1', body: {
                acceptance_required: true, approver_ids: [], approver_pool_ids: [POOL], deny_user_ids: [3],
            },
        });
        editor.reset();
        expect(editor.state).toMatchObject({ approverPoolIds: [], denyUserIds: [] });
        await editor.load('d1');
        expect(editor.state).toMatchObject({ version: 2, approverPoolIds: [POOL], denyUserIds: [3] });
        editor.state.approverPoolIds = [];
        editor.state.denyUserIds = [];
        editor.toggle(2);
        expect(await editor.save()).toEqual({ ok: true });
        expect(put.mock.lastCall[1]).toMatchObject({ ifMatch: '2', body: {
            approver_ids: [2], approver_pool_ids: [], deny_user_ids: [],
        } });
        await editor.load('d1');
        expect(editor.state).toMatchObject({ version: 3, approvers: [2], approverPoolIds: [], denyUserIds: [] });
    });

    it('keeps untouched scopes off the wire when editing direct approvers', async () => {
        const { editor, put } = fixture({ approver_pool_ids: [POOL], deny_user_ids: [3] });
        await editor.load('d1');
        editor.toggle(2);
        expect(await editor.save()).toEqual({ ok: true });
        expect(put.mock.lastCall[1].body).toEqual({ acceptance_required: true, approver_ids: [2] });
    });

    it.each([
        [{ outcome: 'permission' }, 'permission'],
        [{ outcome: 'validation', code: 'INVALID_POOL' }, 'invalid_pool'],
        [{ outcome: 'validation', code: 'INVALID_DENY' }, 'invalid_deny'],
        [{ outcome: 'conflict', code: 'STALE_VERSION' }, 'stale_version'],
    ])('keeps an unsaved scope draft on %j', async (response, code) => {
        const { editor, put } = fixture();
        await editor.load('d1');
        editor.state.approverPoolIds.push(POOL);
        put.mockResolvedValueOnce(response);
        expect(await editor.save()).toMatchObject({ ok: false, code });
        expect(editor.state).toMatchObject({ version: 1, approverPoolIds: [POOL] });
        if (code === 'stale_version') {
            expect(await editor.save()).toMatchObject({ ok: false, code });
            expect(put).toHaveBeenCalledTimes(1);
        }
    });
});
