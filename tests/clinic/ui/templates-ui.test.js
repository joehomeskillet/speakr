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
});
