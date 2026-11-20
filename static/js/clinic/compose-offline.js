/**
 * Composed clinic offline and recovery view (PAR-REC-007, PAR-LIVE-003, PAR-LIVE-004).
 *
 * One Vue view over offline-crypto-ui (grant, unlock, record encryption) and
 * offline-store-ui (bounded encrypted storage). Nothing from those modules is
 * re-implemented; the view owns the wiring and the German status text.
 *
 * Default deny: without a server grant for the managed offline profile, without
 * a storage adapter or in any other profile nothing is stored, the draft list is
 * empty and the view says so. The storage adapter is injected; there is no
 * default store, so no clinical data reaches browser storage on its own. Drafts
 * are sealed by offline-crypto before they are written. Logout purges every
 * stored draft and locks the key.
 *
 * Neither the view nor its messages carry draft content: lists show the
 * dictation id and the sequence only, errors surface a safe code, nothing is
 * logged. A restored draft is handed to the caller (onRestore) and not kept here.
 *
 * Exports:
 *   OfflineView  Vue options; props client, who, storage, getWrapped?, crypto?,
 *                store?, connection?, vue?, onRestore?, onLoggedOut?, onWrapped?
 *                (receives the wrapped key blob after first-time setup; the caller persists it).
 *   registerOfflineView(app)  app.component('clinic-offline-view', ...).
 *   createOfflineViewController(options)  the controller behind the view.
 */

import { createClinicApiClient } from './api-client.js';
import { createOfflineCrypto } from './offline-crypto-ui.js';
import { NAMESPACE, createOfflineStore } from './offline-store-ui.js';

export const COMPOSE_OFFLINE_VERSION = 1;
export const MANAGED_PROFILE = 'managed_web_offline';

export const PROFILE_LABELS = Object.freeze({
    managed_web_online: 'Verwaltetes Web, nur online',
    byod_internal: 'Eigenes Gerät, nur online',
    kis_webview: 'KIS-Ansicht, nur online',
    managed_web_offline: 'Verwaltetes Web, Offline freigegeben',
});

export const TEXT = Object.freeze({
    title: 'Offline und Wiederherstellung',
    eyebrow: 'Betrieb',
    subline: 'Lokale Entwürfe, Sperrzustand und Verbindung sichtbar behandeln. Es gelten nur freigegebene Profile.',
    draftsAndRecovery: 'Entwürfe und Wiederanlauf',
    connectionAndCheck: 'Verbindung und Prüfung',
    nextStep: 'Aktion',
    unlockSection: 'Entsperren',
    profile: 'Profil',
    connection: 'Verbindung',
    online: 'Online',
    offline: 'Offline. Serverpflichtige Aktionen sind nicht möglich.',
    lastContact: 'Letzter bestätigter Serverkontakt',
    noContact: 'noch keiner in dieser Sitzung',
    checking: 'Freigabe wird geprüft …',
    denied: 'Offline-Speicherung ist nicht freigegeben. Es wird nichts lokal gespeichert und es gibt keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar.',
    noStorage: 'Kein lokaler Speicher bereitgestellt. Es wird nichts gespeichert.',
    drafts: 'Lokale Entwürfe',
    draftsUnavailable: 'Lokale Entwürfe sind in diesem Zustand nicht verfügbar.',
    noDrafts: 'Keine lokalen Entwürfe gefunden. Das sagt nichts über Daten auf dem Server.',
    draft: 'Entwurf',
    sequence: 'Sequenz',
    locked: 'Gesperrt. Zum Wiederherstellen entsperren.',
    unlocked: 'Entsperrt. Entwürfe können wiederhergestellt werden.',
    passphrase: 'Passphrase',
    unlock: 'Entsperren',
    lock: 'Sperren',
    restore: 'Wiederherstellen',
    discard: 'Verwerfen',
    discardConfirm: 'Endgültig verwerfen',
    cancel: 'Abbrechen',
    refresh: 'Neu prüfen',
    logout: 'Abmelden und lokale Entwürfe löschen',
    loggedOut: 'Abgemeldet. Alle lokalen Entwürfe wurden gelöscht.',
    restored: 'Entwurf wiederhergestellt.',
    discarded: 'Entwurf lokal gelöscht. Der Serverstand bleibt unverändert.',
    unsaved: 'Nicht lokal gespeichert: nur im Arbeitsspeicher.',
    unreachable: 'Server nicht erreichbar oder Sitzung abgelaufen. Lokale Entwürfe bleiben erhalten. Später neu prüfen oder neu anmelden.',
    used: 'Belegt',
    of: 'von',
    bytes: 'Bytes',
    lost: 'Der Browser hat gespeicherte Entwürfe entfernt. Bereits bestätigte Serverteile bleiben erreichbar.',
    deviceChanged: 'Gerät, Browser oder Freigabe haben gewechselt. Dieser Schlüssel passt nicht mehr, die Entwürfe bleiben gesperrt.',
    discardWarn: 'Nur die lokale Kopie wird gelöscht. Auf dem Server bestätigte Daten bleiben erhalten.',
    unlockFailed: 'Entsperren fehlgeschlagen.',
    restoreFailed: 'Wiederherstellung nicht möglich.',
    keys: 'Tastatur: Eingabetaste im Passphrase-Feld entsperrt.',
});

const ERROR_TEXT = Object.freeze({
    STALE_VERSION: TEXT.deviceChanged,
    LOCKED: TEXT.locked,
    UNAUTHORIZED: TEXT.denied,
    REVOKED: TEXT.denied,
    NO_STORAGE: TEXT.noStorage,
    POLICY_DENIED: TEXT.denied,
});

/** Safe message from an error code only; never from the error message. */
export function messageFor(code, fallback) {
    return (code && ERROR_TEXT[code]) || fallback;
}

/** Default connection source: the browser flag and events when they exist. */
function browserConnection() {
    const nav = typeof navigator !== 'undefined' ? navigator : null;
    const win = typeof window !== 'undefined' && window.addEventListener ? window : null;
    return {
        isOnline: () => (nav && typeof nav.onLine === 'boolean' ? nav.onLine : true),
        subscribe(fn) {
            if (!win) return () => {};
            win.addEventListener('online', fn);
            win.addEventListener('offline', fn);
            return () => { win.removeEventListener('online', fn); win.removeEventListener('offline', fn); };
        },
    };
}

/**
 * Controller behind OfflineView. Without a storage adapter, a profile other than
 * the managed offline profile or a missing grant the controller stays denied and
 * every write returns {saved:false}. \`vue.reactive\` is used when given.
 */
export function createOfflineViewController(options) {
    const opts = options || {};
    const who = opts.who || null;
    const storage = opts.storage || null;
    const profileOk = !!(who && who.userId && who.profile === MANAGED_PROFILE);
    const crypto = opts.crypto || createOfflineCrypto({ client: opts.client || createClinicApiClient() });
    // The store asks the server through this wrapper. A grant result that the view
    // already fetched is handed over once, so one check costs one request.
    let preset = null;
    const storeCrypto = Object.create(crypto);
    storeCrypto.refreshAuthorization = () => {
        if (!preset) return crypto.refreshAuthorization();
        const r = preset;
        preset = null;
        return Promise.resolve(r);
    };
    const store = profileOk ? (opts.store || createOfflineStore({ crypto: storeCrypto, storage, who })) : null;
    const connection = opts.connection || browserConnection();
    const now = opts.now || (() => new Date());
    const reactive = (opts.vue && opts.vue.reactive) || ((x) => x);
    const prefix = profileOk ? NAMESPACE + '/' + who.userId + '/' + who.profile + '/' : null;

    const state = reactive({
        phase: 'checking', // checking | denied | locked | unlocked
        online: connection.isOnline(),
        lastContact: null,
        profile: who && who.profile ? who.profile : null,
        drafts: [], // { key, dictationId, sequence }
        unsaved: 0,
        lost: 0,
        quotaBytes: 0,
        usedBytes: 0,
        passphrase: '',
        pendingDiscard: null,
        notice: '',
        noticeKind: 'normal',
        busy: false,
        loggedOut: false,
    });

    let stop = () => {};
    let disposed = false;
    let loggedOut = false;
    let everAuthorized = false;
    const say = (text, kind) => { state.notice = text; state.noticeKind = kind || 'normal'; };

    function deny(text) {
        Object.assign(state, { phase: 'denied', drafts: [], unsaved: 0, lost: 0, quotaBytes: 0, usedBytes: 0, passphrase: '', pendingDiscard: null });
        say(text || TEXT.denied, 'error');
    }

    async function listDrafts() {
        if (!storage || !prefix) return [];
        const keys = await storage.keys(prefix);
        return keys.map((key) => {
            const [dictationId, sequence] = key.slice(prefix.length).split('/');
            return { key, dictationId, sequence: Number(sequence) };
        });
    }

    /**
     * Fresh grant check. Only an explicit revoke (ok answer without a grant) reaches
     * the store and purges. A transport or permission failure keeps every draft:
     * returns 'ok' | 'denied' | 'kept'.
     */
    async function authorize() {
        const res = await crypto.refreshAuthorization();
        if (!res.available && res.code !== 'REVOKED') return 'kept';
        preset = res;
        try {
            const auth = await store.authorize();
            if (!auth.available) return 'denied';
        } finally {
            preset = null;
        }
        everAuthorized = true;
        return 'ok';
    }

    /** Server unreachable or session gone: keep drafts, block server actions, never purge. */
    async function keep() {
        state.drafts = await listDrafts();
        if (!state.drafts.length && !everAuthorized) return deny(TEXT.unreachable);
        state.phase = 'locked';
        say(TEXT.unreachable, 'warning');
    }

    /** Fresh grant check and inventory. Offline the grant is not asked. Online but unreachable or signed out: drafts are kept, never purged. */
    async function refresh() {
        if (disposed || loggedOut) return;
        state.busy = true;
        try {
            state.online = connection.isOnline();
            if (!profileOk) return deny(TEXT.denied);
            if (!storage) return deny(TEXT.noStorage);
            if (!state.online) {
                state.drafts = await listDrafts();
                state.phase = crypto.unlocked ? 'unlocked' : 'locked';
                return say(TEXT.offline, 'warning');
            }
            const auth = await authorize();
            if (auth === 'kept') return keep();
            if (auth === 'denied') return deny(TEXT.denied);
            state.lastContact = now();
            const rec = await store.reconcile();
            state.drafts = await listDrafts();
            Object.assign(state, { unsaved: rec.unsaved, lost: rec.lost, quotaBytes: rec.quotaBytes, usedBytes: rec.usedBytes });
            state.phase = crypto.unlocked ? 'unlocked' : 'locked';
            if (rec.lost) say(TEXT.lost, 'error');
            else if (rec.unsaved) say(TEXT.unsaved, 'warning');
            else say(state.phase === 'unlocked' ? TEXT.unlocked : TEXT.locked, state.phase === 'unlocked' ? 'success' : 'warning');
        } catch (e) {
            deny(TEXT.denied);
        } finally {
            state.busy = false;
        }
    }

    /** Seal and store one draft. Denied states never touch crypto or storage. */
    async function saveDraft(binding, value) {
        if (state.phase !== 'locked' && state.phase !== 'unlocked') return { saved: false, code: 'POLICY_DENIED' };
        if (state.phase !== 'unlocked') return { saved: false, code: 'LOCKED' };
        const res = await store.save(binding, value);
        // Inventory and quota only: the grant is re-checked by start, connection events, unlock, restore and the refresh button.
        try {
            const rec = await store.reconcile();
            state.drafts = await listDrafts();
            Object.assign(state, { unsaved: rec.unsaved, lost: rec.lost, quotaBytes: rec.quotaBytes, usedBytes: rec.usedBytes });
            if (rec.lost) say(TEXT.lost, 'error');
            else if (rec.unsaved) say(TEXT.unsaved, 'warning');
            else if (state.online) say(TEXT.unlocked, 'success');
        } catch (e) {
            say(TEXT.restoreFailed, 'error');
        }
        return res;
    }

    async function unlock(wrapped) {
        if (state.phase !== 'locked' || !state.online) return false;
        const secret = state.passphrase;
        state.passphrase = '';
        try {
            const auth = await authorize();
            if (auth === 'kept') { await keep(); return false; }
            if (auth === 'denied') { deny(TEXT.denied); return false; }
            state.lastContact = now();
            if (wrapped) await crypto.unlock(secret, wrapped, who);
            else {
                const created = await crypto.setup(secret, who);
                if (typeof opts.onWrapped === 'function') opts.onWrapped(created);
            }
        } catch (e) {
            say(messageFor(e && e.code, TEXT.unlockFailed), 'error');
            return false;
        }
        await refresh();
        return true;
    }

    function lock() {
        crypto.lock();
        if (state.phase === 'unlocked') { state.phase = 'locked'; say(TEXT.locked, 'warning'); }
    }

    /** Re-checks the grant, decrypts one draft and hands it to the caller. The value is not kept. */
    async function restore(dictationId, sequence) {
        if (state.phase !== 'unlocked' || !state.online) return null;
        try {
            const auth = await authorize();
            if (auth === 'kept') { await keep(); return null; }
            if (auth === 'denied') { deny(TEXT.denied); return null; }
            state.lastContact = now();
            const value = await store.load({ dictationId, sequence });
            say(TEXT.restored, 'success');
            if (typeof opts.onRestore === 'function') opts.onRestore({ dictationId, sequence, value });
            return true;
        } catch (e) {
            say(messageFor(e && e.code, TEXT.restoreFailed), 'error');
            return null;
        }
    }

    function askDiscard(key) {
        if (state.drafts.some((d) => d.key === key)) state.pendingDiscard = key;
    }

    function cancelDiscard() { state.pendingDiscard = null; }

    /** Removes the confirmed local draft only. The server original is never touched. */
    async function confirmDiscard() {
        const key = state.pendingDiscard;
        state.pendingDiscard = null;
        if (!key || !storage || !state.drafts.some((d) => d.key === key)) return false;
        // The store tracks the keys it wrote. Reconcile around the removal so the deliberate
        // discard is not counted as a browser eviction; real losses are still reported.
        let lost = 0;
        if (store) lost += (await store.reconcile()).lost;
        await storage.remove(key);
        if (store) {
            const rec = await store.reconcile();
            lost += Math.max(0, rec.lost - 1);
            Object.assign(state, { unsaved: rec.unsaved, quotaBytes: rec.quotaBytes, usedBytes: rec.usedBytes });
        }
        state.lost = lost;
        state.drafts = await listDrafts();
        if (lost) say(TEXT.lost, 'error');
        else say(TEXT.discarded, 'success');
        return true;
    }

    /** Logout: purge every stored draft of this user and profile, lock the key, drop plaintext. */
    async function logout() {
        loggedOut = true;
        stop();
        state.loggedOut = true;
        if (store) { await store.purge(); store.release(); } else crypto.lock();
        Object.assign(state, { phase: 'denied', drafts: [], unsaved: 0, lost: 0, usedBytes: 0, passphrase: '', pendingDiscard: null });
        say(TEXT.loggedOut, 'normal');
        if (typeof opts.onLoggedOut === 'function') opts.onLoggedOut();
    }

    function start() {
        stop = connection.subscribe(() => { refresh(); });
        return refresh();
    }

    function dispose() {
        disposed = true;
        stop();
        if (store) store.release(); else crypto.lock();
        Object.assign(state, { drafts: [], passphrase: '', pendingDiscard: null });
    }

    return {
        state, start, refresh, saveDraft, unlock, lock, restore,
        askDiscard, cancelDiscard, confirmDiscard, logout, dispose,
    };
}

// Class bundles for the V8 look: the app's utility reset wins over the element rules of clinic-v8.css, so headings and the
// primary button carry their sizes here.
const HEADINGS = '[&_h1]:text-[30px] [&_h1]:max-[760px]:text-[26px] [&_h1]:font-bold [&_h1]:leading-[1.2] [&_h1]:tracking-[-0.8px] [&_h1]:mb-[9px] [&_h1]:text-[color:var(--v8-text)] '
    + '[&_h2]:text-[16px] [&_h2]:font-bold [&_h2]:leading-[1.35] [&_h2]:tracking-[-0.2px] [&_h2]:mb-[14px] [&_h2]:text-[color:var(--v8-text)] [&_button]:px-[13px]';
const PRIMARY = 'primary !bg-[var(--ink)] !border-[color:var(--ink)] !text-[13px] !font-semibold !px-[13px] hover:!bg-[var(--brand2)]';

export const OfflineView = {
    name: 'ClinicOfflineView',
    props: {
        client: { type: Object, default: null },
        who: { type: Object, required: true },
        storage: { type: Object, default: null },
        getWrapped: { type: Function, default: null },
        crypto: { type: Object, default: null },
        store: { type: Object, default: null },
        connection: { type: Object, default: null },
        vue: { type: Object, default: null },
        onRestore: { type: Function, default: null },
        onLoggedOut: { type: Function, default: null },
        onWrapped: { type: Function, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        if (!V) throw new Error('Vue is required to mount the offline view');
        const c = createOfflineViewController({
            client: props.client || undefined,
            who: props.who,
            storage: props.storage || undefined,
            crypto: props.crypto || undefined,
            store: props.store || undefined,
            connection: props.connection || undefined,
            vue: V,
            onRestore: props.onRestore || undefined,
            onLoggedOut: props.onLoggedOut || undefined,
            onWrapped: props.onWrapped || undefined,
        });
        if (typeof V.onMounted === 'function') V.onMounted(() => { c.start(); });
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        const s = c.state;
        const noticeClass = () => (s.noticeKind === 'error' ? 'note burgundy' : s.noticeKind === 'warning' ? 'note warning' : 'note');
        const profileLabel = () => PROFILE_LABELS[s.profile] || 'Nicht freigegebenes Profil';
        const contact = () => (s.lastContact ? s.lastContact.toLocaleTimeString('de-CH') : TEXT.noContact);
        const submit = () => c.unlock(props.getWrapped ? props.getWrapped() : null);
        return { c, s, TEXT, HEADINGS, PRIMARY, noticeClass, profileLabel, contact, submit };
    },
    template: `
<section class="clinic-offline v8-page" :class="HEADINGS" aria-labelledby="clinic-offline-title">
  <div class="pagehead">
    <div>
      <div class="eyebrow">{{ TEXT.eyebrow }}</div>
      <h1 id="clinic-offline-title">{{ TEXT.title }}</h1>
      <p class="subline">{{ TEXT.subline }}</p>
    </div>
    <div class="head-actions"></div>
  </div>
  <div class="grid">
    <section class="panel" aria-labelledby="clinic-offline-drafts">
      <h2 id="clinic-offline-drafts">{{ TEXT.draftsAndRecovery }}</h2>
      <template v-if="s.phase === 'locked' || s.phase === 'unlocked'">
        <p v-if="!s.drafts.length" class="hint" data-testid="no-drafts">{{ TEXT.noDrafts }}</p>
        <div v-else class="table-wrap">
          <table :aria-label="TEXT.drafts">
            <thead><tr><th scope="col">{{ TEXT.draft }}</th><th scope="col">{{ TEXT.nextStep }}</th></tr></thead>
            <tbody>
              <template v-for="d in s.drafts" :key="d.key">
                <tr data-testid="draft">
                  <td><strong>{{ TEXT.draft }} {{ d.dictationId }}</strong><small>{{ TEXT.sequence }} {{ d.sequence }}</small></td>
                  <td class="whitespace-normal">
                    <div class="actions">
                      <button type="button" data-testid="restore"
                              :disabled="s.phase !== 'unlocked' || !s.online || s.busy" @click="c.restore(d.dictationId, d.sequence)">{{ TEXT.restore }}</button>
                      <button type="button" class="danger" data-testid="discard" @click="c.askDiscard(d.key)">{{ TEXT.discard }}</button>
                    </div>
                  </td>
                </tr>
                <tr v-if="s.pendingDiscard === d.key">
                  <td colspan="2" class="whitespace-normal">
                    <div class="note warning !mt-0" role="alert" data-testid="discard-confirm">
                      <p>{{ TEXT.discardWarn }}</p>
                      <div class="actions">
                        <button type="button" class="danger" data-testid="discard-yes" @click="c.confirmDiscard()">{{ TEXT.discardConfirm }}</button>
                        <button type="button" data-testid="discard-no" @click="c.cancelDiscard()">{{ TEXT.cancel }}</button>
                      </div>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <p class="text-status mt-3" data-testid="usage">{{ TEXT.used }}: {{ s.usedBytes }} {{ TEXT.of }} {{ s.quotaBytes }} {{ TEXT.bytes }}</p>
      </template>
      <p v-else class="hint" data-testid="drafts-unavailable">{{ TEXT.draftsUnavailable }}</p>
    </section>
    <section class="panel" aria-labelledby="clinic-offline-connection">
      <h2 id="clinic-offline-connection">{{ TEXT.connectionAndCheck }}</h2>
      <dl class="keyvalues">
        <div><dt>{{ TEXT.profile }}</dt><dd data-testid="profile">{{ profileLabel() }}</dd></div>
        <div><dt>{{ TEXT.connection }}</dt><dd data-testid="connection"><span class="tag dot" :class="s.online ? 'neutral' : 'wait'">{{ s.online ? TEXT.online : TEXT.offline }}</span></dd></div>
        <div><dt>{{ TEXT.lastContact }}</dt><dd>{{ contact() }}</dd></div>
      </dl>
      <p role="status" aria-live="polite" data-testid="notice" :class="noticeClass()">{{ s.phase === 'checking' ? TEXT.checking : s.notice }}</p>
      <div class="actions mt-4">
        <button type="button" :class="PRIMARY" data-testid="refresh" v-if="!s.loggedOut" :disabled="s.busy" @click="c.refresh()">{{ TEXT.refresh }}</button>
        <button type="button" class="danger" data-testid="logout" @click="c.logout()">{{ TEXT.logout }}</button>
      </div>
      <template v-if="s.phase === 'locked' || s.phase === 'unlocked'">
        <div v-if="s.phase === 'locked'" class="mt-4" role="group" :aria-label="TEXT.unlockSection">
          <div class="flex flex-col gap-1.5 mb-4 [&_label]:text-xs [&_label]:font-bold">
            <label for="clinic-offline-pass">{{ TEXT.passphrase }}</label>
            <input id="clinic-offline-pass" type="password" autocomplete="off" v-model="s.passphrase" :disabled="!s.online"
                   @keydown.enter.prevent="submit()">
          </div>
          <div class="actions"><button type="button" :class="PRIMARY" data-testid="unlock" :disabled="!s.online" @click="submit()">{{ TEXT.unlock }}</button></div>
        </div>
        <div v-else class="actions mt-4"><button type="button" data-testid="lock" @click="c.lock()">{{ TEXT.lock }}</button></div>
      </template>
      <p class="hint mt-4">{{ TEXT.keys }}</p>
    </section>
  </div>
</section>`,
};

export function registerOfflineView(app) {
    app.component('clinic-offline-view', OfflineView);
    return app;
}
