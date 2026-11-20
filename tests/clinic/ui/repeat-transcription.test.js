import { afterEach, describe, expect, it, vi } from 'vitest';
import { activeRepeatSession, createRepeatTranscription, REPEAT_TEXT } from '../../../static/js/clinic/repeat-transcription-ui.js';
import { useReprocess } from '../../../static/js/modules/composables/reprocess.js';

const ref = (value) => ({ value });
const sessions = [];
afterEach(() => { sessions.splice(0).forEach((s) => s.dispose()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function fixture(response = { ok: true, json: async () => ({ success: true, queue_position: 3 }) }) {
    const editor = {
        recordingId: ref(42), textRevisionId: ref('revision'), audio: ref({ status: 'ready' }),
        canEdit: ref(true), dirty: ref(false), lease: ref({ writable: true, generation: 7 }),
    };
    const fetchFn = vi.fn(async () => response);
    const csrf = vi.fn(async () => 'csrf');
    const session = createRepeatTranscription({ editor, ref, fetchFn, csrf });
    sessions.push(session);
    return { editor, fetchFn, csrf, session };
}
describe('explicit repeat with the active editor proof', () => {
    it('posts the current generation once and reports only queue acceptance', async () => {
        const { session, editor, fetchFn, csrf } = fixture();
        csrf.mockImplementation(async () => { editor.lease.value.generation = 12; return 'csrf'; });
        session.open();
        expect(fetchFn).not.toHaveBeenCalled();
        expect((await session.submit()).ok).toBe(true);
        expect(fetchFn.mock.calls[0][0]).toBe('/recording/42/reprocess_transcription');
        expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ lease_generation: 12 });
        expect(session.state.value.message).toBe(REPEAT_TEXT.queued + ' Position: 3.');
        await session.submit();
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });
    it.each([400, 403, 409, 423, 503])('never reports queued on HTTP %i', async (status) => {
        const { session } = fixture({ ok: false, status, json: async () => ({ error: 'refused' }) });
        expect((await session.submit()).ok).toBe(false);
        expect(session.state.value.phase).toBe('error');
        expect(session.state.value.message).not.toContain('Warteschlange hinzugefügt');
    });
    it('keeps malformed success and network outcomes unknown, without retrying', async () => {
        const { session, fetchFn } = fixture({ ok: true, json: async () => ({ success: false }) });
        await session.submit();
        expect(session.state.value.phase).toBe('unknown');
        await session.submit();
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });
    it('does not retry when transport fails after submission', async () => {
        const { session, fetchFn } = fixture();
        fetchFn.mockRejectedValue(new TypeError('offline'));
        await session.submit();
        expect(session.state.value.phase).toBe('unknown');
        await session.submit();
        expect(fetchFn).toHaveBeenCalledTimes(1);
    });
    it.each(['readOnly', 'dirty', 'missingAudio', 'initial', 'missingId', 'lostLease'])('blocks %s without a POST', async (reason) => {
        const { session, editor, fetchFn } = fixture();
        if (reason === 'readOnly') editor.canEdit.value = false;
        if (reason === 'dirty') editor.dirty.value = true;
        if (reason === 'missingAudio') editor.audio.value.status = 'unavailable';
        if (reason === 'initial') editor.textRevisionId.value = null;
        if (reason === 'missingId') editor.recordingId.value = null;
        if (reason === 'lostLease') editor.lease.value.writable = false;
        session.open();
        expect(session.state.value.confirming).toBe(false);
        expect((await session.submit()).ok).toBe(false);
        expect(fetchFn).not.toHaveBeenCalled();
    });
    it('checks lease loss during CSRF lookup and removes proof on disposal', async () => {
        const { session, editor, csrf, fetchFn } = fixture();
        expect(activeRepeatSession(42)).toBe(session);
        expect(activeRepeatSession(43)).toBe(null);
        csrf.mockImplementation(async () => { editor.lease.value.writable = false; return 'csrf'; });
        await session.submit();
        expect(fetchFn).not.toHaveBeenCalled();
        session.dispose();
        expect(activeRepeatSession(42)).toBe(null);
    });
});

function legacy(clinic) {
    vi.stubGlobal('Vue', { nextTick: async () => {} });
    vi.stubGlobal('document', {
        getElementById: () => ({ dataset: { clinicMode: clinic ? 'True' : 'False' } }),
        querySelector: () => ({ getAttribute: () => 'csrf' }),
    });
    const state = { showReprocessModal: ref(false), reprocessType: ref(null), reprocessRecording: ref(null),
        selectedRecording: ref({ id: 42 }), recordings: ref([{ id: 42, status: 'COMPLETED' }]),
        asrReprocessOptions: {}, userTranscriptionLanguage: ref('de') };
    const utils = { showToast: vi.fn(), setGlobalError: vi.fn(), t: (key, params) => params?.error || key };
    return { state, utils, c: useReprocess(state, utils) };
}
describe('legacy reprocess dialog', () => {
    it.each([400, 403, 409, 423, 503])('does not toast, poll or change status after HTTP %i', async (status) => {
        fixture();
        vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'refused' }), { status })));
        const poll = vi.spyOn(globalThis, 'setInterval');
        const { c, state, utils } = legacy(true);
        await c.openReprocessModal('transcription');
        await c.executeReprocess();
        expect(utils.showToast).not.toHaveBeenCalled();
        expect(utils.setGlobalError).toHaveBeenCalledWith('refused');
        expect(state.recordings.value[0].status).toBe('COMPLETED');
        expect(poll).not.toHaveBeenCalled();
    });
    it('adds only the matching current editor proof and preserves chosen options', async () => {
        const { editor, fetchFn: repeatFetch } = fixture();
        const fetchFn = vi.fn(async () => new Response('{"success":true}'));
        vi.stubGlobal('fetch', fetchFn);
        vi.spyOn(globalThis, 'setInterval').mockReturnValue(1);
        vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => {});
        const { c, state, utils } = legacy(true);
        await c.openReprocessModal('transcription');
        expect(state.showReprocessModal.value).toBe(true);
        Object.assign(state.asrReprocessOptions, { language: 'fr', hotwords: '  Hint  ', initial_prompt: ' Prompt ',
            min_speakers: 1, max_speakers: 2, transcription_model: 'local' });
        editor.lease.value.generation = 12;
        await c.executeReprocess();
        expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ lease_generation: 12, language: 'fr',
            hotwords: 'Hint', initial_prompt: 'Prompt', min_speakers: 1, max_speakers: 2, transcription_model: 'local' });
        expect(repeatFetch).not.toHaveBeenCalled();
        expect(utils.showToast).toHaveBeenCalled();
        expect(state.recordings.value[0].status).toBe('PROCESSING');
        c.stopReprocessingPoll(42);
    });
    it.each(['invalid_lease_generation', 'stale_generation', 'expired', 'foreign_holder'])('gives actionable German advice for %s', async (code) => {
        const { c, state, utils } = legacy(true);
        const fetchFn = vi.fn(async () => new Response(JSON.stringify({ code, error: 'lease refused' }), { status: 400 }));
        vi.stubGlobal('fetch', fetchFn);
        await c.openReprocessModal('transcription');
        expect(state.showReprocessModal.value).toBe(true);
        await c.reprocessTranscription(42);
        expect(utils.setGlobalError).toHaveBeenCalledWith(REPEAT_TEXT.leaseRequired);
        expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ language: '' });
        expect(utils.showToast).not.toHaveBeenCalled();
        expect(state.recordings.value[0].status).toBe('COMPLETED');
    });
    it.each([false, true])('preserves standalone options and polling, clinic=%s', async (clinic) => {
        const { c, state, utils } = legacy(clinic);
        vi.spyOn(globalThis, 'setInterval').mockReturnValue(1);
        vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => {});
        const fetchFn = vi.fn(async () => ({ ok: true, text: async () => '{"success":true}' }));
        vi.stubGlobal('fetch', fetchFn);
        await c.reprocessTranscription(42, 'de', 1, 2, 'Hint', 'Prompt', 'local');
        expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ language: 'de', min_speakers: 1,
            max_speakers: 2, hotwords: 'Hint', initial_prompt: 'Prompt', transcription_model: 'local' });
        expect(state.recordings.value[0].status).toBe('PROCESSING');
        expect(utils.showToast).toHaveBeenCalled();
        c.stopReprocessingPoll(42);
    });
    it.each(['otherRecording', 'lostLease', 'dirty', 'disposed', 'nonclinic'])('does not attach a proof for %s', async (reason) => {
        const { session, editor } = fixture();
        if (reason === 'otherRecording') editor.recordingId.value = 43;
        if (reason === 'lostLease') editor.lease.value.writable = false;
        if (reason === 'dirty') editor.dirty.value = true;
        if (reason === 'disposed') session.dispose();
        const { c } = legacy(reason !== 'nonclinic');
        const fetchFn = vi.fn(async () => new Response('{"error":"refused"}', { status: 400 }));
        vi.stubGlobal('fetch', fetchFn);
        await c.reprocessTranscription(42, 'de');
        expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toEqual({ language: 'de' });
    });
    it.each(['network', 'html'])('keeps %s failures truthful without retrying', async (failure) => {
        const { c, state, utils } = legacy(true);
        const fetchFn = vi.fn(async () => {
            if (failure === 'network') throw new TypeError('offline');
            return new Response('<html>unavailable</html>', { status: 503 });
        });
        vi.stubGlobal('fetch', fetchFn);
        await c.reprocessTranscription(42, 'de');
        expect(fetchFn).toHaveBeenCalledTimes(1);
        expect(utils.setGlobalError).toHaveBeenCalled();
        expect(utils.showToast).not.toHaveBeenCalled();
        expect(state.recordings.value[0].status).toBe('COMPLETED');
    });
    it.each([false, true])('keeps saved hints and cancel semantics, clinic=%s', async (clinic) => {
        const { c, state } = legacy(clinic);
        const fetchFn = vi.fn(); vi.stubGlobal('fetch', fetchFn);
        state.selectedRecording.value = { id: 42, resolved_hotwords: 'Saved hint', resolved_initial_prompt: 'Saved prompt' };
        await c.openReprocessModal('transcription');
        expect(state.asrReprocessOptions).toEqual({ language: 'de', hotwords: 'Saved hint', initial_prompt: 'Saved prompt',
            min_speakers: '', max_speakers: '', transcription_model: '' });
        c.cancelReprocess();
        await c.executeReprocess();
        expect(fetchFn).not.toHaveBeenCalled();
    });
});
