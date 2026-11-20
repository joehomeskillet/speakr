import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { RecordingsView, registerRecordingsView } from '../../../static/js/clinic/compose-recordings.js';

describe('recordings view component', () => {
    const tpl = RecordingsView.template;

    it('declares the open emit and German labelled controls', () => {
        expect(RecordingsView.emits).toEqual(['open']);
        for (const text of ['t.author', 't.pool', 't.lease', 't.loadMore', 't.apply', 't.reset', 't.authorId', 't.poolId', 't.from', 't.to']) {
            expect(tpl).toContain(text);
        }
        expect(tpl).toContain('data-testid="load-more"');
        expect(tpl).toContain('@click.prevent="c.open(r)"');
    });

    it('renders the three role cells separately and the fallback marker in table and cards', () => {
        for (const cell of ['data-cell="author"', 'data-cell="pool"', 'data-cell="lease"']) expect(tpl).toContain(cell);
        expect(tpl.match(/data-testid="fallback"/g)).toHaveLength(2);
        expect(tpl).toContain('r.fallbackTip');
        expect(tpl).toContain('md:hidden');
        expect(tpl).toContain('hidden md:table');
    });

    it('uses no hard colours, patient fields, storage or URL state', () => {
        expect(tpl).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(tpl).not.toMatch(/patient|\br\.(title|notes|name)\b|item\.(title|notes)/i);
        for (const file of ['compose-recordings.js', 'recordings-ui.js']) {
            const src = readFileSync('static/js/clinic/' + file, 'utf8');
            expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB|location\.|history\./);
        }
    });

    it('setup forwards the open payload and restores scroll after mount', async () => {
        const mounted = [];
        const vue = {
            ref: (v) => ({ value: v }), reactive: (o) => o,
            onMounted: (fn) => mounted.push(fn), onBeforeUnmount: () => {}, nextTick: () => Promise.resolve(),
        };
        const client = { request: vi.fn(async () => ({ outcome: 'ok', data: { items: [{ dictation_id: 5, workflow_status: 'zu_schreiben', day: '2026-11-19', time_source: 'recorded', sort_time: '2026-11-19T08:00:00Z' }], next_cursor: null } })) };
        const emit = vi.fn();
        const state = RecordingsView.setup({ client, currentUser: { id: 1, role: 'typist' }, vue, memory: { snapshot: null }, autoStart: true }, { emit });
        state.scroller.value = { scrollTop: 0 };
        state.c.setScroll(0);
        await mounted[0]();
        state.c.open(state.c.state.rows[0]);
        expect(emit).toHaveBeenCalledWith('open', { dictationId: '5', mode: 'player' });
        state.onScroll({ target: { scrollTop: 42 } });
        expect(state.c.scrollTop()).toBe(42);
    });

    it('registers the element name used by the page', () => {
        const app = { component: vi.fn() };
        expect(registerRecordingsView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-recordings-view', RecordingsView);
    });
});
