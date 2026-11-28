export function liveDraftFence(position, draftId, tabId) {
    if (position?.state !== 'PAUSED' || position.dictation_id !== draftId || position.draft_id !== draftId
            || typeof position.session_id !== 'string' || !position.session_id
            || !Number.isInteger(position.generation) || position.generation < 1) return null;
    return { session_id: position.session_id, client_id: tabId, generation: position.generation };
}

export const LiveBindingStatus = {
    props: {
        draftTitle: String, speakerName: String, phase: String,
        bound: Boolean, uncertain: Boolean, busy: Boolean,
    },
    emits: ['release'],
    template: `
    <div class="document-context" style="display: flex; gap: 8px 16px; justify-content: space-between">
      <p role="status" data-testid="live-binding-status" style="min-width: 0; margin: 0; overflow-wrap: anywhere">
        <strong v-if="uncertain || (bound && phase === 'stale')">Bindung nicht bestätigt: {{ draftTitle }}</strong>
        <strong v-else-if="bound">Gebunden: {{ draftTitle }}</strong>
        <strong v-else>Kein Entwurf gebunden</strong>
        <br>
        <span v-if="bound && !uncertain && phase === 'live' && !busy">Aktiv spricht: {{ speakerName || 'Sie' }}</span>
        <span v-else>Niemand spricht in diesem Browserfenster.</span>
      </p>
      <button v-if="bound" type="button" class="button" style="min-width: 48px; min-height: 48px; font-size: 14px"
              :disabled="busy" @click="$emit('release')">Bindung lösen</button>
    </div>`,
};
