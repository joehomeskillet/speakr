import { acquireMicrophoneStream } from '../modules/composables/audio.js';
import { getUploadCsrfToken, getCsrfTokenFromMeta } from '../modules/csrf.js';

export const LIVE = '/api/clinic/v1/live';
export const sessionPath = (id) => `${LIVE}/sessions/${encodeURIComponent(id)}`;
const SAMPLE_RATE = 24000;
const FRAME_BYTES = 960;
const MIN_FRAMES = 5;
const MAX_FRAMES = 500;
const QUIET_FRAMES = 33;
const SPEECH_PEAK = 0.015;
const PROTOCOL = 'clinic-live-v1';

export function releaseLiveHolder(position, fence) {
    const token = getCsrfTokenFromMeta();
    if (!token) return;
    void fetch(sessionPath(position.sessionId) + '/pause', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
        body: JSON.stringify({ ...fence, release: true }),
    }).catch(() => {});
}

export function createLiveCapture({ position, clientId, onPartial, onSaved, onFailure, canWrite, renew }) {
    let context, stream, source, node, turn;
    let closed = false;
    let draining = false;
    let queue = [];
    let lead = [];
    let frames = 0;
    let quiet = 0;
    let opening;
    let readyWait;
    let started = false;

    function silenceInput() {
        if (node) node.port.onmessage = null;
        source?.disconnect();
        node?.disconnect();
        for (const track of stream?.getTracks() || []) {
            track.onended = null;
            track.stop();
        }
        if (context && context.state !== 'closed') void context.close().catch(() => {});
        context = stream = source = node = null;
    }

    function endTurn(current, saved) {
        if (turn !== current) return;
        turn = null;
        clearTimeout(current.timer);
        current.socket.onopen = current.socket.onmessage = current.socket.onerror = current.socket.onclose = null;
        current.socket.close();
        current.done(saved);
        frames = quiet = 0;
        lead = [];
    }

    function close() {
        closed = true;
        silenceInput();
        queue = lead = [];
        if (turn) endTurn(turn, false);
        readyWait?.(false);
    }

    function fail(code) {
        if (closed) return;
        close();
        onFailure(code);
    }

    async function prepare() {
        if (!globalThis.AudioContext || !globalThis.AudioWorkletNode || !navigator.mediaDevices?.getUserMedia) {
            throw new Error('capture_unavailable');
        }
        context = new AudioContext({ sampleRate: SAMPLE_RATE });
        const resumed = context.resume();
        void resumed.catch(() => {});
        const { stream: input } = await acquireMicrophoneStream(navigator.mediaDevices);
        if (closed) {
            input.getTracks().forEach((track) => track.stop());
            return false;
        }
        stream = input;
        for (const track of stream.getTracks()) track.onended = () => fail('microphone');
        const audio = context;
        await Promise.all([resumed, audio.audioWorklet.addModule(new URL('./live-capture-worklet.js', import.meta.url))]);
        if (closed) return false;
        if (audio.sampleRate !== SAMPLE_RATE) throw new Error('capture_unavailable');
        source = audio.createMediaStreamSource(stream);
        node = new AudioWorkletNode(audio, 'clinic-live-pcm', { channelCount: 1, channelCountMode: 'explicit', numberOfOutputs: 0 });
        node.port.onmessage = ({ data }) => {
            if (!started || draining || closed) return;
            if (!canWrite()) return fail('POLICY_DENIED');
            if (queue.length >= MAX_FRAMES) return fail('network');
            queue.push(data);
            flush();
        };
        return true;
    }

    function finish(current) {
        while (frames < MIN_FRAMES) {
            current.socket.send(new ArrayBuffer(FRAME_BYTES));
            frames += 1;
        }
        current.finishing = true;
        clearTimeout(current.timer);
        current.timer = setTimeout(() => fail('network'), 15000);
        current.socket.send('finish');
    }

    function flush() {
        const current = turn;
        if (!current?.ready || current.finishing) return;
        try {
            while (queue.length && !current.finishing) {
                const { pcm, peak } = queue.shift();
                const voice = peak >= SPEECH_PEAK;
                if (!frames && !voice) {
                    lead.push(pcm);
                    if (lead.length > MIN_FRAMES) lead.shift();
                    continue;
                }
                if (current.socket.bufferedAmount > 48000) return fail('network');
                for (const frame of lead) current.socket.send(frame);
                frames += lead.length + 1;
                lead = [];
                current.socket.send(pcm);
                quiet = voice ? 0 : quiet + 1;
                if (frames >= MAX_FRAMES || quiet >= QUIET_FRAMES) finish(current);
            }
            if (draining && !current.finishing) {
                if (frames) finish(current);
                else endTurn(current, true);
            }
        } catch {
            fail('network');
        }
    }

    async function openTurn() {
        let token;
        try { token = await getUploadCsrfToken(); }
        catch { fail('network'); return false; }
        if (closed) return false;
        if (!token) { fail('network'); return false; }
        const { sessionId, generation, checkpoint } = position();
        const url = new URL(sessionPath(sessionId) + '/stream', location.href);
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
        let socket;
        try { socket = new WebSocket(url, [PROTOCOL, token]); }
        catch { fail('network'); return false; }
        let done;
        const complete = new Promise((resolve) => { done = resolve; });
        const current = turn = { socket, done, complete, ready: false, finishing: false };
        current.timer = setTimeout(() => fail('network'), 10000);
        socket.onopen = () => {
            if (socket.protocol !== PROTOCOL || !canWrite()) return fail('POLICY_DENIED');
            try { socket.send(JSON.stringify({ client_id: clientId, generation, after_sequence: checkpoint })); }
            catch { fail('network'); }
        };
        socket.onerror = socket.onclose = () => fail('network');
        socket.onmessage = ({ data }) => {
            if (turn !== current || closed) return;
            if (!canWrite()) return fail('POLICY_DENIED');
            let event;
            try { event = JSON.parse(data); }
            catch { return fail('network'); }
            if (!event || event.session_id !== sessionId) return;
            if (event.status === 'error' && event.generation === generation && event.sequence === checkpoint + 1) return fail(event.code);
            if (event.generation !== generation || event.sequence !== checkpoint + 1) return;
            if (!current.ready && event.status === 'ready') {
                if (event.sample_rate !== SAMPLE_RATE || event.max_frame_bytes < FRAME_BYTES
                    || event.max_audio_seconds < MAX_FRAMES / 50) return fail('network');
                current.ready = true;
                clearTimeout(current.timer);
                current.timer = setTimeout(() => {
                    if (frames) { try { finish(current); } catch { fail('network'); } return; }
                    endTurn(current, true);
                    opening = (async () => {
                        try {
                            if (!await renew()) return fail('network');
                            if (!closed && (!draining || queue.length)) await openTurn();
                        } catch { fail('network'); }
                    })();
                }, 20000);
                readyWait?.(true);
                flush();
            } else if (current.ready && event.status === 'partial' && typeof event.text === 'string') {
                if (!draining) onPartial(event.text);
            } else if (current.finishing && event.status === 'server_saved') {
                if (!onSaved(event)) return fail('network');
                endTurn(current, true);
                if (!draining || queue.length) opening = openTurn();
            }
        };
        return true;
    }

    return {
        prepare,
        async start() {
            const ready = new Promise((resolve) => { readyWait = resolve; });
            opening = openTurn();
            if (!await ready || closed) return false;
            readyWait = null;
            try { source.connect(node); }
            catch { fail('microphone'); return false; }
            started = true;
            return true;
        },
        async drain() {
            draining = true;
            silenceInput();
            while (!closed) {
                await opening;
                if (!turn) break;
                const current = turn;
                flush();
                if (!await current.complete) return false;
            }
            if (closed) return false;
            close();
            return true;
        },
        close,
    };
}
