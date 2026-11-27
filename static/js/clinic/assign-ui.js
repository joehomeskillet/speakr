/** W13: only the server's candidate response authorizes the assignment UI. */
export function createAssignment({ client, dictationId, currentUser = {}, reactive = (v) => v, onAssigned = () => {} }) {
    const state = reactive({ allowed: false, candidates: [], query: '', selected: '', busy: false, error: '', message: '', etag: null, target: '' });
    const path = '/dictations/' + encodeURIComponent(dictationId) + '/assignment';
    let generation = 0;
    let receipt = null;
    async function load() {
        const seen = ++generation;
        state.allowed = false;
        state.candidates = [];
        state.etag = null;
        try {
            const [candidates, detail] = await Promise.all([client.request(path + '/candidates'), client.request('/dictations/' + encodeURIComponent(dictationId) + '/detail')]);
            if (seen !== generation) return;
            state.allowed = candidates.status === 200 && candidates.outcome === 'ok' && Array.isArray(candidates.data);
            state.candidates = state.allowed ? candidates.data : [];
            state.etag = detail.outcome === 'ok' ? detail.data?.etag : null;
            state.target = detail.outcome === 'ok' && detail.data?.routing_target?.label ? detail.data.routing_target.label : '';
        } catch {
            if (seen === generation) state.error = 'Die Zuweisung konnte nicht geladen werden.';
        }
    }
    const choices = () => state.candidates.filter((c) => c.display_name.toLocaleLowerCase().includes(state.query.trim().toLocaleLowerCase()) || String(c.id) === state.query.trim());
    function search(value) {
        state.query = value;
        const matches = state.candidates.filter((c) => String(c.id) === value.trim() || c.display_name.toLocaleLowerCase() === value.trim().toLocaleLowerCase());
        state.selected = matches.length === 1 ? String(matches[0].id) : '';
    }
    const canClaim = () => state.allowed && currentUser.role !== 'admin' && state.candidates.some((c) => String(c.id) === String(currentUser.id));
    async function assign(id = state.selected) {
        const candidate = state.candidates.find((c) => String(c.id) === String(id));
        if (!state.allowed || !state.etag || state.busy || !candidate) return false;
        const seen = generation;
        state.busy = true;
        state.error = '';
        // Keep the receipt after an unknown outcome so retry cannot duplicate a write.
        if (!receipt || receipt.id !== candidate.id || receipt.etag !== state.etag) receipt = { id: candidate.id, etag: state.etag, key: globalThis.crypto.randomUUID() };
        try {
            const result = await client.request(path, { method: 'POST', body: { assignee_user_id: candidate.id }, ifMatch: receipt.etag, idempotencyKey: receipt.key });
            if (seen !== generation) return false;
            if (result.outcome !== 'ok') {
                state.error = result.outcome === 'conflict' ? 'Der Stand ist veraltet. Bitte neu laden und erneut auswählen.' : result.message || 'Zuweisen fehlgeschlagen. Bitte erneut versuchen.';
                if (result.outcome === 'permission' || result.outcome === 'conflict') { state.allowed = false; state.etag = null; }
                return false;
            }
            state.etag = result.data?.etag || null;
            state.message = 'Zugewiesen an: ' + candidate.display_name;
            receipt = null;
            onAssigned({ ...result.data, display_name: candidate.display_name });
            return true;
        } catch {
            if (seen === generation) state.error = 'Ergebnis unbekannt. Bitte erneut versuchen.';
            return false;
        } finally {
            if (seen === generation) state.busy = false;
        }
    }
    function dispose() { generation += 1; state.allowed = false; state.candidates = []; receipt = null; }
    return { state, load, search, choices, canClaim, assign, dispose };
}

export const AssignmentPanel = {
    props: ['client', 'dictationId', 'currentUser', 'author', 'label', 'assignee', 'vue', 'actionOnly'],
    emits: ['assigned'],
    setup(props, { emit }) {
        const V = props.vue;
        const dialog = V.ref(null);
        const c = createAssignment({ ...props, reactive: V.reactive, onAssigned: (data) => emit('assigned', data) });
        V.onMounted(c.load);
        V.onBeforeUnmount(() => { dialog.value?.close(); c.dispose(); });
        function open() { c.state.query = ''; c.state.selected = ''; c.state.error = ''; dialog.value.showModal(); }
        async function submit(id) { if (await c.assign(id)) dialog.value?.close(); }
        function keepFocus(event) {
            const controls = [...dialog.value.querySelectorAll('input, select, button')].filter((el) => !el.disabled);
            const first = controls[0], last = controls.at(-1);
            if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
        }
        return { c, dialog, open, submit, keepFocus };
    },
    template: `
<section v-if="!actionOnly || c.state.allowed || c.state.error" class="clinic-assignment" aria-label="Zuweisung">
  <p v-if="c.state.target" data-testid="routing-target">Versandziel: {{ c.state.target }}</p>
  <p role="status" aria-live="polite">{{ c.state.message || (assignee ? 'Zugewiesen an: ' + assignee : 'Klärliste') }}</p>
  <button v-if="c.state.allowed" type="button" class="secondary" @click="open">Zuweisen</button>
  <button v-if="c.canClaim()" type="button" class="secondary" :disabled="c.state.busy || !c.state.etag" @click="submit(currentUser.id)">Für mich übernehmen</button>
  <p v-if="c.state.error" role="alert">{{ c.state.error }}</p>
  <button v-if="c.state.error && !c.state.allowed" type="button" class="secondary" @click="c.load()">Neu laden</button>
  <dialog ref="dialog" class="pool-dialog panel" :aria-labelledby="'assign-title-' + dictationId" @keydown.esc.stop.prevent="dialog.close()" @keydown.tab="keepFocus">
    <form @submit.prevent="submit()">
      <div class="pool-dialog-head"><div><span class="eyebrow">Zuweisung</span><h2 :id="'assign-title-' + dictationId">Diktat zuweisen</h2></div></div>
      <p class="pool-dialog-record">{{ label }}</p>
      <p class="pool-dialog-author">Diktiert von: <strong>{{ author }}</strong></p>
      <div style="margin-bottom: 12px">
        <label class="pool-choice-label" :for="'assign-search-' + dictationId">Schreibkraft suchen</label>
        <input :id="'assign-search-' + dictationId" class="input" type="search" autofocus autocomplete="off" :value="c.state.query" :disabled="c.state.busy" @input="c.search($event.target.value)">
      </div>
      <label class="pool-choice-label" :for="'assign-choice-' + dictationId">Schreibkraft</label>
      <select :id="'assign-choice-' + dictationId" class="select" v-model="c.state.selected" :disabled="c.state.busy" required>
        <option value="" disabled>Bitte auswählen</option>
        <option v-for="person in c.choices()" :key="person.id" :value="String(person.id)">{{ person.display_name }}</option>
      </select>
      <p v-if="!c.choices().length" role="status" class="pool-dialog-hint">Keine passende Schreibkraft gefunden.</p>
      <p v-if="!c.state.etag" class="pool-dialog-hint">Bitte den aktuellen Stand neu laden.</p>
      <p v-if="c.state.error" role="alert">{{ c.state.error }}</p>
      <button v-if="!c.state.etag" type="button" class="secondary" @click="c.load()">Neu laden</button>
      <div class="pool-dialog-actions">
        <button type="button" class="secondary" @click="dialog.close()">Abbrechen</button>
        <button type="submit" class="primary" :disabled="!c.state.allowed || !c.state.selected || !c.state.etag || c.state.busy">{{ c.state.busy ? 'Wird zugewiesen …' : 'Zuweisen' }}</button>
      </div>
    </form>
  </dialog>
</section>`,
};
