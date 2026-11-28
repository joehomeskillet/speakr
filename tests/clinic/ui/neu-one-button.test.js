import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRecordingViewController } from '../../../static/js/clinic/compose-recording.js';

const id = '636677e5-622e-4fa1-ba8e-18858971edc6';
const path = '/dictations/' + id;
function setup(routable = true) {
    const request = vi.fn();
    const fetchPreflight = vi.fn().mockResolvedValue(new Response(JSON.stringify({ routable })));
    const recorder = {
        startRecording: vi.fn(), stopRecording: vi.fn(), discardRecording: vi.fn(),
        uploadRecordedAudio: vi.fn().mockResolvedValue({ dictation_id: id }),
    };
    const controller = createRecordingViewController({
        oneButton: true, currentUser: { id: 1 }, client: { request }, recorder, uploadFile: vi.fn(), fetchPreflight,
    });
    return { controller, request, recorder, fetchPreflight };
}

afterEach(() => vi.useRealTimers());

describe('Neue Aufnahme', () => {
    it('waits for the routing check and refuses capture without a route', async () => {
        const { controller, fetchPreflight, recorder } = setup(false);
        expect((await controller.start()).ok).toBe(false);
        await controller.checkRouting();
        expect(fetchPreflight).toHaveBeenCalledWith('/api/config?recording_preflight=1', { cache: 'no-store' });
        expect(controller.model().message).toBe('Ihr Konto ist noch keinem Autorenpool zugeordnet. Bitte wenden Sie sich an die Verwaltung.');
        expect((await controller.start()).ok).toBe(false);
        expect(recorder.startRecording).not.toHaveBeenCalled();
    });

    it('generates a local title, sends in one action and retries submission without re-uploading', async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date(2026, 10, 27, 9, 3));
        const { controller, request, recorder } = setup();
        request.mockResolvedValueOnce({ outcome: 'ok', data: { dictation_id: id, etag: '"1"' } })
            .mockResolvedValueOnce({ outcome: 'network' })
            .mockResolvedValueOnce({ outcome: 'ok', data: { dictation_id: id, status: 'vorbereitung_online' } });
        await controller.checkRouting();
        expect((await controller.start()).ok).toBe(true);
        await controller.stop();
        expect((await controller.sendRecording()).ok).toBe(false);
        expect(controller.model().phase).not.toBe('submitted');
        expect((await controller.sendRecording()).ok).toBe(true);
        expect(controller.model().phase).toBe('submitted');
        expect(recorder.uploadRecordedAudio.mock.calls).toEqual([[{
            title: 'Diktat 27.11.2026 09:03',
            useSpeechRecognition: true,
            clientUploadId: expect.stringMatching(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i),
        }]]);
        expect(request.mock.calls[1]).toEqual([path + '/send', {
            method: 'POST', body: {}, ifMatch: '"1"', idempotencyKey: expect.any(String),
        }]);
        expect(request.mock.calls[2]).toEqual(request.mock.calls[1]);
    });

    it('blocks a second start while waiting for microphone permission and stays ready if denied', async () => {
        const { controller, recorder } = setup();
        let deny;
        recorder.startRecording.mockImplementationOnce(() => new Promise((_resolve, reject) => { deny = reject; }));
        await controller.checkRouting();
        const start = controller.start();
        expect((await controller.start()).ok).toBe(false);
        deny(new DOMException('Permission denied', 'NotAllowedError'));
        expect((await start).code).toBe('microphone_denied');
        expect(controller.model().message).toContain('Website-Einstellungen Ihres Browsers');
        expect(recorder.startRecording).toHaveBeenCalledOnce();
    });
});
