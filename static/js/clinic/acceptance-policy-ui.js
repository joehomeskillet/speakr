/**
 * Acceptance policy editor (PAR-WF-009, PAR-WF-010).
 *
 * The policy of one dictation comes from GET /dictations/<id>/acceptance-policy:
 * { version, required, approver_user_ids, eligible: [{user_id, name}],
 *   decisions: [{kind, actor_id, actor_name, revision_id, reason, policy_version}] }.
 * Edits stay in memory until saved through the parity command channel with the
 * policy version as expected revision. Author and admin are never approvers by
 * label: only explicitly selected, eligible participants count. Participants
 * that are no longer eligible are flagged removed and block the save until they
 * are deselected. Clinical data is never written to browser storage. Status
 * colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Pflichtfreigabe',
    required: 'Freigabe erforderlich',
    approvers: 'Berechtigte Freigebende',
    save: 'Speichern',
    decisions: 'Entscheidungen',
    actor: 'Verantwortlich',
    removed: 'nicht mehr berechtigt',
});

export const DECISION_LABELS = Object.freeze({
    pending: 'Ausstehend',
    accepted: 'Akzeptiert',
    rejected: 'Abgelehnt',
});

const REASONS = Object.freeze({
    not_eligible: 'Diese Person ist nicht freigabeberechtigt.',
    removed_participant: 'Eine entfernte Person ist noch ausgewählt.',
    no_approver: 'Eine Pflichtfreigabe braucht mindestens eine freigebende Person.',
    not_loaded: 'Die Richtlinie ist nicht geladen.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    failure: 'Das Speichern ist fehlgeschlagen.',
});

// Keyboard states: Space/Enter toggles the focused participant.
export const KEYS = Object.freeze({ toggle: [' ', 'Enter'] });

function refuse(code) {
    return { ok: false, code: code, message: REASONS[code] || REASONS.failure };
}

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'acc-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function decisionState(kind) {
    if (kind === 'accept' || kind === 'accepted') return 'accepted';
    if (kind === 'reject' || kind === 'rejected' || kind === 'return') return 'rejected';
    return 'pending';
}

/**
 * @param {{client?: object, reactive?: Function}} [options]
 */
export function createAcceptancePolicyEditor(options) {
    const client = (options && options.client) || createClinicApiClient();
    const wrap = (options && options.reactive) || ((value) => value);
    const state = wrap({
        loaded: false,
        dictationId: null,
        version: 0,
        required: false,
        approvers: [], // selected user ids
        eligible: [], // { userId, name }
        decisions: [], // { state, label, actorId, actorName, revisionId, reason }
        stale: false,
        status: 'normal',
    });

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    function isEligible(userId) {
        return state.eligible.some((row) => row.userId === userId);
    }

    /** Selected approvers that are no longer eligible. */
    function removed() {
        return state.approvers.filter((id) => !isEligible(id));
    }

    async function load(dictationId) {
        state.dictationId = String(dictationId);
        client.switchContext({ dictationId: state.dictationId });
        setStatus('processing');
        const row = await client.request('/dictations/' + encodeURIComponent(state.dictationId) + '/acceptance-policy');
        if (row.outcome !== 'ok') {
            setStatus(row.outcome === 'permission' ? 'warning' : 'error');
            return refuse(row.outcome === 'permission' ? 'permission' : 'failure');
        }
        const data = row.data || {};
        state.version = Number.isInteger(data.version) ? data.version : 0;
        state.required = !!data.required;
        state.approvers = (data.approver_user_ids || []).slice();
        state.eligible = (data.eligible || []).map((p) => ({ userId: p.user_id, name: p.name }));
        state.decisions = (data.decisions || []).map((d) => {
            const s = decisionState(d.kind);
            return {
                state: s,
                label: DECISION_LABELS[s],
                actorId: d.actor_id,
                actorName: d.actor_name,
                revisionId: d.revision_id,
                reason: d.reason || null,
                current: d.policy_version === state.version,
            };
        });
        state.stale = false;
        state.loaded = true;
        setStatus('success');
        return { ok: true };
    }

    function setRequired(flag) {
        if (!state.loaded) return refuse('not_loaded');
        state.required = !!flag;
        return { ok: true };
    }

    function toggle(userId) {
        if (!state.loaded) return refuse('not_loaded');
        const at = state.approvers.indexOf(userId);
        if (at >= 0) {
            state.approvers.splice(at, 1);
            return { ok: true };
        }
        if (!isEligible(userId)) return refuse('not_eligible');
        state.approvers.push(userId);
        return { ok: true };
    }

    /** Rows for rendering with the checked state and the removed flag. */
    function participants() {
        const rows = state.eligible.map((p) => ({
            userId: p.userId,
            name: p.name,
            checked: state.approvers.includes(p.userId),
            removed: false,
            ariaLabel: p.name,
        }));
        for (const id of removed()) {
            rows.push({
                userId: id,
                name: String(id),
                checked: true,
                removed: true,
                ariaLabel: id + ' (' + LABELS.removed + ')',
            });
        }
        return rows;
    }

    async function save() {
        if (!state.loaded) return refuse('not_loaded');
        if (state.stale) return refuse('stale_version');
        if (removed().length > 0) return refuse('removed_participant');
        if (state.required && state.approvers.length === 0) return refuse('no_approver');
        setStatus('processing');
        const key = newKey();
        const result = await client.command(
            {
                command: 'save_acceptance_policy',
                request_id: key,
                expected_revision: state.version,
                payload: {
                    dictation_id: state.dictationId,
                    required: state.required,
                    approver_user_ids: state.approvers.slice(),
                },
            },
            { idempotencyKey: key, ifMatch: '"' + state.version + '"' },
        );
        if (result.outcome === 'ok') {
            const data = result.data || {};
            if (Number.isInteger(data.revision)) state.version = data.revision;
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            state.stale = true;
            setStatus('warning');
            return refuse('stale_version');
        }
        setStatus(result.outcome === 'permission' ? 'warning' : 'error');
        return refuse(result.outcome === 'permission' ? 'permission' : 'failure');
    }

    /** Keyboard handler for a participant row. */
    function onKey(userId, event) {
        if (!event) return { ok: false };
        if (KEYS.toggle.includes(event.key)) return toggle(userId);
        return { ok: false };
    }

    function reset() {
        state.loaded = false;
        state.dictationId = null;
        state.version = 0;
        state.required = false;
        state.approvers = [];
        state.eligible = [];
        state.decisions = [];
        state.stale = false;
        state.status = 'normal';
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, setRequired, toggle, participants, save, onKey, reset };
}

/** Vue composable. `vue` supplies { reactive }. */
export function useAcceptancePolicyEditor(vue, options) {
    const editor = createAcceptancePolicyEditor(Object.assign({}, options, { reactive: vue.reactive }));
    return Object.assign(editor, { labels: LABELS, keys: KEYS, decisionLabels: DECISION_LABELS });
}

/** Component definition. Colours are CSS variables from tokens.js. */
export function acceptancePolicyComponent(vue, options) {
    return {
        name: 'ClinicAcceptancePolicy',
        props: { dictationId: { type: String, required: true } },
        setup(props) {
            const editor = useAcceptancePolicyEditor(vue, options);
            editor.load(props.dictationId);
            return { editor };
        },
        template: `
<section role="region" aria-labelledby="clinic-acc-title" :aria-busy="editor.state.status === 'processing'">
  <h2 id="clinic-acc-title">${LABELS.title}</h2>
  <label><input type="checkbox" :checked="editor.state.required" @change="editor.setRequired($event.target.checked)"> ${LABELS.required}</label>
  <fieldset>
    <legend>${LABELS.approvers}</legend>
    <ul role="list">
      <li v-for="p in editor.participants()" :key="p.userId">
        <label><input type="checkbox" :checked="p.checked" :aria-label="p.ariaLabel" @change="editor.toggle(p.userId)" @keydown="editor.onKey(p.userId, $event)"> {{ p.name }}<span v-if="p.removed"> (${LABELS.removed})</span></label>
      </li>
    </ul>
  </fieldset>
  <button type="button" @click="editor.save()">${LABELS.save}</button>
  <h3>${LABELS.decisions}</h3>
  <ul role="list" aria-label="${LABELS.decisions}">
    <li v-for="d in editor.state.decisions" :key="d.revisionId + d.actorId">{{ d.label }} &ndash; ${LABELS.actor}: {{ d.actorName }}</li>
  </ul>
</section>`,
    };
}
