import { describe, expect, it, vi } from 'vitest';
import { createWorkflowActions, WORKFLOW_ACTIONS_TEMPLATE } from '../../../static/js/clinic/workflow-actions-ui.js';

const D = '22222222-2222-4222-8222-222222222222';
const T = '33333333-3333-4333-8333-333333333333';
const C = '44444444-4444-4444-8444-444444444444';

function ok(data) {
    return { outcome: 'ok', data: data || {}, message: '' };
}
function fail(outcome, message) {
    return { outcome: outcome, code: null, correlationId: null, message: message || 'm' };
}

function setup(allowed, extra) {
    const client = { command: vi.fn(async () => ok()), request: vi.fn(async (path) =>
        ok(path.endsWith('/leases') ? { held: true, lease_id: C, generation: 1 } : {})) };
    const wf = createWorkflowActions({ client: client });
    wf.load(Object.assign({
        dictationId: D, etag: '"' + D + ':3"', revision: 4, allowedActions: allowed,
        transitions: { reject: 'Zu korrigieren' }, asrStatus: 'completed', workState: 'pending',
        binding: { audioRevisionId: T, textRevisionId: T, contextRevisionId: C, acceptanceRuleId: C,
            release: { lease_id: C, lease_generation: 1, original_version_id: T, transcript_revision_id: T,
                context_revision_id: C, document_type: 'report', mode: 'PLATFORM_REVIEWED_KIS_DRAFT', text_sha256: 'h' } },
    }, extra));
    return { wf, client };
}

describe('workflow actions ui', () => {
    it('shows only authorized known actions, grouped, apart from status', () => {
        const { wf } = setup(['reject', 'archive', 'bogus']);
        expect(wf.visible().map((a) => a.id)).toEqual(['reject', 'archive']);
        expect(wf.groups().map((g) => g.id)).toEqual(['correction', 'archive']);
        expect(wf.visible()[0].target).toBe('Zu korrigieren');
        expect(wf.statuses().asr.label).toBe('Abgeschlossen');
        expect(wf.statuses().work.label).toBe('Ausstehend');
    });

    it('refuses an unauthorized action without sending', () => {
        const { wf, client } = setup(['submit']);
        expect(wf.request('archive')).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
        expect(client.command).not.toHaveBeenCalled();
    });

    it('requires explicit confirmation and a reason for reject', async () => {
        const { wf, client } = setup(['reject']);
        wf.request('reject');
        expect(wf.state.confirming).toBe('reject');
        expect(client.command).not.toHaveBeenCalled();
        await wf.confirm();
        expect(wf.state.error).toContain('Begründung');
        wf.state.reason = ' zu kurz ';
        await wf.confirm();
        const env = client.command.mock.calls[0][0];
        expect(env).toMatchObject({ command: 'decide_acceptance', expected_revision: 5 });
        expect(env.payload).toEqual({ dictation_id: D, text_revision_id: T, decision: 'return', reason: 'zu kurz' });
        expect(wf.state.confirming).toBeNull();
    });

    it('submit sends without confirmation and cancel clears the dialog', async () => {
        const { wf, client } = setup(['submit', 'reopen']);
        wf.request('submit');
        await Promise.resolve();
        expect(client.command.mock.calls[0][0].command).toBe('request_acceptance');
        wf.request('reopen');
        wf.cancel();
        expect(wf.state.confirming).toBeNull();
    });

    it('release needs both confirmations and posts the exact binding with If-Match', async () => {
        const { wf, client } = setup(['release']);
        wf.request('release');
        await wf.confirm();
        expect(client.request).not.toHaveBeenCalled();
        wf.state.confirmedContent = true;
        wf.state.confirmedContext = true;
        await wf.confirm();
        expect(client.request.mock.calls[0][0]).toBe('/api/clinic/v1/dictations/' + D + '/leases');
        const [path, opts] = client.request.mock.calls[1];
        expect(path).toBe('/api/clinic/v1/dictations/' + D + '/releases');
        expect(opts.ifMatch).toBe('"' + D + ':3"');
        expect(opts.body.confirmed_content).toBe(true);
        expect(opts.body.text_sha256).toBe('h');
    });

    it('archive sends archive only, never delete', async () => {
        const { wf, client } = setup(['archive']);
        wf.request('archive');
        await wf.confirm();
        expect(client.request.mock.calls[0][1].body).toEqual({ action: 'archive', confirm: true });
    });

    it('stale version marks stale and next attempt uses a new key', async () => {
        const { wf, client } = setup(['archive']);
        client.request.mockResolvedValueOnce(fail('conflict', 'Der Stand ist veraltet.'));
        wf.request('archive');
        await wf.confirm();
        expect(wf.state.stale).toBe(true);
        expect(wf.state.error).toBe('Der Stand ist veraltet.');
        await wf.confirm();
        const keys = client.request.mock.calls.map((c) => c[1].idempotencyKey);
        expect(keys[0]).not.toBe(keys[1]);
    });

    it('network failure retries with the same idempotency key', async () => {
        const { wf, client } = setup(['archive']);
        client.request.mockResolvedValueOnce(fail('network'));
        wf.request('archive');
        await wf.confirm();
        expect(wf.state.error).toBe('m');
        expect(wf.state.confirming).toBe('archive');
        await wf.confirm();
        const keys = client.request.mock.calls.map((c) => c[1].idempotencyKey);
        expect(keys[0]).toBe(keys[1]);
        expect(wf.state.lastResult.action).toBe('archive');
    });

    it('permission failure is shown and does not leak into state beyond the code', async () => {
        const { wf, client } = setup(['reopen']);
        client.command.mockResolvedValueOnce(fail('permission', 'Keine Berechtigung für diese Aktion.'));
        wf.request('reopen');
        wf.state.reason = 'Rückfrage';
        await wf.confirm();
        expect(wf.state.error).toBe('Keine Berechtigung für diese Aktion.');
        expect(Object.keys(wf.state.lastResult).sort()).toEqual(['action', 'code', 'correlationId', 'outcome']);
    });

    it('template is accessible: native buttons, live region, labelled controls', () => {
        expect(WORKFLOW_ACTIONS_TEMPLATE).toContain('aria-live="polite"');
        expect(WORKFLOW_ACTIONS_TEMPLATE).toContain('role="alertdialog"');
        expect(WORKFLOW_ACTIONS_TEMPLATE).toContain('@keydown.esc');
        expect(WORKFLOW_ACTIONS_TEMPLATE).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(WORKFLOW_ACTIONS_TEMPLATE).not.toMatch(/-\[/);
    });
});
