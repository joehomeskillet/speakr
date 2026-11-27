import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    createVocabularyViewController, registerVocabularyView, TABS, TEXT, VocabularyView, VOCABULARY_VIEW_TEMPLATE,
} from '../../../static/js/clinic/compose-vocabulary.js';
import { createVocabularyStore } from '../../../static/js/clinic/vocabulary-ui.js';
import { ResourcesView } from '../../../static/js/clinic/compose-resources.js';

const ok = (data) => ({ outcome: 'ok', data });
const denied = { outcome: 'permission', status: 403, code: 'FORBIDDEN', message: 'Keine Berechtigung für diese Aktion.' };

// Real response shape of GET /api/clinic/v1/vocabulary/search (routes_vocabulary._entry_json).
const entries = {
    entries: [
        { id: 'e1', written: 'Metformin', spoken: 'Metformin', status: 'active', version: 'v1' },
        { id: 'e2', written: 'Teststoff', spoken: null, status: 'suppressed', version: 'v2' },
    ],
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
        request: vi.fn(async (path, opts) => (handler ? handler(path, opts) : ok(entries))),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-vocabulary.js', import.meta.url), 'utf8');

describe('vocabulary view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        expect(registerVocabularyView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-vocabulary-view', VocabularyView);
        expect(Object.keys(VocabularyView.props)).toEqual(['client', 'currentUser', 'vue']);
    });

    it('has german tabs with tab semantics and a text label on every button', () => {
        expect(TABS.map((t) => t.label)).toEqual(['Wörter', 'Vorschläge', 'Aussprache', 'Enginewirkung']);
        expect(VOCABULARY_VIEW_TEMPLATE).toContain('role="tablist"');
        expect(VOCABULARY_VIEW_TEMPLATE).toContain('role="tab"');
        expect(VOCABULARY_VIEW_TEMPLATE).toContain('role="tabpanel"');
        expect(VOCABULARY_VIEW_TEMPLATE).toContain(':aria-selected=');
        expect(VOCABULARY_VIEW_TEMPLATE).toContain(':tabindex=');
        const buttons = VOCABULARY_VIEW_TEMPLATE.match(/<button[^>]*>[^<]*<\/button>/g) || [];
        expect(buttons.length).toBeGreaterThan(0);
        for (const b of buttons) expect(b.replace(/<[^>]+>/g, '').trim()).not.toBe('');
    });

    it('moves between tabs with arrow keys, Home and End once ready', async () => {
        const ctrl = createVocabularyViewController({ client: fakeClient(), currentUser: { id: 7 } });
        expect(ctrl.select('proposals')).toBe(false);
        await ctrl.load();
        expect(ctrl.state.active).toBe('words');
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('proposals');
        expect(ctrl.onKeydown({ key: 'End' })).toBe('engine');
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBe('words');
        expect(ctrl.onKeydown({ key: 'ArrowLeft' })).toBe('engine');
        expect(ctrl.onKeydown({ key: 'Home' })).toBe('words');
        expect(ctrl.onKeydown({ key: 'x' })).toBeNull();
        expect(ctrl.select('nope')).toBe(false);
    });
});

describe('states', () => {
    it('renders with synthetic data: probes the own list once and is ready', async () => {
        const client = fakeClient();
        const ctrl = createVocabularyViewController({ client, currentUser: { id: 7 } });
        expect(ctrl.state.status).toBe('loading');
        await ctrl.load();
        expect(client.request).toHaveBeenCalledTimes(1);
        expect(client.request.mock.calls[0][0]).toBe('/vocabulary/search?scope=user&q=&mode=contains&status=all');
        expect(ctrl.state.status).toBe('ready');
        expect(ctrl.state.empty).toBe(false);
        expect(ctrl.scope()).toEqual({ type: 'user', id: '7' });
    });

    it('shows the empty state for an empty list but still mounts the children', async () => {
        const ctrl = createVocabularyViewController({ client: fakeClient(() => ok({ entries: [] })), currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.status).toBe('ready');
        expect(ctrl.state.empty).toBe(true);
        expect(ctrl.select('proposals')).toBe(true);
        expect(VOCABULARY_VIEW_TEMPLATE).toContain('data-testid="empty"');
        expect(TEXT.empty).toContain('Noch keine Wörter');
    });

    it('shows the 403 state and mounts no child', async () => {
        const ctrl = createVocabularyViewController({ client: fakeClient(() => denied), currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.status).toBe('forbidden');
        expect(ctrl.select('proposals')).toBe(false);
        expect(ctrl.onKeydown({ key: 'ArrowRight' })).toBeNull();
        expect(VOCABULARY_VIEW_TEMPLATE).toContain('state.status === \'forbidden\'');
        expect(TEXT.forbidden).toBe('Keine Berechtigung für den Wortschatz.');
    });

    it('shows a retryable error for a network failure', async () => {
        let answer = { outcome: 'network', message: 'Die Verbindung ist fehlgeschlagen.' };
        const ctrl = createVocabularyViewController({ client: fakeClient(() => answer), currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.status).toBe('error');
        expect(ctrl.state.message).toBe('Die Verbindung ist fehlgeschlagen.');
        answer = ok(entries);
        await ctrl.load();
        expect(ctrl.state.status).toBe('ready');
    });

    it('refuses an unauthenticated user and requests nothing', async () => {
        const client = fakeClient();
        const ctrl = createVocabularyViewController({ client, currentUser: null });
        expect(ctrl.state.status).toBe('denied');
        expect(await ctrl.load()).toBeNull();
        expect(ctrl.select('proposals')).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
        expect(TEXT.denied).toBe('Kein Zugriff. Bitte anmelden.');
    });

    it('drops a late probe answer after dispose or a newer load', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient(async () => { await gate; return ok(entries); });
        const ctrl = createVocabularyViewController({ client, currentUser: { id: 7 } });
        const pending = ctrl.load();
        ctrl.dispose();
        release();
        expect(await pending).toBeNull();
        expect(ctrl.state.status).toBe('loading');
    });

    it('the reused words child reports a refused foreign pool as denied', async () => {
        const client = fakeClient((path) => (path.includes('scope=pool') ? denied : ok(entries)));
        const store = createVocabularyStore(client);
        await store.search();
        expect(store.state.status).toBe('ready');
        expect(store.state.entries.map((e) => e.written)).toEqual(['Metformin', 'Teststoff']);
        await store.setScope('pool', 'pool-x');
        expect(store.state.status).toBe('denied');
        expect(store.state.entries).toEqual([]);
    });
});

describe('mounted view', () => {
    it.each([[VocabularyView, 'proposals'], [ResourcesView, 'review']])(
        'switches the shared review tab between personal and authorized pool proposals',
        async (component, tab) => {
            const Vue = fakeVue();
            const client = fakeClient((path) => {
                if (path.endsWith('?catalog=author_pools')) return ok({ author_pools: [{ id: 'pool-1', name: 'Kardiologie' }] });
                if (path.includes('scope_type=author_pool')) return ok({
                    items: [
                        { id: 'word-1', written_form: 'Alpha', operation: 'ADD', status: 'PENDING' },
                        { id: 'word-2', written_form: 'Beta', operation: 'DELETE', status: 'PENDING' },
                        { id: 'word-3', written_form: 'Gamma', operation: 'ADD', status: 'PENDING' },
                    ],
                });
                return ok(entries);
            });
            const view = component.setup({ client, currentUser: { id: 7 }, vue: Vue });
            await Vue.hooks.mounted[0]?.();
            expect(view.select(tab)).toBe(true);
            const beforePanel = Vue.hooks.mounted.length;
            const panel = view.activeComponent.value.setup(view.activeProps.value);
            await Promise.all(Vue.hooks.mounted.slice(beforePanel).map((mount) => mount()));
            expect(panel.reviewPools.value).toEqual([{ id: 'pool-1', name: 'Kardiologie' }]);
            expect(panel.selectedPool.value).toBe('');
            await panel.selectReviewPool('foreign-pool');
            expect(client.request.mock.calls.some(([path]) => path.includes('foreign-pool'))).toBe(false);
            await panel.selectReviewPool('pool-1');
            expect(client.request).toHaveBeenLastCalledWith(
                '/resources/vocabulary/reviews?scope_type=author_pool&scope_id=pool-1&status=PENDING',
            );
            expect(panel.view.value.items.map((item) => item.operation)).toEqual(['ADD', 'DELETE', 'ADD']);
            await panel.controller.acceptPool('word-1');
            await panel.controller.acceptUser('word-2');
            await panel.controller.reject('word-3');
            expect(client.command.mock.calls.map(([command]) => command.payload)).toEqual([
                { proposal_id: 'word-1', decision: 'pool' },
                { proposal_id: 'word-2', decision: 'user' },
                { proposal_id: 'word-3', decision: 'reject' },
            ]);
            await panel.selectReviewPool('');
            expect(client.request).toHaveBeenLastCalledWith(
                '/resources/vocabulary/reviews?scope_type=user&scope_id=7&status=PENDING',
            );
            expect(panel.view.value.items).toEqual([]);
            expect(panel.view.value.history).toEqual([]);
        },
    );

    it('mounts only the active existing child, built on first use', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const view = VocabularyView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        expect(Vue.hooks.mounted).toHaveLength(1);
        view.state.status = 'ready';
        const first = view.activeComponent.value;
        expect(first.template).toContain('voc-title');
        expect(view.activeProps.value).toEqual({});
        view.select('pronunciation');
        expect(view.activeComponent.value.name).toBe('ClinicPronunciationHintsUi');
        expect(view.activeComponent.value.template).toContain('ph-phonetic');
        expect(view.activeComponent.value.template).not.toContain('vc-steps');
        expect(view.activeProps.value).toEqual({ client, scope: { type: 'user', id: '7' } });
        view.select('proposals');
        expect(view.activeComponent.value.name).toBe('ClinicVocabularyReviewPanel');
        view.select('engine');
        expect(view.activeComponent.value.name).toBe('ClinicAsrProfilesPanel');
        expect(view.activeProps.value).toEqual({ client });
        view.select('words');
        expect(view.activeComponent.value).toBe(first);
    });

    it('loads on mount, scopes state to the current user and reloads on user change', async () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const props = { client, currentUser: { id: 1 }, vue: Vue };
        const view = VocabularyView.setup(props);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '1', dictationId: null });
        await Vue.hooks.mounted[0]();
        expect(view.state.status).toBe('ready');
        view.select('engine');
        const epoch = view.state.epoch;
        props.currentUser = { id: 2 };
        const watcher = Vue.hooks.watchers[0];
        expect(watcher.getter()).toBe('2');
        watcher.cb();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        expect(view.state.active).toBe('words');
        expect(view.state.epoch).toBe(epoch + 1);
        expect(client.request).toHaveBeenCalledTimes(2);
    });

    it('releases in-flight calls on unmount', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const view = VocabularyView.setup({ client, currentUser: { id: 3 }, vue: Vue });
        expect(Vue.hooks.unmount).toHaveLength(1);
        Vue.hooks.unmount[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(view.select('proposals')).toBe(false);
        const before = client.switchContext.mock.calls.length;
        Vue.hooks.unmount[0]();
        expect(client.switchContext.mock.calls.length).toBe(before);
    });

    it('needs Vue to mount', () => {
        const saved = globalThis.Vue;
        delete globalThis.Vue;
        expect(() => VocabularyView.setup({ client: fakeClient(), currentUser: { id: 1 } })).toThrow(/Vue is required/);
        if (saved) globalThis.Vue = saved;
    });
});

describe('module hygiene', () => {
    it('imports existing building blocks and writes no browser storage', () => {
        const text = source();
        for (const m of ['vocabulary-ui.js', 'voice-commands-ui.js', 'compose-resources.js', 'tokens.js']) {
            expect(text).toContain("from './" + m + "'");
        }
        expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
        expect(text).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(text).not.toMatch(/patient/i);
    });
});
