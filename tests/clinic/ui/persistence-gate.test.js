import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
    failedRecordingHandling, gateFailedUploads, gateRecordingDB, NOT_STORED_MESSAGE, persistenceAllowed, persistenceDenied,
} from '../../../static/js/clinic/persistence-gate.js';
import * as RawRecordingDB from '../../../static/js/modules/db/recording-persistence.js';
import * as RawFailedUploads from '../../../static/js/modules/db/failed-uploads.js';

let open;

function stubPage(dataset) {
    vi.stubGlobal('document', { getElementById: (id) => (id === 'app' ? { dataset } : null) });
}

beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    // An open() whose request always errors: any call is visible in the spy.
    open = vi.fn(() => {
        const req = { onsuccess: null, onerror: null, onupgradeneeded: null, error: new Error('stub') };
        queueMicrotask(() => req.onerror && req.onerror({ target: req }));
        return req;
    });
    vi.stubGlobal('indexedDB', { open });
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

async function callAll(rec, failed) {
    const blob = new Blob(['x']);
    const calls = [
        () => rec.startRecordingSession({ mode: 'microphone' }),
        () => rec.saveChunk(blob, 0),
        () => rec.updateRecordingMetadata({ duration: 1 }),
        () => rec.pruneOldChunks(5),
        () => rec.checkForRecoverableRecording(),
        () => rec.recoverRecording(),
        () => rec.clearRecordingSessionIfStartedAt(1),
        () => rec.clearRecordingSession(),
        () => failed.storeFailedUpload({ file: blob, fileName: 'a.webm', fileSize: 1 }),
        () => failed.getFailedUploads(),
        () => failed.updateRetryCount(1, 1, 'e'),
        () => failed.deleteFailedUpload(1),
    ];
    const results = [];
    for (const call of calls) {
        try { results.push({ ok: await call() }); } catch (e) { results.push({ err: e.message }); }
    }
    return results;
}

describe('clinic persistence gate', () => {
    it('clinic mode on, no policy: never opens IndexedDB, recording and retry store stay empty', async () => {
        stubPage({ clinicMode: 'True' });
        expect(persistenceAllowed()).toBe(false);
        expect(persistenceDenied()).toBe(true);
        const results = await callAll(gateRecordingDB(RawRecordingDB), gateFailedUploads(RawFailedUploads));
        expect(open).not.toHaveBeenCalled();
        expect(results[0].ok).toBeNull(); // no session
        expect(results[5].ok).toBeNull(); // nothing to recover
        expect(results[8].err).toBe('PERSISTENCE_DENIED'); // store refuses, caller shows the message
        expect(results[9].ok).toEqual([]);
    });

    it.each([['False'], ['true'], ['1'], ['']])('only the exact grant value allows it (%s denies)', async (value) => {
        stubPage({ clinicMode: 'True', clinicPersistentAudio: value });
        expect(persistenceAllowed()).toBe(false);
        await callAll(gateRecordingDB(RawRecordingDB), gateFailedUploads(RawFailedUploads));
        expect(open).not.toHaveBeenCalled();
    });

    it('without an #app element clinic mode is off and nothing is gated', async () => {
        vi.stubGlobal('document', { getElementById: () => null });
        expect(persistenceAllowed()).toBe(true);
    });

    it('allowing policy re-enables the upstream path', async () => {
        stubPage({ clinicMode: 'True', clinicPersistentAudio: 'True' });
        expect(persistenceAllowed()).toBe(true);
        await callAll(gateRecordingDB(RawRecordingDB), gateFailedUploads(RawFailedUploads));
        expect(open).toHaveBeenCalled();
    });

    it('clinic mode off: upstream behaviour, IndexedDB is used', async () => {
        stubPage({ clinicMode: 'False' });
        expect(persistenceAllowed()).toBe(true);
        await callAll(gateRecordingDB(RawRecordingDB), gateFailedUploads(RawFailedUploads));
        expect(open).toHaveBeenCalled();
    });

    it('policy is read per call: revoking it stops later writes', async () => {
        const dataset = { clinicMode: 'True', clinicPersistentAudio: 'True' };
        stubPage(dataset);
        const rec = gateRecordingDB(RawRecordingDB);
        await rec.saveChunk(new Blob(['x']), 0).catch(() => {});
        const before = open.mock.calls.length;
        expect(before).toBeGreaterThan(0);
        dataset.clinicPersistentAudio = 'False';
        await rec.saveChunk(new Blob(['x']), 1);
        expect(open.mock.calls.length).toBe(before);
    });

    it('failed recording: notice when denied, store when allowed, no retry promise', () => {
        stubPage({ clinicMode: 'True' });
        expect(failedRecordingHandling()).toBe('notice');
        expect(NOT_STORED_MESSAGE).not.toMatch(/wiederholen/);
        stubPage({ clinicMode: 'True', clinicPersistentAudio: 'True' });
        expect(failedRecordingHandling()).toBe('store');
        stubPage({ clinicMode: 'False' });
        expect(failedRecordingHandling()).toBe('store');
    });

    it('denied initDB never opens IndexedDB', async () => {
        stubPage({ clinicMode: 'True' });
        await gateRecordingDB(RawRecordingDB).initDB();
        await gateFailedUploads(RawFailedUploads).initDB();
        expect(open).not.toHaveBeenCalled();
    });

    it('gate wraps every exported persistence function', () => {
        const rec = gateRecordingDB(RawRecordingDB);
        const failed = gateFailedUploads(RawFailedUploads);
        for (const name of ['initDB', 'startRecordingSession', 'saveChunk', 'updateRecordingMetadata', 'pruneOldChunks',
            'checkForRecoverableRecording', 'recoverRecording', 'clearRecordingSessionIfStartedAt', 'clearRecordingSession']) {
            expect(rec[name]).not.toBe(RawRecordingDB[name]);
        }
        for (const name of ['initDB', 'storeFailedUpload', 'getFailedUploads', 'updateRetryCount', 'deleteFailedUpload']) {
            expect(failed[name]).not.toBe(RawFailedUploads[name]);
        }
    });
});
