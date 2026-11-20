import { expect, it, vi } from 'vitest';
import { createWorkflowActions } from '../../../static/js/clinic/workflow-actions-ui.js';

function setup(allowedActions) {
    const client = { request: vi.fn(), command: vi.fn() };
    const wf = createWorkflowActions({ client });
    wf.load({ dictationId: 'synthetic', etag: '"synthetic:3"', revision: 3,
        allowedActions, binding: { release: { original_version_id: 'audio', transcript_revision_id: 'text', context_revision_id: 'context', text_sha256: 'hash' } } });
    return { client, wf };
}

it('sends only an allowed, confirmed send with empty body and concurrency headers', async () => {
    const { client, wf } = setup(['send']);
    client.request.mockResolvedValue({ outcome: 'ok', data: {} });
    expect(wf.request('send')).toBe(true);
    expect(client.request).not.toHaveBeenCalled();
    await wf.confirm();
    expect(client.request).toHaveBeenCalledWith('/api/clinic/v1/dictations/synthetic/send',
        expect.objectContaining({ method: 'POST', body: {}, ifMatch: '"synthetic:3"', idempotencyKey: expect.any(String) }));
    wf.load({ allowedActions: [] });
    expect(wf.request('send')).toBe(false);
});

it('loads holder proof after both confirmations and adds exact lease keys', async () => {
    const { client, wf } = setup(['release']);
    client.request.mockResolvedValueOnce({ outcome: 'ok', data: { held: true, lease_id: 'lease', generation: 7 } })
        .mockResolvedValueOnce({ outcome: 'ok', data: {} });
    wf.request('release');
    await wf.confirm();
    expect(client.request).not.toHaveBeenCalled();
    wf.state.confirmedContent = wf.state.confirmedContext = true;
    await wf.confirm();
    expect(client.request.mock.calls[0][0]).toBe('/api/clinic/v1/dictations/synthetic/leases');
    expect(client.request.mock.calls[1][1].body).toEqual({ original_version_id: 'audio', transcript_revision_id: 'text', context_revision_id: 'context', text_sha256: 'hash',
        lease_id: 'lease', lease_generation: 7, confirmed_content: true, confirmed_context: true });
});

it('does not release a foreign or absent lease', async () => {
    const { client, wf } = setup(['release']);
    client.request.mockResolvedValue({ outcome: 'ok', data: { held: true, lease_id: null } });
    wf.request('release');
    wf.state.confirmedContent = wf.state.confirmedContext = true;
    expect((await wf.confirm()).code).toBe('LEASE_REQUIRED');
    expect(wf.state.error).toContain('Bearbeitung übernehmen');
    expect(client.request).toHaveBeenCalledTimes(1);
    expect(wf.state.busy).toBe(false);
});

it('never transfers a pending lease proof to another dictation', async () => {
    const { client, wf } = setup(['release']);
    let resolve;
    client.request.mockReturnValue(new Promise((done) => { resolve = done; }));
    wf.request('release');
    wf.state.confirmedContent = wf.state.confirmedContext = true;
    const pending = wf.confirm();
    wf.load({ dictationId: 'other', etag: '"other:1"', allowedActions: ['release'] });
    resolve({ outcome: 'ok', data: { held: true, lease_id: 'lease', generation: 7 } });
    expect(await pending).toBeNull();
    expect(client.request).toHaveBeenCalledTimes(1);
    expect(wf.state.binding).toEqual({});
});
