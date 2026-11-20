import { createClinicApiClient } from './api-client.js';

const PATH = '/admin/task-models';
const sections = [
    { title: 'Transkription', description: 'Überträgt die Aufnahme in Text.', rows: [
        { id: 'asr_gpu', label: 'GPU-Modell' }, { id: 'asr_cpu', label: 'CPU-Modell' },
    ] },
    { title: 'Zusammenfassung', description: 'Fasst das gespeicherte Transkript zusammen.', rows: [
        { id: 'summary', label: 'Textmodell' },
    ] },
];
const sources = {
    override: 'Hier festgelegt', deployment: 'Installationsvorgabe', service: 'Vom lokalen Dienst',
    existing_default: 'Bestehender Standard', unset: 'Keine Vorgabe',
};
const keys = sections.flatMap(section => section.rows.map(row => row.id));

export default {
    name: 'ClinicModelSelection',
    setup() {
        const { ref, computed, onMounted, onBeforeUnmount } = window.Vue;
        const client = createClinicApiClient();
        const config = ref(null);
        const draft = ref({});
        const busy = ref(false);
        const error = ref('');
        const saved = ref(false);
        const conflict = ref(false);
        const overrides = computed(() => Object.fromEntries(keys.map(key => [key, draft.value[key]?.trim() || null])));
        const changed = computed(() => config.value && keys.some(key => overrides.value[key] !== config.value.models[key].override));
        async function request(save = false) {
            if (busy.value || (save && (!changed.value || conflict.value))) return;
            busy.value = true;
            error.value = '';
            saved.value = false;
            try {
                const res = await client.request(PATH, save ? {
                    method: 'PUT', body: { revision: config.value.revision, models: overrides.value },
                } : undefined);
                if (res.outcome === 'cancelled') return;
                if (res.outcome !== 'ok') {
                    conflict.value = res.status === 409;
                    error.value = conflict.value ? 'Die Modellwahl wurde zwischenzeitlich geändert. Bitte den aktuellen Stand neu laden.'
                        : res.status === 403 ? 'Für die Modellwahl fehlt die Administratorberechtigung.'
                        : res.status === 400 ? 'Mindestens eine Modellkennung ist für den lokalen Dienst ungültig.'
                        : save ? 'Die Modellwahl konnte nicht gespeichert werden.' : 'Die Modellwahl konnte nicht geladen werden.';
                    return;
                }
                config.value = res.data;
                draft.value = Object.fromEntries(keys.map(key => [key, res.data.models[key].override || '']));
                conflict.value = false;
                saved.value = save;
            } finally {
                busy.value = false;
            }
        }
        onMounted(() => request());
        onBeforeUnmount(() => client.switchContext());
        return { sections, sources, config, draft, changed, busy, error, saved, conflict, request };
    },
    template: `
<section class="v8-page clinic-model-selection" aria-labelledby="model-title">
    <nav class="cadm-crumb" aria-label="Brotkrumennavigation">
        <a href="/#/aufnahmen">Speakr</a><span aria-hidden="true">›</span><span>Verwaltung</span>
        <span aria-hidden="true">›</span><span aria-current="page">Modellwahl</span>
    </nav>
    <header class="pagehead"><div><h1 id="model-title">Modellwahl</h1>
        <p class="subline">Lokale Modelle für neue Verarbeitungsaufträge festlegen.</p>
    </div></header>
    <p v-if="busy && !config" role="status" class="note">Modellwahl wird geladen …</p>
    <div v-if="error" class="note warning" role="alert">
        <span v-text="error"></span> <button v-if="!config || conflict" type="button" :disabled="busy" @click="request()">Neu laden</button>
    </div>
    <form v-if="config" @submit.prevent="request(true)">
        <div class="grid equal">
            <section v-for="section in sections" :key="section.title" class="panel" :aria-label="section.title">
                <h2 v-text="section.title"></h2>
                <p class="subline" v-text="section.description"></p>
                <div v-for="row in section.rows" :key="row.id" class="model-setting">
                    <div class="model-input">
                        <label :for="'model-' + row.id" v-text="row.label"></label>
                        <select v-if="config.models[row.id].choices_strict && config.models[row.id].choices.length"
                            :id="'model-' + row.id" v-model="draft[row.id]" :disabled="busy"
                            :aria-describedby="'effective-' + row.id" @change="saved = false">
                            <option value="">Vorgabe übernehmen</option>
                            <option v-if="config.models[row.id].override && !config.models[row.id].choices.includes(config.models[row.id].override)"
                                :value="config.models[row.id].override" disabled v-text="config.models[row.id].override"></option>
                            <option v-for="name in config.models[row.id].choices" :key="name" :value="name" v-text="name"></option>
                        </select>
                        <template v-else>
                            <input :id="'model-' + row.id" v-model="draft[row.id]" type="text"
                                :list="'known-' + row.id" :aria-describedby="'effective-' + row.id" :disabled="busy"
                                autocomplete="off" spellcheck="false" placeholder="Vorgabe übernehmen" @input="saved = false">
                            <datalist :id="'known-' + row.id">
                                <option v-for="name in config.models[row.id].choices" :key="name" :value="name"></option>
                            </datalist>
                        </template>
                    </div>
                    <p :id="'effective-' + row.id" class="model-effective">
                        <span>{{ config.models[row.id].active ? 'Wirksam' : 'Derzeit inaktiv' }}:</span>
                        <strong v-text="config.models[row.id].effective || (config.models[row.id].active ? 'Keine Modellkennung gemeldet' : 'Kein Modell aktiv')"></strong>
                        <span v-text="sources[config.models[row.id].source]"></span>
                    </p>
                </div>
            </section>
        </div>
        <div class="model-save">
            <p class="t-meta">Ohne eigene Auswahl gilt die Vorgabe der Installation oder des lokalen Dienstes.</p>
            <button type="submit" class="primary" :disabled="busy || !changed || conflict">
                {{ busy ? 'Wird gespeichert …' : 'Modellwahl speichern' }}
            </button>
            <span v-if="saved" role="status">Modellwahl gespeichert.</span>
        </div>
    </form>
</section>`,
};
