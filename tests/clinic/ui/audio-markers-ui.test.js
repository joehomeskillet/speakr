import { describe, expect, it, vi } from 'vitest';
import {
    AudioMarkers, LABELS, registerAudioMarkers, useAudioMarkers,
} from '../../../static/js/clinic/audio-markers-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const AUDIO = '33333333-3333-4333-8333-333333333333';
const OLD_AUDIO = '44444444-4444-4444-8444-444444444444';
const base = '/dictations/' + D;
const TEXT = 'Befund unauffällig heute';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status) => ({ outcome, status, code: null, message: '' });

const marker = (id, extra) => ({
    id, marker_type: 'SECTION', position_ms: 1000, label: null, audio_revision_id: AUDIO, version: 1,
    deleted: false, anchor_revision_id: null, anchor_offset: null, events: [{ action: 'create' }], ...(extra || {}),
});

function mockEditor(extra) {
    return {
        text: { value: TEXT },
        audio: { value: { status: 'ready', revisionId: AUDIO } },
        lease: { value: { generation: 7, writable: true } },
        canEdit: { value: true },
        ...(extra || {}),
    };
}

function mockAlignment(selection) {
    const links = [];
    let at = 0;
    for (const m of TEXT.matchAll(/\S+/gu)) {
        links.push({ id: 'L' + links.length, start: m.index, end: m.index + m[0].length, audioStartMs: at, audioEndMs: at + 500 });
        at += 500;
    }
    return { status: { value: 'ready' }, links: { value: links }, selection: { value: selection || { start: 8, end: 8 } } };
}

function mockClient(routes) {
    const calls = [];
    const client = {
        request: vi.fn(async (path, options) => {
            const method = (options && options.method) || 'GET';
            calls.push({ method, path, options });
            const hit = routes[method + ' ' + path];
            const value = typeof hit === 'function' ? hit(options) : hit;
            return value || fail('UNKNOWN', 500);
        }),
    };
    return { client, calls };
}

function happy(extra) {
    return {
        ['GET ' + base + '/correction']: ok({ markers: [marker('M1'), marker('M0', { audio_revision_id: OLD_AUDIO, position_ms: 500 })] }),
        ...(extra || {}),
    };
}

async function make(routes, editor = mockEditor(), alignment = mockAlignment(), extra) {
    const mocked = mockClient(routes || happy());
    const seek = vi.fn();
    const api = useAudioMarkers({
        editor, alignment, client: mocked.client, dictationId: D, seek, getPositionMs: () => 2500,
        newKey: () => 'key-' + (mocked.calls.length + 1), ...(extra || {}),
    });
    await api.refresh();
    return { ...mocked, editor, alignment, seek, api };
}

describe('audio markers ui', () => {
    it('lists markers with revision ownership and keeps historical ones read-only', async () => {
        const { api, seek } = await make();
        expect(api.status.value).toBe('ready');
        expect(api.markers.value.map((m) => [m.id, m.ownership])).toEqual([['M0', 'historical'], ['M1', 'current']]);
        expect(api.markers.value[1].timeLabel).toBe('0:01.0');
        expect(await api.move('M0', 900)).toMatchObject({ ok: false, code: 'historical', message: LABELS.historicalRefused });
        expect(await api.remove('M0')).toMatchObject({ ok: false, code: 'historical' });
        expect(api.goTo('M0')).toMatchObject({ ok: false, code: 'historical' });
        expect(api.goTo('M1').ok).toBe(true);
        expect(seek).toHaveBeenCalledWith(1);
    });

    it('turns current markers historical after an audio edit but keeps them listed', async () => {
        const editor = mockEditor();
        const { api, calls } = await make(happy(), editor);
        editor.audio.value = { status: 'ready', revisionId: '55555555-5555-4555-8555-555555555555' };
        expect(api.markers.value.map((m) => m.ownership)).toEqual(['historical', 'historical']);
        expect(await api.move('M1', 2000)).toMatchObject({ ok: false, code: 'historical' });
        expect(calls.filter((c) => c.method === 'POST')).toEqual([]);
    });

    it('creates, moves and removes markers through the existing endpoints', async () => {
        const created = marker('M2', { marker_type: 'INDEX', position_ms: 2500, version: 1 });
        const moved = { ...marker('M1'), position_ms: 3000, version: 2 };
        const removed = { ...marker('M1'), deleted: true, version: 2 };
        const routes = happy({
            ['POST ' + base + '/markers']: ok(created),
            ['POST ' + base + '/markers/M1/move']: ok(moved),
            ['POST ' + base + '/markers/M1/delete']: ok(removed),
        });
        const { api, calls } = await make(routes);
        const res = await api.create({ type: 'INDEX' });
        expect(res).toMatchObject({ ok: true, message: LABELS.created });
        expect(calls.at(-1).options.body).toEqual({ position_ms: 2500, audio_revision_id: AUDIO, lease_generation: 7, marker_type: 'INDEX' });
        expect(calls.at(-1).options.idempotencyKey).toMatch(/^key-/);
        expect(api.markers.value.some((m) => m.id === 'M2' && m.type === 'INDEX')).toBe(true);

        expect((await api.move('M1', 3000)).ok).toBe(true);
        expect(calls.at(-1).options.body).toEqual({ position_ms: 3000, expected_version: 1, lease_generation: 7 });
        expect(api.markers.value.find((m) => m.id === 'M1').positionMs).toBe(3000);

        expect((await api.remove('M1')).ok).toBe(true);
        expect(calls.at(-1).options.body).toEqual({ expected_version: 2, lease_generation: 7 });
        expect(api.markers.value.some((m) => m.id === 'M1')).toBe(false);
        expect(await api.create({ positionMs: -1 })).toMatchObject({ ok: false, code: 'invalid' });
    });

    it('marks a phrase as an anchored INDEX marker at the audio time of its word', async () => {
        const routes = happy({ ['POST ' + base + '/markers']: ok(marker('M3', { marker_type: 'INDEX', position_ms: 500 })) });
        const { api, calls } = await make(routes, mockEditor(), mockAlignment({ start: 7, end: 18 }));
        expect((await api.markPhrase()).ok).toBe(true);
        expect(calls.at(-1).options.body).toEqual({
            position_ms: 500, audio_revision_id: AUDIO, lease_generation: 7, marker_type: 'INDEX',
            label: 'unauffällig', anchor_offset: 7,
        });
        const none = await make(routes, mockEditor(), mockAlignment({ start: 6, end: 6 }));
        expect(await none.api.markPhrase()).toMatchObject({ ok: false, code: 'unaligned' });
        const stale = mockAlignment();
        stale.status.value = 'stale';
        const staleRun = await make(routes, mockEditor(), stale);
        expect(await staleRun.api.markPhrase()).toMatchObject({ ok: false, code: 'stale' });
    });

    it('refuses writes without a lease and maps 403 to a visible state', async () => {
        const readOnly = await make(happy(), mockEditor({ canEdit: { value: false } }));
        expect(await readOnly.api.create()).toMatchObject({ ok: false, code: 'readOnly' });
        expect(readOnly.api.writable.value).toBe(false);
        const routes = happy({ ['POST ' + base + '/markers']: fail('permission', 403) });
        const { api } = await make(routes);
        expect(await api.create()).toMatchObject({ ok: false, code: 'denied', message: LABELS.denied });
        const locked = await make(happy({ ['POST ' + base + '/markers']: fail('conflict', 423) }));
        expect(await locked.api.create()).toMatchObject({ ok: false, code: 'locked' });
    });

    it('409 on a stale marker version reloads the list and reports stale', async () => {
        let reads = 0;
        const routes = happy({
            ['GET ' + base + '/correction']: () => { reads += 1; return ok({ markers: [marker('M1', { version: reads === 1 ? 1 : 2 })] }); },
            ['POST ' + base + '/markers/M1/move']: fail('conflict', 409),
        });
        const { api } = await make(routes);
        expect(await api.move('M1', 2000)).toMatchObject({ ok: false, code: 'stale', message: LABELS.stale });
        expect(reads).toBe(2);
        expect(api.markers.value[0].version).toBe(2);
    });

    it('reuses the idempotency key after a network failure and shows load errors with retry', async () => {
        let n = 0;
        const routes = happy({ ['POST ' + base + '/markers']: () => (++n === 1 ? fail('network', 0) : ok(marker('M4'))) });
        let keys = 0;
        const { api, calls } = await make(routes, mockEditor(), mockAlignment(), { newKey: () => 'key-' + (++keys) + '-abcdef' });
        expect(await api.create({ positionMs: 100 })).toMatchObject({ ok: false, code: 'failed' });
        expect((await api.create({ positionMs: 100 })).ok).toBe(true);
        const posts = calls.filter((c) => c.method === 'POST');
        expect(posts[0].options.idempotencyKey).toBe(posts[1].options.idempotencyKey);
        expect(keys).toBe(1);

        let loads = 0;
        const flaky = happy({ ['GET ' + base + '/correction']: () => (++loads === 1 ? fail('permission', 403) : ok({ markers: [] })) });
        const failing = await make(flaky);
        expect(failing.api.status.value).toBe('error');
        expect(failing.api.error.value.code).toBe('denied');
        expect((await failing.api.refresh()).ok).toBe(true);
        expect(failing.api.status.value).toBe('ready');
    });

    it('drops a superseded load after dispose', async () => {
        const { api } = await make();
        const pending = api.refresh();
        api.dispose();
        expect(await pending).toMatchObject({ ok: false, code: 'cancelled' });
        expect(api.markers.value).toEqual([]);
    });

    it('exports a component with labelled, keyboard-operable markup', () => {
        expect(AudioMarkers.name).toBe('ClinicAudioMarkers');
        expect(AudioMarkers.template).toContain('<button type="button"');
        expect(AudioMarkers.template).toContain('aria-live="polite"');
        expect(AudioMarkers.template).toContain('role="alert"');
        expect(AudioMarkers.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        const app = { component: vi.fn() };
        expect(registerAudioMarkers(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-audio-markers', AudioMarkers);
    });
});
