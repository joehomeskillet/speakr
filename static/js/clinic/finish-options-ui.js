import { createOutputViewController } from './compose-output.js';

export function createFinishOptionsController({ workflow, client, vue, newKey }) {
    const state = (vue?.reactive || ((value) => value))({
        step: null, busy: false, content: false, context: false,
        revision: null, targets: [], selected: {}, results: {}, error: '',
    });
    let binding;
    let output;

    function open() {
        if (state.step || state.busy || !workflow.request('release')) return false;
        const { dictationId, etag } = workflow.state;
        const revision = workflow.state.binding.release.transcript_revision_id;
        binding = { dictationId, etag, revision };
        Object.assign(state, { step: 'release', content: false, context: false, revision,
            targets: [], selected: {}, results: {}, error: '' });
        return true;
    }

    function close() {
        if (!state.step || state.busy) return false;
        if (state.step === 'release') workflow.cancel();
        state.step = null;
        output = null;
        binding = null;
        return true;
    }

    async function release(withOutput) {
        if (state.step !== 'release' || state.busy || !state.content || !state.context) return;
        state.error = '';
        const { dictationId, etag, revision } = binding;
        if (workflow.state.dictationId !== dictationId || workflow.state.etag !== etag
                || workflow.state.binding.release?.transcript_revision_id !== revision
                || workflow.state.confirming !== 'release') {
            state.error = 'Der Arbeitsstand hat sich geändert. Bitte neu öffnen.';
            return;
        }
        state.busy = true;
        try {
            workflow.state.confirmedContent = state.content;
            workflow.state.confirmedContext = state.context;
            const result = await workflow.confirm();
            if (result?.outcome !== 'ok') {
                state.error = workflow.state.error || 'Fertigstellen konnte nicht bestätigt werden.';
                return;
            }
            state.step = 'done';
            if (!withOutput) return;
            state.step = 'output';
            output = createOutputViewController({ client, dictationId, vue, newKey });
            if (!await output.load()) {
                state.error = 'Die Ausgabeziele konnten nicht geladen werden.';
            } else if (result.data?.transcript_revision_id !== revision || output.releasedRevisionId.value !== revision) {
                state.error = 'Die freigegebene Textrevision konnte für die Ausgabe nicht bestätigt werden.';
            } else {
                state.targets = output.targets.value.filter((target) =>
                    target.channel === 'file' && ['txt', 'docx'].includes(target.id));
            }
        } catch {
            state.error = state.step === 'release' ? 'Fertigstellen konnte nicht bestätigt werden.'
                : 'Die Ausgabeziele konnten nicht geladen werden.';
        } finally {
            state.busy = false;
        }
    }

    const selectedTargets = () => state.targets.filter((target) => target.available && state.selected[target.id] === true);

    async function exportSelected() {
        if (state.step !== 'output' || state.busy || !output || output.releasedRevisionId.value !== binding.revision) return;
        const targets = selectedTargets();
        if (!targets.length) return;
        state.busy = true;
        try {
            const results = await Promise.allSettled(targets.map((target) => output.exportTo(target.id)));
            state.results = Object.fromEntries(targets.map((target, index) => {
                const result = results[index];
                return [target.id, (result.status === 'fulfilled' && result.value) || {
                    phase: 'unknown', label: 'Das Ergebnis ist unbekannt. Bitte den Ausgabestatus prüfen.',
                }];
            }));
            state.step = 'done';
        } finally {
            state.busy = false;
        }
    }

    return { state, open, close, release, exportSelected, selectedTargets };
}

export const FinishOptions = {
    name: 'ClinicFinishOptions',
    props: {
        workflow: { type: Object, required: true },
        client: { type: Object, required: true },
        disabled: Boolean,
        vue: { type: Object, default: null },
    },
    emits: ['finished'],
    setup(props, { emit, expose }) {
        const vue = props.vue || globalThis.Vue;
        const c = createFinishOptionsController({ ...props, vue });
        const dialog = vue.ref(null);
        const title = vue.ref(null);
        function open() {
            if (props.disabled || !c.open()) return false;
            dialog.value.showModal();
            return true;
        }
        function onKeydown(event) {
            if (event.key !== 'F5' || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || props.disabled) return;
            if (event.repeat || c.state.step || open()) event.preventDefault();
        }
        function close() {
            const finished = c.state.step !== 'release';
            const receipt = { revision: c.state.revision, results: c.state.results };
            if (!c.close()) return;
            dialog.value.close();
            if (finished) emit('finished', receipt);
        }
        vue.watch(() => [c.state.step, c.state.busy], ([step, busy], [previous]) => {
            if (step && !busy && previous !== step) title.value?.focus();
            else if (step && !busy && !dialog.value?.contains(document.activeElement)) title.value?.focus();
        }, { flush: 'post' });
        expose({ open, onKeydown });
        return { c, dialog, title, open, close, onKeydown };
    },
    template: `
<div class="v8-page" @keydown="onKeydown">
  <button type="button" class="primary" data-action="release" style="min-height: 48px" :disabled="disabled" @click="open">Fertigstellen</button>
  <dialog ref="dialog" class="panel [&_button]:min-h-12 [&_label]:flex [&_label]:min-h-12 [&_label]:items-center [&_label]:gap-2" aria-label="Fertigstellen" @cancel.prevent="close">
    <div class="dialoghead"><h2 ref="title" tabindex="-1">Fertigstellen</h2></div>
    <template v-if="c.state.step === 'release'">
      <p class="hint">Der geprüfte Text und der Fallkontext werden abgeschlossen.</p>
      <fieldset :disabled="c.state.busy">
        <legend>1. Freigabe bestätigen</legend>
        <p><label><input v-model="c.state.content" type="checkbox"> Inhalt geprüft</label></p>
        <p><label><input v-model="c.state.context" type="checkbox"> Fallkontext geprüft</label></p>
      </fieldset>
      <div class="actions">
        <button type="button" :disabled="c.state.busy" @click="close">Abbrechen</button>
        <button type="button" :disabled="c.state.busy || !c.state.content || !c.state.context" @click="c.release(true)">Fertigstellen und Ausgabe wählen</button>
        <button type="button" class="primary" :disabled="c.state.busy || !c.state.content || !c.state.context" @click="c.release(false)">Ohne Ausgabe fertigstellen</button>
      </div>
    </template>
    <template v-else-if="c.state.step">
      <p role="status">Das Diktat ist fertiggestellt.</p>
      <fieldset v-if="c.state.step === 'output'" :disabled="c.state.busy">
        <legend>2. Ausgabeziele bestätigen</legend>
        <p v-if="!c.state.targets.length && !c.state.busy && !c.state.error" class="hint">Keine Ausgabeziele verfügbar.</p>
        <p v-for="target in c.state.targets" :key="target.id">
          <label><input v-model="c.state.selected[target.id]" type="checkbox" :disabled="!target.available"> {{ target.label }} bestätigen</label>
          <span v-if="!target.available" class="hint"> — {{ target.reason }}</span>
        </p>
      </fieldset>
      <p v-for="target in c.state.targets.filter(t => c.state.results[t.id])" :key="target.id" role="status">
        <strong>{{ target.label }}:</strong> {{ c.state.results[target.id].label }}
      </p>
      <div class="actions">
        <button type="button" :disabled="c.state.busy" @click="close">Schliessen</button>
        <button v-if="c.state.step === 'output' && c.state.targets.length" type="button" class="primary"
                :disabled="c.state.busy || !c.selectedTargets().length" @click="c.exportSelected">Bestätigte Ziele ausgeben</button>
      </div>
    </template>
    <p v-if="c.state.busy" class="hint" role="status">Wird verarbeitet …</p>
    <p v-if="c.state.error" class="text-status error" role="alert">{{ c.state.error }}</p>
  </dialog>
</div>`,
};
