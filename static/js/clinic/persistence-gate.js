/**
 * Browser persistence gate for the stock recorder (V8 P21, byod_internal.persistent_audio=false).
 *
 * In CLINIC_MODE no audio is written to IndexedDB (recording crash-recovery,
 * failed-upload retry store) unless the server explicitly allows it for this
 * device class: #app carries data-clinic-persistent-audio="True". Default is
 * deny; a missing or any other value denies. With CLINIC_MODE off the gate is
 * transparent and upstream behaviour is unchanged.
 *
 * The policy is read on every call (never cached), so a changed page state is
 * honoured and a stale allow cannot outlive its source.
 */

export const NOT_STORED_MESSAGE =
    'Der Upload ist fehlgeschlagen. Die Aufnahme wird nicht lokal gespeichert. Bitte erneut aufnehmen oder den Upload wiederholen.';

function appDataset() {
    try {
        return (typeof document !== 'undefined' && document.getElementById('app')?.dataset) || {};
    } catch (_) {
        return {};
    }
}

/** True when the browser may keep audio locally. Default deny in clinic mode. */
export function persistenceAllowed() {
    const data = appDataset();
    if (data.clinicMode !== 'True') return true;
    return data.clinicPersistentAudio === 'True';
}

/** True when clinic mode is on and local persistence is denied. */
export function persistenceDenied() {
    return !persistenceAllowed();
}

// What a denied call resolves to; keys are the persistence functions.
const RECORDING_DENIED = {
    startRecordingSession: () => null,
    saveChunk: () => undefined,
    updateRecordingMetadata: () => undefined,
    pruneOldChunks: () => undefined,
    checkForRecoverableRecording: () => null,
    recoverRecording: () => null,
    clearRecordingSessionIfStartedAt: () => undefined,
    clearRecordingSession: () => undefined,
    getDatabaseSize: () => 0,
};

const FAILED_DENIED = {
    storeFailedUpload: () => { throw new Error('PERSISTENCE_DENIED'); },
    getFailedUploads: () => [],
    getFailedUpload: () => null,
    updateRetryCount: () => undefined,
    deleteFailedUpload: () => undefined,
    clearAllFailedUploads: () => undefined,
    getFailedUploadCount: () => 0,
};

function gate(module, denied) {
    const wrapped = { ...module };
    for (const [name, fallback] of Object.entries(denied)) {
        const real = module[name];
        if (typeof real !== 'function') continue;
        wrapped[name] = (...args) => {
            if (persistenceAllowed()) return real(...args);
            try {
                return Promise.resolve(fallback());
            } catch (e) {
                return Promise.reject(e);
            }
        };
    }
    return wrapped;
}

/** Same API as db/recording-persistence.js; touches IndexedDB only when allowed. */
export const gateRecordingDB = (module) => gate(module, RECORDING_DENIED);

/** Same API as db/failed-uploads.js; touches IndexedDB only when allowed. */
export const gateFailedUploads = (module) => gate(module, FAILED_DENIED);
