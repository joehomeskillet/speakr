import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createAcceptancePolicyEditor, useAcceptancePolicyEditor, acceptancePolicyComponent, KEYS,
} from '../../../static/js/clinic/acceptance-policy-ui.js';

const POLICY = {
    version: 2,
    required: true,
    approver_user_ids: [1, 9],
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

function fakeClient(overrides) {
    return Object.assign({
        request: vi.fn(async () => ok(POLICY)),
        command: vi.fn(async () => ok({ revision: 3 })),
        switchContext: vi.fn(),
    }, overrides || {});
}

async function loaded(overrides) {
    const client = fakeClient(overrides);
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
        expect(client.command).not.toHaveBeenCalled();
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

    it('saves with the version as expected revision and idempotency key', async () => {
        const { client, editor } = await loaded();
        editor.toggle(9);
        editor.toggle(2);
        expect((await editor.save()).ok).toBe(true);
        const [body, opts] = client.command.mock.calls[0];
        expect(body).toMatchObject({ command: 'save_acceptance_policy', expected_revision: 2 });
        expect(body.payload).toEqual({ dictation_id: 'd1', required: true, approver_user_ids: [1, 2] });
        expect(opts.ifMatch).toBe('"2"');
        expect(opts.idempotencyKey).toBe(body.request_id);
        expect(editor.state.version).toBe(3);
    });

    it('marks a stale version and refuses further saves until reload', async () => {
        const { client, editor } = await loaded({ command: vi.fn(async () => ({ outcome: 'conflict', message: 'x' })) });
        editor.toggle(9);
        expect((await editor.save()).code).toBe('stale_version');
        expect(editor.state.status).toBe('warning');
        expect((await editor.save()).code).toBe('stale_version');
        expect(client.command).toHaveBeenCalledTimes(1);
        await editor.load('d1');
        expect(editor.state.stale).toBe(false);
    });

    it('reports unauthorized, failure and retry outcomes', async () => {
        const denied = await loaded({ command: vi.fn(async () => ({ outcome: 'permission', message: 'x' })) });
        denied.editor.toggle(9);
        expect((await denied.editor.save()).code).toBe('permission');
        const broken = await loaded({ command: vi.fn(async () => ({ outcome: 'network', message: 'x' })) });
        broken.editor.toggle(9);
        expect((await broken.editor.save()).code).toBe('failure');
        expect(broken.editor.state.status).toBe('error');
        const flaky = await loaded({
            command: vi.fn()
                .mockResolvedValueOnce({ outcome: 'network', message: 'x' })
                .mockResolvedValueOnce(ok({ revision: 3 })),
        });
        flaky.editor.toggle(9);
        expect((await flaky.editor.save()).ok).toBe(false);
        expect((await flaky.editor.save()).ok).toBe(true);
        const noLoad = createAcceptancePolicyEditor({ client: fakeClient({ request: vi.fn(async () => ({ outcome: 'permission', message: 'x' })) }) });
        expect((await noLoad.load('d1')).code).toBe('permission');
        expect((await noLoad.save()).code).toBe('not_loaded');
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
