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
    return { name: '', pattern: '', target_field: '', replacement: '', priority_order: 100 };
}

function cleanRule(rule) {
    const out = {
        name: rule.name || 'rule',
        pattern: rule.pattern,
        target_field: rule.target_field,
        priority_order: Number.isInteger(rule.priority_order) ? rule.priority_order : 100,
    };
    if (rule.replacement) out.replacement = rule.replacement;
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

    const state = {
        rules: [emptyRule()],
        text: '',
        phase: 'idle', // idle | previewing | preview | applying | applied | failed
        preview: null, // { snapshot, rows, matches, truncated, ruleVersion, inputDigest }
        failure: null, // { key, message, code, correlationId, retryable }
        lastAction: null,
    };

    function invalidate() {
        if (state.phase === 'previewing' || state.phase === 'applying') return;
        state.preview = null;
        state.failure = null;
        state.phase = 'idle';
    }

    function fail(action, res) {
        const key = failureKey(res.outcome, res.code);
        state.failure = {
            key: key,
            message: label(key),
            code: res.code || null,
            correlationId: res.correlationId || null,
            retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
        };
        state.lastAction = action;
        state.phase = 'failed';
        return state.failure;
    }

    function body(extra) {
        return Object.assign({
            dictation_id: String(opts.dictationId),
            rules: state.rules.map(cleanRule),
            text: state.text,
        }, extra || {});
    }

    function rowsFor(fields) {
        const current = opts.currentFields || {};
        return Object.keys(fields).sort().map((field) => ({
            field: field,
            oldValue: current[field] === undefined || current[field] === null ? '' : String(current[field]),
            newValue: String(fields[field]),
        }));
    }

    async function runPreview() {
        if (state.phase === 'previewing' || state.phase === 'applying') return null;
        state.phase = 'previewing';
        state.failure = null;
        state.preview = null;
        const sent = body();
        const res = await client.request(PREVIEW_PATH, { method: 'POST', body: sent });
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
        if (state.phase !== 'preview' || !shown) return null;
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
            else state.phase = 'failed';
            return null;
        }
        state.phase = 'applied';
        state.lastAction = null;
        return res.data;
    }

    // Retry repeats the failed step; a failed apply returns to the still-valid preview first.
    function retry() {
        if (state.phase !== 'failed' || !state.failure || !state.failure.retryable) return Promise.resolve(null);
        if (state.lastAction === 'apply' && state.preview) {
            state.phase = 'preview';
            return apply();
        }
        return runPreview();
    }

    function discard() {
        if (state.phase === 'previewing' || state.phase === 'applying') return;
        invalidate();
    }

    return {
        state: state,
        label: label,
        addRule() {
            state.rules.push(emptyRule());
            invalidate();
        },
        removeRule(index) {
            if (state.rules.length > 1 && index >= 0 && index < state.rules.length) {
                state.rules.splice(index, 1);
                invalidate();
            }
        },
        setRule(index, field, value) {
            if (state.rules[index] && field in state.rules[index]) {
                state.rules[index][field] = field === 'priority_order' ? Number(value) : value;
                invalidate();
            }
        },
        setText(value) {
            state.text = String(value);
            invalidate();
        },
        canPreview() {
            return state.phase !== 'previewing' && state.phase !== 'applying'
                && state.text !== '' && state.rules.every((r) => r.pattern && r.target_field);
        },
        canApply() {
            return state.phase === 'preview' && state.preview !== null && state.preview.rows.length > 0;
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
            t: { type: Function, default: null },
        },
        emits: ['applied'],
        setup(props, ctx) {
            const model = Vue.reactive(createRegexRulesModel(props.client, {
                dictationId: props.dictationId,
                currentFields: props.currentFields,
                t: props.t,
            }));
            async function onApply() {
                const data = await model.apply();
                if (data !== null) ctx.emit('applied', data);
            }
            function onKeydown(event) {
                if (event.key === 'Escape') model.discard();
            }
            return { model, style, onApply, onKeydown };
        },
        template: `
<section class="clinic-regex space-y-4" :aria-label="model.label('title')" @keydown="onKeydown">
  <h2 class="text-lg font-semibold text-[var(--text-primary)]">{{ model.label('title') }}</h2>
  <fieldset v-for="(rule, i) in model.state.rules" :key="i" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
    <legend class="px-1 text-sm font-semibold text-[var(--text-primary)]">{{ model.label('rule') }} {{ i + 1 }}</legend>
    <div class="grid gap-3 sm:grid-cols-2">
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('name') }}</span>
        <input type="text" class="field" :value="rule.name" @input="model.setRule(i, 'name', $event.target.value)">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('pattern') }}</span>
        <input type="text" class="field font-mono" required :id="'clinic-regex-pattern-' + i" :aria-invalid="model.state.failure ? 'true' : null" :aria-describedby="model.state.failure ? 'clinic-regex-failure' : null" :value="rule.pattern" @input="model.setRule(i, 'pattern', $event.target.value)">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('target') }}</span>
        <input type="text" class="field" required :id="'clinic-regex-target-' + i" :aria-invalid="model.state.failure ? 'true' : null" :aria-describedby="model.state.failure ? 'clinic-regex-failure' : null" :value="rule.target_field" @input="model.setRule(i, 'target_field', $event.target.value)">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('replacement') }}</span>
        <input type="text" class="field font-mono" :value="rule.replacement" @input="model.setRule(i, 'replacement', $event.target.value)">
      </label>
    </div>
    <button type="button" class="btn btn--sm btn--ghost mt-3" :disabled="model.state.rules.length < 2" @click="model.removeRule(i)">
      {{ model.label('removeRule') }}
    </button>
  </fieldset>
  <button type="button" class="btn btn--sm btn--ghost" @click="model.addRule()">{{ model.label('addRule') }}</button>
  <label class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 block">
    <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">{{ model.label('text') }}</span>
    <textarea rows="4" class="field" :value="model.state.text" @input="model.setText($event.target.value)"></textarea>
  </label>
  <button type="button" class="btn btn--primary" :disabled="!model.canPreview()" @click="model.preview()">{{ model.label('preview') }}</button>
  <div role="status" aria-live="polite" class="empty:hidden">
    <p v-if="model.state.phase === 'previewing'" class="text-sm text-[var(--text-muted)]">{{ model.label('previewing') }}</p>
    <p v-if="model.state.phase === 'applying'" class="text-sm text-[var(--text-muted)]">{{ model.label('applying') }}</p>
    <p v-if="model.state.phase === 'applied'" :style="style(model.previewSurface())" class="rounded-lg border p-3 text-sm">{{ model.label('applied') }}</p>
  </div>
  <div v-if="model.state.failure" id="clinic-regex-failure" role="alert" :style="style(model.failureSurface())" class="rounded-lg border p-3 text-sm">
    <p>{{ model.state.failure.message }}</p>
    <button v-if="model.state.failure.retryable" type="button" class="btn btn--sm btn--ghost mt-2" @click="model.retry()">{{ model.label('retry') }}</button>
  </div>
  <div v-if="model.state.preview" role="region" :aria-label="model.label('preview')" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
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
            <th scope="row" class="px-4 py-3 text-left text-sm font-medium text-[var(--text-primary)]">{{ row.field }}</th><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ row.oldValue }}</td><td class="px-4 py-3 text-sm text-[var(--text-secondary)]">{{ row.newValue }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!model.state.preview.rows.length" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">{{ model.label('noMatch') }}</p>
    <p v-if="model.state.preview.truncated" class="text-sm text-[var(--text-muted)]">{{ model.label('truncated') }}</p>
    <p class="text-sm text-[var(--text-secondary)]">{{ model.label('ruleVersion') }}: <code class="rounded bg-[var(--bg-tertiary)] px-1.5">{{ model.state.preview.ruleVersion }}</code></p>
    <p class="break-all text-sm text-[var(--text-secondary)]">{{ model.label('inputDigest') }}: <code class="rounded bg-[var(--bg-tertiary)] px-1.5">{{ model.state.preview.inputDigest }}</code></p>
    <p class="text-sm text-[var(--text-muted)]">{{ model.label('applyHint') }}</p>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="btn btn--primary" :disabled="!model.canApply()" @click="onApply">{{ model.label('apply') }}</button>
      <button type="button" class="btn btn--ghost" @click="model.discard()">{{ model.label('discard') }}</button>
    </div>
  </div>
</section>`,
    };
}
