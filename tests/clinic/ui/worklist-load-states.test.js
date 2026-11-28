import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, it, vi } from 'vitest';
import { WorklistView } from '../../../static/js/clinic/compose-worklist.js';

const vueSource = new URL('../../../static/vendor/js/vue.global.js', import.meta.url);
const Vue = runInNewContext(readFileSync(vueSource, 'utf8') + '\nVue', { console });
const node = (text = '') => ({ text, children: [], props: {}, style: {} });
function remove(item) {
    const { parent } = item;
    parent.children.splice(parent.children.indexOf(item), 1);
    item.parent = null;
}

function insert(item, parent, before) {
    if (item.parent) remove(item);
    item.parent = parent;
    const index = before ? parent.children.indexOf(before) : parent.children.length;
    parent.children.splice(index, 0, item);
}

const renderer = Vue.createRenderer({
    createElement: () => node(),
    createText: node,
    createComment: () => node(),
    setText: (item, text) => { item.text = text; },
    setElementText: (item, text) => { item.text = text; item.children = []; },
    patchProp: (item, name, _previous, value) => { item.props[name] = value; },
    insert,
    remove,
    parentNode: (item) => item.parent,
    nextSibling: (item) => item.parent.children[item.parent.children.indexOf(item) + 1] || null,
    insertStaticContent(text, parent, before) {
        const item = node(text);
        insert(item, parent, before);
        return [item, item];
    },
});

function findAll(item, predicate) {
    return [...(predicate(item) ? [item] : []), ...item.children.flatMap((child) => findAll(child, predicate))];
}

const textOf = (item) => item.text + item.children.map(textOf).join('');
const ok = (data) => ({ outcome: 'ok', data });

it('renders loading, retry, an empty team list and the unchanged counts for rows', async () => {
    let rejectLoad;
    const load = vi.fn()
        .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectLoad = reject; }))
        .mockResolvedValueOnce(ok({ items: [], total: 0 }))
        .mockResolvedValueOnce(ok({ items: [
            { dictation_id: 'test-1', status: 'zu_schreiben', duration_seconds: 30 },
        ], total: 1 }));
    const client = {
        request: vi.fn((path) => {
            if (path === '/worklist/rows') return load();
            if (path === '/pools') return Promise.resolve(ok({ staff_pools: [
                { name: 'Schreibdienst Nord', member_user_ids: [7] },
                { name: 'Schreibdienst West', member_user_ids: ['7'] },
                { name: 'Fremdes Team', member_user_ids: [8] },
            ] }));
            return Promise.resolve(ok({ items: [] }));
        }),
        switchContext: vi.fn(),
    };
    const root = node();
    const app = renderer.createApp({
        ...WorklistView,
        render: Vue.compile(WorklistView.template, { decodeEntities: (raw) => raw }),
        components: {
            WorklistFiltersPanel: {
                inheritAttrs: false,
                setup: (_props, { slots }) => () => slots.toolbar?.().slice(0, 1),
            },
        },
    }, { client, currentUser: { id: '7' }, vue: Vue, intervalMs: 0 });
    const byTestId = (id) => findAll(root, (item) => item.props['data-testid'] === id)[0];
    const assertNoCounts = () => {
        expect(Boolean(byTestId('result-count'))).toBe(false);
        expect(Boolean(byTestId('status-totals'))).toBe(false);
        expect(findAll(root, (item) => item.props.class === 'num-circle')).toHaveLength(0);
    };
    app.mount(root);
    try {
        assertNoCounts();
        expect(textOf(byTestId('loading-state'))).toContain('Wird geladen …');

        rejectLoad(new Error('offline'));
        await vi.waitFor(() => expect(byTestId('load-error')).toBeDefined());
        assertNoCounts();
        expect(textOf(byTestId('load-error'))).toContain('Laden fehlgeschlagen');
        const retry = findAll(byTestId('load-error'), (item) => item.props.onClick)[0];
        expect(textOf(retry)).toBe('Erneut versuchen');
        expect(byTestId('refresh-button')).toBeUndefined();
        expect(byTestId('empty-state')).toBeUndefined();

        retry.props.onClick();
        await vi.waitFor(() => expect(textOf(byTestId('empty-state'))).toContain('Schreibdienst Nord, Schreibdienst West'));
        expect(load).toHaveBeenCalledTimes(2);
        expect(textOf(byTestId('empty-state'))).toContain('Keine offenen Diktate in deinen Schreibteams');
        expect(textOf(byTestId('empty-state'))).not.toContain('Fremdes Team');
        const [recordings] = findAll(byTestId('empty-state'), (item) => item.props.href === '/#/aufnahmen');
        expect(textOf(recordings)).toBe('Aufnahmen');

        byTestId('refresh-button').props.onClick();
        await vi.waitFor(() => expect(textOf(byTestId('result-count'))).toBe('1 Eintrag'));
        expect(findAll(byTestId('status-totals'), (item) => item.props['data-kpi']).map(textOf)).toEqual(['1', '1', '0', '0']);
        expect(byTestId('empty-state')).toBeUndefined();
    } finally {
        app.unmount();
    }
});
