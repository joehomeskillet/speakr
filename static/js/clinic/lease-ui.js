/**
 * Edit-lease display and lease-loss handling for one clinic dictation.
 *
 * Talks to the lease routes through the clinic api client (acquire, renew,
 * release, takeover). Lease proof and unsaved edits live in memory only. Writes are
 * disabled the moment the lease or its generation is lost; transient edits
 * stay until the user resolves the conflict explicitly. Takeover requires
 * explicit confirmation and server authorization.
 */

import { surfaceFor } from './tokens.js';

export const LEASE_STATUSES = Object.freeze(['idle', 'acquiring', 'held', 'foreign', 'lost', 'error']);

const MESSAGES = Object.freeze({
    idle: 'Nicht zur Bearbeitung geöffnet.',
    acquiring: 'Bearbeitung wird angefordert …',
    foreign: 'Dieses Diktat wird von einer anderen Person bearbeitet.',
    lost: 'Die Bearbeitung wurde entzogen. Das Diktat ist schreibgeschützt.',
    error: 'Die Bearbeitung konnte nicht angefordert werden.',
    renewRetry: 'Verbindung unterbrochen. Erneuerung wird wiederholt.',
    takeoverDenied: 'Übernahme ist nicht erlaubt.',
});

const SURFACE = Object.freeze({
    idle: 'normal', acquiring: 'processing', held: 'success',
    foreign: 'warning', lost: 'error', error: 'error',
});

function defaultKey() {
    return globalThis.crypto.randomUUID();
}

function formatTime(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' });
}

/**
 * @param {object} options
 * @param {{request: Function}} options.client clinic api client
 * @param {string} options.dictationId
 * @param {boolean} [options.mayTakeOver] fallback when lease status omits takeover authority
 * @param {number} [options.heartbeatMs]
 * @param {() => number} [options.now] epoch ms
 */
export function createLeaseController(options) {
    const { client, dictationId } = options;
    const heartbeatMs = options.heartbeatMs || 30000;
    const now = options.now || Date.now;
    const newKey = options.newKey || defaultKey;
    const setTimer = options.setTimer || ((fn, ms) => setTimeout(fn, ms));
    const clearTimer = options.clearTimer || ((id) => clearTimeout(id));
    const base = '/dictations/' + encodeURIComponent(dictationId) + '/leases';
    const listeners = new Set();

    let status = 'idle';
    let proof = null; // { leaseId, generation, expiresAt } - memory only
    let unsaved = null;
    let timer = null;
    let note = '';
    const pendingKeys = { acquire: null, takeover: null };
    let mayTakeOver = options.mayTakeOver === true;
    let inspection = 0;
    let disposed = false;

    function emit() {
        const snap = snapshot();
        for (const fn of listeners) fn(snap);
    }

    function canWrite() {
        return status === 'held' && proof !== null && now() < proof.expiresAt;
    }

    function snapshot() {
        const key = SURFACE[status];
        let message = note || MESSAGES[status] || '';
        if (status === 'held' && proof && !note) {
            message = 'Sie bearbeiten dieses Diktat bis ' + formatTime(proof.expiresAt) + ' Uhr.';
        }
        return {
            status,
            writable: canWrite(),
            holder: status === 'held' ? 'self' : (status === 'foreign' ? 'other' : null),
            message,
            surface: surfaceFor(key),
            generation: proof ? proof.generation : null,
            hasUnsaved: unsaved !== null,
            canTakeOver: status === 'foreign' && mayTakeOver,
            conflict: unsaved !== null && status !== 'held' && status !== 'acquiring',
        };
    }

    function stopTimer() {
        if (timer !== null) clearTimer(timer);
        timer = null;
    }

    function drop(next, text) {
        stopTimer();
        proof = null;
        status = next;
        note = text || '';
        emit();
    }

    function schedule() {
        stopTimer();
        timer = setTimer(renew, heartbeatMs);
    }

    function adopt(data) {
        const expiresAt = Date.parse(data && data.expires_at);
        if (!data || typeof data.lease_id !== 'string' || !Number.isInteger(data.generation) || Number.isNaN(expiresAt)) {
            drop('error');
            return false;
        }
        proof = { leaseId: data.lease_id, generation: data.generation, expiresAt };
        status = 'held';
        note = '';
        schedule();
        emit();
        return true;
    }

    async function inspectLease() {
        const actor = globalThis.document?.getElementById?.('app')?.dataset?.currentUserId;
        if (!actor) return;
        const seen = ++inspection;
        const res = await client.request(base);
        if (disposed || seen !== inspection || res.outcome !== 'ok' || !res.data) return;
        const { held, holder_user_id: holder, may_take_over: allowed } = res.data;
        mayTakeOver = typeof allowed === 'boolean' ? allowed : options.mayTakeOver === true;
        if (held === true && holder != null && String(holder) !== actor) drop('foreign');
        else if ((held === false || String(holder) === actor) && status === 'foreign') drop('idle');
        else emit();
    }

    async function requestLease(operation) {
        if (disposed || status === 'acquiring') return snapshot();
        const was = status;
        inspection += 1;
        status = 'acquiring';
        note = '';
        emit();
        const takeover = operation === 'takeover';
        const opts = { method: 'POST', idempotencyKey: pendingKeys[operation] ||= newKey() };
        if (takeover) opts.body = { confirm: true };
        const res = await client.request(takeover ? base + '/takeover' : base, opts);
        if (disposed) return snapshot();
        if (res.outcome === 'cancelled') {
            status = was;
            emit();
            return snapshot();
        }
        if (res.outcome === 'ok') {
            pendingKeys[operation] = null;
            adopt(res.data);
        } else if (res.status === 423) {
            pendingKeys[operation] = null;
            drop('foreign');
            await inspectLease();
        } else {
            // network/unknown outcome keeps the key so a retry replays instead of duplicating
            if (res.outcome !== 'network' && res.outcome !== 'UNKNOWN') pendingKeys[operation] = null;
            if (takeover && (res.outcome === 'permission' || res.status === 404)) mayTakeOver = false;
            drop(takeover ? 'foreign' : (was === 'lost' ? 'lost' : 'error'), res.message);
            if (!takeover && res.status === 404) await inspectLease();
        }
        return snapshot();
    }

    function acquire() {
        return requestLease('acquire');
    }

    function lose(text) {
        // Edits typed before the loss stay in memory for explicit resolution.
        drop('lost', text);
    }

    async function renew() {
        timer = null;
        if (status !== 'held' || !proof) return;
        if (now() >= proof.expiresAt) return lose();
        const res = await client.request(base + '/renew', {
            method: 'POST',
            idempotencyKey: newKey(),
            body: { lease_id: proof.leaseId, generation: proof.generation },
        });
        if (res.outcome === 'cancelled' || status !== 'held') return;
        if (res.outcome === 'ok') {
            if (res.data && res.data.generation !== proof.generation) return lose();
            if (!adopt(res.data)) return;
        } else if (res.outcome === 'network' || res.outcome === 'UNKNOWN') {
            if (now() >= proof.expiresAt) return lose();
            note = MESSAGES.renewRetry;
            schedule();
            emit();
        } else {
            lose();
        }
    }

    async function release() {
        if (status !== 'held' || !proof) return snapshot();
        const current = proof;
        // Writes stop first; the release call follows.
        drop('idle');
        await client.request(base + '/release', {
            method: 'POST',
            idempotencyKey: newKey(),
            body: { lease_id: current.leaseId, generation: current.generation },
        });
        return snapshot();
    }

    async function takeOver() {
        if (status !== 'foreign' || !mayTakeOver) {
            note = MESSAGES.takeoverDenied;
            emit();
            return snapshot();
        }
        if (typeof globalThis.confirm === 'function'
            && !globalThis.confirm('Die Bearbeitung von der anderen Person übernehmen?')) return snapshot();
        return requestLease('takeover');
    }

    return {
        acquire, release, takeOver, renew,
        canWrite,
        getState: snapshot,
        subscribe(fn) {
            listeners.add(fn);
            if (listeners.size === 1 && status === 'idle' && !disposed) void inspectLease();
            return () => listeners.delete(fn);
        },
        /** Another module saw this generation on the server; a different one means ours is stale. */
        notifyGeneration(generation) {
            if (proof && generation !== proof.generation) lose();
        },
        /** Keep transient edits in memory. Ignored while no write is allowed and nothing is held. */
        setUnsaved(value) {
            if (!canWrite()) return false;
            unsaved = value;
            emit();
            return true;
        },
        /** 'discard' drops the edits; 'reapply' re-acquires and returns them only when the lease is held again. */
        async resolveConflict(action) {
            if (status === 'held' || status === 'acquiring' || unsaved === null) return null;
            if (action === 'discard') {
                unsaved = null;
                if (status === 'lost') drop('idle');
                else emit();
                return null;
            }
            if (action === 'reapply') {
                await acquire();
                if (status !== 'held') return null;
                const value = unsaved;
                unsaved = null;
                emit();
                return value;
            }
            return null;
        },
        dispose() { disposed = true; stopTimer(); listeners.clear(); proof = null; unsaved = null; },
    };
}

/** Buttons the banner offers, with labels and disabled state. */
export function leaseActions(state) {
    const actions = [];
    if (state.status === 'idle' || state.status === 'error') actions.push({ id: 'acquire', label: 'Bearbeiten', disabled: false });
    if (state.status === 'acquiring') actions.push({ id: 'acquire', label: 'Bearbeiten', disabled: true });
    if (state.status === 'held') actions.push({ id: 'release', label: 'Freigeben', disabled: false });
    if (state.canTakeOver) actions.push({ id: 'takeOver', label: 'Übernehmen', disabled: false });
    if (state.conflict) {
        actions.push({ id: 'reapply', label: 'Änderungen erneut anwenden', disabled: false });
        actions.push({ id: 'discard', label: 'Änderungen verwerfen', disabled: false });
    }
    return actions;
}

/** Vue composable. Pass the Vue namespace (window.Vue in the app). */
export function useLease(options, vue) {
    const V = vue || globalThis.Vue;
    const controller = createLeaseController(options);
    const state = V.shallowRef(controller.getState());
    const unsubscribe = controller.subscribe((snap) => { state.value = snap; });
    const run = {
        acquire: controller.acquire,
        release: controller.release,
        takeOver: controller.takeOver,
        discard: () => controller.resolveConflict('discard'),
        reapply: () => controller.resolveConflict('reapply'),
    };
    function dispose() { unsubscribe(); controller.dispose(); }
    if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(dispose);
    return { state, controller, run, dispose };
}

/** Component: status text in a live region plus native buttons (Tab, Enter, Space). */
export function createLeaseStatusComponent(vue) {
    const V = vue || globalThis.Vue;
    return {
        name: 'ClinicLeaseStatus',
        props: { state: { type: Object, required: true }, run: { type: Object, required: true } },
        setup(props) {
            const actions = V.computed(() => leaseActions(props.state));
            return { actions };
        },
        template: `
            <section class="clinic-lease live-inline-status" aria-label="Bearbeitungsstatus"
                :data-status="state.status"
                :style="{ background: state.surface.background, color: state.surface.color, border: '1px solid ' + state.surface.border }">
                <p role="status" aria-live="polite">
                    <strong>{{ state.surface.label }}:</strong> {{ state.message }}
                </p>
                <p v-if="state.conflict" role="alert">
                    Nicht gespeicherte Änderungen sind noch vorhanden. Bitte entscheiden.
                </p>
                <button v-for="a in actions" :key="a.id" type="button" class="small" :disabled="a.disabled"
                    @click="run[a.id]()">{{ a.label }}</button>
            </section>`,
    };
}
