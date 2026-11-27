import { describe, expect, it, vi } from 'vitest';
import { createCorrectionViewController } from '../../../static/js/clinic/compose-correction.js';
import { createDetailViewController } from '../../../static/js/clinic/compose-detail.js';

const D = 'd1';
const base = '/dictations/' + D;
const leases = base + '/leases';
const ok = (data) => ({ outcome: 'ok', status: 200, data });
const denied = { outcome: 'permission', status: 404 };

function sharedCase() {
    let holder = null;
    let generation = 0;
    let text = 'Letzte gespeicherte Fassung';
    let revision = 'r0';
    const calls = [];
    const server = { calls, failSave: false, text: () => text, holder: () => holder };
    server.client = (actor, mayAcquire = true) => ({
        switchContext() {},
        async request(path, options = {}) {
            const method = options.method || 'GET';
            calls.push([actor, method, path]);
            if (path === leases && method === 'GET') {
                return ok({ held: holder !== null, holder_user_id: holder, may_acquire: mayAcquire });
            }
            if (path === leases) {
                if (!mayAcquire) return denied;
                if (holder !== null && holder !== actor) return { outcome: 'conflict', status: 423 };
                if (holder === null) generation += 1;
                holder = actor;
                return ok({ lease_id: 'l' + generation, generation, expires_at: new Date(Date.now() + 60000).toISOString() });
            }
            if (path === leases + '/release') {
                expect(holder).toBe(actor);
                expect(options.body.generation).toBe(generation);
                holder = null;
                return ok({});
            }
            if (path === base + '/correction/save') {
                if (server.failSave) return { outcome: 'validation', status: 422 };
                expect(holder).toBe(actor);
                expect(options.body.lease_generation).toBe(generation);
                expect(options.ifMatch).toBe('"' + revision + '.g' + generation + '"');
                text = options.body.text;
                revision += 'x';
                return ok({ text_revision_id: revision, etag: '"' + revision + '.g' + generation + '"', state_version: 1 });
            }
            if (path === base + '/correction') {
                return ok({ text, text_revision_id: revision, etag: '"' + revision + '.g' + generation + '"' });
            }
            if (path === base + '/detail') return ok({ dictation_id: D });
            if (path === '/templates') return ok({ items: [] });
            return denied;
        },
    });
    return server;
}

function correction(server, id, options = {}) {
    return createCorrectionViewController({
        client: server.client(id), currentUser: { id }, dictationId: D,
        contract: 'v8', openMode: 'correction', pedal: false, autosaveMs: 0, ...options,
    });
}

function writes(server) {
    return server.calls.filter(([, method]) => method === 'POST');
}

function detail(server, id, options = {}) {
    return createDetailViewController({ client: server.client(id), currentUser: { id }, dictationId: D, ...options });
}

describe('PAR-WF-007 edit lease lifecycle', () => {
    it('acquires on writable open, excludes another writer, and saves before releasing and closing', async () => {
        const server = sharedCase();
        const a = correction(server, 1, { onClose: () => expect(server.holder()).toBeNull() });
        const b = correction(server, 2);
        try {
            await Promise.all([a.start(), a.start()]);
            expect(server.holder()).toBe(1);
            expect(a.editor.canEdit.value).toBe(true);
            await b.start();
            expect(b.editor.lease.value.status).toBe('foreign');
            expect(b.setText('Nicht erlaubt')).toBe(false);
            expect(a.setText('Neue gespeicherte Fassung')).toBe(true);
            expect(await a.close()).toBe(true);
            expect(writes(server)).toEqual([
                [1, 'POST', leases], [2, 'POST', leases],
                [1, 'POST', base + '/correction/save'], [1, 'POST', leases + '/release'],
            ]);
            b.dispose();
            const reopened = correction(server, 2);
            try {
                await reopened.start();
                expect(reopened.editor.canEdit.value).toBe(true);
                expect(reopened.editor.text.value).toBe('Neue gespeicherte Fassung');
            } finally {
                await reopened.close();
                reopened.dispose();
            }
        } finally {
            a.dispose();
            b.dispose();
        }
    });

    it('does not acquire on player/detail open or if write permission is not confirmed', async () => {
        const server = sharedCase();
        const player = correction(server, 1, { openMode: 'player' });
        const listener = correction(server, 2, { client: server.client(2, false) });
        const details = detail(server, 3);
        try {
            await Promise.all([player.start(), listener.start(), details.start()]);
            expect(player.editor.canEdit.value).toBe(false);
            expect(listener.editor.canEdit.value).toBe(false);
            expect(await player.close()).toBe(true);
            expect(await listener.close()).toBe(true);
            expect(writes(server)).toEqual([]);
        } finally {
            details.dispose();
            player.dispose();
            listener.dispose();
        }
    });

    it('hands off a detail lease only once and reuses an already-held lease', async () => {
        const server = sharedCase();
        const onOpenEditor = vi.fn();
        const c = detail(server, 1, { onOpenEditor });
        try {
            await c.start();
            await c.leaseRun.acquire();
            expect(await Promise.all([c.openEditor(), c.openEditor()])).toEqual([true, false]);
            expect(onOpenEditor.mock.calls).toEqual([[{ dictationId: D }]]);
            c.dispose();
            expect(writes(server)).toEqual([[1, 'POST', leases]]);
        } finally {
            c.dispose();
        }
    });

    it('keeps failed saves and their lease open without publishing unsaved text', async () => {
        const server = sharedCase();
        const onClose = vi.fn();
        const a = correction(server, 1, { onClose });
        try {
            await a.start();
            a.setText('Nicht gespeichert');
            server.failSave = true;
            expect(await a.close()).toBe(false);
            expect(onClose).not.toHaveBeenCalled();
            expect(server.holder()).toBe(1);
            expect(server.text()).toBe('Letzte gespeicherte Fassung');
            expect(a.editor.dirty.value).toBe(true);
            expect(writes(server)).toEqual([[1, 'POST', leases], [1, 'POST', base + '/correction/save']]);
        } finally {
            a.dispose();
        }
    });
});
