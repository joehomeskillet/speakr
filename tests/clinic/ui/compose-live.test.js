import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    ANNOUNCEMENTS,
    LiveView,
    PHASES,
    createLiveViewController,
    liveViewActions,
    registerLiveView,
} from '../../../static/js/clinic/compose-live.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-live.js'), 'utf8');

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status = 0) => ({ outcome, status, code: null, message: 'Fehler' });
const position = (extra = {}) => ({ session_id: 's1', dictation_id: 'd1', state: 'PAUSED', generation: 1, sequence: 0, audio_offset_ms: 0, events: [], ...extra });
const committed = () => ok({ status: 'committed', revision: 1 });

let keyN = 0;
const newKey = () => 'key-' + String(++keyN).padStart(6, '0');

/** Fake client. routes: "METHOD suffix" -> result or function. */
function fakeClient(routes = {}) {
    const calls = [];
    const find = (method, path) => Object.keys(routes).find((k) => {
        const [m, p] = k.split(' ');
        return m === method && path.endsWith(p);
    });
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ kind: 'request', method, path, options });
            const key = find(method, path);
            if (!key) return ok({});
            const hit = routes[key];
            return typeof hit === 'function' ? hit(path, options) : hit;
        }),
        command: vi.fn(async (body, options) => {
            calls.push({ kind: 'command', body, options });
            const hit = routes['CMD ' + body.command];
            return typeof hit === 'function' ? hit(body) : (hit || committed());
        }),
        switchContext: vi.fn(),
    };
}

const baseRoutes = (extra = {}) => ({
    'POST /dictations/d1/resume': ok(position()),
    'POST /sessions/s1/pause': ok(position()),
    'POST /sessions/s1/continue': ok(position({ state: 'RECORDING' })),
    'POST /sessions/s1/phrases': (path, options) => ok({
        status: 'server_saved', phrase_id: 'p' + options.body.sequence, session_id: 's1',
        generation: options.body.generation, sequence: options.body.sequence, event_id: options.body.event_id,
    }),
    ...extra,
});

const make = (client, extra = {}) => createLiveViewController({ client, dictationId: 'd1', newKey, ...extra });
const wire = { audio_start_ms: 0, audio_end_ms: 500, audio_b64: 'AAAA', audio_revision_id: 'a1', transcript_revision_id: 'r1', text_hash: 'h' };

async function live(client, extra) {
    const c = make(client, extra);
    await c.start();
    if (c.getState().phase === 'paused') await c.continueRecording();
    return c;
}

describe('compose-live contract', () => {
    it('exports the view and registers it as clinic-live-view', () => {
        const app = { component: vi.fn() };
        expect(registerLiveView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-live-view', LiveView);
        expect(Object.keys(LiveView.props)).toEqual(expect.arrayContaining(['client', 'dictationId', 'vue']));
    });

    it('reuses the live-session block, tokens and api-client', () => {
        expect(source).toContain("from './live-session-ui.js'");
        expect(source).toContain("from './tokens.js'");
        expect(source).toContain("from './api-client.js'");
        const imports = [...source.matchAll(/from '(\.\/[^']+)'/g)].map((m) => m[1]);
        expect(imports).toHaveLength(3);
    });

    it('keeps nothing in browser storage and cites the server-side draft policy', () => {
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie|caches\./);
        expect(source).toContain('src/clinic/live_drafts.py');
    });

    it('has one status live region for phases and none for phrases', () => {
        const template = LiveView.components.LiveBody.template;
        expect(template.match(/aria-live="polite"/g)).toHaveLength(1);
        expect(template).toContain('aria-live="off"');
        expect(template).toContain('role="group"');
        expect(template).toContain('aria-labelledby="clv-title"');
    });

    it('rejects a missing dictation id and a missing client', () => {
        expect(() => createLiveViewController({ client: fakeClient() })).toThrow('dictationId');
        expect(() => createLiveViewController({ client: {}, dictationId: 'd1' })).toThrow('client');
    });
});

describe('start', () => {
    it('sends start_live_session and stays paused until continue reports recording', async () => {
        const client = fakeClient(baseRoutes());
        const c = make(client);
        expect(c.getState().phase).toBe('idle');
        const state = await c.start();
        const cmd = client.calls.find((x) => x.kind === 'command');
        expect(cmd.body).toMatchObject({ command: 'start_live_session', payload: { dictation_id: 'd1' } });
        expect(cmd.options.idempotencyKey).toMatch(/^key-/);
        expect(state.phase).toBe('paused');
        expect(state.session.status).toBe('paused');
        expect(state.session.writable).toBe(false);
        expect(state.session.sessionId).toBe('s1');
        expect(state.session.revision).toBe(1);
        expect(state.actions.map((a) => a.id)).toEqual(['continueRecording', 'stop']);
        expect(client.calls.some((x) => x.path && x.path.endsWith('/continue'))).toBe(false);
        expect((await c.continueRecording()).phase).toBe('live');
    });

    it('goes through starting and does not start twice', async () => {
        const client = fakeClient(baseRoutes());
        const c = make(client);
        const seen = [];
        c.subscribe((s) => seen.push(s.phase));
        await c.start();
        await c.start();
        expect(seen).toContain('starting');
        expect(client.calls.filter((x) => x.kind === 'command')).toHaveLength(1);
    });

    it('adopts an already open session after a conflict without recording', async () => {
        const c = make(fakeClient(baseRoutes({ 'CMD start_live_session': fail('conflict', 409) })));
        const state = await c.start();
        expect(state.phase).toBe('paused');
        expect(state.session.sessionId).toBe('s1');
    });

    it('ends in error with a German note when the start is denied', async () => {
        const c = await live(fakeClient(baseRoutes({ 'CMD start_live_session': fail('permission', 403) })));
        expect(c.getState().phase).toBe('error');
        expect(c.getState().note).toBe('Keine Berechtigung für diese Live-Sitzung.');
        expect(c.getState().actions.map((a) => a.id)).toEqual(['start']);
    });

    it('ends in reconnecting when the resume after start has no network', async () => {
        const c = await live(fakeClient(baseRoutes({ 'POST /dictations/d1/resume': fail('network') })));
        expect(c.getState().phase).toBe('reconnecting');
    });
});

describe('pause and continue', () => {
    it('pauses, blocks new phrases and continues', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        c.partial('Hallo');
        expect((await c.pause()).phase).toBe('paused');
        const pause = client.calls.find((x) => x.path && x.path.endsWith('/sessions/s1/pause'));
        expect(pause.method).toBe('POST');
        expect(c.getState().session.partial).toBeNull();
        expect(await c.finalizePhrase('nicht erlaubt', wire)).toBeNull();
        expect(c.getState().actions.map((a) => a.id)).toEqual(['continueRecording', 'stop']);
        expect((await c.continueRecording()).phase).toBe('live');
        expect(client.calls.some((x) => x.path && x.path.endsWith('/sessions/s1/continue'))).toBe(true);
        expect(await c.finalizePhrase('erlaubt', wire)).not.toBeNull();
    });

    it('turns a denied pause into the stale state', async () => {
        const c = await live(fakeClient(baseRoutes({ 'POST /sessions/s1/pause': fail('permission', 403) })));
        expect((await c.pause()).phase).toBe('stale');
        expect(c.getState().note).toBe('Keine Berechtigung für diese Live-Sitzung.');
    });

    it('does not present recording before continue succeeds', async () => {
        let release;
        const gate = new Promise((resolve) => { release = resolve; });
        const client = fakeClient(baseRoutes({ 'POST /sessions/s1/continue': () => gate }));
        const c = make(client);
        await c.start();
        const pending = c.continueRecording();
        expect(c.getState().phase).toBe('paused');
        expect(c.getState().session.status).toBe('paused');
        release(ok(position({ state: 'RECORDING' })));
        const state = await pending;
        expect(state.phase).toBe('live');
        expect(state.session.status).toBe('live');
    });

    it('stays paused when continue returns the paused recovery state', async () => {
        const c = make(fakeClient(baseRoutes({
            'POST /sessions/s1/continue': ok(position({ state: 'PAUSED' })),
        })));
        await c.start();
        const state = await c.continueRecording();
        expect(state.phase).toBe('paused');
        expect(state.session.writable).toBe(false);
        expect(await c.finalizePhrase('nicht erlaubt', wire)).toBeNull();
    });

    it('stays live when the server is still recording after pause', async () => {
        const c = await live(fakeClient(baseRoutes({
            'POST /sessions/s1/pause': ok(position({ state: 'RECORDING' })),
        })));
        expect((await c.pause()).phase).toBe('live');
        expect(c.getState().session.status).toBe('live');
    });

    it('ignores continue while live', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        const before = client.calls.filter((x) => x.path && x.path.endsWith('/continue')).length;
        await c.continueRecording();
        expect(client.calls.filter((x) => x.path && x.path.endsWith('/continue'))).toHaveLength(before);
    });
});

describe('phrases', () => {
    it('shows a phrase as saved only after the matching receipt', async () => {
        const c = await live(fakeClient(baseRoutes()));
        await c.finalizePhrase('Guten Tag', wire);
        const [p] = c.getState().session.phrases;
        expect(p.state).toBe('server_saved');
        expect(p.view.label).toBe('Gespeichert');
    });

    it('keeps an unsaved phrase and goes to reconnecting when the save has no network', async () => {
        const client = fakeClient(baseRoutes({ 'POST /sessions/s1/phrases': fail('network') }));
        const c = await live(client);
        await c.finalizePhrase('Guten Tag', wire);
        const state = c.getState();
        expect(state.phase).toBe('reconnecting');
        expect(state.session.phrases[0].state).toBe('final_unsaved');
        expect(state.session.hasUnsaved).toBe(true);
    });
});

describe('restore', () => {
    const cursor = { session_id: 's1', after_sequence: 1 };
    const event = (sequence, event_id) => ({ sequence, event_id, generation: 1, text: 'Text ' + sequence,
        phrase_id: 'p' + sequence, audio_url: '/original-' + sequence });

    it('does not publish a partial recovery, then retries from the beginning and retains the stop checkpoint', async () => {
        let broken = true;
        const client = fakeClient(baseRoutes({
            'POST /dictations/d1/resume': (_path, { body }) => body.cursor
                ? (broken ? ok(position({ sequence: 2, next_cursor: cursor }))
                    : ok(position({ sequence: 2, read_only: true, events: [event(2, 'second')], next_cursor: null })))
                : ok(position({ sequence: 2, events: [event(1, 'first')], next_cursor: cursor })),
        }));
        const c = make(client);
        expect((await c.restore()).phase).toBe('reconnecting');
        expect(c.getState().session.phrases).toEqual([]);
        expect(c.getState().actions.map((action) => action.id)).toEqual(['reconnect']);
        broken = false;
        const state = await c.reconnect();
        expect(client.command).not.toHaveBeenCalled();
        expect(state.phase).toBe('paused');
        expect(state.session.phrases.map((p) => [p.text, p.audioUrl])).toEqual([
            ['Text 1', '/original-1'], ['Text 2', '/original-2'],
        ]);
        await c.stop();
        expect(client.command.mock.calls[0][0]).toMatchObject({ expected_revision: 3, payload: { last_sequence: 2 } });
    });

    it('does not allow start or concurrent recovery, and ignores a response after dispose', async () => {
        let finish;
        const pending = new Promise((resolve) => { finish = resolve; });
        const client = fakeClient(baseRoutes({ 'POST /dictations/d1/resume': () => pending }));
        const c = make(client);
        const restoring = c.restore();
        await c.restore();
        await c.reconnect();
        await c.start();
        expect(client.request).toHaveBeenCalledTimes(1);
        expect(client.command).not.toHaveBeenCalled();
        c.dispose();
        finish(ok(position({ sequence: 1, events: [event(1, 'first')] })));
        expect((await restoring).session.phrases).toEqual([]);
    });
});

describe('reconnect', () => {
    it('resumes from the durable checkpoint, adopts server phrases and returns to live', async () => {
        let phraseOk = false;
        const client = fakeClient(baseRoutes({
            'POST /sessions/s1/phrases': (path, options) => (phraseOk
                ? ok({ status: 'server_saved', phrase_id: 'p1', session_id: 's1', generation: 1, sequence: 1, event_id: options.body.event_id })
                : fail('network')),
            'POST /dictations/d1/resume': ok(position()),
        }));
        const c = await live(client);
        await c.finalizePhrase('Guten Tag', wire);
        expect(c.getState().phase).toBe('reconnecting');
        phraseOk = true;
        const state = await c.reconnect();
        const resumes = client.calls.filter((x) => x.path && x.path.endsWith('/dictations/d1/resume'));
        expect(resumes.at(-1).options.body).toEqual({ after_sequence: 0 });
        expect(state.phase).toBe('paused');
        expect(state.session.status).toBe('paused');
        expect(state.session.phrases[0].state).toBe('server_saved');
        expect(state.session.phrases[0].text).toBe('Guten Tag');
    });

    it('markDisconnected moves live and paused to reconnecting and offers the reconnect button', async () => {
        const c = await live(fakeClient(baseRoutes()));
        c.markDisconnected();
        expect(c.getState().phase).toBe('reconnecting');
        expect(c.getState().actions.map((a) => a.id)).toContain('reconnect');
    });

    it('stays reconnecting while the network is down', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        c.markDisconnected();
        client.request.mockImplementation(async () => fail('network'));
        expect((await c.reconnect()).phase).toBe('reconnecting');
    });

    it('goes stale when the resume reports a lost holder role', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        c.markDisconnected();
        client.request.mockImplementation(async () => fail('conflict', 409));
        expect((await c.reconnect()).phase).toBe('stale');
        expect(c.getState().session.writable).toBe(false);
    });
});

describe('stop', () => {
    it('sends stop_live_session with generation and the durable checkpoint', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        await c.finalizePhrase('Guten Tag', wire);
        const state = await c.stop();
        const cmd = client.calls.filter((x) => x.kind === 'command').at(-1);
        expect(cmd.body).toMatchObject({
            command: 'stop_live_session', expected_revision: 2,
            payload: { session_id: 's1', generation: 1, last_sequence: 1 },
        });
        expect(cmd.body.expected_revision).toBeGreaterThan(0);
        expect(state.phase).toBe('stopped');
        expect(state.actions.map((a) => a.id)).toEqual(['start']);
    });

    it('stops from the paused state with the current positive revision', async () => {
        const client = fakeClient(baseRoutes());
        const c = await live(client);
        await c.pause();
        const state = await c.stop();
        expect(state.phase).toBe('stopped');
        const cmd = client.calls.filter((x) => x.kind === 'command').at(-1);
        expect(cmd.body.expected_revision).toBe(1);
        expect(cmd.body.payload).toMatchObject({ session_id: 's1', generation: 1, last_sequence: 0 });
    });

    it('refuses to stop while a phrase cannot be saved', async () => {
        const client = fakeClient(baseRoutes({ 'POST /sessions/s1/phrases': fail('validation', 400) }));
        const c = await live(client);
        await c.finalizePhrase('Guten Tag', wire);
        const state = await c.stop();
        expect(client.calls.some((x) => x.kind === 'command' && x.body.command === 'stop_live_session')).toBe(false);
        expect(state.phase).toBe('live');
        expect(state.note).toBe('Beenden nicht möglich: Es gibt nicht gespeicherte Sätze.');
    });

    it('returns to the previous phase when the stop conflicts and keeps the text', async () => {
        const c = await live(fakeClient(baseRoutes({ 'CMD stop_live_session': fail('conflict', 409) })));
        await c.finalizePhrase('Guten Tag', wire);
        const state = await c.stop();
        expect(state.phase).toBe('live');
        expect(state.note).toBe('Beenden fehlgeschlagen. Bitte erneut versuchen.');
        expect(state.session.phrases.map((p) => p.text)).toEqual(['Guten Tag']);
        expect(state.session.phrases[0].state).toBe('server_saved');
    });

    it('keeps unsaved text when a conflict blocks the stop', async () => {
        const client = fakeClient(baseRoutes({ 'POST /sessions/s1/phrases': fail('conflict', 409) }));
        const c = await live(client);
        await c.finalizePhrase('Nicht verlieren', wire);
        const state = await c.stop();
        expect(client.calls.some((x) => x.kind === 'command' && x.body.command === 'stop_live_session')).toBe(false);
        expect(state.phase).toBe('stale');
        expect(state.session.phrases.map((p) => p.text)).toEqual(['Nicht verlieren']);
    });
});

describe('live region', () => {
    it('announces phase changes only, never words, partials or phrases', async () => {
        const client = fakeClient(baseRoutes());
        const c = make(client);
        const announced = [];
        let last = c.getState().announcement;
        c.subscribe((s) => { if (s.announcement !== last) { last = s.announcement; announced.push(s.announcement); } });
        await c.start();
        await c.continueRecording();
        c.partial('Das');
        c.partial('Das ist');
        c.partial('Das ist ein');
        await c.finalizePhrase('Das ist ein Satz', wire);
        await c.finalizePhrase('Noch ein Satz', wire);
        expect(announced).toEqual([ANNOUNCEMENTS.starting, ANNOUNCEMENTS.paused, ANNOUNCEMENTS.live]);
        await c.pause();
        await c.continueRecording();
        c.markDisconnected();
        await c.reconnect();
        await c.stop();
        expect(announced).toEqual([
            ANNOUNCEMENTS.starting, ANNOUNCEMENTS.paused, ANNOUNCEMENTS.live, ANNOUNCEMENTS.paused, ANNOUNCEMENTS.live,
            ANNOUNCEMENTS.reconnecting, ANNOUNCEMENTS.paused, ANNOUNCEMENTS.stopping, ANNOUNCEMENTS.stopped,
        ]);
        for (const text of announced) expect(text).not.toMatch(/Satz|Das ist/);
    });

    it('has a German announcement for every phase and no duplicates', () => {
        for (const p of PHASES) expect(ANNOUNCEMENTS[p]).toBeTruthy();
        expect(new Set(Object.values(ANNOUNCEMENTS)).size).toBe(PHASES.length);
    });
});

describe('memory only', () => {
    it('dispose clears phrases and stops notifying', async () => {
        const c = await live(fakeClient(baseRoutes()));
        await c.finalizePhrase('Guten Tag', wire);
        const fn = vi.fn();
        c.subscribe(fn);
        c.dispose();
        expect(c.getState().session.phrases).toEqual([]);
        expect(c.getState().session.sessionId).toBeNull();
        expect(fn).not.toHaveBeenCalled();
    });

    it('liveViewActions offers the save button for unsaved phrases', () => {
        const session = { status: 'live', hasUnsaved: true, writable: true };
        expect(liveViewActions({ phase: 'live', session }).map((a) => a.id)).toEqual(['pause', 'stop', 'saveUnsaved']);
    });
});
