import { describe, expect, it, vi } from 'vitest';
import {
    FIELDS,
    LABELS,
    MAX_FILTERS,
    createWorklistFilters,
    draftToSpec,
    validateCondition,
} from '../../../static/js/clinic/worklist-filters-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });

// In-memory stand-in for /worklist/filters (server contract, see worklist-filters-ui.js).
function fakeServer(count = 0) {
    const store = new Map();
    for (let i = 1; i <= count; i += 1) store.set('f' + i, { id: 'f' + i, name: 'F' + i, version: 1, spec: { all: [], any: [] } });
    let next = count + 1;
    const posts = [];
    const request = vi.fn(async (path, opts = {}) => {
        const id = path.split('/')[3];
        if (!id && !opts.method) return ok({ items: [...store.values()] });
        if (!id && opts.method === 'POST') {
            posts.push(opts.body);
            const f = { id: 'f' + next, name: opts.body.name, version: 1, spec: opts.body.spec };
            next += 1;
            store.set(f.id, f);
            return ok({ id: f.id });
        }
        if (opts.method === 'DELETE') { store.delete(id); return ok(null); }
        return ok(store.get(id));
    });
    return { client: { request }, store, posts };
}

const make = (count) => {
    const server = fakeServer(count);
    const c = createWorklistFilters({ client: server.client, userId: 7, storage: null });
    return { c, ...server };
};

describe('PAR-WF-003 supported filter fields', () => {
    it('offers pool and author but not staff_pool or lease_holder', () => {
        expect(FIELDS).toEqual(expect.arrayContaining(['status', 'author', 'pool']));
        expect(FIELDS).not.toContain('staff_pool');
        expect(FIELDS).not.toContain('lease_holder');
        expect(validateCondition({ field: 'staff_pool', op: 'eq', value: '1' })).toBe('unknown_field');
        expect(validateCondition({ field: 'lease_holder', op: 'eq', value: '1' })).toBe('unknown_field');
    });

    it('builds a combined author/pool/status spec', () => {
        const spec = draftToSpec({ conditions: [
            { field: 'author', op: 'eq', value: '7' },
            { field: 'pool', op: 'eq', value: 'Pool A' },
            { field: 'status', op: 'eq', value: 'open' },
        ] });
        expect(spec).toEqual({ all: [
            { field: 'author', op: 'eq', value: '7' },
            { field: 'pool', op: 'eq', value: 'Pool A' },
            { field: 'status', op: 'eq', value: 'open' },
        ], any: [] });
    });
});

describe('PAR-WF-003 save, reselect, delete', () => {
    it('saves a pool+status filter, reselects it and deletes it', async () => {
        const { c, posts } = make(0);
        await c.load();
        c.state.draft.name = 'Pool A und offen';
        c.addCondition();
        Object.assign(c.state.draft.conditions[0], { field: 'pool', op: 'eq', value: 'Pool A' });
        c.addCondition();
        Object.assign(c.state.draft.conditions[1], { field: 'status', op: 'eq', value: 'open' });
        expect(await c.save(false)).toBe(true);
        expect(posts[0].spec.all.map((x) => x.field)).toEqual(['pool', 'status']);
        expect(c.state.selectedId).toBe('f1');

        await c.select(null);
        expect(c.state.selectedId).toBeNull();
        expect(c.state.draft.conditions).toEqual([]);
        await c.select('f1');
        expect(c.state.selectedId).toBe('f1');
        expect(c.state.draft.name).toBe('Pool A und offen');
        expect(c.state.draft.conditions).toEqual([
            { field: 'pool', op: 'eq', value: 'Pool A' },
            { field: 'status', op: 'eq', value: 'open' },
        ]);

        expect(await c.remove()).toBe(true);
        expect(c.state.filters).toEqual([]);
        expect(c.state.selectedId).toBeNull();
    });
});

describe('PAR-WF-003 filter limit', () => {
    it('fills 20 filters and rejects the 21st, leaving the others unchanged', async () => {
        const { c, store, posts } = make(MAX_FILTERS - 1);
        await c.load();
        c.state.draft.name = 'F20';
        expect(await c.save(false)).toBe(true);
        expect(c.state.filters).toHaveLength(MAX_FILTERS);
        const before = JSON.stringify([...store.values()]);

        await c.select(null);
        c.state.draft.name = 'F21';
        expect(await c.save(false)).toBe(false);
        expect(c.state.error).toBe('limit_reached');
        expect(c.state.message).toBe('Es sind höchstens 20 Filter möglich.');
        expect(LABELS.errors.limit_reached).toBe(c.state.message);
        expect(posts).toHaveLength(1);
        expect(c.state.filters).toHaveLength(MAX_FILTERS);
        expect(JSON.stringify([...store.values()])).toBe(before);
    });
});
