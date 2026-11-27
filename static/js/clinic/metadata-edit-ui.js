import { PRIORITY_OPTIONS } from './dictation-properties-ui.js';
import { createLeaseController } from './lease-ui.js';

const LABELS = { title: 'Titel', notes: 'Anmerkung', priority: 'Priorität', deadline_date: 'Frist' };
const EMPTY_LABELS = { title: 'Ohne Titel', notes: 'Keine Anmerkung', priority: 'Normal', deadline_date: 'Keine Frist' };
const formatDate = (date) => date.split('-').reverse().join('.');

function changedFields(original, draft) {
    const values = { ...draft };
    if ('title' in values && values.title !== original.title) values.title = values.title.normalize('NFC').trim();
    if ('notes' in values && values.notes !== original.notes) values.notes = values.notes.normalize('NFC');
    if ('deadline_date' in values) {
        values.deadline_date ||= null;
        values.deadline_time = values.deadline_date && values.deadline_time ? values.deadline_time : null;
    }
    return Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== original[key]));
}

export function createMetadataEditor({ client, dictationId, hasLease, reactive = (value) => value,
    newKey = () => globalThis.crypto.randomUUID() }) {
    const path = '/dictations/' + encodeURIComponent(dictationId);
    const state = reactive({ draft: null, saved: null, busy: false, error: '', notice: '' });
    let original;
    let receipt;
    let disposed = false;

    async function read() {
        const response = await client.request(path + '/detail/metadata');
        const data = response?.data;
        return response?.outcome === 'ok' && data?.dictation_id === dictationId && data.etag && data.fields ? data : null;
    }

    async function open() {
        if (state.busy || disposed) return false;
        state.busy = true;
        state.error = '';
        state.notice = '';
        try {
            original = await read();
            if (disposed) return false;
            if (state.saved) state.saved = original?.fields ?? null;
            if (!original) {
                state.error = 'Die Diktatangaben konnten nicht geladen werden.';
                return false;
            }
            state.draft = { ...original.fields };
            return true;
        } finally {
            state.busy = false;
        }
    }

    function cancel() {
        if (state.busy) return;
        state.draft = null;
        state.error = '';
        state.notice = 'Eingaben verworfen.';
        original = receipt = null;
    }

    function canSave() {
        return !disposed && !state.busy && Boolean(original && state.draft)
            && Object.keys(changedFields(original.fields, state.draft)).length > 0;
    }

    async function save() {
        if (!canSave()) return false;
        const fields = changedFields(original.fields, state.draft);
        if (fields.title === '') {
            state.error = 'Bitte geben Sie einen Titel ein.';
            return false;
        }
        state.busy = true;
        state.error = '';
        let temporaryLease;
        try {
            if (!hasLease()) {
                temporaryLease = createLeaseController({ client, dictationId });
                const held = await temporaryLease.acquire();
                if (!held.writable) {
                    state.error = held.status === 'foreign' ? 'Jemand anderes bearbeitet dieses Diktat gerade.'
                        : 'Das Diktat kann gerade nicht bearbeitet werden. Bitte versuchen Sie es erneut.';
                    return false;
                }
            }
            if (disposed) return false;
            const payload = JSON.stringify(fields);
            if (receipt?.payload !== payload) receipt = { payload, key: newKey() };
            const response = await client.request(path + '/metadata', {
                method: 'POST', body: { fields }, ifMatch: original.etag, idempotencyKey: receipt.key,
            });
            if (disposed) return false;
            if (response.outcome !== 'ok') {
                if (response.outcome === 'conflict' || response.outcome === 'permission' || response.status === 404) {
                    original = null;
                    state.error = 'Dieses Diktat wurde geändert oder ist nicht mehr verfügbar. Schliessen Sie die Maske und öffnen Sie sie bei Bedarf erneut.';
                } else {
                    state.error = response.outcome === 'validation' ? 'Bitte prüfen Sie die Diktatangaben.'
                        : 'Bitte versuchen Sie es erneut. Ihre Eingaben bleiben erhalten.';
                }
                return false;
            }
            state.draft = null;
            original = receipt = null;
            const confirmed = await read();
            if (disposed) return false;
            state.saved = confirmed?.fields ?? null;
            state.notice = confirmed ? 'Diktatangaben gespeichert.'
                : 'Diktatangaben gespeichert. Der aktuelle Stand konnte nicht geladen werden.';
            return true;
        } finally {
            try {
                await temporaryLease?.release();
            } finally {
                temporaryLease?.dispose();
                state.busy = false;
            }
        }
    }

    return { state, open, cancel, canSave, save, dispose() { disposed = true; } };
}

export const MetadataEdit = {
    props: ['client', 'dictationId', 'vue', 'allowed', 'hasLease'],
    emits: ['saved'],
    setup(props, { emit }) {
        const V = props.vue;
        const dialog = V.ref(null);
        const trigger = V.ref(null);
        const editor = createMetadataEditor({ ...props, reactive: V.reactive });
        const editable = (name) => Object.hasOwn(editor.state.draft || {}, name);
        V.onBeforeUnmount(editor.dispose);
        async function open() {
            if (await editor.open() && dialog.value) dialog.value.showModal();
        }
        function cancel() {
            if (editor.state.busy) return;
            editor.cancel();
            dialog.value.close();
        }
        async function save() {
            if (!await editor.save()) return;
            dialog.value?.close();
            emit('saved', editor.state.notice);
        }
        function displayFields(fields) {
            return Object.entries(LABELS).filter(([key]) => Object.hasOwn(fields, key)).map(([key, label]) => {
                let value = fields[key];
                if (key === 'priority') value = PRIORITY_OPTIONS.find((option) => option.value === value)?.label;
                if (key === 'deadline_date' && value) {
                    value = formatDate(value) + (fields.deadline_time ? ', ' + fields.deadline_time : '');
                }
                return { label, value: value || EMPTY_LABELS[key] };
            });
        }
        return { editor, dialog, trigger, open, cancel, save, editable, displayFields, formatDate, priorities: PRIORITY_OPTIONS };
    },
    template: `
<div class="clinic-metadata-edit">
  <button v-if="allowed" ref="trigger" type="button" class="secondary" :disabled="editor.state.busy" @click="open">Angaben bearbeiten</button>
  <p v-if="editor.state.error && !editor.state.draft" role="alert">{{ editor.state.error }}</p>
  <p role="status">{{ editor.state.notice }}</p>
  <dl v-if="allowed && editor.state.saved">
    <div v-for="field in displayFields(editor.state.saved)" :key="field.label" class="metadata-pair">
      <dt>{{ field.label }}</dt><dd>{{ field.value }}</dd>
    </div>
  </dl>
  <dialog ref="dialog" class="pool-dialog panel clinic-metadata-dialog" aria-label="Diktatangaben bearbeiten"
          @keydown.esc.stop.prevent="cancel" @cancel.prevent="cancel" @close="trigger?.focus()">
    <form v-if="editor.state.draft" @submit.prevent="save">
      <div class="dialoghead"><h2>Diktatangaben bearbeiten</h2></div>
      <label v-if="editable('title')" class="field"><span>Titel</span>
        <input v-model="editor.state.draft.title" autofocus maxlength="200" :disabled="editor.state.busy">
      </label>
      <label v-if="editable('notes')" class="field"><span>Anmerkung</span>
        <textarea v-model="editor.state.draft.notes" rows="2" maxlength="4000" :disabled="editor.state.busy"></textarea>
      </label>
      <label v-if="editable('priority')" class="field"><span>Priorität</span>
        <select v-model="editor.state.draft.priority" :disabled="editor.state.busy" :autofocus="!editable('title')">
          <option v-for="option in priorities" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <div v-if="editable('deadline_date')" class="field-grid">
        <label class="field"><span>Frist</span><input v-model="editor.state.draft.deadline_date" type="date" :disabled="editor.state.busy">
          <small v-if="editor.state.draft.deadline_date" aria-hidden="true">{{ formatDate(editor.state.draft.deadline_date) }}</small>
        </label>
        <label class="field"><span>Uhrzeit (optional)</span><input v-model="editor.state.draft.deadline_time" type="time" step="1"
          :disabled="editor.state.busy || !editor.state.draft.deadline_date"></label>
      </div>
      <p v-if="editor.state.error" role="alert">{{ editor.state.error }}</p>
      <div class="pool-dialog-actions">
        <button type="button" class="secondary" :disabled="editor.state.busy" @click="cancel">Abbrechen</button>
        <button type="submit" class="primary" :disabled="!editor.canSave()">{{ editor.state.busy ? 'Speichert …' : 'Speichern' }}</button>
      </div>
    </form>
  </dialog>
</div>`,
};
