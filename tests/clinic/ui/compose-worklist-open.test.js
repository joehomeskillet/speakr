import { describe, expect, it, vi } from 'vitest';

import {
    WorklistView,
    createWorklistViewController,
} from '../../../static/js/clinic/compose-worklist.js';

const row = (id, extra = {}) => ({
    dictation_id: id,
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

function fakeClient(routes = {}) {
    const run = async (path, options) => {
        const method = String((options && options.method) || 'GET').toUpperCase();
        const key = Object.keys(routes).find((k) => {
            const [m, p] = k.split(' ');
            return m === method && path.startsWith(p);
        });
        if (!key) return ok({ items: [] });
        return typeof routes[key] === 'function' ? routes[key](path, options) : routes[key];
    };
    return {
        request: vi.fn(run),
        command: vi.fn(async (body, options) => run('/parity/commands', { method: 'POST', body, ...options })),
        switchContext: vi.fn(),
    };
}

const feed = () => vi.fn(async () => ({ outcome: 'ok', data: { changes: [], next_cursor: 'c1', has_more: false } }));
const layoutDoc = { version: 1, columns: [], sort: [], group_by: [] };
const base = (rows, lease) => ({
    'GET /worklist/rows': ok({ items: rows, total: rows.length }),
    'GET /worklist/layout': ok({ version: 1, layout: layoutDoc }),
    'GET /worklist/filters': ok({ items: [] }),
    ...(lease || {}),
});

async function started(rows, role, extra = {}, routes) {
    const onOpen = vi.fn();
    const c = createWorklistViewController({
        client: fakeClient(base(rows, routes)),
        currentUser: { id: 7, role },
        fetchChanges: feed(),
        storage: null,
        intervalMs: 0,
        heartbeatMs: 30000,
        onOpen,
        ...extra,
    });
    await c.start();
    return { c, onOpen };
}

const enter = (extra = {}) => ({ key: 'Enter', preventDefault: vi.fn(), ...extra });

describe('worklist open event', () => {
    it('declares the open emit and a German, labelled open button in the detail panel', () => {
        expect(WorklistView.emits).toEqual(['open']);
        expect(WorklistView.template).toContain('@dblclick="c.open(item.row)"');
        expect(WorklistView.template).toMatch(/data-testid="open-dictation"[^>]*:disabled="!c\.canOpen\.value"[^>]*@click="c\.open\(\)"/);
        expect(WorklistView.template).toContain('c.text.open');
        expect(createWorklistViewController({
            client: fakeClient(), currentUser: { id: 7 },
        }).text.open).toBe('Öffnen');
        expect(WorklistView.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('Enter on a row opens it with the dictation id and a typist mode', async () => {
        const { c, onOpen } = await started([row('1'), row('2')], 'typist');
        const ev = enter();
        c.onRowKey(ev, c.worklist.rows.value[1]);
        expect(ev.preventDefault).toHaveBeenCalled();
        expect(c.selectedId.value).toBe('2');
        expect(onOpen).toHaveBeenCalledTimes(1);
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '2', mode: 'player' });
        c.dispose();
    });

    it('double-click (open on a row) selects and opens it', async () => {
        const { c, onOpen } = await started([row('1', { status: 'korrektur' })], 'staff');
        expect(c.open(c.worklist.rows.value[0])).toBe(true);
        expect(c.selectedId.value).toBe('1');
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '1', mode: 'correction' });
        c.dispose();
    });

    it('opens the first row clicked if the preview shifts rows under the pointer', async () => {
        const { c, onOpen } = await started([row('1'), row('2')], 'typist');
        const target = (id) => ({ closest: () => ({ dataset: { id } }) });
        c.rememberFirstClick({ detail: 1, target: target('1') });
        c.rememberFirstClick({ detail: 2, target: target('2') });
        const stopPropagation = vi.fn();
        c.keepDoubleClickOnRow({ target: target('2'), stopPropagation });
        expect(stopPropagation).toHaveBeenCalledOnce();
        expect(onOpen).toHaveBeenCalledOnce();
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '1', mode: 'player' });
        c.dispose();
    });

    it('the Öffnen button opens the selected row', async () => {
        const { c, onOpen } = await started([row('1'), row('2')], 'typist');
        c.select(c.worklist.rows.value[0]);
        expect(c.canOpen.value).toBe(true);
        c.open();
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '1', mode: 'player' });
        c.dispose();
    });

    it('opens a document in the correction editor even when queued for typing', async () => {
        const { c, onOpen } = await started([row('1', { open_mode: 'player', source_kind: 'document' })], 'typist');
        c.open(c.worklist.rows.value[0]);
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '1', mode: 'correction' });
        c.dispose();
    });

    it('author gets the author mode; a server open_mode wins', async () => {
        const author = await started([row('1'), row('2', { open_mode: 'custom_mode' })], 'author');
        author.c.open(author.c.worklist.rows.value[0]);
        author.c.open(author.c.worklist.rows.value[1]);
        expect(author.onOpen.mock.calls.map((x) => x[0])).toEqual([
            { dictationId: '1', mode: 'author_case' },
            { dictationId: '2', mode: 'custom_mode' },
        ]);
        author.c.dispose();
        const typist = await started([row('3', { open_mode: 'custom_mode' })], 'typist');
        typist.c.open(typist.c.worklist.rows.value[0]);
        expect(typist.onOpen).toHaveBeenCalledWith({ dictationId: '3', mode: 'custom_mode' });
        typist.c.dispose();
    });

    it('emits the owner when the server identifies an own case in a typist worklist', async () => {
        const { c, onOpen } = await started([row('1', { open_mode: 'author_case' })], 'typist');
        c.open(c.worklist.rows.value[0]);
        expect(onOpen).toHaveBeenCalledWith({ dictationId: '1', mode: 'author_case', author_user_id: 7 });
        c.dispose();
    });

    it('single selection, arrow keys, space and Escape never emit open', async () => {
        const { c, onOpen } = await started([row('1'), row('2')], 'typist');
        const rows = c.worklist.rows.value;
        c.select(rows[0]);
        c.onRowKey({ key: 'ArrowDown', preventDefault: vi.fn() }, rows[0]);
        c.onRowKey({ key: ' ', preventDefault: vi.fn() }, rows[1]);
        c.onRowKey({ key: 'Escape', preventDefault: vi.fn() }, rows[1]);
        expect(onOpen).not.toHaveBeenCalled();
        expect(WorklistView.template).toContain('@click="c.select(item.row)"');
        c.dispose();
    });

    it('does not emit without a selection and the button is disabled', async () => {
        const { c, onOpen } = await started([row('1')], 'typist');
        expect(c.canOpen.value).toBe(false);
        expect(c.open()).toBe(false);
        expect(onOpen).not.toHaveBeenCalled();
        c.dispose();
    });

    it('does not emit and disables the button while the lease is foreign or lost', async () => {
        const { c, onOpen } = await started([row('1')], 'typist');
        c.select(c.worklist.rows.value[0]);
        for (const status of ['foreign', 'lost']) {
            c.leaseState.value = { ...c.leaseState.value, status };
            expect(c.canOpen.value).toBe(false);
            expect(c.open()).toBe(false);
            c.onRowKey(enter(), c.worklist.rows.value[0]);
        }
        expect(onOpen).not.toHaveBeenCalled();
        c.dispose();
    });

    it('setup forwards the open payload through the component emit', async () => {
        const vue = {
            ref: (v) => ({ value: v }),
            shallowRef: (v) => ({ value: v }),
            reactive: (o) => o,
            computed: (fn) => ({ get value() { return fn(); } }),
            defineComponent: (o) => o,
            onMounted: () => {},
            onBeforeUnmount: () => {},
        };
        const emit = vi.fn();
        const state = WorklistView.setup({
            client: fakeClient(base([row('5')])), currentUser: { id: 7, role: 'typist' }, vue,
            fetchChanges: feed(), intervalMs: 0, autoStart: false,
        }, { emit });
        await state.c.start();
        state.c.open(state.c.worklist.rows.value[0]);
        expect(emit).toHaveBeenCalledWith('open', { dictationId: '5', mode: 'player' });
        state.c.dispose();
    });
});
