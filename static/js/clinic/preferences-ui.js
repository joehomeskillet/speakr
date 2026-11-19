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
    'clinic.preferences.mode.staff_pool': 'Personalpool',
    'clinic.preferences.mode.self_correction': 'Selbstkorrektur',
    'clinic.preferences.target': 'Empfänger auswählen',
    'clinic.preferences.choice': 'Abweichende Auswahl beim Diktat',
    'clinic.preferences.choice.default_only': 'Nur der Standard',
    'clinic.preferences.choice.staff_pools_only': 'Nur Personalpools',
    'clinic.preferences.choice.open': 'Frei wählbar',
    'clinic.preferences.workstation': 'Arbeitsplatz-Einstellungen',
    'clinic.preferences.locked': 'Zentral festgelegt',
    'clinic.preferences.save': 'Übernehmen',
    'clinic.preferences.cancel': 'Abbrechen',
    'clinic.preferences.retry': 'Erneut versuchen',
    'clinic.preferences.reload': 'Neu laden',
    'clinic.preferences.removed': 'Die gewählte Auswahl ist nicht mehr verfügbar.',
    'clinic.preferences.saved': 'Gespeichert.',
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
        workstation: { values: {}, origins: {}, version: 0 },
        draft: { mode: 'all_eligible', targetIds: [], choice: 'open', changes: {} },
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function fail(res, fallbackCode) {
        const code = res.code || fallbackCode || null;
        const message = SERVER_MESSAGES[code] || res.message;
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
        if (isLocked(key)) return false;
        state.draft.changes[key] = value;
        emit();
        return true;
    }

    async function saveDefault() {
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
export function usePreferences(deps) {
    const controller = createPreferencesController(deps);
    const Vue = typeof window !== 'undefined' ? window.Vue : null;
    if (!Vue) return { controller, view: null };
    const view = Vue.shallowRef(controller.view());
    controller.subscribe(() => { view.value = controller.view(); });
    return { controller, view };
}
