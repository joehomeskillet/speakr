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

    it('keeps the native-window clause unresolved and exports a labelled component', () => {
        expect(NATIVE_WINDOW_TRANSFER).toBe('unresolved');
        expect(ClipboardCopy.template).toContain('aria-live="polite"');
        expect(ClipboardCopy.template).toContain('type="button"');
        expect(ClipboardCopy.template).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    });
});
