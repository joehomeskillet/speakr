import { describe, expect, it, vi } from 'vitest';
import { createCautionStore, CAUTION_TEMPLATE } from '../../../static/js/clinic/compose-vocabulary.js';

const ok = (data) => ({ outcome: 'ok', data });
const list = (extra) => ok({
    scope: 'global', version: 2, words: ['Allergie', 'Verdacht'], published_by: 5, published_at: '2026-11-27T10:00:00+00:00',
    can_write: true, history: [{ version: 2, count: 2, published_by: 5, published_at: 'x' }], ...extra,
});

function fakeClient(handler) {
    return { request: vi.fn(async (path, opts) => handler(path, opts)) };
}

describe('caution words store', () => {
    it('loads the global list and publishes with the read version', async () => {
        const calls = [];
        const client = fakeClient((path, opts) => { calls.push([path, opts]); return list(); });
        const store = createCautionStore(client);
        await store.load();
        expect(store.state).toMatchObject({ status: 'ready', version: 2, words: ['Allergie', 'Verdacht'], canWrite: true });
        await store.publish('Embolie\n\n  Marcumar \n');
        const post = calls.find(([, o]) => o && o.method === 'POST');
        expect(post[0]).toBe('/vocabulary/caution-words/global');
        expect(post[1].body).toEqual({ words: ['Embolie', 'Marcumar'], expected_version: 2 });
    });

    it('sends the pool id for a pool list and refuses writes without permission', async () => {
        const client = fakeClient(() => list({ can_write: false }));
        const store = createCautionStore(client);
        await store.setScope('pool', 'p-1');
        expect(client.request.mock.calls[0][0]).toBe('/vocabulary/caution-words?scope=pool&pool_id=p-1');
        expect(await store.publish('X')).toBeNull();
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('reloads on a stale version and keeps the newer server state', async () => {
        let first = true;
        const client = fakeClient((path, opts) => {
            if (opts && opts.method === 'POST') return { outcome: 'conflict', status: 409 };
            if (first) { first = false; return list(); }
            return list({ version: 3, words: ['Neu'] });
        });
        const store = createCautionStore(client);
        await store.load();
        await store.publish('Alt');
        expect(store.state.version).toBe(3);
        expect(store.state.words).toEqual(['Neu']);
        expect(store.state.message).toContain('geändert');
    });

    it('shows denied for a refused pool and previews only for a pool', async () => {
        const client = fakeClient((path) => (path.includes('p-x') ? { outcome: 'permission', status: 403 }
            : ok({ version: 'g1.p1', hits: [{ word: 'Verdacht', offset: 0 }] })));
        const store = createCautionStore(client);
        expect(await store.preview('Verdacht')).toBeNull();
        await store.setScope('pool', 'p-x');
        expect(store.state.status).toBe('denied');
        store.state.scope = { kind: 'pool', poolId: 'p-1' };
        await store.preview('Verdacht');
        expect(client.request.mock.calls.at(-1)[1].body).toEqual({ pool_id: 'p-1', text: 'Verdacht' });
        expect(store.state.preview.hits).toEqual([{ word: 'Verdacht', offset: 0 }]);
    });

    it('has an empty state, labelled controls and no hard-coded colour', () => {
        expect(CAUTION_TEMPLATE).toContain('data-testid="caution-empty"');
        expect(CAUTION_TEMPLATE).toContain('for="cw-edit"');
        expect(CAUTION_TEMPLATE).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
