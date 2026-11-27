/** Routes files from a locally approved source to an existing author's import order. */
export const ImportSourceRouting = {
    props: { client: { type: Object, required: true } },
    setup(props) {
        const { ref, onMounted } = window.Vue;
        const access = ref(false), sources = ref([]), authors = ref([]), pools = ref([]);
        const choice = ref(null), author = ref(null), pool = ref(null);
        const notice = ref(''), busy = ref(false), allowDelete = ref(false);
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
            allowDelete.value = item.allow_source_delete === true;
            notice.value = '';
            retry = null;
        }
        async function save() {
            const item = choice.value;
            if (!item || busy.value) return;
            const body = { expected_version: item.policy_version };
            if (author.value !== item.target_author_user_id || pool.value !== item.target_pool_id) {
                body.target_author_user_id = author.value;
                body.target_pool_id = pool.value;
            }
            const cleanupChanged = item.can_set_source_delete && allowDelete.value !== (item.allow_source_delete === true);
            if (cleanupChanged) body.allow_source_delete = allowDelete.value;
            if (Object.keys(body).length === 1) {
                choice.value = null;
                notice.value = 'Keine Änderungen gespeichert.';
                return;
            }
            const signature = JSON.stringify(body);
            if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
            busy.value = true;
            try {
                const saved = await props.client.request('/imports/sources/' + encodeURIComponent(item.id),
                    { method: 'PUT', body, idempotencyKey: retry.key });
                if (saved.outcome === 'ok') {
                    choice.value = null;
                    await load();
                    notice.value = cleanupChanged ? 'Einstellung zum Löschen gespeichert.' : 'Importzuordnung gespeichert.';
                } else if (saved.outcome === 'conflict') {
                    choice.value = null;
                    await load();
                    notice.value = 'Die Zuordnung wurde inzwischen geändert. Bitte erneut auswählen.';
                } else {
                    notice.value = saved.outcome === 'permission' ? 'Berechtigung fehlt.' : 'Speichern fehlgeschlagen.';
                }
            } finally {
                busy.value = false;
            }
        }
        onMounted(load);
        return { access, sources, authors, pools, choice, author, pool, allowDelete, notice, busy, edit, save };
    },
    template: `
    <section v-if="access" class="panel" aria-labelledby="source-routing-title">
      <h2 id="source-routing-title">Importzuordnung</h2>
      <p v-if="!sources.length" class="empty">Keine lokal freigegebene Quelle zur Verwaltung vorhanden.</p>
      <div v-for="source in sources" :key="source.id" class="space-y-2">
        <button type="button" class="small min-h-12 focus-visible:outline-brand-accent" :disabled="busy" @click="edit(source)">
          {{ source.source_kind === 'device' ? 'Gerät' : 'Ordner' }} {{ source.source_ref }} zuordnen
        </button>
        <p v-if="choice?.id !== source.id" class="note">
          Importautor · {{ authors.find(a => a.id === source.target_author_user_id)?.name || 'Bestehende Zuordnung' }}
          <span v-if="source.can_set_source_delete" class="table">Automatisches Löschen: {{ source.allow_source_delete === true ? 'erlaubt' : 'aus' }}</span>
        </p>
        <form v-if="choice?.id === source.id" @submit.prevent="save" class="flex flex-wrap items-end gap-2">
          <label>Importautor
            <select v-model="author" :disabled="busy" required @change="pool = null">
              <option v-if="!authors.some(a => a.id === author)" :value="author" disabled>Bestehende Zuordnung</option>
              <option v-for="person in authors" :key="person.id" :value="person.id">{{ person.name }}</option>
            </select>
          </label>
          <label>Autorenpool
            <select v-model="pool" :disabled="busy">
              <option :value="null">Ohne Poolvorgabe</option>
              <option v-if="pool && !pools.some(p => p.author_id === author && p.id === pool)" :value="pool" disabled>Bestehende Zuordnung</option>
              <option v-for="p in pools.filter(p => p.author_id === author)" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </label>
          <div v-if="source.can_set_source_delete" class="w-full">
            <label class="inline-flex min-h-12 items-center gap-2">
              <input v-model="allowDelete" type="checkbox" class="focus-visible:outline-brand-accent" :disabled="busy" aria-describedby="source-delete-hint">
              Automatisches Löschen von dieser Quelle erlauben
            </label>
            <p id="source-delete-hint" class="hint">Nur wenn das Löschen beim Import gewählt wurde und Speakr die sichere Übernahme und Sicherung bestätigt hat.</p>
          </div>
          <button type="submit" class="small primary min-h-12 focus-visible:outline-brand-accent" :disabled="busy">Speichern</button>
          <button type="button" class="small min-h-12 focus-visible:outline-brand-accent" :disabled="busy" @click="choice = null">Abbrechen</button>
        </form>
      </div>
      <p v-if="notice" role="status" class="note">{{ notice }}</p>
    </section>`,
};
