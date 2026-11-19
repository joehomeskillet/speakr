import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createAcceptancePolicyEditor, useAcceptancePolicyEditor, acceptancePolicyComponent, KEYS,
} from '../../../static/js/clinic/acceptance-policy-ui.js';

const POLICY = {
    version: 2,
    acceptance_required: true,
    approver_ids: [1, 9],
    eligible: [{ user_id: 1, name: 'Dr. A' }, { user_id: 2, name: 'Dr. B' }],
    decisions: [
        { kind: 'accept', actor_id: 1, actor_name: 'Dr. A', revision_id: 'r1', policy_version: 2 },
        { kind: 'reject', actor_id: 2, actor_name: 'Dr. B', revision_id: 'r2', reason: 'x', policy_version: 1 },
        { kind: 'pending', actor_id: 3, actor_name: 'Dr. C', revision_id: 'r3', policy_version: 2 },
    ],
};

function ok(data) {
    return { outcome: 'ok', data: data, message: '' };
}

// GET returns the policy; PUT goes to `put` (default: success with version 3).
function fakeClient(put, policy) {
    const putFn = put || vi.fn(async () => ok({ version: 3, acceptance_required: true, approver_ids: [] }));
    const client = {
        put: putFn,
        request: vi.fn(async (path, opts) => {
            if (opts && opts.method === 'PUT') return putFn(path, opts);
            return ok(policy || POLICY);
        }),
        switchContext: vi.fn(),
    };
    return client;
}

async function loaded(put, policy) {
    const client = fakeClient(put, policy);
    const editor = createAcceptancePolicyEditor({ client: client });
    await editor.load('d1');
    return { client, editor };
}

describe('acceptance policy ui', () => {
    it('loads version, flag, approvers and decisions with actors', async () => {
        const { client, editor } = await loaded();
        expect(client.request.mock.calls[0][0]).toBe('/dictations/d1/acceptance-policy');
        expect(editor.state).toMatchObject({ version: 2, required: true, status: 'success' });
        expect(editor.state.decisions.map((d) => [d.label, d.actorName])).toEqual([
            ['Akzeptiert', 'Dr. A'], ['Abgelehnt', 'Dr. B'], ['Ausstehend', 'Dr. C'],
        ]);
        expect(editor.state.decisions[1].current).toBe(false);
    });

    it('flags removed participants and blocks the save until deselected', async () => {
        const { client, editor } = await loaded();
        const rows = editor.participants();
        expect(rows.find((r) => r.userId === 9)).toMatchObject({ removed: true, checked: true });
        expect((await editor.save()).code).toBe('removed_participant');
        expect(client.put).not.toHaveBeenCalled();
        editor.toggle(9);
        expect((await editor.save()).ok).toBe(true);
    });

    it('rejects ineligible selection and an empty required approver set', async () => {
        const { editor } = await loaded();
        expect(editor.toggle(77).code).toBe('not_eligible');
        editor.toggle(9);
        editor.toggle(1);
        expect((await editor.save()).code).toBe('no_approver');
        editor.setRequired(false);
        expect((await editor.save()).ok).toBe(true);
    });

    it('saves with PUT, If-Match version and the exact server body', async () => {
        const { client, editor } = await loaded();
        editor.toggle(9);
        editor.toggle(2);
        expect((await editor.save()).ok).toBe(true);
        const [path, opts] = client.put.mock.calls[0];
        expect(path).toBe('/dictations/d1/acceptance-policy');
        expect(opts.method).toBe('PUT');
        expect(opts.ifMatch).toBe('2');
        expect(opts.body).toEqual({ acceptance_required: true, approver_ids: [1, 2] });
        expect(editor.state.version).toBe(3);
    });

    it('marks a stale version (409 STALE_VERSION) and refuses saves until reload', async () => {
        const { client, editor } = await loaded(vi.fn(async () => ({ outcome: 'conflict', code: 'STALE_VERSION', message: 'x' })));
        editor.toggle(9);
        expect((await editor.save()).code).toBe('stale_version');
        expect(editor.state.status).toBe('warning');
        expect((await editor.save()).code).toBe('stale_version');
        expect(client.put).toHaveBeenCalledTimes(1);
        await editor.load('d1');
        expect(editor.state.stale).toBe(false);
    });

    it('maps 422 INVALID_APPROVER, 503 AUDIT_UNAVAILABLE and 428', async () => {
        const bad = await loaded(vi.fn(async () => ({ outcome: 'validation', code: 'INVALID_APPROVER', message: 'x' })));
        bad.editor.toggle(9);
        expect((await bad.editor.save()).code).toBe('invalid_approver');
        const audit = await loaded(vi.fn(async () => ({ outcome: 'network', code: 'AUDIT_UNAVAILABLE', message: 'x' })));
        audit.editor.toggle(9);
        expect((await audit.editor.save()).code).toBe('audit_unavailable');
        expect(audit.editor.state.status).toBe('error');
        const missing = await loaded(vi.fn(async () => ({ outcome: 'validation', code: 'ERROR', message: 'x' })));
        missing.editor.toggle(9);
        expect((await missing.editor.save()).code).toBe('failure');
    });

    it('reports unauthorized, failure and retry outcomes', async () => {
        const denied = await loaded(vi.fn(async () => ({ outcome: 'permission', message: 'x' })));
        denied.editor.toggle(9);
        expect((await denied.editor.save()).code).toBe('permission');
        const broken = await loaded(vi.fn(async () => ({ outcome: 'network', message: 'x' })));
        broken.editor.toggle(9);
        expect((await broken.editor.save()).code).toBe('failure');
        expect(broken.editor.state.status).toBe('error');
        const flaky = await loaded(vi.fn()
            .mockResolvedValueOnce({ outcome: 'network', message: 'x' })
            .mockResolvedValueOnce(ok({ version: 3, acceptance_required: true, approver_ids: [1] })));
        flaky.editor.toggle(9);
        expect((await flaky.editor.save()).ok).toBe(false);
        expect((await flaky.editor.save()).ok).toBe(true);
        const noLoad = createAcceptancePolicyEditor({ client: { request: vi.fn(async () => ({ outcome: 'permission', message: 'x' })), switchContext: vi.fn() } });
        expect((await noLoad.load('d1')).code).toBe('permission');
        expect((await noLoad.save()).code).toBe('not_loaded');
    });

    it('works with the x15 response shape that has no eligible list', async () => {
        const { client, editor } = await loaded(undefined, { version: 1, acceptance_required: true, approver_ids: [4, 5] });
        expect(editor.participants().map((r) => [r.userId, r.checked, r.removed])).toEqual([[4, true, false], [5, true, false]]);
        expect((await editor.save()).ok).toBe(true);
        expect(client.put.mock.calls[0][1].body).toEqual({ acceptance_required: true, approver_ids: [4, 5] });
    });

    it('does not double-toggle: native checkbox has no keydown handler', () => {
        const comp = acceptancePolicyComponent({ reactive: (v) => v }, { client: fakeClient() });
        expect(comp.template).toContain('@change="editor.toggle(p.userId)"');
        expect(comp.template).not.toContain('@keydown');
    });

    it('handles keyboard states', async () => {
        const { editor } = await loaded();
        expect(KEYS.toggle).toContain(' ');
        expect(editor.onKey(2, { key: ' ' }).ok).toBe(true);
        expect(editor.state.approvers).toContain(2);
        expect(editor.onKey(2, { key: 'Enter' }).ok).toBe(true);
        expect(editor.state.approvers).not.toContain(2);
        expect(editor.onKey(2, { key: 'a' }).ok).toBe(false);
    });

    it('keeps clinical data out of browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const { editor } = await loaded();
        editor.reset();
        expect(writes).toEqual([]);
        expect(editor.state.decisions).toEqual([]);
    });

    it('exposes a Vue composable and component with labels and roles', async () => {
        const reactive = vi.fn((v) => v);
        const composable = useAcceptancePolicyEditor({ reactive }, { client: fakeClient() });
        expect(reactive).toHaveBeenCalled();
        expect(composable.labels.title).toBe('Pflichtfreigabe');
        const comp = acceptancePolicyComponent({ reactive }, { client: fakeClient() });
        expect(comp.template).toContain('aria-label');
        expect(comp.template).toContain('role="region"');
        expect(typeof comp.setup).toBe('function');
    });

    it('uses no hard colours', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/acceptance-policy-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
