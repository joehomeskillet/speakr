/**
 * Managed offline record encryption (PAR-REC-007).
 *
 * Lifecycle: a random AES-GCM data key per browser profile is wrapped by a
 * key derived from the user's unlock passphrase (PBKDF2-SHA-256). The wrapped
 * blob is not secret and may be persisted by the caller. The unwrapped data key
 * is non-extractable and exists only in memory. Login/session tokens are never
 * read or written here; this module touches no browser storage at all.
 *
 * Every record is sealed with AES-GCM using associated data that binds user,
 * profile, dictation and sequence, so a record cannot be moved to another
 * dictation, position, profile or account. A revoked, replaced or unreachable
 * server grant locks the key, after which nothing can be decrypted.
 */

import { surfaceFor } from './tokens.js';

export const KEY_POLICY = 'encrypted_user_unlock';
export const WRAP_ITERATIONS = 600000;
const CAPABILITY_PATH = '/offline-web/capability';
const FORMAT = 1;

export class OfflineCryptoError extends Error {
    constructor(code) {
        super(code);
        this.name = 'OfflineCryptoError';
        this.code = code;
    }
}

const fail = (code) => new OfflineCryptoError(code);
const enc = new TextEncoder();
const dec = new TextDecoder();

function b64(bytes) {
    let s = '';
    for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
    return btoa(s);
}

function unb64(text) {
    if (typeof text !== 'string') throw fail('INVALID_RECORD');
    try {
        return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
    } catch (e) {
        throw fail('INVALID_RECORD');
    }
}

// Fixed key order keeps the associated data byte-identical on both sides.
function aad(parts) {
    return enc.encode(JSON.stringify(parts));
}

function recordAad(binding, grant) {
    const { userId, profile, dictationId, sequence } = binding || {};
    if ([userId, profile, dictationId].some((v) => typeof v !== 'string' || !v)
            || !Number.isInteger(sequence) || sequence < 0) {
        throw fail('INVALID_BINDING');
    }
    return aad(['rec', FORMAT, userId, profile, dictationId, sequence, grant.accountVersion, grant.version]);
}

export function createOfflineCrypto({ client, subtle, iterations, requireExpiry = false } = {}) {
    const api = subtle || globalThis.crypto.subtle;
    const rounds = iterations || WRAP_ITERATIONS;
    let grant = null; // server facts: { version, accountVersion, browserId }
    let key = null; // in-memory data key, never persisted
    let owner = null; // { userId, profile } the key was unlocked for

    function lock() {
        key = null;
        owner = null;
    }

    function revoke() {
        grant = null;
        lock();
    }

    /** Fresh server check. Anything but an explicit grant revokes and locks. */
    async function refreshAuthorization() {
        const res = await client.request(CAPABILITY_PATH);
        const d = res.outcome === 'ok' ? res.data : null;
        if (!d || d.available !== true || d.key_policy !== KEY_POLICY) {
            revoke();
            return { available: false, code: res.outcome === 'ok' ? 'REVOKED' : res.outcome };
        }
        const expiresAt = res.grantExpiresAt;
        if ((requireExpiry || expiresAt !== undefined)
                && (!Number.isSafeInteger(expiresAt) || expiresAt * 1000 <= Date.now())) {
            revoke();
            return { available: false, code: 'EXPIRED' };
        }
        const next = Object.freeze({ version: d.version, accountVersion: d.account_version, browserId: d.browser_id, expiresAt });
        if (key && grant && (grant.version !== next.version || grant.accountVersion !== next.accountVersion
                || grant.browserId !== next.browserId)) {
            lock(); // stale grant: unlock again against the new version
        }
        grant = next;
        return { available: true, quotaBytes: d.quota_bytes };
    }

    function currentGrant() {
        if (grant?.expiresAt && grant.expiresAt * 1000 <= Date.now()) revoke();
        return grant;
    }

    function requireGrant() {
        const current = currentGrant();
        if (!current) throw fail('UNAUTHORIZED');
        return current;
    }

    async function wrappingKey(passphrase, salt, usage) {
        if (typeof passphrase !== 'string' || passphrase.length < 8) throw fail('WEAK_PASSPHRASE');
        const base = await api.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
        return api.deriveKey(
            { name: 'PBKDF2', hash: 'SHA-256', salt, iterations: rounds },
            base, { name: 'AES-GCM', length: 256 }, false, usage);
    }

    function wrapAad(g, who) {
        return aad(['wrap', FORMAT, who.userId, who.profile, g.accountVersion, g.version, g.browserId]);
    }

    /** First use: create the data key. Returns the wrapped blob for the caller to persist. */
    async function setup(passphrase, who) {
        const g = requireGrant();
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const raw = crypto.getRandomValues(new Uint8Array(32));
        const kek = await wrappingKey(passphrase, salt, ['encrypt']);
        const sealed = await api.encrypt({ name: 'AES-GCM', iv, additionalData: wrapAad(g, who) }, kek, raw);
        const wrapped = {
            format: FORMAT, iterations: rounds, salt: b64(salt), iv: b64(iv), key: b64(sealed),
            version: g.version, accountVersion: g.accountVersion, browserId: g.browserId,
        };
        key = await api.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
        owner = { userId: who.userId, profile: who.profile };
        raw.fill(0);
        return wrapped;
    }

    /** Unwrap a stored data key. Stale version, other account or wrong passphrase fail. */
    async function unlock(passphrase, wrapped, who) {
        const g = requireGrant();
        if (!wrapped || wrapped.version !== g.version || wrapped.accountVersion !== g.accountVersion
                || wrapped.browserId !== g.browserId) {
            throw fail('STALE_VERSION');
        }
        try {
            const kek = await wrappingKey(passphrase, unb64(wrapped.salt), ['decrypt']);
            const raw = await api.decrypt(
                { name: 'AES-GCM', iv: unb64(wrapped.iv), additionalData: wrapAad(g, who) }, kek, unb64(wrapped.key));
            key = await api.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
            owner = { userId: who.userId, profile: who.profile };
        } catch (e) {
            if (e instanceof OfflineCryptoError) throw e;
            throw fail('UNLOCK_FAILED');
        }
    }

    function requireKey(binding) {
        const g = requireGrant();
        if (!key || !owner) throw fail('LOCKED');
        if (!binding || binding.userId !== owner.userId || binding.profile !== owner.profile) throw fail('FOREIGN_ACCOUNT');
        return g;
    }

    async function encryptRecord(binding, value) {
        const g = requireKey(binding);
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const ct = await api.encrypt(
            { name: 'AES-GCM', iv, additionalData: recordAad(binding, g) }, key, enc.encode(JSON.stringify(value)));
        return { format: FORMAT, version: g.version, iv: b64(iv), ct: b64(ct) };
    }

    async function decryptRecord(binding, record) {
        const g = requireKey(binding);
        if (!record || record.format !== FORMAT) throw fail('INVALID_RECORD');
        if (record.version !== g.version) throw fail('STALE_VERSION');
        try {
            const plain = await api.decrypt(
                { name: 'AES-GCM', iv: unb64(record.iv), additionalData: recordAad(binding, g) }, key, unb64(record.ct));
            return JSON.parse(dec.decode(plain));
        } catch (e) {
            if (e instanceof OfflineCryptoError) throw e;
            throw fail('DECRYPT_FAILED');
        }
    }

    return {
        refreshAuthorization, setup, unlock, lock, encryptRecord, decryptRecord,
        get unlocked() { return currentGrant() !== null && key !== null; },
        get authorized() { return currentGrant() !== null; },
        get reference() { return currentGrant(); },
    };
}

const STATE_LABEL = {
    unauthorized: ['error', 'Offline-Speicherung ist nicht freigegeben.'],
    locked: ['warning', 'Gesperrt. Zum Entsperren die Passphrase eingeben.'],
    unlocked: ['success', 'Entsperrt. Daten bleiben nur im Arbeitsspeicher.'],
    failed: ['error', 'Entsperren fehlgeschlagen.'],
};

/**
 * Composable: reactive panel state over a crypto instance. Uses global Vue
 * when present (as the other composables do), a plain object otherwise.
 */
export function useOfflineCrypto(crypto_, who) {
    const base = { state: crypto_.authorized ? 'locked' : 'unauthorized', passphrase: '' };
    const s = typeof Vue !== 'undefined' && Vue.reactive ? Vue.reactive(base) : base;

    function sync(state) {
        s.state = state;
        s.passphrase = '';
    }

    async function refresh() {
        const res = await crypto_.refreshAuthorization();
        sync(!res.available ? 'unauthorized' : crypto_.unlocked ? 'unlocked' : 'locked');
    }

    async function submit(wrapped) {
        if (s.state === 'unauthorized') return null;
        try {
            if (wrapped) {
                await crypto_.unlock(s.passphrase, wrapped, who);
                sync('unlocked');
                return null;
            }
            const created = await crypto_.setup(s.passphrase, who);
            sync('unlocked');
            return created;
        } catch (e) {
            sync('failed');
            return null;
        }
    }

    function lock() {
        crypto_.lock();
        sync(crypto_.authorized ? 'locked' : 'unauthorized');
    }

    function view() {
        const [status, text] = STATE_LABEL[s.state];
        return { text, surface: surfaceFor(status) };
    }

    return { s, refresh, submit, lock, view };
}

/** Vue options object. Enter submits, Escape locks; the status region is announced. */
export function offlineCryptoPanel(panel, getWrapped) {
    return {
        template: `
<section class="offline-crypto" aria-labelledby="offline-crypto-title" @keydown.esc="lock">
  <h3 id="offline-crypto-title">Offline-Verschlüsselung</h3>
  <p role="status" aria-live="polite" :style="{ background: view.surface.background, color: view.surface.color, border: '1px solid ' + view.surface.border }">{{ view.text }}</p>
  <label for="offline-crypto-pass">Passphrase</label>
  <input id="offline-crypto-pass" type="password" autocomplete="off" v-model="p.passphrase" :disabled="p.state === 'unauthorized'" @keydown.enter.prevent="submit">
  <button type="button" :disabled="p.state === 'unauthorized' || p.state === 'unlocked'" @click="submit">Entsperren</button>
  <button type="button" :disabled="p.state !== 'unlocked'" @click="lock">Sperren</button>
</section>`,
        data() { return { p: panel.s }; },
        computed: { view() { return panel.view(); } },
        methods: {
            submit() { return panel.submit(getWrapped ? getWrapped() : null); },
            lock() { panel.lock(); },
        },
    };
}
