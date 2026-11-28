import { describe, expect, it, vi } from 'vitest';
import { createClinicRecorder } from '../../../static/js/clinic/recorder-phases-ui.js';

function stoppedTake() {
    const state = {
        audioChunks: { value: [new Blob(['first']), new Blob(['last'])] },
        audioBlobURL: { value: 'blob:synthetic' },
        mediaRecorder: { value: { mimeType: 'audio/webm;codecs=opus' } },
        isRecording: { value: false },
    };
    const stock = { uploadRecordedAudio: vi.fn(), discardRecording: vi.fn(), clearRecording: vi.fn() };
    const upload = vi.fn();
    return { recorder: createClinicRecorder(stock, state, upload), state, stock, upload };
}

describe('clinic recorder upload', () => {
    it('sends every recorded chunk in one file and returns the original receipt', async () => {
        const { recorder, stock, upload } = stoppedTake();
        const receipt = { id: 42, dictation_id: 'synthetic-dictation' };
        upload.mockResolvedValue(receipt);
        expect(await recorder.uploadRecordedAudio({ title: 'Testaufnahme' })).toBe(receipt);
        expect(upload).toHaveBeenCalledTimes(1);
        const [file, title] = upload.mock.calls[0];
        expect(file).toBeInstanceOf(File);
        expect(file.name).toMatch(/\.webm$/);
        expect(file.type).toBe('audio/webm');
        expect(await file.text()).toBe('firstlast');
        expect(title).toBe('Testaufnahme');
        expect(stock.clearRecording).toHaveBeenCalledOnce();
        expect(stock.uploadRecordedAudio).not.toHaveBeenCalled();
        expect(stock.discardRecording).not.toHaveBeenCalled();
    });

    it('rejects a failed request or missing receipt and keeps the take for retry', async () => {
        const { recorder, stock, upload } = stoppedTake();
        upload.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({});
        await expect(recorder.uploadRecordedAudio({ title: 'Test' })).rejects.toThrow('offline');
        await expect(recorder.uploadRecordedAudio({ title: 'Test' })).rejects.toThrow('no_receipt');
        expect(stock.clearRecording).not.toHaveBeenCalled();
        expect(stock.uploadRecordedAudio).not.toHaveBeenCalled();
        expect(stock.discardRecording).not.toHaveBeenCalled();
    });

    it('keeps the original title, type and take ID across upload retries', async () => {
        const { recorder, stock, upload } = stoppedTake();
        const receipt = { id: 42, dictation_id: 'synthetic-dictation', recording_type: 'notiz' };
        upload.mockResolvedValueOnce({ id: 42 }).mockResolvedValueOnce(receipt);
        await expect(recorder.uploadRecordedAudio({ title: 'Original', recording_type: 'notiz' }))
            .rejects.toThrow('no_receipt');
        expect(stock.clearRecording).not.toHaveBeenCalled();
        await expect(recorder.uploadRecordedAudio({ title: 'Edited', recording_type: 'bericht' }))
            .resolves.toEqual(receipt);
        expect(stock.clearRecording).toHaveBeenCalledOnce();
        const [, title, metadata] = upload.mock.calls[0];
        expect(title).toBe('Original');
        expect(metadata).toEqual({ id: expect.any(String), retry: false, recording_type: 'notiz' });
        expect(upload).toHaveBeenLastCalledWith(expect.any(File), title, { ...metadata, retry: true });
    });

    it('keeps a new recording when the previous upload finishes', async () => {
        const { recorder, state, stock, upload } = stoppedTake();
        upload.mockResolvedValue({ id: 1 });
        const pending = recorder.uploadRecordedAudio({ title: 'Erste Aufnahme' });
        state.audioChunks.value = [];
        expect(await pending).toEqual({ id: 1 });
        expect(stock.clearRecording).not.toHaveBeenCalled();
    });
});
