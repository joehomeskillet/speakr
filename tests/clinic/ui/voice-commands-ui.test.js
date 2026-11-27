import { describe, expect, it } from 'vitest';
import {
    MAX_DEPTH, MAX_STEPS, createVoiceCommandsController, createVoiceCommandsComponent, createPronunciationHintsComponent,
    previewMacro, validateDefinition, isSensitiveAction,
} from '../../../static/js/clinic/voice-commands-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data, message: '' });
const bad = (outcome, message) => ({ outcome, status: 409, message: message || outcome });

function fakeClient(over) {
    const calls = [];
    const routes = Object.assign({
        '/resources/commands': ok({ items: [{ id: '1', trigger_phrase: 'm1', action_type: 'macro', action_payload: '["next_marker"]' }] }),
        '/resources/pronunciations': ok({ items: [] }),
        '/pronunciations/capabilities': ok({ training_proven: false }),
        '/tts/capability': ok({ available: false }),
    }, over || {});
    return {
        calls,
        request: async (path, o) => { calls.push(['request', path, o]); const r = routes[path]; return typeof r === 'function' ? r(o) : r; },
        command: async (body, o) => { calls.push(['command', body, o]); const r = routes.command; return typeof r === 'function' ? r(body, o) : (r || ok({ status: 'committed' })); },
    };
}

describe('voice commands ui', () => {
    it('previews bounded macro expansion', () => {
        expect(previewMacro(['m1', 'focus_editor'], { m1: ['next_marker'] }).steps).toEqual(['next_marker', 'focus_editor']);
        expect(previewMacro(['a'], { a: ['a'] }).error).toBe('macro_cycle');
        const chain = {};
        for (let i = 0; i <= MAX_DEPTH + 1; i += 1) chain['n' + i] = ['n' + (i + 1)];
        chain['n' + (MAX_DEPTH + 2)] = ['focus_editor'];
        expect(previewMacro(['n0'], chain).error).toBe('macro_too_deep');
        expect(previewMacro(new Array(MAX_STEPS + 1).fill('focus_editor'), {}).error).toBe('macro_too_long');
        expect(previewMacro(['rm_rf'], {}).error).toBe('unknown_action');
    });

    it('validates definitions and flags unsupported training', () => {
        expect(validateDefinition({ kind: 'command', phrase: 'x', action_ids: ['focus_editor'] }, {})).toEqual([]);
        expect(validateDefinition({ kind: 'command', phrase: '', action_ids: [] }, {})).toContain('phrase_required');
        expect(validateDefinition({ kind: 'pronunciation', written_form: 'a', phonetic_hint: 'b', train: true }, { training_proven: false }))
            .toContain('training_unsupported');
        expect(validateDefinition({ kind: 'pronunciation', written_form: 'a', phonetic_hint: 'b', train: true }, { training_proven: true })).toEqual([]);
    });

    it('edits commands while training is unavailable and sends the parity envelope', async () => {
        const client = fakeClient();
        const c = createVoiceCommandsController({ client });
        expect((await c.load()).outcome).toBe('ok');
        expect(c.state.capabilities.training_proven).toBe(false);
        expect(c.state.message).toContain('nicht verfügbar');
        const res = await c.saveCommand({ kind: 'command', phrase: ' weiter ', action_ids: ['next_marker'], revision: 2 }, { type: 'user', id: 7 });
        expect(res.outcome).toBe('ok');
        const [, body, opts] = client.calls.find((x) => x[0] === 'command');
        expect(body).toMatchObject({ command: 'save_command', expected_revision: 2, payload: { scope_type: 'user', scope_id: '7', phrase: 'weiter' } });
        expect(opts.idempotencyKey.length).toBeGreaterThanOrEqual(8);
    });

    it('requires fresh, single-use confirmation for sensitive commands', async () => {
        let t = 1000;
        const client = fakeClient();
        const c = createVoiceCommandsController({ client, now: () => t });
        await c.load();
        const def = { kind: 'command', phrase: 'senden', action_ids: ['send_dictation'] };
        expect(isSensitiveAction('send_dictation')).toBe(true);
        expect((await c.saveCommand(def, { type: 'user', id: 1 })).outcome).toBe('confirmation_required');
        c.requestConfirmation(def);
        t += 61000;
        expect((await c.saveCommand(def, { type: 'user', id: 1 })).outcome).toBe('confirmation_required');
        c.requestConfirmation(def);
        expect((await c.saveCommand(def, { type: 'user', id: 1 })).outcome).toBe('ok');
        expect((await c.saveCommand(def, { type: 'user', id: 1 })).outcome).toBe('confirmation_required');
        expect(client.calls.filter((x) => x[0] === 'command').length).toBe(1);
    });

    it('surfaces unauthorized, stale-version and failure results, and retries', async () => {
        let n = 0;
        const client = fakeClient({ command: () => { n += 1; return n === 1 ? bad('permission') : n === 2 ? bad('conflict') : n === 3 ? bad('network') : ok({}); } });
        const c = createVoiceCommandsController({ client });
        await c.load();
        const def = { kind: 'command', phrase: 'a', action_ids: ['focus_editor'] };
        await c.saveCommand(def, { type: 'user', id: 1 });
        expect(c.state.errors).toEqual(['permission']);
        await c.saveCommand(def, { type: 'user', id: 1 });
        expect(c.state.errors).toEqual(['stale_version']);
        await c.saveCommand(def, { type: 'user', id: 1 });
        expect(c.state.status).toBe('error');
        expect((await c.saveCommand(def, { type: 'user', id: 1 })).outcome).toBe('ok');
        expect(c.state.status).toBe('ready');
    });

    it('dispatches training only when the capability is proven', async () => {
        const off = createVoiceCommandsController({ client: fakeClient() });
        await off.load();
        expect((await off.requestTraining('p1')).code).toBe('CAPABILITY_UNAVAILABLE');
        const client = fakeClient({ '/pronunciations/capabilities': ok({ training_proven: true }) });
        const on = createVoiceCommandsController({ client });
        await on.load();
        await on.requestTraining('p1');
        expect(client.calls.find((x) => x[0] === 'command')[1].command).toBe('request_adaptation');
    });

    it('keeps command editing alive when the capability call fails and loads fail visibly', async () => {
        const c = createVoiceCommandsController({ client: fakeClient({ '/pronunciations/capabilities': bad('network') }) });
        expect((await c.load()).outcome).toBe('ok');
        expect(c.state.capabilities.training_proven).toBe(false);
        const d = createVoiceCommandsController({ client: fakeClient({ '/resources/commands': bad('permission') }) });
        await d.load();
        expect(d.state.status).toBe('error');
    });

    it('saves pronunciation hints and exposes labelled markup', async () => {
        const client = fakeClient({ '/pronunciations/commands': ok({ status: 'committed' }) });
        const c = createVoiceCommandsController({ client });
        await c.load();
        expect((await c.savePronunciation({ written_form: 'Ileus', phonetic_hint: 'ile-us' }, { type: 'user', id: 1 })).outcome).toBe('ok');
        const comp = createVoiceCommandsComponent({});
        expect(comp.template).toContain('for="vc-phrase"');
        expect(comp.template).toContain('aria-live="polite"');
        expect(comp.template).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    });
});

describe('pronunciation hints panel', () => {
    function mount(client) {
        let mounted;
        const vue = {
            reactive: (value) => value, ref: (value) => ({ value }),
            computed: (get) => ({ get value() { return get(); } }),
            onMounted: (callback) => { mounted = callback; },
        };
        const panel = createPronunciationHintsComponent(vue).setup({ client, scope: { type: 'user', id: '7' } });
        return { panel, mounted };
    }

    it('loads hints without the commands permission and preserves capability evidence', async () => {
        const hint = { id: '1', written_form: 'Ileus', phonetic_hint: 'ile-us', sounds_like: 'Ile us' };
        const client = fakeClient({ '/resources/pronunciations': ok({ items: [hint] }) });
        const { panel, mounted } = mount(client);
        await mounted();
        expect(panel.loaded.value).toBe(true);
        expect(panel.c.state.pronunciations).toEqual([hint]);
        expect(panel.c.state.capabilities).toEqual({ training_proven: false, tts: { available: false } });
        expect(client.calls.map((call) => call[1])).toEqual([
            '/resources/pronunciations', '/pronunciations/capabilities', '/tts/capability',
        ]);
        panel.edit(hint);
        expect(panel.form).toStrictEqual({ written_form: 'Ileus', phonetic_hint: 'ile-us', sounds_like: 'Ile us', editing: true });
        panel.reset();
        expect(panel.form.written_form).toBe('');
        expect(panel.form.editing).toBe(false);
    });

    it('creates and updates a hint, then reads the saved list back', async () => {
        let items = [];
        const client = fakeClient({
            '/resources/pronunciations': () => ok({ items }),
            '/pronunciations/commands': (options) => {
                items = [{ id: 'hint-1', ...options.body.payload }];
                return ok({ status: 'committed', hint_id: 'hint-1' });
            },
        });
        const { panel, mounted } = mount(client);
        await mounted();
        Object.assign(panel.form, { written_form: ' Ileus ', phonetic_hint: ' ile-us ', sounds_like: ' Ile us ' });
        await panel.save();
        expect(panel.c.state.pronunciations).toEqual([{
            id: 'hint-1', scope_type: 'user', scope_id: '7', written_form: 'Ileus', phonetic_hint: 'ile-us', sounds_like: 'Ile us',
        }]);
        expect(panel.form.written_form).toBe('');
        panel.edit(panel.c.state.pronunciations[0]);
        panel.form.phonetic_hint = 'i-le-us';
        panel.form.sounds_like = '  ';
        await panel.save();
        expect(panel.c.state.pronunciations).toHaveLength(1);
        expect(panel.c.state.pronunciations[0]).toMatchObject({ written_form: 'Ileus', phonetic_hint: 'i-le-us', sounds_like: null });
        const writes = client.calls.filter((call) => call[1] === '/pronunciations/commands');
        expect(writes).toHaveLength(2);
        expect(writes[0][2]).toMatchObject({ method: 'POST', body: { command: 'save_pronunciation_hint' } });
        expect(writes[0][2].body.request_id).toBeTruthy();
        expect(panel.c.state.message).toBe('Gespeichert.');
        await panel.load();
        expect(panel.c.state.pronunciations[0].phonetic_hint).toBe('i-le-us');
    });

    it('keeps input after validation or save failure and retries failed reads', async () => {
        let response = bad('permission');
        const client = fakeClient({
            '/resources/pronunciations': () => response,
            '/pronunciations/capabilities': bad('network'), '/tts/capability': bad('network'),
            '/pronunciations/commands': bad('network'),
        });
        const { panel, mounted } = mount(client);
        await mounted();
        expect(panel.loaded.value).toBe(false);
        await panel.save();
        expect(client.calls.filter((call) => call[1] === '/pronunciations/commands')).toHaveLength(0);
        response = ok({ items: [] });
        await panel.load();
        expect(panel.loaded.value).toBe(true);
        expect(panel.c.state.capabilities).toEqual({ training_proven: false, tts: { available: null } });
        expect(panel.c.state.errors).toEqual([]);
        panel.form.written_form = 'Ileus';
        expect((await panel.save()).outcome).toBe('validation');
        expect(panel.form.written_form).toBe('Ileus');
        panel.form.phonetic_hint = 'ile-us';
        expect((await panel.save()).outcome).toBe('network');
        expect(panel.form.phonetic_hint).toBe('ile-us');
        expect(panel.c.state.errors).toEqual(['network']);
        panel.c.state.status = 'saving';
        const before = client.calls.length;
        await panel.save();
        expect(client.calls).toHaveLength(before);
    });
});
