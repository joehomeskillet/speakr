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

export const LiveBindingRule = {
    props: { draftTitle: String, ruleTitle: String, busy: Boolean, claiming: Boolean, uncertain: Boolean, stopping: Boolean },
    emits: ['change'],
    template: `
    <section class="panel" aria-label="Automatisch verbinden" style="display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; margin-bottom: var(--ui-space-4)">
        <p role="status" style="margin: 0; min-width: 0; overflow-wrap: anywhere; flex: 1 1 220px">
            <strong>Automatisch verbinden: {{ stopping ? 'Wird ausgeschaltet …' : ruleTitle ? 'An' : 'Aus' }}</strong><br>
            <span v-if="ruleTitle">Nur für «{{ ruleTitle }}» in dieser Ansicht. Die Aufnahme startet nicht von selbst.</span>
            <span v-else-if="draftTitle && !stopping">Nur für «{{ draftTitle }}» einschalten. Die Aufnahme startet nicht von selbst.</span>
        </p>
        <button type="button" class="button" data-testid="live-automatic-binding"
                style="min-height: 48px; min-width: 48px; font-size: 14px; max-width: 100%; white-space: normal"
                :aria-pressed="!!ruleTitle" :aria-disabled="stopping || (busy && !claiming) || (!ruleTitle && uncertain)"
                @click="!(stopping || (busy && !claiming) || (!ruleTitle && uncertain)) && $emit('change', !ruleTitle)">
            {{ ruleTitle ? 'Automatik ausschalten' : 'Automatik einschalten' }}
        </button>
    </section>`,
};
