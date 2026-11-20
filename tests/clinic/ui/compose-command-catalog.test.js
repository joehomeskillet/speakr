import { expect, it, vi } from 'vitest';
import { createCommandsTemplatesController } from '../../../static/js/clinic/compose-commands-templates.js';

it('keeps only the latest authorised catalog and clears details when access changes', async () => {
    let finishEarlierLoad;
    const row = { id: 'sample-cmd', trigger_phrase: 'Neuer Absatz', action_type: 'insert_text' };
    const client = { switchContext: vi.fn(), request: vi.fn()
        .mockImplementationOnce(() => new Promise((resolve) => { finishEarlierLoad = resolve; }))
        .mockResolvedValueOnce({ outcome: 'ok', data: { items: [row] } })
        .mockResolvedValueOnce({ outcome: 'permission' }) };
    const page = createCommandsTemplatesController({ client, currentUser: { id: 7 } });
    const earlier = page.load();
    await page.load();
    page.state.selectedId = row.id;
    finishEarlierLoad({ outcome: 'ok', data: { items: [] } });
    await earlier;
    expect(page.state.commands).toEqual([row]);
    expect(page.state.selectedId).toBe(row.id);
    await page.load();
    expect(page.state.status).toBe('forbidden');
    expect(page.state.commands).toEqual([]);
    expect(page.state.selectedId).toBeNull();
});
