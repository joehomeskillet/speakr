import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClinicRecorder } from '../../../static/js/clinic/recorder-phases-ui.js';

function stoppedTake(offlineDrafts) {
    const state = {
        audioChunks: { value: [new Blob(['first']), new Blob(['last'])] },
        audioBlobURL: { value: 'blob:synthetic' },
        mediaRecorder: { value: { mimeType: 'audio/webm;codecs=opus' } },
        isRecording: { value: false },
    };
    const stock = { uploadRecordedAudio: vi.fn(), discardRecording: vi.fn(), clearRecording: vi.fn() };
    const upload = vi.fn();
    return { recorder: createClinicRecorder(stock, state, upload, offlineDrafts), state, stock, upload };
}

afterEach(() => vi.unstubAllGlobals());

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

    it('keeps a new recording when the previous upload finishes', async () => {
        const { recorder, state, stock, upload } = stoppedTake();
        upload.mockResolvedValue({ id: 1 });
        const pending = recorder.uploadRecordedAudio({ title: 'Erste Aufnahme' });
        state.audioChunks.value = [];
        expect(await pending).toEqual({ id: 1 });
        expect(stock.clearRecording).not.toHaveBeenCalled();
    });

    it('retains an approved offline take as unsent until a retry returns a receipt', async () => {
        const drafts = { saveTake: vi.fn(async () => true), removeTake: vi.fn() };
        const { recorder, stock, upload } = stoppedTake(() => drafts);
        vi.stubGlobal('navigator', { onLine: false });
        upload.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({}).mockResolvedValueOnce({ id: 1 });

        await expect(recorder.uploadRecordedAudio({ title: 'Test' })).rejects.toThrow('offline_retained');
        const [binding, file, title] = drafts.saveTake.mock.calls[0];
        expect(await file.text()).toBe('firstlast');
        expect(title).toBe('Test');
        expect(drafts.removeTake).not.toHaveBeenCalled();
        expect(stock.clearRecording).not.toHaveBeenCalled();

        vi.stubGlobal('navigator', { onLine: true });
        await expect(recorder.uploadRecordedAudio({ title: 'Test' })).rejects.toThrow('offline_retained');
        expect(drafts.removeTake).not.toHaveBeenCalled();
        expect(await recorder.uploadRecordedAudio({ title: 'Test' })).toEqual({ id: 1 });
        expect(drafts.removeTake).toHaveBeenCalledOnce();
        expect(drafts.removeTake).toHaveBeenCalledWith(binding);
        expect(stock.clearRecording).toHaveBeenCalledOnce();
    });
});
