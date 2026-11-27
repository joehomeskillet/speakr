import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    LIST_COLUMNS,
    HOSTED_CSS,
    WorklistView,
    createDetailClient,
    createWorklistViewController,
    flattenGroups,
    neighborRow,
    registerWorklistView,
    statusBadge,
} from '../../../static/js/clinic/compose-worklist.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-worklist.js'), 'utf8');

it('separates workflow and ASR labels without changing the combined status contract', () => {
    for (const [asr_state, processing] of Object.entries({
        PENDING: 'Transkription wartet', RUNNING: 'Transkription läuft',
        FAILED: 'Transkription fehlgeschlagen', SUCCEEDED: 'Transkription abgeschlossen',
    })) {
        expect(statusBadge({ status: 'vorbereitung_offline', asr_state })).toMatchObject({
            workflow: 'In Vorbereitung [Offline]', processing,
            label: 'In Vorbereitung [Offline] · ' + processing,
        });
    }
    expect(statusBadge({ status: 'unknown', asr_state: 'SKIPPED' })).toMatchObject({
        workflow: 'unknown', processing: undefined, label: 'unknown',
    });
});

const row = (id, extra = {}) => ({
    dictation_id: id,
    title: `Mustermann ${id}`,
    notes: 'Notiz',
    recipient: 'Empfaenger',
    status: 'zu_schreiben',
    priority: 1,
    duration_seconds: 60,
    author_user_id: 7,
    revision: 3,
    etag: `"${id}:3"`,
    allowed_actions: [],
    ...extra,
});

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status, message) => ({ outcome, status, code: null, message });

/** Fake clinic client: `routes` maps "METHOD path-prefix" to a handler or a fixed result. */
function fakeClient(routes = {}) {
    const calls = [];
    const find = (method, path) => {
        const key = Object.keys(routes).find((k) => {
            const [m, p] = k.split(' ');
            return m === method && path.startsWith(p);
        });
        return key ? routes[key] : null;
    };
    const run = async (path, options) => {
        const method = String((options && options.method) || 'GET').toUpperCase();
        calls.push({ method, path, options });
        const hit = find(method, path);
        if (hit === null) return ok({ items: [] });
        return typeof hit === 'function' ? hit(path, options) : hit;
    };
    return {
        calls,
        request: vi.fn(run),
        command: vi.fn(async (body, options) => run('/parity/commands', { method: 'POST', body, ...options })),
        switchContext: vi.fn(),
    };
}

const feed = () => vi.fn(async () => ({ outcome: 'ok', data: { changes: [], next_cursor: 'c1', has_more: false } }));
const user = { id: 7, role: 'staff' };
const layoutDoc = { version: 1, columns: [], sort: [], group_by: [] };

function controllerFor(client, extra = {}) {
    return createWorklistViewController({
        client,
        currentUser: user,
        fetchChanges: feed(),
        storage: null,
        intervalMs: 0,
        heartbeatMs: 30000,
        ...extra,
    });
}

const base = (rows) => ({
    'GET /worklist/rows': ok({ items: rows, total: rows.length }),
    'GET /worklist/layout': ok({ version: 1, layout: layoutDoc }),
    'GET /worklist/filters': ok({ items: [] }),
});

it('loads selected-row workflow capabilities from detail and ignores a late previous selection', async () => {
    const rows = [row('1'), row('2')];
    let first, second;
    const client = fakeClient({
        ...base(rows),
        'GET /api/clinic/v1/dictations/1/detail': () => new Promise((resolve) => { first = resolve; }),
        'GET /api/clinic/v1/dictations/2/detail': () => new Promise((resolve) => { second = resolve; }),
    });
    const c = controllerFor(client);
    await c.start();
    c.select(c.worklist.rows.value[0]);
    c.select(c.worklist.rows.value[1]);
    first(ok({ ...rows[0], allowed_actions: ['release'] }));
    await Promise.resolve();
    expect(c.workflow.state.allowed).toEqual([]);
    second(ok({ ...rows[1], allowed_actions: ['send'], transitions: { send: 'vorbereitung_online' } }));
    await vi.waitFor(() => expect(c.workflow.visible().map((action) => action.id)).toEqual(['send']));
    expect(c.workflow.state.dictationId).toBe('2');
    c.dispose();
});

describe('compose-worklist contract', () => {
    it('exports the view and registers it as clinic-worklist-view', () => {
        const app = { component: vi.fn() };
        registerWorklistView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-worklist-view', WorklistView);
        expect(Object.keys(WorklistView.props)).toEqual(expect.arrayContaining(['client', 'currentUser', 'vue']));
        expect(typeof WorklistView.setup).toBe('function');
    });

    it('composes the child modules (all but the unbound properties panel) and uses German labels, tokens and the api client', () => {
        for (const mod of [
            'worklist-ui', 'worklist-refresh-ui', 'worklist-filters-ui', 'worklist-layout-ui',
            'dictation-protection-ui', 'workflow-actions-ui',
            'acceptance-policy-ui', 'history-ui', 'lease-ui',
        ]) expect(source).toContain(`./${mod}.js`);
        expect(source).toContain("from './tokens.js'");
        expect(WorklistView.template).toContain('Arbeitsliste');
        expect(WorklistView.template).not.toContain('Fall-ID');
        expect(WorklistView.template).not.toContain('fall-id-filter');
        expect(WorklistView.template).toMatch(/aria-labelledby/);
        expect(WorklistView.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('renders no title, notes, recipient or Fall-ID (W16) and does not mount the properties panel', () => {
        expect(WorklistView.template).not.toMatch(/row\.(title|notes|recipient)\b/);
        expect(LIST_COLUMNS.map((c) => c.key)).not.toEqual(expect.arrayContaining(['title']));
        expect(source).not.toContain("./dictation-properties-ui.js");
        expect(WorklistView.template).not.toMatch(/Titel|Empf/);
    });

    it('requires a client and a current user id', () => {
        expect(() => createWorklistViewController({ currentUser: user })).toThrow(/client/);
        expect(() => createWorklistViewController({ client: fakeClient() })).toThrow(/currentUser/);
    });
});

describe('list behaviour', () => {
    it('loads rows through the injected change feed', async () => {
        const client = fakeClient(base([row('1'), row('2'), row('3')]));
        const fetchChanges = feed();
        const c = controllerFor(client, { fetchChanges });
        await c.start();
        expect(fetchChanges).toHaveBeenCalled();
        expect(c.worklist.rows.value).toHaveLength(3);
        expect(c.displayed.value.filter((i) => i.kind === 'row')).toHaveLength(3);
        expect(c.cell(row('1'), 'status')).toBe('Zu schreiben');
        c.dispose();
    });

    it('keyboard moves the selection in display order and Escape closes the details', async () => {
        const client = fakeClient(base([row('1'), row('2'), row('3')]));
        const c = controllerFor(client);
        await c.start();
        const prevented = vi.fn();
        c.select(c.worklist.rows.value[0]);
        c.onRowKey({ key: 'ArrowDown', preventDefault: prevented }, c.worklist.rows.value[0]);
        expect(c.selectedId.value).toBe('2');
        c.onRowKey({ key: 'ArrowUp', preventDefault: prevented }, c.worklist.rows.value[1]);
        expect(c.selectedId.value).toBe('1');
        c.onRowKey({ key: 'Escape', preventDefault: prevented }, c.worklist.rows.value[0]);
        expect(c.selectedId.value).toBeNull();
        expect(prevented).toHaveBeenCalled();
        expect(neighborRow([row('1')], row('1'), 1)).toBeNull();
        c.dispose();
    });

    it('groups rows into headings and rows', () => {
        const groups = [{ path: ['zu_schreiben'], rows: [row('1')], children: [] }, { path: ['abgenommen'], rows: [row('2')], children: [] }];
        const items = flattenGroups(groups, ['status']);
        expect(items.map((i) => i.kind)).toEqual(['group', 'row', 'group', 'row']);
        expect(items[0].label).toBe('Status: Zu schreiben');
    });

    it('a named filter reloads with filter_id and pauses polling; clearing resumes it', async () => {
        const client = fakeClient(base([row('1')]));
        const c = controllerFor(client, { intervalMs: 1000 });
        await c.start();
        expect(c.refresh.isRunning.value).toBe(true);
        await c.applyFilter('f1', 'F-1');
        const last = client.calls.filter((x) => x.path.startsWith('/worklist/rows')).pop();
        expect(last.path).toContain('filter_id=f1');
        expect(last.path).toContain('search=F-1');
        expect(c.refresh.isRunning.value).toBe(false);
        expect(c.paused.value).toBe(true);
        await c.applyFilter(null, '');
        expect(c.refresh.isRunning.value).toBe(true);
        c.dispose();
    });

    it('layout order and sort apply to the page of rows', async () => {
        const client = fakeClient(base([row('1', { priority: 0 }), row('2', { priority: 128 })]));
        const c = controllerFor(client);
        await c.start();
        c.layout.cycleSort('priority'); // ascending: Dringend first
        const ids = c.displayed.value.filter((i) => i.kind === 'row').map((i) => i.row.dictation_id);
        expect(ids).toEqual(['2', '1']);
        c.dispose();
    });
});

describe('detail scoping and teardown', () => {
    it('selecting a row scopes workflow actions to that dictation and capabilities come from the API', async () => {
        const rows = [row('1', { allowed_actions: ['release'], binding: { release: { transcript_revision_id: 't' } } }), row('2')];
        const c = controllerFor(fakeClient(base(rows)));
        await c.start();
        c.select(rows[0]);
        expect(c.selectedId.value).toBe('1');
        expect(c.workflow.state.dictationId).toBe('1');
        expect(c.workflow.visible().map((a) => a.id)).toEqual(['release']);
        c.select(rows[1]);
        expect(c.workflow.state.dictationId).toBe('2');
        expect(c.workflow.visible()).toEqual([]); // no capability from the API, no action
        expect(c.leaseState.value.status).toBe('idle');
        c.dispose();
    });

    it('changing the selection releases the held lease and stops its heartbeat', async () => {
        const expires = () => new Date(Date.now() + 60000).toISOString();
        const client = fakeClient({
            ...base([row('1'), row('2')]),
            'POST /dictations/1/leases/renew': () => ok({ lease_id: 'L1', generation: 1, expires_at: expires() }),
            'POST /dictations/1/leases/release': ok(null),
            'POST /dictations/1/leases': () => ok({ lease_id: 'L1', generation: 1, expires_at: expires() }),
        });
        const c = controllerFor(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        await c.leaseRun.acquire();
        expect(c.leaseState.value.status).toBe('held');
        c.select(c.worklist.rows.value[1]);
        await vi.waitFor(() => expect(client.calls.some((x) => x.path === '/dictations/1/leases/release')).toBe(true));
        expect(c.leaseState.value.status).toBe('idle');
        const renewals = () => client.calls.filter((x) => x.path.endsWith('/renew')).length;
        expect(renewals()).toBe(0);
        c.dispose();
    });

    it('a foreign lease (423) is shown as foreign', async () => {
        const client = fakeClient({ ...base([row('1')]), 'POST /dictations/1/leases': fail('UNKNOWN', 423, 'gesperrt') });
        const c = controllerFor(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        await c.leaseRun.acquire();
        expect(c.leaseState.value.status).toBe('foreign');
        c.dispose();
    });

    it('unmounting releases timers, the lease and clinical data and sends no further requests', async () => {
        vi.useFakeTimers();
        try {
            const expires = () => new Date(Date.now() + 600000).toISOString();
            const client = fakeClient({
                ...base([row('1')]),
                'POST /dictations/1/leases/release': ok(null),
                'POST /dictations/1/leases/renew': () => ok({ lease_id: 'L1', generation: 1, expires_at: expires() }),
                'POST /dictations/1/leases': () => ok({ lease_id: 'L1', generation: 1, expires_at: expires() }),
            });
            const c = controllerFor(client, { intervalMs: 1000 });
            await c.start();
            c.select(c.worklist.rows.value[0]);
            await c.leaseRun.acquire();
            c.dispose();
            expect(c.worklist.rows.value).toEqual([]);
            expect(c.refresh.isRunning.value).toBe(false);
            expect(client.switchContext).toHaveBeenCalledWith({ accountId: null, dictationId: null });
            await vi.advanceTimersByTimeAsync(120000);
            const after = client.calls.filter((x) => x.path.endsWith('/renew') || x.path.startsWith('/worklist/rows'));
            expect(after.filter((x) => x.path.endsWith('/renew'))).toHaveLength(0);
            expect(client.calls.some((x) => x.path === '/dictations/1/leases/release')).toBe(true);
        } finally {
            vi.useRealTimers();
        }
    });

    it('detail modules cannot switch the shared client context', async () => {
        const client = fakeClient();
        const detail = createDetailClient(client);
        detail.switchContext({ dictationId: 'x' });
        expect(client.switchContext).not.toHaveBeenCalled();
        await detail.request('/dictations/x/history');
        expect(client.request).toHaveBeenCalled();
    });
});

describe('unauthorized, stale and retry', () => {
    it('a 403 on the list drops rows and closes the detail', async () => {
        const client = fakeClient(base([row('1')]));
        const c = controllerFor(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        client.request.mockImplementation(async (path) => (path.startsWith('/worklist/rows')
            ? fail('permission', 403, 'Keine Berechtigung für diese Aktion.') : ok({ items: [] })));
        await c.reload();
        expect(c.worklist.isPermissionLost.value).toBe(true);
        expect(c.worklist.rows.value).toEqual([]);
        expect(c.selectedId.value).toBeNull();
        expect(c.refresh.isRunning.value).toBe(false);
        c.dispose();
    });

    it('a 403 on a filtered reload also shows the permission state', async () => {
        const client = fakeClient(base([row('1')]));
        const c = controllerFor(client);
        await c.start();
        client.request.mockImplementation(async () => fail('permission', 403, 'Keine Berechtigung für diese Aktion.'));
        await c.applyFilter('f1', '');
        expect(c.worklist.isPermissionLost.value).toBe(true);
        expect(c.worklist.rows.value).toEqual([]);
        c.dispose();
    });

    it('a stale layout version (409) reloads the layout and reports the conflict', async () => {
        const client = fakeClient({
            ...base([row('1')]),
            'PUT /worklist/layout': fail('conflict', 409, 'Der Stand ist veraltet.'),
        });
        const c = controllerFor(client);
        await c.start();
        c.layout.toggleVisible('status');
        expect(await c.layout.save()).toBe(false);
        expect(c.layout.state.status).toBe('stale');
        expect(c.layout.state.message).toContain('zwischenzeitlich geändert');
        c.dispose();
    });

    it('a stale workflow action (409) marks the panel stale and a success reloads the list', async () => {
        const rows = [row('1', { allowed_actions: ['release'], binding: { release: {
            original_version_id: 'audio', transcript_revision_id: 't', context_revision_id: 'context', text_sha256: 'hash',
        } } })];
        let attempt = 0;
        const client = fakeClient({
            ...base(rows),
            'GET /api/clinic/v1/dictations/1/leases': ok({ held: true, lease_id: 'lease', generation: 1 }),
            'POST /api/clinic/v1/dictations/1/releases': () => (++attempt === 1 ? fail('conflict', 409, 'Der Stand ist veraltet.') : ok({ etag: '"1:4"' })),
        });
        const c = controllerFor(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        c.workflow.request('release');
        Object.assign(c.workflow.state, { confirmedContent: true, confirmedContext: true });
        await c.workflow.confirm();
        expect(c.workflow.state.stale).toBe(true);
        const listCalls = () => client.calls.filter((x) => x.path.startsWith('/worklist/rows')).length;
        const before = listCalls();
        rows[0] = { ...rows[0], revision: 4, etag: '"1:4"' };
        await c.reload();
        c.select(c.worklist.rows.value[0]);
        c.workflow.request('release');
        Object.assign(c.workflow.state, { confirmedContent: true, confirmedContext: true });
        await c.workflow.confirm();
        await vi.waitFor(() => expect(listCalls()).toBeGreaterThan(before));
        c.dispose();
    });

    it('a failed refresh shows an error and a retry succeeds', async () => {
        let up = false;
        const client = fakeClient({
            ...base([row('1')]),
            'GET /worklist/rows': () => (up ? ok({ items: [row('1')], total: 1 }) : fail('network', null, 'Die Verbindung ist fehlgeschlagen.')),
        });
        const c = controllerFor(client);
        await c.start();
        expect(c.refresh.refreshError.value).toContain('fehlgeschlagen');
        up = true;
        await c.reload();
        expect(c.refresh.refreshError.value).toBeNull();
        expect(c.worklist.rows.value).toHaveLength(1);
        c.dispose();
    });

    it('a changed revision of the selected row reloads the workflow capabilities', async () => {
        const first = row('1', { allowed_actions: ['release'] });
        const client = fakeClient(base([first]));
        const c = controllerFor(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        expect(c.workflow.visible()).toHaveLength(1);
        client.request.mockImplementation(async (path) => (path.startsWith('/worklist/rows')
            ? ok({ items: [row('1', { revision: 4, etag: '"1:4"', allowed_actions: [] })], total: 1 }) : ok({ items: [] })));
        await c.reload();
        expect(c.workflow.visible()).toEqual([]);
        c.dispose();
    });
});

describe('Vue wiring', () => {
    it('setup mounts the controller lifecycle and unmount disposes it', async () => {
        const mounted = [];
        const unmounting = [];
        const vue = {
            ref: (v) => ({ value: v }),
            shallowRef: (v) => ({ value: v }),
            reactive: (o) => o,
            computed: (fn) => ({ get value() { return fn(); } }),
            defineComponent: (o) => o,
            onMounted: (fn) => mounted.push(fn),
            onBeforeUnmount: (fn) => unmounting.push(fn),
        };
        const client = fakeClient(base([row('1')]));
        const state = WorklistView.setup({
            client, currentUser: user, vue, fetchChanges: feed(), intervalMs: 0, autoStart: true,
        });
        expect(Object.keys(state.parts)).toEqual(expect.arrayContaining(['Layout', 'Lease', 'Workflow', 'Protection', 'Acceptance', 'History']));
        await Promise.all(mounted.map((fn) => fn()));
        expect(state.c.worklist.rows.value).toHaveLength(1);
        unmounting.forEach((fn) => fn());
        expect(state.c.worklist.rows.value).toEqual([]);
        expect(client.switchContext).toHaveBeenCalled();
    });
});

describe('review fixes', () => {
    it('keeps the V8 column set after "Neu laden" while no layout is saved', async () => {
        const all = LIST_COLUMNS.map(({ key }) => ({ key, width: 140, visible: true }));
        const client = fakeClient({
            ...base([row('1')]),
            'GET /worklist/layout': ok({ version: 0, layout: { version: 1, columns: all, sort: [], group_by: [] } }),
        });
        const c = controllerFor(client);
        await c.start();
        const before = c.columns().map((x) => x.key);
        expect(before.length).toBeLessThan(LIST_COLUMNS.length);
        await c.layout.load();
        expect(c.columns().map((x) => x.key)).toEqual(before);
        c.dispose();
    });

    it('drops the field class from the selects, the nowrap override and the shared dashed empty box', () => {
        expect(WorklistView.template).not.toContain('white-space: normal');
        expect(WorklistView.template).toContain('class="empty v8-empty"');
        expect(HOSTED_CSS).toContain('.v8-empty{border:0');
        const ui = ['worklist-filters-ui.js', 'worklist-layout-ui.js'].map((f) => readFileSync(join(__dirname, '../../../static/js/clinic/' + f), 'utf8'));
        for (const text of ui) expect(text).not.toMatch(/class="select field"/);
    });
});

describe('configured worklist columns', () => {
    it('uses active XML definitions, saved visibility and display formats even without a value on every row', async () => {
        const rows = [row('1', { station: 'Lab-A', exam: '2026-11-27T08:30:00Z', clinician: 'Ada Beispiel' }), row('2', { station: null })];
        const client = fakeClient({
            ...base(rows),
            'GET /metadata-fields': ok({ fields: [
                { name: 'station', label: 'Station', xml_path: 'patient/station', field_type: 'string', is_active: true },
                { name: 'exam', label: 'Untersuchung', xml_path: 'exam/time', field_type: 'datetime', display_format: 'DD.MM.YYYY HH:mm', is_active: true },
                { name: 'clinician', label: 'Behandelnde Person', xml_path: 'exam/name', field_type: 'string', display_format: 'Nachname, Vorname', is_active: true },
                { name: 'form', label: 'Formularfeld', xml_path: null, field_type: 'string', is_active: true },
                { name: 'old', label: 'Alt', xml_path: 'old', field_type: 'string', is_active: false },
                { name: 'title', label: 'Kollision', xml_path: 'private', field_type: 'string', is_active: true },
            ] }),
            'GET /worklist/layout': ok({ version: 3, layout: { ...layoutDoc,
                columns: [{ key: 'station', width: 140, visible: true }], group_by: ['station'],
            } }),
        });
        const c = controllerFor(client);
        await c.start();
        expect(c.columns()[0]).toMatchObject({ key: 'station', label: 'Station' });
        expect(c.columns().map(({ key }) => key)).not.toContain('exam');
        expect(c.layout.state.layout.columns.map(({ key }) => key)).not.toContain('form');
        expect(c.layout.state.layout.columns.map(({ key }) => key)).not.toContain('old');
        expect(c.layout.labelOf('title')).toBe('title');
        expect(c.displayed.value[0].label).toBe('Station: Lab-A');
        expect(c.cell(rows[0], 'station')).toBe('Lab-A');
        expect(c.cell(rows[1], 'station')).toBe('');
        c.layout.toggleVisible('exam');
        expect(c.columns().some(({ key }) => key === 'exam')).toBe(true);
        expect(c.cell(rows[0], 'exam')).toBe('27.11.2026 09:30');
        expect(c.cell(rows[0], 'clinician')).toBe('Beispiel, Ada');
        expect(c.cell({ clinician: 'Ada Marie Beispiel' }, 'clinician')).toBe('Ada Marie Beispiel');
        expect(c.cell({ exam: 'unbekannt' }, 'exam')).toBe('unbekannt');
        expect(rows[0].exam).toBe('2026-11-27T08:30:00Z');
        c.dispose();
    });
});
