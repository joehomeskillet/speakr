import { describe, expect, it, vi } from 'vitest';
import {
    LABELS,
    MAX_FILTERS,
    OPERATORS,
    WorklistFiltersPanel,
    createWorklistFilters,
    draftToSpec,
    validateCondition,
} from '../../../static/js/clinic/worklist-filters-ui.js';

function memoryStorage(initial) {
    const data = new Map(Object.entries(initial || {}));
    return {
        data,
        getItem: (k) => (data.has(k) ? data.get(k) : null),
        setItem: (k, v) => { data.set(k, String(v)); },
        removeItem: (k) => { data.delete(k); },
    };
}

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const bad = (outcome, code) => ({ outcome, status: 400, code: code || null, message: 'x' });
const spec = { all: [{ field: 'status', op: 'eq', value: 'pending' }], any: [] };

function items(n) {
    return Array.from({ length: n }, (_, i) => ({ id: 'f' + (i + 1), name: 'F' + (i + 1), version: 1, spec }));
}

function make(responses, extra) {
    const calls = [];
    const client = {
        request: vi.fn(async (path, opts) => {
            calls.push([path, opts || {}]);
            const next = responses.shift();
            return typeof next === 'function' ? next(path, opts) : next;
        }),
    };
    const applied = [];
    const storage = (extra && extra.storage) || memoryStorage();
    const c = createWorklistFilters({ client, userId: 7, storage, onApply: (a) => applied.push(a) });
    return { c, calls, applied, storage };
}

describe('worklist filters ui', () => {
    it('loads and applies a server-backed filter', async () => {
        const { c, applied } = make([ok({ items: items(2) })]);
        expect(await c.load()).toBe(true);
        c.select('f2');
        expect(c.state.selectedId).toBe('f2');
        expect(c.state.draft.conditions[0]).toEqual({ field: 'status', op: 'eq', value: 'pending' });
        expect(applied.at(-1)).toEqual({ filterId: 'f2', query: '' });
    });

    it('creates, edits with If-Match and deletes', async () => {
        const { c, calls } = make([
            ok({ items: [] }),
            ok({ id: 'f1' }),
            ok({ items: items(1) }),
            ok({ id: 'f1' }),
            ok({ items: items(1) }),
            ok(null),
            ok({ items: [] }),
        ]);
        await c.load();
        c.state.draft.name = '  Dringend ';
        c.addCondition();
        c.state.draft.conditions[0].value = 'pending';
        expect(await c.save(false)).toBe(true);
        expect(calls[1][0]).toBe('/worklist/filters');
        expect(calls[1][1].method).toBe('POST');
        expect(calls[1][1].body).toEqual({ name: 'Dringend', spec });
        expect(c.state.selectedId).toBe('f1');
        expect(await c.save(false)).toBe(true);
        expect(calls[3][0]).toBe('/worklist/filters/f1');
        expect(calls[3][1]).toMatchObject({ method: 'PUT', ifMatch: '1' });
        expect(await c.remove()).toBe(true);
        expect(calls[5][1]).toMatchObject({ method: 'DELETE', ifMatch: '1' });
        expect(c.state.selectedId).toBeNull();
    });

    it('enforces the 20-filter limit without a request', async () => {
        const { c, calls } = make([ok({ items: items(MAX_FILTERS) })]);
        await c.load();
        expect(c.state.limitReached).toBe(true);
        c.select(null);
        c.state.draft.name = 'Neu';
        expect(await c.save(false)).toBe(false);
        expect(c.state.error).toBe('limit_reached');
        expect(calls).toHaveLength(1);
    });

    it('maps a server limit refusal', async () => {
        const { c } = make([ok({ items: [] }), bad('UNKNOWN', 'CAPABILITY_UNAVAILABLE')]);
        await c.load();
        c.state.draft.name = 'A';
        expect(await c.save(false)).toBe(false);
        expect(c.state.message).toBe(LABELS.errors.limit_reached);
    });

    it('validates operators and values before sending', async () => {
        expect(OPERATORS).toContain('is_null');
        expect(validateCondition({ field: 'status', op: 'regex', value: 'x' })).toBe('unknown_op');
        expect(validateCondition({ field: 'nope', op: 'eq', value: 'x' })).toBe('unknown_field');
        expect(validateCondition({ field: 'meta.ward_1', op: 'eq', value: 'x' })).toBeNull();
        expect(validateCondition({ field: 'meta.Bad Key', op: 'eq', value: 'x' })).toBe('unknown_field');
        expect(validateCondition({ field: 'status', op: 'in', value: 'a,,b' })).toBe('bad_value');
        expect(validateCondition({ field: 'status', op: 'eq', value: '' })).toBe('bad_value');
        expect(draftToSpec({ conditions: [{ field: 'status', op: 'in', value: 'a, b' }, { field: 'deadline', op: 'is_null', value: 'true' }] }))
            .toEqual({ all: [{ field: 'status', op: 'in', value: ['a', 'b'] }, { field: 'deadline', op: 'is_null', value: true }], any: [] });
        const { c, calls } = make([ok({ items: [] })]);
        await c.load();
        c.state.draft.name = 'A';
        c.addCondition();
        c.state.draft.conditions[0].op = 'regex';
        expect(await c.save(false)).toBe(false);
        expect(c.state.error).toBe('unknown_op');
        expect(calls).toHaveLength(1);
        expect(c.setQuery('x'.repeat(201))).toBe(false);
        expect(c.state.error).toBe('query_too_long');
    });

    it('restores only the current user last selection', async () => {
        const storage = memoryStorage({ 'clinic.worklist.filter.7': 'f2', 'clinic.worklist.filter.8': 'f1' });
        const mine = make([ok({ items: items(2) })], { storage });
        await mine.c.load();
        expect(mine.c.state.selectedId).toBe('f2');
        const stale = make([ok({ items: items(1) })], { storage });
        await stale.c.load();
        expect(stale.c.state.selectedId).toBeNull();
        expect(storage.data.has('clinic.worklist.filter.7')).toBe(false);
        expect(storage.data.get('clinic.worklist.filter.8')).toBe('f1');
    });

    it('keeps clinical data out of browser storage', async () => {
        const { c, storage } = make([ok({ items: items(1) })]);
        await c.load();
        c.select('f1');
        c.setQuery('Patientin Muster');
        const stored = JSON.stringify([...storage.data.entries()]);
        expect(stored).toBe('[["clinic.worklist.filter.7","f1"]]');
        expect(stored).not.toContain('Muster');
        expect(stored).not.toContain('pending');
    });

    it('survives blocked storage', async () => {
        const throwing = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); }, removeItem() { throw new Error('x'); } };
        const { c } = make([ok({ items: items(1) })], { storage: throwing });
        expect(await c.load()).toBe(true);
        c.select('f1');
        expect(c.state.selectedId).toBe('f1');
    });

    it('reloads on a stale version and refuses unauthorized', async () => {
        const stale = make([ok({ items: items(1) }), bad('conflict', 'REVISION_CONFLICT'), ok({ items: [{ ...items(1)[0], version: 2 }] })]);
        await stale.c.load();
        stale.c.select('f1');
        expect(await stale.c.save(false)).toBe(false);
        expect(stale.c.state.error).toBe('conflict');
        expect(stale.c.state.filters[0].version).toBe(2);
        expect(stale.calls).toHaveLength(3);

        const denied = make([bad('permission', 'FORBIDDEN')]);
        expect(await denied.c.load()).toBe(false);
        expect(denied.c.state.message).toBe(LABELS.errors.permission);
        expect(denied.c.state.filters).toEqual([]);
    });

    it('retries a failed load and does not auto-repeat a failed write', async () => {
        const { c, calls } = make([bad('network', 'NETWORK'), ok({ items: items(1) }), bad('network', 'NETWORK')]);
        expect(await c.load()).toBe(false);
        expect(c.state.error).toBe('network');
        expect(await c.load()).toBe(true);
        expect(c.state.error).toBeNull();
        c.select('f1');
        expect(await c.remove()).toBe(false);
        expect(c.state.error).toBe('network');
        expect(calls).toHaveLength(3);
        expect(c.state.busy).toBe(false);
    });

    it('exposes labelled, native, keyboard-operable markup with token styling only', () => {
        const t = WorklistFiltersPanel.template;
        expect(t).toContain('aria-labelledby="wf-title"');
        expect(t).toContain('role="alert"');
        expect(t).toContain('aria-live="polite"');
        for (const id of ['wf-select', 'wf-search', 'wf-name']) expect(t).toContain(`for="${id}"`);
        expect(t).not.toMatch(/<div[^>]*@click/);
        expect(t).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(t).not.toMatch(/class="[^"]*\[/);
        const data = WorklistFiltersPanel.data();
        expect(data.boxStyle).toContain('var(--border-primary)');
        expect(data.errorStyle).toContain('var(--bg-danger-light)');
        expect(Object.keys(LABELS.errors)).toEqual(expect.arrayContaining(['limit_reached', 'conflict', 'permission', 'network']));
    });
});
