/**
 * Editor for pronunciation hints, voice commands and macros (PAR-VOC-007, PAR-VOC-008).
 *
 * Exports pure helpers, a framework-light controller and a Vue composable /
 * component factory. Vue is injected (no bundler import), so the module also
 * runs in the browser through the global Vue build. Clinical text stays in
 * memory only: nothing is written to web storage. Colours come from the
 * token contract (tokens.js) and requests from the clinic api client.
 */

import { surfaceFor } from './tokens.js';

export const MAX_DEPTH = 8;
export const MAX_STEPS = 64;
export const MAX_TEXT_CHARS = 8192;
export const CONFIRMATION_TTL_MS = 60000;

// Mirrors APP_RIGHTS in src/clinic/voice_command_runtime.py.
export const APP_ACTIONS = Object.freeze({
    focus_editor: 'edit',
    open_editor: 'edit',
    insert_marker: 'edit',
    next_marker: 'view',
    previous_marker: 'view',
    send_dictation: 'export',
    release_dictation: 'release',
});
const SENSITIVE_RIGHTS = new Set(['export', 'release']);

export const LABELS = Object.freeze({
    title: 'Sprachbefehle und Aussprache',
    phrase: 'Auslösephrase',
    written: 'Schreibweise',
    phonetic: 'Lautschrift',
    steps: 'Aktionen (eine pro Zeile)',
    preview: 'Makro-Vorschau',
    save: 'Speichern',
    confirm: 'Aktion bestätigen',
    trainingOff: 'Aussprachetraining nicht verfügbar. Befehle lassen sich trotzdem bearbeiten.',
    sensitive: 'Diese Aktion ist sensibel und erfordert eine frische Bestätigung.',
    confirmationNeeded: 'Bestätigung erforderlich.',
    saved: 'Gespeichert.',
});

const ACTION_LABELS = Object.freeze({
    focus_editor: 'Editor fokussieren', open_editor: 'Editor öffnen', insert_marker: 'Markierung einfügen',
    next_marker: 'Nächste Markierung', previous_marker: 'Vorherige Markierung',
    send_dictation: 'Diktat senden', release_dictation: 'Diktat freigeben',
});
const ERROR_LABELS = Object.freeze({
    macro_too_deep: 'Das Makro ist zu tief verschachtelt.',
    macro_cycle: 'Das Makro verweist auf sich selbst.',
    invalid_step: 'Ein Makroschritt ist ungültig.',
    unknown_action: 'Eine Aktion ist unbekannt.',
    macro_too_long: 'Das Makro ist zu lang.',
    written_form_required: 'Bitte eine Schreibweise eingeben.',
    phonetic_hint_required: 'Bitte einen Aussprachehinweis eingeben.',
    training_unsupported: LABELS.trainingOff,
    phrase_required: 'Bitte eine Auslösephrase eingeben.',
    kind_invalid: 'Die Befehlsart ist ungültig.',
    actions_required: 'Bitte mindestens eine Aktion eingeben.',
    command_single_action: 'Ein Einzelbefehl darf nur eine Aktion enthalten.',
    confirmation_required: LABELS.confirmationNeeded,
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    network: 'Verbindung fehlgeschlagen. Bitte erneut versuchen.',
    UNKNOWN: 'Das Ergebnis ist unklar. Bitte den aktuellen Stand prüfen.',
});

export function commandErrorLabel(code) {
    return ERROR_LABELS[code] || 'Der Befehl konnte nicht geprüft werden.';
}

export function commandStepLabel(step) {
    return step.startsWith('text:') ? 'Text einfügen: ' + step.slice(5) : (ACTION_LABELS[step] || 'Unbekannte Aktion');
}

export function isSensitiveAction(actionId) {
    return SENSITIVE_RIGHTS.has(APP_ACTIONS[actionId]);
}

/** Expand macro steps with a bound on depth, step count and text length. */
export function previewMacro(steps, macros) {
    const defs = macros || {};
    const out = [];
    let chars = 0;
    function walk(list, depth, trail) {
        if (depth > MAX_DEPTH) return 'macro_too_deep';
        for (const step of list) {
            if (typeof step !== 'string' || !step.trim()) return 'invalid_step';
            const name = step.trim();
            if (Object.prototype.hasOwnProperty.call(defs, name)) {
                if (trail.includes(name)) return 'macro_cycle';
                const err = walk(defs[name], depth + 1, trail.concat(name));
                if (err) return err;
                continue;
            }
            if (!Object.prototype.hasOwnProperty.call(APP_ACTIONS, name) && !name.startsWith('text:')) {
                return 'unknown_action';
            }
            chars += name.length;
            if (out.length + 1 > MAX_STEPS) return 'macro_too_long';
            if (chars > MAX_TEXT_CHARS) return 'macro_too_long';
            out.push(name);
        }
        return null;
    }
    const error = walk(Array.isArray(steps) ? steps : [], 1, []);
    return error ? { ok: false, error: error, steps: [] } : { ok: true, error: null, steps: out };
}

/** Validate a definition against provider capabilities. Returns a list of error codes. */
export function validateDefinition(def, capabilities, macros) {
    const errors = [];
    const d = def || {};
    if (d.kind === 'pronunciation') {
        if (!String(d.written_form || '').trim()) errors.push('written_form_required');
        if (!String(d.phonetic_hint || '').trim()) errors.push('phonetic_hint_required');
        if (!(capabilities && capabilities.training_proven === true) && d.train === true) {
            errors.push('training_unsupported');
        }
        return errors;
    }
    if (!String(d.phrase || '').trim()) errors.push('phrase_required');
    const steps = Array.isArray(d.action_ids) ? d.action_ids : [];
    if (d.kind !== 'command' && d.kind !== 'macro') errors.push('kind_invalid');
    else if (!steps.length) errors.push('actions_required');
    else if (d.kind === 'command' && steps.length !== 1) errors.push('command_single_action');
    else {
        const pv = previewMacro(steps, macros);
        if (!pv.ok) errors.push(pv.error);
    }
    return errors;
}

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12).padEnd(8, '0');
}

/**
 * @param {{client: object, now?: () => number, makeState?: (o: object) => object}} deps
 */
export function createVoiceCommandsController(deps) {
    const client = deps.client;
    const now = deps.now || Date.now;
    const state = (deps.makeState || ((o) => o))({
        commands: [],
        pronunciations: [],
        capabilities: { training_proven: false },
        etags: {},
        status: 'idle',
        message: '',
        errors: [],
        confirmation: null,
    });

    function fail(res) {
        state.status = 'error';
        state.message = res.message;
        state.errors = [res.outcome === 'conflict' ? 'stale_version' : res.outcome];
        return res;
    }

    async function load() {
        state.status = 'loading';
        const [cmds, prons, caps] = await Promise.all([
            client.request('/resources/commands'),
            client.request('/resources/pronunciations'),
            client.request('/pronunciations/capabilities'),
        ]);
        for (const res of [cmds, prons]) if (res.outcome !== 'ok') return fail(res);
        state.commands = (cmds.data && cmds.data.items) || [];
        state.pronunciations = (prons.data && prons.data.items) || [];
        // A capability failure leaves training disabled; command editing continues.
        state.capabilities = caps.outcome === 'ok' && caps.data
            ? { training_proven: caps.data.training_proven === true }
            : { training_proven: false };
        state.status = 'ready';
        state.message = state.capabilities.training_proven ? '' : LABELS.trainingOff;
        return { outcome: 'ok' };
    }

    function macroMap() {
        const map = {};
        for (const c of state.commands) {
            if (c.action_type === 'macro') {
                try { map[c.trigger_phrase] = JSON.parse(c.action_payload || '[]'); } catch (e) { /* skip */ }
            }
        }
        return map;
    }

    function requestConfirmation(def) {
        const sensitive = (def.action_ids || []).filter(isSensitiveAction);
        if (!sensitive.length) return null;
        state.confirmation = { phrase: def.phrase, actions: sensitive.slice().sort(), at: now(), used: false };
        return state.confirmation;
    }

    function confirmationValid(def) {
        const c = state.confirmation;
        if (!c || c.used || now() - c.at > CONFIRMATION_TTL_MS) return false;
        const sensitive = (def.action_ids || []).filter(isSensitiveAction).sort();
        return c.phrase === def.phrase && JSON.stringify(c.actions) === JSON.stringify(sensitive);
    }

    async function saveCommand(def, scope) {
        const errors = validateDefinition(def, state.capabilities, macroMap());
        if (errors.length) {
            state.errors = errors;
            state.status = 'invalid';
            return { outcome: 'validation', errors: errors };
        }
        if ((def.action_ids || []).some(isSensitiveAction) && !confirmationValid(def)) {
            state.errors = ['confirmation_required'];
            state.message = LABELS.confirmationNeeded;
            return { outcome: 'confirmation_required' };
        }
        const body = {
            command: 'save_command',
            request_id: newKey(),
            expected_revision: Number.isInteger(def.revision) ? def.revision : 0,
            payload: {
                scope_type: scope.type, scope_id: String(scope.id),
                phrase: def.phrase.trim(), action_ids: def.action_ids.map((s) => s.trim()),
            },
        };
        state.status = 'saving';
        // The key is reused on manual retry through def.idempotencyKey so replays stay exact.
        const res = await client.command(body, { idempotencyKey: def.idempotencyKey || newKey() });
        if (state.confirmation) state.confirmation.used = true;
        if (res.outcome !== 'ok') return fail(res);
        state.status = 'ready';
        state.errors = [];
        state.message = LABELS.saved;
        return res;
    }

    async function savePronunciation(def, scope) {
        const errors = validateDefinition(Object.assign({ kind: 'pronunciation' }, def), state.capabilities);
        if (errors.length) {
            state.errors = errors;
            state.status = 'invalid';
            return { outcome: 'validation', errors: errors };
        }
        state.status = 'saving';
        const res = await client.request('/pronunciations/commands', {
            method: 'POST',
            body: {
                command: 'save_pronunciation_hint', request_id: newKey(),
                payload: {
                    scope_type: scope.type, scope_id: String(scope.id),
                    written_form: def.written_form.trim(), phonetic_hint: def.phonetic_hint.trim(),
                    sounds_like: def.sounds_like || null,
                },
            },
        });
        if (res.outcome !== 'ok') return fail(res);
        state.status = 'ready';
        state.errors = [];
        state.message = LABELS.saved;
        return res;
    }

    // Dispatched only when the separately registered capability is proven.
    async function requestTraining(profileRevisionId) {
        if (!state.capabilities.training_proven) {
            state.errors = ['training_unsupported'];
            state.message = LABELS.trainingOff;
            return { outcome: 'UNKNOWN', code: 'CAPABILITY_UNAVAILABLE' };
        }
        const res = await client.command({
            command: 'request_adaptation', request_id: newKey(), expected_revision: 0,
            payload: { profile_revision_id: profileRevisionId, capability: 'pronunciation_training' },
        }, { idempotencyKey: newKey() });
        return res.outcome === 'ok' ? res : fail(res);
    }

    return {
        state: state, load: load, saveCommand: saveCommand, savePronunciation: savePronunciation,
        requestTraining: requestTraining, requestConfirmation: requestConfirmation,
        preview: (steps) => previewMacro(steps, macroMap()),
        surface: (kind) => surfaceFor(kind),
    };
}

/** Vue composable. `vue` is the Vue namespace (global Vue in the browser). */
export function useVoiceCommands(client, vue) {
    const V = vue || globalThis.Vue;
    return createVoiceCommandsController({ client: client, makeState: (o) => V.reactive(o) });
}

/** Component factory with labelled, keyboard-operable markup (Enter saves, Escape clears). */
export function createVoiceCommandsComponent(vue) {
    const V = vue || globalThis.Vue;
    return {
        name: 'ClinicVoiceCommandsUi',
        props: { client: { type: Object, required: true }, scope: { type: Object, required: true } },
        setup(props) {
            const c = useVoiceCommands(props.client, V);
            const form = V.reactive({ phrase: '', stepsText: '' });
            const pv = V.computed(() => c.preview(form.stepsText.split('\n').filter((s) => s.trim())));
            const needsConfirm = V.computed(() => form.stepsText.split('\n').some((s) => isSensitiveAction(s.trim())));
            const def = () => ({ kind: 'macro', phrase: form.phrase, action_ids: form.stepsText.split('\n').filter((s) => s.trim()) });
            const save = () => c.saveCommand(def(), props.scope);
            const confirm = () => c.requestConfirmation(def());
            const reset = () => { form.phrase = ''; form.stepsText = ''; };
            V.onMounted(() => c.load());
            return { c, form, pv, needsConfirm, save, confirm, reset, LABELS,
                stepLabel: commandStepLabel, errorLabel: commandErrorLabel };
        },
        template: `
<section class="v8-page stack" aria-labelledby="vc-title" @keydown.esc="reset" :aria-busy="c.state.status === 'loading' || c.state.status === 'saving'">
  <h2 id="vc-title">{{ LABELS.title }}</h2>
  <p v-if="c.state.status === 'loading'" class="note" role="status">Sprachbefehle werden geladen …</p>
  <p v-if="c.state.message" class="note" :class="{ burgundy: c.state.status === 'error' }" role="status" aria-live="polite">{{ c.state.message }}</p>
  <ul v-if="c.state.errors.length" class="note burgundy" role="alert">
    <li v-for="error in c.state.errors" :key="error">{{ errorLabel(error) }}</li>
  </ul>
  <form class="stack" @submit.prevent="save">
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="vc-phrase">{{ LABELS.phrase }}</label>
      <input id="vc-phrase" v-model="form.phrase" type="text" autocomplete="off" required>
    </div>
    <div class="field" style="border:0;padding:0;box-shadow:none;background:none">
      <label for="vc-steps">{{ LABELS.steps }}</label>
      <textarea id="vc-steps" v-model="form.stepsText" rows="4" aria-describedby="vc-preview"></textarea>
    </div>
    <div id="vc-preview" class="note" aria-live="polite">
      <strong>{{ LABELS.preview }}: </strong>
      <span v-if="!form.stepsText.trim()"> Noch keine Aktionen.</span>
      <span v-else-if="pv.ok"> {{ pv.steps.map(stepLabel).join(', ') }}</span>
      <span v-else role="alert"> {{ errorLabel(pv.error) }}</span>
    </div>
    <div v-if="needsConfirm" class="note warning">
      <p>{{ LABELS.sensitive }}</p>
      <button type="button" class="small" @click="confirm">{{ LABELS.confirm }}</button>
    </div>
    <div class="actions">
      <button type="submit" class="primary" :disabled="c.state.status === 'saving'">{{ LABELS.save }}</button>
    </div>
  </form>
</section>`,
    };
}
