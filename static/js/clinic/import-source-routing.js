/** Routes files from locally approved devices and dropfolders to an author and pool. */
import { PRIORITY_OPTIONS } from './dictation-properties-ui.js';

export const LANGUAGE_OPTIONS = Object.freeze([
    Object.freeze({ value: 1031, label: 'Deutsch' }),
    Object.freeze({ value: 2055, label: 'Deutsch (Schweiz)' }),
    Object.freeze({ value: 1033, label: 'Englisch' }),
    Object.freeze({ value: 1036, label: 'Französisch' }),
    Object.freeze({ value: 1040, label: 'Italienisch' }),
]);

const REF_PATTERN = /^[A-Za-z0-9._:@-]{1,128}$/;
const PROFILE_PATTERN = /^[A-Za-z0-9._:@-]{1,36}$/;
const CONTROL_STYLE = '!min-h-12 focus-visible:!outline-ui-text';
const FIELD_STYLE = CONTROL_STYLE + ' !border-ui-text-muted';
const SOURCES_URL = '/imports/sources';

export function priorityLabel(value) {
    return PRIORITY_OPTIONS.find((opt) => opt.value === value)?.label || String(value ?? '');
}

export function languageLabel(value) {
    return LANGUAGE_OPTIONS.find((opt) => opt.value === value)?.label || (value != null ? `Sprache ${value}` : '–');
}

export const ImportSourceRouting = {
    props: {
        client: { type: Object, required: true },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        const { ref, onMounted, nextTick } = V;
        const access = ref(false), sources = ref([]), authors = ref([]), pools = ref([]);
        const creating = ref(false), choice = ref(null), confirmDeleteId = ref(null);
        const form = ref({}), formElement = ref(null), addButton = ref(null);
        const notice = ref(''), error = ref(''), errorElement = ref(null), busy = ref(false);
        let retry = null, trigger = null;

        function showError(message) {
            error.value = message;
            nextTick(() => errorElement.value?.focus());
        }

        function restoreFocus() {
            const previous = trigger;
            trigger = null;
            if (previous && !error.value) nextTick(() => (previous.isConnected ? previous : addButton.value)?.focus());
        }

        function resetForm(overrides) {
            form.value = {
                source_ref: '',
                approved_root: '',
                target_author_user_id: authors.value.length === 1 ? authors.value[0].id : null,
                target_pool_id: null,
                file_pattern: '*',
                priority: 1,
                language_id: 1031,
                asr_profile_id: '',
                ...overrides,
            };
        }

        async function load() {
            const result = await props.client.request(SOURCES_URL);
            if (result.outcome === 'ok') {
                access.value = true;
                sources.value = result.data.items;
                authors.value = result.data.authors;
                pools.value = result.data.pools;
            } else if (result.outcome === 'permission') {
                access.value = false;
            }
        }

        function startForm(item, button) {
            creating.value = !item;
            choice.value = item;
            notice.value = '';
            error.value = '';
            retry = null;
            trigger = button;
            resetForm(item && { ...item, asr_profile_id: item.asr_profile_id ?? '' });
            if (button) nextTick(() => formElement.value?.querySelector('input, select')?.focus());
        }

        function startCreate(button) {
            startForm(null, button);
        }

        function edit(item, button) {
            startForm(item, button);
        }

        function cancelForm() {
            creating.value = false;
            choice.value = null;
            retry = null;
            restoreFocus();
        }

        function onAuthorChange() {
            form.value.target_pool_id = null;
        }

        function askRemove(item, button) {
            if (busy.value || item.source_kind !== 'folder') return;
            confirmDeleteId.value = item.id;
            notice.value = '';
            error.value = '';
            trigger = button;
            if (button) nextTick(() => button.closest('[data-source-id]')
                ?.querySelector('[data-testid="cancel-remove-dropfolder"]')?.focus());
        }

        function cancelRemove() {
            confirmDeleteId.value = null;
            restoreFocus();
        }

        async function confirmRemove(item) {
            if (busy.value || confirmDeleteId.value !== item.id || item.source_kind !== 'folder') return;
            busy.value = true;
            notice.value = '';
            error.value = '';
            try {
                const removed = await props.client.request(SOURCES_URL + '/' + encodeURIComponent(item.id), {
                    method: 'DELETE',
                });
                if (removed.outcome === 'ok') {
                    sources.value = sources.value.filter((source) => source.id !== item.id);
                    await load();
                    notice.value = 'Ordnerkonfiguration entfernt.';
                } else if (removed.outcome === 'permission') {
                    showError('Sie dürfen diese Einstellung nicht entfernen, oder das Löschen von Quelldateien ist noch eingeschaltet.');
                } else {
                    await load();
                    showError('Das Entfernen konnte nicht bestätigt werden. Bitte prüfen Sie die Liste.');
                }
            } finally {
                busy.value = false;
                cancelRemove();
            }
        }

        async function save() {
            if (busy.value || (!creating.value && !choice.value)) return;
            notice.value = '';
            error.value = '';
            const isCreate = creating.value;
            const item = choice.value;
            const isFolder = isCreate || item?.source_kind === 'folder';
            const draft = form.value;
            const name = draft.source_ref.trim();
            const root = draft.approved_root.trim();
            const profileId = draft.asr_profile_id.trim();

            if (isCreate && !REF_PATTERN.test(name)) {
                showError('Geben Sie einen Kurznamen ohne Leerzeichen ein. Erlaubt sind A–Z, a–z, Zahlen und . _ - : @');
                return;
            }
            if (isFolder && !root) {
                showError('Bitte geben Sie einen Ordnerpfad an.');
                return;
            }
            if (draft.target_author_user_id == null) {
                showError('Bitte wählen Sie eine Person aus.');
                return;
            }
            if (isFolder && profileId && !PROFILE_PATTERN.test(profileId)) {
                showError('Die Profilkennung darf höchstens 36 Zeichen (Buchstaben, Zahlen, . _ - : @) enthalten.');
                return;
            }

            const body = {
                target_author_user_id: draft.target_author_user_id,
                target_pool_id: draft.target_pool_id,
            };
            if (isFolder) {
                Object.assign(body, {
                    approved_root: root, file_pattern: draft.file_pattern.trim() || '*',
                    priority: draft.priority, language_id: draft.language_id, asr_profile_id: profileId || null,
                });
            }
            let url = SOURCES_URL;
            const options = { method: isCreate ? 'POST' : 'PUT', body };
            if (isCreate) {
                Object.assign(body, { source_kind: 'folder', source_ref: name });
            } else {
                url += '/' + encodeURIComponent(item.id);
                body.expected_version = item.policy_version;
                const signature = JSON.stringify(body);
                if (retry?.signature !== signature) retry = { signature, key: globalThis.crypto.randomUUID() };
                options.idempotencyKey = retry.key;
            }
            busy.value = true;
            let close = false;
            try {
                const saved = await props.client.request(url, options);
                if (saved.outcome === 'ok') {
                    close = true;
                    sources.value = isCreate ? [...sources.value, saved.data]
                        : sources.value.map((source) => source.id === item.id ? saved.data : source);
                    await load();
                    notice.value = isCreate ? 'Ordner angelegt.'
                        : isFolder ? 'Ordnerkonfiguration gespeichert.' : 'Importzuordnung gespeichert.';
                } else if (saved.outcome === 'conflict') {
                    if (isCreate) {
                        showError('Ein Ordner mit diesem Namen existiert bereits.');
                    } else {
                        close = true;
                        await load();
                        showError('Die Zuordnung wurde inzwischen geändert. Bitte erneut auswählen.');
                    }
                } else if (saved.outcome === 'permission') {
                    showError(isFolder ? 'Der Ordnerpfad ist nicht freigegeben oder Ihnen fehlt die Berechtigung.'
                        : 'Ihnen fehlt die Berechtigung.');
                } else if (saved.outcome === 'validation') {
                    showError('Bitte prüfen Sie Ihre Eingaben.');
                } else if (isCreate) {
                    await load();
                    showError('Das Anlegen konnte nicht bestätigt werden. Bitte prüfen Sie die Liste.');
                } else {
                    showError('Das Speichern konnte nicht bestätigt werden. Bitte versuchen Sie es erneut.');
                }
            } finally {
                busy.value = false;
                if (close) cancelForm();
            }
        }

        function authorName(id) {
            return authors.value.find((a) => a.id === id)?.name || 'Bestehende Zuordnung';
        }

        function poolName(id) {
            if (!id) return 'Keine Gruppe vorgegeben';
            return pools.value.find((p) => p.id === id)?.name || 'Bestehende Zuordnung';
        }

        onMounted(load);
        return {
            access, sources, authors, pools, creating, choice, confirmDeleteId, form,
            notice, error, errorElement, busy, formElement, addButton, controlStyle: CONTROL_STYLE, fieldStyle: FIELD_STYLE,
            priorityOptions: PRIORITY_OPTIONS, languageOptions: LANGUAGE_OPTIONS,
            startCreate, edit, cancelForm, onAuthorChange, askRemove, cancelRemove, confirmRemove,
            save, authorName, poolName, priorityLabel, languageLabel,
        };
    },
    template: `
    <section v-if="access" class="panel" aria-labelledby="source-routing-title" data-testid="import-source-routing">
      <div class="panel-header flex-wrap">
        <h2 id="source-routing-title">Importzuordnung</h2>
        <button v-show="!creating && !choice && !confirmDeleteId" ref="addButton" type="button" class="primary"
                :class="controlStyle" data-testid="add-dropfolder" :disabled="busy" @click="startCreate($event.currentTarget)">
          Ordner hinzufügen
        </button>
      </div>

      <p v-if="notice" role="status" class="note" data-testid="source-routing-notice">{{ notice }}</p>
      <p v-if="error" ref="errorElement" role="alert" tabindex="-1" class="note warning" data-testid="source-routing-error">{{ error }}</p>

      <form v-if="creating || choice" ref="formElement" @submit.prevent="save"
            novalidate class="state-box mb-4"
            :data-testid="creating ? 'dropfolder-create-form' : 'source-edit-form'" aria-labelledby="isr-form-title">
        <h3 id="isr-form-title" class="break-words">{{ creating ? 'Neuer Eingangsordner' : choice.source_ref }}</h3>
        <div class="field-grid items-start">
          <div v-if="creating" class="field min-w-0">
            <label for="isr-name">Kurzname</label>
            <input id="isr-name" v-model="form.source_ref" :disabled="busy" :class="fieldStyle"
                   maxlength="128" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="isr-name-hint" required>
            <small id="isr-name-hint">Zum Beispiel labor-eingang, ohne Leerzeichen.</small>
          </div>
          <div v-if="creating || choice.source_kind === 'folder'" class="field min-w-0" :class="{ full: !creating }">
            <label for="isr-root">Ordnerpfad</label>
            <input id="isr-root" v-model="form.approved_root" :disabled="busy" :class="fieldStyle"
                   maxlength="1024" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="isr-root-hint" required>
            <small id="isr-root-hint">Verwenden Sie einen von der Klinik freigegebenen Pfad.</small>
          </div>
          <div class="field min-w-0">
            <label for="isr-author">{{ choice?.source_kind === 'device' ? 'Importautor' : 'Diktiert von' }}</label>
            <select id="isr-author" v-model="form.target_author_user_id" :disabled="busy" :class="fieldStyle"
                    required @change="onAuthorChange">
              <option :value="null" disabled>Bitte Person wählen</option>
              <option v-if="form.target_author_user_id && !authors.some(a => a.id === form.target_author_user_id)"
                      :value="form.target_author_user_id" disabled>Bestehende Zuordnung</option>
              <option v-for="person in authors" :key="person.id" :value="person.id">{{ person.name }}</option>
            </select>
          </div>
          <div class="field min-w-0">
            <label for="isr-pool">{{ choice?.source_kind === 'device' ? 'Autorenpool' : 'Autorengruppe' }}</label>
            <select id="isr-pool" v-model="form.target_pool_id" :disabled="busy" :class="fieldStyle">
              <option :value="null">{{ choice?.source_kind === 'device' ? 'Ohne Poolvorgabe' : 'Keine Gruppe vorgeben' }}</option>
              <option v-if="form.target_pool_id && !pools.some(p => p.author_id === form.target_author_user_id && p.id === form.target_pool_id)"
                      :value="form.target_pool_id" disabled>Bestehende Zuordnung</option>
              <option v-for="p in pools.filter(p => p.author_id === form.target_author_user_id)" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </div>
          <template v-if="creating || choice.source_kind === 'folder'">
            <div class="field min-w-0">
              <label for="isr-pattern">Dateimuster</label>
              <input id="isr-pattern" v-model="form.file_pattern" :disabled="busy" :class="fieldStyle"
                     maxlength="255" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="isr-pattern-hint">
              <small id="isr-pattern-hint">* für alle Dateien, *.ds2 für Dateien mit der Endung .ds2.</small>
            </div>
            <div class="field min-w-0">
              <label for="isr-priority">Priorität</label>
              <select id="isr-priority" v-model="form.priority" :disabled="busy" :class="fieldStyle">
                <option v-for="opt in priorityOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div class="field min-w-0">
              <label for="isr-language">Sprache</label>
              <select id="isr-language" v-model="form.language_id" :disabled="busy" :class="fieldStyle">
                <option v-if="!languageOptions.some(l => l.value === form.language_id)" :value="form.language_id">{{ languageLabel(form.language_id) }}</option>
                <option v-for="lang in languageOptions" :key="lang.value" :value="lang.value">{{ lang.label }}</option>
              </select>
            </div>
            <div class="field min-w-0">
              <label for="isr-profile">Spracherkennungsprofil (optional)</label>
              <input id="isr-profile" v-model="form.asr_profile_id" :disabled="busy" :class="fieldStyle"
                     maxlength="36" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="isr-profile-hint">
              <small id="isr-profile-hint">Leer lassen für die Standardeinstellung oder Profilkennung eingeben.</small>
            </div>
          </template>
        </div>
        <div class="actions">
          <button type="submit" class="primary" :class="controlStyle" :data-testid="creating ? 'save-new-dropfolder' : null" :disabled="busy">
            {{ creating ? 'Ordner anlegen' : 'Speichern' }}
          </button>
          <button type="button" :class="controlStyle" :data-testid="creating ? 'cancel-new-dropfolder' : null" :disabled="busy"
                  @click="error = ''; cancelForm()">Abbrechen</button>
        </div>
      </form>

      <p v-if="!sources.length && !creating" class="empty" data-testid="source-routing-empty">Keine Geräte oder Ordner eingerichtet.</p>

      <div v-for="source in sources" :key="source.id" class="state-box mt-3" :data-source-id="source.id">
        <div class="actions justify-between">
          <button type="button" class="min-w-0 max-w-full text-left" :class="controlStyle"
                  :disabled="busy || creating || !!choice || !!confirmDeleteId" @click="edit(source, $event.currentTarget)">
            <span class="min-w-0 break-words">
              {{ source.source_kind === 'device' ? 'Gerät ' + source.source_ref + ' zuordnen' : 'Ordner ' + source.source_ref + ' bearbeiten' }}
            </span>
          </button>
          <button v-if="source.source_kind === 'folder'" v-show="confirmDeleteId !== source.id" type="button"
                  :class="controlStyle" data-testid="remove-dropfolder" :disabled="busy || creating || !!choice || !!confirmDeleteId"
                  @click="askRemove(source, $event.currentTarget)">
            Konfiguration entfernen
          </button>
        </div>

        <div v-if="confirmDeleteId === source.id" role="region" aria-label="Entfernen bestätigen"
             class="note warning break-words" data-testid="confirm-remove-box">
          <p>Import für „{{ source.source_ref }}“ beenden? Die Dateien im Ordner bleiben erhalten.</p>
          <div class="actions">
            <button type="button" :class="controlStyle" data-testid="cancel-remove-dropfolder" :disabled="busy" @click="cancelRemove">Abbrechen</button>
            <button type="button" class="!text-brand-accent" :class="controlStyle" data-testid="confirm-remove-dropfolder" :disabled="busy" @click="confirmRemove(source)">Ja, entfernen</button>
          </div>
        </div>

        <template v-if="choice?.id !== source.id">
          <p class="note break-words">{{ source.source_kind === 'device' ? 'Importautor' : 'Diktiert von' }} · {{ authorName(source.target_author_user_id) }}</p>
          <dl v-if="source.source_kind === 'folder'" class="keyvalues" :data-testid="'folder-details-' + source.source_ref">
            <div><dt>Ordnerpfad</dt><dd class="min-w-0 break-words">{{ source.approved_root }}</dd></div>
            <div><dt>Autorengruppe</dt><dd class="min-w-0 break-words">{{ poolName(source.target_pool_id) }}</dd></div>
            <div><dt>Dateimuster</dt><dd class="min-w-0 break-words">{{ source.file_pattern || '*' }}</dd></div>
            <div><dt>Priorität</dt><dd>{{ priorityLabel(source.priority) }}</dd></div>
            <div><dt>Sprache</dt><dd>{{ languageLabel(source.language_id) }}</dd></div>
            <div><dt>Sprachprofil</dt><dd class="min-w-0 break-words">{{ source.asr_profile_id || 'Standard' }}</dd></div>
          </dl>
        </template>
      </div>
    </section>`,
};
