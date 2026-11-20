import { describe, expect, it, vi } from 'vitest';
import {
    createGrantsHost, createRegexHost, createPanelDefs, createRootComponent, mountClinicAdmin,
} from '../../../static/js/clinic/admin-clinic-tab.js';

// Minimal stand-in for window.Vue: refs are plain {value}, computed re-evaluates on read.
function fakeVue() {
    const mounted = [];
    const created = [];
    const Vue = {
        ref: (value) => ({ value }),
        reactive: (value) => value,
        markRaw: (value) => value,
        computed: (fn) => ({ get value() { return fn(); } }),
        onMounted: (fn) => mounted.push(fn),
        createApp: vi.fn((root) => {
            const app = { root, mount: vi.fn(), component: vi.fn() };
            created.push(app);
            return app;
        }),
    };
    return { Vue, mounted, created };
}

const PANEL_IDS = ['accounts', 'pools', 'grants', 'directory', 'templates', 'categories', 'regex', 'columns', 'preferences'];

describe('panel definitions', () => {
    it('lists the nine panels in order with the probe each one uses', () => {
        const { Vue } = fakeVue();
        const client = { request: vi.fn() };
        const defs = createPanelDefs(Vue, client, 7, true);
        expect(defs.map((d) => d.id)).toEqual(PANEL_IDS);
        expect(defs.find((d) => d.id === 'grants').probe).toBe('/identity/users/7/grants');
        expect(defs.find((d) => d.id === 'regex').probe).toBeNull();
        expect(defs.find((d) => d.id === 'directory').props.currentUser).toEqual({ is_admin: false });
        expect(createPanelDefs(Vue, client, 7, false).find((d) => d.id === 'directory').props.currentUser).toEqual({ is_admin: true });
    });
});

describe('root component', () => {
    function build(team, results) {
        const { Vue, mounted } = fakeVue();
        const client = { request: vi.fn(async (path) => results[path] || { outcome: 'ok' }) };
        const defs = PANEL_IDS.map((id) => ({ id, label: id.toUpperCase(), probe: id === 'regex' ? null : '/' + id }));
        const root = createRootComponent(Vue, client, defs, team);
        const state = root.setup();
        return { state, mounted, client, root };
    }

    it('shows every panel at once on the admin page and starts on the first', () => {
        const { state, mounted, client } = build(false, {});
        expect(state.shown.value).toHaveLength(PANEL_IDS.length);
        expect(state.activeId.value).toBe('accounts');
        expect(state.probing.value).toBe(false);
        mounted.forEach((fn) => fn());
        expect(client.request).not.toHaveBeenCalled();
    });

    it('on the team page probes each panel and withholds the ones the API refuses', async () => {
        const { state, mounted, client } = build(true, { '/pools': { outcome: 'forbidden' }, '/columns': { outcome: 'forbidden' } });
        expect(state.probing.value).toBe(true);
        expect(state.shown.value).toEqual([]);
        mounted.forEach((fn) => fn());
        await vi.waitFor(() => expect(state.probing.value).toBe(false));
        expect(client.request).toHaveBeenCalledTimes(PANEL_IDS.length - 1); // regex has no probe
        expect(state.shown.value.map((d) => d.id)).not.toContain('pools');
        expect(state.withheld.value).toEqual(['POOLS', 'COLUMNS']);
        expect(state.withheldText.value).toBe('Für dieses Konto nicht freigegeben: POOLS, COLUMNS.');
        expect(state.activeId.value).toBe('accounts');
        expect(state.active.value.id).toBe('accounts');
    });

    it('keeps the panels without a probe when every probe is refused', async () => {
        const refuse = Object.fromEntries(PANEL_IDS.filter((id) => id !== 'regex').map((id) => ['/' + id, { outcome: 'forbidden' }]));
        const { state, mounted } = build(true, refuse);
        mounted.forEach((fn) => fn());
        await vi.waitFor(() => expect(state.probing.value).toBe(false));
        expect(state.shown.value.map((d) => d.id)).toEqual(['regex']);
    });
});

describe('hosts', () => {
    it('grants host opens on the viewer id and ignores an invalid id', () => {
        const { Vue } = fakeVue();
        const host = createGrantsHost(Vue, {}, 12, false);
        const state = host.setup();
        expect(state.draft.value).toBe('12');
        state.draft.value = 'abc';
        state.open();
        expect(state.panel.value).toBeNull();
        expect(state.key.value).toBe(1);
        expect(host.template).toContain('Fachrechte laden');
    });

    it('regex host needs a dictation id before it shows the panel', () => {
        const { Vue } = fakeVue();
        const host = createRegexHost(Vue, { tag: 'client' });
        const state = host.setup();
        state.open();
        expect(state.panel.value).toBeNull();
        state.draft.value = ' d-1 ';
        state.open();
        expect(state.dictationId.value).toBe('d-1');
        expect(state.panel.value).not.toBeNull();
        expect(state.client).toEqual({ tag: 'client' });
    });
});

describe('mountClinicAdmin', () => {
    function fakeDoc({ insideApp, appReady }) {
        const root = { dataset: { mode: 'admin', userId: '3' } };
        const attrs = new Set(appReady ? ['data-v-app'] : []);
        const host = { contains: () => insideApp, hasAttribute: (name) => attrs.has(name), attrs };
        const doc = { getElementById: (id) => (id === 'clinic-admin-root' ? root : id === 'app' ? host : null) };
        return { doc, host, root };
    }
    const client = { request: vi.fn() };

    it('mounts at once when the container is outside the page app', () => {
        const { Vue, created } = fakeVue();
        const { doc } = fakeDoc({ insideApp: false, appReady: false });
        mountClinicAdmin({ Vue, doc, client });
        expect(created).toHaveLength(1);
        expect(created[0].mount).toHaveBeenCalledOnce();
    });

    it('waits for the page app to finish when the container sits inside it', () => {
        const { Vue, created } = fakeVue();
        const { doc, host } = fakeDoc({ insideApp: true, appReady: false });
        let callback;
        const observer = { observe: vi.fn(), disconnect: vi.fn() };
        const MutationObserverImpl = function (cb) { callback = cb; return observer; };
        mountClinicAdmin({ Vue, doc, client, MutationObserverImpl });
        expect(created).toHaveLength(0);
        expect(observer.observe).toHaveBeenCalledWith(host, { attributes: true });
        callback([], observer);            // attribute changed but the app is not ready
        expect(created).toHaveLength(0);
        host.attrs.add('data-v-app');
        callback([], observer);
        expect(observer.disconnect).toHaveBeenCalledOnce();
        expect(created).toHaveLength(1);
    });

    it('mounts at once when the page app is already mounted', () => {
        const { Vue, created } = fakeVue();
        const { doc } = fakeDoc({ insideApp: true, appReady: true });
        mountClinicAdmin({ Vue, doc, client });
        expect(created).toHaveLength(1);
    });
});
