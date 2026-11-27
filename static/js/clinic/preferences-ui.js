/**
 * Recipient defaults, self-correction and workstation overrides (w166).
 *
 * PAR-ADM-006, PAR-ADM-007, PAR-ADM-011, PAR-CFG-001. Reuses the clinic api
 * client (static/js/clinic/api-client.js) and the status tokens
 * (static/js/clinic/tokens.js). Server state lives only in memory: nothing is
 * written to browser storage. The server revalidates every save and every use,
 * so removed selections are dropped here after each reload and each context
 * switch, and the server is asked again before the default is applied.
 *
 * German labels go through the injected `t` (existing i18n mechanism, keys
 * `clinic.preferences.*`); the German default applies when a key is missing.
 */

import { surfaceFor } from './tokens.js';

const BASE = '/preferences';

export const MODES = Object.freeze(['all_eligible', 'person', 'staff_pool', 'self_correction']);
export const CHOICES = Object.freeze(['default_only', 'staff_pools_only', 'open']);
export const LOCKED_ORIGINS = Object.freeze(['central', 'ceiling']);

const LABELS = Object.freeze({
    'clinic.preferences.title': 'Empfänger und Arbeitsplatz',
    'clinic.preferences.mode': 'Standardempfänger',
    'clinic.preferences.mode.all_eligible': 'Alle berechtigten Personen',
    'clinic.preferences.mode.person': 'Bestimmte Person',
    'clinic.preferences.mode.staff_pool': 'Schreibteam',
    'clinic.preferences.mode.self_correction': 'Selbstkorrektur',
    'clinic.preferences.target': 'Empfänger auswählen',
    'clinic.preferences.choice': 'Abweichende Auswahl beim Diktat',
    'clinic.preferences.choice.default_only': 'Nur der Standardempfänger',
    'clinic.preferences.choice.staff_pools_only': 'Nur Schreibteams',
    'clinic.preferences.choice.open': 'Personen und Schreibteams',
    'clinic.preferences.default_missing': 'Noch kein Standardempfänger gespeichert.',
    'clinic.preferences.default_unavailable': 'Der bisherige Standardempfänger ist nicht mehr verfügbar.',
    'clinic.preferences.teams_empty': 'Es ist kein Schreibteam verfügbar.',
    'clinic.preferences.workstation': 'Arbeitsplatz-Einstellungen',
    'clinic.preferences.locked': 'Zentral festgelegt',
    'clinic.preferences.save': 'Übernehmen',
    'clinic.preferences.cancel': 'Abbrechen',
    'clinic.preferences.retry': 'Erneut versuchen',
    'clinic.preferences.reload': 'Neu laden',
    'clinic.preferences.removed': 'Die gewählte Auswahl ist nicht mehr verfügbar.',
    'clinic.preferences.saved': 'Gespeichert.',
    'clinic.preferences.loading': 'Wird geladen …',
});

const SERVER_MESSAGES = Object.freeze({
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    recipient_not_eligible: 'Der Empfänger ist nicht mehr berechtigt.',
    not_author_pool: 'Kein Autorenpool zugewiesen.',
    choice_disabled: 'Diese Auswahl ist zentral gesperrt.',
});

function sameList(a, b) {
    return a.length === b.length && a.every((v, i) => v === b[i]);
}

/**
 * @param {{client: {request: Function, switchContext: Function}, workstationId?: string|null,
 *          t?: (key: string) => string}} deps
 */
export function createPreferencesController(deps) {
    const client = deps.client;
    const translate = typeof deps.t === 'function' ? deps.t : null;
    let workstationId = deps.workstationId || null;
    const listeners = new Set();

    function label(key) {
        const text = translate ? translate(key) : key;
        return text && text !== key ? text : (LABELS[key] || key);
    }

    const state = {
        loading: false,
        busy: false,
        error: null, // { outcome, code, message, retryable }
        notice: null,
        version: 0,
        mode: 'all_eligible',
        targetIds: [],
        choice: 'open',
        overrides: null,
        recipientChoiceGrant: null,
        defaultTargets: null,
        hasDefault: false,
        workstation: { values: {}, origins: {}, version: 0 },
        draft: { mode: 'all_eligible', targetIds: [], choice: 'open', changes: {} },
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function fail(res, fallbackCode) {
        const code = res.code || fallbackCode || null;
        const message = SERVER_MESSAGES[String(code).toLowerCase()] || res.message;
        state.error = {
            outcome: res.outcome, code, message,
            retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
        };
        return false;
    }

    // Targets the server no longer offers are dropped from the draft.
    function revalidate() {
        const draft = state.draft;
        let removed = false;
        if (modeAllowed(draft.mode) && (draft.mode === 'person' || draft.mode === 'staff_pool')) {
            const offered = new Set(optionIds(draft.mode));
            const kept = draft.targetIds.filter((id) => offered.has(String(id)));
            if (!sameList(kept, draft.targetIds)) {
                draft.targetIds = kept;
                removed = true;
            }
        }
        if (draft.mode === 'self_correction' && state.overrides && state.overrides.self_correction === false) {
            draft.mode = 'all_eligible';
            removed = true;
        }
        for (const key of Object.keys(draft.changes)) {
            if (isLocked(key)) {
                delete draft.changes[key];
                removed = true;
            }
        }
        if (removed) state.notice = label('clinic.preferences.removed');
        return removed;
    }

    function modeAllowed(mode) {
        return state.recipientChoiceGrant !== 'default_only'
            && !(mode === 'person' && state.recipientChoiceGrant === 'staff_pools_only');
    }

    function targetOptions(mode) {
        const o = state.defaultTargets || state.overrides;
        if (!o || typeof o !== 'object') return [];
        const list = mode === 'person' ? (o.person || o.persons) : (o.staff_pool || o.staff_pools);
        if (!Array.isArray(list)) return [];
        return list.map((x) => {
            const value = String(x && typeof x === 'object' ? x.id : x);
            return { value, name: x?.name || value };
        });
    }

    function optionIds(mode) {
        return targetOptions(mode).map((option) => option.value);
    }

    function isLocked(key) {
        return LOCKED_ORIGINS.includes(state.workstation.origins[key]);
    }

    async function load() {
        state.loading = true;
        state.error = null;
        state.notice = null;
        emit();
        const def = await client.request(BASE + '/recipient-default');
        if (def.outcome !== 'ok') {
            state.loading = false;
            fail(def);
            emit();
            return false;
        }
        const d = def.data || {};
        state.version = d.version || 0;
        state.mode = d.mode || 'all_eligible';
        state.targetIds = Array.isArray(d.target_ids) ? d.target_ids.map(String) : [];
        state.choice = d.choice || 'open';
        state.overrides = d.overrides || null;
        state.recipientChoiceGrant = CHOICES.includes(d.recipient_choice_grant) ? d.recipient_choice_grant : null;
        state.defaultTargets = d.default_targets || null;
        state.hasDefault = !!d.mode;
        state.draft = { mode: state.mode, targetIds: state.targetIds.slice(), choice: state.choice, changes: {} };
        if (workstationId) {
            const ws = await client.request(BASE + '/workstations/' + encodeURIComponent(workstationId));
            if (ws.outcome !== 'ok') {
                state.loading = false;
                fail(ws);
                emit();
                return false;
            }
            const w = ws.data || {};
            state.workstation = { values: w.values || {}, origins: w.origins || {}, version: w.version || 0 };
        }
        revalidate();
        state.loading = false;
        emit();
        return true;
    }

    function setMode(mode) {
        if (!MODES.includes(mode) || !modeAllowed(mode)) return false;
        if (state.recipientChoiceGrant && mode !== state.draft.mode) state.draft.targetIds = [];
        state.draft.mode = mode;
        if (mode !== 'person' && mode !== 'staff_pool') state.draft.targetIds = [];
        if (mode === 'self_correction' && !state.recipientChoiceGrant) state.draft.choice = 'open';
        emit();
        return true;
    }

    function setTarget(id) {
        if (!modeAllowed(state.draft.mode) || !optionIds(state.draft.mode).includes(String(id))) return false;
        state.draft.targetIds = [String(id)];
        emit();
        return true;
    }

    function setChoice(choice) {
        if (state.recipientChoiceGrant || !CHOICES.includes(choice)) return false;
        state.draft.choice = choice;
        emit();
        return true;
    }

    function setWorkstationValue(key, value) {
        if (isLocked(key)) return false;
        state.draft.changes[key] = value;
        emit();
        return true;
    }

    async function saveDefault() {
        const d = state.draft;
        if (!modeAllowed(d.mode)) return false;
        if ((d.mode === 'person' || d.mode === 'staff_pool') && d.targetIds.length !== 1) {
            state.error = { outcome: 'validation', code: 'INVALID_PAYLOAD', message: label('clinic.preferences.target'), retryable: false };
            emit();
            return false;
        }
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.request(BASE + '/recipient-default', {
            method: 'PUT',
            body: {
                mode: d.mode, target_ids: d.targetIds, expected_version: state.version,
                ...(!state.recipientChoiceGrant ? { choice: d.choice } : {}),
            },
        });
        state.busy = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const loaded = await load();
        if (loaded) { state.notice = label("clinic.preferences.saved"); emit(); }
        return true;
    }

    async function saveWorkstation() {
        if (!workstationId || !Object.keys(state.draft.changes).length) return true;
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.request(BASE + '/workstations/' + encodeURIComponent(workstationId), {
            method: 'PUT',
            body: { changes: state.draft.changes, expected_version: state.workstation.version },
        });
        state.busy = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const loaded = await load();
        if (loaded) { state.notice = label("clinic.preferences.saved"); emit(); }
        return true;
    }

    function cancel() {
        state.draft = { mode: state.mode, targetIds: state.targetIds.slice(), choice: state.choice, changes: {} };
        state.error = null;
        emit();
    }

    // Account or workstation change: abort in-flight calls, drop all state, reload.
    async function switchContext(next) {
        const n = next || {};
        workstationId = n.workstationId !== undefined ? n.workstationId : workstationId;
        client.switchContext({ accountId: n.accountId, dictationId: n.dictationId });
        state.workstation = { values: {}, origins: {}, version: 0 };
        state.overrides = null;
        state.recipientChoiceGrant = null;
        state.defaultTargets = null;
        state.hasDefault = false;
        state.draft = { mode: 'all_eligible', targetIds: [], choice: 'open', changes: {} };
        return load();
    }

    // Use time: ask the server whether the stored default still applies.
    async function resolve(override) {
        const res = await client.request(BASE + '/recipient-default/resolve', {
            method: 'POST',
            body: { override: override === undefined ? null : override, expected_version: state.version },
        });
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return null;
        }
        return res.data;
    }

    function view() {
        const d = state.draft;
        const errorSurface = state.error ? surfaceFor('error') : null;
        const dirty = !sameDefault(d, state) || Object.keys(d.changes).length > 0;
        const grant = state.recipientChoiceGrant;
        const namedDefault = state.mode === 'person' || state.mode === 'staff_pool';
        const defaultName = namedDefault
            ? targetOptions(state.mode).find((target) => state.targetIds.includes(target.value))?.name
            : label('clinic.preferences.mode.' + state.mode);
        const targets = !modeAllowed(d.mode) || !['person', 'staff_pool'].includes(d.mode)
            ? [] : targetOptions(d.mode).map((target) => ({ ...target, selected: d.targetIds.includes(target.value) }));
        return {
            title: label('clinic.preferences.title'),
            dirty,
            recipientChoiceGrant: grant && label('clinic.preferences.choice.' + grant),
            defaultOnly: grant === 'default_only',
            savedDefault: !state.hasDefault ? label('clinic.preferences.default_missing') :
                defaultName || label('clinic.preferences.default_unavailable'),
            targetMessage: grant && (!modeAllowed(d.mode) && grant !== 'default_only'
                ? label('clinic.preferences.default_unavailable') :
                (d.mode === 'staff_pool' && targets.length === 0 ? label('clinic.preferences.teams_empty') : null)),
            modes: MODES.filter(modeAllowed).map((m) => ({
                value: m,
                label: label('clinic.preferences.mode.' + m),
                selected: d.mode === m,
                disabled: m === 'self_correction' && !!state.overrides && state.overrides.self_correction === false,
            })),
            targets,
            choices: CHOICES.map((c) => ({
                value: c, label: label('clinic.preferences.choice.' + c), selected: d.choice === c,
                disabled: d.mode === 'self_correction' && c !== 'open',
            })),
            settings: Object.keys(state.workstation.values).map((key) => ({
                key,
                value: key in d.changes ? d.changes[key] : state.workstation.values[key],
                locked: isLocked(key),
                lockedLabel: isLocked(key) ? label('clinic.preferences.locked') : '',
            })),
            error: state.error,
            errorSurface,
            notice: state.notice,
            // Labels and keyboard behaviour for the markup.
            a11y: {
                role: 'form',
                modeGroup: { role: 'radiogroup', label: label('clinic.preferences.mode') },
                choiceGroup: { role: 'radiogroup', label: label('clinic.preferences.choice') },
                alert: { role: 'alert', live: 'assertive' },
                status: { role: 'status', live: 'polite' },
                keys: { select: ['Enter', ' '], move: ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'], cancel: ['Escape'] },
            },
            labels: {
                save: label('clinic.preferences.save'),
                cancel: label('clinic.preferences.cancel'),
                retry: label('clinic.preferences.retry'),
                reload: label('clinic.preferences.reload'),
                target: label('clinic.preferences.target'),
                workstation: label('clinic.preferences.workstation'),
                loading: label('clinic.preferences.loading'),
            },
        };
    }

    // Arrow keys move within a radio group; Escape cancels; wraps around.
    function onKeydown(event, group) {
        const key = event && event.key;
        if (key === 'Escape') {
            cancel();
            return true;
        }
        if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(key)) return false;
        const list = group === 'choice' ? CHOICES : MODES;
        const current = group === 'choice' ? state.draft.choice : state.draft.mode;
        const step = key === 'ArrowUp' || key === 'ArrowLeft' ? -1 : 1;
        const next = list[(list.indexOf(current) + step + list.length) % list.length];
        if (group === 'choice') return setChoice(next);
        return setMode(next);
    }

    return {
        state, view, load, setMode, setTarget, setChoice, setWorkstationValue,
        saveDefault, saveWorkstation, cancel, switchContext, resolve, onKeydown,
        subscribe(fn) {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
    };
}

/** Vue composable: reactive snapshot of the controller view (needs window.Vue). */
export function usePreferences(deps, VueArg) {
    const controller = createPreferencesController(deps);
    const Vue = VueArg || (typeof window !== 'undefined' ? window.Vue : null);
    if (!Vue) return { controller, view: null };
    const view = Vue.shallowRef(controller.view());
    const unsubscribe = controller.subscribe(() => { view.value = controller.view(); });
    return { controller, view, unsubscribe };
}

const PANEL_TEMPLATE = `
<section class="clinic-preferences" :aria-label="view.title" data-testid="clinic-preferences" @keydown="onKeydown">
  <h2 class="text-lg font-semibold text-[var(--text-primary)]">{{ view.title }}</h2>
  <p class="mb-4 mt-1 text-sm text-[var(--text-muted)]">Hier sehen Sie, wer Ihre Diktate normalerweise erhält.</p>
  <p v-if="view.error" role="alert" aria-live="assertive" class="mb-3 p-2 border rounded" :style="errorStyle"
     data-testid="clinic-preferences-error">{{ view.error.message }}</p>
  <p v-if="view.notice" role="status" aria-live="polite" class="mb-3 p-2 border rounded" :style="noticeStyle"
     data-testid="clinic-preferences-notice">{{ view.notice }}</p>
  <p v-if="loading" role="status" aria-live="polite">{{ view.labels.loading }}</p>
  <form v-else @submit.prevent="onSave" :aria-busy="busy ? 'true' : 'false'" novalidate>
    <div v-if="view.defaultOnly" class="mb-4">
      <h3 class="text-sm font-semibold">{{ view.a11y.modeGroup.label }}</h3>
      <p data-testid="clinic-preferences-saved-default">{{ view.savedDefault }}</p>
    </div>
    <fieldset v-else class="mb-4 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" :aria-label="view.a11y.modeGroup.label" role="radiogroup">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.a11y.modeGroup.label }}</legend>
      <label v-for="m in view.modes" :key="m.value" class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]" :style="view.recipientChoiceGrant ? { minHeight: 'var(--sp-7)' } : null">
        <input type="radio" name="clinic-preferences-mode" :value="m.value" :checked="m.selected" :disabled="m.disabled || busy"
               :data-testid="'clinic-preferences-mode-' + m.value" @change="controller.setMode(m.value)">
        {{ m.label }}
      </label>
    </fieldset>
    <div v-if="view.targets.length" class="mb-4">
      <label for="clinic-preferences-target" class="mb-1 block text-sm font-medium text-[var(--text-secondary)]">{{ view.labels.target }}</label>
      <select id="clinic-preferences-target" class="field field--sm" :disabled="busy" data-testid="clinic-preferences-target"
              :style="view.recipientChoiceGrant ? { minHeight: 'var(--sp-7)', borderColor: 'var(--ui-text-muted)' } : null"
              v-model="selectedTarget">
        <option value="" disabled>Bitte wählen</option>
        <option v-for="x in view.targets" :key="x.value" :value="x.value">{{ x.name }}</option>
      </select>
    </div>
    <p v-if="view.targetMessage" role="status" class="mb-4 text-sm">{{ view.targetMessage }}</p>
    <div v-if="view.recipientChoiceGrant" class="mb-4">
      <h3 class="text-sm font-semibold">Empfängerwahl beim Versand</h3>
      <p data-testid="clinic-preferences-recipient-limit">{{ view.recipientChoiceGrant }} (von der Verwaltung festgelegt)</p>
    </div>
    <fieldset v-else class="mb-4 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" :aria-label="view.a11y.choiceGroup.label" role="radiogroup">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.a11y.choiceGroup.label }}</legend>
      <label v-for="c in view.choices" :key="c.value" class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">
        <input type="radio" name="clinic-preferences-choice" :value="c.value" :checked="c.selected" :disabled="c.disabled || busy"
               :data-testid="'clinic-preferences-choice-' + c.value" @change="controller.setChoice(c.value)">
        {{ c.label }}
      </label>
    </fieldset>
    <fieldset v-if="view.settings.length" class="mb-4 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.labels.workstation }}</legend>
      <div v-for="s in view.settings" :key="s.key" class="py-1" :data-testid="'clinic-preferences-setting-' + s.key">
        <template v-if="s.locked">
          <span>{{ s.key }}: </span>
          <output :data-testid="'clinic-preferences-locked-' + s.key">{{ String(s.value) }}</output>
          <span class="text-sm"> ({{ s.lockedLabel }})</span>
        </template>
        <label v-else-if="typeof s.value === 'boolean'" class="inline-flex items-center gap-2 text-sm text-[var(--text-primary)]">
          <input type="checkbox" :checked="s.value" :disabled="busy" @change="controller.setWorkstationValue(s.key, $event.target.checked)">
          {{ s.key }}
        </label>
        <label v-else class="grid gap-1 text-sm font-medium text-[var(--text-secondary)]">{{ s.key }}
          <input type="text" class="field field--sm" :value="s.value" :disabled="busy" @input="controller.setWorkstationValue(s.key, $event.target.value)">
        </label>
      </div>
    </fieldset>
    <div class="flex flex-wrap gap-2">
      <button v-if="!view.defaultOnly || view.settings.length" type="submit" class="btn btn--sm btn--primary" :style="buttonStyle" :disabled="!view.dirty || busy" data-testid="clinic-preferences-save">{{ view.labels.save }}</button>
      <button v-if="!view.defaultOnly || view.settings.length" type="button" class="btn btn--sm btn--ghost" :style="buttonStyle" :disabled="!view.dirty || busy" data-testid="clinic-preferences-cancel"
              @click="controller.cancel()">{{ view.labels.cancel }}</button>
      <button type="button" class="btn btn--sm btn--ghost" :style="buttonStyle" :disabled="busy" data-testid="clinic-preferences-reload"
              @click="controller.load()">{{ view.labels.reload }}</button>
      <button v-if="view.error && view.error.retryable" type="button" class="btn btn--sm btn--ghost" :style="buttonStyle" data-testid="clinic-preferences-retry"
              @click="onSave">{{ view.labels.retry }}</button>
    </div>
  </form>
</section>`;

function sameDefault(draft, state) {
    return draft.mode === state.mode && draft.choice === state.choice && sameList(draft.targetIds, state.targetIds);
}

/**
 * Vue options component for the admin pages. `props` carries the optional `workstationId` and `t`; the shared api
 * client is the only authority: a refused or stale save keeps the draft and shows the server message.
 */
export function createPreferencesComponent(Vue, client, props = {}) {
    return {
        name: 'ClinicPreferences',
        template: PANEL_TEMPLATE,
        setup() {
            const { controller, view, unsubscribe } = usePreferences(
                { client, workstationId: props.workstationId || null, t: props.t },
                Vue,
            );
            const selectedTarget = Vue.computed({
                get: () => view.value.targets.find((target) => target.selected)?.value || '',
                set: (id) => controller.setTarget(id),
            });
            const buttonStyle = Vue.computed(() => view.value.recipientChoiceGrant ? 'min-height: var(--sp-7) !important' : '');
            const loading = Vue.ref(true);
            const busy = Vue.ref(false);
            const syncFlags = () => { loading.value = controller.state.loading; busy.value = controller.state.busy; };
            controller.subscribe(syncFlags);
            const errorStyle = Vue.computed(() => {
                const s = view.value.errorSurface;
                return s ? { background: s.background, color: s.color, borderColor: s.border } : {};
            });
            const success = surfaceFor('success');
            const noticeStyle = { background: success.background, color: success.color, borderColor: success.border };

            // Default first: a successful save reloads and drops workstation edits, so those are re-applied.
            async function onSave() {
                const st = controller.state;
                const changes = Object.assign({}, st.draft.changes);
                if (!sameDefault(st.draft, st)) {
                    if (!(await controller.saveDefault())) return false;
                    for (const key of Object.keys(changes)) controller.setWorkstationValue(key, changes[key]);
                }
                if (Object.keys(changes).length) return controller.saveWorkstation();
                return true;
            }
            function onKeydown(event) {
                if (event && event.key === 'Escape') controller.cancel();
            }
            Vue.onMounted(async () => { await controller.load(); syncFlags(); });
            Vue.onBeforeUnmount(() => { if (unsubscribe) unsubscribe(); });
            return { controller, view, selectedTarget, buttonStyle, loading, busy, errorStyle, noticeStyle, onSave, onKeydown };
        },
    };
}
