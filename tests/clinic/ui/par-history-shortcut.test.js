import { describe, expect, it, vi } from 'vitest';
import { createDetailViewController, DetailView } from '../../../static/js/clinic/compose-detail.js';
import { createWorklistViewController, WorklistView } from '../../../static/js/clinic/compose-worklist.js';
import { createHistoryComponent, isHistoryShortcut, translate } from '../../../static/js/clinic/history-ui.js';

const D = '11111111-1111-4111-8111-111111111111';
const DENIED = 'Keine Berechtigung für diesen Verlauf.';
const vue = {
    ref: (value) => ({ value }),
    shallowRef: (value) => ({ value }),
    reactive: (o) => o,
    computed: (get) => ({ get value() { return get(); } }),
    onMounted: () => {},
};

const assigned = { kind: 'routing', id: 'r1', action: 'work_state_transferred', at: '2026-01-01T10:00:00+00:00', actor_label: 'A. Muster' };
const ok = (data) => ({ outcome: 'ok', status: 200, data });
const historyOf = (events) => ok({ dictation_id: D, events });

function key(name, extra = {}) {
    return { key: name, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault: vi.fn(), ...extra };
}

function clientWith(responses) {
    const calls = [];
    const queue = responses.slice();
    return {
        calls,
        switchContext: vi.fn(),
        request: vi.fn(async (path) => {
            calls.push(path);
            const hit = queue.find((entry) => path.startsWith(entry.prefix));
            if (hit && hit.once) queue.splice(queue.indexOf(hit), 1);
            return hit ? (typeof hit.body === 'function' ? hit.body() : hit.body) : ok({ items: [] });
        }),
    };
}

function worklist(client) {
    return createWorklistViewController({
        client, currentUser: { id: 7, role: 'staff' }, storage: null, intervalMs: 0, heartbeatMs: 30000,
        fetchChanges: vi.fn(async () => ({ outcome: 'ok', data: { changes: [], next_cursor: 'c1', has_more: false } })),
    });
}

const row = {
    dictation_id: D, status: 'zu_schreiben', priority: 1, duration_seconds: 60, author_user_id: 7,
    revision: 3, etag: `"${D}:3"`, allowed_actions: [],
};

describe('PAR-WF-006 Strg+I', () => {
    it('recognises only an unmodified Ctrl or Cmd+I outside a typing target', () => {
        expect(isHistoryShortcut(key('i', { ctrlKey: true }))).toBe(true);
        expect(isHistoryShortcut(key('I', { metaKey: true }))).toBe(true);
        expect(isHistoryShortcut(key('i'))).toBe(false);
        expect(isHistoryShortcut(key('i', { ctrlKey: true, shiftKey: true }))).toBe(false);
        expect(isHistoryShortcut(key('i', { ctrlKey: true, altKey: true }))).toBe(false);
        expect(isHistoryShortcut(key('i', { ctrlKey: true, repeat: true }))).toBe(false);
        expect(isHistoryShortcut(key('k', { ctrlKey: true }))).toBe(false);
        expect(isHistoryShortcut(key('i', { ctrlKey: true, target: { tagName: 'INPUT' } }))).toBe(false);
        expect(isHistoryShortcut(key('i', { ctrlKey: true, target: { tagName: 'DIV', isContentEditable: true } }))).toBe(false);
    });

    it('opens the same Verlauf section as the menu for the selected dictation', async () => {
        const client = clientWith([
            { prefix: '/worklist/rows', body: ok({ items: [row], total: 1 }) },
            { prefix: '/worklist/layout', body: ok({ version: 1, layout: { version: 1, columns: [], sort: [], group_by: [] } }) },
            { prefix: '/worklist/filters', body: ok({ items: [] }) },
            { prefix: '/dictations/' + D + '/history', body: historyOf([assigned]) },
        ]);
        const c = worklist(client);
        await c.start();
        const event = key('i', { ctrlKey: true });
        expect(c.onHistoryKey(event)).toBe(false);
        expect(c.panels.history).toBe(false);
        c.select(c.worklist.rows.value[0]);
        expect(c.onHistoryKey(event)).toBe(true);
        expect(event.preventDefault).toHaveBeenCalled();
        expect(c.panels.history).toBe(true);
        expect(c.selectedId.value).toBe(D);

        const history = createHistoryComponent(vue);
        const bound = history.setup({ client: c.detailClient, dictationId: c.selectedId.value });
        await bound.load();
        expect(bound.view.value.rows[0].displayHeading).toBe('Arbeitsstand weitergeleitet');
        expect(WorklistView.template).toContain('data-testid="history-panel"');
        expect(WorklistView.template).toContain('c.onHistoryKey');
        c.dispose();
    });

    it('shows the same denial as the Verlauf section when view is refused', async () => {
        const client = clientWith([
            { prefix: '/worklist/rows', body: ok({ items: [row], total: 1 }) },
            { prefix: '/dictations/' + D + '/history', body: { outcome: 'permission', status: 403 } },
        ]);
        const c = worklist(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        c.openHistory();
        const history = createHistoryComponent(vue);
        const bound = history.setup({ client: c.detailClient, dictationId: D });
        await bound.load();
        expect(bound.status.value).toBe('denied');
        expect(bound.message.value).toBe(DENIED);
        expect(bound.message.value).toBe(translate('history_denied'));
        expect(history.template).toContain('{{ message }}');
        c.dispose();
    });

    it('shows a confirmed assignment the next time the Verlauf is opened', async () => {
        const events = [];
        const client = clientWith([
            { prefix: '/worklist/rows', body: ok({ items: [row], total: 1 }) },
            { prefix: '/dictations/' + D + '/history', body: () => historyOf(events.slice()) },
        ]);
        const c = worklist(client);
        await c.start();
        c.select(c.worklist.rows.value[0]);
        const history = createHistoryComponent(vue);
        const first = history.setup({ client: c.detailClient, dictationId: D });
        c.openHistory();
        await first.load();
        expect(first.view.value.total).toBe(0);

        events.push(assigned);
        c.panels.history = false;
        const again = history.setup({ client: c.detailClient, dictationId: D });
        expect(c.onHistoryKey(key('i', { ctrlKey: true }))).toBe(true);
        await again.load();
        expect(again.view.value.rows.map((entry) => entry.displayHeading)).toEqual(['Arbeitsstand weitergeleitet']);
        expect(again.view.value.rows[0].displayFields.some((field) => field.value === 'A. Muster')).toBe(true);
        c.dispose();
    });

    it('opens the detail Verlauf with the same key and the same denial', async () => {
        const detail = {
            dictation_id: D, revision: 1, author: { display_name: 'Dr. Synthetisch' }, staff_pools: [],
            recording_time: { at: '2026-11-20T09:00:00Z', source: 'recorded' }, duration_seconds: 75,
            priority: 1, allowed_actions: [], status_axes: { workflow: 'zu_schreiben' },
        };
        const client = clientWith([
            { prefix: '/dictations/' + D + '/detail', body: ok(detail) },
            { prefix: '/dictations/' + D + '/history', once: true, body: { outcome: 'permission', status: 403 } },
            { prefix: '/dictations/' + D + '/history', body: historyOf([assigned]) },
        ]);
        const c = createDetailViewController({ client, dictationId: D, currentUser: { id: 7 }, vue });
        await c.start();
        const event = key('i', { ctrlKey: true });
        expect(c.onHistoryKey(event)).toBe(true);
        expect(c.historyOpen.value).toBe(true);
        const history = createHistoryComponent(vue);
        const denied = history.setup({ client: c.detailClient, dictationId: D });
        await denied.load();
        expect(denied.message.value).toBe(DENIED);
        c.historyOpen.value = false;
        const again = history.setup({ client: c.detailClient, dictationId: D });
        c.onHistoryKey(key('I', { metaKey: true }));
        await again.load();
        expect(again.status.value).toBe('ready');
        expect(again.view.value.rows[0].kindLabel).toBe('Weiterleitung');
        expect(DetailView.components.DetailBody.template).toContain('onHistoryKey');
        expect(DetailView.components.DetailBody.template).toContain('c.historyOpen.value');
        c.dispose();
    });
});
