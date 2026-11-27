import { createOfflineViewController, MANAGED_PROFILE } from './compose-offline.js';
import { createOfflineCrypto } from './offline-crypto-ui.js';
import { NAMESPACE } from './offline-store-ui.js';

function browserStorage() {
    const database = new Promise((resolve, reject) => {
        const request = indexedDB.open(NAMESPACE, 1);
        request.onupgradeneeded = () => request.result.createObjectStore('encrypted');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });

    async function transact(mode, operation) {
        const db = await database;
        return new Promise((resolve, reject) => {
            const transaction = db.transaction('encrypted', mode);
            const request = operation(transaction.objectStore('encrypted'));
            transaction.oncomplete = () => resolve(request.result);
            transaction.onabort = () => reject(transaction.error);
        });
    }

    return {
        get: (key) => transact('readonly', (store) => store.get(key)),
        put: (key, value, stillAllowed) => transact('readwrite', (store) => {
            if (!stillAllowed()) throw new Error('UNAUTHORIZED');
            return key.startsWith('wrapped/') ? store.add(value, key) : store.put(value, key);
        }),
        remove: (key) => transact('readwrite', (store) => store.delete(key)),
        keys: (prefix) => transact('readonly', (store) => store.getAllKeys(IDBKeyRange.bound(prefix, prefix + '\uffff'))),
        close: async () => (await database).close(),
    };
}

function audioData(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

function restoreAudio({ value }) {
    const bytes = Uint8Array.from(atob(value.audio), (character) => character.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: value.type }));
    const link = document.createElement('a');
    link.href = url;
    link.download = value.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function createManagedRecorderDrafts({ client, userId, vue, onLoggedOut }) {
    if (!globalThis.crypto?.subtle || !globalThis.indexedDB || !globalThis.navigator?.locks || !userId) return null;
    const crypto = createOfflineCrypto({ client, requireExpiry: true });
    if (!(await crypto.refreshAuthorization()).available) return null;

    const storage = browserStorage();
    const who = { userId: String(userId), profile: MANAGED_PROFILE };
    const prefix = `${NAMESPACE}/${who.userId}/${who.profile}/`;
    const wrapKey = (ref) => `wrapped/${ref.accountVersion}/${ref.browserId}/${ref.version}`;
    const controller = createOfflineViewController({
        who, crypto, vue,
        storage: { ...storage, put: (key, value) => storage.put(key, value, () => crypto.unlocked) },
        async onWrapped(wrapped) {
            try {
                await storage.put(wrapKey(wrapped), wrapped, () => crypto.unlocked);
            } catch (error) {
                crypto.lock();
                throw error;
            }
        },
        onRestore(result) {
            if (!crypto.unlocked) throw new Error('UNAUTHORIZED');
            restoreAudio(result);
        },
        onLoggedOut,
    });
    await controller.start();
    return {
        controller,
        get canUseLocalFiles() {
            return ['locked', 'unlocked'].includes(controller.state.phase)
                && !controller.state.loggedOut && crypto.authorized;
        },
        getWrapped() {
            const reference = crypto.reference;
            return reference ? storage.get(wrapKey(reference)) : null;
        },
        async saveTake(binding, file, title) {
            if (!crypto.unlocked || file.size > controller.state.quotaBytes) return false;
            const audio = await audioData(file);
            return navigator.locks.request(prefix, async () => {
                const result = await controller.saveDraft(binding, { audio, title, name: file.name, type: file.type });
                return result.saved;
            });
        },
        removeTake: (binding) => storage.remove(`${prefix}${binding.dictationId}/${binding.sequence}`),
        dispose() {
            controller.dispose();
            void storage.close().catch(() => {});
        },
    };
}
