import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createPanels, createResourcesController, registerResourcesView, ResourcesView, RESOURCES_TEMPLATE, TABS,
} from '../../../static/js/clinic/compose-resources.js';

const ok = (data) => ({ outcome: 'ok', data });

// Minimal Vue stand-in: enough surface for setup() and lifecycle capture.
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

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-resources.js', import.meta.url), 'utf8');

describe('resources view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerResourcesView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-resources-view', ResourcesView);
        expect(Object.keys(ResourcesView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('has german tabs with tab semantics', () => {
        expect(TABS.map((t) => t.label)).toEqual(['Vokabular', 'Vokabelprüfung', 'Sprachbefehle', 'ASR-Profile']);
        expect(RESOURCES_TEMPLATE).toContain('role="tablist"');
        expect(RESOURCES_TEMPLATE).toContain('role="tab"');
        expect(RESOURCES_TEMPLATE).toContain('role="tabpanel"');
        expect(RESOURCES_TEMPLATE).toContain(':aria-selected=');
        expect(RESOURCES_TEMPLATE).toContain(':tabindex=');
    });

    it('moves between tabs with arrow keys, Home and End', () => {
        const ctrl = createResourcesController({ client: fakeClient(), currentUser: { id: 7 } });
        expect(ctrl.state.active).toBe('vocabulary');
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('review');
        expect(ctrl.onKeydown({ key: 'End' })).toBe('asr');
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('vocabulary');
        expect(ctrl.onKeydown({ key: 'ArrowLeft' })).toBe('asr');
        expect(ctrl.onKeydown({ key: 'Home' })).toBe('vocabulary');
        expect(ctrl.onKeydown({ key: 'x' })).toBeNull();
        expect(ctrl.select('nope')).toBe(false);
    });

    it('mounts only the active child, built on first use', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const view = ResourcesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        const first = view.activeComponent.value;
        expect(first.template).toContain('voc-title');
        expect(view.activeProps.value).toEqual({});
        view.select('commands');
        expect(view.activeComponent.value.name).toBe('ClinicVoiceCommandsUi');
        expect(view.activeProps.value).toEqual({ client, scope: { type: 'user', id: '7' } });
        view.select('review');
        expect(view.activeComponent.value.name).toBe('ClinicVocabularyReviewPanel');
        view.select('asr');
        expect(view.activeComponent.value.name).toBe('ClinicAsrProfilesPanel');
        expect(view.activeProps.value).toEqual({ client });
        view.select('vocabulary');
        expect(view.activeComponent.value).toBe(first);
    });

    it('scopes state to the current user and drops it on user change', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const props = { client, currentUser: { id: 1 }, vue: Vue };
        const view = ResourcesView.setup(props);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '1', dictationId: null });
        view.select('asr');
        const epoch = view.state.epoch;
        props.currentUser = { id: 2 };
        const watcher = Vue.hooks.watchers[0];
        expect(watcher.getter()).toBe('2');
        watcher.cb();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        expect(view.state.active).toBe('vocabulary');
        expect(view.state.epoch).toBe(epoch + 1);
        watcher.cb();
        expect(view.state.epoch).toBe(epoch + 1);
    });

    it('refuses an unauthenticated user and loads nothing', () => {
        const client = fakeClient();
        const ctrl = createResourcesController({ client, currentUser: null });
        expect(ctrl.state.denied).toBe(true);
        expect(ctrl.select('review')).toBe(false);
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBeNull();
        expect(ctrl.state.active).toBe('vocabulary');
        expect(client.request).not.toHaveBeenCalled();
        expect(RESOURCES_TEMPLATE).toContain('state.denied');
    });

    it('releases subscriptions and in-flight calls on unmount', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const view = ResourcesView.setup({ client, currentUser: { id: 3 }, vue: Vue });
        expect(Vue.hooks.unmount).toHaveLength(1);
        Vue.hooks.unmount[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(view.select('review')).toBe(false);
        const before = client.switchContext.mock.calls.length;
        Vue.hooks.unmount[0]();
        expect(client.switchContext.mock.calls.length).toBe(before);
    });
});

describe('review and asr panels', () => {
    const items = { revision: 1, items: [{ id: 'p1', written_form: 'Metformin', status: 'PENDING', revision: 1 }] };

    it('review panel loads on mount, shows stale state and unsubscribes on unmount', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok(items));
        const { review } = createPanels(Vue);
        const panel = review.setup({ client, scope: { type: 'user', id: '7' } });
        await Vue.hooks.mounted[0]();
        expect(client.request.mock.calls[0][0]).toBe('/resources/vocabulary/reviews?scope_type=user&scope_id=7&status=PENDING');
        expect(panel.view.value.items.map((i) => i.writtenForm)).toEqual(['Metformin']);
        client.command.mockResolvedValueOnce({ outcome: 'conflict', message: 'm' });
        await panel.controller.acceptUser('p1');
        expect(panel.view.value.stale).toBe(true);
        expect(panel.view.value.items[0].actions.acceptUser.enabled).toBe(false);
        Vue.hooks.unmount[0]();
        const frozen = panel.view.value;
        await panel.controller.load();
        expect(panel.view.value).toBe(frozen);
    });

    it('review panel denies a pool decision without permission and keeps the item', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok(items));
        const { review } = createPanels(Vue);
        const panel = review.setup({ client, scope: { type: 'user', id: '7' } });
        await Vue.hooks.mounted[0]();
        client.command.mockResolvedValueOnce({ outcome: 'permission', message: 'm' });
        expect(await panel.controller.acceptPool('p1')).toBe(false);
        expect(panel.view.value.items[0].actions.acceptPool.enabled).toBe(false);
        expect(panel.view.value.items).toHaveLength(1);
    });

    it('asr panel reports the cpu fallback and survives a network failure with retry', async () => {
        const Vue = fakeVue();
        let calls = 0;
        const client = fakeClient(() => {
            calls += 1;
            if (calls === 1) return { outcome: 'network', message: 'offline' };
            return ok({
                version: 3, source: 'headset', profiles: [{ id: '1', name: 'Turbo', mode: 'both' }],
                engine: { configured: 'gpu', actual: 'cpu', reason: 'gpu busy' },
            });
        });
        const { asr } = createPanels(Vue);
        const panel = asr.setup({ client });
        await Vue.hooks.mounted[0]();
        expect(panel.view.value.error.retryable).toBe(true);
        await panel.controller.load();
        expect(panel.view.value.error).toBeNull();
        expect(panel.view.value.engine.fallback).toBe(true);
        expect(panel.view.value.prompts.enabled).toBe(false);
        expect(panel.errorStyle.color).toBe('var(--text-danger-strong)');
        expect(panel.onKey({ key: 'ArrowRight' })).toBe(true);
        expect(panel.controller.state.draft.source).toBe('recorder');
        expect(panel.selected(panel.view.value.live)).toBe('');
    });

    it('asr panel save sends the loaded version and a stale version is reported', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path, opts) => {
            if (opts && opts.method === 'PUT') return { outcome: 'conflict', code: 'stale_version', message: 'm' };
            return ok({ version: 5, source: 'headset', profiles: [] });
        });
        const { asr } = createPanels(Vue);
        const panel = asr.setup({ client });
        await Vue.hooks.mounted[0]();
        panel.controller.setSource('recorder');
        await panel.controller.save();
        const put = client.request.mock.calls.find((c) => c[1] && c[1].method === 'PUT');
        expect(put[1].body.expected_version).toBe(5);
        expect(panel.view.value.error.message).toBe('Der Stand ist veraltet. Bitte neu laden.');
        expect(panel.view.value.dirty).toBe(true);
    });
});

describe('module hygiene', () => {
    it('uses no hex colours and no browser storage', () => {
        expect(source()).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source()).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
