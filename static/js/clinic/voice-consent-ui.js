/**
 * Own-user voice enrollment consent and revocation (w160).
 *
 * OWN-SPK-001, R-034. Covers only the signed-in user's own voice: no request
 * names another user, and there are no patient profile controls. Reuses the
 * clinic api client (static/js/clinic/api-client.js) and the status tokens
 * (static/js/clinic/tokens.js). Server state lives only in memory: nothing is
 * written to browser storage, and no embedding payload is ever requested or
 * shown (only the server's embedding count).
 *
 * Routes (w108): GET /voice-consent, POST /voice-consent/grant and /revoke.
 * Writes carry expected_consent_id; a conflict means the view is stale and is
 * answered by a reload. Each action keeps one Idempotency-Key until it
 * succeeds or the view is reloaded, so a retry replays instead of duplicating.
 *
 * German labels go through the injected `t` (existing i18n mechanism, keys
 * `clinic.voiceConsent.*`); the German default applies when a key is missing.
 */

import { surfaceFor } from './tokens.js';

const BASE = '/voice-consent';

export const STATEMENT_VERSION = 1;
export const STATEMENT_TEXT = 'Einwilligung (Version ' + STATEMENT_VERSION + '): '
    + 'Ich willige ein, dass aus meiner eigenen Stimme ein Stimmprofil erstellt wird, '
    + 'damit Sprecherwechsel in meinen Diktaten erkannt und mir als Autorenvorschlag '
    + 'angezeigt werden. Ich kann die Einwilligung jederzeit widerrufen; mit dem Widerruf '
    + 'werden mein Stimmprofil und die Vorschläge entfernt.';

const LABELS = Object.freeze({
    'clinic.voiceConsent.title': 'Eigene Stimme: Einwilligung',
    'clinic.voiceConsent.statement': 'Einwilligungserklärung',
    'clinic.voiceConsent.state.none': 'Keine Einwilligung erteilt',
    'clinic.voiceConsent.state.granted': 'Einwilligung erteilt',
    'clinic.voiceConsent.state.revoked': 'Einwilligung widerrufen',
    'clinic.voiceConsent.grant': 'Einwilligen',
    'clinic.voiceConsent.revoke': 'Einwilligung widerrufen',
    'clinic.voiceConsent.confirm': 'Widerruf bestätigen',
    'clinic.voiceConsent.cancel': 'Abbrechen',
    'clinic.voiceConsent.retry': 'Erneut versuchen',
    'clinic.voiceConsent.reload': 'Neu laden',
    'clinic.voiceConsent.granted': 'Einwilligung gespeichert.',
    'clinic.voiceConsent.revoked': 'Einwilligung widerrufen. Stimmprofil und Vorschläge sind entfernt.',
    'clinic.voiceConsent.stale': 'Der Stand war veraltet und wurde neu geladen. Bitte erneut prüfen.',
});

function makeKey() {
    const c = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'vc-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

/**
 * @param {{client: {request: Function, switchContext: Function},
 *          t?: (key: string) => string, newKey?: () => string}} deps
 */
export function createVoiceConsentController(deps) {
    const client = deps.client;
    const translate = typeof deps.t === 'function' ? deps.t : null;
    const newKey = typeof deps.newKey === 'function' ? deps.newKey : makeKey;
    const listeners = new Set();
    const keys = {}; // action -> { expected, key }

    function label(key) {
        const text = translate ? translate(key) : key;
        return text && text !== key ? text : (LABELS[key] || key);
    }

    const state = {
        loading: false,
        busy: false,
        confirmingRevoke: false,
        error: null, // { outcome, code, message, retryable }
        notice: null,
        consent: { state: 'none', latestId: 0, embeddingCount: 0, grantedAt: null, revokedAt: null, history: [] },
        suggestions: [], // in-memory author suggestions, cleared on revoke
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function apply(data) {
        const d = data || {};
        state.consent = {
            state: ['none', 'granted', 'revoked'].includes(d.state) ? d.state : 'none',
            latestId: Number.isInteger(d.latest_consent_id) ? d.latest_consent_id : 0,
            embeddingCount: Number.isInteger(d.embedding_count) ? d.embedding_count : 0,
            grantedAt: d.granted_at || null,
            revokedAt: d.revoked_at || null,
            history: Array.isArray(d.history) ? d.history.map((r) => ({
                id: r.id, status: r.status, createdAt: r.created_at,
            })) : state.consent.history,
        };
        if (state.consent.state !== 'granted') state.suggestions = [];
    }

    function fail(res) {
        state.error = {
            outcome: res.outcome, code: res.code || null, message: res.message,
            retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
        };
        return false;
    }

    async function load() {
        state.loading = true;
        state.error = null;
        emit();
        const res = await client.request(BASE);
        state.loading = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        for (const k of Object.keys(keys)) delete keys[k];
        apply(res.data);
        emit();
        return true;
    }

    // One command against the server. The result is shown only from the committed response.
    async function command(action, body, doneKey) {
        if (state.busy) return false;
        const expected = state.consent.latestId;
        if (!keys[action] || keys[action].expected !== expected) keys[action] = { expected, key: newKey() };
        state.busy = true;
        state.error = null;
        state.notice = null;
        emit();
        const res = await client.request(BASE + '/' + action, {
            method: 'POST', body, idempotencyKey: keys[action].key,
        });
        state.busy = false;
        if (res.outcome === 'ok') {
            delete keys[action];
            state.confirmingRevoke = false;
            apply(res.data);
            if (!res.data || !Array.isArray(res.data.history)) await refreshHistory();
            state.notice = label(doneKey);
            emit();
            return true;
        }
        fail(res);
        if (res.outcome === 'conflict') {
            // Stale version or state already changed: show the real state.
            await load();
            state.error = null;
            state.notice = label('clinic.voiceConsent.stale');
        }
        emit();
        return false;
    }

    async function refreshHistory() {
        const res = await client.request(BASE);
        if (res.outcome === 'ok') apply(res.data);
    }

    function grant() {
        if (state.consent.state === 'granted') return Promise.resolve(false);
        return command('grant', {
            expected_consent_id: state.consent.latestId, statement_text: STATEMENT_TEXT,
        }, 'clinic.voiceConsent.granted');
    }

    // Revocation needs a second explicit step.
    function requestRevoke() {
        if (state.consent.state !== 'granted') return false;
        state.confirmingRevoke = true;
        emit();
        return true;
    }

    function cancel() {
        state.confirmingRevoke = false;
        state.error = null;
        emit();
    }

    function confirmRevoke() {
        if (!state.confirmingRevoke || state.consent.state !== 'granted') return Promise.resolve(false);
        return command('revoke', { expected_consent_id: state.consent.latestId }, 'clinic.voiceConsent.revoked');
    }

    // Suggestions exist only while consent is granted.
    function setSuggestions(list) {
        if (state.consent.state !== 'granted') {
            state.suggestions = [];
            return false;
        }
        state.suggestions = Array.isArray(list) ? list.slice() : [];
        emit();
        return true;
    }

    // Account change: abort in-flight calls, drop all state, reload.
    function switchContext(next) {
        const n = next || {};
        client.switchContext({ accountId: n.accountId, dictationId: n.dictationId });
        for (const k of Object.keys(keys)) delete keys[k];
        state.consent = { state: 'none', latestId: 0, embeddingCount: 0, grantedAt: null, revokedAt: null, history: [] };
        state.suggestions = [];
        state.confirmingRevoke = false;
        state.notice = null;
        return load();
    }

    function view() {
        const s = state.consent.state;
        return {
            title: label('clinic.voiceConsent.title'),
            statement: { version: STATEMENT_VERSION, text: STATEMENT_TEXT, label: label('clinic.voiceConsent.statement') },
            status: { value: s, label: label('clinic.voiceConsent.state.' + s) },
            statusSurface: surfaceFor(s === 'granted' ? 'success' : (s === 'revoked' ? 'warning' : 'normal')),
            embeddingCount: state.consent.embeddingCount,
            suggestionCount: state.suggestions.length,
            history: state.consent.history,
            canGrant: s !== 'granted' && !state.busy && !state.loading,
            canRevoke: s === 'granted' && !state.busy && !state.loading,
            confirmingRevoke: state.confirmingRevoke,
            busy: state.busy,
            error: state.error,
            errorSurface: state.error ? surfaceFor('error') : null,
            notice: state.notice,
            a11y: {
                role: 'region',
                alert: { role: 'alert', live: 'assertive' },
                status: { role: 'status', live: 'polite' },
                confirm: { role: 'alertdialog', label: label('clinic.voiceConsent.confirm') },
                keys: { activate: ['Enter', ' '], cancel: ['Escape'] },
            },
            labels: {
                grant: label('clinic.voiceConsent.grant'),
                revoke: label('clinic.voiceConsent.revoke'),
                confirm: label('clinic.voiceConsent.confirm'),
                cancel: label('clinic.voiceConsent.cancel'),
                retry: label('clinic.voiceConsent.retry'),
                reload: label('clinic.voiceConsent.reload'),
            },
        };
    }

    // Escape leaves the revoke confirmation.
    function onKeydown(event) {
        if (event && event.key === 'Escape' && state.confirmingRevoke) {
            cancel();
            return true;
        }
        return false;
    }

    return {
        state, view, load, grant, requestRevoke, confirmRevoke, cancel,
        setSuggestions, switchContext, onKeydown,
        subscribe(fn) {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
    };
}

/** Vue composable: reactive snapshot of the controller view (needs window.Vue). */
export function useVoiceConsent(deps) {
    const controller = createVoiceConsentController(deps);
    const Vue = typeof window !== 'undefined' ? window.Vue : null;
    if (!Vue) return { controller, view: null };
    const view = Vue.shallowRef(controller.view());
    controller.subscribe(() => { view.value = controller.view(); });
    return { controller, view };
}
