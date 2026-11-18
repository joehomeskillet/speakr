import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildPage, createHistoryComponent, useDictationHistory, PAGE_SIZE } from '../../../static/js/clinic/history-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const vue = {
    ref: (value) => ({ value }),
    computed: (get) => ({ get value() { return get(); } }),
    onMounted: () => {},
};
const ok = (events, id = D) => ({ outcome: 'ok', data: { dictation_id: id, events } });
const fake = (...responses) => {
    const calls = [];
    return { calls, request: async (path) => { calls.push(path); return responses.shift(); } };
};
const sample = [
    { kind: 'routing', id: 'r1', action: 'work_state_transferred', at: '2026-01-01T10:00:00+00:00', actor_label: 'A. Muster' },
    { kind: 'revision', id: 'v1', revision_kind: 'correction', parent_revision_id: null },
    { kind: 'release', id: 'x1', action: 'released', at: '2026-01-02T10:00:00+00:00', audio_revision_id: 'a1', transcript_revision_id: null },
    { kind: 'release', id: 'x2', action: 'superseded', at: '2026-01-03T10:00:00+00:00', release_id: 'x1' },
];

describe('history ui', () => {
    it('renders actor/time/version in input order with token surfaces', () => {
        const page = buildPage(sample, 1);
        expect(page.rows.map((r) => r.key)).toEqual(['routing:r1', 'revision:v1', 'release:x1', 'release:x2']);
        expect(page.rows[0]).toMatchObject({ actor: 'A. Muster', at: '2026-01-01T10:00:00+00:00', version: 'r1', kindLabel: 'Weiterleitung' });
        expect(page.rows[2].style.background).toMatch(/^var\(--bg-success-light\)$/);
        expect(page.rows[3].statusLabel).toBe('Warnung');
    });

    it('redacts unavailable linked objects without losing the event', () => {
        const rows = buildPage(sample, 1).rows;
        expect(rows[2].links).toEqual([
            { key: 'audio_revision_id', available: true, value: 'a1' },
            { key: 'transcript_revision_id', available: false, value: null },
        ]);
        const hidden = buildPage([{ kind: 'release', id: 'h', redacted: true, actor: 'Geheim', audio_revision_id: 'a' }], 1).rows[0];
        expect(hidden.actor).toBeNull();
        expect(hidden.links).toEqual([]);
        expect(hidden.key).toBe('release:h');
    });

    it('paginates in order and clamps the page', () => {
        const many = Array.from({ length: PAGE_SIZE + 3 }, (_, i) => ({ kind: 'routing', id: 'e' + i }));
        expect(buildPage(many, 2).rows.map((r) => r.index)).toEqual([20, 21, 22]);
        expect(buildPage(many, 99).page).toBe(2);
        expect(buildPage(many, -4).page).toBe(1);
        expect(buildPage([], 1)).toMatchObject({ pages: 1, total: 0, rows: [] });
    });

    it('loads for the authorized dictation and requests the history path', async () => {
        const client = fake(ok(sample));
        const h = useDictationHistory(client, D, vue);
        await h.load();
        expect(client.calls).toEqual(['/dictations/' + D + '/history']);
        expect(h.status.value).toBe('ready');
        expect(h.view.value.total).toBe(4);
    });

    it('drops rows when unauthorized', async () => {
        const client = fake(ok(sample), { outcome: 'permission' });
        const h = useDictationHistory(client, D, vue);
        await h.load();
        await h.load();
        expect(h.status.value).toBe('denied');
        expect(h.events.value).toEqual([]);
        expect(h.message.value).toBe('Keine Berechtigung für diesen Verlauf.');
    });

    it('treats conflict and foreign-dictation data as stale', async () => {
        const h = useDictationHistory(fake({ outcome: 'conflict' }, ok(sample, 'other')), D, vue);
        await h.load();
        expect(h.status.value).toBe('stale');
        await h.load();
        expect(h.status.value).toBe('stale');
        expect(h.events.value).toEqual([]);
    });

    it('retries after a network failure and ignores superseded loads', async () => {
        const h = useDictationHistory(fake({ outcome: 'network' }, ok(sample)), D, vue);
        await h.load();
        expect(h.status.value).toBe('error');
        await h.retry();
        expect(h.status.value).toBe('ready');
        const waiting = [];
        const slow = { request: () => new Promise((r) => { waiting.push(r); }) };
        const s = useDictationHistory(slow, D, vue);
        const first = s.load();
        const second = s.load();
        waiting[1](ok(sample));
        await second;
        waiting[0](ok([]));
        await first;
        expect(s.view.value.total).toBe(4);
    });

    it('page controls move within bounds', async () => {
        const many = Array.from({ length: PAGE_SIZE + 1 }, (_, i) => ({ kind: 'routing', id: 'e' + i }));
        const h = useDictationHistory(fake(ok(many)), D, vue);
        await h.load();
        h.prev();
        expect(h.page.value).toBe(1);
        h.next();
        h.next();
        expect(h.page.value).toBe(2);
    });

    it('component markup is labelled, keyboard reachable and colour-literal free', () => {
        const comp = createHistoryComponent(vue);
        expect(comp.template).toContain('aria-label');
        expect(comp.template).toContain('type="button"');
        expect(comp.template).toContain('tabindex="0"');
        expect(comp.template).toContain('role="alert"');
        const src = readFileSync(new URL('../../../static/js/clinic/history-ui.js', import.meta.url), 'utf8');
        expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(/);
        expect(src).not.toMatch(/localStorage|sessionStorage/);
    });
});
