import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    canonicalSelection, createVocabularyStore, handleKey, label, statusSurface, VOCABULARY_TEMPLATE,
} from '../../../static/js/clinic/vocabulary-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const bad = (outcome) => ({ outcome, message: 'm' });
const ENTRY = (version) => ({ id: 'e1', written: 'Metformin', spoken: null, status: 'active', version });

function setup(writeResults, readResults = []) {
    const client = {
        request: vi.fn(async (path, opts) => {
            if (opts && opts.method === 'POST') return writeResults.shift() || ok({});
            return readResults.shift() || ok({ entries: [ENTRY('v1')], content: 'Metformin' });
        }),
    };
    const writes = () => client.request.mock.calls.filter((c) => c[1] && c[1].method === 'POST');
    const reads = () => client.request.mock.calls.filter((c) => !(c[1] && c[1].method === 'POST'));
    return { client, writes, reads, store: createVocabularyStore(client) };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/vocabulary-ui.js', import.meta.url), 'utf8');

describe('vocabulary ui', () => {
    it('searches the own list and shows the current scope', async () => {
        const { store, reads } = setup([]);
        await store.search({ q: 'met', mode: 'prefix' });
        expect(reads()[0][0]).toBe('/vocabulary/search?q=met&mode=prefix&status=active&scope=user');
        expect(store.state.entries.map((e) => e.written)).toEqual(['Metformin']);
        expect(store.state.scope).toEqual({ kind: 'user', poolId: null });
        expect(VOCABULARY_TEMPLATE).toContain('state.scope.kind');
    });

    it('switches to a pool scope', async () => {
        const { store, reads } = setup([]);
        await store.setScope('pool', 'p1');
        expect(reads()[0][0]).toContain('scope=pool&pool_id=p1');
        expect(await store.setScope('pool', '')).toBeNull();
    });

    it('submits read-only pool additions and suppressions for review without editing the list', async () => {
        const { client, store, writes } = setup([], [ok({ entries: [ENTRY('v1')], can_write: false })]);
        client.command = vi.fn().mockResolvedValue(ok({ status: 'committed' }));
        await store.setScope('pool', 'p1');
        await store.propose('  Neue   Vokabel  ');
        await store.proposeSuppression('e1');
        expect(client.command.mock.calls.map(([command]) => command.payload)).toEqual([
            { scope_type: 'author_pool', scope_id: 'p1', operation: 'add', written: 'Neue Vokabel' },
            { scope_type: 'author_pool', scope_id: 'p1', operation: 'negative', written: 'Metformin' },
        ]);
        expect(client.command.mock.calls[0][0]).toMatchObject({
            command: 'propose_vocabulary_change', request_id: expect.any(String), expected_revision: 0,
        });
        expect(writes()).toHaveLength(0);
        expect(store.state.entries).toEqual([ENTRY('v1')]);
        expect(store.state.message).toContain('zur Prüfung');
    });

    it('reuses the pending proposal receipt after an unknown response and never in another scope', async () => {
        const { client, store } = setup([], [ok({ entries: [], can_write: false })]);
        client.command = vi.fn().mockResolvedValue(bad('UNKNOWN'));
        await store.setScope('pool', 'p1');
        await store.propose('Neues Wort');
        await store.propose('Anderes Wort');
        await store.retry();
        expect(client.command.mock.calls).toHaveLength(2);
        expect(client.command.mock.calls[1]).toEqual(client.command.mock.calls[0]);
        await store.setScope('user');
        await store.retry();
        await store.propose('Andere Liste');
        expect(client.command.mock.calls).toHaveLength(2);
    });

    it('keeps the selected pool entries when the previous personal search completes late', async () => {
        let finishPersonal;
        const personal = new Promise((resolve) => { finishPersonal = resolve; });
        const client = { request: vi.fn().mockReturnValueOnce(personal).mockResolvedValueOnce(ok({ entries: [] })) };
        const store = createVocabularyStore(client);
        const first = store.search();
        await store.setScope('pool', 'p1');
        finishPersonal(ok({ entries: [ENTRY('v1')] }));
        await first;
        expect(store.state.scope.poolId).toBe('p1');
        expect(store.state.entries).toEqual([]);
    });

    it('adds only an explicit, bounded selection after a membership recheck', async () => {
        const { store, writes, reads } = setup([]);
        await store.setScope('pool', 'p1');
        const before = reads().length;
        await store.addSelection('  Herz   insuffizienz ');
        expect(reads().length).toBeGreaterThan(before);
        const [path, opts] = writes()[0];
        expect(path).toBe('/vocabulary/additions');
        expect(opts.body).toEqual({ scope: 'pool', pool_id: 'p1', written: 'Herz insuffizienz' });
        expect(typeof opts.idempotencyKey).toBe('string');
        expect(await store.addSelection('x'.repeat(256))).toBeNull();
        expect(await store.addSelection('a\u0000b')).toBeNull();
        expect(await store.addSelection('   ')).toBeNull();
        expect(writes()).toHaveLength(1);
        expect(canonicalSelection(12)).toBeNull();
    });

    it('unauthorized pool recheck sends no mutation', async () => {
        const { store, writes } = setup([], [ok({ entries: [] }), bad('permission')]);
        await store.setScope('pool', 'p1');
        await store.addSelection('Wort');
        expect(writes()).toHaveLength(0);
        expect(store.state.status).toBe('denied');
        expect(store.state.entries).toEqual([]);
    });

    it('suppresses and reactivates with the entry version', async () => {
        const { store, writes } = setup([]);
        await store.search();
        await store.suppress('e1');
        expect(writes()[0][0]).toBe('/vocabulary/entries/e1/suppress');
        expect(writes()[0][1].body).toEqual({ expected_version: 'v1' });
        await store.reactivate('e1');
        expect(writes()[1][0]).toBe('/vocabulary/entries/e1/reactivate');
        expect(await store.suppress('missing')).toBeNull();
    });

    it('keeps decoder unavailability distinct from the saved list status', async () => {
        const active = ENTRY('v1');
        const suppressed = { ...active, version: 'v2', status: 'suppressed' };
        const reactivated = ENTRY('v3');
        const { store, reads, writes } = setup([], [
            ok({ entries: [active] }), ok({ entries: [] }), ok({ entries: [suppressed] }),
            ok({ entries: [] }), ok({ entries: [reactivated] }),
        ]);
        await store.search();
        await store.suppress('e1');
        expect(store.state.entries).toEqual([]);
        await store.search({ filter: 'suppressed' });
        expect(reads()[2][0]).toContain('status=suppressed');
        expect(store.state.entries).toEqual([suppressed]);
        await store.reactivate('e1');
        expect(writes()[1][1].body).toEqual({ expected_version: 'v2' });
        expect(store.state.entries).toEqual([]);
        await store.search({ filter: 'active' });
        expect(store.state.entries).toEqual([reactivated]);
        expect(VOCABULARY_TEMPLATE).toContain('<p id="voc-asr-suppression" class="note">');
        expect(VOCABULARY_TEMPLATE).toContain("label('clinic.vocabulary.asrSuppressionUnavailable')");
        expect(VOCABULARY_TEMPLATE).toContain("label('clinic.vocabulary.asrSuppressionDetail')");
        expect(label('clinic.vocabulary.asrSuppressionUnavailable')).toBe('ASR-Wortunterdrückung nicht verfügbar');
        expect(label('clinic.vocabulary.asrSuppressionDetail')).toContain('weiterhin erkennen');
    });

    it('stale version keeps the newer server state and does not retry', async () => {
        const { store, writes } = setup([bad('conflict')], [ok({ entries: [ENTRY('v1')] }), ok({ entries: [ENTRY('v2')] })]);
        await store.search();
        await store.suppress('e1');
        expect(store.state.status).toBe('stale');
        expect(store.state.entries[0].version).toBe('v2');
        expect(store.state.failedWrite).toBeNull();
        expect(writes()).toHaveLength(1);
        expect(statusSurface(store.state).status).toBe('warning');
    });

    it('unauthorized write is denied without retry state', async () => {
        const { store } = setup([bad('permission')]);
        await store.search();
        await store.suppress('e1');
        expect(store.state.status).toBe('denied');
        expect(store.state.failedWrite).toBeNull();
    });

    it('network failure retries with the same key and body', async () => {
        const { store, writes } = setup([bad('network'), ok({})]);
        await store.addSelection('Wort');
        expect(store.state.failedWrite).not.toBeNull();
        await store.retry();
        const [first, second] = writes();
        expect(second[1].idempotencyKey).toBe(first[1].idempotencyKey);
        expect(second[1].body).toEqual(first[1].body);
        expect(store.state.failedWrite).toBeNull();
        expect(store.state.status).toBe('ready');
    });

    it('imports and exports word lists in memory', async () => {
        const { store, writes, reads } = setup([ok({ added: 2 })]);
        await store.importText('a\nb', 'skip');
        expect(writes()[0][0]).toBe('/vocabulary/import');
        expect(writes()[0][1].body).toEqual({ scope: 'user', content: 'a\nb', duplicate_rule: 'skip' });
        expect(store.state.importResult).toEqual({ added: 2 });
        expect(await store.importText('  ')).toBeNull();
        await store.exportList(true);
        expect(reads().pop()[0]).toBe('/vocabulary/export?scope=effective');
        expect(store.state.exportText).toBe('Metformin');
    });

    it('previews legacy bytes without refreshing the list and imports with the same options', async () => {
        const preview = { added: 1, updated: 0, skipped: 1, errors: [{ line: 3, reason: 'invalid_encoding' }], dry_run: true };
        const { store, writes, reads } = setup([ok(preview), ok({ ...preview, dry_run: false })]);
        const file = new Blob([Uint8Array.of(0xc4, 0x72, 0x7a, 0x74)]);
        file.name = 'Wörter.TXT';
        await store.importFile(file, 'cp1252', 'skip', true);
        const body = { scope: 'user', content_base64: 'xHJ6dA==', encoding: 'cp1252', duplicate_rule: 'skip' };
        expect(writes()[0][1].body).toEqual({ ...body, dry_run: true });
        expect(store.state.importResult).toEqual(preview);
        expect(reads()).toHaveLength(0);

        await store.importFile(file, 'cp1252', 'skip', false);
        expect(writes()[1][1].body).toEqual(body);
        expect(store.state.importResult).toEqual({ ...preview, dry_run: false });
        expect(reads()).toHaveLength(1);
    });

    it('does not queue failed previews or send payloads exceeding the API body limit', async () => {
        const { store, writes } = setup([bad('network')]);
        await store.importText('Ärztin', 'replace', true);
        expect(store.state.failedWrite).toBeNull();
        expect((await store.importText('ä'.repeat(131072), 'replace')).outcome).toBe('validation');
        expect(writes()).toHaveLength(1);
    });

    it('does not expose an export or an import report from a previous list', async () => {
        let finishExport;
        let finishImport;
        const client = { request: vi.fn((path) => {
            if (path.startsWith('/vocabulary/export')) return new Promise((resolve) => { finishExport = resolve; });
            if (path === '/vocabulary/import') return new Promise((resolve) => { finishImport = resolve; });
            return Promise.resolve(ok({ entries: [] }));
        }) };
        const store = createVocabularyStore(client);
        const download = store.exportList(false);
        const importJob = store.importText('Ärztin');
        await Promise.resolve();
        await store.setScope('pool', 'p1');
        finishExport(ok({ content: 'Ärztin' }));
        finishImport(ok({ added: 1 }));
        expect(await download).toBeNull();
        expect(await importJob).toBeNull();
        expect(store.state.exportText).toBe('');
        expect(store.state.importResult).toBeNull();
    });

    it('has german labels, accessible markup, keyboard search, no hex colours and no storage', () => {
        expect(label('clinic.vocabulary.addSelection')).toContain('hinzufügen');
        expect(VOCABULARY_TEMPLATE).toContain('aria-live="polite"');
        expect(VOCABULARY_TEMPLATE).toContain('<label for="voc-q">');
        expect(VOCABULARY_TEMPLATE).toContain('scope="col"');
        const { store } = setup([]);
        const search = vi.spyOn(store, 'search');
        expect(handleKey(store, { key: 'Enter', target: { dataset: { role: 'query' } } })).toBe(true);
        expect(search).toHaveBeenCalled();
        expect(handleKey(store, { key: 'a', target: { dataset: {} } })).toBe(false);
        expect(source()).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source()).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    });
});
