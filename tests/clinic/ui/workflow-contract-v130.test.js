import { describe, expect, it, vi } from 'vitest';
import { createWorkflowActions } from '../../../static/js/clinic/workflow-actions-ui.js';

function setup() {
    const client = {
        request: vi.fn(async (path) => ({ outcome: 'ok', data: path.endsWith('/leases')
            ? { held: true, lease_id: 'current-own', generation: 7 } : {} })),
        command: vi.fn(async () => ({ outcome: 'ok', data: {} })),
    };
    const wf = createWorkflowActions({ client });
    const row = { dictationId: 'd', etag: '"d:4"', revision: 4, allowedActions: ['submit', 'release'],
        binding: { audioRevisionId: 'audio', textRevisionId: 't', contextRevisionId: 'c', acceptanceRuleId: 'policy',
            release: { original_version_id: 'audio', transcript_revision_id: 't', context_revision_id: 'c',
                text_sha256: 'hash', lease_id: 'old', lease_generation: 1 } } };
    wf.load(row);
    return { wf, client, row };
}

describe('v130 workflow contract', () => {
    it('sends next parity revision and exact published rule identity', async () => {
        const { wf, client } = setup();
        wf.request('submit');
        await Promise.resolve();
        expect(client.command.mock.calls[0][0]).toMatchObject({ expected_revision: 5,
            payload: { text_revision_id: 't', context_revision_id: 'c', acceptance_rule_id: 'policy' } });
    });

    it.each(['audioRevisionId', 'textRevisionId', 'contextRevisionId', 'acceptanceRuleId'])('blocks missing submit %s', (field) => {
        const { wf, client } = setup();
        wf.state.binding[field] = null;
        expect(wf.request('submit')).toBe(false);
        expect(client.command).not.toHaveBeenCalled();
        expect(wf.state.error).toContain('fehlen');
    });

    it.each(['original_version_id', 'transcript_revision_id', 'context_revision_id'])('blocks missing release %s', (field) => {
        const { wf, client } = setup();
        wf.state.binding.release[field] = null;
        expect(wf.request('release')).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
        expect(wf.state.error).toContain('gesperrt');
    });

    it('uses fresh holder proof after both confirmations', async () => {
        const { wf, client } = setup();
        wf.request('release');
        await wf.confirm();
        expect(client.request).not.toHaveBeenCalled();
        wf.state.confirmedContent = wf.state.confirmedContext = true;
        await wf.confirm();
        expect(client.request.mock.calls[1][1].body).toMatchObject({
            lease_id: 'current-own', lease_generation: 7, original_version_id: 'audio' });
    });

    it.each([{ held: true, generation: 7 }, { held: false, lease_id: 'expired', generation: 7 }])(
        'refuses a foreign or expired lease without posting', async (data) => {
            const { wf, client } = setup();
            client.request.mockResolvedValue({ outcome: 'ok', data });
            wf.request('release');
            wf.state.confirmedContent = wf.state.confirmedContext = true;
            const res = await wf.confirm();
            expect(res.code).toBe('LEASE_REQUIRED');
            expect(client.request).toHaveBeenCalledTimes(1);
        });

    it('does not post after a context switch during the lease read', async () => {
        const { wf, client } = setup();
        let resolve;
        client.request.mockImplementation(() => new Promise((done) => { resolve = done; }));
        wf.request('release');
        wf.state.confirmedContent = wf.state.confirmedContext = true;
        const pending = wf.confirm();
        wf.load(null);
        resolve({ outcome: 'ok', data: { held: true, lease_id: 'own', generation: 1 } });
        expect(await pending).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(1);
    });
});
