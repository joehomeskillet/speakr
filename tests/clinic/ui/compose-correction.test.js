import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    CorrectionView,
    STEP_SECONDS,
    TEXT,
    createCorrectionViewController,
    createLeaseAdapter,
    registerCorrectionView,
    scopedClient,
} from '../../../static/js/clinic/compose-correction.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-correction.js'), 'utf8');

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status, message) => ({ outcome, status, code: null, message });

const correction = (extra = {}) => ({
    dictation_id: 'd1',
    text_revision_id: 'r1',
    text: 'Hallo Welt heute',
    state: { version: 1, audio_position_ms: 4000 },
    links: [
        { id: 'l1', text_start: 0, text_end: 5, audio_start_ms: 0, audio_end_ms: 900 },
        { id: 'l2', text_start: 6, text_end: 10, audio_start_ms: 1000, audio_end_ms: 1800 },
    ],
    markers: [],
    ...extra,
});

const lease = (generation = 1) => ok({
    lease_id: 'lease-1', generation, expires_at: new Date(Date.now() + 600000).toISOString(),
});

/** Fake clinic client: `routes` maps "METHOD path-suffix" to a handler or a fixed result. */
function fakeClient(routes = {}) {
    const calls = [];
    const find = (method, path) => {
        const key = Object.keys(routes).find((k) => {
            const [m, p] = k.split(' ');
            return m === method && path.endsWith(p);
        });
        return key ? routes[key] : null;
    };
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            const hit = find(method, path);
            if (hit === null) return ok({ items: [] });
            return typeof hit === 'function' ? hit(path, options) : hit;
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}

const base = (extra = {}) => ({
    'GET /dictations/d1/correction': ok(correction()),
    'GET /dictations/d1/audio-revisions/current': ok({ revision_id: 'a1', segments: [] }),
    'GET /preferences/correction-mode': fail('http', 404, 'nicht gefunden'),
    'PUT /preferences/correction-mode': fail('http', 404, 'nicht gefunden'),
    'POST /dictations/d1/leases': lease(),
    'POST /dictations/d1/leases/release': ok({}),
    'POST /dictations/d1/leases/renew': lease(),
    'POST /dictations/d1/correction/save': ok({ text_revision_id: 'r2', state_version: 2, audio_position_ms: 5000 }),
    ...extra,
});

class FakeAudio extends EventTarget {
    constructor() {
        super();
        this.currentTime = 0;
        this.duration = 60;
        this.playbackRate = 1;
        this.paused = true;
        this.preservesPitch = true;
    }

    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
}

const user = { id: 7 };

function controllerFor(client, extra = {}) {
    return createCorrectionViewController({
        client, dictationId: 'd1', currentUser: user, heartbeatMs: 600000, autosaveMs: 0, ...extra,
    });
}

const count = (client, method, suffix) => client.calls.filter((c) => c.method === method && c.path.endsWith(suffix)).length;

async function openForEdit(client, extra = {}) {
    const c = controllerFor(client, extra);
    await c.start();
    await c.editor.acquire();
    return c;
}

describe('compose-correction contract', () => {
    it('exports the view and registers it as clinic-correction-view', () => {
        const app = { component: vi.fn() };
        expect(registerCorrectionView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-correction-view', CorrectionView);
        expect(Object.keys(CorrectionView.props)).toEqual(expect.arrayContaining(['client', 'dictationId', 'currentUser', 'vue']));
        expect(CorrectionView.emits).toContain('close');
    });

    it('composes exactly the eight building blocks and reuses tokens and api-client', () => {
        for (const mod of [
            'correction-editor-ui', 'correction-alignment-ui', 'correction-waveform-ui', 'playback-tempo-ui',
            'correction-modes-ui', 'correction-save-ui', 'audio-markers-ui', 'playback-gain-ui',
        ]) expect(source).toContain(`./${mod}.js`);
        expect(source).toContain("from './tokens.js'");
        expect(source).toContain("from './api-client.js'");
        const imports = [...source.matchAll(/from '(\.\/(?!correction-player-position\.js')[^']+)'/g)].map((m) => m[1]);
        expect(imports).toHaveLength(new Set(imports).size);
        expect(imports).toHaveLength(11);
    });

    it('uses German labels, tokens only, keyboard-operable native controls and no browser storage', () => {
        const template = JSON.stringify(Object.values(CorrectionView.components || {}).map((c) => c.template));
        expect(template).toContain('ccv-title');
        expect(template).toContain('aria-labelledby');
        expect(template).toContain('role=\\"toolbar\\"');
        expect(template).toContain('role=\\"radiogroup\\"');
        expect(template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(TEXT.title).toBe('Korrektur');
        expect(TEXT.close).toBe('Schliessen');
    });

    it('shows no patient data (W16): the view carries the dictation id only', () => {
        const template = JSON.stringify(Object.values(CorrectionView.components).map((c) => c.template));
        expect(template).not.toMatch(/patient|Patient|Fall-ID|Titel|Empf/);
    });

    it('re-keys the inner body on the dictation id', () => {
        expect(CorrectionView.template).toContain(':key="dictationId"');
    });

    it('requires a dictation id and a current user id', () => {
        expect(() => createCorrectionViewController({ client: fakeClient(), currentUser: user })).toThrow(/dictationId/);
        expect(() => createCorrectionViewController({ client: fakeClient(), dictationId: 'd1' })).toThrow(/currentUser/);
    });

    it('uses the 3 second skip step of the tempo block', () => {
        expect(STEP_SECONDS).toBe(3);
        expect(createCorrectionViewController({ client: fakeClient(base()), dictationId: 'd1', currentUser: user }).stepSeconds).toBe(3);
    });
});

describe('loading and shared state', () => {
    it('loads text, audio revision, links, resume position and defaults to the W6 mode', async () => {
        const client = fakeClient(base());
        const c = controllerFor(client);
        const res = await c.start();
        expect(res.ok).toBe(true);
        expect(c.ready.value).toBe(true);
        expect(c.editor.text.value).toBe('Hallo Welt heute');
        expect(c.editor.audio.value.revisionId).toBe('a1');
        expect(c.alignment.status.value).toBe('ready');
        expect(c.alignment.segments.value.some((s) => s.aligned)).toBe(true);
        expect(c.saver.resumePosition.value).toBe(4000);
        expect(c.resumeLabel.value).toBe('Letzte gespeicherte Position: 00:04');
        expect(c.modes.mode.value).toBe('stop_on_typing');
        expect(c.modes.status.value).toBe('local'); // preference route is missing: session-only, no storage
        expect(client.switchContext).toHaveBeenCalledWith({ accountId: 7, dictationId: 'd1' });
        c.dispose();
    });

    it('is read-only until the edit lease is held and refuses text changes', async () => {
        const c = controllerFor(fakeClient(base()));
        await c.start();
        expect(c.editor.canEdit.value).toBe(false);
        expect(c.setText('x')).toBe(false);
        expect(c.editor.dirty.value).toBe(false);
        c.dispose();
    });

    it('reports a 403 on load, mounts nothing and recovers on retry', async () => {
        let allow = false;
        const client = fakeClient(base({
            'GET /dictations/d1/correction': () => (allow ? ok(correction()) : fail('permission', 403, 'verboten')),
        }));
        const c = controllerFor(client);
        const res = await c.start();
        expect(res.ok).toBe(false);
        expect(c.ready.value).toBe(false);
        expect(c.editor.error.value.code).toBe('denied');
        expect(c.editor.error.value.message).toBe('Keine Berechtigung für dieses Diktat.');
        allow = true;
        expect((await c.load()).ok).toBe(true);
        expect(c.ready.value).toBe(true);
        expect(c.editor.error.value).toBeNull();
        c.dispose();
    });

    it('passes the recording id for the peaks only from the caller or the audio response', async () => {
        const c = controllerFor(fakeClient(base()), { recordingId: 'rec-9' });
        expect(c.recordingId()).toBe('rec-9');
        const bare = controllerFor(fakeClient(base()));
        expect(bare.recordingId()).toBeNull();
        c.dispose();
        bare.dispose();
    });
});

describe('save, W10 follow-up, stale revision and retry', () => {
    it('saves with the lease generation, adopts the new revision and reloads the alignment', async () => {
        const client = fakeClient(base());
        const c = await openForEdit(client);
        expect(c.editor.canEdit.value).toBe(true);
        const before = count(client, 'GET', '/dictations/d1/correction');
        const markersKey = c.revisionGeneration.value;
        expect(c.setText('Hallo Welt heute morgen')).toBe(true);
        expect(c.editor.dirty.value).toBe(true);
        const res = await c.save();
        expect(res.ok).toBe(true);
        const post = client.calls.find((x) => x.path.endsWith('/correction/save'));
        expect(post.options.body).toMatchObject({
            text: 'Hallo Welt heute morgen', lease_generation: 1, expected_text_revision_id: 'r1', audio_position_ms: 4000,
        });
        expect(post.options.ifMatch).toBe('"r1"');
        expect(c.editor.textRevisionId.value).toBe('r2');
        expect(c.editor.dirty.value).toBe(false);
        expect(c.saver.state.value).toBe('saved');
        expect(count(client, 'GET', '/dictations/d1/correction')).toBeGreaterThan(before);
        expect(c.revisionGeneration.value).toBe(markersKey + 1);
        c.dispose();
    });

    it('sends the audio position of the player with the save', async () => {
        const client = fakeClient(base());
        const c = await openForEdit(client);
        const audio = new FakeAudio();
        c.bindAudio(audio);
        audio.currentTime = 12.5;
        c.setText('Neu');
        await c.save();
        const post = client.calls.find((x) => x.path.endsWith('/correction/save'));
        expect(post.options.body.audio_position_ms).toBe(12500);
        c.dispose();
    });

    it('Ctrl+S saves from anywhere in the view', async () => {
        const client = fakeClient(base());
        const c = await openForEdit(client);
        c.setText('Anders');
        const prevent = vi.fn();
        expect(c.onShortcut({ ctrlKey: true, key: 'S', preventDefault: prevent })).toBe(true);
        expect(prevent).toHaveBeenCalled();
        await vi.waitFor(() => expect(count(client, 'POST', '/correction/save')).toBe(1));
        expect(c.onShortcut({ key: 's' })).toBe(false);
        c.dispose();
    });

    it('handles a 409 stale revision: conflict state, then reload of the latest text', async () => {
        let stale = true;
        const client = fakeClient(base({
            'POST /dictations/d1/correction/save': fail('conflict', 409, 'veraltet'),
            'GET /dictations/d1/correction': () => ok(stale ? correction() : correction({ text: 'Server', text_revision_id: 'r9' })),
        }));
        const c = await openForEdit(client);
        c.setText('Lokal');
        const res = await c.save();
        expect(res.ok).toBe(false);
        expect(c.saver.state.value).toBe('conflict');
        stale = false;
        const reloaded = await c.reloadLatest();
        expect(reloaded.ok).toBe(true);
        expect(c.editor.text.value).toBe('Server');
        expect(c.editor.dirty.value).toBe(false);
        expect(c.saver.state.value).toBe('saved');
        expect(c.loadGeneration.value).toBeGreaterThan(1);
        c.dispose();
    });

    it('keeps the idempotency key when a network failure is retried', async () => {
        let attempt = 0;
        const client = fakeClient(base({
            'POST /dictations/d1/correction/save': () => {
                attempt += 1;
                return attempt === 1 ? fail('network', 0, 'offline') : ok({ text_revision_id: 'r2', state_version: 2 });
            },
        }));
        const c = await openForEdit(client);
        c.setText('Retry');
        const first = await c.save();
        expect(first.ok).toBe(false);
        expect(first.retryable).toBe(true);
        expect(c.saver.state.value).toBe('unsaved');
        const second = await c.save();
        expect(second.ok).toBe(true);
        const posts = client.calls.filter((x) => x.path.endsWith('/correction/save'));
        expect(posts).toHaveLength(2);
        expect(posts[1].options.idempotencyKey).toBe(posts[0].options.idempotencyKey);
        c.dispose();
    });

    it('reports a lost lease (423) and a refused save (403) without claiming success', async () => {
        const client = fakeClient(base({ 'POST /dictations/d1/correction/save': fail('http', 423, 'gesperrt') }));
        const c = await openForEdit(client);
        c.setText('Weg');
        expect((await c.save()).state).toBe('lost');
        c.dispose();

        const denied = fakeClient(base({ 'POST /dictations/d1/correction/save': fail('permission', 403, 'verboten') }));
        const d = await openForEdit(denied);
        d.setText('Nein');
        const res = await d.save();
        expect(res.ok).toBe(false);
        expect(d.saver.state.value).toBe('lost');
        expect(d.saver.message.value).toBe('Keine Berechtigung zum Speichern.');
        expect(d.editor.dirty.value).toBe(true);
        d.dispose();
    });

    it('close saves unsaved edits first and stays open when the save fails', async () => {
        const onClose = vi.fn();
        const failing = fakeClient(base({ 'POST /dictations/d1/correction/save': fail('http', 500, 'kaputt') }));
        const c = await openForEdit(failing, { onClose });
        c.setText('Offen');
        expect(await c.close()).toBe(false);
        expect(onClose).not.toHaveBeenCalled();
        expect(c.closeMessage.value).toBe(TEXT.closeFailed);
        c.dispose();

        const good = await openForEdit(fakeClient(base()), { onClose });
        good.setText('Fertig');
        expect(await good.close()).toBe(true);
        expect(onClose).toHaveBeenCalledWith({ dictationId: 'd1' });
        good.dispose();
    });

    it('the lease adapter exposes the authoritative editor lease and ignores controller-only calls', async () => {
        const c = await openForEdit(fakeClient(base()));
        const adapter = createLeaseAdapter(c.editor, null);
        expect(adapter.getState().generation).toBe(1);
        expect(adapter.setUnsaved(null)).toBe(false);
        expect(typeof adapter.subscribe(() => {})).toBe('function');
        const own = { getState: () => ({}) };
        expect(createLeaseAdapter({ leaseController: own }, null)).toBe(own);
        c.dispose();
    });
});

describe('shared audio element', () => {
    it('feeds the current word, the save position and the modes from one element', async () => {
        const c = await openForEdit(fakeClient(base()));
        const audio = new FakeAudio();
        c.bindAudio(audio);
        expect(c.audioEl.value).toBe(audio);
        audio.currentTime = 1.2;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(c.alignment.currentWord.value).toMatchObject({ id: 'l2' });
        expect(c.getPositionMs()).toBe(1200);
        c.seek(3);
        expect(audio.currentTime).toBe(3);
        expect(c.seekToResume()).toBe(true);
        expect(audio.currentTime).toBe(4);
        c.dispose();
    });

    it('stop-on-typing pauses at the first key and resumes before the pause position after the idle time', async () => {
        let now = 0;
        const c = await openForEdit(fakeClient(base()), { modesOptions: { now: () => now, resumeIdleMs: 1500, rewindMs: 2000 } });
        const audio = new FakeAudio();
        c.bindAudio(audio);
        audio.currentTime = 10;
        audio.play();
        c.onKeydown({ key: 'a' });
        expect(audio.paused).toBe(true);
        now = 1600;
        c.modes.tick();
        expect(audio.paused).toBe(false);
        expect(audio.currentTime).toBe(8);
        c.onKeydown({ key: 'Shift' });
        c.onKeydown({ key: 's', ctrlKey: true });
        expect(c.modes.waiting.value).toBe(false);
        c.dispose();
    });

    it('type-while-playing never touches playback', async () => {
        const c = await openForEdit(fakeClient(base()));
        const audio = new FakeAudio();
        c.bindAudio(audio);
        await c.modes.setMode('type_while_playing');
        audio.currentTime = 5;
        audio.play();
        c.onKeydown({ key: 'a' });
        expect(audio.paused).toBe(false);
        c.dispose();
    });

    it('forwards the text area selection to the alignment and the tempo sync to the current word', async () => {
        const c = await openForEdit(fakeClient(base()));
        c.onSelect({ target: { selectionStart: 0, selectionEnd: 5 } });
        expect(c.alignment.selection.value).toEqual({ start: 0, end: 5 });
        c.onSync({ reason: 'seeked', positionMs: 100, rate: 1 });
        expect(c.alignment.currentWord.value).toMatchObject({ id: 'l1' });
        c.dispose();
    });

    it('gain falls back to "nicht verfügbar" without an AudioContext and uses an injected factory', async () => {
        const plain = await openForEdit(fakeClient(base()));
        plain.bindAudio(new FakeAudio());
        expect(plain.gainState.value.value.available).toBe(false);
        expect(plain.gainState.value.value.message).toBe('Wiedergabeverstärkung ist in diesem Browser nicht verfügbar.');
        plain.dispose();

        const ctx = {
            destination: {},
            resume: vi.fn(async () => {}),
            close: vi.fn(async () => {}),
            createMediaElementSource: () => ({ connect() {} }),
            createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }),
            createAnalyser: () => ({ fftSize: 32, connect() {}, disconnect() {}, getFloatTimeDomainData() {} }),
        };
        const factory = vi.fn(() => ctx);
        const c = await openForEdit(fakeClient(base()), { createAudioContext: factory });
        const audio = new FakeAudio();
        c.bindAudio(audio);
        expect(factory).toHaveBeenCalledTimes(1);
        expect(c.gainState.value.value.available).toBe(true);
        audio.dispatchEvent(new Event('play'));
        expect(ctx.resume).toHaveBeenCalled();
        c.dispose();
        expect(ctx.close).toHaveBeenCalled();
    });
});

describe('dispose on navigation and dictation switch', () => {
    it('releases the held lease, drops listeners, clears clinical data and never clears the shared client context', async () => {
        const client = fakeClient(base());
        const c = await openForEdit(client);
        const audio = new FakeAudio();
        c.bindAudio(audio);
        c.setText('Vertraulich');
        c.dispose();
        expect(count(client, 'POST', '/leases/release')).toBe(1);
        expect(c.ready.value).toBe(false);
        expect(c.editor.text.value).toBe('');
        expect(c.audioEl.value).toBeNull();
        audio.currentTime = 1.2;
        audio.dispatchEvent(new Event('timeupdate'));
        expect(c.alignment.currentWord.value).toBeNull();
        expect(client.switchContext).not.toHaveBeenCalledWith({ accountId: null, dictationId: null });
        expect((await c.load()).code).toBe('cancelled');
        expect(c.setText('mehr')).toBe(false);
        c.dispose(); // idempotent
        expect(count(client, 'POST', '/leases/release')).toBe(1);
    });

    it('a view that never held the lease sends no release', async () => {
        const client = fakeClient(base());
        const c = controllerFor(client);
        await c.start();
        c.dispose();
        expect(count(client, 'POST', '/leases/release')).toBe(0);
    });

    it('switching to another dictation disposes the first controller and scopes the next to its id', async () => {
        const client = fakeClient({
            ...base(),
            'GET /dictations/d2/correction': ok(correction({ dictation_id: 'd2', text: 'Zwei', text_revision_id: 'q1', links: [] })),
            'GET /dictations/d2/audio-revisions/current': ok({ revision_id: 'b1', segments: [] }),
        });
        const first = await openForEdit(client);
        first.dispose();
        const second = createCorrectionViewController({ client, dictationId: 'd2', currentUser: user, autosaveMs: 0 });
        await second.start();
        expect(second.editor.text.value).toBe('Zwei');
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: 7, dictationId: 'd2' });
        expect(first.editor.text.value).toBe('');
        second.dispose();
    });

    it('scopedClient forwards requests and only real context switches', async () => {
        const raw = fakeClient(base());
        const scoped = scopedClient(raw);
        await scoped.request('/x', { method: 'GET' });
        expect(raw.request).toHaveBeenCalledWith('/x', { method: 'GET' });
        scoped.switchContext({ accountId: null, dictationId: null });
        expect(raw.switchContext).not.toHaveBeenCalled();
        scoped.switchContext({ accountId: 1, dictationId: 'd1' });
        expect(raw.switchContext).toHaveBeenCalledWith({ accountId: 1, dictationId: 'd1' });
        expect(() => scopedClient(null)).toThrow(/client/);
    });
});
