import { createDictationChoices } from './dictation-choices.js';
import { liveDraftFence, LiveBindingRule, LiveBindingStatus } from './live-binding-ui.js';
import { releaseLiveHolder } from './live-capture-ui.js';

const DRAFTS = '/api/clinic/v1/live/drafts';
const MIN_TARGET = 'min-height: 48px; font-size: 14px';
const DANGER = MIN_TARGET + '; color: var(--text-danger-strong)';

function draftError(result, fallback) {
    if (result.outcome === 'conflict') return 'Der Entwurf oder die Aufnahme wurde inzwischen geändert. Ihre Eingabe bleibt hier erhalten.';
    if (result.outcome === 'permission') return 'Dieser Entwurf ist hier nicht verfügbar. Prüfen Sie auch, ob er in einem anderen Tab geöffnet ist.';
    return fallback;
}

const LiveDraftPicker = {
    components: { LiveBindingRule, LiveBindingStatus },
    props: { client: { type: Object, required: true }, authorId: { type: String, required: true }, speakerName: String },
    setup(props, { expose }) {
        const { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref, shallowRef } = globalThis.Vue;
        const rows = ref([]);
        const active = ref(null);
        const directDictation = ref(null);
        const liveView = ref(null);
        const currentUser = { id: props.authorId };
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
        const holder = shallowRef(restored?.holder || null);
        const bindingUncertain = ref(!!restored?.uncertain);
        const automaticRule = shallowRef(null);
        const automaticStopping = ref(false);
        // Once configured, draft navigation follows the visible rule until this view closes.
        const automaticManaged = ref(false);
        const automaticClaiming = ref(false);
        let automaticClaim = null;
        let controller = null;
        let destination = null;
        let trigger = null;
        let disposed = false;
        let hidden = false;
        let pageVisit = 0;

        function remember(draftId) {
            // Same-document history carries only the selection and API correlation fields for reload.
            const state = { ...history.state };
            delete state.liveDraft;
            if (draftId) state.liveDraft = {
                authorId: props.authorId, draftId, tabId, holder: automaticManaged.value ? null : holder.value,
                uncertain: automaticManaged.value ? false : bindingUncertain.value,
            };
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

        function bindOnFocus(event) {
            if (event && !event.relatedTarget) return;
            const id = active.value?.draft_id;
            if (!id || id !== automaticRule.value?.draft_id || !controller || holder.value || bindingUncertain.value
                    || busy.value || mode.value || automaticStopping.value || disposed || hidden) return;
            automaticClaiming.value = true;
            automaticClaim = run(async () => {
                if (await claim(id) && automaticRule.value?.draft_id === id) await controller.adoptDraftBinding();
            }).finally(() => { automaticClaiming.value = false; });
        }

        async function setAutomatic(enabled) {
            if (enabled) {
                if (!active.value || busy.value || automaticStopping.value || bindingUncertain.value) return;
                automaticManaged.value = true;
                automaticRule.value = { draft_id: active.value.draft_id, title: active.value.title };
                remember(active.value.draft_id);
                bindOnFocus();
                return;
            }
            if (automaticStopping.value) return;
            automaticRule.value = null;
            automaticStopping.value = true;
            try {
                await automaticClaim;
                if (disposed || hidden) return;
                if (holder.value) {
                    await run(async () => await readyToLeave({ allowUnconfirmed: true }) && release());
                } else if (bindingUncertain.value) {
                    error.value = 'Die Automatik ist aus. Ob die Verbindung gelöst ist, konnte nicht geprüft werden.';
                } else {
                    error.value = '';
                    message.value = 'Automatik ausgeschaltet.';
                }
            } finally {
                automaticClaim = null;
                automaticStopping.value = false;
            }
        }

        async function claim(id) {
            const path = '/' + encodeURIComponent(id) + '/switch';
            const body = { tab_id: tabId };
            const visit = pageVisit;
            const previous = holder.value;
            let result = await request(path, 'POST', previous ? { ...body, acknowledged_holder: previous } : body);
            // An expired or stopped holder cannot be acknowledged. The API still refuses another tab's active holder.
            const oldFenceRefused = previous && result.code === 'POLICY_DENIED';
            if (oldFenceRefused && !disposed && !hidden && visit === pageVisit) result = await request(path, 'POST', body);
            const fence = liveDraftFence(result.data, id, tabId);
            if (disposed || hidden || visit !== pageVisit) {
                if (fence) releaseOnExit(fence);
                return false;
            }
            if (!fence) {
                if (oldFenceRefused || ['network', 'UNKNOWN', 'cancelled', 'ok'].includes(result.outcome)) {
                    bindingUncertain.value = true;
                    if (active.value) remember(active.value.draft_id);
                }
                error.value = result.outcome === 'conflict'
                    ? 'Der Entwurf konnte nicht verbunden werden. Prüfen Sie, ob Sie in einem anderen Browserfenster diktieren.'
                    : 'Der Entwurf konnte nicht zum Diktieren verbunden werden.';
                return false;
            }
            holder.value = fence;
            bindingUncertain.value = false;
            remember(id);
            return true;
        }

        const draftSession = {
            tabId,
            get position() { return holder.value; },
            get uncertain() { return bindingUncertain.value; },
            claim: () => run(() => claim(active.value.draft_id)),
            release: releaseFromEditor,
            ended(sessionId) {
                if (holder.value?.session_id !== sessionId) return;
                automaticRule.value = null;
                holder.value = null;
                bindingUncertain.value = false;
                remember(active.value.draft_id);
            },
        };

        async function open(id, bind = true) {
            const visit = pageVisit;
            const result = await request('/' + encodeURIComponent(id));
            if (disposed || hidden || visit !== pageVisit) return false;
            if (result.outcome !== 'ok' || result.data.discarded_at) return report(result, 'Der Entwurf konnte nicht geladen werden.');
            if (bind) {
                if (automaticManaged.value) {
                    if (!await release()) return false;
                } else if (!await claim(id)) return false;
            }
            active.value = result.data;
            text.value = result.data.text;
            remember(id);
            message.value = '';
            return true;
        }

        async function readyToLeave(options) {
            if (!controller || await controller.prepareRelease(options)) return true;
            error.value = 'Die Aufnahme konnte noch nicht angehalten werden. Bitte versuchen Sie es erneut.';
            return false;
        }

        async function releaseFromEditor() {
            automaticRule.value = null;
            if (await run(async () => await readyToLeave({ allowUnconfirmed: automaticManaged.value }) && release())) {
                await nextTick(() => document.getElementById('clv-title')?.focus());
            }
        }

        async function release() {
            if (!holder.value) return true;
            const { session_id, ...body } = holder.value;
            const result = await props.client.request('/api/clinic/v1/live/sessions/' + encodeURIComponent(session_id) + '/pause', {
                method: 'POST', body: { ...body, release: true }, idempotencyKey: crypto.randomUUID(),
            });
            const confirmed = result.outcome === 'ok' && result.data?.state === 'PAUSED'
                && result.data.session_id === session_id && result.data.dictation_id === active.value.draft_id;
            if (!confirmed && !['POLICY_DENIED', 'REVISION_CONFLICT'].includes(result.code)) {
                bindingUncertain.value = true;
                remember(active.value.draft_id);
                error.value = 'Das Lösen der Bindung wurde nicht bestätigt. Bitte versuchen Sie es erneut.';
                return false;
            }
            holder.value = null;
            bindingUncertain.value = false;
            remember(active.value.draft_id);
            controller?.released();
            message.value = confirmed ? 'Bindung gelöst.' : 'Dieses Browserfenster kann den Entwurf nicht mehr zum Diktieren verwenden.';
            return true;
        }

        function releaseOnExit(fence) {
            const { session_id, ...body } = fence;
            releaseLiveHolder({ sessionId: session_id }, body);
        }

        function pageHide(event) {
            hidden = true;
            pageVisit += 1;
            // A paused draft can keep its fence through reload. An active capture must let it go.
            automaticRule.value = null;
            if (!holder.value || (!automaticManaged.value && !event.persisted && controller?.getState().phase !== 'live')) return;
            releaseOnExit(holder.value);
            if (event.persisted) {
                if (automaticManaged.value) holder.value = null;
                bindingUncertain.value = !automaticManaged.value;
                remember(active.value?.draft_id);
            }
        }

        function pageShow() { hidden = false; }

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
            if (automaticRule.value?.draft_id === draft.draft_id) automaticRule.value = null;
            holder.value = null;
            bindingUncertain.value = false;
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
            if (liveView.value?.canLeave() === false) return;
            if (directDictation.value) {
                directDictation.value = null;
                action.after?.();
                return;
            }
            let moved = false;
            await run(async () => {
                if (!await readyToLeave()) return;
                if (saveFirst && !await save()) return;
                if (discard && !await archive()) return;
                if (action.id) {
                    const saved = message.value;
                    moved = await open(action.id);
                    if ((saveFirst || discard) && saved) message.value = [saved, moved ? message.value : ''].filter(Boolean).join(' ');
                    if (!moved && !discard) return;
                } else {
                    if (active.value) {
                        const saved = message.value;
                        if (!await release()) return;
                        message.value = [saved && saveFirst ? saved : 'Entwurf «' + active.value.title + '» geschlossen.',
                            message.value !== saved ? message.value : ''].filter(Boolean).join(' ');
                    }
                    clearEditor();
                }
                closeDialog(false);
                moved = true;
            });
            if (!moved) return;
            if (action.after) action.after();
            else await nextTick(() => {
                (active.value ? document.getElementById('clv-title') : heading.value)?.focus();
                bindOnFocus();
            });
        }

        function choose(action) {
            if (busy.value || automaticStopping.value || mode.value || (action.id && action.id === active.value?.draft_id)) return;
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
            const linked = choices.resolve(new URLSearchParams(location.search).get('dictation'));
            const initial = restored?.draftId || linked;
            const result = await run(() => request());
            if (!result || disposed) return;
            if (result.outcome !== 'ok') {
                error.value = 'Die Entwürfe konnten nicht geladen werden.';
                return;
            }
            rows.value = result.data.drafts.map(({ draft_id, title }) => ({ draft_id, title }));
            loaded.value = true;
            if (rows.value.some(row => row.draft_id === initial)) {
                await run(() => open(initial, !!restored?.holder && !restored.uncertain));
            } else directDictation.value = linked;
        }

        onMounted(() => {
            void load();
            void choices.load();
            globalThis.addEventListener('pagehide', pageHide);
            globalThis.addEventListener('pageshow', pageShow);
        });
        onBeforeUnmount(() => {
            disposed = true;
            if (automaticManaged.value && holder.value && !hidden) releaseOnExit(holder.value);
            choices.clear();
            globalThis.removeEventListener('pagehide', pageHide);
            globalThis.removeEventListener('pageshow', pageShow);
        });
        expose({ isDirty, needsLeave: () => !!active.value || !!directDictation.value || busy.value, leave: after => choose({ after }) });
        return { rows, active, directDictation, liveView, currentUser, text, title, dictation, busy, loaded, message, error, heading, dialog, cancelButton, mode, choices,
            available, readyToCreate, draftSession, automaticRule, automaticStopping, automaticClaiming, setAutomatic, bindOnFocus,
            showSwitchHint: computed(() => !!holder.value && rows.value.length > 1 && !automaticManaged.value),
            minTarget: MIN_TARGET, dangerTarget: DANGER, choose, create, load, ask,
            closeDialog, saveNow: () => run(save), saveAndLeave: () => finish(destination, { saveFirst: true }),
            discardAndLeave: () => finish(destination, { discard: true }), setController: value => { controller = value; } };
    },
    template: `
    <clinic-live-view v-if="directDictation" ref="liveView" :client="client" :dictation-id="directDictation"
                      :current-user="currentUser" />
    <div v-else class="v8-page" data-testid="clinic-live-picker">
        <div class="pagehead">
            <h1 ref="heading" tabindex="-1">Live-Diktat</h1>
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
            <p v-if="showSwitchHint" style="margin: 0">
                Wählen Sie einen anderen Entwurf, um das Diktatziel zu wechseln.
            </p>
            <form v-if="loaded && !active" class="actions" style="align-items: end" @submit.prevent="create">
                <label style="flex: 1 1 220px; min-width: 0; display: grid; gap: 4px; font-weight: 650">
                    <span>Eigene Aufnahme</span>
                    <input v-model="dictation" type="text" list="clinic-live-choices" data-testid="clinic-live-id" autocomplete="off"
                           :style="minTarget + '; border-color: var(--v8-text-2)'" :disabled="busy">
                </label>
                <label style="flex: 1 1 180px; min-width: 0; display: grid; gap: 4px; font-weight: 650">
                    <span>Name des Entwurfs</span>
                    <input v-model="title" type="text" data-testid="clinic-live-title" maxlength="200" autocomplete="off"
                           :style="minTarget + '; border-color: var(--v8-text-2)'" :disabled="busy">
                </label>
                <button type="submit" data-testid="clinic-live-open" class="button" :class="{ primary: readyToCreate && !busy }" :style="minTarget"
                        :disabled="busy || !readyToCreate">Entwurf anlegen</button>
            </form>
            <datalist id="clinic-live-choices"><option v-for="row in available" :key="row.id" :value="row.label"></option></datalist>
            <p v-if="!active && choices.state.message" class="hint" style="margin-top: 8px">{{ choices.state.message }}</p>
            <button v-if="!active && choices.state.cursor" type="button" class="button" :style="minTarget" :disabled="busy || choices.state.loading"
                    @click="choices.load(true)">Weitere Aufnahmen laden</button>
            <p role="status" style="margin: 0" :style="message ? 'margin-top: 12px' : null">{{ message }}</p>
            <p v-if="error && !mode" role="alert" style="margin: 12px 0 0; color: var(--text-danger-strong)">{{ error }}</p>
        </section>
        <LiveBindingRule v-if="active || automaticRule || automaticStopping" :draft-title="active?.title"
                         :rule-title="automaticRule?.title" :busy="busy" :claiming="automaticClaiming"
                         :uncertain="draftSession.uncertain" :stopping="automaticStopping" @change="setAutomatic" />
        <clinic-live-view v-if="active" ref="liveView" :key="active.draft_id" :client="client" :dictation-id="active.draft_id"
                          :current-user="currentUser"
                          :draft-title="active.title" :draft-session="draftSession" :draft-busy="busy" :on-ready="setController">
            <template #binding="{ phase, busy: recordingBusy }">
                <LiveBindingStatus :draft-title="active.title" :speaker-name="speakerName" :phase="phase"
                                   :bound="!!draftSession.position" :uncertain="draftSession.uncertain"
                                   :busy="busy || recordingBusy" @release="draftSession.release()" />
            </template>
            <template #draft-actions>
                <button type="button" class="button" :style="minTarget" data-testid="live-draft-save" :disabled="busy" @click="saveNow">Speichern</button>
                <button type="button" class="button" :style="minTarget" data-testid="live-draft-close" :disabled="busy" @click="choose({})">Schliessen</button>
                <button type="button" class="button" :style="dangerTarget" data-testid="live-draft-discard" :disabled="busy" @click="ask({}, 'discard')">Verwerfen</button>
            </template>
            <template #draft-text>
                <label for="clinic-live-draft-text" style="display: block; margin: 12px 16px 4px; font-weight: 650">Entwurfstext</label>
                <textarea id="clinic-live-draft-text" v-model="text" data-testid="live-draft-text" :readonly="busy" rows="6" @focus="bindOnFocus"
                          style="display: block; width: calc(100% - 32px); margin: 0 16px 12px; font-size: 16px; border-color: var(--v8-text-2)"></textarea>
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
