import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createCategoriesEditor, useCategoriesEditor, categoriesComponent, KEYS } from '../../../static/js/clinic/categories-ui.js';

const SETS = [
    { id: 's1', name: 'Befund', version: 3, categories: ['Normal', 'Auffällig'], required: true, target_field: 'notes', scope: 'system' },
    { id: 's2', name: 'Optional', version: 1, categories: ['A'], required: false, target_field: 'tags', scope: 'user' },
];

function ok(data) {
    return { outcome: 'ok', data: data, message: '' };
}

function fakeClient(overrides) {
    return Object.assign({
        request: vi.fn(async (_path, options) => options
            ? ok({ ...SETS[0], ...options.body, version: 4 })
            : ok({ sets: SETS })),
        switchContext: vi.fn(),
    }, overrides || {});
}

async function loaded(overrides) {
    const client = fakeClient(overrides);
    const editor = createCategoriesEditor({ client: client });
    await editor.load();
    return { client, editor };
}

describe('categories ui', () => {
    it('loads sets and previews mandatory fields', async () => {
        const { editor } = await loaded();
        expect(editor.state.status).toBe('success');
        const rows = editor.preview();
        expect(rows[0]).toMatchObject({ setId: 's1', field: 'notes', required: true, satisfied: false });
        expect(rows[1].satisfied).toBe(true);
        editor.select('s1', 'Normal');
        expect(editor.preview()[0].satisfied).toBe(true);
    });

    it('edits drafts and rejects invalid edits', async () => {
        const { editor } = await loaded();
        expect(editor.addCategory('s1', '  ').code).toBe('empty_category');
        expect(editor.addCategory('s1', 'normal').code).toBe('duplicate_category');
        expect(editor.addCategory('s1', 'Kritisch').ok).toBe(true);
        expect(editor.state.sets[0].categories).toContain('Kritisch');
        expect(editor.removeCategory('nope', 'x').code).toBe('unknown_set');
    });

    it('rejects removal that invalidates a pending selection', async () => {
        const { editor } = await loaded();
        editor.select('s1', 'Normal');
        const refusal = editor.removeCategory('s1', 'Normal');
        expect(refusal).toMatchObject({ ok: false, code: 'pending_selection_invalid' });
        expect(editor.state.sets[0].categories).toEqual(['Normal', 'Auffällig']);
        expect(editor.removeCategory('s1', 'Auffällig').ok).toBe(true);
        expect(editor.setRequired('s2', true).ok).toBe(true);
    });

    it('rejects an empty required group', async () => {
        const { editor } = await loaded();
        editor.removeCategory('s1', 'Normal');
        expect(editor.removeCategory('s1', 'Auffällig').code).toBe('no_category');
    });

    it('replaces a set at the expected version and uses the returned revision', async () => {
        const { client, editor } = await loaded();
        editor.addCategory('s1', 'Kritisch');
        expect((await editor.save('s1')).ok).toBe(true);
        const [path, opts] = client.request.mock.calls[1];
        expect(opts.body).toEqual({ id: 's1', version: 3, required: true, categories: ['Normal', 'Auffällig', 'Kritisch'] });
        expect(path).toBe('/categories');
        expect(opts.method).toBe('PUT');
        expect(client.request).toHaveBeenCalledTimes(2);
        expect(editor.state.sets[0].version).toBe(4);
    });

    it('creates a required set and can update the returned server set', async () => {
        const { client, editor } = await loaded();
        client.request.mockResolvedValueOnce(ok({
            id: 's3', version: 1, name: 'Testbereich', categories: ['Rot', 'Blau'],
            required: true, scope: 'system', target_field: 'notes',
        }));
        expect(editor.createSet('  Testbereich  ').ok).toBe(true);
        expect((await editor.save(null)).ok).toBe(false);
        editor.addCategory(null, 'Rot');
        editor.addCategory(null, 'Blau');
        editor.setRequired(null, true);
        expect((await editor.save(null)).ok).toBe(true);
        expect(client.request).toHaveBeenNthCalledWith(2, '/categories', {
            method: 'POST', body: { name: 'Testbereich', categories: ['Rot', 'Blau'], required: true },
        });
        expect(editor.state.sets.find(row => row.id === 's3')).toMatchObject({ required: true, version: 1 });
        editor.setRequired('s3', false);
        expect((await editor.save('s3')).ok).toBe(true);
        expect(client.request).toHaveBeenNthCalledWith(3, '/categories', {
            method: 'PUT', body: { id: 's3', version: 1, categories: ['Rot', 'Blau'], required: false },
        });
    });

    it('marks a stale version and refuses further saves until reload', async () => {
        const { client, editor } = await loaded();
        client.request.mockResolvedValueOnce({ outcome: 'conflict', message: 'x' });
        expect((await editor.save('s1')).code).toBe('stale_version');
        expect(editor.state.status).toBe('warning');
        expect((await editor.save('s1')).code).toBe('stale_version');
        expect(client.request).toHaveBeenCalledTimes(2);
    });

    it('reports unauthorized and failure outcomes', async () => {
        const denied = await loaded();
        denied.client.request.mockResolvedValueOnce({ outcome: 'permission', message: 'x' });
        expect((await denied.editor.save('s1')).code).toBe('permission');
        const broken = await loaded();
        broken.client.request.mockResolvedValueOnce({ outcome: 'network', message: 'x' });
        expect((await broken.editor.save('s1')).code).toBe('failure');
        expect(broken.editor.state.status).toBe('error');
        const noLoad = createCategoriesEditor({ client: fakeClient({ request: vi.fn(async () => ({ outcome: 'permission', message: 'x' })) }) });
        expect((await noLoad.load()).code).toBe('permission');
    });

    it('handles keyboard states', async () => {
        const { editor } = await loaded();
        expect(KEYS.add).toBe('Enter');
        expect(editor.onKey('s1', { key: 'Enter' }, 'Neu').ok).toBe(true);
        expect(editor.onKey('s1', { key: 'Delete' }, 'Neu').ok).toBe(true);
        expect(editor.state.sets[0].categories).not.toContain('Neu');
        expect(editor.onKey('s1', { key: 'a' }, 'x').ok).toBe(false);
    });

    it('keeps clinical data out of browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const { editor } = await loaded();
        editor.select('s1', 'Normal');
        editor.reset();
        expect(writes).toEqual([]);
        expect(editor.state.selections).toEqual({});
    });

    it('exposes a Vue composable and component with labels and roles', async () => {
        const reactive = vi.fn((v) => v);
        const composable = useCategoriesEditor({ reactive }, { client: fakeClient() });
        expect(reactive).toHaveBeenCalled();
        expect(composable.labels.title).toBe('Kategorien');
        const comp = categoriesComponent({ reactive }, { client: fakeClient() });
        expect(comp.template).toContain('aria-label');
        expect(comp.template).toContain('role="region"');
        expect(typeof comp.setup).toBe('function');
    });

    it('uses no hard colours', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/categories-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
