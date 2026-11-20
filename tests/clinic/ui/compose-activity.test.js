import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    ACTIVITY_TEMPLATE, ActivityView, LABELS, STATISTICS_PATH, buildStatisticsQuery, buildStatisticsView,
    createActivityController, registerActivityView,
} from '../../../static/js/clinic/compose-activity.js';

const ok = (data) => ({ outcome: 'ok', data });
const D = '11111111-1111-4111-8111-111111111111';

// Same shape as StatisticsResult.as_dict() in src/clinic/statistics.py.
const AGGREGATE = {
    time_basis: 'created',
    zone: 'Europe/Zurich',
    window: '[start, end)',
    start: '2026-10-31T23:00:00+00:00',
    end: '2026-11-30T23:00:00+00:00',
    grain: null,
    group_by: ['status', 'author'],
    as_of: '2026-11-20T08:00:00+00:00',
    excluded_missing_timestamp: 2,
    buckets: [
        { status: 'released', author_user_id: 4, count: 3, total_seconds: 125, average_seconds: 62.5, known_duration_count: 2, incomplete: true },
        { status: 'open', author_user_id: 5, count: 1, total_seconds: null, average_seconds: null, known_duration_count: 0, incomplete: true },
        { status: 'in_review', author_user_id: 4, count: 2, total_seconds: 3700, average_seconds: 1850, known_duration_count: 2, incomplete: false },
    ],
};

// Same shape as routes_history._history: ids, actions and revision references only.
const HISTORY = {
    dictation_id: D,
    events: [
        { kind: 'revision', id: 'v1', revision_kind: 'correction', parent_revision_id: null },
        { kind: 'routing', id: 'r1', action: 'work_state_transferred', at: '2026-11-01T10:00:00+00:00' },
        { kind: 'release', id: 'x1', action: 'released', at: '2026-11-02T10:00:00+00:00', audio_revision_id: 'a1', transcript_revision_id: 't1', context_revision_id: 'c1' },
    ],
};

function fakeVue() {
    const hooks = { mounted: [], unmount: [], watchers: [] };
    return {
        hooks,
        reactive: (o) => o,
        ref: (v) => ({ value: v }),
        markRaw: (o) => o,
        computed: (fn) => ({ get value() { return fn(); } }),
        watch: (getter, cb) => hooks.watchers.push({ getter, cb }),
        onMounted: (fn) => hooks.mounted.push(fn),
        onBeforeUnmount: (fn) => hooks.unmount.push(fn),
    };
}

function fakeClient(handler) {
    return {
        request: vi.fn(async (path, opts) => (handler ? handler(path, opts) : ok({}))),
        command: vi.fn(async () => ok({})),
        switchContext: vi.fn(),
    };
}

const source = () => readFileSync(new URL('../../../static/js/clinic/compose-activity.js', import.meta.url), 'utf8');

describe('activity view composition', () => {
    it('exports the registration contract', () => {
        const app = { component: vi.fn() };
        registerActivityView(app);
        expect(app.component).toHaveBeenCalledWith('clinic-activity-view', ActivityView);
        expect(Object.keys(ActivityView.props)).toEqual(['client', 'currentUser', 'dictationId', 'vue']);
    });

    it('imports the existing building blocks and does not copy them', () => {
        const text = source();
        expect(text).toMatch(/from '\.\/history-ui\.js'/);
        expect(text).toMatch(/from '\.\/worklist-ui\.js'/);
        expect(text).toMatch(/from '\.\/tokens\.js'/);
        expect(text).not.toMatch(/\/dictations\/'/);
        expect(text).not.toMatch(/function formatAudioDuration/);
        expect(text).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(text).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });

    it('has german labels and accessible names on every button', () => {
        expect(LABELS.title).toBe('Aktivität und Audit');
        expect(LABELS.historyTitle).toBe('Verlauf eines Diktats');
        expect(LABELS.notCollected).toBe('Nicht erhoben');
        const buttons = ACTIVITY_TEMPLATE.match(/<button[^>]*>/g);
        expect(buttons.length).toBeGreaterThanOrEqual(3);
        for (const b of buttons) expect(b).toMatch(/aria-label="/);
        expect(ACTIVITY_TEMPLATE).toContain('<label for="act-from">');
        expect(ACTIVITY_TEMPLATE).toContain('state.denied');
    });
});

describe('statistics query', () => {
    it('sends day bounds unchanged so the server reads Europe/Zurich midnight', () => {
        const built = buildStatisticsQuery({ timeBasis: 'completed', from: '2026-03-01', to: '2026-04-01', grain: 'week', groupBy: ['period', 'status'] });
        const params = new URLSearchParams(built.query);
        expect(params.get('from')).toBe('2026-03-01');
        expect(params.get('to')).toBe('2026-04-01');
        expect(params.get('grain')).toBe('week');
        expect(params.get('group_by')).toBe('status,period');
        expect(params.get('time_basis')).toBe('completed');
    });

    it('refuses invalid input before any request', () => {
        expect(buildStatisticsQuery({ from: '2026-02-30' }).error).toMatch(/JJJJ-MM-TT/);
        expect(buildStatisticsQuery({ from: '2026-05-02', to: '2026-05-01' }).error).toMatch(/Enddatum/);
        expect(buildStatisticsQuery({ groupBy: ['period'], grain: 'day' }).error).toMatch(/Start- und Enddatum/);
        expect(buildStatisticsQuery({ groupBy: ['period'], from: '2026-01-01', to: '2026-02-01' }).error).toMatch(/Zeitraster/);
        expect(buildStatisticsQuery({ timeBasis: 'processing' }).error).toMatch(/Zeitbasis/);
        expect(buildStatisticsQuery({}).query).toBe('time_basis=created');
    });
});

describe('statistics rendering from the real aggregate shape', () => {
    it('renders rows, provenance and marks incomplete buckets in words', () => {
        const view = buildStatisticsView(AGGREGATE);
        expect(view.columns.map((c) => c.label)).toEqual(['Status', 'Diktiert von']);
        expect(view.rows[0]).toMatchObject({ cells: ['Freigegeben', 'Benutzer 4'], count: 3, total: '02:05', average: '01:02', known: 2, stateLabel: 'unvollständig', surface: 'warning' });
        expect(view.rows[2]).toMatchObject({ cells: ['In Prüfung', 'Benutzer 4'], total: '1:01:40', stateLabel: 'vollständig', surface: 'normal' });
        expect(view.provenance).toMatchObject({ zone: 'Europe/Zurich', window: '[start, end)', excluded: 2, timeBasis: 'Eingang des Diktats' });
    });

    it('shows a missing length as Nicht erhoben, never as zero', () => {
        const row = buildStatisticsView(AGGREGATE).rows[1];
        expect(row.total).toBe('Nicht erhoben');
        expect(row.average).toBe('Nicht erhoben');
        expect(row.known).toBe(0);
    });

    it('rejects a malformed body', () => {
        expect(buildStatisticsView(null)).toBeNull();
        expect(buildStatisticsView({ buckets: 'x', group_by: [], zone: 'Europe/Zurich' })).toBeNull();
    });

    it('loads through the statistics route and keeps the ready state', async () => {
        const client = fakeClient(() => ok(AGGREGATE));
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        ctrl.toggleGroup('status');
        ctrl.state.form.from = '2026-11-01';
        ctrl.state.form.to = '2026-12-01';
        const done = ctrl.load();
        expect(ctrl.state.status).toBe('loading');
        await done;
        expect(client.request).toHaveBeenCalledWith(STATISTICS_PATH + '?time_basis=created&from=2026-11-01&to=2026-12-01&group_by=status');
        expect(ctrl.state.status).toBe('ready');
        expect(ctrl.state.view.rows).toHaveLength(3);
    });

    it('shows the empty state', async () => {
        const client = fakeClient(() => ok({ ...AGGREGATE, buckets: [], excluded_missing_timestamp: 0 }));
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.status).toBe('empty');
        expect(ctrl.state.view.rows).toEqual([]);
    });

    it('shows the 403 state and keeps no figures', async () => {
        let first = true;
        const client = fakeClient(() => {
            if (first) { first = false; return ok(AGGREGATE); }
            return { outcome: 'permission', status: 403, code: 'FORBIDDEN' };
        });
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.view).not.toBeNull();
        await ctrl.load();
        expect(ctrl.state.status).toBe('denied');
        expect(ctrl.state.message).toBe('Keine Berechtigung für diese Statistik.');
        expect(ctrl.state.view).toBeNull();
    });

    it('maps conflict, network failure and a wrong body to retryable states', async () => {
        const answers = [{ outcome: 'conflict', status: 409 }, { outcome: 'network' }, ok({ nope: true })];
        const client = fakeClient(() => answers.shift());
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        await ctrl.load();
        expect(ctrl.state.status).toBe('stale');
        await ctrl.load();
        expect(ctrl.state.status).toBe('error');
        await ctrl.load();
        expect(ctrl.state.status).toBe('error');
        expect(ctrl.state.message).toBe('Die Statistik konnte nicht geladen werden.');
    });

    it('does not request on an invalid form', async () => {
        const client = fakeClient();
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        ctrl.state.form.from = 'morgen';
        await ctrl.load();
        expect(ctrl.state.status).toBe('invalid');
        expect(client.request).not.toHaveBeenCalled();
    });

    it('ignores a late answer after a newer request', async () => {
        const pending = [];
        const client = fakeClient(() => new Promise((resolve) => pending.push(resolve)));
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        const a = ctrl.load();
        const b = ctrl.load();
        pending[1](ok({ ...AGGREGATE, buckets: [] }));
        await b;
        pending[0](ok(AGGREGATE));
        await a;
        expect(ctrl.state.status).toBe('empty');
    });
});

describe('history section', () => {
    it('opens only a canonical dictation id and aborts the previous context', () => {
        const client = fakeClient();
        const ctrl = createActivityController({ client, currentUser: { id: 7 } });
        expect(ctrl.openHistory('nicht-gueltig')).toBe(false);
        expect(ctrl.state.historyMessage).toBe('Bitte eine gültige Diktat-ID eingeben.');
        expect(ctrl.state.dictationId).toBeNull();
        expect(ctrl.openHistory(' ' + D.toUpperCase() + ' ')).toBe(true);
        expect(ctrl.state.dictationId).toBe(D);
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '7', dictationId: D });
    });

    it('mounts the existing history component with ids and revision references', async () => {
        const Vue = fakeVue();
        const client = fakeClient((path) => (path === '/dictations/' + D + '/history' ? ok(HISTORY) : ok({})));
        const view = ActivityView.setup({ client, currentUser: { id: 7 }, dictationId: D, vue: Vue });
        expect(view.state.dictationId).toBe(D);
        expect(view.historyComponent.name).toBe('ClinicHistory');
        const child = view.historyComponent.setup({ client, dictationId: D });
        await child.load();
        expect(child.status.value).toBe('ready');
        expect(child.view.value.rows.map((r) => r.key)).toEqual(['revision:v1', 'routing:r1', 'release:x1']);
        expect(child.view.value.rows[2].links.map((l) => l.value)).toEqual(['a1', 't1', 'c1']);
        expect(JSON.stringify(child.view.value)).not.toMatch(/transcript_text|patient|title/i);
    });

    it('shows the history 403 state through the existing component', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ({ outcome: 'permission', status: 403 }));
        const view = ActivityView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        const child = view.historyComponent.setup({ client, dictationId: D });
        await child.load();
        expect(child.status.value).toBe('denied');
        expect(child.message.value).toBe('Keine Berechtigung für diesen Verlauf.');
        expect(child.events.value).toEqual([]);
    });
});

describe('account scope', () => {
    it('refuses an unauthenticated user and requests nothing', async () => {
        const client = fakeClient();
        const ctrl = createActivityController({ client, currentUser: null });
        expect(ctrl.state.denied).toBe(true);
        expect(await ctrl.load()).toBeNull();
        expect(ctrl.openHistory(D)).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
    });

    it('drops figures and the open dictation on a user change', async () => {
        const Vue = fakeVue();
        const client = fakeClient(() => ok(AGGREGATE));
        const props = { client, currentUser: { id: 1 }, vue: Vue };
        const view = ActivityView.setup(props);
        await view.load();
        view.openHistory(D);
        const epoch = view.state.epoch;
        props.currentUser = { id: 2 };
        const watcher = Vue.hooks.watchers[0];
        expect(watcher.getter()).toBe('2');
        watcher.cb();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: '2', dictationId: null });
        expect(view.state.view).toBeNull();
        expect(view.state.dictationId).toBeNull();
        expect(view.state.epoch).toBe(epoch + 1);
    });

    it('releases in-flight calls on unmount and ignores a late answer', async () => {
        const Vue = fakeVue();
        let resolve;
        const client = fakeClient(() => new Promise((r) => { resolve = r; }));
        const view = ActivityView.setup({ client, currentUser: { id: 3 }, vue: Vue });
        const pending = view.load();
        Vue.hooks.unmount[0]();
        expect(client.switchContext).toHaveBeenLastCalledWith({ accountId: null, dictationId: null });
        resolve(ok(AGGREGATE));
        await pending;
        expect(view.state.view).toBeNull();
        expect(view.openHistory(D)).toBe(false);
    });
});
