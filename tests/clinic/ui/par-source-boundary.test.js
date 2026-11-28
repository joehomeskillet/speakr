import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRecordingViewController } from '../../../static/js/clinic/compose-recording.js';
import * as audio from '../../../static/js/modules/composables/audio.js';

afterEach(() => vi.unstubAllGlobals());

it.each(['mp4a.40.2', 'opus'])('offers one M4A option with a supported %s encoder', (codec) => {
    vi.stubGlobal('MediaRecorder', { isTypeSupported: (type) =>
        type === 'audio/mp4;codecs=' + codec || (codec === 'mp4a.40.2' && type === 'audio/mp4;codecs=opus') });
    expect(audio.clinicRecordingFormats()).toEqual([
        { mimeType: 'audio/mp4;codecs=' + codec, extension: 'm4a', label: 'M4A' },
    ]);
});

function setup() {
    const microphone = {
        deviceId: 'desk',
        inputs: [{ deviceId: 'desk', label: 'Diktiermikrofon' }, { deviceId: 'headset', label: 'Headset' }],
        select(id) { this.deviceId = id; },
        switchInput: vi.fn().mockResolvedValue(true),
    };
    const recorder = {
        startRecording: vi.fn(), stopRecording: vi.fn(), pauseRecording: vi.fn(), resumeRecording: vi.fn(),
        uploadRecordedAudio: vi.fn().mockResolvedValue({ id: 1 }),
    };
    const controller = createRecordingViewController({
        currentUser: { id: 1 }, microphone, recorder, uploadFile: vi.fn(),
    });
    controller.setTitle('Synthetische Aufnahme');
    return { controller, microphone, recorder };
}

describe('microphone changes during a take', () => {
    it('keeps the current microphone and recording on cancel', async () => {
        const { controller, microphone, recorder } = setup();
        await controller.start();
        controller.chooseMicrophone('headset');
        expect(controller.model().pendingMicrophone).toBe('headset');
        expect(microphone.deviceId).toBe('desk');
        controller.cancelMicrophone();
        expect(controller.model()).toMatchObject({ pendingMicrophone: null, phase: 'recording' });
        expect(microphone.deviceId).toBe('desk');
        expect(microphone.switchInput).not.toHaveBeenCalled();
        expect(recorder.stopRecording).not.toHaveBeenCalled();
    });

    it('changes selection only after the new source is capturing and preserves pause', async () => {
        const { controller, microphone, recorder } = setup();
        await controller.start();
        controller.pause();
        controller.chooseMicrophone('headset');
        let finish;
        microphone.switchInput.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
        const pending = controller.confirmMicrophone();
        expect(microphone.switchInput).toHaveBeenCalledWith('headset');
        expect(microphone.deviceId).toBe('desk');
        expect((await controller.confirmMicrophone()).ok).toBe(false);
        finish(true);
        expect(await pending).toEqual({ ok: true });
        expect(microphone.deviceId).toBe('headset');
        expect(controller.model()).toMatchObject({ pendingMicrophone: null, phase: 'paused' });
        expect(controller.model().message).toContain('Headset');
        expect(recorder.startRecording).toHaveBeenCalledOnce();
        expect(recorder.stopRecording).not.toHaveBeenCalled();
        expect(recorder.resumeRecording).not.toHaveBeenCalled();
    });

    it('ignores a microphone open that finishes after stop', async () => {
        const { controller, microphone } = setup();
        await controller.start();
        controller.chooseMicrophone('headset');
        let finish;
        microphone.switchInput.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
        const change = controller.confirmMicrophone();
        await controller.stop();
        finish(true);
        expect((await change).ok).toBe(false);
        expect(microphone.deviceId).toBe('desk');
        expect(controller.model()).toMatchObject({ microphoneSwitching: false, phase: 'review' });
    });

    it('keeps the original source when the new device cannot open', async () => {
        const { controller, microphone } = setup();
        await controller.start();
        controller.chooseMicrophone('headset');
        microphone.switchInput.mockRejectedValueOnce(new DOMException('Unplugged', 'NotFoundError'));
        expect((await controller.confirmMicrophone()).ok).toBe(false);
        expect(microphone.deviceId).toBe('desk');
        expect(controller.model().phase).toBe('recording');
        expect(controller.model().message).toContain('Diktiermikrofon');
    });
});

it('leaves file upload available when the browser cannot encode a supported format', async () => {
    const { controller, microphone, recorder } = setup();
    microphone.formats = [];
    expect(controller.model().controls.find(control => control.id === 'start').disabled).toBe(true);
    expect((await controller.start()).ok).toBe(false);
    expect(recorder.startRecording).not.toHaveBeenCalled();
    expect(controller.setMode('file')).toEqual({ ok: true });
});

it('offers only browser-supported container and codec combinations', () => {
    vi.stubGlobal('MediaRecorder', { isTypeSupported: (type) => type === 'audio/ogg;codecs=opus' || type === 'audio/webm' });
    expect(audio.clinicRecordingFormats()).toEqual([
        { mimeType: 'audio/ogg;codecs=opus', extension: 'ogg', label: 'Ogg' },
    ]);
    vi.stubGlobal('MediaRecorder', undefined);
    expect(audio.clinicRecordingFormats()).toEqual([]);
});
