/** Routes files from a locally approved source to an existing author's import order. */
export const ImportSourceRouting = {
    props: { client: { type: Object, required: true } },
    setup(props) {
        const { ref, onMounted } = window.Vue;
        const access = ref(false), sources = ref([]), authors = ref([]), pools = ref([]);
        const choice = ref(null), author = ref(null), pool = ref(null);
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
            notice.value = '';
            retry = null;
        }
        async function save() {
            const item = choice.value;
            if (!item || busy.value) return;
            const body = { expected_version: item.policy_version, target_author_user_id: author.value,
                target_pool_id: pool.value };
            const signature = JSON.stringify(body);
            if (retry?.signature !== signature) retry = { signature, key: crypto.randomUUID() };
            busy.value = true;
            try {
                const saved = await props.client.request('/imports/sources/' + encodeURIComponent(item.id),
                    { method: 'PUT', body, idempotencyKey: retry.key });
                if (saved.outcome === 'ok') {
                    choice.value = null;
                    await load();
                    notice.value = 'Importzuordnung gespeichert.';
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
        return { access, sources, authors, pools, choice, author, pool, notice, busy, edit, save };
    },
    template: `
    <section v-if="access" class="panel" aria-labelledby="source-routing-title">
      <h2 id="source-routing-title">Importzuordnung</h2>
      <p v-if="!sources.length" class="empty">Keine lokal freigegebene Quelle zur Verwaltung vorhanden.</p>
      <div v-for="source in sources" :key="source.id" class="space-y-2">
        <button type="button" class="small" :disabled="busy" @click="edit(source)">
          {{ source.source_kind === 'device' ? 'Gerät' : 'Ordner' }} {{ source.source_ref }} zuordnen
        </button>
        <p v-if="choice?.id !== source.id" class="note">
          Importautor · {{ authors.find(a => a.id === source.target_author_user_id)?.name || 'Bestehende Zuordnung' }}
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
          <button type="submit" class="small primary" :disabled="busy">Speichern</button>
          <button type="button" class="small" :disabled="busy" @click="choice = null">Abbrechen</button>
        </form>
      </div>
      <p v-if="notice" role="status" class="note">{{ notice }}</p>
    </section>`,
};
