import { afterEach, describe, expect, it, vi } from 'vitest';
import { createClinicRecorder, uploadClinicFile } from '../../../static/js/clinic/recorder-phases-ui.js';

afterEach(() => vi.unstubAllGlobals());

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

    it('keeps a new recording when the previous upload finishes', async () => {
        const { recorder, state, stock, upload } = stoppedTake();
        upload.mockResolvedValue({ id: 1 });
        const pending = recorder.uploadRecordedAudio({ title: 'Erste Aufnahme' });
        state.audioChunks.value = [];
        expect(await pending).toEqual({ id: 1 });
        expect(stock.clearRecording).not.toHaveBeenCalled();
    });
});

describe('clinic upload transport', () => {
    it.each(['error', 'abort', 'timeout'])('rejects %s instead of waiting for a receipt', async (event) => {
        const refreshToken = vi.fn(async () => 'synthetic-csrf');
        vi.stubGlobal('window', { csrfManager: { refreshToken } });
        vi.stubGlobal('XMLHttpRequest', class {
            open() {}
            setRequestHeader() {}
            send() { this['on' + event](); }
        });
        await expect(uploadClinicFile(new File(['test'], 'diktat.wav'))).rejects.toThrow('upload_failed');
        expect(refreshToken).toHaveBeenCalledOnce();
    });

    it('rejects a successful HTTP response with malformed receipt JSON', async () => {
        vi.stubGlobal('window', { csrfManager: { refreshToken: async () => 'synthetic-csrf' } });
        vi.stubGlobal('XMLHttpRequest', class {
            status = 202;
            responseText = '<html>nicht bestätigt</html>';
            open() {}
            setRequestHeader() {}
            send() { this.onload(); }
        });
        await expect(uploadClinicFile(new File(['test'], 'diktat.wav'))).rejects.toThrow(SyntaxError);
    });
});
