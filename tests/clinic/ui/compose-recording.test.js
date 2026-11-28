import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    RECORDING_TEMPLATE, RecordingView, TEXT, createRecordingViewController, isAudioFile, receiptDictationId,
    registerRecordingView,
} from '../../../static/js/clinic/compose-recording.js';

function recorder(upload) {
    return {
        startRecording: vi.fn(async () => {}),
        pauseRecording: vi.fn(),
        resumeRecording: vi.fn(),
        stopRecording: vi.fn(async () => {}),
        discardRecording: vi.fn(async () => {}),
        uploadRecordedAudio: vi.fn(upload || (async () => ({ id: 'rec-1' }))),
    };
}

const audio = (name = 'diktat.wav') => ({ name, size: 1200, type: 'audio/wav' });

function make(extra) {
    const rec = (extra && extra.recorder) || recorder();
    const uploadFile = (extra && extra.uploadFile) || vi.fn(async () => ({ dictation_id: 'd-9' }));
    const onOpen = vi.fn();
    const onWorklist = vi.fn();
    const client = { switchContext: vi.fn() };
    const c = createRecordingViewController({
        client, currentUser: { id: 4, display_name: 'Dr. Muster' }, recorder: rec, uploadFile, onOpen, onWorklist,
        ...(extra && extra.options),
    });
    return { c, rec, uploadFile, onOpen, onWorklist, client };
}

async function toReview(c) {
    c.setTitle('Bericht');
    await c.start();
    c.pause();
    c.resume();
    await c.stop();
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-recording.js', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');

afterEach(() => vi.unstubAllGlobals());

describe('recording view controller', () => {
    it('requires a user id, a recorder and an upload function', () => {
        expect(() => createRecordingViewController({ currentUser: {}, recorder: recorder(), uploadFile() {} })).toThrow('currentUser.id');
        expect(() => createRecordingViewController({ currentUser: { id: 1 }, uploadFile() {} })).toThrow('recorder');
        expect(() => createRecordingViewController({ currentUser: { id: 1 }, recorder: recorder() })).toThrow('uploadFile');
    });

    it('uses the logged-in user as author without showing author controls', () => {
        const { c } = make();
        const m = c.model();
        expect(m.author).toBe('Dr. Muster');
        expect(m.authorId).toBe('4');
        expect(Object.keys(c)).not.toContain('setAuthor');
        expect(Object.keys(c)).not.toContain('setPool');
        expect(RECORDING_TEMPLATE).not.toMatch(/<select|author-picker|pool/i);
        expect(RECORDING_TEMPLATE).not.toContain('c.text.author');
        expect(TEXT.author).toBe('Diktiert von');
    });

    it('moves through prepare, record, pause, review, upload', async () => {
        const { c, rec } = make();
        expect(c.model().phase).toBe('title');
        expect(c.model().message).toBe('Ein Titel ist erforderlich.');
        c.setTitle('Bericht');
        expect(c.model().phase).toBe('ready');
        await c.start();
        expect(c.model().phase).toBe('recording');
        expect(c.model().titleLocked).toBe(true);
        c.pause();
        expect(c.model().phase).toBe('paused');
        c.resume();
        expect(c.model().phase).toBe('recording');
        await c.stop();
        expect(c.model().phase).toBe('review');
        expect(c.model().controls.find((b) => b.id === 'send').disabled).toBe(false);
        const out = await c.send();
        expect(out.ok).toBe(true);
        expect(rec.uploadRecordedAudio).toHaveBeenCalledWith({ title: 'Bericht' });
        expect(c.model().phase).toBe('sent');
        expect(c.model().created).toEqual({ dictationId: 'rec-1' });
        expect(c.model().message).toBe(TEXT.sent);
    });

    it('keeps the title editable after stop, blocks empty titles on start/send, and uploads the edited title', async () => {
        let release;
        const rec = recorder(() => new Promise((resolve) => { release = resolve; }));
        const { c } = make({ recorder: rec });

        // Empty or whitespace-only title cannot start recording.
        expect(c.model().titleLocked).toBe(false);
        expect(c.model().controls.find((b) => b.id === 'start').disabled).toBe(true);
        c.setTitle('   ');
        expect(c.model().phase).toBe('title');
        expect((await c.start()).code).toBe('invalid_phase');
        expect(rec.startRecording).not.toHaveBeenCalled();

        // Start with initial title; locked during recording and pause.
        c.setTitle('Erster Titel');
        await c.start();
        expect(c.model().titleLocked).toBe(true);
        expect(c.setTitle('Zwischentitel').code).toBe('invalid_phase');
        c.pause();
        expect(c.model().titleLocked).toBe(true);
        expect(c.setTitle('Zwischentitel').code).toBe('invalid_phase');

        // Stop unlocks the title in review; clearing blocks send until a new title is entered.
        await c.stop();
        expect(c.model().phase).toBe('review');
        expect(c.model().titleLocked).toBe(false);
        expect(c.setTitle('').ok).toBe(true);
        expect(c.model().phase).toBe('review');
        expect(c.model().controls.find((b) => b.id === 'send').disabled).toBe(true);
        expect(c.model().message).toBe('Ein Titel ist erforderlich.');
        expect((await c.send()).code).toBe('missing_title');
        expect(rec.uploadRecordedAudio).not.toHaveBeenCalled();

        // Editing the title in review enables send and sends the trimmed new title.
        expect(c.setTitle('  Geänderter Titel  ').ok).toBe(true);
        expect(c.model().controls.find((b) => b.id === 'send').disabled).toBe(false);
        const pending = c.send();
        expect(c.model().phase).toBe('sending');
        expect(c.model().titleLocked).toBe(true);
        expect(c.setTitle('Zu spät').code).toBe('invalid_phase');
        release({ dictation_id: 'd-42' });
        expect((await pending).ok).toBe(true);
        expect(rec.uploadRecordedAudio).toHaveBeenCalledWith({ title: 'Geänderter Titel' });
        expect(c.model().phase).toBe('sent');
        expect(c.model().titleLocked).toBe(true);
        expect(c.setTitle('Nach Speichern').code).toBe('invalid_phase');
    });

    it('keeps the title editable after an upload failure so the edited title is sent on retry', async () => {
        let calls = 0;
        const rec = recorder(async () => {
            calls += 1;
            if (calls === 1) throw new Error('offline');
            return { dictation_id: 'd-7' };
        });
        const { c } = make({ recorder: rec });
        await toReview(c);
        expect((await c.send()).code).toBe('no_receipt');
        expect(c.model().phase).toBe('failed');
        expect(c.model().titleLocked).toBe(false);
        expect(c.setTitle('Korrigierter Titel').ok).toBe(true);
        expect((await c.send()).ok).toBe(true);
        expect(rec.uploadRecordedAudio).toHaveBeenNthCalledWith(1, { title: 'Bericht' });
        expect(rec.uploadRecordedAudio).toHaveBeenNthCalledWith(2, { title: 'Korrigierter Titel' });
    });

    it('refuses invalid transitions and a second start', async () => {
        const { c, rec } = make();
        expect((await c.start()).code).toBe('invalid_phase');
        c.setTitle('x');
        await c.start();
        expect((await c.start()).code).toBe('invalid_phase');
        expect(rec.startRecording).toHaveBeenCalledOnce();
        expect((await c.send()).code).toBe('invalid_phase');
        expect(c.resume().code).toBe('invalid_phase');
    });

    it('reports an upload failure without claiming the audio is stored locally, then retries', async () => {
        let calls = 0;
        const { c } = make({ recorder: recorder(async () => { calls += 1; if (calls === 1) throw new Error('offline'); return { id: 3 }; }) });
        await toReview(c);
        expect((await c.send()).code).toBe('no_receipt');
        const m = c.model();
        expect(m.phase).toBe('failed');
        expect(m.created).toBeNull();
        expect(m.message).toBe(TEXT.recordFailed);
        expect(m.message).toContain('nicht gesendet');
        expect(m.message).toContain('nicht lokal gespeichert');
        expect(m.controls.find((b) => b.id === 'send').label).toBe('Erneut senden');
        expect((await c.send()).ok).toBe(true);
        expect(c.model().created).toEqual({ dictationId: '3' });
    });

    it('treats an upload without receipt as failed', async () => {
        const { c } = make({ recorder: recorder(async () => ({})) });
        await toReview(c);
        await c.send();
        expect(c.model().phase).toBe('failed');
        expect(c.model().created).toBeNull();
    });

    it('explains a failed start and points to the file upload', async () => {
        const rec = recorder();
        rec.startRecording = vi.fn(async () => { throw new Error('NotAllowedError'); });
        const { c } = make({ recorder: rec });
        c.setTitle('x');
        expect((await c.start()).code).toBe('start_failed');
        expect(c.model().phase).toBe('ready');
        expect(c.model().message).toBe(TEXT.startFailed);
        expect(c.model().message).toContain('Datei hochladen');
    });

    it('offers Zur Arbeitsliste and Öffnen only after success', async () => {
        const { c, onOpen, onWorklist } = make();
        expect(c.openCreated()).toBe(false);
        expect(c.toWorklist()).toBe(false);
        await toReview(c);
        await c.send();
        expect(c.toWorklist()).toBe(true);
        expect(onWorklist).toHaveBeenCalledOnce();
        expect(c.openCreated()).toBe(true);
        expect(onOpen).toHaveBeenCalledWith({ dictationId: 'rec-1' });
        expect(TEXT.toWorklist).toBe('Zur Arbeitsliste');
        expect(TEXT.open).toBe('Öffnen');
    });

    it('starts the next dictation clean after success', async () => {
        const { c, client } = make();
        expect(c.another().code).toBe('invalid_phase');
        await toReview(c);
        await c.send();
        expect(c.another().ok).toBe(true);
        expect(client.switchContext).toHaveBeenCalledWith({});
        expect(c.model().phase).toBe('title');
        expect(c.model().created).toBeNull();
        expect(c.model().title).toBe('');
    });

    it('cancel discards the recording and returns to the title phase', async () => {
        const { c, rec } = make();
        await toReview(c);
        expect((await c.cancel()).ok).toBe(true);
        expect(rec.discardRecording).toHaveBeenCalledOnce();
        expect(c.model().phase).toBe('title');
    });

    it('refuses every action when unauthorized', async () => {
        const { c, rec, uploadFile } = make({ options: { authorized: () => false } });
        expect(c.model().message).toBe(TEXT.denied);
        c.setTitle('x');
        expect((await c.start()).code).toBe('unauthorized');
        expect(c.setMode('file').code).toBe('unauthorized');
        expect(c.selectFile(audio()).code).toBe('unauthorized');
        expect((await c.sendFile()).code).toBe('unauthorized');
        expect(rec.startRecording).not.toHaveBeenCalled();
        expect(uploadFile).not.toHaveBeenCalled();
    });

    it('notifies subscribers on every change and stops after unsubscribe', async () => {
        const { c } = make();
        const seen = vi.fn();
        const off = c.subscribe(seen);
        c.setTitle('x');
        expect(seen).toHaveBeenCalled();
        const n = seen.mock.calls.length;
        off();
        c.setTitle('xy');
        expect(seen.mock.calls.length).toBe(n);
    });
});

describe('file upload path', () => {
    it('validates audio files', () => {
        expect(isAudioFile(audio())).toBe(true);
        expect(isAudioFile({ name: 'a.DS2', size: 5, type: '' })).toBe(true);
        expect(isAudioFile({ name: 'a.pdf', size: 5, type: 'application/pdf' })).toBe(false);
        expect(isAudioFile({ name: 'a.wav', size: 0, type: 'audio/wav' })).toBe(false);
        expect(isAudioFile(null)).toBe(false);
    });

    it('selects, uploads and shows the created dictation', async () => {
        const { c, uploadFile, onOpen } = make();
        expect(c.selectFile(audio()).code).toBe('invalid_phase');
        expect(c.setMode('file').ok).toBe(true);
        expect(c.selectFile({ name: 'x.txt', size: 3, type: 'text/plain' }).code).toBe('invalid_file');
        expect(c.model().message).toBe(TEXT.fileInvalid);
        expect(c.selectFile(audio()).ok).toBe(true);
        expect(c.model().file).toEqual({ name: 'diktat.wav', size: 1200 });
        expect(c.model().canUpload).toBe(true);
        const out = await c.sendFile();
        expect(out.ok).toBe(true);
        expect(uploadFile).toHaveBeenCalledOnce();
        expect(c.model().phase).toBe('sent');
        expect(c.model().created).toEqual({ dictationId: 'd-9' });
        expect(c.model().message).toBe(TEXT.sent);
        c.openCreated();
        expect(onOpen).toHaveBeenCalledWith({ dictationId: 'd-9' });
        expect(c.another().ok).toBe(true);
        expect(c.model().phase).toBe('idle');
    });

    it('blocks a double upload while sending', async () => {
        let release;
        const uploadFile = vi.fn(() => new Promise((resolve) => { release = resolve; }));
        const { c } = make({ uploadFile });
        c.setMode('file');
        c.selectFile(audio());
        const first = c.sendFile();
        expect(c.model().phase).toBe('sending');
        expect(c.model().message).toBe(TEXT.uploading);
        expect((await c.sendFile()).code).toBe('invalid_phase');
        expect(c.cancelFile().code).toBe('invalid_phase');
        expect(c.setMode('record').code).toBe('invalid_phase');
        release({ id: 11 });
        expect((await first).ok).toBe(true);
        expect(uploadFile).toHaveBeenCalledOnce();
    });

    it('says the audio is not stored locally when the upload fails, and allows a retry', async () => {
        let calls = 0;
        const uploadFile = vi.fn(async () => { calls += 1; if (calls === 1) throw new Error('boom'); return { id: 'd-2' }; });
        const { c } = make({ uploadFile });
        c.setMode('file');
        c.selectFile(audio());
        expect((await c.sendFile()).code).toBe('no_receipt');
        const m = c.model();
        expect(m.phase).toBe('failed');
        expect(m.created).toBeNull();
        expect(m.message).toBe(TEXT.fileFailed);
        expect(m.message).toContain('nicht gesendet');
        expect(m.message).toContain('nicht lokal gespeichert');
        expect(m.uploadLabel).toBe('Erneut hochladen');
        expect(m.canUpload).toBe(true);
        expect((await c.sendFile()).ok).toBe(true);
        expect(c.model().created).toEqual({ dictationId: 'd-2' });
    });

    it('treats a receipt without id as failed', async () => {
        const { c } = make({ uploadFile: vi.fn(async () => ({ ok: true })) });
        c.setMode('file');
        c.selectFile(audio());
        await c.sendFile();
        expect(c.model().phase).toBe('failed');
        expect(receiptDictationId({ ok: true })).toBeNull();
        expect(receiptDictationId({ dictation_id: 5 })).toBe('5');
    });

    it('cancelFile clears the selection', () => {
        const { c } = make();
        c.setMode('file');
        c.selectFile(audio());
        expect(c.cancelFile().ok).toBe(true);
        expect(c.model().file).toBeNull();
        expect(c.model().phase).toBe('idle');
    });

    it('does not switch source while a recording is running', async () => {
        const { c } = make();
        c.setTitle('x');
        await c.start();
        expect(c.setMode('file').code).toBe('invalid_phase');
        expect(c.setMode('nope').code).toBe('invalid_mode');
    });
});

describe('dispose', () => {
    it('discards an unsent recording and ignores a late file result', async () => {
        const a = make();
        await toReview(a.c);
        await a.c.dispose();
        expect(a.rec.discardRecording).toHaveBeenCalledOnce();

        let release;
        const uploadFile = vi.fn(() => new Promise((resolve) => { release = resolve; }));
        const b = make({ uploadFile });
        b.c.setMode('file');
        b.c.selectFile(audio());
        const pending = b.c.sendFile();
        await b.c.dispose();
        release({ id: 1 });
        expect((await pending).code).toBe('stale');
    });

    it('does not discard a recording that is being sent', async () => {
        let release;
        const rec = recorder(() => new Promise((resolve) => { release = resolve; }));
        const { c } = make({ recorder: rec });
        await toReview(c);
        const pending = c.send();
        await c.dispose();
        expect(rec.discardRecording).not.toHaveBeenCalled();
        release({ id: 1 });
        await pending;
    });
});

describe('no browser storage', () => {
    it('never touches IndexedDB, localStorage or sessionStorage', async () => {
        const store = () => ({ getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn(), key: vi.fn() });
        const local = store();
        const session = store();
        const idb = { open: vi.fn(), deleteDatabase: vi.fn() };
        vi.stubGlobal('localStorage', local);
        vi.stubGlobal('sessionStorage', session);
        vi.stubGlobal('indexedDB', idb);
        const { c } = make();
        await toReview(c);
        await c.send();
        c.another();
        c.setMode('file');
        c.selectFile(audio());
        await c.sendFile();
        await c.dispose();
        for (const spy of [...Object.values(local), ...Object.values(session), ...Object.values(idb)]) {
            expect(spy).not.toHaveBeenCalled();
        }
        expect(source()).not.toMatch(/indexedDB|localStorage|sessionStorage|RecordingDB/);
    });
});

function fakeVue() {
    const hooks = { mount: [], unmount: [] };
    return {
        hooks,
        ref: (v) => ({ value: v }),
        computed: (fn) => ({ get value() { return fn(); } }),
        onMounted: (fn) => hooks.mount.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
    };
}

describe('view registration and setup', () => {
    it('registers the component with its props and events', () => {
        const app = { component: vi.fn() };
        registerRecordingView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-recording-view', RecordingView);
        expect(Object.keys(RecordingView.props)).toEqual(['client', 'currentUser', 'recorder', 'uploadFile', 'player', 'authorized', 'vue']);
        expect(RecordingView.emits).toEqual(['open', 'worklist']);
    });

    it('has german labels, status live region and touch targets', () => {
        expect(RECORDING_TEMPLATE).toContain('role="status"');
        expect(RECORDING_TEMPLATE).toContain('aria-live="polite"');
        expect(RECORDING_TEMPLATE).toContain('min-height: 2.75rem');
        expect(RECORDING_TEMPLATE).toContain('c.text.toWorklist');
        expect(RECORDING_TEMPLATE).toContain('c.text.open');
    });

    it('emits open and worklist and disposes on unmount', async () => {
        const Vue = fakeVue();
        const emit = vi.fn();
        const rec = recorder();
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"routable":true}')));
        const view = RecordingView.setup({
            client: { switchContext: vi.fn() }, currentUser: { id: 2 }, recorder: rec, uploadFile: vi.fn(), vue: Vue,
        }, { emit });
        view.c.setTitle('x');
        await view.c.checkRouting();
        await view.c.start();
        await view.c.stop();
        await view.c.send();
        view.c.toWorklist();
        view.c.openCreated();
        expect(emit).toHaveBeenCalledWith('worklist', undefined);
        expect(emit).toHaveBeenCalledWith('open', { dictationId: 'rec-1' });
        expect(view.m.value.created).toEqual({ dictationId: 'rec-1' });
        view.act('nope');
        await Vue.hooks.unmount[0]();
    });

    it('reviews an attached take without restarting capture and exposes playback errors', async () => {
        const rec = { ...recorder(), reviewSource: () => 'blob:take' };
        const view = RecordingView.setup({ currentUser: { id: 2 }, recorder: rec, uploadFile: vi.fn(), vue: fakeVue() }, {});
        const media = { duration: Infinity, currentTime: 0, paused: true, play: vi.fn(), pause: vi.fn() };
        const disabled = (id) => view.m.value.controls.find(b => b.id === id).disabled;
        await toReview(view.c);
        expect(view.reviewSource()).toBe('blob:take');
        expect(disabled('play')).toBe(true);
        view.loadReview({ currentTarget: media });
        expect(media.currentTime).toBeGreaterThan(0);
        expect(disabled('end')).toBe(true);
        media.duration = 12;
        view.finishDurationProbe({ currentTarget: media });
        expect(media.currentTime).toBe(0);
        expect(disabled('end')).toBe(false);
        view.act('end');
        expect(media.currentTime).toBe(12);
        await view.c.play();
        expect(media.play).not.toHaveBeenCalled();
        view.act('beginning');
        expect(media.currentTime).toBe(0);
        media.play.mockRejectedValueOnce(new Error('unavailable'));
        expect((await view.c.play()).code).toBe('playback_failed');
        expect(view.m.value.message).toMatch(/nicht abgespielt/);
        expect((await view.c.play()).ok).toBe(true);
        expect(view.m.value.message).toBe('');
        media.currentTime = 8;
        view.act('rewind');
        expect(media.currentTime).toBe(0);
        expect(rec.startRecording).toHaveBeenCalledOnce();
        await view.c.cancel();
        expect(media.pause).toHaveBeenCalledOnce();
        view.bindReview(null);
        expect(disabled('play')).toBe(true);
    });

    it('plays the review take on the chosen output device where setSinkId exists', async () => {
        const devices = [
            { kind: 'audiooutput', deviceId: 'default', label: 'Default' },
            { kind: 'audiooutput', deviceId: 'spk-2', label: 'Lautsprecher 2' },
            { kind: 'audioinput', deviceId: 'mic', label: 'Mikrofon' },
        ];
        vi.stubGlobal('navigator', { mediaDevices: { enumerateDevices: vi.fn(async () => devices) } });
        const view = RecordingView.setup({ currentUser: { id: 2 }, recorder: recorder(), uploadFile: vi.fn(), vue: fakeVue() }, {});
        const media = { sinkId: '', setSinkId: vi.fn(async (id) => { media.sinkId = id; }) };
        view.bindReview(media);
        expect(view.outputSupported.value).toBe(true);
        await Promise.resolve();
        await Promise.resolve();
        expect(view.outputs.value.map((d) => d.deviceId)).toEqual(['spk-2']);
        await view.chooseOutput('spk-2');
        expect(media.setSinkId).toHaveBeenLastCalledWith('spk-2');
        view.bindReview(media); // Vue calls function refs again on view updates.
        expect(navigator.mediaDevices.enumerateDevices).toHaveBeenCalledTimes(1);
        media.setSinkId.mockRejectedValueOnce(new Error('gone'));
        await view.chooseOutput('spk-3');
        expect(view.outputId.value).toBe('');
        expect(media.sinkId).toBe('');
        expect(RECORDING_TEMPLATE).toContain('data-testid="rec-output"');
    });

    it('disables the output control and says so where setSinkId is missing, without touching pedal chords', () => {
        const view = RecordingView.setup({ currentUser: { id: 2 }, recorder: recorder(), uploadFile: vi.fn(), vue: fakeVue() }, {});
        const shortcuts = { ...view.shortcuts };
        view.bindReview({});
        expect(view.outputSupported.value).toBe(false);
        expect(RECORDING_TEMPLATE).toContain(':disabled="!outputSupported"');
        expect(RECORDING_TEMPLATE).toContain('Systemausgang');
        view.bindReview(null);
        expect(view.shortcuts).toEqual(shortcuts);
    });

    it('dispatches only the enabled recorder chords in the mounted view', async () => {
        const rec = recorder();
        const vue = fakeVue();
        const player = { duration: 12, currentTime: 0, paused: true,
            play: vi.fn(() => { player.paused = false; }), pause: vi.fn(() => { player.paused = true; }) };
        const view = RecordingView.setup({ currentUser: { id: 2 }, recorder: rec, uploadFile: vi.fn(), vue, player }, {});
        const doc = { body: {}, addEventListener: vi.fn(), removeEventListener: vi.fn() };
        const inside = {};
        view.root.value = { ownerDocument: doc, contains: (target) => target === inside };
        vue.hooks.mount[0]();
        const [type, dispatch] = doc.addEventListener.mock.calls[0];
        expect(type).toBe('keydown');
        const press = (key, extra) => dispatch({ key, altKey: true, shiftKey: true, target: inside, preventDefault: vi.fn(), ...extra });
        view.c.setTitle('Bericht');
        await press(' ');
        await press('R', { target: { tagName: 'TEXTAREA' } });
        await press('R', { ctrlKey: true });
        await press('R', { repeat: true });
        expect(rec.startRecording).not.toHaveBeenCalled();
        await press('R', { target: doc.body });
        expect(view.m.value.phase).toBe('recording');
        await press('R');
        expect(view.m.value.phase).toBe('paused');
        await press('R');
        expect(view.m.value.phase).toBe('recording');
        await press('S');
        expect(rec.stopRecording).toHaveBeenCalledOnce();
        expect(view.m.value.phase).toBe('review');
        await press('ArrowRight');
        expect(player.currentTime).toBe(5);
        await press('ArrowLeft');
        await press('ArrowLeft');
        expect(player.currentTime).toBe(0);
        await press(' ', { target: { tagName: 'INPUT' } });
        expect(player.play).not.toHaveBeenCalled();
        await press(' ');
        await press(' ');
        await press(' ');
        await press('S');
        expect(player.play).toHaveBeenCalledTimes(2);
        expect(player.pause).toHaveBeenCalledTimes(2);
        expect(rec.startRecording).toHaveBeenCalledOnce();
        await vue.hooks.unmount[0]();
        expect(doc.removeEventListener).toHaveBeenCalledWith('keydown', dispatch);
    });

    it('throws without Vue', () => {
        expect(() => RecordingView.setup({ currentUser: { id: 1 }, recorder: recorder(), uploadFile() {} }, {}))
            .toThrow('Vue is required');
    });
});
