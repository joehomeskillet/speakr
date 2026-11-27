import { describe, expect, it, vi } from 'vitest';
import { createMetadataEditor } from '../../../static/js/clinic/metadata-edit-ui.js';

const original = { title: 'Original', notes: 'Alt', priority: 1, deadline_date: '2026-11-30', deadline_time: '13:45:30' };
const readPath = '/dictations/d1/detail/metadata';
const writePath = '/dictations/d1/metadata';
const ok = (data) => ({ outcome: 'ok', data });

function setup(fields = original) {
    const reply = { outcome: 'ok', data: { dictation_id: 'd1', etag: '"d1:4"', fields } };
    const request = vi.fn().mockResolvedValue(reply);
    const newKey = vi.fn(() => 'receipt');
    const editor = createMetadataEditor({ client: { request }, dictationId: 'd1',
        hasLease: () => true, newKey });
    return { editor, request, reply, newKey };
}

describe('editing dictation details', () => {
    it('discards the draft and reopens the server values without writing', async () => {
        const { editor, request } = setup();
        await editor.open();
        editor.state.draft.title = 'Verwerfen';
        editor.state.draft.deadline_date = '';
        editor.cancel();
        await editor.open();
        expect(editor.state.draft).toEqual(original);
        expect(request.mock.calls).toEqual([[readPath], [readPath]]);
    });

    it('saves just the changed fields with the read version and displays the reread values', async () => {
        const { editor, request } = setup();
        await editor.open();
        const saved = { title: 'Geänderter Titel', notes: '', priority: 0, deadline_date: null, deadline_time: null };
        Object.assign(editor.state.draft, { ...saved, title: '  Gea\u0308nderter Titel  ', deadline_date: '' });
        request.mockResolvedValueOnce(ok({ dictation_id: 'd1', etag: '"d1:5"' }))
            .mockResolvedValueOnce(ok({ dictation_id: 'd1', etag: '"d1:5"', fields: saved }));
        expect(await editor.save()).toBe(true);
        expect(request).toHaveBeenNthCalledWith(2, writePath, {
            method: 'POST', ifMatch: '"d1:4"', idempotencyKey: 'receipt', body: { fields: saved },
        });
        expect(editor.state.saved).toEqual(saved);
        expect(editor.state.draft).toBeNull();
    });

    it('preserves existing deadline seconds and sends only a changed date for a deadline-only grant', async () => {
        const fields = { priority: 128, deadline_date: '2026-11-30', deadline_time: '13:45:30' };
        const { editor, request, reply } = setup(fields);
        await editor.open();
        expect(editor.state.draft).toEqual(fields);
        editor.state.draft.deadline_date = '2026-12-01';
        request.mockResolvedValueOnce(ok({ dictation_id: 'd1', etag: '"d1:5"' }))
            .mockResolvedValueOnce(ok({ ...reply.data, fields: { ...fields, deadline_date: '2026-12-01' } }));
        await editor.save();
        expect(request.mock.calls[1][1].body).toEqual({ fields: { deadline_date: '2026-12-01' } });
    });

    it('reuses the receipt after a lost response and preserves the draft on a conflict', async () => {
        const { editor, request, newKey } = setup();
        await editor.open();
        editor.state.draft.title = 'Mein Entwurf';
        request.mockResolvedValueOnce({ outcome: 'network' }).mockResolvedValueOnce({ outcome: 'conflict' });
        expect(await editor.save()).toBe(false);
        expect(await editor.save()).toBe(false);
        expect(editor.state.draft.title).toBe('Mein Entwurf');
        expect(editor.canSave()).toBe(false);
        expect(request.mock.calls[1]).toEqual(request.mock.calls[2]);
        expect(newKey).toHaveBeenCalledTimes(1);
    });
});
