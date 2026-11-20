import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    ASR_PROFILES_TEMPLATE, AsrProfilesView, createAsrProfilesViewController, createSections,
    createTrainingController, registerAsrProfilesView, TEXT, TRAINING_BASE,
} from '../../../static/js/clinic/compose-asr-profiles.js';
import { createPanels } from '../../../static/js/clinic/compose-resources.js';

const ok = (data) => ({ outcome: 'ok', data });
const forbidden = { outcome: 'permission', status: 403, code: 'FORBIDDEN', message: 'Keine Berechtigung für diese Aktion.' };

function fakeVue() {
    const hooks = { mounted: [], unmount: [], watchers: [] };
    return {
        hooks,
        reactive: (o) => o,
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
        switchContext: vi.fn(),
    };
}

// Shapes taken from routes_training.py (v8-31) and routes_asr_profiles.py.
const schedule = {
    runs: [
        { job_id: 11, user_id: 1, manifest_hash: 'h1', profile_version: 3, kind: 'language_model', scope: 'author', pool_id: null, items: 12, state: 'queued' },
        { job_id: 10, user_id: 1, manifest_hash: 'h0', profile_version: 2, kind: 'acoustic', scope: 'author', pool_id: null, items: 40, state: 'completed' },
    ],
};
const manifest = {
    format: 'clinic-training-manifest', kind: 'language_model', scope: 'author', learning_claimed: false,
    capability: 'capability_unavailable', capability_evidence_ref: null,
    items: [{ dictation_id: 'd1' }, { dictation_id: 'd2' }],
    exclusions: [{ dictation_id: 'd3', transcript_revision_id: 'r3', reason: 'not_spoken_addition' }],
    manifest_hash: 'abc',
};
const profile = { version: 3, profile: { id: '5', name: 'turbo', version: 3 }, profiles: [{ id: '5', name: 'turbo', mode: 'both' }] };

function adminHandler(overrides = {}) {
    return (path, opts) => {
        const key = (opts && opts.method ? opts.method : 'GET') + ' ' + path.replace(TRAINING_BASE, '');
        if (overrides[key]) return overrides[key](path, opts);
        if (key === 'GET /schedule') return ok(schedule);
        if (key.startsWith('GET /manifest')) return ok(manifest);
        if (key === 'POST /runs') return ok({ manifest_hash: 'abc', profile_version: 3, state: 'scheduled', job_id: 12 });
        if (key.endsWith('/cancel')) return ok({ job_id: 11, outcome: 'cancelled' });
        return ok({});
    };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-asr-profiles.js', import.meta.url), 'utf8');

describe('asr profiles view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerAsrProfilesView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-asr-profiles-view', AsrProfilesView);
        expect(Object.keys(AsrProfilesView.props)).toEqual(['client', 'adminClient', 'currentUser', 'vue']);
    });

    it('reuses the existing building blocks and does not copy them', () => {
        const text = source();
        for (const name of ['./compose-resources.js', './voice-consent-ui.js', './tokens.js']) expect(text).toContain("from '" + name + "'");
        expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(text).not.toMatch(/https?:\/\//);
        const Vue = fakeVue();
        const view = AsrProfilesView.setup({ client: fakeClient(), currentUser: { id: 7 }, vue: Vue });
        expect(view.parts.Profile.name).toBe('ClinicAsrProfilesPanel');
        expect(view.parts.Profile.template).toBe(createPanels(Vue).asr.template);
        expect(view.parts.Consent.name).toBe('ClinicVoiceConsentPanel');
    });

    it('renders the four sections for a user with an admin client', () => {
        const Vue = fakeVue();
        const view = AsrProfilesView.setup({ client: fakeClient(), adminClient: fakeClient(), currentUser: { id: 7 }, vue: Vue });
        expect(view.state.denied).toBe(false);
        expect(ASR_PROFILES_TEMPLATE).toContain('parts.Profile');
        expect(ASR_PROFILES_TEMPLATE).toContain('parts.Capabilities');
        expect(ASR_PROFILES_TEMPLATE).toContain('parts.Consent');
        expect(ASR_PROFILES_TEMPLATE).toContain('v-if="adminClient"');
        expect(TEXT.title).toBe('Sprachprofile');
    });

    it('refuses an unauthenticated user and scopes both clients on user change and unmount', () => {
        const Vue = fakeVue();
        const client = fakeClient();
        const adminClient = fakeClient();
        const props = { client, adminClient, currentUser: null, vue: Vue };
        const view = AsrProfilesView.setup(props);
        expect(view.state.denied).toBe(true);
        expect(client.request).not.toHaveBeenCalled();
        props.currentUser = { id: 4 };
        Vue.hooks.watchers[0].cb();
        expect(view.state.denied).toBe(false);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '4', dictationId: null });
        expect(adminClient.switchContext).toHaveBeenLastCalledWith({ accountId: '4', dictationId: null });
        const epoch = view.state.epoch;
        Vue.hooks.watchers[0].cb();
        expect(view.state.epoch).toBe(epoch);
        Vue.hooks.unmount[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(adminClient.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        const ctrl = createAsrProfilesViewController({ client, currentUser: { id: 1 } });
        ctrl.dispose();
        ctrl.setUser({ id: 2 });
        expect(ctrl.state.userId).toBe('1');
    });
});

describe('capability and consent sections', () => {
    it('reports pronunciation training as unavailable, never as success without proof', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok({ training_proven: false, capability: 'CAPABILITY_UNAVAILABLE', adaptation_state: 'capability_unavailable' }));
        const panel = createSections(Vue).capabilities.setup({ client });
        await Vue.hooks.mounted[0]();
        expect(client.request).toHaveBeenCalledWith('/pronunciations/capabilities');
        expect(panel.row.value.label).toBe(TEXT.unavailable);
    });

    it('shows proven and undetermined states', async () => {
        const Vue = fakeVue();
        const proven = createSections(Vue).capabilities.setup({ client: fakeClient(() => ok({ training_proven: true })) });
        await Vue.hooks.mounted[0]();
        expect(proven.row.value.label).toBe(TEXT.proven);
        const Vue2 = fakeVue();
        const unknown = createSections(Vue2).capabilities.setup({ client: fakeClient(() => ({ outcome: 'network', message: 'x' })) });
        await Vue2.hooks.mounted[0]();
        expect(unknown.row.value.label).toBe(TEXT.capabilityUnknown);
    });

    it('voice profile stays opt-in: nothing is granted on load', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok({ state: 'none', latest_consent_id: 0, embedding_count: 0, history: [] }));
        const panel = createSections(Vue).consent.setup({ client });
        await Vue.hooks.mounted[0]();
        expect(panel.view.value.status.value).toBe('none');
        expect(panel.view.value.canGrant).toBe(true);
        expect(client.request.mock.calls.every(([, o]) => !o || !o.method)).toBe(true);
    });

    it('asr profile panel renders the profile shape of GET /asr-profiles', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok({
            ...profile, source: 'headset', live_profile_id: '5', batch_profile_id: '5',
            output_format: { container: 'webm', codec: 'opus', sample_rate: 48000 },
            engine: { configured: 'cpu', actual: 'cpu', reason: 'nachgewiesen' },
            capabilities: { configured: ['batch', 'live'], proven: ['batch'] },
        }));
        const panel = createPanels(Vue).asr.setup({ client });
        await Vue.hooks.mounted[0]();
        expect(panel.view.value.live.map((o) => o.label)).toEqual(['turbo']);
        expect(panel.view.value.capabilities.map((c) => c.label)).toEqual(['Nachgewiesen', 'Nicht nachgewiesen']);
    });
});

describe('optimisation section', () => {
    it('renders runs from the schedule shape in german', async () => {
        const client = fakeClient(adminHandler());
        const c = createTrainingController({ client });
        expect(await c.load()).toBe(true);
        expect(client.request).toHaveBeenCalledWith('/admin/clinic/training/schedule');
        const v = c.view();
        expect(v.runs.map((r) => r.stateLabel)).toEqual(['In Warteschlange', 'Abgeschlossen']);
        expect(v.runs[0].canCancel).toBe(true);
        expect(v.runs[1].canCancel).toBe(false);
        expect(v.runs[0].cancelLabel).toBe('Lauf abbrechen 11');
        expect(v.runs[0].kind).toBe('Sprachmodell (nur Text)');
        expect(v.empty).toBe(false);
    });

    it('empty state: no runs', async () => {
        const c = createTrainingController({ client: fakeClient(adminHandler({ 'GET /schedule': () => ok({ runs: [] }) })) });
        await c.load();
        const v = c.view();
        expect(v.empty).toBe(true);
        expect(v.runs).toEqual([]);
        expect(v.denied).toBe(false);
    });

    it('403 state: a non-admin gets its own message and nothing can start', async () => {
        const client = fakeClient(() => forbidden);
        const c = createTrainingController({ client, profileClient: fakeClient() });
        expect(await c.load()).toBe(false);
        const v = c.view();
        expect(v.denied).toBe(true);
        expect(v.error).toBeNull();
        expect(v.deniedMessage).toContain('Keine Rechte');
        expect(v.canStart).toBe(false);
        client.request.mockClear();
        expect(await c.start()).toBe(false);
        expect(await c.prepare()).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
    });

    it('network failure is retryable and not a denial', async () => {
        const c = createTrainingController({ client: fakeClient(() => ({ outcome: 'network', status: null, code: 'NETWORK', message: 'Die Verbindung ist fehlgeschlagen.' })) });
        await c.load();
        const v = c.view();
        expect(v.denied).toBe(false);
        expect(v.error.retryable).toBe(true);
    });

    it('prepare shows counts and blocks on missing material without starting', async () => {
        const client = fakeClient(adminHandler());
        const c = createTrainingController({ client });
        await c.prepare();
        expect(client.request).toHaveBeenCalledWith('/admin/clinic/training/manifest?kind=language_model&scope=author');
        expect(c.view().manifest).toEqual({ items: 2, excluded: 1, capability: 'capability_unavailable' });
        const empty = createTrainingController({ client: fakeClient(adminHandler({ 'GET /manifest?kind=language_model&scope=author': () => ok({ items: [], exclusions: [] }) })) });
        await empty.prepare();
        expect(empty.view().notice).toBe(TEXT.noMaterial);
        expect(client.request.mock.calls.some(([, o]) => o && o.method === 'POST')).toBe(false);
    });

    it('start sends the profile version of the user profile and reports scheduled and repeated runs', async () => {
        const client = fakeClient(adminHandler());
        const profileClient = fakeClient(() => ok(profile));
        const c = createTrainingController({ client, profileClient });
        c.setKind('acoustic');
        expect(await c.start()).toBe(true);
        expect(profileClient.request).toHaveBeenCalledWith('/asr-profiles');
        const post = client.request.mock.calls.find(([, o]) => o && o.method === 'POST');
        expect(post[0]).toBe('/admin/clinic/training/runs');
        expect(post[1].body).toEqual({ kind: 'acoustic', scope: 'author', expected_profile_version: 3 });
        expect(c.view().notice).toBe('Lauf eingeplant.');
        client.request.mockImplementation(async (p, o) => (o && o.method === 'POST'
            ? ok({ state: 'already_scheduled', job_id: 12 }) : adminHandler()(p, o)));
        expect(await c.start()).toBe(true);
        expect(c.view().notice).toBe('Für dieses Material läuft bereits ein Lauf.');
    });

    it('start reports insufficient data and the time window as controlled blocks', async () => {
        const profileClient = fakeClient(() => ok(profile));
        const few = createTrainingController({ client: fakeClient(adminHandler({ 'POST /runs': () => ok({ state: 'insufficient_data', utterances: 1, audio_seconds: 0 }) })), profileClient });
        expect(await few.start()).toBe(false);
        expect(few.view().notice).toBe('Mindestdaten fehlen: 1 Beispiele, 0 s Audio.');
        const late = createTrainingController({ client: fakeClient(adminHandler({ 'POST /runs': () => ok({ state: 'deferred', reason: 'outside_window', next_window_start: '2026-11-21T22:00:00+01:00' }) })), profileClient });
        expect(await late.start()).toBe(false);
        expect(late.view().notice).toContain('Ausserhalb des Zeitfensters');
    });

    it('maps server codes to german messages and never starts twice at once', async () => {
        const stale = createTrainingController({
            client: fakeClient(adminHandler({ 'POST /runs': () => ({ outcome: 'conflict', status: 409, code: 'stale_version', message: 'Der Stand ist veraltet.' }) })),
            profileClient: fakeClient(() => ok(profile)),
        });
        await stale.start();
        expect(stale.view().error.message).toBe('Der Profilstand ist veraltet. Bitte neu laden.');
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient(adminHandler({ 'POST /runs': async () => { await gate; return ok({ state: 'scheduled', job_id: 1 }); } }));
        const c = createTrainingController({ client, profileClient: fakeClient(() => ok(profile)) });
        const first = c.start();
        expect(await c.start()).toBe(false);
        release();
        await first;
        expect(client.request.mock.calls.filter(([, o]) => o && o.method === 'POST')).toHaveLength(1);
    });

    it('cancels only waiting or running runs and reloads', async () => {
        const client = fakeClient(adminHandler());
        const c = createTrainingController({ client });
        await c.load();
        expect(await c.cancel(10)).toBe(false);
        expect(await c.cancel(99)).toBe(false);
        expect(await c.cancel(11)).toBe(true);
        expect(client.request).toHaveBeenCalledWith('/admin/clinic/training/runs/11/cancel', { method: 'POST', body: {} });
        expect(client.request.mock.calls.filter(([p]) => p.endsWith('/schedule'))).toHaveLength(2);
    });

    it('ignores results after dispose', async () => {
        const Vue = fakeVue();
        const client = fakeClient(adminHandler());
        const panel = createSections(Vue).training.setup({ client, profileClient: null });
        const pending = panel.controller.load();
        const before = panel.view.value;
        Vue.hooks.unmount[0]();
        await pending;
        expect(panel.view.value).toBe(before);
    });

    it('template has accessible names for the cancel buttons and text status', () => {
        const Vue = fakeVue();
        const tpl = createSections(Vue).training.template;
        expect(tpl).toContain(':aria-label="r.cancelLabel"');
        expect(tpl).toContain('role="radiogroup"');
        expect(tpl).toContain('role="alert"');
    });
});
