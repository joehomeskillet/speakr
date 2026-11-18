import { describe, expect, it, vi } from 'vitest';
import {
    DictationPropertiesComponent,
    PRIORITY_OPTIONS,
    TEXT,
    VALIDATE_PATH,
    createDictationPropertiesModel,
    describeDeadline,
    localErrors,
} from '../../../static/js/clinic/dictation-properties-ui.js';

const SETS = [
    { setId: 's1', label: 'Kategorie', version: 3, required: true, values: [{ value: 'a', label: 'A' }] },
];

function fill(model, over) {
    Object.assign(model.state.fields, {
        title: 'Brief Muster', poolId: 'pool-1', priority: 1, source: 'browser',
        selections: { s1: 'a' }, deadlineDate: '', deadlineTime: '',
    }, over || {});
}

function client(outcome, extra) {
    return { request: vi.fn(async () => Object.assign({ outcome: outcome }, extra || {})) };
}

describe('dictation properties ui', () => {
    it('offers exact priorities 0/1/128 in German', () => {
        expect(PRIORITY_OPTIONS.map((o) => [o.label, o.value])).toEqual([
            ['Bei Gelegenheit', 0], ['Normal', 1], ['Dringend', 128],
        ]);
    });

    it('shows Europe/Zurich end of day when time is omitted', () => {
        expect(describeDeadline('2026-12-01', '')).toBe('2026-12-01 23:59:59 Europe/Zurich');
        expect(describeDeadline('2026-12-01', '08:30')).toBe('2026-12-01 08:30:00 Europe/Zurich');
        expect(describeDeadline('', '')).toBe('');
    });

    it('reports required category, recipient and deadline errors', () => {
        const f = { title: 'x', poolId: '', priority: 1, selections: {}, deadlineDate: '', deadlineTime: '09:00' };
        const errors = localErrors(f, SETS);
        expect(errors.poolId).toBe(TEXT.missingRecipient);
        expect(errors['category:s1']).toBe(TEXT.missingCategory);
        expect(errors.deadlineDate).toBe(TEXT.missingDeadlineDate);
    });

    it('does not call the server while local errors exist', async () => {
        const c = client('ok');
        const m = createDictationPropertiesModel(c, { categorySets: SETS });
        fill(m, { poolId: '' });
        await m.validate();
        expect(c.request).not.toHaveBeenCalled();
        expect(m.state.status).toBe('invalid');
    });

    it('posts the body with date and optional time via the client', async () => {
        const c = client('ok', { data: { ok: true } });
        const m = createDictationPropertiesModel(c, { categorySets: SETS });
        fill(m, { priority: 128, deadlineDate: '2026-12-01' });
        await m.validate();
        const [path, opts] = c.request.mock.calls[0];
        expect(path).toBe(VALIDATE_PATH);
        expect(opts.method).toBe('POST');
        expect(opts.body).toMatchObject({
            priority: 128, pool_id: 'pool-1', deadline_date: '2026-12-01',
            categories: [{ set_id: 's1', version: 3, value: 'a' }],
        });
        expect(opts.body).not.toHaveProperty('deadline_time');
        expect(m.state.valid).toBe(true);
    });

    it('keeps unsaved fields on a stale-version conflict', async () => {
        const m = createDictationPropertiesModel(client('conflict'), { categorySets: SETS });
        fill(m, { deadlineDate: '2026-12-01', deadlineTime: '10:00' });
        await m.validate();
        expect(m.state.conflict).toBe(true);
        expect(m.state.message).toBe(TEXT.conflict);
        expect(m.state.fields.title).toBe('Brief Muster');
        expect(m.state.fields.deadlineTime).toBe('10:00');
        m.keepMine();
        expect(m.state.conflict).toBe(false);
        expect(m.state.fields.title).toBe('Brief Muster');
    });

    it('handles unauthorized without losing fields or colour invention', async () => {
        const m = createDictationPropertiesModel(client('permission'), { categorySets: SETS });
        fill(m);
        await m.validate();
        expect(m.state.status).toBe('denied');
        expect(m.state.fields.title).toBe('Brief Muster');
        expect(m.surface().tokens.border).toBe('--border-danger');
    });

    it('offers retry after a network failure and then succeeds', async () => {
        const c = { request: vi.fn()
            .mockResolvedValueOnce({ outcome: 'network' })
            .mockResolvedValueOnce({ outcome: 'ok', data: {} }) };
        const m = createDictationPropertiesModel(c, { categorySets: SETS });
        fill(m);
        await m.validate();
        expect(m.state.status).toBe('retry');
        await m.retry();
        expect(m.state.status).toBe('valid');
        expect(c.request).toHaveBeenCalledTimes(2);
    });

    it('stores nothing in browser storage', async () => {
        const writes = [];
        global.localStorage = { setItem: (...a) => writes.push(a) };
        global.sessionStorage = { setItem: (...a) => writes.push(a) };
        const m = createDictationPropertiesModel(client('ok'), { categorySets: SETS });
        fill(m);
        await m.validate();
        expect(writes).toEqual([]);
    });

    it('exports a component with labelled, keyboard-operable markup', () => {
        const Vue = { reactive: (o) => o, computed: (fn) => ({ get value() { return fn(); } }) };
        const comp = DictationPropertiesComponent(Vue);
        expect(comp.template).toContain('<form');
        expect(comp.template).toContain('type="submit"');
        expect(comp.template).toContain('role="alert"');
        expect(comp.template).toContain('<legend>');
        expect(comp.template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        const ctx = comp.setup({ client: client('ok'), categorySets: SETS });
        expect(ctx.priorities).toBe(PRIORITY_OPTIONS);
    });
});
