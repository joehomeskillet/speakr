import { afterEach, describe, expect, it, vi } from 'vitest';
import { createLiveViewController } from '../../../static/js/clinic/compose-live.js';
import * as captureModule from '../../../static/js/clinic/live-capture-ui.js';

const position = (state, sequence = 0) => ({ state, session_id: 's1', generation: 1, sequence });
const event = (status, sequence = 1, extra = {}) => ({ status, session_id: 's1', generation: 1, sequence, ...extra });
const ok = (data) => ({ outcome: 'ok', data });

function harness() {
    const sockets = [];
    const track = { stop: vi.fn() };
    const stream = { getTracks: () => [track] };
    const microphone = vi.fn(async () => stream);
    const source = { connect: vi.fn(), disconnect: vi.fn() };
    let port;
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: microphone } });
    vi.stubGlobal('location', { href: 'https://example.test/#/live' });
    vi.stubGlobal('document', { querySelector: () => ({ getAttribute: () => 'token', setAttribute() {} }) });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ csrf_token: 'token' }) })));
    vi.stubGlobal('AudioContext', class {
        state = 'running';
        sampleRate = 24000;
        audioWorklet = { addModule: async () => {} };
        resume = async () => {};
        close = async () => {};
        createMediaStreamSource = () => source;
    });
    vi.stubGlobal('AudioWorkletNode', class {
        constructor() { this.port = port = {}; }
        disconnect() {}
    });
    vi.stubGlobal('WebSocket', class {
        protocol = 'clinic-live-v1';
        bufferedAmount = 0;
        send = vi.fn();
        close = vi.fn();
        constructor(url, protocols) {
            this.url = url.href;
            this.protocols = protocols;
            sockets.push(this);
        }
        receive(payload) { this.onmessage?.({ data: JSON.stringify(payload) }); }
    });
    let sequence = 0;
    let key = 0;
    const command = vi.fn(async () => ok({ revision: 1 }));
    const request = vi.fn(async (path) => ok(position(path.endsWith('/continue') ? 'RECORDING' : 'PAUSED', sequence)));
    const c = createLiveViewController({ client: { request, command }, dictationId: 'd1', captureModule, newKey: () => `key-${++key}` });
    const ready = async (index) => {
        await vi.waitFor(() => expect(sockets[index]).toBeDefined());
        const socket = sockets[index];
        socket.onopen();
        socket.receive(event('ready', index + 1, { sample_rate: 24000, max_frame_bytes: 4800, max_audio_seconds: 30 }));
        return socket;
    };
    return {
        c, command, request, track, microphone, sockets, ready,
        frame(peak) { port.onmessage?.({ data: { pcm: new ArrayBuffer(960), peak } }); },
        save(socket, n) {
            sequence = n;
            socket.receive(event('server_saved', n, { text: `Testsatz ${n}`, event_id: `ev${n}`, phrase_id: `p${n}` }));
        },
    };
}

afterEach(() => vi.unstubAllGlobals());

describe('browser live capture', () => {
    it('prompts once, gates audio on ready, displays partials and durable finals before stop, and drains to the stop revision', async () => {
        const h = harness();
        const start = h.c.start();
        await h.c.start();
        expect(h.microphone).toHaveBeenCalledTimes(1);
        await vi.waitFor(() => expect(h.sockets).toHaveLength(1));
        expect(h.c.getState().phase).not.toBe('live');
        const first = await h.ready(0);
        await start;
        expect(h.command.mock.calls[0][0].payload).toMatchObject({ dictation_id: 'd1', capture_mime: 'audio/pcm' });
        expect(h.command.mock.calls[0][1].liveClientId).toBe('key-1');
        expect(h.request.mock.calls[0][1].body).toEqual({ after_sequence: 0, client_id: 'key-1', generation: 1 });
        expect(first.url).toBe('wss://example.test/api/clinic/v1/live/sessions/s1/stream');
        expect(first.protocols).toEqual(['clinic-live-v1', 'token']);
        expect(JSON.parse(first.send.mock.calls[0][0])).toEqual({ client_id: 'key-1', generation: 1, after_sequence: 0 });
        expect(h.c.getState().phase).toBe('live');
        h.frame(0.1);
        first.receive(event('partial', 1, { text: 'Vorher' }));
        first.receive(event('partial', 1, { text: 'Vorläufig' }));
        first.receive(event('partial', 1, { text: 'Fremd', generation: 2 }));
        expect(h.c.getState().session.partial).toBe('Vorläufig');
        for (let i = 0; i < 33; i += 1) h.frame(0);
        expect(first.send.mock.lastCall[0]).toBe('finish');
        h.save(first, 1);
        expect(h.c.getState().session).toMatchObject({ partial: null, checkpoint: 1, phrases: [{ text: 'Testsatz 1', state: 'server_saved' }] });
        const second = await h.ready(1);
        first.receive(event('partial', 1, { text: 'Verspätet' }));
        h.frame(0.1);
        const stop = h.c.stop();
        expect(h.track.stop).toHaveBeenCalledOnce();
        await vi.waitFor(() => expect(second.send.mock.lastCall[0]).toBe('finish'));
        expect(h.command).toHaveBeenCalledTimes(1);
        h.save(second, 2);
        expect((await stop).phase).toBe('stopped');
        expect(h.command.mock.lastCall[0]).toMatchObject({ expected_revision: 3, payload: { last_sequence: 2 } });
        expect(h.request.mock.calls.some(([path]) => path.endsWith('/phrases'))).toBe(false);
        expect(first.close).toHaveBeenCalledOnce();
        expect(second.close).toHaveBeenCalledOnce();
        h.c.dispose();
    });

    it('pauses, requests fresh capture on continue, and closes and releases on leaving', async () => {
        const h = harness();
        const start = h.c.start();
        const first = await h.ready(0);
        await start;
        expect((await h.c.pause()).phase).toBe('paused');
        expect(first.close).toHaveBeenCalledOnce();
        const resume = h.c.continueRecording();
        await vi.waitFor(() => expect(h.sockets).toHaveLength(2));
        const next = h.sockets[1];
        next.onopen();
        next.receive(event('ready', 1, { sample_rate: 24000, max_frame_bytes: 4800, max_audio_seconds: 30 }));
        expect((await resume).phase).toBe('live');
        h.c.dispose();
        expect(h.track.stop).toHaveBeenCalledTimes(2);
        expect(next.close).toHaveBeenCalledOnce();
        expect(h.c.getState().session.sessionId).toBeNull();
        expect(fetch.mock.lastCall).toMatchObject(['/api/clinic/v1/live/sessions/s1/pause', { keepalive: true }]);
        expect(JSON.parse(fetch.mock.lastCall[1].body)).toEqual({ client_id: 'key-1', generation: 1, release: true });
    });

    it.each(['NotAllowedError', 'NotFoundError'])('does not start a server session on %s', async (name) => {
        const h = harness();
        h.microphone.mockRejectedValueOnce(Object.assign(new Error(), { name }));
        expect((await h.c.start()).phase).toBe('error');
        expect(h.command).not.toHaveBeenCalled();
        expect(h.sockets).toHaveLength(0);
        h.c.dispose();
    });

    it('stops a late microphone stream after disposal', async () => {
        const h = harness();
        let release;
        h.microphone.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
        const start = h.c.start();
        h.c.dispose();
        release({ getTracks: () => [h.track] });
        await start;
        expect(h.track.stop).toHaveBeenCalledOnce();
        expect(h.command).not.toHaveBeenCalled();
    });

    it('does not open transport unless continue explicitly reports recording', async () => {
        const h = harness();
        h.request.mockResolvedValueOnce(ok(position('PAUSED'))).mockResolvedValueOnce(ok({}));
        expect((await h.c.start()).phase).toBe('paused');
        expect(h.track.stop).toHaveBeenCalledOnce();
        expect(h.sockets).toHaveLength(0);
        h.c.dispose();
    });

    it('recovers the text of a receipt lost on the wire without reopening the microphone', async () => {
        const h = harness();
        const start = h.c.start();
        const socket = await h.ready(0);
        await start;
        socket.receive(event('partial', 1, { text: 'Vorläufig' }));
        socket.onclose();
        expect(h.c.getState()).toMatchObject({ phase: 'reconnecting', session: { partial: null, checkpoint: 0 } });
        h.request.mockResolvedValueOnce(ok({ ...position('PAUSED', 1), events: [
            event('server_saved', 1, { text: 'Gesicherter Text', event_id: 'e1', phrase_id: 'p1' }),
        ] }));
        expect(await h.c.reconnect()).toMatchObject({ phase: 'paused', session: { checkpoint: 1,
            phrases: [{ text: 'Gesicherter Text', state: 'server_saved' }] } });
        expect(h.track.stop).toHaveBeenCalledOnce();
        expect(h.microphone).toHaveBeenCalledOnce();
        h.c.dispose();
    });

    it('stops on holder loss without accepting late events', async () => {
        const h = harness();
        const start = h.c.start();
        const socket = await h.ready(0);
        await start;
        socket.receive(event('partial', 1, { text: 'Vorläufig' }));
        socket.receive(event('error', 1, { code: 'POLICY_DENIED' }));
        expect(h.c.getState()).toMatchObject({ phase: 'stale', session: { partial: null, checkpoint: 0 } });
        expect(h.track.stop).toHaveBeenCalledOnce();
        expect(socket.close).toHaveBeenCalledOnce();
        h.c.dispose();
    });
});
