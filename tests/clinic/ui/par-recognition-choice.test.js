import { describe, expect, it, vi } from 'vitest';
import { createRecordingViewController } from '../../../static/js/clinic/compose-recording.js';
import { createClinicRecorder } from '../../../static/js/clinic/recorder-phases-ui.js';

const receipt = { dictation_id: 'synthetic-draft' };
const audio = new File(['synthetic'], 'diktat.wav', { type: 'audio/wav' });

function compose() {
    const uploadFile = vi.fn().mockResolvedValue(receipt);
    const state = {
        audioChunks: { value: [audio] },
        audioBlobURL: { value: 'blob:synthetic' },
        mediaRecorder: { value: { mimeType: 'audio/webm' } },
        isRecording: { value: false },
    };
    const recorder = createClinicRecorder({ startRecording: vi.fn(), clearRecording: vi.fn() }, state, uploadFile);
    const client = { request: vi.fn(), switchContext: vi.fn() };
    const c = createRecordingViewController({
        client, currentUser: { id: 4 }, recorder, uploadFile, oneButton: true, preflightOnMount: true,
        fetchPreflight: async () => new Response('{"routable":true}'),
    });
    return { c, uploadFile, client };
}

describe('P04 speech recognition choice', () => {
    it('keeps the microphone opt-out on upload retry and sends the draft without approval overrides', async () => {
        const { c, uploadFile, client } = compose();
        uploadFile.mockRejectedValueOnce(new Error('offline'));
        client.request
            .mockResolvedValueOnce({ outcome: 'ok', data: { ...receipt, etag: '"v1"' } })
            .mockRejectedValueOnce(new Error('response lost'))
            .mockResolvedValueOnce({ outcome: 'ok', data: { ...receipt, status: 'zu_schreiben' } });

        expect(c.model().useSpeechRecognition).toBe(true);
        await c.start();
        await c.stop();
        expect(c.toggleSpeechRecognition().ok).toBe(true);
        expect((await c.sendRecording()).code).toBe('no_receipt');
        expect(c.model().useSpeechRecognition).toBe(false);
        expect(c.toggleSpeechRecognition().ok).toBe(false);
        expect((await c.sendRecording()).code).toBe('unconfirmed');
        expect(c.model().useSpeechRecognition).toBe(false);
        expect((await c.sendRecording()).ok).toBe(true);

        expect(uploadFile).toHaveBeenCalledTimes(2);
        const [, , firstOptions] = uploadFile.mock.calls[0];
        expect(firstOptions).toEqual({ useSpeechRecognition: false, clientUploadId: expect.any(String) });
        expect(uploadFile.mock.calls[1][2]).toEqual(firstOptions);
        expect(client.request).toHaveBeenNthCalledWith(2, '/dictations/synthetic-draft/send', {
            method: 'POST', body: {}, ifMatch: '"v1"', idempotencyKey: expect.any(String),
        });
        expect(client.request.mock.calls[2]).toEqual(client.request.mock.calls[1]);
        expect(c.another().ok).toBe(true);
        expect(c.model().useSpeechRecognition).toBe(true);
    });

    it('keeps the uploaded-file opt-out visible at Einreichen and starts another draft with ASR on', async () => {
        const { c, uploadFile } = compose();
        uploadFile.mockRejectedValueOnce(new Error('offline'));
        expect(c.setMode('file').ok).toBe(true);
        expect(c.toggleSpeechRecognition().ok).toBe(true);
        expect(c.selectFile(audio).ok).toBe(true);
        expect((await c.sendFile()).code).toBe('no_receipt');
        expect(c.model().useSpeechRecognition).toBe(false);
        expect((await c.sendFile()).ok).toBe(true);
        expect(c.model()).toMatchObject({ canSubmit: true, useSpeechRecognition: false });
        expect(c.toggleSpeechRecognition().ok).toBe(false);

        expect(c.another().ok).toBe(true);
        expect(c.selectFile(audio).ok).toBe(true);
        expect((await c.sendFile()).ok).toBe(true);
        expect(uploadFile).toHaveBeenNthCalledWith(1, audio, undefined, {
            useSpeechRecognition: false, clientUploadId: expect.any(String),
        });
        expect(uploadFile.mock.calls[1]).toEqual(uploadFile.mock.calls[0]);
        expect(uploadFile).toHaveBeenNthCalledWith(3, audio, undefined, {
            useSpeechRecognition: true, clientUploadId: expect.any(String),
        });
        expect(uploadFile.mock.calls[2][2].clientUploadId).not.toBe(uploadFile.mock.calls[0][2].clientUploadId);
    });
});
