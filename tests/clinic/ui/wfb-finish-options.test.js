import { expect, it, vi } from 'vitest';
import { createFinishOptionsController } from '../../../static/js/clinic/finish-options-ui.js';
import { createWorkflowActions } from '../../../static/js/clinic/workflow-actions-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const binding = (revision) => ({ original_version_id: 'audio', transcript_revision_id: revision,
    context_revision_id: 'context', text_sha256: 'hash' });

function setup({ active = 'reviewed', released = 'reviewed', refusal, empty = false } = {}) {
    const client = { request: vi.fn(async (path, options) => {
        if (path.endsWith('/leases')) return ok({ held: true, lease_id: 'lease', generation: 1 });
        if (path.endsWith('/releases')) return refusal || ok({ transcript_revision_id: released });
        if (path.endsWith('/output-targets')) return ok({ released_revision_id: active, targets: empty ? [] : [
            ...['txt', 'docx', 'pdf'].map((id) => ({ id, channel: 'file', available: true })),
            { id: 'kis', channel: 'kis', available: true },
        ] });
        if (options?.body.format === 'txt') return ok({ id: 'txt-job', status: 'ready' });
        if (options?.body.format === 'docx') return { outcome: 'validation' };
        throw new Error(`Unexpected request: ${path}`);
    }) };
    const workflow = createWorkflowActions({ client });
    function load(revision, version) {
        workflow.load({ dictationId: 'dictation', etag: `"dictation:${version}"`, revision: version,
            binding: { release: binding(revision) }, allowedActions: ['release'] });
    }
    load('reviewed', 3);
    const c = createFinishOptionsController({ workflow, client });
    const check = () => { c.state.content = c.state.context = true; };
    const paths = () => client.request.mock.calls.map(([path]) => path.split('/').at(-1));
    return { c, check, client, load, paths };
}

it('cancels without a request; reopening resets confirmations and no-output only releases', async () => {
    const { c, check, paths } = setup();
    expect(c.open()).toBe(true);
    check();
    expect(c.close()).toBe(true);
    expect(paths()).toEqual([]);
    expect(c.open()).toBe(true);
    expect(c.state.content).toBe(false);
    expect(c.state.context).toBe(false);
    c.state.content = true;
    await c.release(false);
    expect(paths()).toEqual([]);
    check();
    await c.release(false);
    expect(c.state.step).toBe('done');
    expect(paths()).toEqual(['leases', 'releases']);
});

it('requires separate selections and uses the same released revision with distinct IDs and results', async () => {
    const { c, check, client, paths } = setup();
    c.open();
    check();
    await c.release(true);
    expect(c.state.targets.map(({ id }) => id)).toEqual(['txt', 'docx']);
    c.state.selected.kis = true;
    await c.exportSelected();
    expect(paths()).toEqual(['leases', 'releases', 'output-targets']);
    c.state.selected.txt = c.state.selected.docx = true;
    await Promise.all([c.exportSelected(), c.exportSelected()]);
    const writes = client.request.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(new Set(writes.map(([, options]) => options.idempotencyKey)).size).toBe(3);
    expect(writes.slice(1).map(([, options]) => options.body)).toEqual([
        { revision_id: 'reviewed', format: 'txt' }, { revision_id: 'reviewed', format: 'docx' },
    ]);
    expect(c.state.step).toBe('done');
    expect(c.state.results.txt).toMatchObject({ phase: 'ready', job: { id: 'txt-job' } });
    expect(c.state.results.docx).toMatchObject({ phase: 'validation', job: null });
});

it.each([{ active: 'different' }, { released: 'different' }, { empty: true }])('does not export when no bound targets are presented (%j)', async (fixture) => {
    const { c, check, paths } = setup(fixture);
    c.open();
    check();
    await c.release(true);
    expect(c.state.targets).toEqual([]);
    c.state.selected.txt = true;
    await c.exportSelected();
    expect(paths()).toEqual(['leases', 'releases', 'output-targets']);
});

it('never loads output or exports after a release refusal', async () => {
    const { c, check, paths } = setup({ refusal: { outcome: 'conflict', message: 'Veraltet' } });
    c.open();
    check();
    await c.release(true);
    expect(c.state.step).toBe('release');
    expect(c.state.error).toBe('Veraltet');
    expect(paths()).toEqual(['leases', 'releases']);
});

it('does not confirm a different workflow state under an already open dialog', async () => {
    const { c, check, load, paths } = setup();
    c.open();
    check();
    load('new-text', 4);
    await c.release(true);
    expect(c.state.error).toContain('geändert');
    expect(paths()).toEqual([]);
});
