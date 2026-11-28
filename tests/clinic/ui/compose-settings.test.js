import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createSettingsController, LABELS, ORIGIN_LABELS, originLabel, registerSettingsView, SettingsView, SETTINGS_TEMPLATE,
} from '../../../static/js/clinic/compose-settings.js';
import { createPreferencesComponent } from '../../../static/js/clinic/preferences-ui.js';
import { CorrectionModes } from '../../../static/js/clinic/correction-modes-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const denied = { outcome: 'permission', status: 403, code: 'FORBIDDEN', message: 'Keine Berechtigung' };

// Shapes of routes_preferences.py (recipient-default, workstations/<id>, correction-mode).
const DEFAULT = { version: 2, mode: 'person', target_ids: ['9'], choice: 'open', overrides: { person: ['9'], staff_pool: [] } };
const WORKSTATION = {
    values: { use_speech_recognition: true, language: 'de', theme: 'light' },
    origins: { use_speech_recognition: 'workstation', language: 'central', theme: 'ceiling' },
    version: 4,
};

function fakeVue() {
    const hooks = { mounted: [], unmount: [], watchers: [] };
    return {
        hooks,
        reactive: (o) => o,
        ref: (v) => ({ value: v }),
        shallowRef: (v) => ({ value: v }),
        markRaw: (o) => o,
        computed: (fn) => ({ get value() { return fn(); } }),
        watch: (getter, cb) => hooks.watchers.push({ getter, cb }),
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
    };
}

function fakeClient(handler) {
    return {
        request: vi.fn(async (path, opts) => (handler ? handler(path, opts) : ok({}))),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

const routes = (path) => {
    if (path === '/preferences/recipient-default') return ok(DEFAULT);
    if (path === '/preferences/workstations/ws-1') return ok(WORKSTATION);
    if (path === '/preferences/correction-mode') return ok({ mode: 'type_while_playing', version: 1 });
    return ok({});
};

describe('settings view composition', () => {
    it('exports the registration contract and reuses the existing building blocks', () => {
        const app = { component: vi.fn() };
        registerSettingsView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-settings-view', SettingsView);
        expect(Object.keys(SettingsView.props)).toEqual(['client', 'currentUser', 'workstationId', 't', 'vue']);
        const src = readFileSync(new URL('../../../static/js/clinic/compose-settings.js', import.meta.url), 'utf8');
        expect(src).toContain("from './preferences-ui.js'");
        expect(src).toContain("from './correction-modes-ui.js'");
        expect(src).toContain("from './tokens.js'");
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        const view = SettingsView.setup({ client: fakeClient(), currentUser: { id: 1 }, workstationId: 'ws-1', vue: fakeVue() });
        expect(view.preferences.name).toBe(createPreferencesComponent(fakeVue(), fakeClient()).name);
        expect(view.correction).toBe(CorrectionModes);
    });

    it('renders effective values with their origin from the real API shapes', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const view = SettingsView.setup({ client, currentUser: { id: 7 }, workstationId: 'ws-1', vue: Vue });
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '7', dictationId: null });
        expect(view.rows()).toEqual([]);
        // The preferences child loads through the tapped client; the origins appear without a second request.
        const child = view.preferences.setup();
        await Vue.hooks.mounted[0]();
        expect(client.request.mock.calls.map((c) => c[0])).toEqual([
            '/preferences/recipient-default', '/preferences/workstations/ws-1',
        ]);
        expect(view.rows()).toEqual([
            { key: 'use_speech_recognition', origin: 'workstation', label: 'Persönlich (Arbeitsplatz)', locked: false },
            { key: 'language', origin: 'central', label: 'Zentral festgelegt', locked: true },
            { key: 'theme', origin: 'ceiling', label: 'Zentral begrenzt', locked: true },
        ]);
        expect(child.view.value.settings.find((x) => x.key === 'language').locked).toBe(true);
        expect(view.state.forbidden).toBe(false);
        expect(view.state.denied).toBe(false);
    });

    it('opens settings with a server-issued workstation and inherited editable values', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => {
            if (path === '/preferences/workstations') return ok({ workstation_id: 'ws-1', defaults: { editor_autosave: false } });
            return routes(path);
        });
        const view = SettingsView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        const child = view.preferences.setup();
        await Vue.hooks.mounted[0]();
        expect(client.request).toHaveBeenCalledWith('/preferences/workstations', { method: 'POST', body: { reuse: true } });
        expect(client.request).toHaveBeenCalledWith('/preferences/workstations/ws-1', undefined);
        expect(child.view.value.settings.find((field) => field.key === 'editor_autosave')).toMatchObject({
            value: false, origin: 'Persönlich (Konto)', locked: false,
        });
    });

    it('does not publish an origin response from the previous account', async () => {
        let resolve;
        const client = fakeClient(() => new Promise((done) => { resolve = done; }));
        const ctrl = createSettingsController({ client, currentUser: { id: 7 } });
        const pending = ctrl.client.request('/preferences/workstations/ws-1');
        ctrl.setUser({ id: 8 });
        resolve(ok(WORKSTATION));
        expect(await pending).toMatchObject({ outcome: 'cancelled' });
        expect(ctrl.originRows()).toEqual([]);
    });

    it('offers the correction mode preference without audio and keeps the saved mode', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const view = SettingsView.setup({ client, currentUser: { id: 7 }, workstationId: 'ws-1', vue: Vue });
        expect(view.audio.isPlaying()).toBe(false);
        const modes = view.correction.setup({ audio: view.audio, client: view.client, vue: Vue });
        await modes.load();
        expect(modes.mode.value).toBe('type_while_playing');
        expect(client.request).toHaveBeenCalledWith('/preferences/correction-mode', undefined);
    });

    it('shows the 403 state and hides all content', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => (path === '/preferences/recipient-default' ? denied : ok({})));
        const view = SettingsView.setup({ client, currentUser: { id: 7 }, workstationId: 'ws-1', vue: Vue });
        view.preferences.setup();
        await Vue.hooks.mounted[0]();
        expect(view.state.forbidden).toBe(true);
        expect(view.labels.forbidden).toBe('Keine Berechtigung für diese Einstellungen.');
        expect(SETTINGS_TEMPLATE).toContain('v-else-if="state.forbidden"');
        expect(SETTINGS_TEMPLATE).toContain('role="alert"');
        // Content sits in the final v-else branch only.
        expect(SETTINGS_TEMPLATE.indexOf('state.forbidden')).toBeLessThan(SETTINGS_TEMPLATE.indexOf(':is="preferences"'));
        expect(view.errorStyle.color).toBe('var(--text-danger-strong)');
    });

    it('shows the empty state when there are no personal values', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => (path.startsWith('/preferences/workstations/')
            ? ok({ values: {}, origins: {}, version: 0 }) : routes(path)));
        const view = SettingsView.setup({ client, currentUser: { id: 7 }, workstationId: 'ws-1', vue: Vue });
        view.preferences.setup();
        await Vue.hooks.mounted[0]();
        expect(view.rows()).toEqual([]);
        expect(SETTINGS_TEMPLATE).toContain('data-testid="clinic-settings-empty"');
        expect(LABELS.empty).toBe('Keine eigenen Werte. Es gelten die Standardwerte.');
    });

    it('refuses an unauthenticated user and loads nothing', () => {
        const client = fakeClient();
        const ctrl = createSettingsController({ client, currentUser: null });
        expect(ctrl.state.denied).toBe(true);
        expect(SETTINGS_TEMPLATE).toContain('v-if="state.denied"');
        expect(client.request).not.toHaveBeenCalled();
    });

    it('scopes state to the current user and drops origins on user change', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const props = { client, currentUser: { id: 1 }, workstationId: 'ws-1', vue: Vue };
        const view = SettingsView.setup(props);
        view.preferences.setup();
        await Vue.hooks.mounted[0]();
        expect(view.rows()).toHaveLength(3);
        const epoch = view.state.epoch;
        props.currentUser = { id: 2 };
        const watcher = Vue.hooks.watchers[0];
        expect(watcher.getter()).toBe('2');
        watcher.cb();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        expect(view.rows()).toEqual([]);
        expect(view.state.epoch).toBe(epoch + 1);
        watcher.cb();
        expect(view.state.epoch).toBe(epoch + 1);
    });

    it('releases in-flight calls on unmount and ignores late answers', async () => {
        const Vue = fakeVue();
        const client = fakeClient(routes);
        const ctrl = createSettingsController({ client, currentUser: { id: 3 } });
        Vue.hooks.unmount.length = 0;
        ctrl.dispose();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        await ctrl.client.request('/preferences/workstations/ws-1');
        expect(ctrl.originRows()).toEqual([]);
        const before = client.switchContext.mock.calls.length;
        ctrl.dispose();
        expect(client.switchContext.mock.calls.length).toBe(before);
    });

    it('labels every known origin in german and flags unknown ones', () => {
        expect(Object.values(ORIGIN_LABELS)).toEqual([
            'Persönlich (Arbeitsplatz)', 'Zentral festgelegt', 'Zentral begrenzt',
        ]);
        expect(originLabel('workstation')).toBe('Persönlich (Arbeitsplatz)');
        expect(originLabel('workstation')).not.toBe('Unbekannte Herkunft');
        expect(originLabel('pool')).toBe('Unbekannte Herkunft');
        expect(originLabel('bogus')).toBe('Unbekannte Herkunft');
        expect(originLabel('toString')).toBe('Unbekannte Herkunft');
    });
});
