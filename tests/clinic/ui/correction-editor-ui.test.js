import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    CorrectionEditor, LABELS, registerCorrectionEditor, useCorrectionEditor,
} from '../../../static/js/clinic/correction-editor-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const REV = '22222222-2222-4222-8222-222222222222';
const AUDIO = '33333333-3333-4333-8333-333333333333';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: '' });

function template(id, version) {
    return {
        id, name: 'Arztbrief', scope: 'user', scope_id: '7', version,
        mime_type: 'text/plain', content_sha256: 'a'.repeat(64), insertion_mark: '[[TEXT]]',
    };
}

// routes: map of "METHOD path" to a response or a function returning one.
function mockClient(routes) {
    const calls = [];
    const client = {
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push(method + ' ' + path);
            const hit = routes[method + ' ' + path];
            const value = typeof hit === 'function' ? hit(options) : hit;
            return value || fail('UNKNOWN', 500);
        }),
    };
    return { client, calls };
}

const base = '/dictations/' + D;
function happy(extra) {
    return {
        ['GET ' + base + '/correction']: ok({ dictation_id: D, text_revision_id: REV, text: 'Befund unauffällig', links: [] }),
        ['GET ' + base + '/audio-revisions/current']: ok({ revision_id: AUDIO, segments: [] }),
        ['GET /templates?dictation_id=' + D]: ok({ items: [template('t1', 2), template('t0', 1)] }),
        ['POST ' + base + '/leases']: ok({ lease_id: 'L1', generation: 5, expires_at: new Date(Date.now() + 600000).toISOString() }),
        ...(extra || {}),
    };
}

function make(routes) {
    const mocked = mockClient(routes || happy());
    const editor = useCorrectionEditor({ client: mocked.client, dictationId: D, currentUser: { id: 7 } });
    return { ...mocked, editor };
}

describe('correction editor ui', () => {
    it('loads text, validated audio and exposes revision and etag', async () => {
        const { editor, client } = make();
        const res = await editor.load();
        expect(res.ok).toBe(true);
        expect(editor.text.value).toBe('Befund unauffällig');
        expect(editor.textRevisionId.value).toBe(REV);
        expect(editor.etag.value).toBe('"' + REV + '"');
        expect(editor.manual.value).toBe(false);
        expect(editor.audio.value.status).toBe('ready');
        expect(editor.audioSynced.value).toBeNull();
        expect(editor.audio.value.etag).toBe('"' + AUDIO + '"');
        expect(editor.audio.value.src).toBe(`/api/clinic/v1${base}/audio/derived?format=wav&audio_revision_id=${AUDIO}`);
        expect(editor.templates.items.map((t) => t.id)).toEqual(['t1', 't0']);
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: 7, dictationId: D });
    });

    it('keeps audio playable when imported text is unsynced', async () => {
        const { editor } = make(happy({
            ['GET ' + base + '/correction']: ok({ text_revision_id: REV, text: 'Import', audio_synced: false }),
        }));
        await editor.load();
        expect(editor.audioSynced.value).toBe(false);
        expect(editor.text.value).toBe('Import');
        expect(editor.audio.value.src).toContain(AUDIO);
    });

    it('runs in manual mode when ASR produced no text and edits only with the lease', async () => {
        const routes = happy({ ['GET ' + base + '/correction']: ok({ dictation_id: D, text_revision_id: null, text: '', links: [] }) });
        const { editor } = make(routes);
        await editor.load();
        expect(editor.manual.value).toBe(true);
        expect(editor.etag.value).toBeNull();
        expect(editor.audio.value.status).toBe('ready');
        expect(editor.setText('x')).toBe(false);
        expect(editor.canEdit.value).toBe(false);
        await editor.acquire();
        expect(editor.lease.value.generation).toBe(5);
        expect(editor.canEdit.value).toBe(true);
        expect(editor.setText('Handschriftlich nachgetragen')).toBe(true);
        expect(editor.text.value).toBe('Handschriftlich nachgetragen');
        expect(editor.dirty.value).toBe(true);
        editor.dispose();
    });

    it('keeps unsaved edits instead of reloading over them', async () => {
        const { editor, client } = make();
        await editor.load();
        await editor.acquire();
        editor.setText('geändert');
        const before = client.request.mock.calls.length;
        const res = await editor.load();
        expect(res.code).toBe('unsaved');
        expect(client.request.mock.calls.length).toBe(before);
        expect(editor.text.value).toBe('geändert');
        expect((await editor.load({ discard: true })).ok).toBe(true);
        expect(editor.text.value).toBe('Befund unauffällig');
        editor.dispose();
    });

    it('applies a versioned template and flags a newer version', async () => {
        const { editor } = make();
        await editor.load();
        const res = await editor.applyTemplate('t0');
        expect(res.ok).toBe(true);
        expect(editor.template.value).toMatchObject({ id: 't0', version: 1, marker: '[[TEXT]]', stale: true });
        const missing = await editor.applyTemplate('nope');
        expect(missing.ok).toBe(false);
        expect(editor.template.value.id).toBe('t0');
        const latest = await editor.applyTemplate('t1');
        expect(latest.template.version).toBe(2);
        expect(latest.template.stale).toBe(false);
    });

    it('reports unauthorized (403) for the dictation without exposing text', async () => {
        const routes = happy({ ['GET ' + base + '/correction']: { outcome: 'permission', status: 403, code: 'FORBIDDEN' } });
        const { editor } = make(routes);
        const res = await editor.load();
        expect(res.code).toBe('denied');
        expect(editor.error.value.message).toBe(LABELS.denied);
        expect(editor.text.value).toBe('');
        expect(editor.audio.value.src).toBeNull();
        expect(editor.canEdit.value).toBe(false);
    });

    it('treats an unreadable dictation (404) as not found and a missing audio as non-fatal', async () => {
        const gone = make(happy({ ['GET ' + base + '/correction']: { outcome: 'UNKNOWN', status: 404, code: 'NOT_FOUND' } }));
        expect((await gone.editor.load()).code).toBe('notFound');
        const noAudio = make(happy({ ['GET ' + base + '/audio-revisions/current']: { outcome: 'UNKNOWN', status: 404, code: 'NOT_FOUND' } }));
        expect((await noAudio.editor.load()).ok).toBe(true);
        expect(noAudio.editor.audio.value).toMatchObject({ status: 'unavailable', src: null, message: LABELS.audioMissing });
        expect(noAudio.editor.text.value).toBe('Befund unauffällig');
    });

    it('reports a stale version (409) and reloads on demand', async () => {
        let stale = true;
        const routes = happy({
            ['GET ' + base + '/correction']: () => (stale
                ? { outcome: 'conflict', status: 409, code: 'REVISION_CONFLICT' }
                : ok({ dictation_id: D, text_revision_id: REV, text: 'neu', links: [] })),
        });
        const { editor } = make(routes);
        const res = await editor.load();
        expect(res.code).toBe('stale');
        expect(editor.error.value.message).toBe(LABELS.stale);
        stale = false;
        expect((await editor.load({ discard: true })).ok).toBe(true);
        expect(editor.error.value).toBeNull();
        expect(editor.text.value).toBe('neu');
    });

    it('stale template list entry is flagged and a template failure does not block transcription', async () => {
        const down = make(happy({ ['GET /templates?dictation_id=' + D]: { outcome: 'network', status: null, code: 'NETWORK' } }));
        expect((await down.editor.load()).ok).toBe(true);
        expect(down.editor.templatesError.value).not.toBe('');
        expect(down.editor.text.value).toBe('Befund unauffällig');
    });

    it('retries after a network failure with the same method', async () => {
        let attempts = 0;
        const routes = happy({
            ['GET ' + base + '/correction']: () => {
                attempts += 1;
                return attempts === 1
                    ? { outcome: 'network', status: null, code: 'NETWORK' }
                    : ok({ dictation_id: D, text_revision_id: REV, text: 'zweiter Versuch', links: [] });
            },
        });
        const { editor } = make(routes);
        const first = await editor.load();
        expect(first.code).toBe('failed');
        expect(editor.loading.value).toBe(false);
        expect(editor.error.value.message).toBe(LABELS.failed);
        expect((await editor.load()).ok).toBe(true);
        expect(editor.error.value).toBeNull();
        expect(editor.text.value).toBe('zweiter Versuch');
    });

    it('rejects a malformed correction payload', async () => {
        const { editor } = make(happy({ ['GET ' + base + '/correction']: ok({ text: 5 }) }));
        expect((await editor.load()).code).toBe('failed');
    });

    it('stops accepting edits after the lease is released and keeps the text in memory', async () => {
        const { editor } = make(happy({ ['POST ' + base + '/leases/release']: ok(null) }));
        await editor.load();
        await editor.acquire();
        expect(editor.setText('lokal')).toBe(true);
        await editor.release();
        expect(editor.canEdit.value).toBe(false);
        expect(editor.setText('weiter')).toBe(false);
        expect(editor.text.value).toBe('lokal');
        editor.dispose();
    });

    it('ignores a result that arrives after dispose and writes nothing to browser storage', async () => {
        const writes = [];
        globalThis.localStorage = { setItem: (...a) => writes.push(a), getItem: () => null };
        const { editor, client } = make();
        const pending = editor.load();
        editor.dispose();
        const res = await pending;
        expect(res.code).toBe('cancelled');
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        expect(writes).toEqual([]);
        delete globalThis.localStorage;
    });

    it('component and registration expose labels, roles, keyboard-native controls and no hard colours', () => {
        const app = { component: vi.fn() };
        registerCorrectionEditor(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-editor', CorrectionEditor);
        expect(Object.keys(CorrectionEditor.props)).toEqual(['client', 'dictationId', 'currentUser', 'vue']);
        const t = CorrectionEditor.template;
        expect(t).toContain('role="alert"');
        expect(t).toContain('aria-live="polite"');
        expect(t).toContain('<audio');
        expect(t).toContain('<textarea');
        expect(t).toContain('aria-readonly');
        expect(t).toContain('LABELS.template');
        const source = readFileSync(new URL('../../../static/js/clinic/correction-editor-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
