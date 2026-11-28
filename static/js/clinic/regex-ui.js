/**
 * Preview and explicit confirmation of bounded metadata assignment rules
 * (PAR-CFG-004, R-039).
 *
 * The model talks to /api/clinic/v1/regex/preview and /regex/apply through the
 * clinic API client. Apply always sends the rule set, text, rule version and
 * input digest of the preview that was shown, so the server can refuse a stale
 * preview. Any edit after a preview discards it and requires a new preview.
 * Rules, dictation text and results stay in memory only: nothing is written to
 * browser storage. Validation failures are shown from the server's safe codes;
 * the engine in src/clinic/presets.py stays the single source of the target and
 * pattern policy.
 */

import { surfaceFor } from './tokens.js';

const PREVIEW_PATH = '/regex/preview';
const APPLY_PATH = '/regex/apply';
const RULES_PATH = '/regex/rules';
const MAX_TEXT_LENGTH = 10000;
const TARGET_LABELS = Object.freeze({
    title: 'Titel', dictation_name: 'Diktatname', notes: 'Anmerkungen', remarks: 'Bemerkungen',
    priority: 'Dringlichkeit', urgency: 'Dringlichkeit (alternatives Feld)', deadline: 'Frist', due_date: 'Fälligkeit',
});
const EDIT_TARGETS = ['title', 'notes', 'priority', 'deadline'];

export const LABELS = Object.freeze({
    title: 'Regeln für Metadaten',
    rulesLegend: 'Zuweisungsregeln',
    rule: 'Regel',
    name: 'Name',
    pattern: 'Muster',
    target: 'Zielfeld',
    replacement: 'Ersetzung',
    addRule: 'Regel hinzufügen',
    removeRule: 'Regel entfernen',
    text: 'Eingabetext',
    preview: 'Vorschau',
    apply: 'Übernehmen',
    discard: 'Vorschau verwerfen',
    retry: 'Erneut versuchen',
    field: 'Feld',
    oldValue: 'Bisheriger Wert',
    newValue: 'Neuer Wert',
    noMatch: 'Keine Regel hat getroffen.',
    truncated: 'Der Text wurde für die Vorschau gekürzt.',
    ruleVersion: 'Regelversion',
    inputDigest: 'Eingabe-Prüfsumme',
    applyHint: 'Es wird nur übernommen, was die Vorschau zeigt.',
    previewing: 'Vorschau wird berechnet.',
    applying: 'Werte werden übernommen.',
    applied: 'Die Werte wurden übernommen.',
    invalidPattern: 'Ungültiges oder zu aufwendiges Muster.',
    // POLICY_DENIED covers both an actor denial and a forbidden target; the server does not separate them.
    forbiddenTarget: 'Keine Berechtigung oder Zielfeld nicht erlaubt (Kontext- und Identitätsfelder können nicht gesetzt werden).',
    stale: 'Regeln oder Eingabe haben sich geändert. Bitte die Vorschau neu berechnen.',
    denied: 'Keine Berechtigung für diese Aktion.',
    failed: 'Die Aktion ist fehlgeschlagen.',
    invalidRule: 'Die Regel wurde nicht gespeichert. Prüfen Sie das Suchmuster und den eingesetzten Text.',
    applyConflict: 'Die Werte wurden nicht übernommen. Öffnen Sie das Diktat zur Bearbeitung und berechnen Sie die Vorschau erneut.',
});

const FAILURE = Object.freeze({
    INVALID_PAYLOAD: 'invalidPattern',
    POLICY_DENIED: 'forbiddenTarget',
    REVISION_CONFLICT: 'stale',
});

function failureKey(outcome, code) {
    if (FAILURE[code]) return FAILURE[code];
    if (outcome === 'permission') return 'denied';
    if (outcome === 'conflict') return 'stale';
    return 'failed';
}

function emptyRule() {
    return { name: '', pattern: '', target_field: '', replacement: '', priority_order: 100, max_input_length: MAX_TEXT_LENGTH };
}

function cleanRule(rule) {
    const out = {
        name: rule.name || 'rule',
        pattern: rule.pattern,
        target_field: rule.target_field,
        priority_order: Number.isInteger(rule.priority_order) ? rule.priority_order : 100,
    };
    if (rule.replacement) out.replacement = rule.replacement;
    if (rule.max_input_length !== undefined && rule.max_input_length !== MAX_TEXT_LENGTH) {
        out.max_input_length = Number(rule.max_input_length);
    }
    return out;
}

/**
 * @param {{request: Function}} client  createClinicApiClient() instance
 * @param {{dictationId: string, currentFields?: object, t?: (key: string) => string}} options
 *   currentFields holds the present metadata values that the preview compares against.
 */
export function createRegexRulesModel(client, options) {
    const opts = options || {};
    const translate = typeof opts.t === 'function' ? opts.t : null;
    const label = (key) => {
        const own = LABELS[key] || key;
        if (!translate) return own;
        const text = translate('clinic.regex.' + key);
        return text && text !== 'clinic.regex.' + key ? text : own;
    };

    const reactive = opts.reactive || ((value) => value);
    const state = reactive({
        rules: opts.manageRules ? [] : [emptyRule()],
        rulesLoaded: !opts.manageRules,
        reloadRequired: false,
        savedRules: [],
        notice: '',
        text: '',
        phase: 'idle', // idle | previewing | preview | applying | applied | failed
        preview: null, // { snapshot, rows, matches, truncated, ruleVersion, inputDigest }
        failure: null, // { key, message, code, correlationId, retryable }
        lastAction: null,
    });
    let draftKey = 0;
    const busy = () => ['loading', 'saving', 'previewing', 'applying'].includes(state.phase);
    const limitValid = (rule) => Number.isInteger(rule.max_input_length)
        && rule.max_input_length >= 1 && rule.max_input_length <= MAX_TEXT_LENGTH;
    const validRule = (rule) => limitValid(rule) && Boolean(rule.pattern && rule.target_field);
    const fromSaved = (rule) => ({ ...emptyRule(), ...rule, replacement: rule.replacement ?? '' });
    const isSaved = (rule) => {
        const saved = state.savedRules.find((entry) => entry.id === rule.id);
        return Boolean(saved) && JSON.stringify(cleanRule(rule)) === JSON.stringify(cleanRule(saved));
    };

    function invalidate() {
        if (busy() || state.reloadRequired) return;
        state.preview = null;
        state.failure = null;
        state.notice = '';
        state.phase = 'idle';
    }

    function fail(action, res) {
        let key = failureKey(res.outcome, res.code);
        if (action === 'save' && res.outcome === 'validation') key = 'invalidRule';
        state.failure = {
            key: key,
            message: label(opts.manageRules && action === 'apply' && key === 'stale' ? 'applyConflict' : key),
            code: res.code || null,
            correlationId: res.correlationId || null,
            retryable: action === 'load' || (action !== 'save' && (res.outcome === 'network' || res.outcome === 'UNKNOWN')),
        };
        state.lastAction = action;
        state.phase = 'failed';
        return state.failure;
    }

    async function loadRules() {
        if (!opts.manageRules || state.rulesLoaded || state.reloadRequired || busy()) return;
        invalidate();
        state.phase = 'loading';
        const res = await client.request(RULES_PATH);
        if (res.outcome !== 'ok' || !Array.isArray(res.data?.rules)) {
            fail('load', res);
            state.failure.message = 'Die gespeicherten Regeln konnten nicht geladen werden.';
            return;
        }
        state.savedRules = res.data.rules.map(fromSaved);
        state.rules = state.savedRules.map((rule) => ({ ...rule, key: ++draftKey }));
        state.rulesLoaded = true;
        state.phase = 'idle';
    }

    function canSave(index) {
        const rule = state.rules[index];
        return opts.manageRules && state.rulesLoaded && !busy() && rule && rule.name.trim()
            && validRule(rule) && !isSaved(rule);
    }

    async function saveRule(index) {
        if (!canSave(index)) return;
        const rule = state.rules[index];
        const body = { ...cleanRule(rule), replacement: rule.replacement || null, max_input_length: rule.max_input_length };
        if (rule.id) body.id = rule.id;
        invalidate();
        state.phase = 'saving';
        const res = await client.request(RULES_PATH, { method: 'POST', body });
        if (res.outcome !== 'ok' || !res.data?.id) {
            fail('save', res);
            state.failure.ruleKey = rule.key;
            if (['ok', 'network', 'UNKNOWN', 'cancelled'].includes(res.outcome)) {
                state.failure.message = 'Das Speichern konnte nicht bestätigt werden. Laden Sie die Seite neu, bevor Sie erneut speichern.';
                state.rulesLoaded = false;
                state.reloadRequired = true;
            }
            return;
        }
        const saved = fromSaved(res.data);
        state.savedRules = [...state.savedRules.filter((entry) => entry.id !== saved.id), saved];
        state.rules[index] = { ...saved, key: rule.key };
        state.phase = 'idle';
        state.notice = 'Regel gespeichert.';
    }

    function canPreview() {
        return state.rulesLoaded && !busy() && Boolean(opts.dictationId) && state.text !== ''
            && state.rules.length > 0 && state.rules.length <= 20 && state.rules.every(validRule);
    }

    function body() {
        const rules = opts.manageRules && state.rules.every(isSaved)
            ? { rule_ids: state.rules.slice().sort((a, b) => a.priority_order - b.priority_order || a.id.localeCompare(b.id)).map((rule) => rule.id) }
            : { rules: state.rules.map(cleanRule) };
        return { dictation_id: String(opts.dictationId), ...rules, text: state.text };
    }

    function rowsFor(fields) {
        const current = opts.currentFields || {};
        return Object.keys(fields).sort().map((field) => ({
            field: field,
            oldValue: String(current[field === 'dictation_name' ? 'title' : field] ?? ''),
            newValue: String(fields[field]),
        }));
    }

    async function runPreview() {
        if (!canPreview()) return null;
        invalidate();
        state.phase = 'previewing';
        const sent = body();
        const res = await client.request(PREVIEW_PATH, { method: 'POST', body: sent });
        if (sent.dictation_id !== String(opts.dictationId)) {
            state.phase = 'idle';
            invalidate();
            return null;
        }
        if (res.outcome !== 'ok' || !res.data) {
            fail('preview', res.outcome === 'ok' ? { outcome: 'UNKNOWN', code: 'UNKNOWN' } : res);
            return null;
        }
        const data = res.data;
        state.preview = {
            snapshot: sent,
            rows: rowsFor(data.resulting_fields || {}),
            matches: Array.isArray(data.matches) ? data.matches : [],
            truncated: data.truncated === true,
            ruleVersion: data.rule_version,
            inputDigest: data.input_digest,
        };
        state.phase = 'preview';
        state.lastAction = null;
        return state.preview;
    }

    // Explicit apply only: sends the exact previewed snapshot with its version and digest.
    async function apply() {
        const shown = state.preview;
        if (state.phase !== 'preview' || !shown || shown.snapshot.dictation_id !== String(opts.dictationId)
            || (opts.manageRules && (!shown.snapshot.rule_ids || !shown.rows.length))) return null;
        state.phase = 'applying';
        state.failure = null;
        const sent = Object.assign({}, shown.snapshot, {
            rule_version: shown.ruleVersion,
            input_digest: shown.inputDigest,
        });
        const res = await client.request(APPLY_PATH, { method: 'POST', body: sent });
        if (res.outcome !== 'ok') {
            const failure = fail('apply', res);
            if (failure.key === 'stale') state.preview = null;
            return null;
        }
        state.phase = 'applied';
        state.lastAction = null;
        return res.data;
    }

    // Retry repeats the failed step; a failed apply returns to the still-valid preview first.
    function retry() {
        if (state.phase !== 'failed' || !state.failure || !state.failure.retryable) return Promise.resolve(null);
        if (state.lastAction === 'load') return loadRules();
        if (state.lastAction === 'apply' && state.preview) {
            state.phase = 'preview';
            return apply();
        }
        return runPreview();
    }

    function discard() {
        invalidate();
    }

    return {
        state: state,
        label: label,
        busy,
        isSaved,
        loadRules,
        canSave,
        saveRule,
        addRule() {
            if (busy() || !state.rulesLoaded || state.rules.length >= 20) return;
            state.rules.push({ ...emptyRule(), key: ++draftKey });
            invalidate();
        },
        removeRule(index) {
            if (busy() || !state.rules[index]) return;
            if (opts.manageRules ? !state.rules[index].id : state.rules.length > 1) {
                state.rules.splice(index, 1);
                invalidate();
                if (opts.manageRules) state.notice = 'Entwurf verworfen.';
            }
        },
        setRule(index, field, value) {
            if (!busy() && state.rules[index] && field in state.rules[index]) {
                state.rules[index][field] = ['priority_order', 'max_input_length'].includes(field) ? Number(value) : value;
                invalidate();
            }
        },
        setText(value) {
            if (busy()) return;
            state.text = String(value);
            invalidate();
        },
        canPreview,
        canApply() {
            return state.phase === 'preview' && state.preview?.rows.length > 0
                && (!opts.manageRules || Boolean(state.preview.snapshot.rule_ids));
        },
        preview: runPreview,
        apply: apply,
        retry: retry,
        discard: discard,
        failureSurface() {
            return state.failure ? surfaceFor('error') : null;
        },
        previewSurface() {
            return surfaceFor(state.phase === 'applied' ? 'success' : 'normal');
        },
    };
}

function style(surface) {
    return {
        background: surface.background,
        color: surface.color,
        border: '1px solid ' + surface.border,
    };
}

/**
 * Vue component factory (Vue 3 global build or module passed in).
 * Props: client, dictationId, currentFields, t. Emits "applied" with the server result.
 */
export function createRegexRulesComponent(Vue) {
    return {
        name: 'ClinicRegexRules',
        props: {
            client: { type: Object, required: true },
            dictationId: { type: String, required: true },
            currentFields: { type: Object, default: () => ({}) },
            manageRules: { type: Boolean, default: false },
            t: { type: Function, default: null },
        },
        emits: ['applied'],
        setup(props, ctx) {
            const model = createRegexRulesModel(props.client, {
                get dictationId() { return props.dictationId; },
                get currentFields() { return props.currentFields; },
                manageRules: props.manageRules,
                reactive: Vue.reactive,
                t: props.t,
            });
            Vue.watch(() => props.dictationId, () => model.setText(''));
            if (props.manageRules) Vue.onMounted(model.loadRules);
            function discardDraft(index) {
                const rule = model.state.rules[index];
                const empty = !rule.name && !rule.pattern && !rule.target_field && !rule.replacement
                    && rule.max_input_length === MAX_TEXT_LENGTH;
                if (empty || window.confirm('Diesen ungespeicherten Entwurf verwerfen?')) model.removeRule(index);
            }
            async function onApply() {
                const data = await model.apply();
                if (data !== null) ctx.emit('applied', data);
            }
            function onKeydown(event) {
                if (event.key === 'Escape') model.discard();
            }
            return { model, style, onApply, onKeydown, discardDraft, TARGET_LABELS, EDIT_TARGETS };
        },
        template: `
<section class="clinic-regex space-y-4" :aria-label="model.label('title')" @keydown="onKeydown">
  <h2 class="text-lg font-semibold text-[var(--text-primary)]">{{ manageRules ? 'Zentrale Regeln' : model.label('title') }}</h2>
  <p class="text-sm text-[var(--text-secondary)]">Mit Regeln lassen sich Titel, Anmerkungen, Dringlichkeit und Frist aus Text ermitteln.</p>
  <p v-if="manageRules && model.state.rulesLoaded && !model.state.rules.length" class="text-sm text-[var(--text-secondary)]">Noch keine Regeln gespeichert. Fügen Sie eine Regel hinzu, um zu beginnen.</p>
     <fieldset v-for="(rule, i) in model.state.rules" :key="rule.key || 0" :disabled="model.busy() || model.state.reloadRequired" class="panel rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
    <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ model.label('rule') }} {{ i + 1 }}</legend>
    <div class="regex-rule-fields grid gap-3 sm:grid-cols-2">
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('name') }}</span>
        <input type="text" class="field" maxlength="128" :value="rule.name" @input="model.setRule(i, 'name', $event.target.value)">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ manageRules ? 'Suchmuster (RegEx)' : model.label('pattern') }}</span>
        <input type="text" class="field font-mono" required maxlength="256" spellcheck="false" :aria-invalid="model.state.failure?.ruleKey === rule.key && model.state.failure?.key === 'invalidRule' ? 'true' : null" :aria-describedby="model.state.failure?.ruleKey === rule.key && model.state.failure?.key === 'invalidRule' ? 'clinic-regex-failure' : null" :value="rule.pattern" @input="model.setRule(i, 'pattern', $event.target.value)">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('target') }}</span>
        <select class="field" required :value="rule.target_field" @change="model.setRule(i, 'target_field', $event.target.value)">
          <option value="" disabled>Feld auswählen</option>
          <option v-if="rule.target_field && !EDIT_TARGETS.includes(rule.target_field)" :value="rule.target_field">{{ TARGET_LABELS[rule.target_field] }}</option>
          <option v-for="target in EDIT_TARGETS" :key="target" :value="target">{{ TARGET_LABELS[target] }}</option>
        </select>
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ manageRules ? 'Text einsetzen (optional)' : model.label('replacement') }}</span>
        <input type="text" class="field font-mono" maxlength="256" :value="rule.replacement" @input="model.setRule(i, 'replacement', $event.target.value)">
      </label>
      <label v-if="manageRules">
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">Maximale Textmenge (Zeichen)</span>
        <input type="number" class="field" min="1" max="10000" step="1" required :value="rule.max_input_length || ''" @input="model.setRule(i, 'max_input_length', $event.target.value)">
        <span class="mt-1 block text-sm text-[var(--text-secondary)]">Die Regel sucht nur am Textanfang. Geben Sie eine Zahl von 1 bis 10’000 ein.</span>
      </label>
    </div>
    <div v-if="manageRules" class="mt-3 flex flex-wrap items-center gap-2">
      <button type="button" class="btn btn--primary" :disabled="!model.canSave(i)" @click="model.saveRule(i)">Regel speichern</button>
      <button v-if="!rule.id" type="button" class="btn btn--ghost" @click="discardDraft(i)">Entwurf verwerfen</button>
      <span v-else-if="model.isSaved(rule)" class="text-sm text-[var(--text-secondary)]">Gespeichert</span>
    </div>
    <p v-if="model.state.failure?.ruleKey === rule.key && model.state.lastAction === 'save'" id="clinic-regex-failure" role="alert" :style="style(model.failureSurface())" class="rounded-lg p-3 text-sm">{{ model.state.failure.message }}</p>
    <button v-if="!manageRules" type="button" class="btn btn--sm btn--ghost mt-3" :disabled="model.state.rules.length < 2" @click="model.removeRule(i)">
      {{ model.label('removeRule') }}
    </button>
  </fieldset>
        <button v-if="model.state.rulesLoaded && model.state.rules.length < 20" type="button" class="btn" :class="manageRules && !model.state.rules.length ? 'btn--primary' : 'btn--ghost'" :disabled="model.busy()" @click="model.addRule()">{{ model.label('addRule') }}</button>
  <p v-if="manageRules && model.state.rules.length >= 20" class="text-sm text-[var(--text-secondary)]">In einer Vorschau können höchstens 20 Regeln geprüft werden.</p>
        <fieldset class="regex-test panel rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3" :disabled="model.busy() || model.state.reloadRequired">
    <legend v-if="manageRules" class="px-1 text-sm font-semibold text-[var(--text-primary)]">Regeln an einem Diktat prüfen</legend>
    <slot name="dictation"></slot>
    <label class="block">
      <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ manageRules ? 'Beispieltext' : model.label('text') }}</span>
      <textarea rows="4" class="field" maxlength="10000" :value="model.state.text" @input="model.setText($event.target.value)"></textarea>
    </label>
    <button type="button" class="btn" :class="manageRules ? 'btn--ghost' : 'btn--primary'" :disabled="!model.canPreview()" @click="model.preview()">{{ model.label('preview') }}</button>
  </fieldset>
  <div role="status" aria-live="polite">
    <p v-if="model.state.phase === 'loading'">Gespeicherte Regeln werden geladen …</p>
    <p v-if="model.state.phase === 'saving'">Regel wird gespeichert …</p>
    <p v-if="model.state.notice">{{ model.state.notice }}</p>
    <p v-if="model.state.phase === 'previewing'" class="text-sm text-[var(--text-muted)]">{{ model.label('previewing') }}</p>
    <p v-if="model.state.phase === 'applying'" class="text-sm text-[var(--text-muted)]">{{ model.label('applying') }}</p>
    <p v-if="model.state.phase === 'applied'" :style="style(model.previewSurface())" class="rounded-lg border p-3 text-sm">{{ model.label('applied') }}</p>
  </div>
     <div v-if="model.state.failure && model.state.lastAction !== 'save'" role="alert" :style="style(model.failureSurface())" class="rounded-lg border p-3 text-sm">
    <p>{{ model.state.failure.message }}</p>
    <button v-if="model.state.failure.retryable" type="button" class="btn btn--sm btn--ghost mt-2" @click="model.retry()">{{ model.label('retry') }}</button>
  </div>
  <div v-if="model.state.preview" role="region" :aria-label="model.label('preview')" class="panel rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
    <div class="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
      <table class="min-w-full divide-y divide-[var(--border-primary)] text-sm">
        <caption class="bg-[var(--bg-tertiary)] px-4 py-2 text-left text-sm font-semibold text-[var(--text-primary)]">{{ model.label('preview') }}</caption>
        <thead class="bg-[var(--bg-tertiary)]"><tr>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('field') }}</th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('oldValue') }}</th>
          <th scope="col" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('newValue') }}</th>
        </tr></thead>
        <tbody class="divide-y divide-[var(--border-primary)] bg-[var(--bg-secondary)]">
          <tr v-for="row in model.state.preview.rows" :key="row.field">
            <th scope="row" class="px-4 py-3 text-left text-sm font-medium text-[var(--text-primary)]">{{ manageRules ? TARGET_LABELS[row.field] : row.field }}</th><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ row.oldValue || (manageRules ? 'Nicht angezeigt' : '–') }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ row.newValue }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!model.state.preview.rows.length" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ model.label('noMatch') }}</p>
    <p v-if="model.state.preview.truncated" class="text-sm text-[var(--text-muted)]">{{ model.label('truncated') }}</p>
    <p v-if="!manageRules" class="text-sm text-[var(--text-secondary)]">{{ model.label('ruleVersion') }}: <code class="rounded bg-[var(--bg-tertiary)] px-1.5">{{ model.state.preview.ruleVersion }}</code></p>
    <p v-if="!manageRules" class="break-all text-sm text-[var(--text-secondary)]">{{ model.label('inputDigest') }}: <code class="rounded bg-[var(--bg-tertiary)] px-1.5">{{ model.state.preview.inputDigest }}</code></p>
    <p v-if="manageRules && !model.state.preview.snapshot.rule_ids" class="text-sm text-[var(--text-secondary)]">Speichern Sie die bearbeiteten Regeln und prüfen Sie die Vorschau erneut, bevor Sie Werte übernehmen.</p>
    <p v-else-if="manageRules" class="text-sm text-[var(--text-secondary)]">Beim Übernehmen werden die angezeigten Werte im ausgewählten Diktat ersetzt. Das Diktat muss zur Bearbeitung geöffnet sein.</p>
    <p v-else class="text-sm text-[var(--text-muted)]">{{ model.label('applyHint') }}</p>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn btn--primary" :disabled="!model.canApply()" @click="onApply">{{ manageRules ? 'Werte im Diktat übernehmen' : model.label('apply') }}</button>
      <button type="button" class="btn btn--ghost" @click="model.discard()">{{ model.label('discard') }}</button>
    </div>
  </div>
</section>`,
    };
}
