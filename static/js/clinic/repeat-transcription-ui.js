/** Explicit repeat from the active editor. Lease proofs never leave memory. */
import { getUploadCsrfToken } from '../modules/csrf.js';

const sessions = new Set();
export const REPEAT_TEXT = Object.freeze({
    title: 'Erneut transkribieren',
    confirm: 'Erneute Transkription anfordern',
    explanation: 'Die Aufnahme wird erneut erkannt. Ein neues Ergebnis liegt erst nach erfolgreicher Verarbeitung vor.',
    unavailable: 'Zum Wiederholen ein bestehendes Transkript mit Audio öffnen, Bearbeiten wählen und Änderungen speichern.',
    leaseRequired: 'Das verknüpfte Diktat im Schreibplatz öffnen, „Bearbeiten“ wählen und Änderungen speichern. Danach die erneute Transkription anfordern.',
    queued: 'Erneute Transkription zur Warteschlange hinzugefügt.',
    unknown: 'Das Ergebnis ist unbekannt. Bitte zuerst den Verarbeitungsstatus prüfen.',
    denied: 'Die erneute Transkription wurde abgelehnt. Bitte Berechtigung und Bearbeitungssperre prüfen.',
    failed: 'Die erneute Transkription wurde nicht angenommen. Bitte den aktuellen Stand laden.',
});

export function activeRepeatSession(recordingId) {
    return [...sessions].find((session) => session.recordingId() === recordingId) || null;
}

export function createRepeatTranscription({ editor, ref, fetchFn = (...args) => fetch(...args), csrf = getUploadCsrfToken }) {
    const state = ref({ phase: 'idle', message: '', confirming: false });
    let disposed = false;
    const recordingId = () => editor.recordingId.value;
    function eligible() {
        const lease = editor.lease.value;
        return !disposed && editor.canEdit.value && lease.writable
            && Number.isSafeInteger(lease.generation) && lease.generation > 0
            && Number.isSafeInteger(recordingId()) && recordingId() > 0
            && editor.audio.value.status === 'ready' && Boolean(editor.textRevisionId.value)
            && !editor.dirty.value && !['sending', 'queued', 'unknown'].includes(state.value.phase);
    }
    function open() {
        if (eligible()) state.value = { phase: 'idle', message: '', confirming: true };
    }
    function cancel() { state.value = { ...state.value, confirming: false }; }
    async function submit() {
        if (!eligible()) return { ok: false, message: REPEAT_TEXT.unavailable };
        state.value = { phase: 'sending', message: 'Anfrage wird gesendet …', confirming: false };
        const id = recordingId();
        try {
            const token = await csrf();
            // Read the actual proof after asynchronous CSRF lookup, never a guessed generation.
            const lease = editor.lease.value;
            if (disposed || !editor.canEdit.value || !lease.writable || editor.dirty.value || id !== recordingId()
                || !Number.isSafeInteger(lease.generation) || lease.generation < 1) {
                throw new Error(REPEAT_TEXT.unavailable);
            }
            const response = await fetchFn(`/recording/${id}/reprocess_transcription`, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
                body: JSON.stringify({ lease_generation: lease.generation }),
            });
            let data;
            try { data = await response.json(); } catch { data = null; }
            if (!response.ok) {
                const message = [401, 403, 409, 423].includes(response.status) ? REPEAT_TEXT.denied : REPEAT_TEXT.failed;
                if (!disposed) state.value = { phase: 'error', message, confirming: false };
                return { ok: false, message };
            }
            if (data?.success !== true) throw new Error(REPEAT_TEXT.unknown);
            const position = Number.isInteger(data.queue_position) && data.queue_position > 0
                ? ` Position: ${data.queue_position}.` : '';
            const message = REPEAT_TEXT.queued + position;
            if (!disposed) state.value = { phase: 'queued', message, confirming: false };
            return { ok: true, data, message };
        } catch (error) {
            const message = error.message === REPEAT_TEXT.unavailable ? error.message : REPEAT_TEXT.unknown;
            if (!disposed) state.value = { phase: message === REPEAT_TEXT.unknown ? 'unknown' : 'error', message, confirming: false };
            return { ok: false, message };
        }
    }
    const session = { state, recordingId, eligible, open, cancel, submit,
        proof: () => eligible() ? { lease_generation: editor.lease.value.generation } : null,
        dispose() { disposed = true; sessions.delete(session); } };
    sessions.add(session);
    return session;
}
