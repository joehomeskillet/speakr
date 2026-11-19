/**
 * Bounded encrypted managed offline storage (PAR-REC-008).
 *
 * Only sealed records from offline-crypto-ui.js are written, and only while the
 * server grant is current. The storage adapter is injected by the caller; there
 * is no default and no fallback store, so a missing adapter means "unsaved",
 * never a silent BYOD copy. Clinical plaintext lives in memory only, in the
 * pending map, until a write succeeds. A reload drops the pending map: records
 * that did not reach storage are gone and reported as such.
 *
 * Adapter contract (async): put(key, record), get(key), remove(key), keys(prefix).
 * Quota comes from the server grant, never from a local guess.
 */

import { surfaceFor } from './tokens.js';

export const NAMESPACE = 'clinic-managed-offline';

export class OfflineStoreError extends Error {
    constructor(code) {
        super(code);
        this.name = 'OfflineStoreError';
        this.code = code;
    }
}

const isQuota = (e) => !!e && (e.name === 'QuotaExceededError' || e.code === 22);

export function createOfflineStore({ crypto, storage, who } = {}) {
    if (!crypto || !who || !who.userId || !who.profile) throw new OfflineStoreError('INVALID_CONFIG');
    let quota = 0;
    const pending = new Map(); // key -> { binding, value, code }: plaintext, memory only
    const saved = new Set(); // keys this session wrote or found

    const prefix = `${NAMESPACE}/${who.userId}/${who.profile}/`;
    const keyOf = (b) => `${prefix}${b.dictationId}/${b.sequence}`;
    const bind = (b) => ({ ...b, userId: who.userId, profile: who.profile });
    const size = (key, rec) => key.length + JSON.stringify(rec).length;

    async function purge() {
        if (!storage) return;
        try {
            for (const k of await storage.keys(prefix)) await storage.remove(k);
        } catch (e) { /* nothing left to protect if storage is unreachable */ }
        saved.clear();
    }

    async function usage(except) {
        let total = 0;
        for (const k of await storage.keys(prefix)) {
            if (k === except) continue;
            total += size(k, await storage.get(k));
        }
        return total;
    }

    /** Fresh server check. A revoked grant purges stored records and drops memory. */
    async function authorize() {
        const res = await crypto.refreshAuthorization();
        if (!res.available) {
            pending.clear();
            await purge();
            return { available: false, code: res.code };
        }
        quota = res.quotaBytes;
        return { available: true, quotaBytes: quota };
    }

    function park(key, binding, value, code) {
        pending.set(key, { binding, value, code });
        return { saved: false, code };
    }

    /** Seal and store one record. Failures keep the value in memory as unsaved. */
    async function save(binding, value) {
        const b = bind(binding);
        const key = keyOf(b);
        if (!storage) return park(key, b, value, 'NO_STORAGE');
        let rec;
        try {
            rec = await crypto.encryptRecord(b, value);
        } catch (e) {
            return park(key, b, value, e.code || 'ENCRYPT_FAILED');
        }
        try {
            if (await usage(key) + size(key, rec) > quota) return park(key, b, value, 'QUOTA');
            await storage.put(key, rec);
        } catch (e) {
            return park(key, b, value, isQuota(e) ? 'QUOTA' : 'STORAGE_FAILED');
        }
        pending.delete(key);
        saved.add(key);
        return { saved: true, code: null };
    }

    /** Retry every unsaved record. Returns how many remain unsaved. */
    async function retry() {
        for (const { binding, value } of [...pending.values()]) await save(binding, value);
        return pending.size;
    }

    /** Read one record. A stale or locked key leaves the record in place (recoverable). */
    async function load(binding) {
        const b = bind(binding);
        const key = keyOf(b);
        const rec = storage ? await storage.get(key) : undefined;
        if (!rec) throw new OfflineStoreError(pending.has(key) ? 'UNSAVED' : 'NOT_FOUND');
        return crypto.decryptRecord(b, rec);
    }

    /** After reload or eviction: what is stored, what is unsaved, what disappeared. */
    async function reconcile() {
        const stored = storage ? new Set(await storage.keys(prefix)) : new Set();
        const lost = [...saved].filter((k) => !stored.has(k) && !pending.has(k));
        for (const k of lost) saved.delete(k);
        for (const k of stored) saved.add(k);
        return { stored: stored.size, unsaved: pending.size, lost: lost.length, usedBytes: storage ? await usage() : 0, quotaBytes: quota };
    }

    /** Logout or account switch: lock the key, drop plaintext. Encrypted records stay recoverable. */
    function release() {
        crypto.lock();
        pending.clear();
        saved.clear();
    }

    return {
        authorize, save, retry, load, reconcile, release, purge,
        get unsaved() { return pending.size; },
        get quotaBytes() { return quota; },
    };
}

const MESSAGE = {
    unauthorized: ['error', 'Offline-Speicherung ist nicht freigegeben.'],
    idle: ['normal', 'Keine Offline-Daten.'],
    saved: ['success', 'Verschlüsselt gespeichert.'],
    unsaved: ['warning', 'Nicht gespeichert. Daten nur im Arbeitsspeicher, bitte erneut versuchen.'],
    quota: ['warning', 'Speicherplatz erschöpft. Nicht gespeichert.'],
    lost: ['error', 'Gespeicherte Daten wurden vom Browser entfernt.'],
    recoverable: ['warning', 'Gesperrt. Zum Wiederherstellen entsperren.'],
};

/** Composable: reactive panel state over a store. Uses global Vue when present. */
export function useOfflineStore(store, getBinding) {
    const base = { state: 'idle', stored: 0, unsaved: 0, usedBytes: 0, quotaBytes: 0, busy: false };
    const s = typeof Vue !== 'undefined' && Vue.reactive ? Vue.reactive(base) : base;

    function apply(r) {
        Object.assign(s, { stored: r.stored, unsaved: r.unsaved, usedBytes: r.usedBytes, quotaBytes: r.quotaBytes });
        s.state = r.lost ? 'lost' : r.unsaved ? 'unsaved' : r.stored ? 'saved' : 'idle';
    }

    async function refresh() {
        const res = await store.authorize();
        if (!res.available) {
            Object.assign(s, { state: 'unauthorized', stored: 0, unsaved: 0, usedBytes: 0, quotaBytes: 0 });
            return;
        }
        apply(await store.reconcile());
    }

    async function save(value) {
        s.busy = true;
        try {
            const res = await store.save(getBinding(), value);
            apply(await store.reconcile());
            if (!res.saved && res.code === 'QUOTA') s.state = 'quota';
            if (!res.saved && (res.code === 'LOCKED' || res.code === 'UNAUTHORIZED')) s.state = 'recoverable';
            return res;
        } finally {
            s.busy = false;
        }
    }

    async function retry() {
        s.busy = true;
        try {
            await store.retry();
            apply(await store.reconcile());
        } finally {
            s.busy = false;
        }
    }

    function release() {
        store.release();
        Object.assign(s, { state: 'idle', stored: 0, unsaved: 0, usedBytes: 0 });
    }

    function view() {
        const [status, text] = MESSAGE[s.state];
        return { text, surface: surfaceFor(status), canRetry: s.unsaved > 0 && !s.busy };
    }

    return { s, refresh, save, retry, release, view };
}

/** Vue options object. Status is announced; Enter on the retry button is native. */
export function offlineStorePanel(panel) {
    return {
        template: `
<section class="offline-store" aria-labelledby="offline-store-title">
  <h3 id="offline-store-title">Offline-Speicher</h3>
  <p role="status" aria-live="polite" :style="{ background: view.surface.background, color: view.surface.color, border: '1px solid ' + view.surface.border }">{{ view.text }}</p>
  <p>Gespeichert: {{ p.stored }} · Nicht gespeichert: {{ p.unsaved }} · {{ p.usedBytes }} von {{ p.quotaBytes }} Bytes</p>
  <button type="button" :disabled="!view.canRetry" @click="retry">Erneut versuchen</button>
  <button type="button" @click="release">Sperren und abmelden</button>
</section>`,
        data() { return { p: panel.s }; },
        computed: { view() { return panel.view(); } },
        methods: {
            retry() { return panel.retry(); },
            release() { panel.release(); },
        },
    };
}
