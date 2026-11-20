import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    createConsentComponent, createPedalSettingsComponent, PEDAL_LABELS, SettingsView, SETTINGS_TEMPLATE,
} from '../../../static/js/clinic/compose-settings.js';
import { createClinicApiClient } from '../../../static/js/clinic/api-client.js';
import { STORAGE_KEY } from '../../../static/js/clinic/pedal-ui.js';

// Node environment (no DOM): events are plain Event objects with a key code, as in pedal-ui.test.js.
const key = (code) => Object.assign(new Event('keydown', { cancelable: true }), { code, repeat: false });

function fakeVue() {
    const hooks = { mounted: [], unmount: [] };
    return {
        hooks,
        reactive: (o) => o,
        ref: (v) => ({ value: v }),
        shallowRef: (v) => ({ value: v }),
        markRaw: (o) => o,
        computed: (fn) => ({ get value() { return fn(); } }),
        watch: () => {},
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
    };
}

function memoryStorage(initial = {}) {
    const data = { ...initial };
    return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

describe('settings page: pedal section', () => {
    it('is part of the page template and of the view setup', () => {
        expect(SETTINGS_TEMPLATE).toContain(':is="pedal"');
        expect(SETTINGS_TEMPLATE).toContain(':is="consent"');
        const view = SettingsView.setup({
            client: { request: vi.fn(), command: vi.fn(), switchContext: vi.fn() }, currentUser: { id: 1 }, vue: fakeVue(),
        });
        expect(view.pedal.template).toContain('data-testid="clinic-settings-pedal"');
        expect(view.consent.template).toContain('data-testid="clinic-settings-consent"');
        expect(PEDAL_LABELS.title).toBe('Fußpedal (Tastaturmodus)');
    });

    it('stores a captured key as a mapping through the pedal-ui store', () => {
        const target = new EventTarget();
        const storage = memoryStorage();
        const pedal = createPedalSettingsComponent(fakeVue(), { target, storage }).setup();
        expect(pedal.rows()).toEqual([]);
        pedal.capture.start();
        expect(pedal.capturing.value).toBe(true);
        const ev = key('F9');
        target.dispatchEvent(ev);
        expect(ev.defaultPrevented).toBe(true);
        expect(pedal.capturing.value).toBe(false);
        expect(pedal.rows()).toEqual([{ code: 'F9', action: 'playPause', label: 'Wiedergabe / Pause' }]);
        expect(JSON.parse(storage.data[STORAGE_KEY]).mapping).toEqual({ F9: 'playPause' });
        pedal.setAction('F9', 'hold');
        pedal.setSeconds('rewindSeconds', '5');
        pedal.setSeconds('skipSeconds', '7');
        pedal.setSeconds('autoRewindSeconds', '2');
        expect(JSON.parse(storage.data[STORAGE_KEY])).toEqual({
            mapping: { F9: 'hold' }, rewindSeconds: 5, skipSeconds: 7, autoRewindSeconds: 2,
        });
        pedal.removeKey('F9');
        expect(pedal.rows()).toEqual([]);
        expect(pedal.saveError.value).toBe(false);
    });

    it('rejects printable keys and keeps the stored mapping', () => {
        const target = new EventTarget();
        const storage = memoryStorage();
        const pedal = createPedalSettingsComponent(fakeVue(), { target, storage }).setup();
        pedal.capture.start();
        for (const code of ['KeyA', 'Space', 'Enter', 'Digit1', 'F5']) {
            const ev = key(code);
            target.dispatchEvent(ev);
            expect(ev.defaultPrevented).toBe(true);
            expect(pedal.message.value).toBe('Diese Taste kann nicht zugeordnet werden, weil sie Text eingeben würde.');
        }
        expect(pedal.capturing.value).toBe(true);
        expect(pedal.rows()).toEqual([]);
        expect(storage.data[STORAGE_KEY]).toBeUndefined();
    });

    it('loads the saved settings, refuses invalid seconds and stops capture on unmount', () => {
        const target = new EventTarget();
        const storage = memoryStorage({ [STORAGE_KEY]: JSON.stringify({ mapping: { F2: 'skip' }, skipSeconds: 9 }) });
        const Vue = fakeVue();
        const pedal = createPedalSettingsComponent(Vue, { target, storage }).setup();
        expect(pedal.rows()).toEqual([{ code: 'F2', action: 'skip', label: 'Vorspringen' }]);
        expect(pedal.settings.value.skipSeconds).toBe(9);
        const input = { value: '' };
        pedal.setSeconds('skipSeconds', '999', input);
        expect(pedal.settings.value.skipSeconds).toBe(3);
        expect(input.value).toBe(3);
        pedal.capture.start();
        Vue.hooks.unmount[0]();
        expect(pedal.capturing.value).toBe(false);
    });

    it('reports an unavailable store instead of claiming success', () => {
        const storage = { getItem: () => null, setItem: () => { throw new Error('full'); } };
        const pedal = createPedalSettingsComponent(fakeVue(), { target: new EventTarget(), storage }).setup();
        pedal.setSeconds('rewindSeconds', '4');
        expect(pedal.saveError.value).toBe(true);
    });
});

describe('settings page: voice consent section', () => {
    const NONE = { state: 'none', latest_consent_id: 0, embedding_count: 0, granted_at: null, revoked_at: null, history: [] };
    const GRANTED = {
        state: 'granted', latest_consent_id: 1, embedding_count: 2, granted_at: '2026-11-19T08:00:00', revoked_at: null,
        history: [{ id: 1, status: 'granted', created_at: '2026-11-19T08:00:00' }],
    };
    const REVOKED = {
        state: 'revoked', latest_consent_id: 2, embedding_count: 0, granted_at: '2026-11-19T08:00:00',
        revoked_at: '2026-11-19T09:00:00', history: GRANTED.history,
    };

    const realFetch = globalThis.fetch;
    afterEach(() => { globalThis.fetch = realFetch; });

    // The real api client over a fake network: the headers that reach fetch are the ones the routes require.
    function network(state) {
        const sent = [];
        globalThis.fetch = vi.fn(async (url, init) => {
            const body = (data, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(data), json: async () => data });
            if (String(url).endsWith('/api/csrf-token')) return body({ csrf_token: 'tok-1' });
            sent.push({ url: String(url), method: init.method, headers: init.headers, body: init.body ? JSON.parse(init.body) : null });
            if (init.method === 'POST' && String(url).endsWith('/grant')) { state.current = GRANTED; return body(GRANTED); }
            if (init.method === 'POST' && String(url).endsWith('/revoke')) { state.current = REVOKED; return body(REVOKED); }
            return body(state.current);
        });
        return sent;
    }

    async function mount(state) {
        const Vue = fakeVue();
        const client = createClinicApiClient();
        client.switchContext({ accountId: '7', dictationId: null });
        const consent = createConsentComponent(Vue, client).setup();
        await Vue.hooks.mounted[0]();
        return { consent, Vue };
    }

    it('shows the consent state from the server', async () => {
        const state = { current: NONE };
        network(state);
        const { consent } = await mount(state);
        expect(consent.view.value.status).toEqual({ value: 'none', label: 'Keine Einwilligung erteilt' });
        expect(consent.view.value.canGrant).toBe(true);
        expect(consent.view.value.canRevoke).toBe(false);
        expect(consent.pill('none')).toBe('pill-status--pending');
    });

    it('grant posts to /voice-consent/grant with CSRF token and Idempotency-Key', async () => {
        const state = { current: NONE };
        const sent = network(state);
        const { consent } = await mount(state);
        await consent.controller.grant();
        const post = sent.find((c) => c.method === 'POST');
        expect(post.url).toBe('/api/clinic/v1/voice-consent/grant');
        expect(post.headers['X-CSRF-Token']).toBe('tok-1');
        expect(post.headers['X-CSRFToken']).toBe('tok-1');
        expect(post.headers['Idempotency-Key']).toMatch(/\S{8,}/);
        expect(post.body.expected_consent_id).toBe(0);
        expect(post.body.statement_text).toContain('Einwilligung (Version 1)');
        expect(consent.view.value.status.value).toBe('granted');
        expect(consent.view.value.notice).toBe('Einwilligung gespeichert.');
    });

    it('revoke needs the confirmation step, then posts to /voice-consent/revoke with both headers', async () => {
        const state = { current: GRANTED };
        const sent = network(state);
        const { consent } = await mount(state);
        expect(consent.view.value.canRevoke).toBe(true);
        consent.controller.requestRevoke();
        expect(consent.view.value.confirmingRevoke).toBe(true);
        expect(sent.some((c) => c.method === 'POST')).toBe(false);
        await consent.controller.confirmRevoke();
        const post = sent.find((c) => c.method === 'POST');
        expect(post.url).toBe('/api/clinic/v1/voice-consent/revoke');
        expect(post.headers['X-CSRF-Token']).toBe('tok-1');
        expect(post.headers['Idempotency-Key']).toBeTruthy();
        expect(post.body).toEqual({ expected_consent_id: 1 });
        expect(consent.view.value.status.value).toBe('revoked');
        expect(consent.view.value.embeddingCount).toBe(0);
    });

    it('the Escape key leaves the revoke confirmation', async () => {
        const state = { current: GRANTED };
        network(state);
        const { consent } = await mount(state);
        consent.controller.requestRevoke();
        expect(consent.onKeydown({ key: 'Escape' })).toBe(true);
        expect(consent.view.value.confirmingRevoke).toBe(false);
    });
});
