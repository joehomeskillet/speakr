import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createTemplatesManager,
    useTemplatesManager,
    templatesComponent,
    validateMarker,
    expandPlaceholders,
    KEYS,
} from '../../../static/js/clinic/templates-ui.js';

const ITEMS = [
    { id: 't1', name: 'Bericht', scope: 'user', version: 1, mime_type: 'application/vnd.oasis.opendocument.text', content_sha256: 'a' },
    { id: 't2', name: 'Bericht', scope: 'user', version: 2, mime_type: 'application/vnd.oasis.opendocument.text', content_sha256: 'b' },
    { id: 't3', name: 'Brief', scope: 'system', version: 1, mime_type: 'application/vnd.oasis.opendocument.text', content_sha256: 'c' },
];
const ODT = 'application/vnd.oasis.opendocument.text';

function ok(data, status) {
    return { outcome: 'ok', status: status || 200, data: data, message: '' };
}

function fakeClient(overrides) {
    return Object.assign({
        request: vi.fn(async (path, opts) => {
            if (opts && opts.method === 'POST') return ok({ id: 't9', name: 'Bericht', scope: 'user', version: 3, mime_type: ODT, content_sha256: 'd' }, 201);
            return ok({ items: ITEMS });
        }),
        switchContext: vi.fn(),
    }, overrides || {});
}

async function loaded(overrides) {
    const client = fakeClient(overrides);
    const manager = createTemplatesManager({ client: client });
    await manager.load();
    return { client, manager };
}

function failing(status, outcome) {
    return fakeClient({
        request: vi.fn(async (path, opts) => (opts && opts.method === 'POST'
            ? { outcome: outcome, status: status, message: 'x' }
            : ok({ items: ITEMS }))),
    });
}

const draft = () => ({ name: 'Bericht', scope: 'user', scopeId: '7', mimeType: ODT, bytes: new Uint8Array([1, 2, 3]), marker: '[[TEXT]]' });

describe('templates ui', () => {
    it('loads versions, selects and keeps the full history', async () => {
        const { manager } = await loaded();
        expect(manager.state.status).toBe('success');
        expect(manager.select('t1').ok).toBe(true);
        expect(manager.history('t1').map((r) => r.version)).toEqual([2, 1]);
        expect(manager.select('nope').code).toBe('unknown_template');
    });

    it('previews the insertion at the marker and substitutes placeholders by position', async () => {
        const { manager } = await loaded();
        manager.state.items[1].marker = '[[TEXT]]';
        const res = manager.previewInsertion('t2', 'Kopf [[TEXT]] Fuss', '%T von %A', { author: 'Dr. Muster', title: 'Bericht' });
        expect(res).toMatchObject({ ok: true, found: true, text: 'Kopf Bericht von Dr. Muster Fuss' });
        expect(expandPlaceholders('%A - %T', { author: 'X', title: 'Y' })).toBe('X - Y');
        expect(expandPlaceholders('%T - %A', { author: 'X', title: 'Y' })).toBe('Y - X');
        expect(expandPlaceholders('100%%', {})).toBe('100%');
        expect(manager.previewInsertion('t2', 'k', '%Z', {}).code).toBe('unknown_placeholder');
    });

    it('flags missing, invalid and absent markers and stale versions', async () => {
        const { manager } = await loaded();
        expect(manager.issues('t1').map((i) => i.code)).toEqual(['missing_marker', 'stale_version']);
        expect(manager.previewInsertion('t1', 'Text', 'Neu').warning).toBe('missing_marker');
        manager.state.items[1].marker = '<script>';
        expect(manager.issues('t2').map((i) => i.code)).toEqual(['invalid_marker']);
        expect(manager.previewInsertion('t2', 'Text', 'Neu').code).toBe('invalid_marker');
        manager.state.items[1].marker = '[[X]]';
        expect(manager.previewInsertion('t2', 'Text', 'Neu').warning).toBe('marker_not_found');
    });

    it('mirrors the server marker rules', () => {
        expect(validateMarker(null).ok).toBe(true);
        expect(validateMarker('[[A]]').ok).toBe(true);
        expect(validateMarker('  ').code).toBe('empty_marker');
        expect(validateMarker(' x').code).toBe('invalid_marker');
        expect(validateMarker('a\nb').code).toBe('invalid_marker');
        expect(validateMarker('x'.repeat(129)).code).toBe('invalid_marker');
        expect(validateMarker('</SCRIPT>').code).toBe('invalid_marker');
    });

    it('saves a new version with If-Match, idempotency key and digest, keeping older versions', async () => {
        const { client, manager } = await loaded();
        const res = await manager.save(draft());
        expect(res.ok).toBe(true);
        const [path, opts] = client.request.mock.calls[1];
        expect(path).toBe('/templates');
        expect(opts.method).toBe('POST');
        expect(opts.ifMatch).toBe('"0"');
        expect(opts.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
        expect(opts.body).toMatchObject({ name: 'Bericht', scope: { scope: 'user', scope_id: '7' }, insertion_mark: '[[TEXT]]', content_base64: 'AQID' });
        expect(opts.body.content_sha256).toMatch(/^[0-9a-f]{64}$/);
        expect(manager.state.items.length).toBe(4);
        expect(manager.state.items.find((r) => r.id === 't1')).toBeTruthy();
    });

    it('uses the latest known version as If-Match for an existing line', async () => {
        const { client, manager } = await loaded();
        await manager.save({ name: 'Bericht', scope: 'user', scopeId: null, mimeType: ODT, bytes: new Uint8Array([1]) });
        expect(client.request.mock.calls[1][1].ifMatch).toBe('"2"');
    });

    it('reuses the idempotency key on retry after a network failure', async () => {
        const client = failing(null, 'network');
        const manager = createTemplatesManager({ client: client });
        expect((await manager.save(draft())).code).toBe('failure');
        expect(manager.state.status).toBe('error');
        expect((await manager.save(draft())).code).toBe('failure');
        const keys = client.request.mock.calls.map((c) => c[1].idempotencyKey);
        expect(keys[0]).toBe(keys[1]);
        const changed = draft();
        changed.marker = '[[ANDERS]]';
        await manager.save(changed);
        expect(client.request.mock.calls[2][1].idempotencyKey).not.toBe(keys[0]);
    });

    it('marks a stale version and refuses saves until reload', async () => {
        const client = failing(412, 'conflict');
        const manager = createTemplatesManager({ client: client });
        expect((await manager.save(draft())).code).toBe('stale_version');
        expect(manager.state.status).toBe('warning');
        expect((await manager.save(draft())).code).toBe('stale_version');
        expect(client.request).toHaveBeenCalledTimes(1);
        await manager.load();
        expect(manager.state.stale).toBe(false);
    });

    it('reports scope errors, invalid markers from the server and oversize content', async () => {
        expect((await createTemplatesManager({ client: failing(403, 'permission') }).save(draft())).code).toBe('scope_error');
        expect((await createTemplatesManager({ client: failing(422, 'validation') }).save(draft())).code).toBe('invalid_marker');
        expect((await createTemplatesManager({ client: failing(413, 'UNKNOWN') }).save(draft())).code).toBe('too_large');
        const denied = createTemplatesManager({ client: fakeClient({ request: vi.fn(async () => ({ outcome: 'permission', status: 403, message: 'x' })) }) });
        expect((await denied.load()).code).toBe('permission');
    });

    it('rejects an incomplete draft or invalid marker before any request', async () => {
        const { client, manager } = await loaded();
        expect((await manager.save({ name: 'x' })).code).toBe('invalid_draft');
        expect((await manager.save(Object.assign(draft(), { marker: ' bad' }))).code).toBe('invalid_marker');
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('assigns a template to another scope without touching the original', async () => {
        const { client, manager } = await loaded();
        const res = await manager.assign('t3', 'pool', '5');
        expect(res.ok).toBe(true);
        const [path, opts] = client.request.mock.calls[1];
        expect(path).toBe('/templates/t3/assign');
        expect(opts.body).toEqual({ scope: { scope: 'pool', scope_id: '5' } });
        expect(manager.state.items.find((r) => r.id === 't3').scope).toBe('system');
        expect((await manager.assign('nope', 'user')).code).toBe('unknown_template');
        const denied = await loaded({ request: vi.fn(async (p, o) => (o && o.method ? { outcome: 'permission', status: 403, message: 'x' } : ok({ items: ITEMS }))) });
        expect((await denied.manager.assign('t3', 'pool', '9')).code).toBe('scope_error');
    });

    it('handles keyboard states', async () => {
        const { manager } = await loaded();
        expect(KEYS.next).toBe('ArrowDown');
        manager.onKey({ key: 'ArrowDown' });
        expect(manager.state.selectedId).toBe('t1');
        manager.onKey({ key: 'ArrowDown' });
        expect(manager.state.selectedId).toBe('t2');
        manager.onKey({ key: 'ArrowUp' });
        expect(manager.state.selectedId).toBe('t1');
        manager.onKey({ key: 'Escape' });
        expect(manager.state.selectedId).toBe(null);
        expect(manager.onKey({ key: 'a' }).ok).toBe(false);
    });

    it('keeps clinical data out of browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const { manager } = await loaded();
        await manager.save(draft());
        manager.reset();
        expect(writes).toEqual([]);
        expect(manager.state.items).toEqual([]);
    });

    it('exposes a Vue composable and component with labels and roles', async () => {
        const reactive = vi.fn((v) => v);
        const composable = useTemplatesManager({ reactive }, { client: fakeClient() });
        expect(reactive).toHaveBeenCalled();
        expect(composable.labels.title).toBe('Vorlagen');
        const comp = templatesComponent({ reactive }, { client: fakeClient() });
        expect(comp.template).toContain('aria-label');
        expect(comp.template).toContain('role="listbox"');
        expect(typeof comp.setup).toBe('function');
    });

    it('uses no hard colours', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/templates-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('hydrates server metadata and versions an own-user upload after reload', async () => {
        const client = fakeClient({ request: vi.fn(async (path, opts) => {
            if (opts?.method === 'POST') return ok({ ...ITEMS[1], id: 't4', version: 3 });
            if (path === '/templates') return ok({ items: ITEMS });
            return ok({ ...ITEMS.find((item) => path.endsWith('/' + item.id)), scope_id: '7',
                insertion_mark: '[[TEXT]]', notes: 'Nur synthetisch', is_active: true });
        }) });
        const manager = createTemplatesManager({ client });
        expect((await manager.refresh()).ok).toBe(true);
        expect(manager.state.items[0]).toMatchObject({ scopeId: '7', marker: '[[TEXT]]', notes: 'Nur synthetisch', active: true });
        expect(manager.history('t1').map((row) => row.version)).toEqual([2, 1]);
        await manager.save({ ...draft(), scopeId: null });
        expect(client.request.mock.calls.at(-1)[1].ifMatch).toBe('"2"');
    });

    it('reports failed metadata loads and does not fabricate marker data', async () => {
        const manager = createTemplatesManager({ client: fakeClient({ request: vi.fn(async (path) =>
            path === '/templates' ? ok({ items: ITEMS }) : { outcome: 'permission', status: 403 }) }) });
        expect((await manager.refresh()).code).toBe('permission');
        expect(manager.state.status).toBe('warning');
        expect(manager.state.items[0].marker).toBe(null);
    });

    it('matches the server system scope when saving another version', async () => {
        const { client, manager } = await loaded();
        manager.state.items[2].scopeId = '__system__';
        await manager.save({ ...draft(), name: 'Brief', scope: 'system', scopeId: null });
        expect(client.request.mock.calls.at(-1)[1].ifMatch).toBe('"1"');
    });

    it('marks remotely deactivated versions without requesting their denied detail endpoint', async () => {
        const { client, manager } = await loaded();
        client.request.mockResolvedValue(ok({ items: [] }));
        expect((await manager.refresh()).ok).toBe(true);
        expect(client.request).toHaveBeenCalledTimes(2);
        expect(manager.state.items.every((row) => !row.active)).toBe(true);
        expect(manager.history('t1')).toHaveLength(2);
    });

    it('deactivates only after successful DELETE and retains version history', async () => {
        const { client, manager } = await loaded();
        manager.select('t2');
        expect((await manager.deactivate('t2')).ok).toBe(true);
        expect(client.request).toHaveBeenLastCalledWith('/templates/t2', { method: 'DELETE' });
        expect(manager.state.items[1].active).toBe(false);
        expect(manager.history('t2').map((row) => row.version)).toEqual([2, 1]);
        expect(manager.state.selectedId).toBe('t2');
        expect((await manager.deactivate('t2')).code).toBe('unknown_template');
        client.request.mockResolvedValue({ outcome: 'permission', status: 403 });
        expect((await manager.deactivate('t1')).code).toBe('permission');
        expect(manager.state.items[0].active).toBe(true);
    });
});

describe('template editor actions', () => {
    async function editor() {
        const rows = [];
        const client = fakeClient({ request: vi.fn(async (path, options = {}) => {
            if (options.method === 'POST') {
                const body = options.body;
                const source = path.endsWith('/assign') ? rows.find((row) => path.includes('/' + row.id + '/')) : body;
                const same = rows.filter((row) => row.name === source.name && row.scope === body.scope.scope && row.scope_id === (body.scope.scope_id || '7'));
                const row = { ...source, id: 'new' + rows.length, scope: body.scope.scope, scope_id: body.scope.scope_id || '7',
                    version: same.length + 1, is_active: true };
                rows.push(row);
                return ok(row, 201);
            }
            if (options.method === 'DELETE') {
                rows.find((row) => path.endsWith('/' + row.id)).is_active = false;
                return ok({ ok: true });
            }
            return path === '/templates' ? ok({ items: rows.filter((row) => row.is_active) }) : ok(rows.find((row) => path.endsWith('/' + row.id)));
        }) });
        const ui = templatesComponent({ reactive: (value) => value }, { client }).setup();
        await vi.waitFor(() => expect(ui.form.ready).toBe(true));
        return { ui, client, rows };
    }
    const file = (name, size = 3) => ({ name, size, arrayBuffer: vi.fn(async () => new Uint8Array([1, 2, 3]).buffer) });

    it.each(['docx', 'odt'])('uploads %s with metadata, assigns a pool, previews and deactivates', async (extension) => {
        const { ui, client, rows } = await editor();
        Object.assign(ui.form, { file: file('synthetic.' + extension), name: 'Bericht', notes: 'Synthetisch', marker: '[[TEXT]]' });
        await ui.upload();
        expect(ui.form.error).toBe(false);
        const post = client.request.mock.calls.find((call) => call[1]?.method === 'POST')[1];
        expect(post.body).toMatchObject({ name: 'Bericht', notes: 'Synthetisch', insertion_mark: '[[TEXT]]', content_base64: 'AQID', scope: { scope: 'user' } });
        expect(post.body.mime_type).toBe(extension === 'odt' ? ODT : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
        expect(ui.selected().scopeId).toBe('7');
        Object.assign(ui.form, { target: 'pool', targetId: '5' });
        await ui.assign();
        expect(rows[0].scope).toBe('user');
        expect(rows[1]).toMatchObject({ scope: 'pool', scope_id: '5', notes: 'Synthetisch', insertion_mark: '[[TEXT]]' });
        expect(ui.contentUrl(rows[0].id)).toBe('/api/clinic/v1/templates/new0/content');
        Object.assign(ui.form, { sample: 'Kopf [[TEXT]] Ende', text: '%T von %A', author: 'Muster', title: 'Bericht' });
        expect(ui.preview().text).toBe('Kopf Bericht von Muster Ende');
        await ui.upload();
        expect(ui.manager.history(ui.manager.state.selectedId).map((row) => row.version)).toEqual([2, 1]);
        await ui.deactivate();
        expect(ui.selected().active).toBe(false);
        expect(ui.form.message).toBe('Vorlage deaktiviert.');
    });

    it('rejects wrong extensions, empty/oversized files, missing pools and invalid markers without upload', async () => {
        const { ui, client } = await editor();
        ui.form.name = 'Bericht';
        for (const chosen of [file('bad.pdf'), file('empty.odt', 0), file('large.docx', 2 * 1024 * 1024 + 1)]) {
            ui.form.file = chosen;
            await ui.upload();
            expect(ui.form.error).toBe(true);
            expect(chosen.arrayBuffer).not.toHaveBeenCalled();
        }
        Object.assign(ui.form, { file: file('valid.odt'), scope: 'pool', scopeId: '' });
        await ui.upload();
        expect(ui.form.error).toBe(true);
        ui.form.scopeId = '5';
        ui.form.marker = ' bad';
        await ui.upload();
        expect(ui.form.message).toContain('Einfügemarke');
        expect(client.request.mock.calls.some((call) => call[1]?.method === 'POST')).toBe(false);
    });

    it('shows read failures and guards double submissions', async () => {
        const { ui, client } = await editor();
        Object.assign(ui.form, { name: 'Bericht', file: file('broken.odt') });
        ui.form.file.arrayBuffer.mockRejectedValue(new Error('read failed'));
        await ui.upload();
        expect(ui.form.error).toBe(true);
        expect(ui.form.busy).toBe(false);
        expect(ui.form.message).toBe('Die Aktion ist fehlgeschlagen.');
        ui.form.busy = true;
        await ui.upload();
        expect(client.request).toHaveBeenCalledTimes(1);
    });
});
