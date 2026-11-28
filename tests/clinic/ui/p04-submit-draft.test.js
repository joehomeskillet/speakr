import { describe, expect, it, vi } from 'vitest';
import { createRecordingViewController } from '../../../static/js/clinic/compose-recording.js';

const id = '636677e5-622e-4fa1-ba8e-18858971edc6';
const path = '/dictations/' + id;
const etag = '"' + id + ':3"';
const detail = { outcome: 'ok', data: { dictation_id: id, etag } };
const sent = { outcome: 'ok', data: { dictation_id: id, status: 'vorbereitung_online' } };
const categorySet = { id: 's1', name: 'Testbereich', version: 3, categories: ['Rot', 'Blau'], required: true };

function categories(sets) {
    return { outcome: 'ok', data: { sets } };
}

function recording(receipt = { dictation_id: id }) {
    const upload = vi.fn().mockResolvedValue(receipt);
    const request = vi.fn();
    const controller = createRecordingViewController({
        currentUser: { id: 1 }, client: { request }, uploadFile: upload,
        recorder: { startRecording: vi.fn(), stopRecording: vi.fn(), uploadRecordedAudio: upload },
    });
    return { controller, upload, request };
}

describe('P04 draft submission', () => {
    it('saves the recording first and submits the selected category with its version', async () => {
        const { controller, upload, request } = recording();
        request.mockResolvedValueOnce(categories([categorySet]));
        await controller.loadCategories();
        controller.setTitle('Synthetische Aufnahme');
        await controller.start();
        await controller.stop();
        expect((await controller.sendRecording()).ok).toBe(true);
        expect(controller.model().message).toContain('Gespeichert');
        expect((await controller.submit()).code).toBe('missing_category');
        expect(controller.model().message).toContain('Testbereich');
        expect(request.mock.calls).toEqual([['/categories']]);

        controller.selectCategory('s1', 'Rot');
        request.mockResolvedValueOnce(detail).mockResolvedValueOnce({ outcome: 'network' }).mockResolvedValueOnce(sent);
        expect((await controller.submit()).ok).toBe(false);
        controller.selectCategory('s1', 'Blau');
        expect((await controller.submit()).ok).toBe(true);
        expect(request.mock.calls[2]).toEqual([path + '/send', {
            method: 'POST', body: { categories: [{ set_id: 's1', version: 3, value: 'Rot' }] },
            ifMatch: etag, idempotencyKey: expect.any(String),
        }]);
        expect(request.mock.calls[3]).toEqual(request.mock.calls[2]);
        expect(upload).toHaveBeenCalledOnce();
    });

    it('refreshes requirements when the server rejects a stale selection', async () => {
        const { controller, request } = recording();
        request.mockResolvedValueOnce(categories([categorySet]));
        await controller.loadCategories();
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
        await controller.sendFile();
        controller.selectCategory('s1', 'Rot');
        request.mockResolvedValueOnce(detail)
            .mockResolvedValueOnce({ outcome: 'conflict', code: 'stale_version' })
            .mockResolvedValueOnce(categories([{ ...categorySet, version: 4, categories: ['Blau'] }]));

        expect((await controller.submit()).code).toBe('stale_version');
        expect(controller.model().categories).toEqual([{ ...categorySet, version: 4, categories: ['Blau'], value: '' }]);
        expect((await controller.submit()).code).toBe('missing_category');
        expect(request.mock.calls[3]).toEqual(['/categories']);
    });

    it('stops locally, saves a draft, then submits the confirmed dictation once', async () => {
        const { controller, upload, request } = recording();
        controller.setTitle('Synthetische Aufnahme');
        await controller.start();
        await controller.stop();
        expect(controller.model().controls.find(c => c.id === 'send').label).toBe('Entwurf sichern');
        expect((await controller.submit()).ok).toBe(false);
        expect(upload).not.toHaveBeenCalled();
        await controller.send();
        expect(controller.model().message).toContain('Gespeichert');
        expect(request).not.toHaveBeenCalled();

        request.mockResolvedValueOnce(detail).mockResolvedValueOnce(sent);
        const pending = controller.submit();
        expect(controller.model().phase).toBe('submitting');
        expect((await controller.submit()).ok).toBe(false);
        expect(controller.another().ok).toBe(false);
        expect((await pending).ok).toBe(true);
        expect(request.mock.calls).toEqual([[path + '/detail'], [path + '/send', {
            method: 'POST', body: {}, ifMatch: etag, idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/),
        }]]);
        expect(controller.model().message).toBe('Eingereicht.');
        expect((await controller.submit()).ok).toBe(false);
        expect(controller.another().ok).toBe(true);
        expect(controller.model().canSubmit).toBe(false);
    });

    it('replays an uncertain file submission with the same version and key, without another upload', async () => {
        const { controller, upload, request } = recording({ id: 19, dictation_id: id });
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
        await controller.sendFile();
        request.mockResolvedValueOnce(detail).mockRejectedValueOnce(new Error('offline'))
            .mockResolvedValueOnce({ outcome: 'ok', data: { ...sent.data, dictation_id: 'other' } })
            .mockResolvedValueOnce(sent);
        expect((await controller.submit()).ok).toBe(false);
        expect(controller.model().message).toContain('nicht bestätigt');
        expect((await controller.submit()).ok).toBe(false);
        expect((await controller.submit()).ok).toBe(true);
        expect(request.mock.calls[1]).toEqual(request.mock.calls[2]);
        expect(request.mock.calls[1]).toEqual(request.mock.calls[3]);
        expect(upload).toHaveBeenCalledOnce();
    });

    it('does not offer another submission after the server confirms this is no longer a draft', async () => {
        const { controller, request } = recording();
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
        await controller.sendFile();
        request.mockResolvedValueOnce(detail).mockResolvedValueOnce({ outcome: 'conflict', code: 'NOT_DRAFT' });
        expect((await controller.submit()).ok).toBe(false);
        expect(controller.model()).toMatchObject({ canSubmit: false, message: expect.stringContaining('kein neuer Entwurf') });
        expect((await controller.submit()).ok).toBe(false);
        expect(request).toHaveBeenCalledTimes(2);
        controller.another();
        controller.selectFile({ name: 'next.wav', size: 30 });
        await controller.sendFile();
        expect(controller.model().canSubmit).toBe(true);
    });

    it('never uses a recording id for a workflow command', async () => {
        const { controller, request } = recording({ id: 19 });
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
        await controller.sendFile();
        expect((await controller.submit()).ok).toBe(false);
        expect(request).not.toHaveBeenCalled();
    });

    it('keeps the draft when routing is refused and stops before POST when the user leaves', async () => {
        const { controller, request } = recording();
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
        await controller.sendFile();
        request.mockResolvedValueOnce(detail)
            .mockResolvedValueOnce({ outcome: 'conflict', code: 'NO_STAFF_POOL' });
        expect((await controller.submit()).ok).toBe(false);
        expect(controller.model()).toMatchObject({
            canSubmit: true, message: 'Kein Schreibkräfte-Pool zugeordnet.', created: { dictationId: id },
        });
        request.mockResolvedValueOnce(detail);
        const retry = controller.submit();
        await controller.dispose();
        expect((await retry).ok).toBe(false);
        expect(request).toHaveBeenCalledTimes(3);
    });
});
