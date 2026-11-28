import { describe, expect, it, vi } from 'vitest';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';
import { createRecordingViewController } from '../../../static/js/clinic/compose-recording.js';
import { createHandmicController } from '../../../static/js/clinic/handmic-webhid.js';

function fixture({ model = 3710, productId = 0x0c1c, usagePage = 0xffa0, outputSize = 9 } = {}) {
    const device = Object.assign(new EventTarget(), {
        vendorId: 0x0911, productId, opened: false,
        collections: [{ usagePage, usage: 1, inputReports: [{ reportId: 0 }],
            outputReports: [{ reportId: 0, items: [{ reportSize: 8, reportCount: outputSize }] }] }],
    });
    function report(command, word = 0, { reportId = 0, length = 9 } = {}) {
        const data = new DataView(new ArrayBuffer(length + 2), 2);
        data.setUint8(0, command);
        if (length >= 9) {
            if (command === 0x8b) data.setUint16(4, model);
            else data.setUint16(7, word, true);
        }
        device.dispatchEvent(Object.assign(new Event('inputreport'), { device, reportId, data }));
    }
    device.open = vi.fn(async () => { device.opened = true; });
    device.close = vi.fn(async () => { device.opened = false; });
    device.sendReport = vi.fn(async (_id, bytes) => { if (bytes[0] === 0x8b) report(0x8b); });
    const hid = Object.assign(new EventTarget(), { requestDevice: vi.fn(async () => [device]) });
    const onInput = vi.fn();
    const onState = vi.fn();
    const controller = createHandmicController(hid, { onInput, onState });
    return { controller, device, hid, onInput, onState, report };
}

describe('handmic WebHID (synthetic reports)', () => {
    it.each([[3710, ['f1', 'forward'], 'play'], [3700, ['f1', 'play', 'forward'], null]])(
        'decodes button snapshots and model-specific slide positions for %i', async (model, buttons, slide) => {
            const { controller, hid, onInput, report } = fixture({ model });
            await controller.pair();
            expect(hid.requestDevice).toHaveBeenCalledWith({ filters: [
                { vendorId: 0x0911, productId: 0x0c1c, usagePage: 0xffa0, usage: 1 },
            ] });
            report(0x80, 0x0c02);
            expect(onInput).toHaveBeenLastCalledWith({ buttons, slide });
            report(0x80, 0x0200);
            expect(onInput).toHaveBeenLastCalledWith(model === 3710 ? { buttons: [], slide: 'stop' } : { buttons: ['stop'], slide: null });
            report(0x91);
            report(0x80, 0, { length: 8 });
            report(0x80, 0x0100, { reportId: 1 });
            if (model === 3710) report(0x80, 0x0600);
            expect(onInput).toHaveBeenCalledTimes(2);
            await controller.dispose();
        },
    );

    it.each([{ productId: 0x1f40 }, { vendorId: 1 }, { usagePage: 1 }, { model: 3720 }])(
        'fails closed for an undeclared contract: %o', async (options) => {
            const { controller, device, onInput, onState, report } = fixture(options);
            if (options.vendorId) device.vendorId = options.vendorId;
            await controller.pair();
            expect(onState).toHaveBeenLastCalledWith({ status: 'unsupported', ledAvailable: false });
            if (!options.model) expect(device.open).not.toHaveBeenCalled();
            expect(device.opened).toBe(false);
            report(0x80, 0x0100);
            expect(onInput).not.toHaveBeenCalled();
            expect(await controller.syncLed('recording')).toBe('unavailable');
            expect(device.sendReport.mock.calls.map(([, bytes]) => bytes[0])).toEqual(options.model ? [0x8b] : []);
        },
    );

    it('synchronizes recording, standby and off through actual output calls', async () => {
        const { controller, device } = fixture();
        await controller.pair();
        for (const [state, red] of [['recording', 12], ['standby', 4], ['off', 0]]) {
            expect(await controller.syncLed(state)).toBe('sent');
            expect(device.sendReport).toHaveBeenLastCalledWith(0, Uint8Array.of(2, 0, 0, 0, 0, 0, red, 0, 0));
        }
        await controller.dispose();
    });

    it('reports unavailable feedback without writing to a short output report', async () => {
        const { controller, device, onState } = fixture({ outputSize: 1 });
        await controller.pair();
        expect(onState).toHaveBeenLastCalledWith({ status: 'connected', name: 'Philips SpeechMike SMP3710', ledAvailable: false });
        expect(await controller.syncLed('recording')).toBe('unavailable');
        expect(device.sendReport).toHaveBeenCalledTimes(1);
        await controller.dispose();
    });

    it('marks rejected LED output unavailable rather than reporting success or retrying', async () => {
        const { controller, device, onState } = fixture();
        await controller.pair();
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        try {
            const error = new Error('Output report rejected');
            device.sendReport.mockRejectedValueOnce(error);
            expect(await controller.syncLed('recording')).toBe('unavailable');
            expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'connected', ledAvailable: false }));
            expect(warn).toHaveBeenCalledWith('WebHID LED output failed', error);
            expect(await controller.syncLed('off')).toBe('unavailable');
            expect(device.sendReport).toHaveBeenCalledTimes(2);
        } finally {
            warn.mockRestore();
            await controller.dispose();
        }
    });

    it('clears held input and LED availability on disconnect and allows explicit re-pairing', async () => {
        const { controller, device, hid, onInput, onState, report } = fixture();
        await controller.pair();
        report(0x80, 0x0102);
        hid.dispatchEvent(Object.assign(new Event('disconnect'), { device }));
        expect(device.close).toHaveBeenCalledOnce();
        expect(onInput).toHaveBeenLastCalledWith({ buttons: [], slide: null });
        expect(onState).toHaveBeenLastCalledWith({ status: 'disconnected', ledAvailable: false });
        report(0x80, 0x0400);
        expect(onInput).toHaveBeenCalledTimes(2);
        expect(await controller.syncLed('recording')).toBe('unavailable');
        await controller.pair();
        expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'connected', ledAvailable: true }));
        await controller.dispose();
        expect(device.close).toHaveBeenCalledTimes(2);
    });

    it('ignores a chooser result that arrives after teardown', async () => {
        const { controller, device } = fixture();
        const pairing = controller.pair();
        await controller.dispose();
        await pairing;
        expect(device.open).not.toHaveBeenCalled();
    });
});

function recordingFixture(hid, options = {}) {
    const recorder = {
        startRecording: vi.fn(async () => {}), pauseRecording: vi.fn(), resumeRecording: vi.fn(),
        stopRecording: vi.fn(async () => {}), discardRecording: vi.fn(async () => {}),
        uploadRecordedAudio: vi.fn(async () => ({ id: 'rec-1' })),
    };
    const c = createRecordingViewController({ hid, recorder, currentUser: { id: 7 }, uploadFile: vi.fn(), ...options });
    return { c, recorder };
}

const ledReport = (red) => Uint8Array.of(2, 0, 0, 0, 0, 0, red, 0, 0);

describe('P-257 compose controllers (synthetic HID reports)', () => {
    it('honors recorder phases and requires a fresh press for capture and EOL actions', async () => {
        const { device, hid, report } = fixture({ model: 3700 });
        const { c, recorder } = recordingFixture(hid);
        const input = (word) => report(0x80, word);
        const press = (word) => { input(0); input(word); };
        recorder.uploadRecordedAudio.mockResolvedValueOnce(null);
        try {
            await c.handmic.pair();
            input(0x0100); // No title.
            expect(recorder.startRecording).not.toHaveBeenCalled();
            c.setTitle('Bericht');
            input(0x0100); // Still held across the phase change.
            expect(recorder.startRecording).not.toHaveBeenCalled();
            press(0x0400);
            press(0x0200);
            press(0x2000);
            expect(c.model().phase).toBe('ready');
            expect(recorder.resumeRecording).not.toHaveBeenCalled();
            expect(recorder.stopRecording).not.toHaveBeenCalled();
            expect(recorder.uploadRecordedAudio).not.toHaveBeenCalled();

            c.toggleVox();
            press(0x0100);
            await vi.waitFor(() => expect(c.model().voxStatus).toBe('Diese Aufnahme wird stimmgesteuert.'));
            expect(c.model().phase).toBe('recording');
            expect(recorder.startRecording).toHaveBeenCalledOnce();
            expect(recorder.startRecording).toHaveBeenCalledWith({ voxEnabled: true });
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(12));
            input(0x0100);
            expect(recorder.pauseRecording).not.toHaveBeenCalled();
            press(0x2000);
            expect(recorder.uploadRecordedAudio).not.toHaveBeenCalled();

            press(0x0400);
            expect(c.model().phase).toBe('paused');
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(4));
            input(0x0400);
            expect(recorder.pauseRecording).toHaveBeenCalledOnce();
            expect(recorder.resumeRecording).not.toHaveBeenCalled();
            press(0x0400);
            expect(c.model().phase).toBe('recording');
            expect(recorder.resumeRecording).toHaveBeenCalledOnce();

            press(0x0200);
            await vi.waitFor(() => expect(c.model().phase).toBe('review'));
            expect(recorder.stopRecording).toHaveBeenCalledOnce();
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(0));
            press(0x2000);
            expect(recorder.uploadRecordedAudio).toHaveBeenCalledWith({ title: 'Bericht' });
            await vi.waitFor(() => expect(c.model().phase).toBe('failed'));
            input(0x2000);
            expect(recorder.uploadRecordedAudio).toHaveBeenCalledOnce();
            press(0x2000);
            await vi.waitFor(() => expect(c.model().phase).toBe('sent'));
            expect(recorder.uploadRecordedAudio).toHaveBeenCalledTimes(2);
        } finally {
            await c.dispose();
        }
    });

    it('saves the recording draft on EOL and leaves the separate submission controls to the screen', async () => {
        const { hid, report } = fixture({ model: 3700 });
        const request = vi.fn(async (path) => ({ outcome: 'ok', data: path.endsWith('/detail') ? {
            dictation_id: 'd1', etag: 'draft-1', allowed_actions: ['send'],
            draft_metadata: { title: 'Bericht' }, status_axes: { workflow: 'neu_angelegt' },
        } : { acceptance_required: false } }));
        const { c, recorder } = recordingFixture(hid, { draftMetadata: true, client: { request } });
        recorder.uploadRecordedAudio.mockResolvedValue({ dictation_id: 'd1' });
        const eol = () => { report(0x80, 0); report(0x80, 0x2000); };
        try {
            await c.handmic.pair();
            c.setTitle('Bericht');
            await c.start();
            await c.stop();
            eol();
            await vi.waitFor(() => expect(c.model()).toMatchObject({
                canSubmit: true, draft: { fields: { title: 'Bericht' } },
            }));
            expect(recorder.uploadRecordedAudio).toHaveBeenCalledOnce();
            expect(request.mock.calls).toEqual([['/dictations/d1/detail'], ['/dictations/d1/acceptance-policy']]);
            eol();
            expect(request).toHaveBeenCalledTimes(2);
            expect(c.model().canSubmit).toBe(true);
        } finally {
            await c.dispose();
        }
    });

    it('locks correction input and turns the LED off synchronously on generation loss', async () => {
        const { device, hid, report } = fixture({ model: 3700 });
        const ok = (data) => ({ outcome: 'ok', status: 200, data });
        const save = vi.fn(() => ok({ text_revision_id: 'r2', state_version: 2 }));
        const routes = {
            'GET /dictations/d1/correction': () => ok({
                dictation_id: 'd1', text_revision_id: 'r1', text: 'Hallo Welt',
                state: { version: 1, audio_position_ms: 0 }, links: [], markers: [],
            }),
            'GET /dictations/d1/audio-revisions/current': () => ok({ revision_id: 'a1', segments: [] }),
            'POST /dictations/d1/leases': () => ok({
                lease_id: 'lease-1', generation: 1, expires_at: new Date(Date.now() + 600000).toISOString(),
            }),
            'POST /dictations/d1/correction/save': save,
        };
        const client = {
            switchContext: vi.fn(),
            request: vi.fn(async (path, options) => routes[`${options?.method || 'GET'} ${path}`]?.(options) ?? ok({})),
        };
        const c = createCorrectionViewController({
            client, hid, contract: 'v8', pedal: false, autosaveMs: 0, dictationId: 'd1', currentUser: { id: 7 },
        });
        const audio = Object.assign(new EventTarget(), {
            currentTime: 0, duration: 60, playbackRate: 1, paused: true,
            play: vi.fn(async () => { audio.paused = false; }),
            pause: vi.fn(() => { audio.paused = true; }),
        });
        const press = (word) => { report(0x80, 0); report(0x80, word); };
        try {
            await c.start();
            await c.handmic.pair();
            c.bindAudio(audio);
            press(0x0400);
            expect(audio.play).not.toHaveBeenCalled();
            await c.editor.acquire();
            expect(c.handmicModel.value.message).toBe('Handmikrofon bereit.');
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(4));

            c.togglePlayback();
            expect(audio.paused).toBe(false);
            press(0x0400);
            expect(audio.paused).toBe(true);
            report(0x80, 0x0400);
            expect(audio.paused).toBe(true);
            press(0x0400);
            expect(audio.paused).toBe(false);
            press(0x2000);
            expect(save).not.toHaveBeenCalled();
            expect(c.setText('Korrigiert')).toBe(true);
            press(0x2000);
            await vi.waitFor(() => expect(c.editor.dirty.value).toBe(false));
            expect(save).toHaveBeenCalledOnce();
            expect(c.setText('Noch nicht gespeichert')).toBe(true);

            c.editor.leaseController.notifyGeneration(2);
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(0));
            expect(c.handmicModel.value.message).toBe('Handmikrofon gesperrt. Bearbeitung nicht möglich.');
            expect(c.editor.canEdit.value).toBe(false);
            press(0x0400);
            press(0x2000);
            expect(audio.paused).toBe(false);
            expect(save).toHaveBeenCalledOnce();
        } finally {
            await c.dispose();
        }
    });

    it('turns off an actively recording handmic before closing and removes both listeners', async () => {
        const { device, hid, report } = fixture({ model: 3700 });
        const addDisconnect = vi.spyOn(hid, 'addEventListener');
        const removeDisconnect = vi.spyOn(hid, 'removeEventListener');
        const addInput = vi.spyOn(device, 'addEventListener');
        const removeInput = vi.spyOn(device, 'removeEventListener');
        const { c, recorder } = recordingFixture(hid);
        try {
            await c.handmic.pair();
            c.setTitle('Bericht');
            report(0x80, 0x0100);
            await vi.waitFor(() => expect(c.model().phase).toBe('recording'));
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(12));

            await c.dispose();
            expect(device.sendReport).toHaveBeenLastCalledWith(0, ledReport(0));
            expect(device.sendReport.mock.invocationCallOrder.at(-1)).toBeLessThan(device.close.mock.invocationCallOrder[0]);
            expect(device.close).toHaveBeenCalledOnce();
            expect(device.opened).toBe(false);
            expect(removeDisconnect).toHaveBeenCalledWith('disconnect', addDisconnect.mock.calls[0][1]);
            expect(removeInput).toHaveBeenCalledWith('inputreport', addInput.mock.calls.at(-1)[1]);
            report(0x80, 0x0100);
            expect(recorder.startRecording).toHaveBeenCalledOnce();
        } finally {
            await c.dispose();
        }
    });
});
