import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
    ClipboardCopy, NATIVE_WINDOW_TRANSFER, capabilities, createClipboardCopy,
} from '../../../static/js/clinic/clipboard-ui.js';

const FLAVORS = { 'text/plain': 'Befund', 'text/html': '<p>Befund</p>', status: 'UNKNOWN', generation: 1 };

function ok(data) { return { outcome: 'ok', data }; }

function setup(overrides = {}) {
    const calls = [];
    const responses = overrides.responses || [ok(FLAVORS), ok({ status: 'COMPLETED' })];
    const client = {
        request: vi.fn(async (path, options) => {
            calls.push({ path, options });
            return responses.shift() || ok({});
        }),
    };
    class Item { constructor(data) { this.data = data; } }
    const clipboard = {
        writeText: vi.fn(async () => {}),
        write: vi.fn(async () => {}),
        ...(overrides.clipboard || {}),
    };
    const copier = createClipboardCopy({
        client, dictationId: 'd1', revisionId: 'r1', clipboard, ClipboardItem: Item,
        newKey: () => 'key-1', ...(overrides.options || {}),
    });
    return { calls, client, clipboard, copier };
}

describe('clinic clipboard ui', () => {
    beforeEach(() => {
        global.localStorage = { setItem: vi.fn(), getItem: () => null };
        global.sessionStorage = { setItem: vi.fn(), getItem: () => null };
    });

    it('copies plain text and reports the observed outcome', async () => {
        const { calls, clipboard, copier } = setup();
        const state = await copier.copy('plain');
        expect(clipboard.writeText).toHaveBeenCalledWith('Befund');
        expect(calls[0].path).toBe('/dictations/d1/clipboard/r1?format=plain');
        expect(calls[1].path).toBe('/dictations/d1/clipboard/r1/outcome');
        expect(calls[1].options.body).toEqual({ outcome: 'copied' });
        expect(calls[1].options.idempotencyKey).toBe('key-1');
        expect(state.phase).toBe('copied');
        expect(state.surface.status).toBe('success');
        expect(global.localStorage.setItem).not.toHaveBeenCalled();
    });

    it('copies formatted data as plain and html flavours', async () => {
        const { calls, clipboard, copier } = setup();
        const state = await copier.copy('formatted');
        expect(calls[0].path).toBe('/dictations/d1/clipboard/r1?format=formatted');
        const [item] = clipboard.write.mock.calls[0][0];
        expect(Object.keys(item.data)).toEqual(['text/plain', 'text/html']);
        expect(state.phase).toBe('copied');
    });

    it('refuses without authorization and makes no request', async () => {
        const { client, copier } = setup({ options: { authorized: false } });
        const state = await copier.copy('plain');
        expect(state.phase).toBe('unauthorized');
        expect(client.request).not.toHaveBeenCalled();
    });

    it('maps a server 403 to unauthorized and writes nothing', async () => {
        const { clipboard, copier } = setup({ responses: [{ outcome: 'permission' }] });
        expect((await copier.copy('plain')).phase).toBe('unauthorized');
        expect(clipboard.writeText).not.toHaveBeenCalled();
    });

    it('treats a stale revision as stale and writes nothing', async () => {
        const { clipboard, copier } = setup({ responses: [{ outcome: 'conflict' }] });
        const state = await copier.copy('formatted');
        expect(state.phase).toBe('stale');
        expect(clipboard.write).not.toHaveBeenCalled();
    });

    it('reports capability absence without contacting the server', async () => {
        const { client, copier } = setup({ options: { clipboard: {} } });
        expect((await copier.copy('plain')).phase).toBe('unavailable');
        expect(client.request).not.toHaveBeenCalled();
        expect(capabilities({ clipboard: { writeText() {} } })).toEqual({ plain: true, formatted: false });
    });

    it('reports a denial as denied', async () => {
        const denied = Object.assign(new Error('x'), { name: 'NotAllowedError' });
        const { calls, copier } = setup({ clipboard: { writeText: vi.fn(async () => { throw denied; }) } });
        expect((await copier.copy('plain')).phase).toBe('denied');
        expect(calls[1].options.body).toEqual({ outcome: 'denied' });
    });

    it('reports an abort as aborted (unknown result)', async () => {
        const abort = Object.assign(new Error('x'), { name: 'AbortError' });
        const { calls, copier } = setup({ clipboard: { writeText: vi.fn(async () => { throw abort; }) } });
        expect((await copier.copy('plain')).phase).toBe('aborted');
        expect(calls[1].options.body).toEqual({ outcome: 'aborted' });
    });

    it('retries an unacknowledged outcome with the same key', async () => {
        const { calls, copier } = setup({ responses: [ok(FLAVORS), { outcome: 'network' }, ok({ status: 'COMPLETED' })] });
        expect((await copier.copy('plain')).phase).toBe('unreported');
        expect((await copier.retryReport()).phase).toBe('copied');
        expect(calls[2].options.idempotencyKey).toBe('key-1');
        expect(calls[2].options.body).toEqual({ outcome: 'copied' });
    });

    const liveOptions = { live: true, releaseId: 'rel-1' };
    const prepared = ok({ ...FLAVORS, revision_id: 'r1', generation: 2 });
    const succeeded = ok({ status: 'succeeded', generation: 2 });

    it('opts into live prepare and requires a matching receipt after a browser write', async () => {
        const { calls, clipboard, copier } = setup({
            options: liveOptions,
            responses: [prepared, ok({ status: 'succeeded', generation: 1 }),
                ok({ status: 'UNKNOWN', generation: 2 }), { outcome: 'network' }, succeeded],
        });
        expect(await copier.copy('formatted')).toMatchObject({ phase: 'unreported', canRetryReport: true, retryFormat: null });
        expect(calls[0]).toEqual({
            path: '/dictations/d1/clipboard/r1?format=formatted&release_id=rel-1&live=1',
            options: { idempotencyKey: 'key-1' },
        });
        await copier.copy('plain');
        expect(calls).toHaveLength(2);
        const retry = copier.retryReport();
        expect(copier.state.value.busy).toBe(true);
        expect(await retry).toMatchObject({ phase: 'unreported', canRetryReport: true, busy: false });
        expect(await copier.retryReport()).toMatchObject({ phase: 'unreported', canRetryReport: true });
        expect(await copier.retryReport()).toMatchObject({ phase: 'copied', canRetryReport: false });
        const report = {
            path: '/dictations/d1/clipboard/r1/outcome?release_id=rel-1&live=1',
            options: { method: 'POST', body: { outcome: 'copied', generation: 2 }, idempotencyKey: 'key-1' },
        };
        expect(calls.slice(1)).toEqual([report, report, report, report]);
        expect(clipboard.write).toHaveBeenCalledTimes(1);
        expect(clipboard.writeText).not.toHaveBeenCalled();
    });

    it.each([
        { outcome: 'network' }, { outcome: 'UNKNOWN' },
        ok({ ...FLAVORS, revision_id: 'r1', generation: 0 }),
        ok({ ...FLAVORS, revision_id: 'r1', status: 'succeeded' }),
    ])('retries ambiguous live prepare using the original key and format (%j)', async (first) => {
        const newKey = vi.fn(() => 'key-1');
        const { calls, clipboard, copier } = setup({
            options: { ...liveOptions, newKey }, responses: [first, prepared, succeeded],
        });
        expect(await copier.copy('formatted')).toMatchObject({ phase: 'failed', retryFormat: 'formatted', canRetryReport: false });
        await copier.copy('plain');
        expect(calls).toHaveLength(1);
        expect(clipboard.write).not.toHaveBeenCalled();
        expect(await copier.copy('formatted')).toMatchObject({ phase: 'copied', retryFormat: null });
        expect(calls[1]).toEqual(calls[0]);
        expect(newKey).toHaveBeenCalledTimes(1);
    });

    it('lets an explicit same-format retry resolve a live abort without losing its ambiguous report', async () => {
        const abort = Object.assign(new Error('x'), { name: 'AbortError' });
        const writeText = vi.fn(async () => {}).mockRejectedValueOnce(abort);
        const newKey = vi.fn(() => 'key-1');
        const { calls, copier } = setup({
            clipboard: { writeText }, options: { ...liveOptions, newKey },
            responses: [prepared, { outcome: 'network' }, ok({ status: 'UNKNOWN', generation: 2 }), prepared, succeeded],
        });
        expect(await copier.copy('plain')).toMatchObject({ phase: 'aborted', retryFormat: 'plain', canRetryReport: true });
        expect(await copier.retryReport()).toMatchObject({ phase: 'aborted', retryFormat: 'plain', canRetryReport: true });
        expect(calls[1].options.body).toEqual({ outcome: 'aborted', generation: 2 });
        expect(calls[2]).toEqual(calls[1]);
        expect(writeText).toHaveBeenCalledTimes(1);
        expect(await copier.copy('plain')).toMatchObject({ phase: 'copied', retryFormat: null, canRetryReport: false });
        expect(calls[3]).toEqual(calls[0]);
        expect(newKey).toHaveBeenCalledTimes(1);
    });

    it('reports denial if authorization disappears during live prepare, without writing', async () => {
        let authorized = true;
        const { client, calls, clipboard, copier } = setup({
            options: { ...liveOptions, authorized: () => authorized },
            responses: [ok({ status: 'failed', generation: 2 })],
        });
        client.request.mockImplementationOnce(async () => {
            authorized = false;
            return prepared;
        });
        expect(await copier.copy('plain')).toMatchObject({ phase: 'stale', canRetryReport: false });
        expect(clipboard.writeText).not.toHaveBeenCalled();
        expect(calls[0].options.body).toEqual({ outcome: 'denied', generation: 2 });
    });

    it('unlocks a live report on definitive conflict without treating it as copied', async () => {
        const newKey = vi.fn().mockReturnValueOnce('key-1').mockReturnValueOnce('key-2');
        const { calls, copier } = setup({
            options: { ...liveOptions, newKey },
            responses: [prepared, { outcome: 'network' }, { outcome: 'conflict' }, prepared, succeeded],
        });
        expect((await copier.copy('plain')).phase).toBe('unreported');
        expect(await copier.retryReport()).toMatchObject({ phase: 'stale', canRetryReport: false });
        expect((await copier.copy('plain')).phase).toBe('copied');
        expect(calls[3].options.idempotencyKey).toBe('key-2');
    });

    it('retains an already written copy for reconciliation after a temporary permission error', async () => {
        const { calls, clipboard, copier } = setup({
            options: liveOptions,
            responses: [prepared, { outcome: 'permission' }, succeeded],
        });
        expect(await copier.copy('plain')).toMatchObject({ phase: 'unreported', canRetryReport: true });
        expect(await copier.retryReport()).toMatchObject({ phase: 'copied', canRetryReport: false });
        expect(calls[2]).toEqual(calls[1]);
        expect(clipboard.writeText).toHaveBeenCalledTimes(1);
    });

    it('keeps the native-window clause unresolved and exports a labelled component', () => {
        expect(NATIVE_WINDOW_TRANSFER).toBe('unresolved');
        expect(ClipboardCopy.template).toContain('aria-live="polite"');
        expect(ClipboardCopy.template).toContain('type="button"');
        expect(ClipboardCopy.template).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    });
});
