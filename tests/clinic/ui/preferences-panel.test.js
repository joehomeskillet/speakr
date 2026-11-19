import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createPreferencesComponent } from '../../../static/js/clinic/preferences-ui.js';

function ok(data) {
    return { outcome: 'ok', status: 200, data, message: '' };
}
function bad(outcome, code, status, message) {
    return { outcome, status, code, message: message || 'msg ' + outcome };
}

// Minimal Vue stand-in for the node environment: refs, computed and lifecycle hooks only.
function fakeVue() {
    const mounted = [];
    const unmounted = [];
    const box = (value) => ({ value });
    return {
        mounted,
        unmounted,
        ref: box,
        shallowRef: box,
        computed: (fn) => ({ get value() { return fn(); } }),
        onMounted: (fn) => mounted.push(fn),
        onBeforeUnmount: (fn) => unmounted.push(fn),
    };
}

function makeClient(routes) {
    const calls = [];
    return {
        calls,
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ path, method, body: options && options.body });
            const handler = routes[method + ' ' + path];
            return handler ? handler(options) : bad('UNKNOWN', 'UNKNOWN', null);
        }),
    };
}

const DEFAULT = {
    version: 2, mode: 'person', target_ids: ['7'], choice: 'open',
    overrides: { person: ['7', '8'], staff_pool: ['p1'], self_correction: true },
};
const WS = {
    values: { theme_dark_mode: true, ui_language: 'de' },
    origins: { theme_dark_mode: 'workstation', ui_language: 'central' },
    version: 3,
};

function routes(extra) {
    return Object.assign({
        'GET /preferences/recipient-default': () => ok(DEFAULT),
        'GET /preferences/workstations/ws1': () => ok(WS),
    }, extra || {});
}

async function mountPanel(client, props) {
    const Vue = fakeVue();
    const comp = createPreferencesComponent(Vue, client, props || { workstationId: 'ws1' });
    const bindings = comp.setup();
    for (const fn of Vue.mounted) await fn();
    return { comp, b: bindings, Vue };
}

describe('preferences panel component', () => {
    it('renders German labels, accessible markup and read-only locked values', async () => {
        const { comp, b } = await mountPanel(makeClient(routes()));
        expect(b.loading.value).toBe(false);
        const v = b.view.value;
        expect(v.title).toBe('Empfänger und Arbeitsplatz');
        expect(v.modes.find((m) => m.selected).value).toBe('person');
        expect(v.settings.find((s) => s.key === 'ui_language')).toMatchObject({ locked: true, lockedLabel: 'Zentral festgelegt' });
        expect(comp.template).toContain('role="radiogroup"');
        expect(comp.template).toContain('role="alert"');
        expect(comp.template).toContain('aria-live="polite"');
        expect(comp.template).toContain('@keydown');
        expect(comp.template).toContain('type="submit"');
        // Locked values render as output, never as an input.
        expect(comp.template).toContain('clinic-preferences-locked-');
        expect(comp.template.split('v-if="s.locked"')[1].split('</template>')[0]).not.toContain('<input');
        const source = readFileSync(new URL('../../../static/js/clinic/preferences-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/\brgba?\(/);
        expect(b.controller.setWorkstationValue('ui_language', 'fr')).toBe(false);
    });

    it('saves a changed default with the expected version and shows a success notice', async () => {
        const puts = [];
        const client = makeClient(routes({
            'PUT /preferences/recipient-default': (o) => { puts.push(o.body); return ok({ version: 3 }); },
        }));
        const { b } = await mountPanel(client);
        b.controller.setMode('staff_pool');
        b.controller.setTarget('p1');
        expect(b.view.value.dirty).toBe(true);
        expect(await b.onSave()).toBe(true);
        expect(puts).toEqual([{ mode: 'staff_pool', target_ids: ['p1'], choice: 'open', expected_version: 2 }]);
        expect(b.view.value.notice).toBe('Gespeichert.');
        expect(b.noticeStyle.background).toBe('var(--bg-success-light)');
        expect(b.busy.value).toBe(false);
    });

    it('re-applies workstation edits after the default saved', async () => {
        const wsPuts = [];
        const client = makeClient(routes({
            'PUT /preferences/recipient-default': () => ok({ version: 3 }),
            'PUT /preferences/workstations/ws1': (o) => { wsPuts.push(o.body); return ok({ version: 4 }); },
        }));
        const { b } = await mountPanel(client);
        b.controller.setMode('all_eligible');
        b.controller.setWorkstationValue('theme_dark_mode', false);
        expect(await b.onSave()).toBe(true);
        expect(wsPuts).toEqual([{ changes: { theme_dark_mode: false }, expected_version: 3 }]);
    });

    it('shows the server refusal on 403 and sends no retry offer', async () => {
        const client = makeClient(routes({
            'PUT /preferences/recipient-default': () => bad('permission', 'RECIPIENT_NOT_ELIGIBLE', 403, 'Nicht erlaubt'),
        }));
        const { comp, b } = await mountPanel(client);
        b.controller.setMode('all_eligible');
        expect(await b.onSave()).toBe(false);
        const v = b.view.value;
        expect(v.error).toMatchObject({ outcome: 'permission', retryable: false });
        expect(b.errorStyle.value.background).toBe('var(--bg-danger-light)');
        expect(comp.template).toContain('v-if="view.error && view.error.retryable"');
    });

    it('keeps the draft on a 409 stale version and reload resolves it', async () => {
        let version = 2;
        const client = makeClient(routes({
            'GET /preferences/recipient-default': () => ok(Object.assign({}, DEFAULT, { version })),
            'PUT /preferences/recipient-default': () => bad('conflict', 'STALE_VERSION', 409),
        }));
        const { b } = await mountPanel(client);
        b.controller.setMode('all_eligible');
        expect(await b.onSave()).toBe(false);
        expect(b.view.value.error.message).toBe('Der Stand ist veraltet. Bitte neu laden.');
        expect(b.controller.state.draft.mode).toBe('all_eligible');
        version = 5;
        await b.controller.load();
        expect(b.controller.state.version).toBe(5);
        expect(b.view.value.error).toBe(null);
        expect(b.view.value.modes.find((m) => m.selected).value).toBe('person');
    });

    it('offers retry after a network failure and the retry saves', async () => {
        let fail = true;
        const client = makeClient(routes({
            'PUT /preferences/recipient-default': () => {
                if (fail) { fail = false; return bad('network', 'NETWORK', null); }
                return ok({ version: 3 });
            },
        }));
        const { b } = await mountPanel(client);
        b.controller.setChoice('staff_pools_only');
        expect(await b.onSave()).toBe(false);
        expect(b.view.value.error.retryable).toBe(true);
        expect(await b.onSave()).toBe(true);
        expect(client.calls.filter((x) => x.method === 'PUT')).toHaveLength(2);
        expect(b.view.value.error).toBe(null);
    });

    it('surfaces a refused load and Escape discards the draft', async () => {
        const denied = await mountPanel(makeClient({
            'GET /preferences/recipient-default': () => bad('permission', 'FORBIDDEN', 403, 'Kein Zugriff'),
        }), {});
        expect(denied.b.view.value.error).toMatchObject({ outcome: 'permission', message: 'Kein Zugriff' });
        expect(denied.b.loading.value).toBe(false);

        const { b } = await mountPanel(makeClient(routes()));
        b.controller.setMode('all_eligible');
        b.onKeydown({ key: 'Escape' });
        expect(b.controller.state.draft.mode).toBe('person');
        expect(b.view.value.dirty).toBe(false);
    });

    it('stops listening on unmount', async () => {
        const { Vue, b } = await mountPanel(makeClient(routes()));
        const before = b.view.value;
        for (const fn of Vue.unmounted) fn();
        b.controller.setMode('all_eligible');
        expect(b.view.value).toBe(before);
    });
});
