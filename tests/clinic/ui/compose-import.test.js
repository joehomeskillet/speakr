import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    ENDPOINTS, ImportView, IMPORT_TEMPLATE, createImportController, mayClarify, registerImportView, statusView,
} from '../../../static/js/clinic/compose-import.js';
import {
    ImportSourceRouting, LANGUAGE_OPTIONS, languageLabel, priorityLabel,
} from '../../../static/js/clinic/import-source-routing.js';

const ok = (data) => ({ outcome: 'ok', data });
const INTENTS = [
    { id: 'i1', status: 'OPEN', source_ref: 'dev-a', expected_files: ['a.dss'], created_at: '2026-11-20T08:00:00Z' },
    { id: 'i2', status: 'FAILED', source_ref: 'dev-b', expected_files: ['b.dss', 'c.dss'], created_at: '2026-11-20T08:05:00Z' },
];
const INVENTORY = [{ source_id: 'dev-a', root_id: 'r', relative_path: 'a.dss', size: 10, mtime_utc: '2026-11-20T07:00:00Z', snapshot_version: 's1' }];
const UNASSIGNED = [{ id: 'd1', source_id: 'dev-a', relative_path: 'x.dss', snapshot_version: 's1' }];

function fakeClient(overrides) {
    const routes = {
        [ENDPOINTS.intents]: () => ok({ items: INTENTS }),
        [ENDPOINTS.inventory]: () => ok({ items: INVENTORY }),
        [ENDPOINTS.unassigned]: () => ok({ items: UNASSIGNED }),
        ...(overrides || {}),
    };
    return {
        request: vi.fn(async (path, opts) => {
            const h = routes[path];
            return h ? h(opts) : ok({});
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}
const calls = (client, path) => client.request.mock.calls.filter((c) => c[0] === path);

describe('import view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerImportView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-import-view', ImportView);
        expect(Object.keys(ImportView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('renders the list with a status per item and the device inventory', async () => {
        const c = createImportController({ client: fakeClient(), currentUser: { id: 5 } });
        await c.load();
        expect(c.state.intents.map((r) => r.id)).toEqual(['i1', 'i2']);
        expect(c.statusView('OPEN').label).toBe('Ausstehend');
        expect(c.statusView('FAILED').label).toBe('Fehlgeschlagen');
        expect(c.statusView('FULFILLED').label).toBe('Übernommen');
        expect(c.state.inventory).toHaveLength(1);
        expect(statusView('OPEN').style.border).toContain('var(--');
        expect(IMPORT_TEMPLATE).toContain('data-testid="intent-list"');
        expect(IMPORT_TEMPLATE).toContain('data-testid="inventory-list"');
    });

    it('shows no device detection without an agent report', async () => {
        const c = createImportController({ client: fakeClient({ [ENDPOINTS.inventory]: () => ok({ items: [] }) }), currentUser: { id: 5 } });
        await c.load();
        expect(c.state.inventory).toEqual([]);
        expect(c.text.inventoryEmpty).toContain('keine Geräteerkennung');
    });

    it('W13: an ordinary typist gets no clarification list and never requests it', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 5, role: 'typist' } });
        await c.load();
        expect(c.canClarify).toBe(false);
        expect(calls(client, ENDPOINTS.unassigned)).toHaveLength(0);
        expect(c.askAssign('d1')).toBe(false);
        expect(await c.confirmAssign('d1', 'i1', 's1')).toBeNull();
        expect(calls(client, ENDPOINTS.assign('d1'))).toHaveLength(0);
        expect(readFileSync(new URL('../../../static/js/clinic/compose-import.js', import.meta.url), 'utf8')).toContain('v-if="c.canClarify"');
    });

    it('W13: admins and pool leads see the clarification list', async () => {
        expect(mayClarify({ role: 'admin' })).toBe(true);
        expect(mayClarify({ isPoolLead: true })).toBe(true);
        expect(mayClarify({ role: 'typist' })).toBe(false);
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        expect(c.state.unassigned.map((r) => r.id)).toEqual(['d1']);
    });

    it('a server refusal hides the clarification list', async () => {
        const client = fakeClient({ [ENDPOINTS.unassigned]: () => ({ outcome: 'permission' }) });
        const c = createImportController({ client, currentUser: { id: 1, isPoolLead: true } });
        await c.load();
        expect(c.state.clarificationDenied).toBe(true);
        expect(c.state.unassigned).toEqual([]);
        expect(c.askAssign('d1')).toBe(false);
    });

    it('loads candidates and submits the author and pool chosen by a routed lead', async () => {
        const candidate = { target_author_user_id: 6, target_pool_id: 'p2', author_name: 'Test', pool_name: 'Pool 2' };
        const client = fakeClient({
            [ENDPOINTS.inventory]: () => ok({ items: INVENTORY, can_clarify: true }),
            '/imports/unassigned/d1/candidates': () => ok({ items: [candidate] }),
        });
        const c = createImportController({ client, currentUser: { id: 5, role: 'typist' } });
        await c.load();
        expect(c.canClarify).toBe(true);
        expect(c.state.unassigned).toEqual(UNASSIGNED);
        expect(c.askAssign('d1')).toBe(true);
        await c.loadCandidates('d1');
        expect(c.state.candidates).toEqual([candidate]);
        await c.confirmAssign('d1', candidate, 's1');
        expect(calls(client, ENDPOINTS.assign('d1'))[0][1].body).toEqual({
            target_author_user_id: 6, target_pool_id: 'p2', expected_snapshot_version: 's1',
        });
    });

    it('assignment needs a confirmation step and sends one request with a key', async () => {
        const client = fakeClient({ [ENDPOINTS.assign('d1')]: () => ok({}) });
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        expect(await c.confirmAssign('d1', 'i1', 's1')).toBeNull(); // not confirmed yet
        expect(calls(client, ENDPOINTS.assign('d1'))).toHaveLength(0);
        c.askAssign('d1');
        await c.confirmAssign('d1', 'i1', 's1');
        const sent = calls(client, ENDPOINTS.assign('d1'));
        expect(sent).toHaveLength(1);
        expect(sent[0][1].method).toBe('POST');
        expect(sent[0][1].body).toEqual({ intent_id: 'i1', expected_snapshot_version: 's1' });
        expect(sent[0][1].idempotencyKey.length).toBeGreaterThanOrEqual(8);
        expect(c.state.pending).toBeNull();
    });

    it('retry calls the API once per click with the same key until it succeeds', async () => {
        let n = 0;
        const client = fakeClient({ [ENDPOINTS.retry('i2')]: () => (++n < 3 ? { outcome: 'network' } : ok({})) });
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        await c.retry('i2');
        await c.retry('i2');
        await c.retry('i2');
        const sent = calls(client, ENDPOINTS.retry('i2'));
        expect(sent).toHaveLength(3);
        const keys = new Set(sent.map((s) => s[1].idempotencyKey));
        expect(keys.size).toBe(1);
        // after success a later retry gets a fresh key
        await c.retry('i2');
        const all = calls(client, ENDPOINTS.retry('i2'));
        expect(all[3][1].idempotencyKey).not.toBe(all[0][1].idempotencyKey);
    });

    it('a double click while a retry is in flight sends one request', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient({ [ENDPOINTS.retry('i2')]: async () => { await gate; return ok({}); } });
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        const first = c.retry('i2');
        const second = await c.retry('i2');
        expect(second).toBeNull();
        release();
        await first;
        expect(calls(client, ENDPOINTS.retry('i2'))).toHaveLength(1);
    });

    it('only failed or expired items can be retried', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 5 } });
        await c.load();
        expect(await c.retry('i1')).toBeNull();
        expect(calls(client, ENDPOINTS.retry('i1'))).toHaveLength(0);
    });

    it('dispose releases data and the client context', async () => {
        const client = fakeClient();
        const c = createImportController({ client, currentUser: { id: 1, role: 'admin' } });
        await c.load();
        c.dispose();
        expect(c.state.intents).toEqual([]);
        expect(c.state.unassigned).toEqual([]);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
    });

    it('uses no browser storage and no patient fields', () => {
        const src = readFileSync(new URL('../../../static/js/clinic/compose-import.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(src).not.toMatch(/patient_(name|ref|display|reference)/);
    });
});

function makeVueStub() {
    const mounted = [];
    return {
        ref: (initial) => ({ value: initial }),
        nextTick: (fn) => Promise.resolve().then(fn),
        onMounted: (fn) => mounted.push(fn),
        runMounted: () => Promise.all(mounted.map((fn) => fn())),
    };
}

describe('import source routing (dropfolders and devices)', () => {
    const AUTHORS = [{ id: 1, name: 'Admin' }, { id: 6, name: 'Dr. Test' }];
    const POOLS = [{ id: 'p6', name: 'Pool Test', author_id: 6 }];

    it('loads authors and pools when starting with zero sources', async () => {
        const vue = makeVueStub();
        const client = fakeClient({
            '/imports/sources': () => ok({ items: [], authors: AUTHORS, pools: POOLS }),
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();
        expect(vm.access.value).toBe(true);
        expect(vm.sources.value).toEqual([]);
        expect(vm.authors.value).toEqual(AUTHORS);
        expect(vm.pools.value).toEqual(POOLS);
    });

    it('hides routing when server refuses access', async () => {
        const vue = makeVueStub();
        const client = fakeClient({
            '/imports/sources': () => ({ outcome: 'permission' }),
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();
        expect(vm.access.value).toBe(false);
    });

    it('creates a dropfolder with defaults and sends null for empty optional fields', async () => {
        const vue = makeVueStub();
        const createdItem = {
            id: 'f1', source_kind: 'folder', source_ref: 'labor', approved_root: '/srv/dropfolders/labor',
            target_author_user_id: 6, target_pool_id: 'p6', file_pattern: '*.ds2', priority: 128,
            language_id: 1033, asr_profile_id: null, policy_version: 1,
        };
        let items = [];
        const client = fakeClient({
            '/imports/sources': (opts) => {
                if (opts?.method === 'POST') {
                    items = [createdItem];
                    return ok(createdItem);
                }
                return ok({ items, authors: AUTHORS, pools: POOLS });
            },
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();

        vm.startCreate();
        expect(vm.creating.value).toBe(true);
        expect(vm.form.value.file_pattern).toBe('*');
        expect(vm.form.value.priority).toBe(1);
        expect(vm.form.value.language_id).toBe(1031);
        expect(vm.form.value.target_author_user_id).toBeNull();
        vm.form.value.source_ref = 'labor';
        vm.form.value.approved_root = '/srv/dropfolders/labor';
        await vm.save();
        expect(calls(client, '/imports/sources')).toHaveLength(1);
        expect(vm.error.value).toContain('Person');

        vm.form.value.target_author_user_id = 6;
        vm.form.value.target_pool_id = 'p6';
        vm.form.value.file_pattern = '*.ds2';
        vm.form.value.priority = 128;
        vm.form.value.language_id = 1033;
        vm.form.value.asr_profile_id = '   ';
        await vm.save();

        const postCall = client.request.mock.calls.find((c) => c[0] === '/imports/sources' && c[1]?.method === 'POST');
        expect(postCall[1].body).toEqual({
            source_kind: 'folder',
            source_ref: 'labor',
            approved_root: '/srv/dropfolders/labor',
            target_author_user_id: 6,
            target_pool_id: 'p6',
            file_pattern: '*.ds2',
            priority: 128,
            language_id: 1033,
            asr_profile_id: null,
        });
        expect(vm.creating.value).toBe(false);
        expect(vm.sources.value).toEqual([createdItem]);
        expect(vm.notice.value).toBe('Ordner angelegt.');
    });

    it('keeps the create draft on server conflict or disallowed root', async () => {
        const vue = makeVueStub();
        const client = fakeClient({
            '/imports/sources': (opts) => (opts?.method === 'POST' ? { outcome: 'conflict' } : ok({ items: [], authors: AUTHORS, pools: POOLS })),
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();
        vm.startCreate();
        vm.form.value.source_ref = 'dup';
        vm.form.value.approved_root = '/srv/dropfolders/dup';
        vm.form.value.target_author_user_id = 6;
        await vm.save();
        expect(vm.creating.value).toBe(true);
        expect(vm.form.value.source_ref).toBe('dup');
        expect(vm.error.value).toContain('existiert bereits');
    });

    it('edits folder options and device routing without leaking folder fields onto devices', async () => {
        const vue = makeVueStub();
        const folder = {
            id: 'f1', source_kind: 'folder', source_ref: 'labor', approved_root: '/srv/dropfolders/a',
            target_author_user_id: 6, target_pool_id: 'p6', file_pattern: '*.ds2', priority: 128,
            language_id: 1033, asr_profile_id: 'prof-1', policy_version: 1,
        };
        const device = {
            id: 'd1', source_kind: 'device', source_ref: 'usb-1',
            target_author_user_id: 1, target_pool_id: null, policy_version: 2,
        };
        const client = fakeClient({
            '/imports/sources': () => ok({ items: [folder, device], authors: AUTHORS, pools: POOLS }),
            '/imports/sources/f1': () => ok({ ...folder, policy_version: 2 }),
            '/imports/sources/d1': () => ok({ ...device, target_author_user_id: 6, target_pool_id: 'p6', policy_version: 3 }),
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();

        vm.edit(folder);
        vm.form.value.approved_root = '/srv/dropfolders/b';
        vm.form.value.priority = 0;
        vm.form.value.asr_profile_id = '';
        vm.form.value.target_author_user_id = 1;
        vm.onAuthorChange();
        expect(vm.form.value.target_pool_id).toBeNull();
        await vm.save();

        const putFolder = client.request.mock.calls.find((c) => c[0] === '/imports/sources/f1' && c[1]?.method === 'PUT');
        expect(putFolder[1].body).toEqual({
            expected_version: 1,
            target_author_user_id: 1,
            target_pool_id: null,
            approved_root: '/srv/dropfolders/b',
            file_pattern: '*.ds2',
            priority: 0,
            language_id: 1033,
            asr_profile_id: null,
        });
        expect(putFolder[1].idempotencyKey).toBeTruthy();

        vm.edit(device);
        vm.form.value.target_author_user_id = 6;
        vm.onAuthorChange();
        vm.form.value.target_pool_id = 'p6';
        await vm.save();

        const putDevice = client.request.mock.calls.find((c) => c[0] === '/imports/sources/d1' && c[1]?.method === 'PUT');
        expect(putDevice[1].body).toEqual({
            expected_version: 2,
            target_author_user_id: 6,
            target_pool_id: 'p6',
        });
        expect(vm.notice.value).toBe('Importzuordnung gespeichert.');
    });

    it('requires confirmation before removing a folder and ignores device removal', async () => {
        const vue = makeVueStub();
        const folder = { id: 'f1', source_kind: 'folder', source_ref: 'labor', approved_root: '/srv/dropfolders/a', target_author_user_id: 1, target_pool_id: null, policy_version: 1 };
        const device = { id: 'd1', source_kind: 'device', source_ref: 'usb-1', target_author_user_id: 1, target_pool_id: null, policy_version: 1 };
        let items = [folder, device];
        const client = fakeClient({
            '/imports/sources': () => ok({ items, authors: AUTHORS, pools: POOLS }),
            '/imports/sources/f1': (opts) => {
                if (opts?.method === 'DELETE') {
                    items = [device];
                    return { outcome: 'ok', status: 204, data: null };
                }
                return ok(folder);
            },
        });
        const vm = ImportSourceRouting.setup({ client, vue });
        await vue.runMounted();

        vm.askRemove(device);
        expect(vm.confirmDeleteId.value).toBeNull();

        vm.askRemove(folder);
        expect(vm.confirmDeleteId.value).toBe('f1');
        vm.cancelRemove();
        expect(vm.confirmDeleteId.value).toBeNull();
        expect(calls(client, '/imports/sources/f1')).toHaveLength(0);

        vm.askRemove(folder);
        await vm.confirmRemove(folder);
        const del = calls(client, '/imports/sources/f1');
        expect(del).toHaveLength(1);
        expect(del[0][1]).toEqual({ method: 'DELETE' });
        expect(vm.sources.value).toEqual([device]);
        expect(vm.notice.value).toBe('Ordnerkonfiguration entfernt.');
    });

    it('formats priorities, known languages, and preserves unknown language ids', () => {
        expect(priorityLabel(0)).toBe('Bei Gelegenheit');
        expect(priorityLabel(1)).toBe('Normal');
        expect(priorityLabel(128)).toBe('Dringend');
        expect(LANGUAGE_OPTIONS.some((l) => l.value === 1031)).toBe(true);
        expect(languageLabel(1031)).toBe('Deutsch');
        expect(languageLabel(1033)).toBe('Englisch');
        expect(languageLabel(3079)).toBe('Sprache 3079');
    });

    it('uses no browser storage, patient fields, or hard-coded hex colours', () => {
        const src = readFileSync(new URL('../../../static/js/clinic/import-source-routing.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(src).not.toMatch(/patient_(name|ref|display|reference)/);
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
