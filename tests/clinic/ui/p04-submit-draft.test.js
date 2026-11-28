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

describe('P04 draft metadata confirmation', () => {
    const initialFields = { title: 'Synthetischer Entwurf', notes: 'Erste Notiz', priority: 128, deadline_date: '2026-12-01' };
    const confirmedEtag = '"' + id + ':4"';
    const lease = { outcome: 'ok', data: { dictation_id: id, lease_id: 'lease-1', etag } };
    const confirmed = { outcome: 'ok', data: { dictation_id: id, etag: confirmedEtag } };
    const uuid = expect.stringMatching(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
    const draftDetail = (processing, editActions = ['rename', 'priority']) => ({
        outcome: 'ok', data: {
            ...detail.data, draft_metadata: initialFields, draft_edit_actions: editActions,
            allowed_actions: ['send'], status_axes: { processing },
        },
    });
    const policy = (required) => ({ outcome: 'ok', data: { acceptance_required: required } });

    function setup() {
        const upload = vi.fn().mockResolvedValue({ dictation_id: id });
        const request = vi.fn();
        const controller = createRecordingViewController({
            draftMetadata: true, currentUser: { id: 1 }, client: { request }, uploadFile: upload,
            recorder: { startRecording: vi.fn(), stopRecording: vi.fn(), uploadRecordedAudio: upload },
        });
        return { controller, upload, request };
    }

    function selectFile(controller) {
        controller.setMode('file');
        controller.selectFile({ name: 'test.wav', size: 20 });
    }

    it('requires confirmed recording metadata and the shared category selection before sending', async () => {
        const { controller, request } = setup();
        request.mockImplementation((url) => ({
            '/categories': categories([categorySet]),
            [path + '/detail']: draftDetail('pending'),
            [path + '/acceptance-policy']: policy(true),
            [path + '/leases']: lease,
            [path + '/metadata']: confirmed,
            [path + '/send']: sent,
        })[url]);

        await controller.loadCategories();
        controller.setTitle('Synthetische Aufnahme');
        await controller.start();
        await controller.stop();
        expect((await controller.sendRecording()).ok).toBe(true);
        expect(controller.model()).toMatchObject({
            canSubmit: false, draft: { fields: initialFields, acceptanceRequired: true },
        });
        expect((await controller.submit()).code).toBe('invalid_phase');

        expect((await controller.saveDraft()).ok).toBe(true);
        expect((await controller.submit()).code).toBe('missing_category');
        expect(request).not.toHaveBeenCalledWith(path + '/send', expect.anything());
        controller.selectCategory('s1', 'Rot');
        expect((await controller.submit()).ok).toBe(true);
        expect(request).toHaveBeenLastCalledWith(path + '/send', {
            method: 'POST', body: { categories: [{ set_id: 's1', version: 3, value: 'Rot' }] },
            ifMatch: confirmedEtag, idempotencyKey: uuid,
        });
    });

    it('loads file draft and policy in parallel, then submits only with the metadata-confirmed ETag', async () => {
        const { controller, upload, request } = setup();
        let resolveDetail;
        let resolveMetadata;
        const pendingDetail = new Promise(resolve => { resolveDetail = resolve; });
        const pendingMetadata = new Promise(resolve => { resolveMetadata = resolve; });
        request.mockImplementation((url) => ({
            [path + '/detail']: pendingDetail,
            [path + '/acceptance-policy']: policy(true),
            [path + '/leases']: lease,
            [path + '/metadata']: pendingMetadata,
            [path + '/send']: sent,
        }[url]));
        selectFile(controller);
        const loading = controller.sendFile();
        await vi.waitFor(() => {
            expect(request).toHaveBeenCalledWith(path + '/detail');
            expect(request).toHaveBeenCalledWith(path + '/acceptance-policy');
        });
        resolveDetail(draftDetail('pending'));
        await loading;
        expect(controller.model()).toMatchObject({
            created: { dictationId: id }, canSubmit: false,
            draft: { fields: initialFields, acceptanceRequired: true, saving: false, saved: false },
        });
        expect((await controller.submit()).ok).toBe(false);
        expect(request).toHaveBeenCalledTimes(2);

        const fields = { title: 'Neuer Titel', notes: 'Neue Notiz', priority: 0, deadline_date: '2026-12-02' };
        for (const [field, value] of Object.entries(fields)) controller.setDraftField(field, value);
        expect(controller.model().draft.fields).toEqual(fields);
        const saving = controller.saveDraft();
        await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(4));
        expect(controller.model()).toMatchObject({ canSubmit: false, draft: { saving: true, saved: false } });
        expect((await controller.submit()).ok).toBe(false);
        resolveMetadata(confirmed);
        expect((await saving).ok).toBe(true);
        expect(controller.model()).toMatchObject({ canSubmit: true, draft: { saving: false, saved: true } });
        expect((await controller.submit()).ok).toBe(true);
        expect(request.mock.calls.slice(2)).toEqual([
            [path + '/leases', expect.objectContaining({ method: 'POST', body: {} })],
            [path + '/metadata', expect.objectContaining({
                method: 'POST', body: { fields: { ...fields, deadline_time: null } }, ifMatch: etag, idempotencyKey: uuid,
            })],
            [path + '/send', expect.objectContaining({ method: 'POST', body: {}, ifMatch: confirmedEtag, idempotencyKey: uuid })],
        ]);
        expect(upload).toHaveBeenCalledOnce();
    });

    it('loads policy after recording upload and relocks submission synchronously on a new edit', async () => {
        const { controller, request } = setup();
        request.mockImplementation((url) => ({
            [path + '/detail']: draftDetail('succeeded'),
            [path + '/acceptance-policy']: policy(false),
            [path + '/leases']: lease,
            [path + '/metadata']: confirmed,
        }[url]));
        controller.setTitle(initialFields.title);
        await controller.start();
        await controller.stop();
        await controller.sendRecording();
        expect(request).toHaveBeenCalledTimes(2);
        expect(request).toHaveBeenCalledWith(path + '/detail');
        expect(request).toHaveBeenCalledWith(path + '/acceptance-policy');
        expect(controller.model()).toMatchObject({ canSubmit: false, draft: { acceptanceRequired: false } });
        controller.setDraftField('deadline_date', '');
        expect((await controller.saveDraft()).ok).toBe(true);
        expect(request).toHaveBeenLastCalledWith(path + '/metadata', expect.objectContaining({
            body: { fields: { ...initialFields, deadline_date: null, deadline_time: null } },
        }));
        expect(controller.model().canSubmit).toBe(true);
        controller.setDraftField('notes', 'Nachträgliche Änderung');
        expect(controller.model()).toMatchObject({
            canSubmit: false, draft: { saved: false, fields: { notes: 'Nachträgliche Änderung' } },
        });
        expect((await controller.submit()).ok).toBe(false);
        expect(request).toHaveBeenCalledTimes(4);
    });

    it.each(['LEASE_STALE_GENERATION', 'STALE_VERSION'])('keeps the draft and blocks submit on %s', async (code) => {
        const { controller, upload, request } = setup();
        request.mockImplementation((url) => ({
            [path + '/detail']: draftDetail('succeeded'),
            [path + '/acceptance-policy']: policy(true),
            [path + '/leases']: lease,
            [path + '/metadata']: { outcome: 'conflict', code },
        }[url]));
        selectFile(controller);
        await controller.sendFile();
        const fields = { ...initialFields, notes: 'Ungesicherte Notiz' };
        controller.setDraftField('notes', fields.notes);
        expect((await controller.saveDraft()).ok).toBe(false);
        expect(controller.model()).toMatchObject({
            created: { dictationId: id }, canSubmit: false,
            draft: { fields, acceptanceRequired: true, saving: false, saved: false },
        });
        expect((await controller.submit()).ok).toBe(false);
        expect(request.mock.calls.slice(2).map(([url]) => url)).toEqual([path + '/leases', path + '/metadata']);
        expect(upload).toHaveBeenCalledOnce();
    });

    it.each([
        { actions: ['rename'], fields: { title: 'Neuer Titel', notes: initialFields.notes } },
        { actions: ['priority'], fields: { priority: 128, deadline_date: '2026-12-01', deadline_time: null } },
        { actions: [], fields: null },
    ])('confirms a saved draft with edit actions $actions', async ({ actions, fields }) => {
        const { controller, request } = setup();
        request.mockImplementation((url) => ({
            [path + '/detail']: draftDetail('pending', actions),
            [path + '/acceptance-policy']: policy(false),
            [path + '/leases']: lease,
            [path + '/metadata']: confirmed,
            [path + '/send']: sent,
        }[url]));
        selectFile(controller);
        await controller.sendFile();
        controller.setDraftField('title', 'Neuer Titel');
        expect(controller.model().draft.fields.title).toBe(fields?.title || initialFields.title);
        expect(controller.model().canSubmit).toBe(fields === null);
        expect((await controller.saveDraft()).ok).toBe(fields !== null);
        if (fields) {
            expect(request).toHaveBeenLastCalledWith(path + '/metadata', expect.objectContaining({
                body: { fields }, ifMatch: etag,
            }));
        } else {
            expect(request).toHaveBeenCalledTimes(2);
        }
        expect((await controller.submit()).ok).toBe(true);
        expect(request).toHaveBeenLastCalledWith(path + '/send', expect.objectContaining({
            ifMatch: fields ? confirmedEtag : etag,
        }));
    });
});
