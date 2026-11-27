import { afterEach, describe, expect, it, vi } from 'vitest';
import {
    VOX_CONFIG, encodeWav, gateAudioBlob, planVoxSegments, rmsDbfs,
} from '../../../static/js/clinic/vox-gate.js';
import { createClinicRecorder } from '../../../static/js/clinic/recorder-phases-ui.js';

const config = { ...VOX_CONFIG, preRollMs: 0, postRollMs: 20, minSpeechMs: 20 };
afterEach(() => vi.unstubAllGlobals());

function pcm(parts, frameSize = 20) {
    return Float32Array.from(parts.flatMap((part) => Array(frameSize).fill(part)));
}

function stubDecode(...channels) {
    const context = {
        decodeAudioData: vi.fn(async () => ({
            sampleRate: 1000, numberOfChannels: channels.length, length: channels[0].length,
            getChannelData: (index) => channels[index],
        })),
        close: vi.fn(async () => {}),
    };
    vi.stubGlobal('AudioContext', class { constructor() { return context; } });
    return context;
}

describe('local vox gate', () => {
    it('uses the VoxConfig threshold, hysteresis and timing defaults', () => {
        expect(VOX_CONFIG).toMatchObject({
            thresholdDbfs: -40, hysteresisDb: 6, preRollMs: 500, postRollMs: 1500, frameMs: 20, minSpeechMs: 100,
        });
        expect(rmsDbfs(new Float32Array(20))).toBe(-96);
        expect(rmsDbfs(new Float32Array(20).fill(0.1))).toBeCloseTo(-20, 5);
        expect(planVoxSegments([-60, -30, -30, -60], { ...config, preRollMs: 20 })).toEqual([[0, 80]]);
    });

    it('drops a silence gap while retaining both speech runs in both channels', async () => {
        const input = pcm([0.5, 0.5, 0.5, 0.5, 0.5, 0, 0, 0, 0, 0, -0.5, -0.5, -0.5, -0.5, -0.5]);
        const channels = [input, input.map(sample => -sample)];
        const context = stubDecode(...channels);
        const source = new Blob([encodeWav(channels, 1000)], { type: 'audio/wav' });
        const output = await gateAudioBlob(source, config);
        const bytes = await output.arrayBuffer();
        const view = new DataView(bytes);
        const samples = new Int16Array(bytes, 44);
        expect(output.type).toBe('audio/wav');
        expect(output.size).toBeLessThan(source.size);
        expect(view.getUint16(22, true)).toBe(2);
        expect(view.getUint32(40, true)).toBe(220 * 2 * 2);
        expect(samples.filter(sample => sample !== 0)).toHaveLength(400);
        expect(Array.from(samples.slice(0, 2))).toEqual([16383, -16384]);
        expect(Array.from(samples.slice(-2))).toEqual([-16384, 16383]);
        expect(context.decodeAudioData).toHaveBeenCalledOnce();
        expect(context.close).toHaveBeenCalledOnce();
    });

    it('keeps a take unchanged when no speech is detected or the input is empty', async () => {
        const silence = pcm([0, 0, 0]);
        stubDecode(silence);
        const source = new Blob([encodeWav([silence], 1000)], { type: 'audio/wav' });
        expect(await gateAudioBlob(source, config)).toBe(source);
        const empty = new Blob();
        expect(await gateAudioBlob(empty, config)).toBe(empty);
    });

    it('encodes PCM as a local WAV blob and falls back without Web Audio', async () => {
        const input = pcm([0.5, 0.5, 0, 0, 0, -0.5, -0.5]);
        const wav = encodeWav([input], 1000);
        expect(new DataView(wav).getUint32(40, true)).toBe(input.length * 2);
        const source = new Blob([wav], { type: 'audio/wav' });
        expect(await gateAudioBlob(source)).toBe(source);
        const context = stubDecode(input);
        context.decodeAudioData.mockRejectedValueOnce(new Error('unsupported audio format'));
        expect(await gateAudioBlob(source, config)).toBe(source);
        expect(context.close).toHaveBeenCalledOnce();
    });
});


describe('clinic recorder take snapshot', () => {
    it('leaves the default take intact and uses the same gated audio for preview and upload when enabled', async () => {
        const input = pcm([...Array(10).fill(0.5), ...Array(200).fill(0), ...Array(10).fill(-0.5)]);
        const source = new Blob([encodeWav([input], 1000)], { type: 'audio/wav' });
        const context = stubDecode(input);
        let onStop;
        const mediaRecorder = { mimeType: 'audio/wav', addEventListener: (name, callback) => { if (name === 'stop') onStop = callback; } };
        const state = {
            isRecording: { value: true }, mediaRecorder: { value: mediaRecorder },
            audioChunks: { value: [source] }, audioBlobURL: { value: URL.createObjectURL(source) },
        };
        const audio = {
            startRecording: vi.fn(async () => {}),
            stopRecording: vi.fn(async () => { state.isRecording.value = false; onStop(); }),
            clearRecording: vi.fn(async () => {}),
        };
        const upload = vi.fn(async () => ({ id: 1 }));
        const recorder = createClinicRecorder(audio, state, upload);
        await recorder.startRecording();
        const originalUrl = state.audioBlobURL.value;
        await recorder.stopRecording();
        expect(state.audioChunks.value).toEqual([source]);
        expect(state.audioBlobURL.value).toBe(originalUrl);
        expect(context.decodeAudioData).not.toHaveBeenCalled();
        state.isRecording.value = true;
        state.audioChunks.value = [source];
        await recorder.startRecording({ voxEnabled: true });
        await recorder.stopRecording();
        expect(state.audioChunks.value).toEqual([source]);
        const previewUrl = state.audioBlobURL.value;
        expect(previewUrl).not.toBe(originalUrl);
        const preview = await (await fetch(previewUrl)).blob();
        expect(preview.size).toBeLessThan(source.size);
        await recorder.uploadRecordedAudio({ title: 'Zweite Aufnahme' });
        const [file] = upload.mock.calls[0];
        expect(file.name).toBe('aufnahme.wav');
        expect(file.type).toBe('audio/wav');
        expect(await file.arrayBuffer()).toEqual(await preview.arrayBuffer());
        URL.revokeObjectURL(previewUrl);
    });
});
