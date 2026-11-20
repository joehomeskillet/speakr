import { describe, it, expect, vi } from 'vitest';
import { createDictationChoices } from '../../../static/js/clinic/dictation-choices.js';
import { createArchiveController } from '../../../static/js/clinic/compose-archive.js';

const row = (id, author = 7, status = 'neu_angelegt') => ({ dictation_id: id,
    dictated_by: { id: author, display_name: 'Dr. Beispiel' }, workflow_status: status,
    day: '2026-11-20', sort_time: '2026-11-20T10:00:00Z', staff_pools: [{ id: 3, name: 'Stationär' }] });
const ok = (items, next_cursor = null) => ({ outcome: 'ok', data: { items, next_cursor } });

describe('name-based dictation entry points', () => {
    it('offers own open dictations, resolves names and retains optional legacy IDs', async () => {
        const client = { request: vi.fn(async () => ok([row('a'), row('b'), row('other', 8), row('done', 7, 'fertiggestellt')])) };
        const c = createDictationChoices({ client, authorId: 7 });
        await c.load();
        expect(client.request).toHaveBeenCalledWith('/recordings?limit=50&author_id=7');
        expect(c.state.rows.map((r) => r.id)).toEqual(['a', 'b']);
        expect(new Set(c.state.rows.map((r) => r.label)).size).toBe(2);
        expect(c.resolve(c.state.rows[1].label)).toBe('b');
        expect(c.resolve('legacy-id')).toBe('legacy-id');
        expect(c.resolve('../secret')).toBeNull();
        expect(c.resolve('Dr. Bei')).toBeNull();
    });
    it('paginates, clears on refusal and ignores responses after context changes', async () => {
        let resolve;
        const client = { request: vi.fn().mockResolvedValueOnce(ok([row('a')], 'next'))
            .mockResolvedValueOnce(ok([row('a'), row('b')]))
            .mockResolvedValueOnce({ outcome: 'permission' })
            .mockImplementationOnce(() => new Promise((r) => { resolve = r; })) };
        const c = createDictationChoices({ client });
        await c.load();
        await c.load(true);
        expect(c.state.rows).toHaveLength(2);
        expect(client.request.mock.calls[1][0]).toContain('cursor=next');
        await c.load();
        expect(c.state.rows).toEqual([]);
        expect(c.state.message).toContain('Keine Berechtigung');
        const pending = c.load();
        c.clear();
        resolve(ok([row('late')]));
        await pending;
        expect(c.state.rows).toEqual([]);
    });
    it('archive filters show names, submit IDs and discard names after denial', async () => {
        const client = { request: vi.fn().mockResolvedValueOnce(ok([row('a')]))
            .mockResolvedValueOnce(ok([])).mockResolvedValueOnce({ outcome: 'permission' }) };
        const c = createArchiveController({ client });
        await c.load();
        expect(c.choices.authors).toEqual([{ value: '7', label: 'Dr. Beispiel' }]);
        expect(c.choices.pools).toEqual([{ value: '3', label: 'Stationär' }]);
        await c.setFilters({ authorId: '7', poolId: '3' });
        expect(client.request.mock.calls[1][0]).toContain('author_id=7');
        expect(c.choices.authors).toHaveLength(1);
        await c.load();
        expect(c.choices.authors).toEqual([]);
    });
});
