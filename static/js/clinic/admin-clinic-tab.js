/**
 * Clinic administration panels for the admin and group-management pages (CLINIC_MODE only).
 *
 * A separate small Vue app with one shared api client, mounted on #clinic-admin-root. It moved here
 * from the inline script that templates/admin.html and templates/group-admin.html both carried; the
 * behaviour is unchanged. Both pages call mountClinicAdmin() from a one-line module script.
 */
import { createClinicApiClient } from './api-client.js';
import { createAccountsAdmin, AccountsAdminPanel } from './accounts-ui.js';
import { createPoolsComponent } from './pools-ui.js';
import { createGrantsComponent } from './grants-ui.js';
import { registerDirectoryReview } from './directory-ui.js';
import { templatesComponent } from './templates-ui.js';
import { categoriesComponent } from './categories-ui.js';
import { columnsComponent } from './columns-ui.js';
import { createRegexRulesComponent } from './regex-ui.js';
import { createPreferencesComponent } from './preferences-ui.js';

// Grants are read per user: the panel starts on the viewer's own id. The API decides who may read or change.
export function createGrantsHost(Vue, client, userId, team) {
    const { ref, markRaw, onMounted } = Vue;
    return {
        name: 'ClinicGrantsHost',
        setup() {
            const draft = ref(String(userId));
            const panel = ref(null);
            const key = ref(0);
            function open() {
                const id = Number(draft.value);
                panel.value = Number.isInteger(id) && id > 0
                    ? markRaw(createGrantsComponent(Vue, client, { userId: id, canEdit: !team }))
                    : null;
                key.value += 1;
            }
            onMounted(open);
            return { draft, panel, key, open };
        },
        template: `
<div>
  <form class="mb-3 flex flex-wrap items-end gap-2" @submit.prevent="open">
    <label for="clinic-grants-user">Benutzer-ID</label>
    <input id="clinic-grants-user" class="field field--sm" type="number" min="1" step="1" inputmode="numeric" v-model="draft">
    <button type="submit" class="btn btn--sm btn--ghost">Fachrechte laden</button>
  </form>
  <component :is="panel" v-if="panel" :key="key"></component>
</div>`,
    };
}

// Regex rules act on one dictation: the panel needs its id. The API authorises preview and apply.
export function createRegexHost(Vue, client) {
    const { ref, markRaw } = Vue;
    return {
        name: 'ClinicRegexHost',
        setup() {
            const draft = ref('');
            const panel = ref(null);
            const dictationId = ref('');
            const regexComponent = markRaw(createRegexRulesComponent(Vue));
            function open() {
                const id = draft.value.trim();
                dictationId.value = id;
                panel.value = id ? regexComponent : null;
            }
            return { draft, panel, dictationId, open, client };
        },
        template: `
<div>
  <form class="mb-3 flex flex-wrap items-end gap-2" @submit.prevent="open">
    <label for="clinic-regex-dictation">Diktat-ID</label>
    <input id="clinic-regex-dictation" class="field field--sm" type="text" autocomplete="off" v-model="draft">
    <button type="submit" class="btn btn--sm btn--ghost">Regeln laden</button>
  </form>
  <component :is="panel" v-if="panel" :key="dictationId" :client="client" :dictation-id="dictationId"></component>
</div>`,
    };
}

// probe: GET that decides, on the team page, whether the API authorises the panel for this account.
export function createPanelDefs(Vue, client, userId, team) {
    const { reactive, markRaw } = Vue;
    return [
        { id: 'accounts', label: 'Konten', probe: '/admin/users', comp: AccountsAdminPanel, props: { controller: createAccountsAdmin({ client, reactive }) } },
        { id: 'pools', label: 'Pools', probe: '/pools', comp: createPoolsComponent(Vue, client), props: {} },
        { id: 'grants', label: 'Fachrechte', probe: '/identity/users/' + userId + '/grants', comp: createGrantsHost(Vue, client, userId, team), props: {} },
        { id: 'directory', label: 'Verzeichnis', probe: '/directory/capability', comp: 'clinic-directory-review', props: { client, currentUser: { is_admin: !team } } },
        { id: 'templates', label: 'Vorlagen', probe: '/templates', comp: templatesComponent(Vue, { client }), props: {} },
        { id: 'categories', label: 'Kategorien', probe: '/categories', comp: categoriesComponent(Vue, { client }), props: {} },
        { id: 'regex', label: 'Regeln', probe: null, comp: createRegexHost(Vue, client), props: {} },
        { id: 'columns', label: 'Spalten', probe: '/metadata-fields', comp: columnsComponent(Vue, { client }), props: {} },
        { id: 'preferences', label: 'Vorgaben', probe: '/preferences/recipient-default', comp: createPreferencesComponent(Vue, client, {}), props: {} },
    ].map((d) => Object.assign(d, { comp: typeof d.comp === 'string' ? d.comp : markRaw(d.comp) }));
}

export function createRootComponent(Vue, client, defs, team) {
    const { ref, computed, onMounted } = Vue;
    return {
        setup() {
            const shown = ref(team ? [] : defs);
            const withheld = ref([]);
            const probing = ref(team);
            const activeId = ref(team ? null : defs[0].id);
            const active = computed(() => defs.find((d) => d.id === activeId.value) || null);
            const withheldText = computed(() => 'Für dieses Konto nicht freigegeben: ' + withheld.value.join(', ') + '.');
            async function probe() {
                const results = await Promise.all(defs.map((d) => (d.probe ? client.request(d.probe) : Promise.resolve({ outcome: 'ok' }))));
                shown.value = defs.filter((d, i) => results[i].outcome === 'ok');
                withheld.value = defs.filter((d, i) => results[i].outcome !== 'ok').map((d) => d.label);
                activeId.value = shown.value.length ? shown.value[0].id : null;
                probing.value = false;
            }
            onMounted(() => { if (team) probe(); });
            return { shown, withheld, withheldText, probing, activeId, active };
        },
        template: `
<section aria-labelledby="clinic-admin-title" data-testid="clinic-admin">
  <h2 id="clinic-admin-title" class="text-lg font-medium mb-3">Klinik-Verwaltung</h2>
  <p v-if="probing" role="status">Berechtigungen werden geprüft …</p>
  <template v-else>
    <p v-if="withheld.length" role="status" class="text-sm mb-3" data-testid="clinic-admin-withheld" v-text="withheldText"></p>
    <nav v-if="shown.length" class="tabs mb-4" aria-label="Klinik-Verwaltung">
      <button v-for="d in shown" :key="d.id" type="button" class="tab" :class="{ 'is-active': d.id === activeId }"
              :aria-current="d.id === activeId ? 'page' : null" :data-testid="'clinic-panel-' + d.id"
              @click="activeId = d.id"><span v-text="d.label"></span></button>
    </nav>
    <div v-if="active" :key="active.id" :data-testid="'clinic-view-' + active.id">
      <component :is="active.comp" v-bind="active.props" @open-accounts="activeId = 'accounts'"></component>
    </div>
  </template>
</section>`,
    };
}

// When the container sits inside #app, the page's own Vue app compiles it from the DOM: mount only after that has
// finished, or the page app would absorb this container.
export function mountClinicAdmin({ Vue = window.Vue, doc = document, client = createClinicApiClient(), MutationObserverImpl = globalThis.MutationObserver } = {}) {
    const root = doc.getElementById('clinic-admin-root');
    const team = root.dataset.mode === 'team';
    const userId = Number(root.dataset.userId);
    const defs = createPanelDefs(Vue, client, userId, team);
    const Root = createRootComponent(Vue, client, defs, team);

    function mountClinic() {
        const app = Vue.createApp(Root);
        registerDirectoryReview(app);
        app.mount(doc.getElementById('clinic-admin-root'));
    }
    const host = doc.getElementById('app');
    if (!host || !host.contains(root) || host.hasAttribute('data-v-app')) {
        mountClinic();
    } else {
        new MutationObserverImpl((_records, observer) => {
            if (host.hasAttribute('data-v-app')) { observer.disconnect(); mountClinic(); }
        }).observe(host, { attributes: true });
    }
}
