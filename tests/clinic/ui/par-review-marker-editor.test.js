import { describe, expect, it, vi } from 'vitest';

import { CorrectionView, createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (status) => ({ outcome: 'http', status, code: null, message: 'nein' });
const TEXT = 'Links ist rechts und Rechts ist links.';
const HITS = [
    { id: 'm1', label: 'Links', anchor_offset: 0 },
    { id: 'm2', label: 'rechts', anchor_offset: 10 },
];

function fakeClient(overrides = {}) {
    const calls = [];
    const routes = {
        'GET /dictations/d1/correction': ok({ dictation_id: 'd1', text_revision_id: 'r1', text: TEXT, state: { version: 1 }, links: [], markers: [] }),
        'GET /dictations/d1/audio-revisions/current': ok({ revision_id: 'a1', segments: [] }),
        'GET /dictations/d1/correction/review-markers': ok({ text_revision_id: 'r1', review_markers: HITS }),
        'POST /dictations/d1/leases': ok({ lease_id: 'l', generation: 3, expires_at: new Date(Date.now() + 600000).toISOString() }),
        'POST /dictations/d1/leases/release': ok({}),
        'POST /dictations/d1/correction/review-markers/m1/confirm': ok({ id: 'm1', text_revision_id: 'r1', confirmed: true }),
        'POST /dictations/d1/correction/review-markers/m2/confirm': ok({ id: 'm2', text_revision_id: 'r1', confirmed: true }),
        ...overrides,
    };
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            const key = Object.keys(routes).find((k) => k === `${method} ${path}`);
            return key ? routes[key] : (method === 'GET' && path.endsWith('/preferences/correction-mode') ? fail(404) : ok({ items: [] }));
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}

async function open(client) {
    const c = createCorrectionViewController({ client, dictationId: 'd1', currentUser: { id: 7 }, heartbeatMs: 600000, autosaveMs: 0 });
    await c.start();
    await c.editor.acquire();
    return c;
}

const states = (c) => c.reviewList.value.map((h) => [h.id, h.state]);

describe('review markers in the editor (PAR-VOC-004)', () => {
    it('loads the open hits with the revision and counts them as outstanding', async () => {
        const c = await open(fakeClient());
        expect(states(c)).toEqual([['m1', 'open'], ['m2', 'open']]);
        expect(c.reviewOutstanding.value).toBe(2);
        expect(c.reviewList.value[1].context).toContain('rechts');
        c.dispose();
    });

    it('steps through the hits with the keyboard model and wraps around', async () => {
        const c = await open(fakeClient());
        expect(c.reviewActive.value).toBe(0);
        expect(c.moveReview(1)).toBe(1);
        expect(c.moveReview(1)).toBe(0);
        expect(c.moveReview(-1)).toBe(1);
        c.dispose();
    });

    it('confirms one hit with revision, lease generation and an idempotency key', async () => {
        const client = fakeClient();
        const c = await open(client);
        expect(await c.confirmReview('m1')).toEqual({ ok: true });
        const call = client.calls.find((x) => x.method === 'POST' && x.path.endsWith('/m1/confirm'));
        expect(call.options.body).toEqual({ expected_text_revision_id: 'r1', lease_generation: 3 });
        expect(call.options.idempotencyKey).toBeTruthy();
        expect(states(c)).toEqual([['m1', 'confirmed'], ['m2', 'open']]);
        expect(c.reviewOutstanding.value).toBe(1);
        expect(await c.confirmReview('m1')).toMatchObject({ ok: false, code: 'noop' });
        c.dispose();
    });

    it('editing a confirmed hit makes it unresolved again; editing elsewhere does not', async () => {
        const c = await open(fakeClient());
        await c.confirmReview('m1');
        await c.confirmReview('m2');
        expect(c.reviewOutstanding.value).toBe(0);
        c.setText(TEXT.replace('und', 'oder'));
        expect(c.reviewOutstanding.value).toBe(0);
        c.setText(TEXT.replace('rechts', 'rechte'));
        expect(states(c)).toEqual([['m1', 'confirmed'], ['m2', 'open']]);
        expect(c.reviewOutstanding.value).toBe(1);
        c.setText(TEXT);
        expect(c.reviewOutstanding.value).toBe(0);
        c.dispose();
    });

    it('moves the span with preceding text edits and refuses to confirm an edited draft span', async () => {
        const client = fakeClient();
        const c = await open(client);
        c.setText('😀 ' + TEXT);
        expect(c.reviewList.value[1]).toMatchObject({ start: 13, end: 19, touched: false });
        c.setText(TEXT.replace('rechts', 'rechte'));
        expect(c.reviewList.value[1]).toMatchObject({ start: 10, end: 16, touched: true });
        expect(await c.confirmReview('m2')).toMatchObject({ ok: false, code: 'noop' });
        expect(client.calls.some((call) => call.path.endsWith('/m2/confirm'))).toBe(false);
        c.setText(TEXT.replace('rechts', 'rechtsseitig'));
        expect(c.reviewList.value[1]).toMatchObject({ start: 10, end: 22, touched: true });
        c.dispose();
    });

    it('does not attach marker offsets from a different server revision to the open text', async () => {
        const c = await open(fakeClient({
            'GET /dictations/d1/correction/review-markers': ok({ text_revision_id: 'r2', review_markers: HITS }),
        }));
        expect(c.reviewList.value).toEqual([]);
        expect(c.reviewMessage.value).not.toBe('');
        expect(c.reviewLoaded.value).toBe(false);
        c.dispose();
    });

    it('keeps the hit open and reports a failed confirmation; no confirm without lease', async () => {
        const client = fakeClient({ 'POST /dictations/d1/correction/review-markers/m1/confirm': fail(409) });
        const c = await open(client);
        expect(await c.confirmReview('m1')).toMatchObject({ ok: false, code: 'failed' });
        expect(c.reviewMessage.value).not.toBe('');
        expect(states(c)[0]).toEqual(['m1', 'open']);
        c.dispose();
        const readOnly = createCorrectionViewController({ client: fakeClient(), dictationId: 'd1', currentUser: { id: 7 }, autosaveMs: 0 });
        await readOnly.start();
        expect(await readOnly.confirmReview('m1')).toMatchObject({ ok: false, code: 'readOnly' });
        readOnly.dispose();
    });

    it('reloads the hits of the new revision after a save', async () => {
        const client = fakeClient({
            'POST /dictations/d1/correction/save': ok({ text_revision_id: 'r2', state_version: 2, audio_position_ms: 0 }),
        });
        const c = await open(client);
        await c.confirmReview('m1');
        const before = client.calls.filter((x) => x.path.endsWith('/review-markers')).length;
        c.editor.textRevisionId.value = 'r2';
        c.syncRevision();
        await Promise.resolve();
        expect(client.calls.filter((x) => x.path.endsWith('/review-markers')).length).toBe(before + 1);
        c.dispose();
    });

    it('mounts the hit list in the editor template with keyboard handlers and tokens only', () => {
        const template = JSON.stringify(Object.values(CorrectionView.components).map((x) => x.template));
        expect(template).toContain('review-hits');
        expect(template).toContain('onReviewKeydown');
        expect(template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
