/**
 * Dictation properties editor: priority, deadline, recipient, categories.
 *
 * `createDictationPropertiesModel` is a framework-free state holder (pass Vue's
 * `reactive` to make it reactive). `DictationPropertiesComponent(Vue)` wraps it
 * as a Vue component. Requests go through the clinic api client; the server
 * stays the authority and validate is read-only. Form values live in memory
 * only: no storage, no logging. A conflict, a failure or a refusal never
 * clears what the user typed. Colours come from tokens.js (no new palette).
 */

import { surfaceFor } from './tokens.js';

export const VALIDATE_PATH = '/dictations/properties/validate';
export const DEADLINE_ZONE = 'Europe/Zurich';
export const END_OF_DAY = '23:59:59';

export const PRIORITY_OPTIONS = Object.freeze([
    Object.freeze({ value: 0, label: 'Bei Gelegenheit' }),
    Object.freeze({ value: 1, label: 'Normal' }),
    Object.freeze({ value: 128, label: 'Dringend' }),
]);

export const TEXT = Object.freeze({
    title: 'Diktat-Eigenschaften',
    titleField: 'Titel',
    recipient: 'Empfänger',
    priority: 'Priorität',
    category: 'Kategorie',
    deadlineDate: 'Fällig am (Datum)',
    deadlineTime: 'Uhrzeit (optional)',
    check: 'Prüfen',
    retry: 'Erneut versuchen',
    keepMine: 'Meine Eingaben behalten',
    fillMarked: 'Bitte die markierten Felder ausfüllen.',
    missingTitle: 'Bitte einen Titel angeben.',
    missingRecipient: 'Bitte einen Empfänger wählen.',
    missingCategory: 'Bitte eine Kategorie wählen.',
    missingPriority: 'Bitte eine Priorität wählen.',
    missingDeadlineDate: 'Für eine Uhrzeit ist ein Datum erforderlich.',
    invalidDeadline: 'Datum oder Uhrzeit sind ungültig.',
    conflict: 'Die Eigenschaften wurden zwischenzeitlich geändert. Ihre Eingaben bleiben erhalten.',
    permission: 'Keine Berechtigung für diese Aktion.',
    network: 'Die Verbindung ist fehlgeschlagen. Ihre Eingaben bleiben erhalten.',
    unknown: 'Das Ergebnis ist unbekannt. Ihre Eingaben bleiben erhalten.',
    server: 'Die Angaben wurden vom Server abgelehnt.',
    valid: 'Die Eigenschaften sind gültig.',
});

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/;

function emptyFields() {
    return { title: '', poolId: '', priority: null, selections: {}, deadlineDate: '', deadlineTime: '', source: '' };
}

/** Deadline as shown to the user; time omitted means end of day in Zurich. */
export function describeDeadline(date, time) {
    if (!date) return '';
    const at = time ? (time.length === 5 ? time + ':00' : time) : END_OF_DAY;
    return date + ' ' + at + ' ' + DEADLINE_ZONE;
}

/** Local required-field check; returns {field: message}. Server stays the authority. */
export function localErrors(fields, categorySets) {
    const errors = {};
    if (!fields.title.trim()) errors.title = TEXT.missingTitle;
    if (!fields.poolId) errors.poolId = TEXT.missingRecipient;
    if (!PRIORITY_OPTIONS.some((o) => o.value === fields.priority)) errors.priority = TEXT.missingPriority;
    for (const set of categorySets) {
        const value = fields.selections[set.setId];
        if (set.required && (value === undefined || value === null || value === '')) {
            errors['category:' + set.setId] = TEXT.missingCategory;
        }
    }
    const { deadlineDate: date, deadlineTime: time } = fields;
    if (!date && time) errors.deadlineDate = TEXT.missingDeadlineDate;
    else if (date && (!DATE.test(date) || Number.isNaN(Date.parse(date)))) errors.deadlineDate = TEXT.invalidDeadline;
    else if (time && !TIME.test(time)) errors.deadlineTime = TEXT.invalidDeadline;
    return errors;
}

function bodyFor(fields, categorySets) {
    const body = {
        title: fields.title.trim(),
        pool_id: fields.poolId,
        priority: fields.priority,
        source: fields.source,
        categories: categorySets
            .filter((s) => fields.selections[s.setId] !== undefined && fields.selections[s.setId] !== '')
            .map((s) => ({ set_id: s.setId, version: s.version, value: fields.selections[s.setId] })),
    };
    if (fields.deadlineDate) body.deadline_date = fields.deadlineDate;
    if (fields.deadlineDate && fields.deadlineTime) body.deadline_time = fields.deadlineTime;
    return body;
}

/**
 * @param {{request: Function}} client  createClinicApiClient() instance
 * @param {{categorySets?: Array, reactive?: Function}} [options]
 *   categorySets: [{setId, label, version, required, values: [{value, label}]}]
 */
export function createDictationPropertiesModel(client, options) {
    const opts = options || {};
    const reactive = opts.reactive || ((o) => o);
    const categorySets = opts.categorySets || [];
    const state = reactive({
        fields: emptyFields(),
        errors: {},
        status: 'idle',
        message: '',
        conflict: false,
        valid: false,
    });

    function fail(status, message, conflict) {
        state.status = status;
        state.message = message;
        state.conflict = Boolean(conflict);
        state.valid = false;
    }

    async function validate() {
        state.errors = localErrors(state.fields, categorySets);
        if (Object.keys(state.errors).length) {
            fail('invalid', TEXT.fillMarked, false);
            return state;
        }
        state.status = 'busy';
        const res = await client.request(VALIDATE_PATH, { method: 'POST', body: bodyFor(state.fields, categorySets) });
        if (res.outcome === 'ok') {
            state.status = 'valid';
            state.message = TEXT.valid;
            state.conflict = false;
            state.valid = true;
        } else if (res.outcome === 'conflict') fail('conflict', TEXT.conflict, true);
        else if (res.outcome === 'permission') fail('denied', TEXT.permission, false);
        else if (res.outcome === 'validation') fail('invalid', TEXT.server, false);
        else if (res.outcome === 'network') fail('retry', TEXT.network, false);
        else if (res.outcome === 'cancelled') fail('idle', '', false);
        else fail('retry', TEXT.unknown, false);
        return state;
    }

    return {
        state,
        priorityOptions: PRIORITY_OPTIONS,
        categorySets,
        validate,
        retry: validate,
        keepMine() { state.conflict = false; state.status = 'idle'; state.message = ''; },
        reset() { state.fields = emptyFields(); state.errors = {}; fail('idle', '', false); },
        deadlinePreview: () => describeDeadline(state.fields.deadlineDate, state.fields.deadlineTime),
        surface: () => surfaceFor(state.status === 'valid' ? 'success'
            : state.status === 'busy' ? 'processing'
            : state.conflict ? 'warning'
            : ['invalid', 'denied', 'retry'].includes(state.status) ? 'error' : 'normal'),
    };
}

/**
 * Vue component factory. Vue is passed in so the module needs no bundler.
 * Props: client, categorySets, recipients ([{value, label}]), sources ([{value, label}]).
 */
export function DictationPropertiesComponent(Vue) {
    return {
        name: 'DictationProperties',
        props: {
            client: { type: Object, required: true },
            categorySets: { type: Array, default: () => [] },
            recipients: { type: Array, default: () => [] },
            sources: { type: Array, default: () => [] },
        },
        setup(props) {
            const model = createDictationPropertiesModel(props.client, {
                categorySets: props.categorySets,
                reactive: Vue.reactive,
            });
            return {
                model,
                text: TEXT,
                priorities: PRIORITY_OPTIONS,
                preview: Vue.computed(() => model.deadlinePreview()),
                surfaceStyle: Vue.computed(() => {
                    const s = model.surface();
                    return { background: s.background, color: s.color, borderColor: s.border };
                }),
            };
        },
        template: `
<form class="clinic-dictation-properties" novalidate @submit.prevent="model.validate()">
  <h2>{{ text.title }}</h2>
  <label>{{ text.titleField }}
    <input type="text" v-model="model.state.fields.title" required
           :aria-invalid="!!model.state.errors.title" aria-describedby="dp-err-title">
  </label>
  <p id="dp-err-title" role="alert">{{ model.state.errors.title }}</p>
  <label>{{ text.recipient }}
    <select v-model="model.state.fields.poolId" required
            :aria-invalid="!!model.state.errors.poolId" aria-describedby="dp-err-pool">
      <option value="" disabled></option>
      <option v-for="r in recipients" :key="r.value" :value="r.value">{{ r.label }}</option>
    </select>
  </label>
  <p id="dp-err-pool" role="alert">{{ model.state.errors.poolId }}</p>
  <fieldset :aria-invalid="!!model.state.errors.priority" aria-describedby="dp-err-prio">
    <legend>{{ text.priority }}</legend>
    <label v-for="p in priorities" :key="p.value">
      <input type="radio" name="dp-priority" :value="p.value" v-model="model.state.fields.priority">
      {{ p.label }}
    </label>
  </fieldset>
  <p id="dp-err-prio" role="alert">{{ model.state.errors.priority }}</p>
  <template v-for="set in categorySets" :key="set.setId">
    <label>{{ set.label }}
      <select v-model="model.state.fields.selections[set.setId]" :required="set.required"
              :aria-invalid="!!model.state.errors['category:' + set.setId]">
        <option value=""></option>
        <option v-for="v in set.values" :key="v.value" :value="v.value">{{ v.label }}</option>
      </select>
    </label>
    <p role="alert">{{ model.state.errors['category:' + set.setId] }}</p>
  </template>
  <label>{{ text.deadlineDate }}
    <input type="date" v-model="model.state.fields.deadlineDate"
           :aria-invalid="!!model.state.errors.deadlineDate" aria-describedby="dp-deadline">
  </label>
  <label>{{ text.deadlineTime }}
    <input type="time" v-model="model.state.fields.deadlineTime"
           :aria-invalid="!!model.state.errors.deadlineTime" aria-describedby="dp-deadline">
  </label>
  <p id="dp-deadline" aria-live="polite">{{ preview }}</p>
  <p role="alert">{{ model.state.errors.deadlineDate || model.state.errors.deadlineTime }}</p>
  <div role="status" aria-live="polite" :style="surfaceStyle">{{ model.state.message }}</div>
  <button type="submit" :disabled="model.state.status === 'busy'">{{ text.check }}</button>
  <button v-if="model.state.status === 'retry'" type="button" @click="model.retry()">{{ text.retry }}</button>
  <button v-if="model.state.conflict" type="button" @click="model.keepMine()">{{ text.keepMine }}</button>
</form>`,
    };
}
