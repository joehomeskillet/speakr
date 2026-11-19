import { describe, expect, it, vi } from 'vitest';
import { useCorrectionEditor } from '../../../static/js/clinic/correction-editor-ui.js';
import {
    CorrectionSave, LABELS, registerCorrectionSave, useCorrectionSave,
} from '../../../static/js/clinic/correction-save-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const REV2 = '44444444-4444-4444-8444-444444444444';
const AUDIO = '33333333-3333-4333-8333-333333333333';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: '' });
const base = '/dictations/' + D;

function routes(extra) {
    return {
        ['GET ' + base + '/correction']: ok({
            dictation_id: D, text_revision_id: REV, text: 'Befund', links: [],
            state: { version: 3, audio_position_ms: 4200 },
        }),
        ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: AUDIO, segments: [] }),
        'GET /templates': ok({ items: [] }),
        ['POST ' + base + '/leases']: ok({ lease_id: 'L1', generation: 5, expires_at: new Date(Date.now() + 600000).toISOString() }),
        ['POST ' + base + '/correction/save']: ok({ text_revision_id: REV2, state_version: 4, audio_position_ms: 9000, derived_links: [] }),
        ...(extra || {}),
    };
}

function mockClient(table) {
    const calls = [];
    const client = {
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ key: method + ' ' + path, options });
            const hit = table[method + ' ' + path];
            const value = typeof hit === 'function' ? hit(options) : hit;
            return value || fail('UNKNOWN', 500);
        }),
    };
    return { client, calls };
}

async function setup(extra, saveOptions) {
    const mocked = mockClient(routes(extra));
    const editor = useCorrectionEditor({ client: mocked.client, dictationId: D, currentUser: { id: 7 } });
    await editor.load();
    await editor.acquire();
    const saver = useCorrectionSave({
        editor, client: mocked.client, dictationId: D, autosaveMs: 0, ...(saveOptions || {}),
    });
    return { ...mocked, editor, saver };
}

const saveCalls = (calls) => calls.filter((c) => c.key === 'POST ' + base + '/correction/save');

describe('correction save ui', () => {
    it('resumes the committed position from the server and keeps nothing in browser storage', async () => {
        const { saver, editor } = await setup();
        const res = await saver.loadResume();
        expect(res).toEqual({ ok: true, position: 4200 });
        expect(saver.resumePosition.value).toBe(4200);
        expect(saver.stateVersion.value).toBe(3);
        expect(globalThis.localStorage).toBeUndefined();
        editor.dispose();
    });

    it('saves with lease generation, revision, state version and position, then adopts the new revision', async () => {
        const { saver, editor, calls } = await setup();
        await saver.loadResume();
        editor.setText('Befund geändert');
        expect(saver.refresh()).toBe('unsaved');
        saver.setPosition(9000);
        const res = await saver.save();
        expect(res.ok).toBe(true);
        const sent = saveCalls(calls)[0];
        expect(sent.options.body).toEqual({
            text: 'Befund geändert', audio_position_ms: 9000, lease_generation: 5,
            expected_text_revision_id: REV, expected_state_version: 3,
        });
        expect(sent.options.ifMatch).toBe('"' + REV + '"');
        expect(typeof sent.options.idempotencyKey).toBe('string');
        expect(saver.state.value).toBe('saved');
        expect(editor.dirty.value).toBe(false);
        expect(editor.textRevisionId.value).toBe(REV2);
        expect(editor.etag.value).toBe('"' + REV2 + '"');
        expect(saver.resumePosition.value).toBe(9000);
        expect(saver.stateVersion.value).toBe(4);
        editor.dispose();
    });

    it('keeps a later edit unsaved when the text changed while saving', async () => {
        const holder = {};
        const { saver, editor } = await setup({
            ['POST ' + base + '/correction/save']: () => {
                holder.editor.setText('noch mehr');
                return ok({ text_revision_id: REV2, state_version: 4, audio_position_ms: 0, derived_links: [] });
            },
        });
        holder.editor = editor;
        editor.setText('eins');
        await saver.save();
        expect(saver.state.value).toBe('unsaved');
        expect(editor.dirty.value).toBe(true);
        editor.dispose();
    });

    it('shows a stale tab (409) as conflict, keeps the text and blocks saving until reload', async () => {
        const { saver, editor, client } = await setup({ ['POST ' + base + '/correction/save']: fail('conflict', 409) });
        editor.setText('lokal');
        const res = await saver.save();
        expect(res).toMatchObject({ ok: false, state: 'conflict' });
        expect(saver.message.value).toBe(LABELS.conflict);
        expect(editor.text.value).toBe('lokal');
        expect(editor.dirty.value).toBe(true);
        const before = client.request.mock.calls.length;
        await saver.save();
        expect(client.request.mock.calls.length).toBe(before);
        expect(saver.refresh()).toBe('conflict');
        const reloaded = await saver.reloadLatest();
        expect(reloaded.ok).toBe(true);
        expect(editor.text.value).toBe('Befund');
        expect(saver.state.value).toBe('saved');
        expect(saver.resumePosition.value).toBe(4200);
        editor.dispose();
    });

    it('maps 403 to a denied message without losing the text', async () => {
        const { saver, editor } = await setup({ ['POST ' + base + '/correction/save']: fail('permission', 403) });
        editor.setText('x');
        const res = await saver.save();
        expect(res.state).toBe('lost');
        expect(saver.message.value).toBe(LABELS.denied);
        expect(editor.text.value).toBe('x');
        editor.dispose();
    });

    it('treats 423 as holder loss and notifies the lease controller', async () => {
        const lease = {
            getState: () => ({ status: 'held', generation: 5, writable: true }),
            subscribe: vi.fn(), notifyGeneration: vi.fn(), setUnsaved: vi.fn(),
        };
        const { saver, editor } = await setup({ ['POST ' + base + '/correction/save']: fail('UNKNOWN', 423) }, { lease });
        editor.setText('y');
        const res = await saver.save();
        expect(res.state).toBe('lost');
        expect(saver.message.value).toBe(LABELS.lost);
        expect(lease.notifyGeneration).toHaveBeenCalledWith(-1);
        expect(editor.dirty.value).toBe(true);
        editor.dispose();
    });

    it('refuses to send without a held lease and shows holder loss', async () => {
        const { saver, editor, calls } = await setup();
        editor.setText('z');
        await editor.release();
        const res = await saver.save();
        expect(res).toMatchObject({ ok: false, state: 'lost' });
        expect(saver.message.value).toBe(LABELS.readOnly);
        expect(saveCalls(calls).length).toBe(0);
        editor.dispose();
    });

    it('retries a timeout with the same idempotency key and schedules a backoff', async () => {
        let n = 0;
        const timers = [];
        const { saver, editor, calls } = await setup({
            ['POST ' + base + '/correction/save']: () => (++n === 1
                ? fail('network', null)
                : ok({ text_revision_id: REV2, state_version: 4, audio_position_ms: 0, derived_links: [] })),
        }, { autosaveMs: 100, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {} });
        editor.setText('retry');
        const first = await saver.save();
        expect(first).toMatchObject({ ok: false, state: 'unsaved', retryable: true });
        expect(saver.message.value).toBe(LABELS.timeout);
        expect(timers.at(-1).ms).toBe(200);
        const second = await saver.save();
        expect(second.ok).toBe(true);
        const [a, b] = saveCalls(calls);
        expect(b.options.idempotencyKey).toBe(a.options.idempotencyKey);
        expect(saver.retries.value).toBe(0);
        editor.dispose();
    });

    it('uses a new idempotency key when the payload changed between retries', async () => {
        const { saver, editor, calls } = await setup({ ['POST ' + base + '/correction/save']: fail('network', null) });
        editor.setText('a');
        await saver.save();
        editor.setText('ab');
        await saver.save();
        const [a, b] = saveCalls(calls);
        expect(b.options.idempotencyKey).not.toBe(a.options.idempotencyKey);
        editor.dispose();
    });

    it('debounces autosave through touch()', async () => {
        const timers = [];
        const { saver, editor, calls } = await setup({}, { autosaveMs: 500, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {} });
        editor.setText('auto');
        saver.touch();
        expect(timers.at(-1).ms).toBe(500);
        expect(saveCalls(calls).length).toBe(0);
        await timers.at(-1).fn();
        await Promise.resolve();
        expect(saveCalls(calls).length).toBe(1);
        editor.dispose();
    });

    it('exports a component with German labels and registers it', () => {
        const app = { component: vi.fn() };
        expect(registerCorrectionSave(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-save', CorrectionSave);
        expect(CorrectionSave.template).toContain('aria-live="polite"');
        expect(CorrectionSave.template).toContain('@keydown="onKey"');
        expect(LABELS.save).toBe('Speichern');
    });
});
