import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    COMMANDS_TEMPLATES_TEMPLATE, CommandsTemplatesView, EDITOR_HOOK, KINDS, TEXT,
    createCommandsTemplatesController, registerCommandsTemplatesView,
} from '../../../static/js/clinic/compose-commands-templates.js';

// Synthetic rows in the shapes of routes_commands.list_commands and routes_templates.template_summary.
const COMMAND = { id: 'c1', trigger_phrase: 'naechster Abschnitt', action_type: 'macro', description: null };
const TEMPLATE = {
    id: 't1', name: 'Arztbrief', scope: 'user', scope_id: 7, version: 2,
    mime_type: 'text/plain', content_sha256: 'a'.repeat(64), insertion_mark: '[[TEXT]]',
};
const okRes = (items) => ({ outcome: 'ok', status: 200, data: { items } });
const denied = { outcome: 'permission', status: 403, code: 'FORBIDDEN', message: 'Keine Berechtigung für diese Aktion.' };

function fakeVue() {
    const hooks = { mounted: [], unmount: [], watchers: [] };
    return {
        hooks,
        reactive: (o) => o,
        markRaw: (o) => o,
        computed: (fn) => ({ get value() { return fn(); } }),
        watch: (getter, cb) => hooks.watchers.push({ getter, cb }),
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
        shallowRef: (v) => ({ value: v }),
        ref: (v) => ({ value: v }),
    };
}

function fakeClient(handler) {
    return {
        request: vi.fn(async (path, opts) => handler(path, opts)),
        command: vi.fn(async () => ({ outcome: 'ok', data: {} })),
        switchContext: vi.fn(),
    };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-commands-templates.js', import.meta.url), 'utf8');

describe('commands and templates view', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerCommandsTemplatesView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-commands-templates-view', CommandsTemplatesView);
        expect(Object.keys(CommandsTemplatesView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('has german kind labels with tab semantics and an icon button with an accessible name', () => {
        expect(KINDS.map((k) => k.label)).toEqual(['Befehle', 'Bausteine', 'Vorlagen']);
        for (const part of ['role="tablist"', 'role="tab"', 'role="tabpanel"', ':aria-selected=', ':tabindex=']) {
            expect(COMMANDS_TEMPLATES_TEMPLATE).toContain(part);
        }
        expect(COMMANDS_TEMPLATES_TEMPLATE).toContain('aria-label="Neu laden"');
        expect(COMMANDS_TEMPLATES_TEMPLATE).toContain('aria-hidden="true"');
    });

    it('documents the editor toolbar hook as a named slot', () => {
        expect(EDITOR_HOOK).toEqual({ slot: 'editor-toolbar', consumer: 'UI-ED-18' });
        expect(COMMANDS_TEMPLATES_TEMPLATE).toContain('<slot name="editor-toolbar">');
    });

    it('renders synthetic command data: ready state, existing child, user scope', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => okRes([COMMAND]));
        const view = CommandsTemplatesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(client.request).toHaveBeenCalledWith('/resources/commands');
        expect(view.state.status).toBe('ready');
        expect(view.showChild.value).toBe(true);
        expect(view.childComponent.value.name).toBe('ClinicVoiceCommandsUi');
        expect(view.childProps.value).toEqual({ client, scope: { type: 'user', id: '7' } });
    });

    it('renders synthetic template data with the existing templates component', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => (path === '/templates' ? okRes([TEMPLATE]) : okRes([])));
        const view = CommandsTemplatesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        view.choose('templates');
        await vi.waitFor(() => expect(view.state.status).toBe('ready'));
        expect(client.request).toHaveBeenCalledWith('/templates');
        expect(view.childComponent.value.name).toBe('ClinicTemplates');
        expect(view.childProps.value).toEqual({});
    });

    it('shows the 403 state and mounts no editor', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => denied);
        const view = CommandsTemplatesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.state.status).toBe('forbidden');
        expect(view.state.message).toBe(TEXT.forbidden);
        expect(view.showChild.value).toBe(false);
    });

    it('shows the empty state per kind and keeps the create form reachable for commands', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => okRes([]));
        const view = CommandsTemplatesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.state.status).toBe('empty');
        expect(view.state.message).toBe(TEXT.empty.commands);
        expect(view.showChild.value).toBe(true);
        view.choose('templates');
        await vi.waitFor(() => expect(view.state.message).toBe(TEXT.empty.templates));
    });

    it('reports a failed load as an error with retry and loads nothing for blocks', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ({ outcome: 'network', status: 503, message: 'x' }));
        const view = CommandsTemplatesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        await Vue.hooks.mounted[0]();
        expect(view.state.status).toBe('error');
        expect(view.state.message).toBe(TEXT.failed);
        client.request.mockClear();
        view.choose('blocks');
        await vi.waitFor(() => expect(view.state.status).toBe('unavailable'));
        expect(view.state.message).toBe(TEXT.blocksOpen);
        expect(client.request).not.toHaveBeenCalled();
        expect(view.showChild.value).toBe(false);
    });

    it('requests nothing without a current user', async () => {
        const client = fakeClient(() => okRes([]));
        const ctrl = createCommandsTemplatesController({ client, currentUser: null });
        expect(ctrl.state.denied).toBe(true);
        expect(await ctrl.load()).toEqual({ outcome: 'denied' });
        expect(ctrl.select('templates')).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
    });

    it('moves between kinds with arrow keys, Home and End', () => {
        const ctrl = createCommandsTemplatesController({ client: fakeClient(() => okRes([])), currentUser: { id: 7 } });
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('blocks');
        expect(ctrl.onKeydown({ key: 'End' })).toBe('templates');
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('commands');
        expect(ctrl.onKeydown({ key: 'ArrowLeft' })).toBe('templates');
        expect(ctrl.onKeydown({ key: 'Home' })).toBe('commands');
        expect(ctrl.onKeydown({ key: 'x' })).toBeNull();
        expect(ctrl.select('nope')).toBe(false);
    });

    it('drops state on user change and ignores a stale answer after dispose', async () => {
        let release;
        const client = fakeClient(() => new Promise((r) => { release = r; }));
        const ctrl = createCommandsTemplatesController({ client, currentUser: { id: 1 } });
        const pending = ctrl.load();
        ctrl.setUser({ id: 2 });
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        release(okRes([COMMAND]));
        expect(await pending).toEqual({ outcome: 'stale' });
        expect(ctrl.state.status).toBe('idle');
        ctrl.dispose();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(ctrl.select('templates')).toBe(false);
    });

    it('reuses the existing modules, copies no logic and touches no browser storage', () => {
        const src = source();
        expect(src).toContain("from './voice-commands-ui.js'");
        expect(src).toContain("from './templates-ui.js'");
        expect(src).toContain("from './tokens.js'");
        expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
        expect(src).not.toMatch(/function (validateMarker|previewMacro|validateDefinition|expandPlaceholders)/);
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
