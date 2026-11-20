import { describe, expect, it, vi } from 'vitest';
import { CorrectionBody, createCorrectionViewController, scopedClient } from '../../../static/js/clinic/compose-correction.js';
import { useCorrectionEditor } from '../../../static/js/clinic/correction-editor-ui.js';
import {
    AUTOSAVE_MS_DEFAULT, LABELS, diffLines, useCorrectionSave,
} from '../../../static/js/clinic/correction-save-ui.js';

// V8 autosave contract (UI8-004): one write in flight, lost-answer reconcile, conflict view, two tabs.
// Synthetic data only; the fake server below models the revision, lease generation and idempotency rules.

const D = '11111111-1111-4111-8111-111111111111';
const base = '/dictations/' + D;
const ok = (data) => ({ outcome: 'ok', status: 200, data });
const bad = (outcome, status, code) => ({ outcome, status, code: code || null, message: '' });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeServer(extra) {
    const s = {
        text: 'Befund', rev: 'rev-0', gen: 0, stateVersion: 3, n: 0,
        ops: new Map(), saves: [], inflight: 0, maxInflight: 0, gate: null, dropAck: false, ...(extra || {}),
    };
    s.etag = () => '"' + s.rev + '.g' + s.gen + '"';
    s.client = {
        switchContext: vi.fn(),
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            const key = method + ' ' + path;
            if (method === 'GET' && s.getMs) await sleep(s.getMs);
            if (key === 'GET ' + base + '/correction') {
                return ok({
                    dictation_id: D, text_revision_id: s.rev, text: s.text, etag: s.etag(), links: [],
                    state: { version: s.stateVersion, audio_position_ms: 1000 },
                });
            }
            if (key === 'GET ' + base + '/audio-revisions/current') return ok({ revision_id: 'a1', segments: [] });
            if (key === 'GET /templates') return ok({ items: [] });
            if (key === 'POST ' + base + '/leases') {
                s.gen += 1;
                return ok({ lease_id: 'L' + s.gen, generation: s.gen, expires_at: new Date(Date.now() + 600000).toISOString() });
            }
            if (key === 'POST ' + base + '/leases/release') return ok({});
            if (key !== 'POST ' + base + '/correction/save') return bad('UNKNOWN', 500);
            s.inflight += 1;
            s.maxInflight = Math.max(s.maxInflight, s.inflight);
            const entry = { key: options.idempotencyKey, ifMatch: options.ifMatch, body: options.body };
            s.saves.push(entry);
            try {
                if (s.saveMs) await sleep(s.saveMs);
                if (s.gate) await s.gate.promise;
                const digest = JSON.stringify(options.body);
                const stored = s.ops.get(options.idempotencyKey);
                if (stored) return stored.digest === digest ? ok(stored.result) : bad('conflict', 409, 'IDEMPOTENCY_CONFLICT');
                if (options.body.lease_generation !== s.gen) return bad('conflict', 409, 'LEASE_STALE');
                if (!options.ifMatch) return bad('validation', 428, 'PRECONDITION_REQUIRED');
                if (options.ifMatch !== s.etag() || options.body.expected_text_revision_id !== s.rev) {
                    return bad('conflict', 409, 'REVISION_CONFLICT');
                }
                s.n += 1;
                s.rev = 'rev-' + s.n;
                s.text = options.body.text;
                s.stateVersion += 1;
                const result = {
                    text_revision_id: s.rev, etag: s.etag(), state_version: s.stateVersion,
                    audio_position_ms: options.body.audio_position_ms, derived_links: 0,
                };
                s.ops.set(options.idempotencyKey, { digest, result });
                if (s.dropAck) { s.dropAck = false; return bad('network', null, 'NETWORK'); }
                return ok(result);
            } finally {
                s.inflight -= 1;
            }
        }),
    };
    return s;
}

function deferred() {
    let resolve;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
}

async function tab(server, saveOptions) {
    const editor = useCorrectionEditor({
        client: server.client, dictationId: D, currentUser: { id: 7 }, exposeLeaseController: true,
    });
    await editor.load();
    await editor.acquire();
    const saver = useCorrectionSave({
        editor, client: server.client, dictationId: D, lease: editor.leaseController, autosaveMs: 0,
        getPositionMs: () => 1000, ...(saveOptions || {}),
    });
    return { editor, saver };
}

describe('V8 save contract', () => {
    it('keeps at most one save in flight, batches newer text and confirms only the sent version', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        editor.setText('eins');
        server.gate = deferred();
        const first = saver.save();
        await tick();
        editor.setText('eins zwei');
        const second = await saver.save();
        expect(second).toMatchObject({ ok: false, state: 'saving' });
        expect(server.saves.length).toBe(1);
        server.gate.resolve();
        server.gate = null;
        expect(await first).toMatchObject({ ok: true, state: 'unsaved' });
        expect(editor.dirty.value).toBe(true);
        expect(saver.state.value).toBe('unsaved');
        expect(editor.text.value).toBe('eins zwei');
        expect(server.text).toBe('eins');
        // The next request is the newer text on the newly confirmed validator.
        await saver.save();
        expect(server.saves.length).toBe(2);
        expect(server.saves[1].ifMatch).toBe('"rev-1.g1"');
        expect(server.saves[1].body.expected_text_revision_id).toBe('rev-1');
        expect(server.text).toBe('eins zwei');
        expect(editor.dirty.value).toBe(false);
        expect(server.maxInflight).toBe(1);
        editor.dispose();
    });

    it('runs the batched text as the next request by itself when autosave is on', async () => {
        const server = makeServer();
        const timers = [];
        const { editor, saver } = await tab(server, {
            autosaveMs: 800, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
        });
        editor.setText('a');
        server.gate = deferred();
        const first = saver.save();
        await tick();
        editor.setText('ab');
        server.gate.resolve();
        server.gate = null;
        await first;
        expect(timers.at(-1).ms).toBe(0);
        await timers.at(-1).fn();
        await tick();
        expect(server.saves.length).toBe(2);
        expect(server.text).toBe('ab');
        expect(server.maxInflight).toBe(1);
        editor.dispose();
    });

    it('sends the validator read from the server with the generation this tab holds', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        expect(editor.etag.value).toBe('"rev-0.g0"'); // read before the lease was held
        editor.setText('neu');
        await saver.save();
        expect(server.saves[0].ifMatch).toBe('"rev-0.g1"');
        expect(editor.etag.value).toBe('"rev-1.g1"');
        editor.dispose();
    });

    it('reconciles a lost answer with the same key and body, without a second revision', async () => {
        const server = makeServer({ dropAck: true });
        const { editor, saver } = await tab(server);
        editor.setText('verloren');
        const lost = await saver.save();
        expect(lost).toMatchObject({ ok: false, state: 'unsaved', retryable: true });
        expect(saver.message.value).toBe(LABELS.timeout);
        expect(server.text).toBe('verloren'); // committed, the answer never arrived
        const again = await saver.save();
        expect(again.ok).toBe(true);
        expect(server.saves.length).toBe(2);
        expect(server.saves[1].key).toBe(server.saves[0].key);
        expect(server.saves[1].ifMatch).toBe(server.saves[0].ifMatch);
        expect(server.saves[1].body).toEqual(server.saves[0].body);
        expect(server.n).toBe(1);
        expect(editor.textRevisionId.value).toBe('rev-1');
        expect(saver.state.value).toBe('saved');
        editor.dispose();
    });

    it('with reconcileFirst the lost operation is settled before a new key is used', async () => {
        const server = makeServer({ dropAck: true });
        const { editor, saver } = await tab(server, { reconcileFirst: true });
        editor.setText('eins');
        await saver.save();
        editor.setText('eins zwei');
        const settled = await saver.save();
        expect(settled.ok).toBe(true);
        expect(server.saves[1].key).toBe(server.saves[0].key);
        expect(server.n).toBe(1);
        expect(editor.dirty.value).toBe(true); // the newer text is still dirty, never confirmed by the old answer
        expect(server.text).toBe('eins');
        await saver.save();
        expect(server.saves[2].key).not.toBe(server.saves[0].key);
        expect(server.saves[2].ifMatch).toBe('"rev-1.g1"');
        expect(server.text).toBe('eins zwei');
        expect(server.n).toBe(2);
        editor.dispose();
    });

    it('opens the conflict view without discarding anything and offers keep-mine, take-server and compare', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        editor.setText('Befund lokal');
        server.rev = 'rev-9'; server.text = 'Befund vom Server'; server.stateVersion = 8; // another writer committed
        const res = await saver.save();
        expect(res).toMatchObject({ ok: false, state: 'conflict' });
        expect(saver.message.value).toBe(LABELS.conflict);
        expect(editor.text.value).toBe('Befund lokal');
        expect(editor.dirty.value).toBe(true);
        expect(saver.conflict.value.mine).toBe('Befund lokal');
        expect(saver.conflict.value.server).toMatchObject({ text: 'Befund vom Server', revisionId: 'rev-9' });
        expect(saver.compare()).toEqual([
            { kind: 'mine', text: 'Befund lokal' },
            { kind: 'server', text: 'Befund vom Server' },
        ]);
        // Automatic saving stays paused while the conflict is open.
        const before = server.saves.length;
        await saver.save();
        saver.touch();
        expect(server.saves.length).toBe(before);
        expect(editor.text.value).toBe('Befund lokal');

        const kept = await saver.keepMine();
        expect(kept).toMatchObject({ ok: true, state: 'unsaved' });
        expect(editor.text.value).toBe('Befund lokal');
        expect(saver.conflict.value).toBe(null);
        const saved = await saver.save();
        expect(saved.ok).toBe(true);
        expect(server.saves.at(-1).ifMatch).toBe('"rev-9.g1"');
        expect(server.saves.at(-1).body.expected_state_version).toBe(8);
        expect(server.text).toBe('Befund lokal');
        editor.dispose();
    });

    it('take-server replaces the local text only by explicit choice', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        editor.setText('lokal');
        server.rev = 'rev-4'; server.text = 'serverseitig';
        await saver.save();
        expect(editor.text.value).toBe('lokal');
        const taken = await saver.takeServer();
        expect(taken).toMatchObject({ ok: true, state: 'saved' });
        expect(editor.text.value).toBe('serverseitig');
        expect(editor.dirty.value).toBe(false);
        expect(editor.textRevisionId.value).toBe('rev-4');
        expect(saver.conflict.value).toBe(null);
        editor.dispose();
    });

    it('keeps the conflict open and the text intact when the server text cannot be read', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        editor.setText('lokal');
        server.rev = 'rev-4';
        const original = server.client.request.getMockImplementation();
        server.client.request.mockImplementation(async (path, options) => (
            (!options || !options.method) && path === base + '/correction' ? bad('network', null) : original(path, options)));
        await saver.save();
        expect(saver.state.value).toBe('conflict');
        expect(saver.conflict.value.server).toBe(null);
        expect(saver.compare()).toBe(null);
        expect(await saver.takeServer()).toMatchObject({ ok: false, state: 'conflict' });
        expect(editor.text.value).toBe('lokal');
        editor.dispose();
    });

    it('a second tab taking the lease over makes the first tab save fail visibly', async () => {
        const server = makeServer();
        const tabA = await tab(server);
        tabA.editor.setText('Tab A');
        const tabB = await tab(server); // acquires again: generation 2
        expect(server.gen).toBe(2);
        const res = await tabA.saver.save();
        expect(res).toMatchObject({ ok: false, state: 'lost' });
        expect(tabA.saver.message.value).toBe(LABELS.leaseStale);
        expect(tabA.editor.lease.value.status).toBe('lost');
        expect(tabA.editor.canEdit.value).toBe(false);
        expect(tabA.editor.text.value).toBe('Tab A');
        expect(tabA.editor.dirty.value).toBe(true);
        expect(server.text).toBe('Befund');
        tabB.editor.setText('Tab B');
        expect((await tabB.saver.save()).ok).toBe(true);
        expect(server.text).toBe('Tab B');
        tabA.editor.dispose();
        tabB.editor.dispose();
    });

    it('shows a missing precondition (428) as unsaved and never as saved', async () => {
        const server = makeServer();
        const { editor, saver } = await tab(server);
        editor.setText('x');
        editor.etag.value = null;
        const res = await saver.save();
        expect(res).toMatchObject({ ok: false, state: 'unsaved' });
        expect(saver.message.value).toBe(LABELS.precondition);
        expect(editor.dirty.value).toBe(true);
        editor.dispose();
    });

    it('has a configurable debounce with the unchanged default', async () => {
        expect(AUTOSAVE_MS_DEFAULT).toBe(5000);
        const server = makeServer();
        const timers = [];
        const hooks = { setTimer: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimer: () => {} };
        const first = await tab(server, { ...hooks, autosaveMs: undefined });
        first.editor.setText('a');
        first.saver.touch();
        expect(timers.at(-1)).toBe(5000);
        const second = await tab(server, { ...hooks, autosaveMs: 1000 });
        second.editor.setText('b');
        second.saver.touch();
        expect(timers.at(-1)).toBe(1000);
        first.editor.dispose();
        second.editor.dispose();
    });

    it('diffLines marks local-only and server-only lines and drops nothing', () => {
        const rows = diffLines('a\nb\nc', 'a\nx\nc\nd');
        expect(rows.filter((r) => r.kind !== 'server').map((r) => r.text).join('\n')).toBe('a\nb\nc');
        expect(rows.filter((r) => r.kind !== 'mine').map((r) => r.text).join('\n')).toBe('a\nx\nc\nd');
    });
});

describe('V8 controller wiring', () => {
    const user = { id: 7 };

    it('exposes editor.leaseController and the +-5 s step only for contract v8', () => {
        const legacy = createCorrectionViewController({ client: makeServer().client, dictationId: D, currentUser: user });
        expect(legacy.editor.leaseController).toBeUndefined();
        expect(legacy.stepSeconds).toBe(3);
        const v8 = createCorrectionViewController({ client: makeServer().client, dictationId: D, currentUser: user, contract: 'v8' });
        expect(typeof v8.editor.leaseController.getState).toBe('function');
        expect(v8.stepSeconds).toBe(5);
        legacy.dispose();
        v8.dispose();
    });

    it('releases the held lease on the server when the next dictation is opened at once', async () => {
        // Model of api-client.js: a context switch aborts every request that is still in flight.
        const released = [];
        const aborted = [];
        let context = 'none';
        const inflight = new Set();
        const shared = {
            switchContext(next) {
                const label = next && next.dictationId ? next.dictationId : 'none';
                if (label === context) return;
                context = label;
                for (const run of inflight) run.abort();
                inflight.clear();
            },
            command: async () => ok({}),
            request: (path, options) => new Promise((resolve) => {
                const method = (options && options.method) || 'GET';
                const run = { abort: () => { aborted.push(path); resolve(bad('cancelled', null, 'CANCELLED')); } };
                inflight.add(run);
                setTimeout(() => {
                    if (!inflight.has(run)) return;
                    inflight.delete(run);
                    if (method === 'POST' && path.endsWith('/leases')) {
                        resolve(ok({ lease_id: 'L1', generation: 1, expires_at: new Date(Date.now() + 600000).toISOString() }));
                    } else if (method === 'POST' && path.endsWith('/leases/release')) {
                        released.push(path);
                        resolve(ok({}));
                    } else if (path.endsWith('/correction')) {
                        resolve(ok({ dictation_id: 'x', text_revision_id: null, text: '', links: [], state: null }));
                    } else {
                        resolve(ok({ items: [], revision_id: 'a1', segments: [] }));
                    }
                }, 5);
            }),
        };
        const one = createCorrectionViewController({ client: shared, dictationId: 'd1', currentUser: user, contract: 'v8' });
        await one.editor.acquire();
        expect(one.editor.lease.value.status).toBe('held');
        one.dispose();
        const two = createCorrectionViewController({ client: shared, dictationId: 'd2', currentUser: user, contract: 'v8' });
        await two.start();
        await tick();
        expect(released).toEqual(['/dictations/d1/leases/release']);
        expect(aborted.filter((p) => p.endsWith('/leases/release'))).toEqual([]);
        expect(two.ready.value).toBe(true);
        two.dispose();
    });

    it('close() keeps saving after a lost ACK until the newer text is on the server', async () => {
        const server = makeServer({ dropAck: true });
        const onClose = vi.fn();
        const c = createCorrectionViewController({ client: server.client, dictationId: D, currentUser: user, contract: 'v8', onClose, autosaveMs: 600000 });
        await c.start();
        await c.editor.acquire();
        c.editor.setText('eins');
        await c.save();
        c.editor.setText('eins zwei');
        server.dropAck = false;
        const closed = await c.close();
        expect(closed).toBe(true);
        expect(server.text).toBe('eins zwei');
        expect(c.editor.dirty.value).toBe(false);
        expect(onClose).toHaveBeenCalledTimes(1);
        c.dispose();
    });

    it('close() waits for an autosave follow-up that is already in flight instead of failing', async () => {
        const server = makeServer({ dropAck: true });
        const onClose = vi.fn();
        const c = createCorrectionViewController({ client: server.client, dictationId: D, currentUser: user, contract: 'v8', onClose, autosaveMs: 600000 });
        await c.start();
        await c.editor.acquire();
        c.editor.setText('eins');
        await c.save();
        c.editor.setText('eins zwei');
        server.dropAck = false;
        server.saveMs = 50; // real latency: the follow-up timer fires while the alignment GET is awaited
        server.getMs = 20;
        const closed = await c.close();
        expect(closed).toBe(true);
        expect(c.closeMessage.value).toBe('');
        expect(server.text).toBe('eins zwei');
        expect(c.editor.dirty.value).toBe(false);
        expect(server.maxInflight).toBe(1);
        expect(onClose).toHaveBeenCalledTimes(1);
        c.dispose();
    });

    it('keep-mine schedules the normal autosave for the kept text', async () => {
        const server = makeServer();
        const timers = [];
        const { editor, saver } = await tab(server, { autosaveMs: 1000, setTimer: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimer: () => {} });
        editor.setText('lokal');
        server.rev = 'rev-x';
        const lost = await saver.save();
        expect(lost.state).toBe('conflict');
        timers.length = 0;
        const kept = await saver.keepMine();
        expect(kept.ok).toBe(true);
        expect(timers).toEqual([1000]);
    });

    it('close() stays open and reports a failure when the text is still dirty after the bounded passes', async () => {
        const server = makeServer();
        const onClose = vi.fn();
        const c = createCorrectionViewController({ client: server.client, dictationId: D, currentUser: user, contract: 'v8', onClose, autosaveMs: 600000 });
        await c.start();
        await c.editor.acquire();
        c.editor.setText('eins');
        let n = 0;
        const realSave = c.saver.save;
        c.saver.save = async (...a) => { const res = await realSave(...a); n += 1; c.editor.setText('weiter ' + n); return res; };
        expect(await c.close()).toBe(false);
        expect(onClose).not.toHaveBeenCalled();
        expect(c.closeMessage.value).not.toBe('');
        c.dispose();
    });

    it('the mounted body runs the V8 contract: 5 s step and a lease controller', () => {
        const V = {
            ref: (value) => ({ value }), shallowRef: (value) => ({ value }),
            computed: (fn) => ({ get value() { return fn(); } }),
            onMounted: () => {}, onBeforeUnmount: () => {},
        };
        const out = CorrectionBody.setup({ client: makeServer().client, dictationId: D, currentUser: user, vue: V }, { emit: () => {} });
        expect(out.c.stepSeconds).toBe(5);
        expect(typeof out.c.editor.leaseController.getState).toBe('function');
        out.c.dispose();
    });

    it('scopedClient forwards the context switch at once when no release is pending', () => {
        const client = { request: vi.fn(), command: vi.fn(), switchContext: vi.fn() };
        const scoped = scopedClient(client);
        scoped.switchContext({ accountId: 1, dictationId: 'd9' });
        scoped.switchContext({ accountId: null, dictationId: null });
        expect(client.switchContext).toHaveBeenCalledTimes(1);
    });
});
