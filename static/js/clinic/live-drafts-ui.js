import { createDictationChoices } from './dictation-choices.js';

const DRAFTS = '/api/clinic/v1/live/drafts';
const MIN_TARGET = 'min-height: 48px; font-size: 14px';
const DANGER = MIN_TARGET + '; color: var(--text-danger-strong)';

function draftError(result, fallback) {
    if (result.outcome === 'conflict') return 'Der Entwurf oder die Aufnahme wurde inzwischen geändert. Ihre Eingabe bleibt hier erhalten.';
    if (result.outcome === 'permission') return 'Dieser Entwurf ist hier nicht verfügbar. Prüfen Sie auch, ob er in einem anderen Tab geöffnet ist.';
    return fallback;
}

const LiveDraftPicker = {
    props: { client: { type: Object, required: true }, authorId: { type: String, required: true } },
    setup(props, { expose }) {
        const { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref } = globalThis.Vue;
        const rows = ref([]);
        const active = ref(null);
        const text = ref('');
        const title = ref('');
        const dictation = ref('');
        const busy = ref(false);
        const loaded = ref(false);
        const message = ref('');
        const error = ref('');
        const heading = ref(null);
        const dialog = ref(null);
        const cancelButton = ref(null);
        const mode = ref('');
        const choices = createDictationChoices({ client: props.client, makeState: reactive, authorId: props.authorId });
        const available = computed(() => choices.state.rows.filter(row => !rows.value.some(draft => draft.draft_id === row.id)));

        const entry = history.state?.liveDraft;
        const restored = performance.getEntriesByType('navigation')[0]?.type === 'reload' && entry?.authorId === props.authorId ? entry : null;
        const tabId = restored?.tabId || crypto.randomUUID();
        let holder = restored?.holder || null;
        let controller = null;
        let destination = null;
        let trigger = null;
        let disposed = false;

        function remember(draftId) {
            // Same-document history carries only the selection and API correlation fields for reload.
            const state = { ...history.state };
            delete state.liveDraft;
            if (draftId) state.liveDraft = { authorId: props.authorId, draftId, tabId, holder };
            const url = new URL(location.href);
            url.searchParams.delete('dictation');
            history.replaceState(state, '', url);
        }

        const request = (id = '', method = 'GET', body) => props.client.request(DRAFTS + id, {
            method, body, ...(method !== 'GET' ? { idempotencyKey: crypto.randomUUID() } : {}),
        });

        function report(result, fallback) {
            error.value = draftError(result, fallback);
            return false;
        }

        async function run(action) {
            if (busy.value) return false;
            busy.value = true;
            error.value = '';
            message.value = '';
            try { return await action(); } finally { busy.value = false; }
        }

        function dirtyPhrases() {
            return controller?.getState().session.phrases.some(p => p.state === 'final_unsaved' || p.state === 'stale');
        }

        function isDirty() {
            return !!active.value && (text.value !== active.value.text || dirtyPhrases());
        }

        function upsert({ draft_id, title }) {
            rows.value = [{ draft_id, title }, ...rows.value.filter(row => row.draft_id !== draft_id)];
        }

        async function claim(id) {
            const path = '/' + encodeURIComponent(id) + '/switch';
            const body = { tab_id: tabId };
            let result = await request(path, 'POST', holder ? { ...body, acknowledged_holder: holder } : body);
            // An expired or stopped holder cannot be acknowledged. The API still refuses another tab's active holder.
            if (holder && result.outcome === 'permission') result = await request(path, 'POST', body);
            if (result.outcome !== 'ok' || disposed) return report(result, 'Der Entwurf konnte nicht geöffnet werden.');
            holder = { session_id: result.data.session_id, client_id: tabId, generation: result.data.generation };
            remember(id);
            return true;
        }

        const draftSession = {
            tabId,
            get position() { return holder; },
            claim: () => run(() => claim(active.value.draft_id)),
            ended(sessionId) {
                if (holder?.session_id !== sessionId) return;
                holder = null;
                remember(active.value.draft_id);
            },
        };

        async function open(id) {
            const result = await request('/' + encodeURIComponent(id));
            if (result.outcome !== 'ok' || result.data.discarded_at) return report(result, 'Der Entwurf konnte nicht geladen werden.');
            if (!await claim(id)) return false;
            active.value = result.data;
            text.value = result.data.text;
            message.value = 'Entwurf «' + result.data.title + '» geöffnet.';
            return true;
        }

        async function release() {
            if (!holder) return true;
            const { session_id, ...body } = holder;
            const result = await props.client.request('/api/clinic/v1/live/sessions/' + encodeURIComponent(session_id) + '/pause', {
                method: 'POST', body: { ...body, release: true }, idempotencyKey: crypto.randomUUID(),
            });
            if (!['ok', 'conflict', 'permission'].includes(result.outcome)) return report(result, 'Der Entwurf konnte nicht geschlossen werden.');
            holder = null;
            return true;
        }

        async function save() {
            if (dirtyPhrases()) {
                await controller.saveUnsaved();
                if (dirtyPhrases()) {
                    error.value = 'Einige Sätze konnten nicht gespeichert werden. Ihre Eingabe bleibt hier erhalten.';
                    return false;
                }
            }
            const draft = active.value;
            if (text.value !== draft.text) {
                const result = await request('/' + encodeURIComponent(draft.draft_id), 'PUT', {
                    base_version: draft.version, text: text.value,
                });
                if (result.outcome !== 'ok') return report(result, 'Der Entwurf konnte nicht gespeichert werden.');
                active.value = result.data;
                text.value = result.data.text;
                upsert(result.data);
            }
            message.value = 'Entwurf «' + draft.title + '» gespeichert.';
            return true;
        }

        async function archive() {
            const draft = active.value;
            const result = await request('/' + encodeURIComponent(draft.draft_id) + '/discard', 'POST', {
                base_version: draft.version, confirm: true,
            });
            if (result.outcome !== 'ok') return report(result, 'Der Entwurf konnte nicht verworfen werden.');
            rows.value = rows.value.filter(row => row.draft_id !== draft.draft_id);
            holder = null;
            clearEditor();
            message.value = 'Entwurf «' + draft.title + '» verworfen. Die Originalaufnahme bleibt erhalten.';
            return true;
        }

        function clearEditor() {
            active.value = null;
            controller = null;
            text.value = '';
            remember(null);
        }

        function closeDialog(restoreFocus) {
            if (dialog.value.open) dialog.value.close();
            mode.value = '';
            destination = null;
            if (restoreFocus) trigger?.focus();
            trigger = null;
        }

        function ask(action, step) {
            destination = action;
            trigger = document.activeElement;
            mode.value = step;
            error.value = '';
            dialog.value.showModal();
            nextTick(() => cancelButton.value?.focus());
        }

        async function finish(action, { saveFirst = false, discard = false } = {}) {
            let moved = false;
            await run(async () => {
                if (saveFirst && !await save()) return;
                if (discard && !await archive()) return;
                if (action.id) {
                    const saved = message.value;
                    moved = await open(action.id);
                    if ((saveFirst || discard) && saved) message.value = [saved, moved ? message.value : ''].filter(Boolean).join(' ');
                    if (!moved && !discard) return;
                } else {
                    if (active.value && !await release()) return;
                    if (active.value && !saveFirst) message.value = 'Entwurf «' + active.value.title + '» geschlossen.';
                    clearEditor();
                }
                closeDialog(false);
                moved = true;
            });
            if (!moved) return;
            if (action.after) action.after();
            else await nextTick(() => (active.value ? document.getElementById('clv-title') : heading.value)?.focus());
        }

        function choose(action) {
            if (busy.value || mode.value || (action.id && action.id === active.value?.draft_id)) return;
            if (isDirty()) ask(action, 'dirty');
            else void finish(action);
        }

        const readyToCreate = computed(() => !!choices.resolve(dictation.value) && title.value.trim().length > 0
            && !rows.value.some(row => row.draft_id === choices.resolve(dictation.value)));

        async function create() {
            if (!readyToCreate.value || active.value) return;
            const draft = await run(async () => {
                const result = await request('', 'POST', { dictation_id: choices.resolve(dictation.value), title: title.value.trim() });
                if (result.outcome !== 'ok') return report(result, 'Der Entwurf konnte nicht angelegt werden. Wählen Sie eine eigene offene Aufnahme.');
                upsert(result.data);
                dictation.value = '';
                title.value = '';
                return result.data;
            });
            if (draft) await finish({ id: draft.draft_id });
        }

        async function load() {
            const initial = restored?.draftId || new URLSearchParams(location.search).get('dictation');
            const result = await run(() => request());
            if (!result || disposed) return;
            if (result.outcome !== 'ok') {
                error.value = 'Die Entwürfe konnten nicht geladen werden.';
                return;
            }
            rows.value = result.data.drafts.map(({ draft_id, title }) => ({ draft_id, title }));
            loaded.value = true;
            if (rows.value.some(row => row.draft_id === initial)) await finish({ id: initial });
        }

        onMounted(() => { void load(); void choices.load(); });
        onBeforeUnmount(() => { disposed = true; choices.clear(); });
        expose({ isDirty, needsLeave: () => !!active.value || busy.value, leave: after => choose({ after }) });
        return { rows, active, text, title, dictation, busy, loaded, message, error, heading, dialog, cancelButton, mode, choices,
            available, readyToCreate, draftSession, minTarget: MIN_TARGET, dangerTarget: DANGER, choose, create, load, ask,
            closeDialog, saveNow: () => run(save), saveAndLeave: () => finish(destination, { saveFirst: true }),
            discardAndLeave: () => finish(destination, { discard: true }), setController: value => { controller = value; } };
    },
    template: `
    <div class="v8-page" data-testid="clinic-live-picker">
        <div class="pagehead">
            <div><div class="eyebrow">Aufnahmen</div><h1 ref="heading" tabindex="-1">Live-Diktat</h1></div>
        </div>
        <section class="panel" aria-labelledby="clinic-live-picker-title" style="margin-bottom: 16px">
            <h2 id="clinic-live-picker-title">Meine Entwürfe</h2>
            <p v-if="!loaded && busy">Entwürfe werden geladen …</p>
            <button v-if="!loaded && !busy" type="button" class="button" :style="minTarget" @click="load">Erneut laden</button>
            <p v-if="loaded && !rows.length">Sie haben noch keine Live-Entwürfe.</p>
            <ul v-if="rows.length" class="actions" style="list-style: none; padding: 0; margin: 0 0 12px">
                <li v-for="row in rows" :key="row.draft_id" style="min-width: 0; max-width: 100%">
                    <button type="button" class="button" data-testid="live-draft-open"
                            :style="minTarget + '; max-width: 100%; white-space: normal; overflow-wrap: anywhere; text-align: start'"
                            :aria-current="active?.draft_id === row.draft_id ? 'true' : null" :disabled="busy"
                            :class="{ 'font-bold': active?.draft_id === row.draft_id }"
                            @click="choose({ id: row.draft_id })">{{ row.title }}</button>
                </li>
            </ul>
            <form v-if="loaded && !active" class="actions" style="align-items: end" @submit.prevent="create">
                <label class="field" style="flex: 1 1 220px; min-width: 0; margin: 0; display: grid; gap: 4px">
                    <span>Eigene Aufnahme</span>
                    <input v-model="dictation" type="text" list="clinic-live-choices" data-testid="clinic-live-id" autocomplete="off"
                           :style="minTarget + '; border-color: var(--v8-input-outline)'" :disabled="busy">
                </label>
                <label class="field" style="flex: 1 1 180px; min-width: 0; margin: 0; display: grid; gap: 4px">
                    <span>Name des Entwurfs</span>
                    <input v-model="title" type="text" data-testid="clinic-live-title" maxlength="200" autocomplete="off"
                           :style="minTarget + '; border-color: var(--v8-input-outline)'" :disabled="busy">
                </label>
                <button type="submit" data-testid="clinic-live-open" class="button primary" :style="minTarget"
                        :disabled="busy || !readyToCreate">Entwurf anlegen</button>
            </form>
            <datalist id="clinic-live-choices"><option v-for="row in available" :key="row.id" :value="row.label"></option></datalist>
            <p v-if="!active && choices.state.message" class="hint" style="margin-top: 8px">{{ choices.state.message }}</p>
            <button v-if="!active && choices.state.cursor" type="button" class="button" :style="minTarget" :disabled="busy || choices.state.loading"
                    @click="choices.load(true)">Weitere Aufnahmen laden</button>
            <p role="status" style="margin: 0" :style="message ? 'margin-top: 12px' : null">{{ message }}</p>
            <p v-if="error && !mode" role="alert" style="margin: 12px 0 0; color: var(--text-danger-strong)">{{ error }}</p>
        </section>
        <clinic-live-view v-if="active" :key="active.draft_id" :client="client" :dictation-id="active.draft_id"
                          :draft-title="active.title" :draft-session="draftSession" :draft-busy="busy" :on-ready="setController">
            <template #draft-actions>
                <button type="button" class="button primary" :style="minTarget" data-testid="live-draft-save" :disabled="busy" @click="saveNow">Speichern</button>
                <button type="button" class="button" :style="minTarget" data-testid="live-draft-close" :disabled="busy" @click="choose({})">Schliessen</button>
                <button type="button" class="button" :style="dangerTarget" data-testid="live-draft-discard" :disabled="busy" @click="ask({}, 'discard')">Verwerfen</button>
            </template>
            <template #draft-text>
                <label for="clinic-live-draft-text" style="display: block; margin: 12px 16px 4px; font-weight: 650">Entwurfstext</label>
                <textarea id="clinic-live-draft-text" v-model="text" data-testid="live-draft-text" :readonly="busy" rows="6"
                          style="display: block; width: calc(100% - 32px); margin: 0 16px 12px; font-size: 16px; border-color: var(--v8-input-outline)"></textarea>
            </template>
        </clinic-live-view>
        <dialog ref="dialog" class="panel" aria-labelledby="clinic-live-dialog-title" style="width: min(460px, calc(100% - 32px)); max-height: calc(100% - 32px); color: var(--v8-text)"
                @cancel.prevent="!busy && closeDialog(true)">
            <div class="dialoghead"><h2 id="clinic-live-dialog-title">{{ mode === 'dirty' ? 'Ungesicherte Änderungen' : 'Entwurf verwerfen?' }}</h2></div>
            <p v-if="mode === 'dirty'" style="overflow-wrap: anywhere">Möchten Sie die Änderungen an «{{ active?.title }}» speichern?</p>
            <p v-else style="overflow-wrap: anywhere">«{{ active?.title }}» wird aus Ihrer Entwurfsliste entfernt. Die Originalaufnahme bleibt erhalten.</p>
            <p v-if="error" role="alert" style="color: var(--text-danger-strong)">{{ error }}</p>
            <div class="actions">
                <button ref="cancelButton" type="button" class="button" :style="minTarget" :disabled="busy" @click="closeDialog(true)">Abbrechen</button>
                <template v-if="mode === 'dirty'">
                    <button type="button" class="button" :style="dangerTarget" :disabled="busy" @click="mode = 'discard'; cancelButton.focus()">Verwerfen</button>
                    <button type="button" class="button primary" :style="minTarget" :disabled="busy" @click="saveAndLeave">Speichern</button>
                </template>
                <button v-else type="button" class="button" :style="dangerTarget" :disabled="busy" @click="discardAndLeave">Entwurf verwerfen</button>
            </div>
        </dialog>
    </div>`,
};

export function registerLiveDraftPicker(app) {
    app.component('clinic-live-draft-picker', LiveDraftPicker);
}
