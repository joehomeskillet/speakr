import { loadGrantUsers } from './grants-ui.js';
import { POOL_TYPES } from './pools-ui.js';

const TARGET_LABELS = { author: 'Diktierende Person', author_pool: 'Autorengruppe' };
const TOUCH = 'min-height: var(--sp-7); min-width: var(--sp-7)';
const targetIds = (grant) => grant.scope === 'author' ? grant.author_ids : grant.pool_ids;

function validGrant(grant) {
    return typeof grant?.id === 'string' && grant.id.length > 0 &&
        Object.hasOwn(TARGET_LABELS, grant.scope) && Array.isArray(grant.author_ids) &&
        Array.isArray(grant.pool_ids) && targetIds(grant).length > 0;
}

const TEMPLATE = `
<section data-testid="statistics-grants" aria-label="Statistikfreigaben" style="margin-top: var(--sp-5)">
  <h3 ref="heading" tabindex="-1" style="overflow-wrap: break-word">Statistikfreigaben</h3>
  <p class="hint">Dieses Konto sieht nur zusammengefasste Zahlen. Die Freigabe gibt keinen Zugriff auf Ton, Diktattexte oder die Kontoverwaltung.</p>
  <p v-if="state.loading" class="hint" role="status">Statistikfreigaben werden geladen …</p>
  <div v-if="state.error" class="note">
    <p role="alert">{{ state.error }}</p>
    <button ref="reloadButton" type="button" :style="touch" @click="load(true)">Aktuellen Stand laden</button>
  </div>
  <div role="status"><p v-if="state.success" class="note">{{ state.success }}</p></div>
  <form v-if="state.items !== null" @submit.prevent="addGrant">
    <div style="display: flex; flex-wrap: wrap; column-gap: var(--sp-4)">
    <div class="field" style="flex: 1 1 14rem; min-width: 0">
      <label :for="'statistics-scope-' + userId">Freigabe für</label>
      <select :id="'statistics-scope-' + userId" v-model="state.scope" :disabled="!canWrite || !!state.confirmId" :style="selectStyle" @change="state.target = ''">
        <option value="author">Diktierende Person</option>
        <option value="author_pool">Autorengruppe</option>
      </select>
    </div>
    <div class="field" style="flex: 1 1 14rem; min-width: 0">
      <label :for="'statistics-target-' + userId">{{ targetLabel }}</label>
      <select :id="'statistics-target-' + userId" ref="targetSelect" v-model="state.target" :disabled="!canWrite || !!state.confirmId" :style="selectStyle">
        <option value="" disabled>Bitte auswählen</option>
        <option v-for="option in targets" :key="option.id" :value="option.id" :disabled="authorized.has(option.id)">{{ option.name }}</option>
      </select>
    </div>
    </div>
    <p v-if="canWrite && !targets.some(option => !authorized.has(option.id))" class="hint">Hier ist nichts Weiteres zum Freigeben verfügbar.</p>
    <div class="head-actions">
      <button type="submit" class="primary" :style="canAdd ? touch : disabledPrimary" :disabled="!canAdd">Statistik freigeben</button>
    </div>
  </form>
  <p v-if="state.version !== null && !state.items.length" class="note">Keine Statistikfreigaben für dieses Konto.</p>
  <ul v-if="state.items?.length" role="list" aria-label="Erteilte Statistikfreigaben" style="margin-top: var(--sp-4)">
    <li v-for="grant in state.items" :key="grant.id" class="note">
      <p style="overflow-wrap: anywhere"><strong>{{ labels[grant.scope] }}:</strong> {{ namesOf(grant) }}</p>
      <div v-if="state.confirmId === grant.id" role="group" :aria-labelledby="'statistics-revoke-' + grant.id" @keydown.esc.stop.prevent="cancelRevoke">
        <p :id="'statistics-revoke-' + grant.id">Statistikzugriff entziehen?</p>
        <div class="actions">
          <button type="button" class="danger" :style="touch" :disabled="!canWrite" @click="revokeGrant(grant)">Entziehen</button>
          <button :ref="rememberCancel" type="button" :style="touch" :disabled="state.writing" @click="cancelRevoke">Abbrechen</button>
        </div>
      </div>
      <button v-else type="button" :style="touch" :disabled="!canWrite || !!state.confirmId" @click="confirmRevoke(grant)">Zugriff entziehen</button>
    </li>
  </ul>
</section>`;

export function createStatisticsGrantsPanel(Vue) {
    return {
        name: 'ClinicStatisticsGrantsPanel',
        props: {
            client: { type: Object, required: true },
            adminClient: { type: Object, required: true },
            userId: { type: Number, required: true },
        },
        template: TEMPLATE,
        setup(props) {
            const userId = props.userId;
            const path = '/statistics/users/' + userId + '/grants';
            const state = Vue.reactive({
                items: null, version: null, loading: false, writing: false, error: '', success: '',
                users: [], pools: [], scope: 'author', target: '', confirmId: null,
            });
            const heading = Vue.ref(null);
            const reloadButton = Vue.ref(null);
            const targetSelect = Vue.ref(null);
            let cancelButton;
            const rememberCancel = (button) => { cancelButton = button; };

            const lists = Vue.computed(() => ({ author: state.users, author_pool: state.pools }));
            const targetLabel = Vue.computed(() => TARGET_LABELS[state.scope]);
            const targets = Vue.computed(() => lists.value[state.scope]);
            const authorized = Vue.computed(() => new Set(state.items?.filter((grant) => grant.scope === state.scope).flatMap(targetIds)));
            const canWrite = Vue.computed(() => state.version !== null && !state.loading && !state.writing);
            const canAdd = Vue.computed(() => canWrite.value && !state.confirmId &&
                targets.value.some(({ id }) => id === state.target) && !authorized.value.has(state.target));
            const nameById = Vue.computed(() => Object.fromEntries(Object.entries(lists.value).map(
                ([scope, options]) => [scope, new Map(options.map(({ id, name }) => [id, name]))],
            )));
            const namesOf = (grant) => targetIds(grant).map((id) => nameById.value[grant.scope].get(id) ||
                (grant.scope === 'author' ? 'Unbekanntes Konto (' + id + ')' : 'Unbekannte Autorengruppe (' + id + ')')).join(', ');

            const focus = (getElement) => Vue.nextTick(() => getElement()?.focus());
            const versioned = (data) => data?.user_id === userId && Number.isSafeInteger(data.version) && data.version >= 0;

            async function load(restoreFocus = false) {
                if (state.loading || state.writing) return;
                state.loading = true;
                state.error = '';
                state.version = null;
                try {
                    const [grants, directory, pools] = await Promise.all([
                        props.client.request(path), loadGrantUsers(props.adminClient), props.client.request('/pools'),
                    ]);
                    const data = grants?.data;
                    const authorPools = pools?.data?.[POOL_TYPES.author.listKey];
                    if (grants?.outcome !== 'ok' || !versioned(data) || !Array.isArray(data.items) || !data.items.every(validGrant) ||
                        directory.message || pools?.outcome !== 'ok' || !Array.isArray(authorPools)) {
                        throw new Error('Statistikfreigaben oder Auswahl nicht verfügbar');
                    }
                    state.users = directory.users.map(({ id, username }) => ({ id, name: username }));
                    state.pools = authorPools.filter(({ id, name }) => typeof id === 'string' && typeof name === 'string');
                    state.items = data.items;
                    state.version = data.version;
                } catch {
                    state.error = 'Statistikfreigaben oder Auswahl konnten nicht geladen werden. Bitte laden Sie den aktuellen Stand.';
                } finally {
                    state.loading = false;
                }
                if (restoreFocus) await focus(() => state.error ? reloadButton.value : heading.value);
            }

            async function write(method, fields, grantId) {
                const expectedVersion = state.version;
                state.writing = true;
                state.success = '';
                const result = await props.client.request(grantId ? path + '/' + encodeURIComponent(grantId) : path, {
                    method, body: { expected_version: expectedVersion, ...fields },
                }).catch(() => null);
                const data = result?.data;
                state.confirmId = null;
                if (result?.outcome !== 'ok' || !versioned(data) || data.version <= expectedVersion ||
                    (!grantId && !validGrant(data.grant))) {
                    state.version = null;
                    state.error = (result?.outcome === 'conflict' ? 'Die Freigaben wurden inzwischen geändert.' :
                        'Die Änderung konnte nicht bestätigt werden.') + ' Laden Sie den aktuellen Stand, bevor Sie erneut etwas ändern.';
                } else {
                    state.version = data.version;
                    if (grantId) {
                        state.items = state.items.filter((grant) => grant.id !== grantId);
                        state.success = 'Statistikzugriff wurde entzogen.';
                    } else {
                        state.items = [...state.items, data.grant];
                        state.target = '';
                        state.success = 'Statistikzugriff wurde freigegeben.';
                    }
                }
                state.writing = false;
                await focus(() => state.error ? reloadButton.value : (grantId ? heading.value : targetSelect.value));
            }

            function addGrant() {
                if (!canAdd.value) return;
                const { scope, target } = state;
                return write('POST', { scope, [scope === 'author' ? 'author_ids' : 'pool_ids']: [target] });
            }

            function confirmRevoke(grant) {
                if (!canWrite.value || state.confirmId) return;
                state.confirmId = grant.id;
                return focus(() => cancelButton);
            }

            function cancelRevoke(event) {
                if (state.writing) return;
                const row = event.currentTarget.closest('li');
                state.confirmId = null;
                return focus(() => row.querySelector('button'));
            }

            function revokeGrant(grant) {
                if (!canWrite.value || state.confirmId !== grant.id) return;
                return write('DELETE', {}, grant.id);
            }

            Vue.onMounted(() => load());
            return {
                state, userId, heading, reloadButton, targetSelect, rememberCancel, touch: TOUCH,
                selectStyle: 'min-height: var(--sp-7); min-width: 0; border-color: var(--v8-text-2)',
                disabledPrimary: TOUCH + '; opacity: 1; color: var(--v8-text-2); background: var(--v8-s2); border-color: var(--v8-text-2); cursor: not-allowed',
                labels: TARGET_LABELS,
                targetLabel, targets, authorized, canWrite, canAdd, namesOf, load, addGrant,
                confirmRevoke, cancelRevoke, revokeGrant,
            };
        },
    };
}
