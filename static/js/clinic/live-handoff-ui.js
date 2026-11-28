import { CATEGORIES, RequiredCategories } from './categories-ui.js';

export const LiveHandoff = {
    components: { RequiredCategories },
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        ready: { type: Boolean, required: true },
        currentCorrection: { type: Function, required: true },
    },
    emits: ['lock', 'refresh', 'submitted'],
    setup(props, { emit }) {
        const { computed, nextTick, onBeforeUnmount, ref } = globalThis.Vue;
        const step = ref('idle');
        const message = ref('');
        const categories = ref([]);
        const opener = ref(null);
        const heading = ref(null);
        const confirmation = ref(null);
        const receipt = ref(null);
        const path = '/dictations/' + encodeURIComponent(props.dictationId);
        let request;
        let disposed = false;
        onBeforeUnmount(() => { disposed = true; });

        const canConfirm = computed(() => step.value === 'retry'
            || (step.value === 'confirm' && props.ready && categories.value.every((set) => set.value)));

        async function close() {
            step.value = 'idle';
            emit('lock', false);
            await nextTick();
            opener.value?.focus();
        }

        async function open() {
            if (step.value !== 'idle' || !props.ready) return;
            step.value = 'loading';
            message.value = 'Angaben zum Einreichen werden geladen …';
            emit('lock', true);
            const result = await props.client.request(CATEGORIES);
            if (disposed) return;
            if (result.outcome !== 'ok' || !Array.isArray(result.data?.sets)) {
                message.value = 'Die Angaben zum Einreichen konnten nicht geladen werden. Bitte versuchen Sie es erneut.';
                await close();
                return;
            }
            categories.value = result.data.sets.filter((set) => set.required).map((set) => ({ ...set, value: '' }));
            message.value = '';
            step.value = 'confirm';
            await nextTick();
            heading.value?.focus();
        }

        function select(id, value) {
            const set = categories.value.find((item) => item.id === id);
            if (step.value === 'confirm' && set) set.value = value;
        }

        async function submit() {
            if (!canConfirm.value) return;
            step.value = 'sending';
            message.value = 'Diktat wird eingereicht …';
            if (!request) {
                const detail = await props.client.request(path + '/detail');
                if (disposed) return;
                const data = detail.data;
                const correction = props.currentCorrection();
                if (!props.ready || !correction || detail.outcome !== 'ok' || data?.dictation_id !== props.dictationId
                    || !data.etag || !data.allowed_actions?.includes('send') || !correction.audio_revision_id
                    || data.binding?.audioRevisionId !== correction.audio_revision_id
                    || data.binding?.textRevisionId !== correction.text_revision_id) {
                    message.value = 'Das Diktat kann gerade nicht eingereicht werden. Bitte prüfen Sie den aktuellen Stand.';
                    if (detail.outcome === 'ok') emit('refresh');
                    await close();
                    return;
                }
                const selections = categories.value.map(({ id, version, value }) => ({ set_id: id, version, value }));
                request = { method: 'POST', body: {
                    ...(selections.length && { categories: selections }),
                    live_revision: { text_revision_id: correction.text_revision_id,
                        document_version: correction.document_version ?? 0, audio_revision_id: correction.audio_revision_id,
                        lease_generation: correction.lease_generation },
                }, ifMatch: data.etag, idempotencyKey: globalThis.crypto.randomUUID() };
            }
            const result = await props.client.request(path + '/send', request);
            if (disposed) return;
            if (result.outcome === 'ok' && result.data?.dictation_id === props.dictationId && result.data.status) {
                step.value = 'sent';
                message.value = 'Diktat eingereicht.';
                emit('submitted');
                await nextTick();
                receipt.value?.focus();
            } else if (['conflict', 'permission', 'validation'].includes(result.outcome)
                       || [400, 403, 404, 409, 422, 423, 428].includes(result.status)) {
                request = null;
                message.value = 'Diktat wurde nicht eingereicht. Bitte prüfen Sie den aktuellen Stand und versuchen Sie es erneut.';
                if (result.outcome === 'conflict' || result.status === 423) emit('refresh', result.code);
                await close();
            } else {
                step.value = 'retry';
                message.value = 'Das Einreichen ist noch nicht bestätigt. Bitte versuchen Sie es erneut.';
                await nextTick();
                confirmation.value?.focus();
            }
        }

        return { step, message, categories, opener, heading, confirmation, receipt, canConfirm, open, close, select, submit };
    },
    template: `
<div style="display:grid;gap:8px;min-width:0">
  <button v-if="step === 'idle'" ref="opener" type="button" class="button primary"
          style="min-height:48px;justify-self:start" :disabled="!ready" @click="open">Diktat einreichen</button>
  <form v-if="['confirm', 'sending', 'retry'].includes(step)" style="display:grid;gap:8px"
        aria-labelledby="live-handoff-confirm" @submit.prevent="submit">
    <p v-if="step === 'confirm'" ref="heading" id="live-handoff-confirm" tabindex="-1" style="margin:0">
      Gespeicherten Text und Aufnahme an den Schreibdienst weitergeben?
    </p>
    <span v-else id="live-handoff-confirm">Diktat einreichen</span>
    <RequiredCategories v-if="categories.length" :items="categories" :disabled="step !== 'confirm'" @select="select" />
    <div style="display:flex;flex-wrap:wrap;gap:8px">
      <button ref="confirmation" type="submit" class="button primary" style="min-height:48px"
              :disabled="!canConfirm">{{ step === 'retry' ? 'Erneut versuchen' : 'Jetzt einreichen' }}</button>
      <button v-if="step === 'confirm'" type="button" class="button" style="min-height:48px" @click="close">Abbrechen</button>
    </div>
  </form>
  <p ref="receipt" role="status" tabindex="-1" style="margin:0" v-show="message">{{ message }}</p>
</div>`,
};
