/**
 * Explicit plain/formatted clipboard copy for a frozen transcript revision.
 *
 * The server prepares the flavours (GET) and records only the outcome the
 * browser observed (POST .../outcome). The Clipboard API call runs in this
 * browser tab under the user's click. Nothing is written to web storage, and
 * the clinical text stays in memory until the copy attempt ends.
 * Transfer into foreign native windows is not implemented and stays open.
 */

import { surfaceFor } from './tokens.js';

export const NATIVE_WINDOW_TRANSFER = 'unresolved';

export const FORMATS = Object.freeze(['plain', 'formatted']);

const LABELS = Object.freeze({
    plain: 'Als Text kopieren',
    formatted: 'Mit Formatierung kopieren',
});

const MESSAGES = Object.freeze({
    idle: '',
    working: 'Wird kopiert …',
    copied: 'In die Zwischenablage kopiert.',
    denied: 'Der Browser hat den Zugriff auf die Zwischenablage verweigert.',
    aborted: 'Das Kopieren wurde abgebrochen. Das Ergebnis ist unbekannt.',
    unavailable: 'Dieser Browser unterstützt das Kopieren in dieser Form nicht.',
    unauthorized: 'Keine Berechtigung zum Kopieren.',
    stale: 'Die Version ist veraltet. Bitte neu laden.',
    failed: 'Das Kopieren ist fehlgeschlagen.',
    unreported: 'Kopiert, aber das Ergebnis konnte nicht gemeldet werden.',
});

const SURFACE = Object.freeze({
    idle: 'normal', working: 'processing', copied: 'success', denied: 'error',
    aborted: 'warning', unavailable: 'warning', unauthorized: 'error', stale: 'warning',
    failed: 'error', unreported: 'warning',
});

function newKey() {
    return globalThis.crypto.randomUUID();
}

function clipboardApi(env) {
    if (env.clipboard !== undefined) return env.clipboard;
    return typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
}

function itemClass(env) {
    if (env.ClipboardItem !== undefined) return env.ClipboardItem;
    return typeof ClipboardItem !== 'undefined' ? ClipboardItem : undefined;
}

/** Capability check without side effects. */
export function capabilities(env = {}) {
    const clip = clipboardApi(env);
    const Item = itemClass(env);
    return {
        plain: !!clip && (typeof clip.writeText === 'function' || (typeof clip.write === 'function' && !!Item)),
        formatted: !!clip && typeof clip.write === 'function' && !!Item,
    };
}

/**
 * Framework-neutral controller. `state.value` holds { phase, message, surface }.
 * Live mode binds a releaseId and exposes retryFormat and canRetryReport in state.
 * Options: client (createClinicApiClient), dictationId, revisionId, authorized (bool or fn),
 * live, releaseId, ref (Vue ref factory), clipboard / ClipboardItem / newKey (test seams).
 */
export function createClipboardCopy(options) {
    const opts = options || {};
    const ref = opts.ref || ((value) => ({ value }));
    const live = opts.live === true;
    const base = () => `/dictations/${encodeURIComponent(opts.dictationId)}/clipboard/${encodeURIComponent(opts.revisionId)}`;
    const binding = opts.releaseId ? `release_id=${encodeURIComponent(opts.releaseId)}` : '';
    const query = [binding, live ? 'live=1' : ''].filter(Boolean).join('&');
    const makeKey = opts.newKey || newKey;
    let pending = null;
    let prepareRetry = null;
    let busy = false;
    const state = ref(view('idle'));

    function view(phase) {
        const value = { phase, message: MESSAGES[phase], surface: surfaceFor(SURFACE[phase]), busy: phase === 'working' };
        if (live) {
            value.retryFormat = prepareRetry?.format ?? null;
            value.canRetryReport = pending !== null;
        }
        return value;
    }
    function set(phase) {
        state.value = view(phase);
        return state.value;
    }
    function allowed() {
        return typeof opts.authorized === 'function' ? opts.authorized() !== false : opts.authorized !== false;
    }
    function failureFor(res) {
        if (res.outcome === 'permission') return 'unauthorized';
        if (res.outcome === 'conflict') return 'stale';
        return 'failed';
    }

    async function report(key, outcome) {
        const res = await opts.client.request(`${base()}/outcome${binding ? '?' + binding : ''}`, {
            method: 'POST', body: { outcome }, idempotencyKey: key,
        });
        return res.outcome === 'ok';
    }

    async function liveRequest(path, options) {
        try {
            return await opts.client.request(path, options);
        } catch (error) {
            return { outcome: error?.name === 'AbortError' ? 'cancelled' : 'network' };
        }
    }

    function definitive(res) {
        return ['permission', 'conflict', 'validation'].includes(res.outcome)
            || res.status === 404 || res.status === 413;
    }

    async function reportLive() {
        const { key, outcome, generation, phase } = pending;
        const res = await liveRequest(`${base()}/outcome?${query}`, {
            method: 'POST', body: { outcome, generation }, idempotencyKey: key,
        });
        // The wire constants SUCCEEDED and FAILED are lower-case; UNKNOWN is not terminal.
        const expected = outcome === 'copied' ? 'succeeded' : 'failed';
        if (outcome !== 'aborted' && res.outcome === 'ok'
                && res.data?.status === expected && res.data.generation === generation) {
            pending = null;
            return set(phase === 'copied' && (res.data.current === false || !allowed()) ? 'stale' : phase);
        }
        if (res.outcome === 'permission' || res.status === 404) {
            return set(phase === 'copied' ? 'unreported' : failureFor(res));
        }
        if (definitive(res)) {
            pending = null;
            prepareRetry = null;
            return set(failureFor(res));
        }
        return set(phase === 'copied' ? 'unreported' : phase);
    }

    async function copy(format) {
        if (busy) return state.value;
        if (!FORMATS.includes(format)) return set('failed');
        if (!allowed() || (live && !binding)) return set('unauthorized');
        if (live && ((pending && pending.outcome !== 'aborted')
                || (prepareRetry && prepareRetry.format !== format))) return state.value;
        const env = { clipboard: opts.clipboard, ClipboardItem: opts.ClipboardItem };
        if (!capabilities(env)[format]) return set('unavailable');
        busy = true;
        if (!live) pending = null;
        set('working');
        try {
            const key = prepareRetry?.key ?? makeKey();
            const path = `${base()}?format=${format}${query ? '&' + query : ''}`;
            const args = { idempotencyKey: key };
            const prepared = await (live ? liveRequest(path, args) : opts.client.request(path, args));
            const generation = prepared.data?.generation;
            if (live) {
                if (definitive(prepared)) {
                    prepareRetry = pending = null;
                    return set(failureFor(prepared));
                }
                if (prepared.outcome !== 'ok' || prepared.data?.status !== 'UNKNOWN'
                        || !Number.isSafeInteger(generation) || generation < 1) {
                    prepareRetry = { key, format };
                    return set(prepared.outcome === 'cancelled' ? 'aborted' : 'failed');
                }
                prepareRetry = null;
                if (!allowed() || prepared.data.revision_id !== opts.revisionId
                        || typeof prepared.data['text/plain'] !== 'string'
                        || (format === 'formatted' && typeof prepared.data['text/html'] !== 'string')) {
                    pending = { key, generation, outcome: 'denied', phase: 'stale' };
                    return await reportLive();
                }
            } else if (prepared.outcome !== 'ok') {
                return set(failureFor(prepared));
            } else if (binding && (!allowed() || prepared.data?.revision_id !== opts.revisionId
                    || typeof prepared.data?.['text/plain'] !== 'string')) {
                await report(key, 'denied');
                return set('stale');
            }
            const flavors = prepared.data || {};
            const clip = clipboardApi(env);
            const Item = itemClass(env);
            let outcome = 'copied';
            try {
                if (format === 'plain' && typeof clip.writeText === 'function') {
                    await clip.writeText(String(flavors['text/plain'] ?? ''));
                } else {
                    const data = { 'text/plain': new Blob([String(flavors['text/plain'] ?? '')], { type: 'text/plain' }) };
                    if (format === 'formatted') {
                        data['text/html'] = new Blob([String(flavors['text/html'] ?? '')], { type: 'text/html' });
                    }
                    await clip.write([new Item(data)]);
                }
            } catch (error) {
                outcome = error && error.name === 'AbortError' ? 'aborted' : 'denied';
            }
            if (live) {
                if (outcome === 'aborted') prepareRetry = { key, format };
                pending = { key, generation, outcome, phase: outcome };
                return await reportLive();
            }
            const reported = await report(key, outcome);
            if (!reported) {
                pending = { key, outcome };
                return set(outcome === 'copied' ? 'unreported' : outcome);
            }
            return set(outcome);
        } finally {
            busy = false;
        }
    }

    /** Re-send an outcome the server did not acknowledge, with the same request key. */
    async function retryReport() {
        if (!pending || busy) return state.value;
        if (live && !allowed()) return set('unauthorized');
        busy = true;
        try {
            if (live) {
                set('working');
                return await reportLive();
            }
            if (await report(pending.key, pending.outcome)) {
                const done = pending.outcome;
                pending = null;
                return set(done);
            }
            return state.value;
        } finally {
            busy = false;
        }
    }

    return { state, copy, retryReport, labels: LABELS, native: NATIVE_WINDOW_TRANSFER };
}

/** Vue composable; Vue is read from the page global unless `ref` is given. */
export function useClipboardCopy(options) {
    const vue = typeof window !== 'undefined' ? window.Vue : undefined;
    return createClipboardCopy({ ref: vue && vue.ref, ...options });
}

/** Component with labelled buttons and a polite live region. Style comes from token variables. */
export const ClipboardCopy = {
    name: 'ClipboardCopy',
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        revisionId: { type: String, required: true },
        authorized: { type: Boolean, default: true },
    },
    setup(props) {
        const copier = useClipboardCopy({
            client: props.client,
            dictationId: props.dictationId,
            revisionId: props.revisionId,
            authorized: () => props.authorized,
        });
        const styleOf = (s) => ({ background: s.background, color: s.color, borderColor: s.border });
        return { copier, state: copier.state, labels: LABELS, styleOf };
    },
    template: `
        <div role="group" aria-label="Zwischenablage">
            <button type="button" :disabled="state.busy" @click="copier.copy('plain')">{{ labels.plain }}</button>
            <button type="button" :disabled="state.busy" @click="copier.copy('formatted')">{{ labels.formatted }}</button>
            <button v-if="state.phase === 'unreported'" type="button" @click="copier.retryReport()">Erneut melden</button>
            <p role="status" aria-live="polite" :style="styleOf(state.surface)">{{ state.message }}</p>
        </div>`,
};
