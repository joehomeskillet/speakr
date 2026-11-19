import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createColumnsEditor, useColumnsEditor, columnsComponent, escapeHtml, KEYS, DISPLAY_TYPES, SERVER_CONTRACT,
} from '../../../static/js/clinic/columns-ui.js';

const FIELDS = [
    { name: 'station', label: 'Station', field_type: 'string', xml_path: 'patient/station', version: 'v2' },
    { name: 'age', label: 'Alter', field_type: 'integer', xml_path: 'patient/age', version: 'v1' },
    { name: 'secret', label: 'Geheim', field_type: 'string', xml_path: 'patient/secret', version: 'v1', readable: false },
    { name: 'gone', label: 'Alt', field_type: 'string', xml_path: 'old', version: 'v1', available: false },
    { name: 'off', label: 'Inaktiv', field_type: 'string', xml_path: 'off', version: 'v1', is_active: false },
];

function ok(data) {
    return { outcome: 'ok', data: data, message: '' };
}

// `put` answers PUT saves; GET returns the definitions. `request` overrides both.
function fakeClient(overrides) {
    const extra = Object.assign({}, overrides || {});
    const put = extra.put || vi.fn(async () => ok({ version: 'v3' }));
    delete extra.put;
    return Object.assign({
        put: put,
        request: vi.fn(async (path, opts) => (opts && opts.method === 'PUT' ? put(path, opts) : ok({ fields: FIELDS }))),
        switchContext: vi.fn(),
    }, extra);
}

async function loaded(overrides) {
    const client = fakeClient(overrides);
    const editor = createColumnsEditor({ client: client });
    await editor.load();
    return { client, editor };
}

describe('columns ui', () => {
    it('loads definitions and edits display type, label and xml path', async () => {
        const { editor } = await loaded();
        expect(editor.state.status).toBe('success');
        expect(editor.state.fields).toHaveLength(5);
        expect(editor.setDisplayType('station', 'date').ok).toBe(true);
        expect(editor.setXmlPath('station', ' patient/ward ').ok).toBe(true);
        expect(editor.setLabel('station', 'Abteilung').ok).toBe(true);
        expect(editor.state.fields[0]).toMatchObject({ displayType: 'date', xmlPath: 'patient/ward', label: 'Abteilung' });
        expect(editor.setXmlPath('station', '').ok).toBe(true);
        expect(editor.state.fields[0].xmlPath).toBe('');
    });

    it('rejects unsupported types and unbounded xml paths', async () => {
        const { editor } = await loaded();
        expect(editor.setDisplayType('station', 'blob').code).toBe('bad_display_type');
        expect(editor.setDisplayType('nope', 'date').code).toBe('unknown_field');
        for (const bad of ['../etc', '/abs', 'a//b', 'a/*', 'a[1]', '@attr', 'a/b/c/d/e/f/g/h/i', 'x'.repeat(257), '<!DOCTYPE x>']) {
            expect(editor.setXmlPath('station', bad).code).toBe('bad_xml_path');
        }
        expect(editor.setLabel('station', '  ').code).toBe('bad_label');
        expect(editor.state.fields[0].xmlPath).toBe('patient/station');
        expect(DISPLAY_TYPES).toContain('datetime');
    });

    it('rejects unavailable, inactive and unauthorized fields for edit and save', async () => {
        const { client, editor } = await loaded();
        expect(editor.setDisplayType('secret', 'date').code).toBe('unauthorized');
        expect(editor.setDisplayType('gone', 'date').code).toBe('unavailable');
        expect(editor.setXmlPath('off', 'a').code).toBe('unavailable');
        expect((await editor.save('secret')).code).toBe('unauthorized');
        expect(client.put).not.toHaveBeenCalled();
    });

    it('previews escaped values and rejects unavailable or unauthorized columns', async () => {
        const { editor } = await loaded();
        const good = editor.preview([
            { key: 'station', label: '<b>Station</b>', display_type: 'string', value: '<img src=x onerror=alert(1)>' },
            { key: 'age', label: 'Alter', display_type: 'integer', value: null },
        ]);
        expect(good.ok).toBe(true);
        expect(good.rows[0].value).toBe('&lt;img src=x onerror=alert(1)&gt;');
        expect(good.rows[0].label).toBe('&lt;b&gt;Station&lt;/b&gt;');
        expect(good.rows[1]).toMatchObject({ empty: true, value: 'Kein Wert' });
        expect(editor.preview([{ key: 'secret', label: 'x', display_type: 'string', value: 'v' }]).code).toBe('unauthorized');
        expect(editor.preview([{ key: 'gone', label: 'x', display_type: 'string', value: 'v' }]).code).toBe('unavailable');
        expect(editor.preview([{ key: 'zzz', label: 'x', display_type: 'string', value: 'v' }]).code).toBe('unknown_field');
        expect(editor.preview([{ key: '__proto__', label: 'x', display_type: 'string', value: 'v' }]).code).toBe('bad_key');
        expect(editor.preview([{ key: 'station', label: 'x', display_type: 'blob', value: 'v' }]).code).toBe('bad_display_type');
        expect(editor.preview(new Array(31).fill({ key: 'station' })).code).toBe('too_many');
        expect(escapeHtml(`&"'`)).toBe('&amp;&quot;&#39;');
    });

    it('saves through PUT with the version as expected_version', async () => {
        const { client, editor } = await loaded();
        editor.setDisplayType('station', 'date');
        expect((await editor.save('station')).ok).toBe(true);
        const [path, opts] = client.put.mock.calls[0];
        expect(path).toBe('/metadata-fields/station');
        expect(opts.method).toBe('PUT');
        expect(opts.body).toEqual({ label: 'Station', field_type: 'date', xml_path: 'patient/station', expected_version: 'v2' });
        expect(editor.state.fields[0].version).toBe('v3');
    });

    it('marks a stale version and refuses further saves until reload', async () => {
        const { client, editor } = await loaded({ put: vi.fn(async () => ({ outcome: 'conflict', status: 409, message: 'x' })) });
        expect((await editor.save('station')).code).toBe('stale_version');
        expect(editor.state.status).toBe('warning');
        expect((await editor.save('station')).code).toBe('stale_version');
        expect(client.put).toHaveBeenCalledTimes(1);
    });

    it('reports unauthorized and failure outcomes, and allows retry after failure', async () => {
        const denied = await loaded({ put: vi.fn(async () => ({ outcome: 'permission', status: 403, message: 'x' })) });
        expect((await denied.editor.save('station')).code).toBe('permission');
        const invalid = await loaded({ put: vi.fn(async () => ({ outcome: 'validation', status: 400, message: 'x' })) });
        expect((await invalid.editor.save('station')).code).toBe('validation');
        expect(invalid.editor.state.status).toBe('error');
        const command = vi.fn()
            .mockResolvedValueOnce({ outcome: 'network', message: 'x' })
            .mockResolvedValueOnce(ok({ version: 'v3' }));
        const flaky = await loaded({ put: command });
        expect((await flaky.editor.save('station')).code).toBe('failure');
        expect(flaky.editor.state.status).toBe('error');
        expect((await flaky.editor.save('station')).ok).toBe(true);
        expect(command).toHaveBeenCalledTimes(2);
        const noLoad = createColumnsEditor({ client: fakeClient({ request: vi.fn(async () => ({ outcome: 'permission', message: 'x' })) }) });
        expect((await noLoad.load()).code).toBe('permission');
        const failed = createColumnsEditor({ client: fakeClient({ request: vi.fn(async () => ({ outcome: 'network', message: 'x' })) }) });
        expect((await failed.load()).code).toBe('failure');
    });

    it('handles keyboard states', async () => {
        const { client, editor } = await loaded();
        expect(KEYS).toEqual({ save: 'Enter', revert: 'Escape' });
        editor.setLabel('station', 'Neu');
        expect((await editor.onKey('station', { key: 'Escape' })).ok).toBe(true);
        expect(editor.state.fields[0].label).toBe('Station');
        expect((await editor.onKey('station', { key: 'Enter' })).ok).toBe(true);
        expect(client.put).toHaveBeenCalledTimes(1);
        expect((await editor.onKey('station', { key: 'a' })).ok).toBe(false);
    });

    it('keeps clinical data out of browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const { editor } = await loaded();
        editor.preview([{ key: 'station', label: 'Station', display_type: 'string', value: 'Befund' }]);
        editor.reset();
        expect(writes).toEqual([]);
        expect(editor.state.fields).toEqual([]);
    });

    it('exposes a Vue composable and component with labels and roles', async () => {
        const reactive = vi.fn((v) => v);
        const composable = useColumnsEditor({ reactive }, { client: fakeClient() });
        expect(reactive).toHaveBeenCalled();
        expect(composable.labels.title).toBe('Metadatenspalten');
        const comp = columnsComponent({ reactive }, { client: fakeClient() });
        expect(comp.template).toContain('role="region"');
        expect(comp.template).toContain('aria-labelledby');
        expect(comp.template).toContain('<label>');
        expect(typeof comp.setup).toBe('function');
    });

    it('reports a missing server endpoint explicitly and documents the contract', async () => {
        const client = fakeClient({ request: vi.fn(async () => ({ outcome: 'UNKNOWN', status: 404, message: '' })) });
        const editor = createColumnsEditor({ client: client });
        const result = await editor.load();
        expect(result.ok).toBe(false);
        expect(result.code).toBe('endpoint_missing');
        expect(SERVER_CONTRACT.implemented).toBe(true);
        expect(SERVER_CONTRACT.list.path).toBe('/api/clinic/v1/metadata-fields');
        expect(SERVER_CONTRACT.save.method).toBe('PUT');
        expect(SERVER_CONTRACT.save.expected).toBe('expected_version');
    });

    it('uses no hard colours', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/columns-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
