/**
 * Composed clinic review view "Prüfen und Freigeben" (V8 P07; PAR-WF-009, PAR-WF-010,
 * PAR-WF-011, W11).
 *
 * One read-only Vue view over existing building blocks: correction-editor-ui
 * (text, audio revision; loaded only, no lease is ever requested), correction-alignment-ui
 * (current-word mark while the audio plays), acceptance-policy-ui (rule and decision
 * history) and workflow-actions-ui (accept, reject, release, archive commands and
 * their idempotent sending). Nothing from those modules is re-implemented; this
 * module owns only the wiring, the blocker rules and the German view.
 *
 * Rules applied here (the server stays the authority and re-checks everything):
 *   - the view names the text, audio and context revision being reviewed; a decision
 *     is bound to the revisions the reviewer saw (binding from the review state);
 *   - a newer text revision than the reviewed one (autosave, UI-V8-17) or a server
 *     conflict blocks every action and shows "Wiedervorlage" until the state is reloaded;
 *   - open review markers (W11) are listed as blockers and disable "Fertigstellen";
 *   - rejection needs a reason; the form shows the field error and focuses the field;
 *   - Freigeben (acceptance), Fertigstellen (release) and Archivieren are separate
 *     buttons, each with an explicit confirmation naming the dictation and revision;
 *     no shortcut and no icon-only control decides anything;
 *   - without an allowed action (read-only user) or offline no mutation is offered;
 *   - no patient name or title (W16): the view shows the dictation id, the case
 *     binding state and staff names only. Nothing is written to browser storage.
 *
 * Review state (assumed contract of package v8-47, GET dictations/<id>/review):
 *   { etag, revision, status, asr_status, allowed_actions, transitions,
 *     binding: { text_revision_id, audio_revision_id, context_revision_id, acceptance_rule_id, release },
 *     review_markers: [{ id, label? }], author_name, pool_name, corrected_by_name,
 *     context_trusted }.
 *   Missing fields mean "not supplied": nothing is invented, and without
 *   allowed_actions no decision is offered.
 *
 * Exports:
 *   ReviewView  Vue options; props client, dictationId, currentUser, vue;
 *               emits `close`, `correct` (jump to the correction process).
 *   registerReviewView(app)  app.component('clinic-review-view', ...).
 *   createReviewViewController(options)  the controller behind the view.
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';
import { useCorrectionEditor } from './correction-editor-ui.js';
import { useCorrectionAlignment } from './correction-alignment-ui.js';
import { createAcceptancePolicyEditor } from './acceptance-policy-ui.js';
import { ACTIONS, createWorkflowActions } from './workflow-actions-ui.js';

export const COMPOSE_REVIEW_VERSION = 1;

// clinic-v8.css states heading, button and form-control styles with zero-specificity element selectors; the Tailwind
// base layer, loaded after it, resets those. These strings restate them where this page needs them.
const H1 = 'font-size: clamp(26px, 4vw, 30px); line-height: 1.2; font-weight: 700; letter-spacing: -.8px; margin: 0 0 9px';
const H2 = 'font-size: 16px; line-height: 1.35; font-weight: 700; letter-spacing: -.2px; margin: 0 0 14px';
const H3 = 'font-size: 14px; line-height: 1.35; font-weight: 700; margin: 0 0 10px';
const FIELD = 'display: grid; gap: 6px; margin: 0 0 16px; min-width: 0';
const LABEL = 'font-size: 12px; font-weight: 650';
const TEXTAREA = 'width: 100%; min-height: 100px; padding: 10px 11px; border: 1px solid var(--line); border-radius: 7px; background-color: var(--surface); color: var(--v8-text); font: inherit; line-height: 1.65';

export const TEXT = Object.freeze({
    title: 'Prüfen und Freigeben',
    dictation: 'Diktat',
    close: 'Schliessen',
    correct: 'Zur Korrektur',
    reload: 'Aktuellen Stand laden',
    retry: 'Erneut versuchen',
    loading: 'Wird geladen …',
    archived: 'Das Diktat wurde archiviert.',
    openArchive: 'Zum Archiv',
    noReview: 'Für dieses Diktat liegt kein Prüfauftrag vor. Es wird nichts freigegeben.',
    denied: 'Keine Berechtigung für diese Ansicht.',
    failed: 'Der Prüfstand konnte nicht geladen werden.',
    head: 'Prüfstand',
    author: 'Diktiert von',
    pool: 'Schreibkräfte-Pool',
    correctedBy: 'Korrigiert durch',
    required: 'Zusatzabnahme erforderlich',
    acceptance: 'Abnahmestand',
    toAccept: 'Abzunehmen',
    accepted: 'Abgenommen',
    rejected: 'Zurückgewiesen',
    noDecisionYet: 'Noch keine Entscheidung',
    acceptedRevision: 'Abgenommene Revision',
    movedSince: 'Seit der Abnahme hat sich Text, Audio oder Kontext geändert. Der Stand ist wieder abzunehmen.',
    rejectReason: 'Begründung der Zurückweisung',
    notRequired: 'Zusatzabnahme nicht erforderlich',
    decisions: 'Bisherige Entscheidungen',
    noDecisions: 'Noch keine Entscheidung.',
    contextTrusted: 'Vertrauenswürdiger Kontext vorhanden.',
    contextMissing: 'Kein vertrauenswürdiger Kontext. Eine Finalausgabe ist nicht möglich.',
    reviewed: 'Geprüfte Revision',
    textRevision: 'Text',
    audioRevision: 'Audio',
    contextRevision: 'Kontext',
    noValue: '–',
    details: 'Revisionsdetails',
    text: 'Prüftext',
    player: 'Wiedergabe',
    audio: 'Audio zum Diktat',
    wordAria: 'Wort abspielen',
    unaligned: 'ohne Audio',
    manual: 'Kein Transkript vorhanden.',
    blockers: 'Offene Prüfmarken',
    blockersHint: 'Solange Prüfmarken offen sind, kann das Diktat nicht fertiggestellt werden.',
    blockerFallback: 'Prüfmarke',
    wiedervorlage: 'Wiedervorlage',
    newerRevision: 'Es liegt eine neuere Fassung vor als die geprüfte. Die Entscheidung ist gesperrt, bis der aktuelle Stand geladen und erneut geprüft wurde.',
    conflict: 'Der Stand hat sich geändert. Die Entscheidung ist gesperrt, bis der aktuelle Stand geladen wurde.',
    offline: 'Keine Verbindung: Es kann keine Entscheidung ausgeführt werden.',
    beginAcceptance: 'Wählen Sie zuerst „Prüfung beginnen“, um die Fassung abzunehmen.',
    foreignAcceptance: 'Eine andere Person bearbeitet das Diktat. Wählen Sie „Prüfung beginnen“, sobald es frei ist.',
    noReviewLease: 'Für dieses Konto ist der Prüfstart gesperrt.',
    readOnly: 'Nur Lesezugriff: Es stehen keine Entscheidungen zur Verfügung.',
    actions: 'Entscheidung',
    separate: 'Freigeben, Fertigstellen und Archivieren sind getrennte Schritte. Ausgabe und Export erfolgen separat.',
    reason: 'Begründung',
    reasonRequired: 'Bitte eine Begründung angeben.',
    unconfirmed: 'Inhalt und Kontext müssen bestätigt werden.',
    contentChecked: 'Inhalt geprüft',
    contextChecked: 'Kontext geprüft',
    confirm: 'Bestätigen',
    cancel: 'Abbrechen',
    eyebrow: 'Arbeitsliste',
    subline: 'Änderungen nachvollziehen und die korrigierte Textrevision bewusst bestätigen.',
    evidence: 'Revisionsnachweis',
    decisionState: 'Prüfentscheidung',
    changes: 'Text und Quelle',
    textShort: 'Textrevision',
    keys: 'Tastatur: Der Player und der Prüftext sind nacheinander erreichbar. Entscheidungen werden nur über benannte Schaltflächen mit Bestätigung ausgelöst.',
});

/** P07 button labels; the workflow module keeps its own wording for the worklist. */
export const ACTION_LABELS = Object.freeze({
    accept: 'Freigeben',
    reject: 'Zurückweisen',
    release: 'Fertigstellen',
    archive: 'Archivieren',
});

/** Actions this view offers, in order. Reopen and submit belong to other views. */
export const VIEW_ACTIONS = Object.freeze(['accept', 'reject', 'release', 'archive']);

const EFFECT = Object.freeze({
    accept: 'Diese Revision wird freigegeben. Es wird nichts exportiert.',
    reject: 'Das Diktat wird mit Ihrer Begründung zur Korrektur zurückgewiesen.',
    release: 'Die geprüften Versionen und der Kontext werden endgültig fertiggestellt.',
    archive: 'Das Diktat wird archiviert. Es wird nichts gelöscht.',
});

const plainRef = (init) => {
    let value = init;
    return { get value() { return value; }, set value(next) { value = next; } };
};
const PLAIN_VUE = Object.freeze({ ref: plainRef, shallowRef: plainRef, computed: (fn) => ({ get value() { return fn(); } }) });

const isNode = (n) => n !== null && typeof n === 'object';
const str = (v) => (v === undefined || v === null || v === '' ? null : String(v));
const fail = (code, message) => ({ ok: false, code, message });

/** Client handed to children. A plain unmount must not clear the context of the next dictation. */
export function scopedClient(client) {
    if (!client || typeof client.request !== 'function') throw new Error('client is required');
    return {
        request: (path, options) => client.request(path, options),
        command: (body, options) => client.command(body, options),
        switchContext(next) {
            if (next && next.dictationId !== null && next.dictationId !== undefined) client.switchContext(next);
        },
    };
}

/** Maps the review state response to the shape createWorkflowActions().load expects. */
export function toWorkflowRow(dictationId, data) {
    const d = data || {};
    const b = d.binding || {};
    return {
        dictationId,
        etag: d.etag || null,
        revision: d.revision,
        allowedActions: Array.isArray(d.allowed_actions) ? d.allowed_actions : [],
        transitions: d.transitions || {},
        asrStatus: d.asr_status || null,
        workState: d.status || null,
        binding: {
            audioRevisionId: b.audio_revision_id,
            textRevisionId: b.text_revision_id,
            contextRevisionId: b.context_revision_id,
            acceptanceRuleId: b.acceptance_rule_id,
            release: b.release || {},
        },
    };
}

export function createReviewViewController(options) {
    const opts = options || {};
    const scoped = scopedClient(opts.client || createClinicApiClient());
    const user = opts.currentUser || {};
    if (opts.dictationId === undefined || opts.dictationId === null || opts.dictationId === '') throw new Error('dictationId is required');
    if (user.id === undefined || user.id === null) throw new Error('currentUser.id is required');
    const dictationId = String(opts.dictationId);
    const V = opts.vue || globalThis.Vue || null;
    const api = V && V.ref && V.shallowRef && V.computed ? V : { ...PLAIN_VUE, ...(V || {}) };
    const ref = api.ref || plainRef;
    const shallowRef = api.shallowRef || ref;
    const reactive = (V && V.reactive) || ((v) => v);
    const isOnline = opts.isOnline || (() => typeof navigator === 'undefined' || navigator.onLine !== false);

    const path = '/dictations/' + encodeURIComponent(dictationId);
    const status = ref('idle'); // idle | loading | ready | none | denied | error
    const review = ref(null); // last review state as served
    const reviewed = ref(null); // { text, audio, context } revisions the reviewer saw
    const acceptance = ref(null); // GET acceptance-state as served (v8-74), or null when not supplied
    const newer = ref(null); // { revisionId } once a newer text revision or a conflict is known
    const confirming = ref(null);
    const reason = ref('');
    const contentChecked = ref(false);
    const contextChecked = ref(false);
    const error = ref('');
    const reasonInvalid = ref(false);
    const audioEl = shallowRef(null);
    let disposed = false;
    let lastCall = null;

    // The workflow module sends the commands; its client is wrapped to await the in-flight call.
    const track = (p) => { lastCall = p; return p; };
    const workflowClient = {
        request: (p, o) => o?.method === 'POST' ? track(scoped.request(p, o)) : scoped.request(p, o),
        command: (b, o) => track(scoped.command(b, o)),
        switchContext: (n) => scoped.switchContext(n),
    };
    const wf = createWorkflowActions({ client: workflowClient, reactive });
    const policy = createAcceptancePolicyEditor({ client: scoped, reactive });
    const editor = useCorrectionEditor({ client: scoped, dictationId, currentUser: user, vue: V || undefined });
    const seek = (seconds) => { if (audioEl.value) audioEl.value.currentTime = seconds; };
    const alignment = useCorrectionAlignment({ editor, client: scoped, dictationId, seek, vue: V || undefined });

    // --- derived state --------------------------------------------------------
    const markers = () => {
        const r = review.value || {};
        if (!Array.isArray(r.review_markers)) return [];
        return r.review_markers.map((m, i) => ({
            id: m && m.id !== undefined ? String(m.id) : String(i),
            label: (m && m.label) || TEXT.blockerFallback,
        }));
    };
    const isNewer = () => newer.value !== null;

    /** Why an action is currently refused, or null. The server re-checks every decision. */
    function blockedReason(id) {
        if (status.value !== 'ready') return TEXT.loading;
        if (isNewer()) return TEXT.wiedervorlage;
        if (!isOnline()) return TEXT.offline;
        if (id === 'release' && markers().length > 0) return TEXT.blockers;
        if (wf.state.busy) return TEXT.loading;
        if (id === 'accept' && !editor.lease.value.writable) {
            const lease = editor.lease.value;
            if (lease.mayAcquire === false) return TEXT.noReviewLease;
            return lease.status === 'foreign' ? TEXT.foreignAcceptance : TEXT.beginAcceptance;
        }
        return wf.blockedReason(id);
    }

    function actions() {
        return wf.visible()
            .filter((a) => VIEW_ACTIONS.includes(a.id))
            .sort((a, b) => VIEW_ACTIONS.indexOf(a.id) - VIEW_ACTIONS.indexOf(b.id))
            .map((a) => {
                const why = blockedReason(a.id);
                return { id: a.id, label: ACTION_LABELS[a.id], group: a.group, target: a.target, disabled: why !== null, why };
            });
    }

    /**
     * Acceptance panel model from GET acceptance-state: { state: offen | angenommen | zurueckgegeben |
     * wiedervorlage, label, current, reject_reason? }. "Required" comes from the acceptance policy.
     * The accepted revision is only named when the server says the accepted stand is the current one.
     */
    function acceptanceView() {
        const a = acceptance.value;
        if (!a || typeof a.state !== 'string') return null;
        const required = Boolean(policy.state.required);
        const cur = isNode(a.current) ? a.current : {};
        const view = { state: a.state, required, label: str(a.label), tone: 'info', acceptedRevision: null, moved: false, reason: null };
        if (a.state === 'angenommen') {
            view.label = TEXT.accepted;
            view.tone = 'success';
            view.acceptedRevision = str(cur.text_revision_id);
        } else if (a.state === 'wiedervorlage') {
            view.label = TEXT.toAccept;
            view.tone = 'warn';
            view.moved = true;
        } else if (a.state === 'zurueckgegeben') {
            view.label = TEXT.rejected;
            view.tone = 'danger';
            view.reason = str(a.reject_reason);
        } else if (a.state === 'offen') {
            view.label = required ? TEXT.toAccept : TEXT.noDecisionYet;
            view.tone = required ? 'warn' : 'info';
        }
        return view;
    }

    const readOnly = () => status.value === 'ready' && actions().length === 0;

    function revisionSummary() {
        const r = reviewed.value;
        return r ? { text: r.text, audio: r.audio, context: r.context } : { text: null, audio: null, context: null };
    }

    function confirmText(id) {
        const rev = revisionSummary().text;
        return TEXT.dictation + ' ' + dictationId + '.' + (rev ? ' Textrevision ' + rev + '.' : '') + ' ' + EFFECT[id];
    }

    // --- decisions: cancel first, it is used by load ---------------------------------
    function cancel() {
        confirming.value = null;
        reason.value = '';
        contentChecked.value = false;
        contextChecked.value = false;
        reasonInvalid.value = false;
        wf.cancel();
    }

    // --- loading -----------------------------------------------------------------
    async function load() {
        if (disposed) return fail('cancelled', '');
        status.value = 'loading';
        error.value = '';
        cancel();
        const [res, acc] = await Promise.all([
            scoped.request(path + '/review'),
            scoped.request(path + '/acceptance-state'),
            editor.load({ discard: true }),
        ]);
        if (disposed) return fail('cancelled', '');
        acceptance.value = acc && acc.outcome === 'ok' && isNode(acc.data) ? acc.data : null;
        // The acceptance state uses the same view right; a refusal there is the neutral denied state.
        if (acc && acc.outcome === 'permission') res.outcome = 'permission';
        if (!res || res.outcome !== 'ok') {
            const denied = Boolean(res) && res.outcome === 'permission';
            const none = Boolean(res) && res.status === 404;
            status.value = denied ? 'denied' : (none ? 'none' : 'error');
            review.value = null;
            reviewed.value = null;
            acceptance.value = null;
            wf.load(null);
            return fail(denied ? 'denied' : (none ? 'none' : 'failed'), denied ? TEXT.denied : (none ? TEXT.noReview : TEXT.failed));
        }
        const data = res.data || {};
        review.value = data;
        const b = data.binding || {};
        const head = str(editor.textRevisionId.value);
        const bound = str(b.text_revision_id);
        reviewed.value = {
            text: bound || head,
            audio: str(b.audio_revision_id) || str(editor.audio.value.revisionId),
            context: str(b.context_revision_id),
        };
        // The review binds the revision the server names; a different text head is a newer revision.
        newer.value = bound && head && bound !== head ? { revisionId: head } : null;
        wf.load(toWorkflowRow(dictationId, data));
        await Promise.all([alignment.refresh(), policy.load(dictationId)]);
        if (disposed) return fail('cancelled', '');
        status.value = 'ready';
        return { ok: true };
    }

    /** Re-reads the current text revision. A different one than reviewed is a Wiedervorlage. */
    async function checkNewer() {
        if (disposed || !reviewed.value) return isNewer();
        const res = await scoped.request(path + '/correction');
        if (disposed || !res || res.outcome !== 'ok' || !res.data) return isNewer();
        const head = str(res.data.text_revision_id);
        if (head && reviewed.value.text && head !== reviewed.value.text) newer.value = { revisionId: head };
        return isNewer();
    }

    // --- decisions -----------------------------------------------------------------
    /** Opens the confirmation. Every action, including Freigeben, needs it. */
    async function request(id) {
        if (disposed || !VIEW_ACTIONS.includes(id)) return fail('unknown', '');
        if (!wf.state.allowed.includes(id)) return fail('permission', TEXT.denied);
        const early = blockedReason(id);
        if (early) return fail('blocked', early);
        if (await checkNewer()) return fail('newer', TEXT.newerRevision);
        confirming.value = id;
        error.value = '';
        reasonInvalid.value = false;
        return { ok: true };
    }

    async function confirm() {
        const id = confirming.value;
        if (!id) return fail('none', '');
        const early = blockedReason(id);
        if (early) { error.value = early; return fail('blocked', early); }
        if (id === 'reject' && !reason.value.trim()) {
            error.value = TEXT.reasonRequired;
            reasonInvalid.value = true;
            if (typeof opts.focusReason === 'function') opts.focusReason();
            return fail('reason', TEXT.reasonRequired);
        }
        if (id === 'release' && !(contentChecked.value && contextChecked.value)) {
            error.value = TEXT.unconfirmed;
            return fail('unconfirmed', TEXT.unconfirmed);
        }
        // A save that landed after the page was opened blocks the decision before anything is sent.
        if (await checkNewer()) { error.value = TEXT.newerRevision; return fail('newer', TEXT.newerRevision); }
        if (disposed) return fail('cancelled', '');
        lastCall = null;
        let confirmed = null;
        if (ACTIONS[id].confirm) {
            wf.request(id);
            wf.state.reason = reason.value;
            wf.state.confirmedContent = contentChecked.value;
            wf.state.confirmedContext = contextChecked.value;
            confirmed = await wf.confirm();
        } else {
            wf.request(id);
        }
        const res = ACTIONS[id].confirm ? confirmed : (lastCall ? await lastCall : null);
        await Promise.resolve();
        await Promise.resolve();
        if (disposed) return fail('cancelled', '');
        if (!res) {
            error.value = wf.state.error || '';
            return fail('not_sent', error.value);
        }
        if (res.outcome === 'ok') {
            const done = { ok: true, action: id, data: res.data };
            confirming.value = null;
            reason.value = '';
            if (id === 'archive' && res.data?.applied === true && res.data.status === 'zu_archivieren') {
                wf.load(null);
                status.value = 'archived';
                if (editor.lease.value.writable) await editor.release();
            } else {
                await load();
            }
            return done;
        }
        const conflict = wf.state.stale || res.outcome === 'conflict';
        if (conflict && !newer.value) newer.value = { revisionId: null };
        error.value = conflict ? TEXT.conflict : (res.message || TEXT.failed);
        return fail(conflict ? 'conflict' : (res.outcome || 'failed'), error.value);
    }

    // --- player ---------------------------------------------------------------------
    const audioListeners = [];
    function unbindAudio() {
        for (const [n, name, fn] of audioListeners.splice(0)) if (typeof n.removeEventListener === 'function') n.removeEventListener(name, fn);
    }
    function bindAudio(node) {
        const el = isNode(node) ? node : null;
        if (disposed || el === audioEl.value) return;
        unbindAudio();
        audioEl.value = el;
        if (el && typeof el.addEventListener === 'function') {
            const fn = () => { alignment.highlightAt(Number.isFinite(el.currentTime) ? el.currentTime : 0); };
            el.addEventListener('timeupdate', fn);
            audioListeners.push([el, 'timeupdate', fn]);
        }
    }

    const start = () => load();

    function close() {
        if (disposed) return false;
        if (typeof opts.onClose === 'function') opts.onClose({ dictationId });
        return true;
    }
    function correct() {
        if (disposed) return false;
        if (typeof opts.onCorrect === 'function') opts.onCorrect({ dictationId });
        return true;
    }

    /** Stops requests and listeners and clears all clinical data held in memory. */
    function dispose() {
        if (disposed) return;
        disposed = true;
        unbindAudio();
        audioEl.value = null;
        alignment.dispose();
        if (editor.lease.value.writable) void editor.release();
        editor.dispose();
        wf.cancel();
        wf.load(null);
        policy.reset();
        review.value = null;
        reviewed.value = null;
        acceptance.value = null;
        editor.text.value = '';
        reason.value = '';
        status.value = 'idle';
    }

    return {
        vue: V, api, editor, alignment, wf, policy,
        status, review, reviewed, acceptance, newer, confirming, reason, contentChecked, contextChecked, error, reasonInvalid, audioEl,
        dictationId, markers, isNewer, blockedReason, actions, acceptanceView, readOnly, revisionSummary, confirmText,
        start, load, checkNewer, request, confirm, cancel, bindAudio, close, correct, dispose,
        client: scoped, text: TEXT,
    };
}

const FOCUS = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
const PRIMARY = 'background: var(--ink); border-color: var(--ink); color: white';
const box = (name) => {
    const s = surfaceFor(name);
    return { background: s.background, color: s.color, border: '1px solid ' + s.border };
};

const BODY_PROPS = {
    client: { type: Object, default: null },
    dictationId: { type: String, required: true },
    currentUser: { type: Object, required: true },
    vue: { type: Object, default: null },
};

const ReviewBody = {
    name: 'ClinicReviewBody',
    props: BODY_PROPS,
    emits: ['close', 'correct'],
    setup(props, ctx) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the review view');
        const emit = (name) => () => { if (ctx && typeof ctx.emit === 'function') ctx.emit(name); };
        let reasonNode = null;
        const c = createReviewViewController({
            client: props.client || undefined,
            dictationId: props.dictationId,
            currentUser: props.currentUser,
            vue: props.vue || undefined,
            onClose: emit('close'),
            onCorrect: emit('correct'),
            focusReason: () => { if (reasonNode && typeof reasonNode.focus === 'function') reasonNode.focus(); },
        });
        if (typeof V.onMounted === 'function') V.onMounted(() => { c.start(); });
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        const a = c.alignment;
        const styleFor = (seg) => {
            const word = a.currentWord.value;
            if (word && seg.linkId === word.id) return box('processing');
            if (seg.flagged) { const w = surfaceFor('warning'); return { background: w.background, color: w.color }; }
            return {};
        };
        return {
            c, FOCUS, short: (v) => (typeof v === 'string' && v.length > 13 ? v.slice(0, 8) + '…' : v), box, styleFor, TEXT, wf: c.wf,
            toneBox: (tone) => box({ success: 'success', warn: 'warning', danger: 'error', info: 'pending' }[tone] || 'normal'),
            status: c.status, review: c.review, newer: c.newer, confirming: c.confirming, reason: c.reason, error: c.error,
            reasonInvalid: c.reasonInvalid, contentChecked: c.contentChecked, contextChecked: c.contextChecked,
            audio: c.editor.audio, manual: c.editor.manual, lease: c.editor.lease,
            segments: a.segments, currentWord: a.currentWord,
            setReasonNode: (n) => { reasonNode = n; },
        };
    },
    template: `
<section class="clinic-review-view v8-page" data-testid="clinic-review-view" aria-labelledby="crv-title" :aria-busy="status === 'loading' ? 'true' : 'false'">
  <nav class="breadcrumb" aria-label="Pfad"><span>Speakr</span><span aria-hidden="true">›</span><span>{{ TEXT.eyebrow }}</span><span aria-hidden="true">›</span><span aria-current="page">{{ TEXT.title }}</span></nav>
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <h1 id="crv-title" style="${H1}">{{ TEXT.title }}</h1>
      <p class="subline">{{ TEXT.subline }}</p>
    </div>
    <div class="head-actions">
      <span class="tag neutral" data-testid="dictation-id">{{ TEXT.dictation }} {{ c.dictationId }}</span>
      <span v-if="status === 'ready'" class="tag burgundy" :title="c.revisionSummary().text">{{ TEXT.textShort }} {{ short(c.revisionSummary().text) || TEXT.noValue }}</span>
      <button type="button" class="button" :style="FOCUS" data-testid="correct" @click="c.correct()">{{ TEXT.correct }}</button>
      <button type="button" class="button quiet" :style="FOCUS" data-testid="close" @click="c.close()">{{ TEXT.close }}</button>
    </div>
  </div>
  <p class="visually-hidden" id="crv-keys">{{ TEXT.keys }}</p>
  <div v-if="status === 'loading'" class="state-box" role="status" aria-live="polite" data-testid="loading-state">{{ TEXT.loading }}</div>
  <div v-if="status === 'archived'" class="state-box">
    <p role="status">{{ TEXT.archived }}</p>
    <a class="button" href="/#/archiv">{{ TEXT.openArchive }}</a>
  </div>
  <p v-if="status === 'none'" class="empty" role="note" data-testid="no-review">{{ TEXT.noReview }}</p>
  <p v-if="status === 'denied'" role="alert" class="note warning" data-testid="denied">{{ TEXT.denied }}</p>
  <div v-if="status === 'error'" role="alert" class="note burgundy" data-testid="load-error">
    {{ TEXT.failed }}
    <button type="button" class="button small" style="margin-left: 8px" :style="FOCUS" @click="c.load()">{{ TEXT.retry }}</button>
  </div>

  <template v-if="status === 'ready'">
    <div v-if="newer" role="alert" class="note warning" style="margin: 0 0 16px" data-testid="wiedervorlage">
      <strong>{{ TEXT.wiedervorlage }}</strong> {{ TEXT.newerRevision }}
      <button type="button" class="button small" style="margin-left: 8px" :style="FOCUS" data-testid="reload" @click="c.load()">{{ TEXT.reload }}</button>
    </div>

    <dl class="metadata" :aria-label="TEXT.head" data-testid="head">
      <div v-if="review.author_name"><dt>{{ TEXT.author }}</dt><dd>{{ review.author_name }}</dd></div>
      <div v-if="review.pool_name"><dt>{{ TEXT.pool }}</dt><dd>{{ review.pool_name }}</dd></div>
      <div v-if="review.corrected_by_name"><dt>{{ TEXT.correctedBy }}</dt><dd>{{ review.corrected_by_name }}</dd></div>
      <div><dt>{{ TEXT.reviewed }}</dt><dd data-testid="reviewed">{{ TEXT.textRevision }} {{ c.revisionSummary().text }}</dd></div>
    </dl>

    <section v-if="c.markers().length" class="note warning" style="margin: 0 0 16px" :aria-label="TEXT.blockers" data-testid="blockers">
      <h3 style="${H3}">{{ TEXT.blockers }} ({{ c.markers().length }})</h3>
      <p>{{ TEXT.blockersHint }}</p>
      <ul role="list" style="margin: 0; padding-left: 18px; list-style: disc"><li v-for="b in c.markers()" :key="b.id">{{ b.label }}</li></ul>
    </section>

    <div class="grid">
      <section class="panel" :aria-label="TEXT.changes">
        <h2 style="${H2}">{{ TEXT.text }}</h2>
        <div :aria-label="TEXT.text" data-testid="review-text">
          <p v-if="manual" class="hint" role="note">{{ TEXT.manual }}</p>
          <p class="revision" role="group" :aria-label="TEXT.text" style="white-space: pre-wrap; margin: 0 0 18px">
            <template v-for="seg in segments" :key="seg.start">
              <span v-if="seg.aligned" role="button" tabindex="0" :data-start="seg.start" :aria-label="TEXT.wordAria + ': ' + seg.text"
                    :aria-current="currentWord && currentWord.id === seg.linkId ? 'true' : null" :style="{ ...FOCUS, ...styleFor(seg) }"
                    @click="c.alignment.seekToSegment(seg)" @keydown.enter.prevent="c.alignment.seekToSegment(seg)"
                    @keydown.space.prevent="c.alignment.seekToSegment(seg)">{{ seg.text }}</span>
              <span v-else :data-start="seg.start" :data-unaligned="seg.flagged || seg.unavailable ? 'true' : null"
                    :title="seg.flagged ? TEXT.unaligned : null" :style="styleFor(seg)">{{ seg.text }}</span>
            </template>
          </p>
        </div>
        <div class="player compact-player" style="flex-wrap: wrap; margin-bottom: 0" :aria-label="TEXT.player" data-testid="player">
          <audio controls preload="metadata" style="flex: 1 1 260px; min-width: 0; width: 100%" :ref="c.bindAudio" :src="audio.status === 'ready' ? audio.src : null" :aria-label="TEXT.audio"></audio>
          <p v-if="audio.status !== 'ready' && audio.status !== 'idle' && audio.status !== 'loading'" class="hint" style="flex-basis: 100%; margin: 0" role="note">{{ audio.message }}</p>
        </div>
      </section>

      <div class="stack">
        <section class="panel" :aria-label="TEXT.actions" data-testid="actions">
          <h2 style="${H2}">{{ TEXT.decisionState }}</h2>
          <div v-if="c.acceptanceView()" :aria-label="TEXT.acceptance" data-testid="acceptance">
            <p class="text-status" style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px">
              <span>{{ TEXT.acceptance }}</span>
              <span class="tag" :style="toneBox(c.acceptanceView().tone)" data-testid="acceptance-state" :data-state="c.acceptanceView().state">{{ c.acceptanceView().label }}</span>
            </p>
            <p class="hint" data-testid="acceptance-required">{{ c.acceptanceView().required ? TEXT.required : TEXT.notRequired }}</p>
            <p v-if="c.acceptanceView().acceptedRevision" class="hint" data-testid="acceptance-revision">{{ TEXT.acceptedRevision }}: {{ TEXT.textRevision }} {{ c.acceptanceView().acceptedRevision }}</p>
            <p v-if="c.acceptanceView().moved" class="note warning" style="margin-top: 0" data-testid="acceptance-moved">{{ TEXT.movedSince }}</p>
            <p v-if="c.acceptanceView().reason" class="hint" data-testid="acceptance-reason"><strong>{{ TEXT.rejectReason }}:</strong> {{ c.acceptanceView().reason }}</p>
          </div>
          <div v-if="c.actions().some(a => ['accept', 'reject', 'release'].includes(a.id)) && lease.mayAcquire === true"
               class="actions text-sm" aria-label="Bearbeitungssperre">
            <button v-if="['idle', 'error', 'lost'].includes(lease.status)" type="button" class="button secondary"
                    :style="FOCUS" @click="c.editor.acquire()" data-testid="begin-review">Prüfung beginnen</button>
            <span role="status" aria-live="polite">{{ lease.message }}</span>
          </div>
          <p v-if="c.readOnly()" class="note" role="note" data-testid="read-only">{{ TEXT.readOnly }}</p>
          <div class="actions text-sm" style="margin-top: 14px">
            <button v-for="a in c.actions()" :key="a.id" type="button" class="button" style="min-height: 2.75rem"
                    :style="[FOCUS, a.id === 'accept' ? '${PRIMARY}' : null]" :disabled="a.disabled" :data-action="a.id" :aria-describedby="a.why ? 'crv-why-' + a.id : null"
                    @click="c.request(a.id)">{{ a.label }}</button>
          </div>
          <template v-for="a in c.actions()" :key="'w' + a.id"><span v-if="a.why" :id="'crv-why-' + a.id" :class="a.id === 'accept' ? 'hint' : 'visually-hidden'">{{ a.why }}</span></template>

          <form v-if="confirming" role="alertdialog" aria-labelledby="crv-confirm" class="state-box" style="margin-top: 16px"
                data-testid="confirm-form" @submit.prevent="c.confirm()" @keydown.esc="c.cancel()">
            <p id="crv-confirm">{{ c.confirmText(confirming) }}</p>
            <div v-if="confirming === 'reject'" style="${FIELD}">
              <label style="${LABEL}" for="crv-reason">{{ TEXT.reason }}</label>
              <textarea style="${TEXTAREA}" id="crv-reason" :ref="setReasonNode" v-model="reason" maxlength="2000"
                        :aria-invalid="reasonInvalid ? 'true' : 'false'" aria-describedby="crv-error" data-testid="reason"></textarea>
            </div>
            <div v-if="confirming === 'release'" class="checkboxes">
              <label class="inline-label"><input type="checkbox" v-model="contentChecked"> {{ TEXT.contentChecked }}</label>
              <label class="inline-label"><input type="checkbox" v-model="contextChecked"> {{ TEXT.contextChecked }}</label>
            </div>
            <div class="actions text-sm">
              <button type="submit" class="button" style="min-height: 2.75rem; ${PRIMARY}" :style="FOCUS" :disabled="wf.state.busy" data-testid="confirm">{{ TEXT.confirm }}</button>
              <button type="button" class="button" style="min-height: 2.75rem" :style="FOCUS" data-testid="cancel" @click="c.cancel()">{{ TEXT.cancel }}</button>
            </div>
          </form>
          <p id="crv-error" role="alert" class="text-status error" style="margin-top: 12px" data-testid="error">{{ error }}</p>
          <div class="note" role="note">{{ TEXT.separate }}</div>
        </section>

        <section class="panel" :aria-label="TEXT.evidence">
          <h2 style="${H2}">{{ TEXT.evidence }}</h2>
          <dl class="keyvalues" data-testid="revisions">
            <div><dt>{{ TEXT.textRevision }}</dt><dd>{{ c.revisionSummary().text || TEXT.noValue }}</dd></div>
            <div><dt>{{ TEXT.audioRevision }}</dt><dd>{{ c.revisionSummary().audio || TEXT.noValue }}</dd></div>
            <div><dt>{{ TEXT.contextRevision }}</dt><dd>{{ c.revisionSummary().context || TEXT.noValue }}</dd></div>
            <div><dt>{{ TEXT.acceptance }}</dt><dd data-testid="rule">{{ c.policy.state.required ? TEXT.required : TEXT.notRequired }}</dd></div>
          </dl>
          <p class="hint" style="margin-top: 12px">{{ review.context_trusted ? TEXT.contextTrusted : TEXT.contextMissing }}</p>
          <h3 style="${H3}; margin-top: 16px">{{ TEXT.decisions }}</h3>
          <ul v-if="c.policy.state.decisions.length" class="timeline" role="list">
            <li v-for="(d, i) in c.policy.state.decisions" :key="i"><strong>{{ d.label }}</strong><p>{{ d.actorName }}<span v-if="d.reason"> · {{ d.reason }}</span></p></li>
          </ul>
          <p v-else class="hint">{{ TEXT.noDecisions }}</p>
        </section>
      </div>
    </div>
  </template>
</section>`,
};

export const ReviewView = {
    name: 'ClinicReviewView',
    components: { ReviewBody },
    props: BODY_PROPS,
    emits: ['close', 'correct'],
    template: `<ReviewBody :key="dictationId" :client="client" :dictation-id="dictationId" :current-user="currentUser"
        :vue="vue" @close="$emit('close')" @correct="$emit('correct')" />`,
};

export function registerReviewView(app) {
    app.component('clinic-review-view', ReviewView);
    return app;
}
