import { describe, expect, it, vi } from 'vitest';
import { createPreferencesController } from '../../../static/js/clinic/preferences-ui.js';

function ok(data) {
    return { outcome: 'ok', status: 200, data, message: '' };
}
function bad(outcome, code, status) {
    return { outcome, status, code, message: 'msg ' + outcome };
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

function standardRoutes(extra) {
    return Object.assign({
        'GET /preferences/recipient-default': () => ok(DEFAULT),
        'GET /preferences/workstations/ws1': () => ok(WS),
    }, extra || {});
}

describe('preferences ui', () => {
    it('loads defaults and shows locked central settings (positive)', async () => {
        const c = createPreferencesController({ client: makeClient(standardRoutes()), workstationId: 'ws1' });
        expect(await c.load()).toBe(true);
        const v = c.view();
        expect(v.modes.map((m) => m.value)).toEqual(['all_eligible', 'person', 'staff_pool', 'self_correction']);
        expect(v.modes.find((m) => m.selected).value).toBe('person');
        expect(v.settings.find((s) => s.key === 'ui_language')).toMatchObject({ locked: true, lockedLabel: 'Zentral festgelegt' });
        expect(v.settings.find((s) => s.key === 'theme_dark_mode').locked).toBe(false);
        expect(c.setWorkstationValue('ui_language', 'fr')).toBe(false);
        expect(v.a11y.modeGroup.role).toBe('radiogroup');
        expect(v.labels.save).toBe('Übernehmen');
    });

    it('saves person, staff pool and self-correction with the expected version', async () => {
        const puts = [];
        const client = makeClient(standardRoutes({
            'PUT /preferences/recipient-default': (o) => { puts.push(o.body); return ok({ version: 3 }); },
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setMode('staff_pool');
        expect(c.setTarget('nope')).toBe(false);
        c.setTarget('p1');
        expect(await c.saveDefault()).toBe(true);
        c.setMode('self_correction');
        expect(c.view().choices.filter((x) => x.disabled).map((x) => x.value)).toEqual(['default_only', 'staff_pools_only']);
        await c.saveDefault();
        expect(puts[0]).toEqual({ mode: 'staff_pool', target_ids: ['p1'], choice: 'open', expected_version: 2 });
        expect(puts[1].mode).toBe('self_correction');
        expect(puts[1].target_ids).toEqual([]);
    });

    it('refuses person mode without a target (validation)', async () => {
        const client = makeClient(standardRoutes());
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setMode('person');
        c.state.draft.targetIds = [];
        expect(await c.saveDefault()).toBe(false);
        expect(client.calls.some((x) => x.method === 'PUT')).toBe(false);
        expect(c.state.error.outcome).toBe('validation');
    });

    it('reports unauthorized without retry', async () => {
        const client = makeClient(standardRoutes({
            'PUT /preferences/recipient-default': () => bad('permission', 'recipient_not_eligible'.toUpperCase(), 403),
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setMode('all_eligible');
        expect(await c.saveDefault()).toBe(false);
        expect(c.state.error).toMatchObject({ outcome: 'permission', retryable: false });
        expect(c.view().errorSurface.status).toBe('error');
    });

    it('keeps the draft on a stale version and reload resolves it', async () => {
        let version = 2;
        const client = makeClient(standardRoutes({
            'GET /preferences/recipient-default': () => ok(Object.assign({}, DEFAULT, { version })),
            'PUT /preferences/recipient-default': () => bad('conflict', 'STALE_VERSION', 409),
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setMode('all_eligible');
        expect(await c.saveDefault()).toBe(false);
        expect(c.state.error.outcome).toBe('conflict');
        expect(c.state.draft.mode).toBe('all_eligible');
        version = 5;
        await c.load();
        expect(c.state.version).toBe(5);
        expect(c.state.error).toBe(null);
    });

    it('marks network failure retryable and succeeds on retry', async () => {
        let fail = true;
        const client = makeClient(standardRoutes({
            'PUT /preferences/workstations/ws1': () => {
                if (fail) { fail = false; return bad('network', 'NETWORK', null); }
                return ok({ version: 4 });
            },
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setWorkstationValue('theme_dark_mode', false);
        expect(await c.saveWorkstation()).toBe(false);
        expect(c.state.error.retryable).toBe(true);
        expect(await c.saveWorkstation()).toBe(true);
        expect(client.calls.filter((x) => x.method === 'PUT')[1].body)
            .toEqual({ changes: { theme_dark_mode: false }, expected_version: 3 });
        expect(c.state.notice).toBe('Gespeichert.');
    });

    it('drops removed selections and newly locked settings on reload', async () => {
        let overrides = DEFAULT.overrides;
        let origins = WS.origins;
        const client = makeClient(standardRoutes({
            'GET /preferences/recipient-default': () => ok(Object.assign({}, DEFAULT, { overrides })),
            'GET /preferences/workstations/ws1': () => ok(Object.assign({}, WS, { origins })),
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        c.setMode('person');
        c.setTarget('8');
        c.setWorkstationValue('theme_dark_mode', false);
        overrides = { person: ['7'], staff_pool: [], self_correction: false };
        origins = Object.assign({}, WS.origins, { theme_dark_mode: 'central' });
        c.state.draft.targetIds = ['8'];
        await c.load();
        // load rebuilds the draft from the server; revalidation keeps it clean.
        c.setMode('person');
        c.state.draft.targetIds = ['8'];
        c.state.draft.changes = { theme_dark_mode: false };
        await c.switchContext({ accountId: 'u2', workstationId: 'ws1' });
        expect(c.state.draft.targetIds).toEqual(['7']);
        expect(c.state.draft.changes).toEqual({});
        expect(c.view().modes.find((m) => m.value === 'self_correction').disabled).toBe(true);
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: 'u2', dictationId: undefined });
    });

    it('revalidates at use time through resolve', async () => {
        const client = makeClient(standardRoutes({
            'POST /preferences/recipient-default/resolve': () => bad('permission', 'RECIPIENT_NOT_ELIGIBLE', 403),
        }));
        const c = createPreferencesController({ client, workstationId: 'ws1' });
        await c.load();
        expect(await c.resolve()).toBe(null);
        expect(c.state.error.outcome).toBe('permission');
        expect(client.calls.at(-1).body).toEqual({ override: null, expected_version: 2 });
    });

    it('handles arrow keys and Escape, and uses the injected translation', async () => {
        const t = (k) => (k === 'clinic.preferences.save' ? 'Speichern' : k);
        const c = createPreferencesController({ client: makeClient(standardRoutes()), workstationId: 'ws1', t });
        await c.load();
        expect(c.view().labels.save).toBe('Speichern');
        expect(c.view().labels.cancel).toBe('Abbrechen');
        c.onKeydown({ key: 'ArrowDown' }, 'mode');
        expect(c.state.draft.mode).toBe('staff_pool');
        c.onKeydown({ key: 'Escape' }, 'mode');
        expect(c.state.draft.mode).toBe('person');
        expect(c.onKeydown({ key: 'a' }, 'mode')).toBe(false);
    });

    it('stores nothing in the browser', async () => {
        const writes = [];
        const spy = { setItem: (...a) => writes.push(a), getItem: () => null, removeItem: () => {}, clear: () => {} };
        global.localStorage = spy;
        global.sessionStorage = spy;
        const c = createPreferencesController({ client: makeClient(standardRoutes()), workstationId: 'ws1' });
        await c.load();
        c.setMode('all_eligible');
        await c.saveDefault();
        expect(writes).toEqual([]);
    });
});
