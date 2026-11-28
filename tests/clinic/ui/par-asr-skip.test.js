import { afterEach, expect, it, vi } from 'vitest';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';

const ETAG = '"d1:1"';
const ok = (data) => ({ outcome: 'ok', data });
const waiting = { can_start_manual: false, asr_wait_message: 'Spracherkennung läuft …', etag: ETAG };
const offered = { ...waiting, can_start_manual: true };
const skipped = ok({ status: 'zu_schreiben' });
const hold = () => ok({ lease_id: 'lease-1', generation: 1, expires_at: new Date(Date.now() + 60000).toISOString() });
let controller;

function mount(detail, skip = () => skipped, acquire = hold) {
    vi.useFakeTimers();
    const request = vi.fn(async (path, options) => {
        if (path.endsWith('/detail')) return detail();
        if (path.endsWith('/asr/skip')) return skip(options);
        if (path.endsWith('/leases') && options?.method === 'POST') return acquire();
        if (path.endsWith('/correction')) return ok({ text_revision_id: null, text: '' });
        return ok({ items: [] });
    });
    const client = { request, switchContext() {} };
    controller = createCorrectionViewController({ client, dictationId: 'd1', currentUser: { id: 7 }, contract: 'v8' });
    const calls = (suffix) => request.mock.calls.filter(([path]) => path.endsWith(suffix));
    return { c: controller, calls };
}

afterEach(() => {
    controller?.dispose();
    vi.useRealTimers();
});

it('polls without reloading text and opens the editor once after the deadline', async () => {
    let wait = waiting;
    const { c, calls } = mount(() => ok(wait));
    await c.start();
    const initialReads = calls('/correction').length;
    const initialAudioReads = calls('/audio-revisions/current').length;
    expect(c.asrWait.value.can_start_manual).toBe(false);
    await c.skipAsr();
    expect(calls('/asr/skip')).toHaveLength(0);

    wait = offered;
    await vi.advanceTimersByTimeAsync(5000);
    expect(c.asrWait.value.can_start_manual).toBe(true);
    expect(calls('/correction')).toHaveLength(initialReads);
    expect(calls('/audio-revisions/current')).toHaveLength(initialAudioReads);
    await Promise.all([c.skipAsr(), c.skipAsr()]);
    expect(calls('/asr/skip')).toEqual([['/dictations/d1/asr/skip', {
        method: 'POST', body: {}, ifMatch: ETAG, idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/),
    }]]);
    expect(calls('/leases').filter(([, opts]) => opts?.method === 'POST')).toHaveLength(1);
    expect(calls('/audio-revisions/current').length).toBeGreaterThan(initialAudioReads);
    expect(c.asrSkipStatus.value).toBe('Zu schreiben');
    expect(c.editor.canEdit.value).toBe(true);
});

it('never offers manual writing when the response keeps it disabled or omits it', async () => {
    let wait = waiting;
    const { c, calls } = mount(() => ok(wait));
    await c.start();
    await vi.advanceTimersByTimeAsync(20000);
    expect(c.asrWait.value.can_start_manual).toBe(false);
    wait = { etag: ETAG };
    await vi.advanceTimersByTimeAsync(5000);
    expect(c.asrWait.value).toBeNull();
    await c.skipAsr();
    expect(calls('/asr/skip')).toHaveLength(0);
});

it('reuses the same request after a lost response and keeps read-only if the lease is denied', async () => {
    let first = true;
    const { c, calls } = mount(() => ok(offered), () => {
        if (!first) return skipped;
        first = false;
        return { outcome: 'network' };
    }, () => ({ outcome: 'permission', status: 403 }));
    await c.start();
    await c.skipAsr();
    expect(c.asrSkipError.value).toBeTruthy();
    await c.skipAsr();
    expect(calls('/asr/skip')).toHaveLength(2);
    expect(calls('/asr/skip')[1]).toEqual(calls('/asr/skip')[0]);
    expect(c.asrSkipStatus.value).toBe('Zu schreiben');
    expect(c.editor.canEdit.value).toBe(false);
});

it('refreshes a stale offer and discards a late detail response after navigation', async () => {
    let detail = () => ok(offered);
    const { c, calls } = mount(() => detail(), () => ({ outcome: 'conflict', status: 409 }));
    await c.start();
    detail = () => ok(waiting);
    await c.skipAsr();
    expect(c.asrWait.value.can_start_manual).toBe(false);
    let resolve;
    detail = () => new Promise((done) => { resolve = done; });
    await vi.advanceTimersByTimeAsync(5000);
    c.dispose();
    resolve(ok(offered));
    await vi.advanceTimersByTimeAsync(20000);
    expect(c.asrWait.value).toBeNull();
    expect(calls('/detail')).toHaveLength(3);
});
