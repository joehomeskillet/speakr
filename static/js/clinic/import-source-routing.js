/** Routes files from a locally approved source to an existing author's import order. */
export const ImportSourceRouting = {
    props: {
        client: { type: Object, required: true },
        canConfigureBackup: { type: Boolean, default: false },
    },
    setup(props) {
        const { ref, onMounted } = window.Vue;
        const access = ref(false), sources = ref([]), authors = ref([]), pools = ref([]);
        const choice = ref(null), author = ref(null), pool = ref(null);
        const backupEnabled = ref(false), backupTemplate = ref(''), backupDays = ref(''), backupFolder = ref('');
        const notice = ref(''), busy = ref(false);
        let retry = null;

        async function load() {
            const result = await props.client.request('/imports/sources');
            access.value = result.outcome === 'ok';
            if (access.value) {
                sources.value = result.data.items;
                authors.value = result.data.authors;
                pools.value = result.data.pools;
            }
        }
        function edit(item) {
            choice.value = item;
            author.value = item.target_author_user_id;
            pool.value = item.target_pool_id;
            backupEnabled.value = item.backup_enabled === true;
            backupTemplate.value = item.backup_filename_template ?? '';
            backupDays.value = item.backup_retention_days ?? '';
            backupFolder.value = item.backup_folder ?? '';
            notice.value = '';
            retry = null;
        }
        async function save() {
            const item = choice.value;
            if (!item || busy.value) return;
            const body = { expected_version: item.policy_version };
            const routingChanged = author.value !== item.target_author_user_id || pool.value !== item.target_pool_id;
            if (routingChanged || !props.canConfigureBackup) {
                body.target_author_user_id = author.value;
                body.target_pool_id = pool.value;
            }
            const backup = {};
            if (props.canConfigureBackup) {
                if (backupEnabled.value !== (item.backup_enabled === true)) backup.backup_enabled = backupEnabled.value;
                if (backupEnabled.value) {
                    const days = backupDays.value === '' || backupDays.value == null ? null : Number(backupDays.value);
                    if (days !== null && (!Number.isInteger(days) || days < 1 || days > 36500)) {
                        notice.value = 'Bitte geben Sie eine ganze Zahl von 1 bis 36 500 Tagen ein oder lassen Sie das Feld leer.';
                        return;
                    }
                    const folder = backupFolder.value.trim();
                    if (!item.backup_folder) {
                        if (!folder) {
                            notice.value = 'Bitte geben Sie einen freigegebenen Ordner für die Sicherungskopien ein.';
                            return;
                        }
                        backup.backup_folder = folder;
                    }
                    const template = backupTemplate.value.trim();
                    if (template !== (item.backup_filename_template ?? '')) backup.backup_filename_template = template;
                    if (days !== (item.backup_retention_days ?? null)) backup.backup_retention_days = days;
                }
            }
            const backupChanged = Object.keys(backup).length > 0;
            if (!routingChanged && props.canConfigureBackup && !backupChanged) {
                notice.value = 'Keine Änderungen zum Speichern.';
                return;
            }
            Object.assign(body, backup);
            const signature = JSON.stringify(body);
            if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
            busy.value = true;
            try {
                const saved = await props.client.request('/imports/sources/' + encodeURIComponent(item.id),
                    { method: 'PUT', body, idempotencyKey: retry.key });
                if (saved.outcome === 'ok') {
                    choice.value = null;
                    await load();
                    notice.value = backupChanged
                        ? (routingChanged ? 'Importzuordnung und Sicherungseinstellungen gespeichert.' : 'Sicherungseinstellungen gespeichert.')
                        : 'Importzuordnung gespeichert.';
                } else if (saved.outcome === 'conflict') {
                    choice.value = null;
                    await load();
                    notice.value = backupChanged
                        ? 'Die Quelle wurde inzwischen geändert. Bitte erneut auswählen.'
                        : 'Die Zuordnung wurde inzwischen geändert. Bitte erneut auswählen.';
                } else {
                    notice.value = saved.outcome === 'permission' ? 'Berechtigung fehlt.'
                        : backupChanged && saved.outcome === 'validation' ? 'Bitte prüfen Sie die Angaben zur Sicherung und zur Importzuordnung.'
                            : 'Speichern fehlgeschlagen.';
                }
            } finally {
                busy.value = false;
            }
        }
        onMounted(load);
        return { access, sources, authors, pools, choice, author, pool, backupEnabled, backupTemplate,
            backupDays, backupFolder, notice, busy, edit, save };
    },
    template: `
    <section v-if="access" class="panel" aria-labelledby="source-routing-title">
      <h2 id="source-routing-title">Importzuordnung</h2>
      <p v-if="!sources.length" class="empty">Keine lokal freigegebene Quelle zur Verwaltung vorhanden.</p>
      <div v-for="source in sources" :key="source.id" class="space-y-2">
        <button type="button" class="small max-w-full break-all" :class="{ '!min-h-12': canConfigureBackup }" :disabled="busy" @click="edit(source)">
          {{ source.source_kind === 'device' ? 'Gerät' : 'Ordner' }} {{ source.source_ref }} {{ canConfigureBackup ? 'bearbeiten' : 'zuordnen' }}
        </button>
        <p v-if="choice?.id !== source.id" class="note">
          Importautor · {{ authors.find(a => a.id === source.target_author_user_id)?.name || 'Bestehende Zuordnung' }}
        </p>
        <p v-if="canConfigureBackup" class="note" data-testid="backup-expiry">
          <strong>Nächster Ablauf:</strong>&nbsp;
          <span v-if="!source.backup_enabled">Sicherungskopien ausgeschaltet.</span>
          <span v-else-if="source.backup_retention_days == null">Keine Aufbewahrungsfrist festgelegt.</span>
          <span v-else>Datum nicht verfügbar.</span>
          <span v-if="source.backup_retention_days != null"> Eingestellte Aufbewahrungsfrist: {{ source.backup_retention_days }} {{ source.backup_retention_days === 1 ? 'Tag' : 'Tage' }}.</span>
        </p>
        <form v-if="choice?.id === source.id" @submit.prevent="save" class="flex flex-wrap items-end gap-2">
          <label>Importautor
            <select v-model="author" :class="{ '!min-h-12 !border-ui-text-muted': canConfigureBackup }" :disabled="busy" required @change="pool = null">
              <option v-if="!authors.some(a => a.id === author)" :value="author" disabled>Bestehende Zuordnung</option>
              <option v-for="person in authors" :key="person.id" :value="person.id">{{ person.name }}</option>
            </select>
          </label>
          <label>Autorenpool
            <select v-model="pool" :class="{ '!min-h-12 !border-ui-text-muted': canConfigureBackup }" :disabled="busy">
              <option :value="null">Ohne Poolvorgabe</option>
              <option v-if="pool && !pools.some(p => p.author_id === author && p.id === pool)" :value="pool" disabled>Bestehende Zuordnung</option>
              <option v-for="p in pools.filter(p => p.author_id === author)" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </label>
          <fieldset v-if="canConfigureBackup" :disabled="busy" class="w-full min-w-0">
            <legend class="font-semibold">Sicherungskopien</legend>
            <label class="flex min-h-12 items-center gap-2">
              <input v-model="backupEnabled" type="checkbox" name="backup_enabled" data-testid="backup-enabled" class="shrink-0">
              Beim Import eine Sicherungskopie erstellen
            </label>
            <template v-if="backupEnabled">
              <div v-if="!source.backup_folder" class="field">
                <label for="source-backup-folder">Freigegebener Ordner für Sicherungskopien</label>
                <input id="source-backup-folder" v-model="backupFolder" type="text" name="backup_folder" data-testid="backup-folder" class="min-w-0 !min-h-12 !border-ui-text-muted" maxlength="1024" aria-describedby="source-backup-folder-help" required>
                <div id="source-backup-folder-help">Pfad zu einem freigegebenen Ordner ausserhalb des Importordners.</div>
              </div>
              <div class="field">
                <label for="source-backup-template">Muster für den Dateinamen</label>
                <input id="source-backup-template" v-model="backupTemplate" type="text" name="backup_filename_template" data-testid="backup-template" class="min-w-0 !min-h-12 !border-ui-text-muted" maxlength="255" placeholder="%A_%T" aria-describedby="source-backup-template-help">
                <div id="source-backup-template-help">%A steht für Autor, %T für Titel. Leer: Originalname als Grundlage.</div>
              </div>
              <div class="field">
                <label for="source-backup-days">Aufbewahrungsfrist in Tagen</label>
                <input id="source-backup-days" v-model.number="backupDays" type="number" name="backup_retention_days" data-testid="backup-days" class="min-w-0 !min-h-12 !border-ui-text-muted" min="1" max="36500" step="1" aria-describedby="source-backup-days-help">
                <div id="source-backup-days-help">Leer lassen für keine Frist.</div>
              </div>
            </template>
          </fieldset>
          <button type="submit" class="small primary" :class="{ '!min-h-12': canConfigureBackup }" :disabled="busy">Speichern</button>
          <button type="button" class="small" :class="{ '!min-h-12': canConfigureBackup }" :disabled="busy" @click="choice = null">Abbrechen</button>
        </form>
      </div>
      <p v-if="notice" role="status" class="note">{{ notice }}</p>
    </section>`,
};
