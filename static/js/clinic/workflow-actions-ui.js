/**
 * Workflow actions panel: correction, acceptance, final release and archive.
 *
 * The server decides which actions are allowed and which state each leads to;
 * this module renders only those. ASR status and work state are shown apart
 * from release/acceptance output. Clinical data stays in memory only. Colours
 * come from tokens.js (semantic variables), calls go through api-client.js.
 * Labels use window.i18n.t when a key exists and fall back to German defaults.
 */

import { surfaceFor, TokenContractError } from './tokens.js';

const ROOT = '/api/clinic/v1/dictations/';

// group: separate sections; confirm: explicit confirmation before sending;
// reason: free text required; kind: parity command or dictation route.
export const ACTIONS = Object.freeze({
    send: { group: 'correction', kind: 'route', path: 'send', confirm: true, de: 'Einreichen' },
    submit: { group: 'acceptance', kind: 'command', command: 'request_acceptance', de: 'Zur Abnahme einreichen' },
    accept: { group: 'acceptance', kind: 'command', command: 'decide_acceptance', decision: 'accept', de: 'Zusätzlich abnehmen' },
    reject: { group: 'correction', kind: 'command', command: 'decide_acceptance', decision: 'return', confirm: true, reason: true, de: 'Zur Korrektur zurückweisen' },
    reopen: { group: 'correction', kind: 'command', command: 'reopen_dictation', confirm: true, reason: true, de: 'Wieder öffnen' },
    release: { group: 'release', kind: 'route', path: 'releases', confirm: true, de: 'Endgültig freigeben' },
    archive: { group: 'archive', kind: 'route', path: 'archive', confirm: true, de: 'Archivieren' },
});

export const GROUPS = Object.freeze({
    correction: 'Korrektur',
    acceptance: 'Abnahme',
    release: 'Freigabe',
    archive: 'Archiv',
});

const CONFIRM_TEXT = Object.freeze({
    send: 'Das Diktat wird zur Bearbeitung eingereicht.',
    reject: 'Das Diktat wird zur Korrektur zurückgewiesen.',
    reopen: 'Das freigegebene Diktat wird wieder geöffnet.',
    release: 'Die geprüften Versionen und der Kontext werden endgültig freigegeben.',
    archive: 'Das Diktat wird archiviert. Es wird nichts gelöscht.',
});

function uuid() {
    return globalThis.crypto.randomUUID();
}

function translate(key, fallback) {
    const i18n = typeof window !== 'undefined' ? window.i18n : null;
    if (i18n && typeof i18n.t === 'function') {
        const text = i18n.t(key);
        if (typeof text === 'string' && text && text !== key) return text;
    }
    return fallback;
}

function badge(status) {
    try {
        const s = surfaceFor(status);
        return { label: s.label, style: { background: s.background, color: s.color, borderColor: s.border } };
    } catch (e) {
        if (e instanceof TokenContractError) return null;
        throw e;
    }
}

/**
 * @param {{client: object, reactive?: Function}} options
 *   client: createClinicApiClient() instance; reactive: Vue.reactive (optional).
 */
export function createWorkflowActions(options) {
    const client = options.client;
    const wrap = typeof options.reactive === 'function' ? options.reactive : (v) => v;
    const state = wrap({
        dictationId: null,
        etag: null,
        revision: null,
        allowed: [],
        transitions: {},
        asrStatus: null,
        workState: null,
        binding: {},
        confirming: null,
        reason: '',
        confirmedContent: false,
        confirmedContext: false,
        busy: false,
        error: null,
        stale: false,
        lastResult: null,
    });
    // Same request id on a retry so the server replays instead of repeating.
    let pending = null;
    let generation = 0;

    function load(server) {
        generation += 1;
        const next = server || {};
        state.dictationId = next.dictationId || null;
        state.etag = next.etag || null;
        state.revision = Number.isInteger(next.revision) ? next.revision : null;
        state.allowed = (next.allowedActions || []).filter((a) => Object.prototype.hasOwnProperty.call(ACTIONS, a));
        state.transitions = Object.assign({}, next.transitions);
        state.asrStatus = next.asrStatus || null;
        state.workState = next.workState || null;
        state.binding = Object.assign({}, next.binding);
        state.stale = false;
        cancel();
    }

    function visible() {
        return state.allowed.map((id) => ({
            id: id,
            group: ACTIONS[id].group,
            label: translate('clinic.workflow.action.' + id, ACTIONS[id].de),
            target: state.transitions[id] || null,
            needsConfirm: !!ACTIONS[id].confirm,
        }));
    }

    function groups() {
        const rows = visible();
        return Object.keys(GROUPS)
            .map((g) => ({ id: g, label: translate('clinic.workflow.group.' + g, GROUPS[g]), actions: rows.filter((r) => r.group === g) }))
            .filter((g) => g.actions.length > 0);
    }

    function statuses() {
        return { asr: badge(state.asrStatus), work: badge(state.workState) };
    }

    function cancel() {
        state.confirming = null;
        state.reason = '';
        state.confirmedContent = false;
        state.confirmedContext = false;
        state.error = null;
    }

    function request(id) {
        if (!state.allowed.includes(id) || state.busy) return false;
        const blocked = blockedReason(id);
        if (blocked) { state.error = blocked; return false; }
        if (ACTIONS[id].confirm) {
            state.confirming = id;
            state.error = null;
            return true;
        }
        send(id);
        return true;
    }

    function confirm() {
        const id = state.confirming;
        if (!id) return Promise.resolve(null);
        const spec = ACTIONS[id];
        if (spec.reason && !state.reason.trim()) {
            state.error = translate('clinic.workflow.reason_required', 'Bitte eine Begründung angeben.');
            return Promise.resolve(null);
        }
        if (id === 'release' && !(state.confirmedContent && state.confirmedContext)) {
            state.error = translate('clinic.workflow.release_confirm', 'Inhalt und Kontext müssen bestätigt werden.');
            return Promise.resolve(null);
        }
        return send(id);
    }

    function body(id) {
        const spec = ACTIONS[id];
        const b = state.binding;
        if (spec.kind === 'route') {
            if (id === 'send') return {};
            if (id === 'archive') return { action: 'archive', confirm: true };
            return Object.assign({}, b.release, { confirmed_content: true, confirmed_context: true });
        }
        const payload = { dictation_id: state.dictationId };
        if (id === 'submit') {
            Object.assign(payload, { text_revision_id: b.textRevisionId, context_revision_id: b.contextRevisionId, acceptance_rule_id: b.acceptanceRuleId });
        } else if (spec.command === 'decide_acceptance') {
            payload.text_revision_id = b.textRevisionId;
            payload.decision = spec.decision;
            if (state.reason.trim()) payload.reason = state.reason.trim();
        } else {
            payload.reason = state.reason.trim();
        }
        return payload;
    }

    function blockedReason(id) {
        if (state.stale || !state.dictationId || !state.etag || !Number.isInteger(state.revision)) {
            return 'Der Arbeitsstand ist nicht aktuell. Bitte neu laden.';
        }
        const b = state.binding;
        if (id === 'submit' && (!b.audioRevisionId || !b.textRevisionId || !b.contextRevisionId || !b.acceptanceRuleId)) {
            return 'Audio, Text, Fall-Kontext oder Abnahmeregel fehlen. Bitte neu laden.';
        }
        if (id === 'release' && (!b.release?.original_version_id || !b.release?.transcript_revision_id
                || !b.release?.context_revision_id || !b.release?.text_sha256)) {
            return 'Audio, geprüfter Text oder Fall-Kontext fehlen. Freigabe ist gesperrt.';
        }
        if ((id === 'accept' || id === 'reject') && !b.textRevisionId) return 'Die Textrevision fehlt.';
        return null;
    }

    async function send(id) {
        if (state.busy || !state.allowed.includes(id)) return null;
        const blocked = blockedReason(id);
        if (blocked) { state.error = blocked; return null; }
        const mine = generation;
        const spec = ACTIONS[id];
        const sig = id + '|' + state.etag;
        if (!pending || pending.sig !== sig) pending = { sig: sig, key: uuid() };
        state.busy = true;
        state.error = null;
        let res;
        try {
            if (spec.kind === 'command') {
                const envelope = { command: spec.command, request_id: pending.key, expected_revision: state.revision + 1, payload: body(id) };
                res = await client.command(envelope, { idempotencyKey: pending.key });
            } else {
                const payload = body(id);
                if (id === 'release') {
                    const proof = await client.request(ROOT + state.dictationId + '/leases');
                    if (mine !== generation) return null;
                    if (proof.outcome !== 'ok') res = proof;
                    else if (!proof.data?.lease_id || proof.data.held !== true
                            || !Number.isInteger(proof.data.generation) || proof.data.generation < 1) {
                        res = { outcome: 'conflict', code: 'LEASE_REQUIRED',
                            message: 'Bitte die Bearbeitung übernehmen. Nur der aktuelle Halter darf freigeben.' };
                    } else Object.assign(payload, { lease_id: proof.data.lease_id, lease_generation: proof.data.generation });
                }
                if (!res) res = await client.request(ROOT + state.dictationId + '/' + spec.path, {
                    method: 'POST', body: payload, idempotencyKey: pending.key, ifMatch: state.etag,
                });
            }
        } finally {
            state.busy = false;
        }
        if (res.outcome === 'ok') {
            pending = null;
            state.lastResult = { action: id, data: res.data };
            state.confirming = null;
            state.reason = '';
            return res;
        }
        // A definitive refusal ends the retry key; network/unknown keeps it.
        if (res.outcome === 'conflict') {
            state.stale = true;
            pending = null;
        } else if (res.outcome === 'permission' || res.outcome === 'validation') {
            pending = null;
        }
        state.error = res.message;
        state.lastResult = { action: id, outcome: res.outcome, code: res.code, correlationId: res.correlationId };
        return res;
    }

    return { state, load, visible, groups, statuses, request, confirm, cancel, blockedReason, confirmText: (id) => translate('clinic.workflow.confirm.' + id, CONFIRM_TEXT[id] || '') };
}

// Vue template for the panel. Native buttons, labelled controls, live region.
export const WORKFLOW_ACTIONS_TEMPLATE = `
<section class="clinic-workflow-actions v8-page panel stack" aria-labelledby="wf-actions-title">
  <h2 id="wf-actions-title" >Aktionen</h2>
  <div class="actions">
    <span v-if="wf.statuses().asr" class="tag" :style="wf.statuses().asr.style">ASR: {{ wf.statuses().asr.label }}</span>
    <span v-if="wf.statuses().work" class="tag" :style="wf.statuses().work.style">Arbeitsstand: {{ wf.statuses().work.label }}</span>
  </div>
  <div v-for="g in wf.groups()" :key="g.id" role="group" :aria-label="g.label" class="stack">
    <h3 class="hint">{{ g.label }}</h3>
    <div class="actions">
    <button v-for="a in g.actions" :key="a.id" type="button" class="button" style="min-height: 2.75rem"
            :disabled="wf.state.busy" :aria-describedby="a.target ? 'wf-target-' + a.id : null" @click="wf.request(a.id)">
      {{ a.label }}
    </button>
    </div>
    <template v-for="a in g.actions" :key="'t-' + a.id">
      <span v-if="a.target" :id="'wf-target-' + a.id" class="sr-only">Ziel: {{ a.target }}</span>
    </template>
  </div>
  <form v-if="wf.state.confirming" role="alertdialog" aria-labelledby="wf-confirm-text" class="note stack"
        @submit.prevent="wf.confirm()" @keydown.esc="wf.cancel()">
    <p id="wf-confirm-text">{{ wf.confirmText(wf.state.confirming) }}</p>
    <label v-if="wf.state.confirming === 'reject' || wf.state.confirming === 'reopen'" class="field">Begründung
      <textarea v-model="wf.state.reason" rows="3" maxlength="2000" required></textarea>
    </label>
    <template v-if="wf.state.confirming === 'release'">
      <label class="inline-label"><input type="checkbox" v-model="wf.state.confirmedContent"> Inhalt geprüft</label>
      <label class="inline-label"><input type="checkbox" v-model="wf.state.confirmedContext"> Kontext geprüft</label>
    </template>
    <div class="actions">
    <button type="submit" class="button" style="min-height: 2.75rem" :disabled="wf.state.busy">Bestätigen</button>
    <button type="button" class="button" style="min-height: 2.75rem" @click="wf.cancel()">Abbrechen</button>
    </div>
  </form>
  <p role="status" aria-live="polite" class="hint" style="color: var(--text-danger-strong)">{{ wf.state.error }}<span v-if="wf.state.stale"> Bitte neu laden.</span></p>
</section>`;
