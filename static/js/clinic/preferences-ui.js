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

export const WORKSTATION_FIELDS = {
    theme_dark_mode: { label: 'Dunkle Darstellung', default: false },
    ui_language: { label: 'Sprache der Oberfläche', default: 'de', options: [
        ['de', 'Deutsch'], ['en', 'Englisch'], ['es', 'Spanisch'], ['fr', 'Französisch'],
        ['pt-BR', 'Portugiesisch (Brasilien)'], ['ru', 'Russisch'], ['zh', 'Chinesisch'],
    ] },
    ui_theme: { label: 'Ansicht', default: 'default', options: [
        ['default', 'Standard'], ['classic', 'Klassisch'], ['workspace', 'Arbeitsbereich'],
        ['focus', 'Fokus'], ['desk', 'Schreibtisch'], ['cards', 'Karten'],
    ] },
    theme_color_scheme: { label: 'Akzentfarbe', default: 'blue', options: [
        ['blue', 'Blau'], ['emerald', 'Grün'], ['purple', 'Violett'], ['rose', 'Rosé'], ['amber', 'Bernstein'], ['teal', 'Türkis'],
    ] },
    audio_player_position: { label: 'Position der Wiedergabe', default: 'bottom', options: [['bottom', 'Unten'], ['top', 'Oben']] },
    keyboard_shortcuts: { label: 'Tastenkürzel', default: true },
    show_timestamps_simple_view: { label: 'Zeitangaben im Text anzeigen', default: false },
    editor_autosave: { label: 'Text automatisch speichern', default: false },
};

export const WORKSTATION_ORIGINS = Object.freeze({
    workstation: 'Persönlich (Arbeitsplatz)', central: 'Zentral festgelegt', ceiling: 'Zentral begrenzt',
});

const LABELS = Object.freeze({
    'clinic.preferences.title': 'Empfänger und Arbeitsplatz',
    'clinic.preferences.mode': 'Standardempfänger',
    'clinic.preferences.mode.all_eligible': 'Alle berechtigten Personen',
    'clinic.preferences.mode.person': 'Bestimmte Person',
    'clinic.preferences.mode.staff_pool': 'Personalpool',
    'clinic.preferences.mode.self_correction': 'Selbstkorrektur',
    'clinic.preferences.target': 'Empfänger auswählen',
    'clinic.preferences.choice': 'Abweichende Auswahl beim Diktat',
    'clinic.preferences.choice.default_only': 'Nur der Standard',
    'clinic.preferences.choice.staff_pools_only': 'Nur Personalpools',
    'clinic.preferences.choice.open': 'Frei wählbar',
    'clinic.preferences.workstation': 'Arbeitsplatz-Einstellungen',
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
    let inherited = {};
    let generation = 0;
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
        workstation: { values: {}, origins: {}, version: 0 },
        draft: { mode: 'all_eligible', targetIds: [], choice: 'open', changes: {} },
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function fail(res, fallbackCode) {
        const code = res.code || fallbackCode || null;
        const message = SERVER_MESSAGES[String(code).toLowerCase()] || res.message || 'Die Einstellungen konnten nicht geladen oder gespeichert werden.';
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
        if (draft.mode === 'person' || draft.mode === 'staff_pool') {
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

    function optionIds(mode) {
        const o = state.overrides;
        if (!o || typeof o !== 'object') return [];
        const list = mode === 'person' ? (o.person || o.persons) : (o.staff_pool || o.staff_pools);
        if (!Array.isArray(list)) return [];
        return list.map((x) => String(x && typeof x === 'object' ? x.id : x));
    }

    function isLocked(key) {
        return LOCKED_ORIGINS.includes(state.workstation.origins[key]);
    }

    async function load() {
        const seen = generation;
        state.loading = true;
        state.error = null;
        state.notice = null;
        emit();
        const def = await client.request(BASE + '/recipient-default');
        if (seen !== generation || def.outcome === 'cancelled') return false;
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
        state.draft = { mode: state.mode, targetIds: state.targetIds.slice(), choice: state.choice, changes: {} };
        if (!workstationId && deps.issueWorkstation) {
            const issued = await client.request(BASE + '/workstations', { method: 'POST', body: { reuse: true } });
            if (seen !== generation || issued.outcome === 'cancelled') return false;
            if (issued.outcome !== 'ok' || !issued.data?.workstation_id) {
                state.loading = false;
                fail(issued);
                emit();
                return false;
            }
            workstationId = issued.data.workstation_id;
            inherited = issued.data.defaults || {};
        }
        if (workstationId) {
            const ws = await client.request(BASE + '/workstations/' + encodeURIComponent(workstationId));
            if (seen !== generation || ws.outcome === 'cancelled') return false;
            if (ws.outcome !== 'ok') {
                state.loading = false;
                fail(ws);
                emit();
                return false;
            }
            const w = ws.data || {};
            state.workstation = { values: { ...inherited, ...w.values }, origins: w.origins || {}, version: w.version || 0 };
        }
        revalidate();
        state.loading = false;
        emit();
        return true;
    }

    function setMode(mode) {
        if (!MODES.includes(mode)) return false;
        state.draft.mode = mode;
        if (mode !== 'person' && mode !== 'staff_pool') state.draft.targetIds = [];
        if (mode === 'self_correction') state.draft.choice = 'open';
        emit();
        return true;
    }

    function setTarget(id) {
        if (!optionIds(state.draft.mode).includes(String(id))) return false;
        state.draft.targetIds = [String(id)];
        emit();
        return true;
    }

    function setChoice(choice) {
        if (!CHOICES.includes(choice)) return false;
        state.draft.choice = choice;
        emit();
        return true;
    }

    function setWorkstationValue(key, value) {
        if (!Object.hasOwn(state.workstation.values, key) || isLocked(key)) return false;
        if (Object.is(value, state.workstation.values[key])) delete state.draft.changes[key];
        else state.draft.changes[key] = value;
        emit();
        return true;
    }

    async function saveDefault() {
        const seen = generation;
        const d = state.draft;
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
            body: { mode: d.mode, target_ids: d.targetIds, choice: d.choice, expected_version: state.version },
        });
        if (seen !== generation || res.outcome === 'cancelled') return false;
        state.busy = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const loaded = await load();
        if (loaded) { state.notice = label("clinic.preferences.saved"); emit(); }
        return loaded;
    }

    async function saveWorkstation() {
        if (!workstationId || !Object.keys(state.draft.changes).length) return true;
        const seen = generation;
        state.busy = true;
        state.error = null;
        emit();
        const res = await client.request(BASE + '/workstations/' + encodeURIComponent(workstationId), {
            method: 'PUT',
            body: { changes: state.draft.changes, expected_version: state.workstation.version },
        });
        if (seen !== generation || res.outcome === 'cancelled') return false;
        state.busy = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const loaded = await load();
        if (loaded) { state.notice = label("clinic.preferences.saved"); emit(); }
        return loaded;
    }

    function cancel() {
        if (state.loading || state.busy || !view().dirty) return;
        state.draft = { mode: state.mode, targetIds: state.targetIds.slice(), choice: state.choice, changes: {} };
        state.error = null;
        state.notice = 'Ungespeicherte Änderungen verworfen.';
        emit();
    }

    // Account or workstation change: abort in-flight calls, drop all state, reload.
    async function switchContext(next) {
        const n = next || {};
        generation += 1;
        state.busy = false;
        inherited = {};
        workstationId = n.workstationId !== undefined ? n.workstationId : (deps.issueWorkstation ? null : workstationId);
        client.switchContext({ accountId: n.accountId, dictationId: n.dictationId });
        state.workstation = { values: {}, origins: {}, version: 0 };
        state.overrides = null;
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
        const dirty = d.mode !== state.mode || d.choice !== state.choice
            || !sameList(d.targetIds, state.targetIds) || Object.keys(d.changes).length > 0;
        return {
            title: label('clinic.preferences.title'),
            dirty,
            modes: MODES.map((m) => ({
                value: m,
                label: label('clinic.preferences.mode.' + m),
                selected: d.mode === m,
                disabled: m === 'self_correction' && !!state.overrides && state.overrides.self_correction === false,
            })),
            targets: (d.mode === 'person' || d.mode === 'staff_pool')
                ? optionIds(d.mode).map((id) => ({ value: id, selected: d.targetIds.includes(id) })) : [],
            choices: CHOICES.map((c) => ({
                value: c, label: label('clinic.preferences.choice.' + c), selected: d.choice === c,
                disabled: d.mode === 'self_correction' && c !== 'open',
            })),
            settings: Object.keys(state.workstation.values).map((key) => ({
                key,
                label: WORKSTATION_FIELDS[key]?.label || 'Arbeitsplatzwert',
                options: WORKSTATION_FIELDS[key]?.options,
                value: key in d.changes ? d.changes[key] : state.workstation.values[key],
                origin: Object.hasOwn(WORKSTATION_ORIGINS, state.workstation.origins[key])
                    ? WORKSTATION_ORIGINS[state.workstation.origins[key]] : 'Persönlich (Konto)',
                locked: isLocked(key),
                lockedLabel: isLocked(key) ? WORKSTATION_ORIGINS[state.workstation.origins[key]] : '',
            })),
            noOverrides: !Object.keys(state.workstation.origins).length,
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
  <p class="mb-4 mt-1 text-sm text-[var(--text-muted)]">Standardempfänger und abweichende Auswahl für Diktate festlegen.</p>
  <p v-if="view.error" role="alert" aria-live="assertive" class="mb-3 p-2 border rounded" :style="errorStyle"
     data-testid="clinic-preferences-error">{{ view.error.message }}</p>
  <p v-if="view.notice" role="status" aria-live="polite" class="mb-3 p-2 border rounded" :style="noticeStyle"
     data-testid="clinic-preferences-notice">{{ view.notice }}</p>
  <p v-if="loading" role="status" aria-live="polite">{{ view.labels.loading }}</p>
  <form v-else @submit.prevent="onSave" :aria-busy="busy ? 'true' : 'false'" novalidate>
    <fieldset class="mb-4 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" :aria-label="view.a11y.modeGroup.label" role="radiogroup">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.a11y.modeGroup.label }}</legend>
      <label v-for="m in view.modes" :key="m.value" class="flex cursor-pointer items-center gap-2 min-h-12 rounded-md px-2 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">
        <input type="radio" name="clinic-preferences-mode" :value="m.value" :checked="m.selected" :disabled="m.disabled || busy"
               :data-testid="'clinic-preferences-mode-' + m.value" @change="controller.setMode(m.value)">
        {{ m.label }}
      </label>
    </fieldset>
    <div v-if="view.targets.length" class="mb-4">
      <label for="clinic-preferences-target" class="mb-1 block text-sm font-medium text-[var(--text-secondary)]">{{ view.labels.target }}</label>
      <select id="clinic-preferences-target" class="field min-h-12 !border-ui-text-muted" :disabled="busy" data-testid="clinic-preferences-target"
              @change="controller.setTarget($event.target.value)">
        <option value="" :selected="!view.targets.some((x) => x.selected)">–</option>
        <option v-for="x in view.targets" :key="x.value" :value="x.value" :selected="x.selected">{{ x.value }}</option>
      </select>
    </div>
    <fieldset class="mb-4 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" :aria-label="view.a11y.choiceGroup.label" role="radiogroup">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.a11y.choiceGroup.label }}</legend>
      <label v-for="c in view.choices" :key="c.value" class="flex cursor-pointer items-center gap-2 min-h-12 rounded-md px-2 py-1.5 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">
        <input type="radio" name="clinic-preferences-choice" :value="c.value" :checked="c.selected" :disabled="c.disabled || busy"
               :data-testid="'clinic-preferences-choice-' + c.value" @change="controller.setChoice(c.value)">
        {{ c.label }}
      </label>
    </fieldset>
    <fieldset v-if="view.settings.length" class="mb-4 min-w-0 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
      <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ view.labels.workstation }}</legend>
      <p v-if="view.noOverrides" class="hint mb-2">Keine eigenen Arbeitsplatzwerte. Es gelten Ihre Kontoeinstellungen.</p>
      <div v-for="s in view.settings" :key="s.key" class="py-1" :data-testid="'clinic-preferences-setting-' + s.key">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <label :for="'clinic-workstation-' + s.key">{{ s.label }}</label>
          <span class="tag neutral" :id="'clinic-origin-' + s.key">{{ s.origin }}</span>
        </div>
        <template v-if="s.locked">
          <output :id="'clinic-workstation-' + s.key" :data-testid="'clinic-preferences-locked-' + s.key">
            {{ s.options?.find(([v]) => v === s.value)?.[1] ?? (typeof s.value === 'boolean' ? (s.value ? 'Ein' : 'Aus') : s.value) }}
          </output>
        </template>
        <select v-else-if="s.options || typeof s.value === 'boolean'" :id="'clinic-workstation-' + s.key" :aria-describedby="'clinic-origin-' + s.key" class="field min-h-12 !border-ui-text-muted"
                :value="s.value" :disabled="busy" @change="controller.setWorkstationValue(s.key, typeof s.value === 'boolean' ? $event.target.value === 'true' : $event.target.value)">
          <option v-if="s.options && !s.options.some(([v]) => v === s.value)" :value="s.value">{{ s.value }}</option>
          <option v-for="[value, label] in s.options || [[true, 'Ein'], [false, 'Aus']]" :key="value" :value="value">{{ label }}</option>
        </select>
        <input v-else :id="'clinic-workstation-' + s.key" :aria-describedby="'clinic-origin-' + s.key" type="text" class="field min-h-12 !border-ui-text-muted" :value="s.value" :disabled="busy" @input="controller.setWorkstationValue(s.key, $event.target.value)">
      </div>
    </fieldset>
    <div class="flex flex-wrap gap-2">
      <button type="submit" class="btn btn--primary min-h-12" :disabled="!view.dirty || busy" data-testid="clinic-preferences-save">{{ view.labels.save }}</button>
      <button type="button" class="btn btn--ghost min-h-12" :disabled="!view.dirty || busy" data-testid="clinic-preferences-cancel"
              @click="controller.cancel()">{{ view.labels.cancel }}</button>
      <button type="button" class="btn btn--ghost min-h-12" :disabled="busy || view.dirty && !view.error" data-testid="clinic-preferences-reload"
              @click="onReload">{{ view.labels.reload }}</button>
      <button v-if="view.error && view.error.retryable" type="button" class="btn btn--ghost min-h-12" :disabled="busy" data-testid="clinic-preferences-retry"
              @click="view.dirty ? onSave() : controller.load()">{{ view.labels.retry }}</button>
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
                { client, workstationId: props.workstationId || null, issueWorkstation: props.issueWorkstation, t: props.t },
                Vue,
            );
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
            function onReload() {
                if (!view.value.dirty || window.confirm('Neu laden und ungespeicherte Änderungen verwerfen?')) return controller.load();
            }
            function onKeydown(event) {
                if (event && event.key === 'Escape') controller.cancel();
            }
            Vue.onMounted(async () => { await controller.load(); syncFlags(); });
            Vue.onBeforeUnmount(() => { if (unsubscribe) unsubscribe(); });
            return { controller, view, loading, busy, errorStyle, noticeStyle, onSave, onReload, onKeydown };
        },
    };
}

const PRESET_FIELDS = {
    language: { label: 'Diktatsprache' },
    priority: { label: 'Priorität', options: [[0, 'Niedrig'], [1, 'Normal'], [128, 'Dringend']] },
    security: { label: 'Vertraulichkeit', options: [['normal', 'Normal'], ['confidential', 'Vertraulich'], ['restricted', 'Streng vertraulich']] },
    use_speech_recognition: { label: 'Spracherkennung verwenden' },
    with_approval: { label: 'Abnahme verlangen' },
    pool: { label: 'Schreibgruppe' },
    typist: { label: 'Schreibkraft' },
};
const ON_OFF_OPTIONS = [[true, 'Ein'], [false, 'Aus']];
const PRESET_VALUE_LABELS = new Map([
    ['<Automatisch>', 'Automatisch'], ['<Voreingestellt>', 'Voreingestellt'], ['all_eligible', 'Alle berechtigten Personen'],
]);

const CENTRAL_TEMPLATE = `
<section v-if="!denied" class="panel mb-4" aria-labelledby="clinic-central-title" data-testid="clinic-central-preferences">
  <h2 id="clinic-central-title" class="t-subhead">Zentrale Vorgaben</h2>
  <p class="hint mt-1 mb-4">Zentral festgelegte Arbeitsplatzwerte gelten für alle und lassen sich persönlich nicht ändern. Vorgaben für Diktate können Sie auch ohne Sperre speichern.</p>
  <p v-if="error" role="alert" class="banner banner--danger mb-3">{{ error }}</p>
  <p v-if="notice" role="status" class="banner banner--success mb-3">{{ notice }}</p>
  <p v-if="!data && busy" role="status">Zentrale Vorgaben werden geladen …</p>
  <button v-else-if="!data" type="button" class="btn btn--ghost min-h-12" @click="load">Erneut laden</button>
  <form v-else @submit.prevent="save" :aria-busy="busy">
    <fieldset :disabled="busy" class="min-w-0 max-w-lg space-y-3">
      <div>
        <label for="clinic-central-field" class="block text-sm font-medium mb-1">Einstellung</label>
        <select id="clinic-central-field" class="field min-h-12 !border-ui-text-muted" :value="key" @change="select($event.target.value)">
          <optgroup label="Arbeitsplatz">
            <option v-for="f in workstationFields" :key="f.key" :value="f.key">{{ f.label }}</option>
          </optgroup>
          <optgroup v-if="presetFields.length" label="Diktatablauf">
            <option v-for="f in presetFields" :key="f.key" :value="f.key">{{ f.label }}</option>
          </optgroup>
        </select>
      </div>
      <div v-if="field" :key="key" class="space-y-3">
        <p class="hint" role="status">{{ currentStatus }}</p>
        <div>
          <label for="clinic-central-value" class="block text-sm font-medium mb-1">Neuer Wert</label>
          <select v-if="options" id="clinic-central-value" class="field min-h-12 !border-ui-text-muted" v-model="draftValue">
            <option v-for="[value, label] in options" :key="value" :value="value">{{ label }}</option>
          </select>
          <input v-else id="clinic-central-value" class="field min-h-12 !border-ui-text-muted" type="text" maxlength="128"
                 :required="key !== 'typist'" v-model.trim="draftValue">
        </div>
        <label v-if="isPreset" class="flex items-center gap-2 min-h-12 text-sm cursor-pointer">
          <input type="checkbox" v-model="draftLocked"> Änderungen durch Benutzende sperren
        </label>
        <div class="actions">
          <button type="submit" class="btn btn--primary min-h-12 !bg-brand-primary !text-[color:var(--v8-on-ink,var(--ui-surface))] hover:!bg-brand-accent dark:hover:!bg-brand-primary" :disabled="busy">{{ isPreset ? 'Vorgabe speichern' : 'Wert festlegen und sperren' }}</button>
          <button v-if="hasOverride" type="button" class="btn btn--ghost min-h-12" :disabled="busy" @click="remove">
            {{ isPreset ? 'Vorgabe entfernen' : 'Sperre aufheben' }}
          </button>
        </div>
      </div>
    </fieldset>
  </form>
</section>`;

export function createCentralPreferencesComponent(Vue, client) {
    return {
        name: 'ClinicCentralPreferences',
        template: CENTRAL_TEMPLATE,
        setup() {
            const data = Vue.ref(null);
            const key = Vue.ref('');
            const draftValue = Vue.ref(null);
            const draftLocked = Vue.ref(false);
            const denied = Vue.ref(false);
            const busy = Vue.ref(false);
            const error = Vue.ref('');
            const notice = Vue.ref('');
            const workstationFields = Object.entries(WORKSTATION_FIELDS).map(([key, field]) => ({ key, ...field }));
            const presetFields = Vue.computed(() => Object.entries(PRESET_FIELDS)
                .filter(([id]) => Object.hasOwn(data.value?.preset_defaults ?? {}, id))
                .map(([id, field]) => ({ key: id, ...field, default: data.value.preset_defaults[id] })));
            const preset = Vue.computed(() => presetFields.value.find((f) => f.key === key.value));
            const isPreset = Vue.computed(() => Boolean(preset.value));
            const field = Vue.computed(() => preset.value ?? workstationFields.find((f) => f.key === key.value));
            const values = Vue.computed(() => isPreset.value ? data.value?.presets : data.value?.workstation_locks);
            const hasOverride = Vue.computed(() => key.value in (values.value ?? {}));
            const current = Vue.computed(() => hasOverride.value
                ? (isPreset.value ? values.value[key.value].value : values.value[key.value])
                : field.value?.default);
            const locked = Vue.computed(() => hasOverride.value && (!isPreset.value || values.value[key.value].is_locked));
            const options = Vue.computed(() => field.value?.options ?? (typeof field.value?.default === 'boolean' ? ON_OFF_OPTIONS : null));
            const currentStatus = Vue.computed(() => {
                if (!field.value) return '';
                if (!isPreset.value && !hasOverride.value) return 'Nicht gesperrt · Persönliche Einstellungen gelten.';
                const value = options.value?.find(([v]) => v === current.value)?.[1]
                    ?? PRESET_VALUE_LABELS.get(current.value) ?? current.value ?? 'Keine Schreibkraft';
                if (!hasOverride.value) return `Standardwert (keine zentrale Vorgabe): ${value}`;
                return locked.value ? `Zentral gesperrt: ${value}` : `Zentrale Vorgabe (änderbar): ${value}`;
            });

            function select(id) {
                key.value = id;
                draftValue.value = current.value;
                draftLocked.value = locked.value;
                error.value = '';
                notice.value = '';
            }
            async function load() {
                if (busy.value) return;
                busy.value = true;
                error.value = '';
                try {
                    const result = await client.request(BASE + '/central');
                    denied.value = result.outcome === 'permission';
                    if (result.outcome === 'ok' && result.data) {
                        data.value = result.data;
                        select(workstationFields[0]?.key ?? presetFields.value[0]?.key ?? '');
                    } else {
                        error.value = result.message || 'Zentrale Vorgaben konnten nicht geladen werden.';
                    }
                } catch {
                    error.value = 'Zentrale Vorgaben konnten nicht geladen werden.';
                } finally {
                    busy.value = false;
                }
            }
            async function change(method) {
                if (!data.value || busy.value || !field.value) return false;
                const removing = method === 'DELETE';
                if (removing && (!hasOverride.value || !window.confirm(isPreset.value
                    ? `Zentrale Vorgabe für „${field.value.label}“ entfernen? Andere Vorgaben bleiben bestehen. Ohne andere Vorgabe gilt der Standardwert.`
                    : `Zentrale Sperre für „${field.value.label}“ aufheben? Persönliche Einstellungen gelten wieder, sofern keine andere Einschränkung greift.`))) return false;
                const id = key.value;
                const value = id === 'typist' && draftValue.value === '' ? null : draftValue.value;
                const body = isPreset.value ? { value, is_locked: draftLocked.value } : { value };
                const path = BASE + (isPreset.value ? '/presets/' : '/workstation-locks/') + id;
                busy.value = true;
                error.value = '';
                notice.value = '';
                try {
                    const result = await client.request(path, removing ? { method } : { method, body });
                    if (result.outcome !== 'ok') {
                        error.value = result.message || 'Die Änderung konnte nicht gespeichert werden.';
                        return false;
                    }
                    if (removing) delete values.value[id];
                    else values.value[id] = isPreset.value ? body : value;
                    select(id);
                    notice.value = removing ? 'Die zentrale Einstellung wurde entfernt.' : 'Die zentrale Einstellung wurde gespeichert.';
                    return true;
                } catch {
                    error.value = 'Die Änderung konnte nicht gespeichert werden.';
                    return false;
                } finally {
                    busy.value = false;
                }
            }
            Vue.onMounted(load);
            return { data, denied, busy, error, notice, key, field, options, workstationFields, presetFields,
                isPreset, hasOverride, currentStatus, draftValue, draftLocked, select, load,
                save: () => change('PUT'), remove: () => change('DELETE') };
        },
    };
}
