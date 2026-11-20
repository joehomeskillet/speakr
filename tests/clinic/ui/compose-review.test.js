import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    ACTION_LABELS,
    ReviewView,
    TEXT,
    VIEW_ACTIONS,
    createReviewViewController,
    registerReviewView,
    scopedClient,
    toWorkflowRow,
} from '../../../static/js/clinic/compose-review.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-review.js'), 'utf8');

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const fail = (outcome, status, message, code = null) => ({ outcome, status, code, message });

const user = { id: 7 };

/** Mutable server model: the head text revision can move (autosave) between calls. */
function server(extra = {}) {
    const model = {
        head: 'r1',
        review: {
            etag: '"d1:3"',
            revision: 4,
            status: 'zu_pruefen',
            asr_status: 'completed',
            allowed_actions: ['accept', 'reject', 'release', 'archive'],
            transitions: { reject: 'Zu korrigieren' },
            author_name: 'Dr. Muster',
            pool_name: 'Pool A',
            corrected_by_name: 'Schreibkraft B',
            context_trusted: true,
            review_markers: [],
            binding: {
                text_revision_id: 'r1',
                audio_revision_id: 'a1',
                context_revision_id: 'c1',
                acceptance_rule_id: 'rule1',
                release: { lease_id: 'l1', lease_generation: 1, original_version_id: 'r1', transcript_revision_id: 'r1', context_revision_id: 'c1', document_type: 'report', mode: 'PLATFORM_REVIEWED_KIS_DRAFT', text_sha256: 'h' },
            },
        },
        ...extra,
    };
    return model;
}

/** Fake clinic client: routes are looked up by "METHOD path-suffix"; the model drives the review and correction reads. */
function fakeClient(model, overrides = {}) {
    const calls = [];
    const commands = [];
    const handlers = {
        'GET /dictations/d1/review': () => ok(model.review),
        'GET /dictations/d1/correction': () => ok({
            dictation_id: 'd1', text_revision_id: model.head, text: 'Hallo Welt heute', state: { version: 1 },
            links: [
                { id: 'l1', text_start: 0, text_end: 5, audio_start_ms: 0, audio_end_ms: 900 },
                { id: 'l2', text_start: 6, text_end: 10, audio_start_ms: 1000, audio_end_ms: 1800 },
            ], markers: [],
        }),
        'GET /dictations/d1/audio-revisions/current': () => ok({ revision_id: 'a1', segments: [] }),
        'GET /dictations/d1/acceptance-policy': () => ok({ version: 2, acceptance_required: true, approver_ids: [7], decisions: [{ kind: 'reject', actor_id: 3, actor_name: 'Dr. Alt', revision_id: 'r0', reason: 'Dosis unklar', policy_version: 2 }] }),
        'POST /dictations/d1/releases': () => ok({ released: true }),
        'GET /dictations/d1/leases': () => ok({ held: true, lease_id: 'l1', generation: 1 }),
        'POST /dictations/d1/archive': () => ok({ archived: true }),
        ...overrides,
    };
    const find = (method, path) => {
        const key = Object.keys(handlers).find((k) => { const [m, p] = k.split(' '); return m === method && path.endsWith(p); });
        return key ? handlers[key] : null;
    };
    return {
        calls,
        commands,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            const h = find(method, path);
            return h ? h(path, options) : ok({ items: [] });
        }),
        command: vi.fn(async (body, options) => {
            commands.push({ body, options });
            return typeof overrides.command === 'function' ? overrides.command(body, options) : ok({ status: 'committed' });
        }),
        switchContext: vi.fn(),
    };
}

async function opened(model = server(), overrides = {}, extra = {}) {
    const client = fakeClient(model, overrides);
    const c = createReviewViewController({ client, dictationId: 'd1', currentUser: user, ...extra });
    await c.start();
    return { c, client, model };
}

describe('compose-review contract', () => {
    it('exports the view and registers it as clinic-review-view', () => {
        const app = { component: vi.fn() };
        expect(registerReviewView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-review-view', ReviewView);
        expect(Object.keys(ReviewView.props)).toEqual(expect.arrayContaining(['client', 'dictationId', 'currentUser', 'vue']));
        expect(ReviewView.emits).toEqual(expect.arrayContaining(['close', 'correct']));
    });

    it('reuses the building blocks, tokens and api client and never touches browser storage', () => {
        for (const mod of ['correction-editor-ui', 'correction-alignment-ui', 'acceptance-policy-ui', 'workflow-actions-ui', 'tokens', 'api-client']) {
            expect(source).toContain('./' + mod + '.js');
        }
        const imports = [...source.matchAll(/from '(\.\/[^']+)'/g)].map((m) => m[1]);
        expect(imports).toHaveLength(new Set(imports).size);
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/);
        expect(source).not.toMatch(/fetch\(/);
    });

    it('uses German labels, native controls, no shortcut that decides and no patient fields', () => {
        const body = JSON.stringify(Object.values(ReviewView.components).map((c) => c.template));
        expect(TEXT.title).toBe('Prüfen und Freigeben');
        expect(body).toContain('TEXT.title');
        expect(body).toContain('role=\\"alertdialog\\"');
        expect(body).not.toMatch(/@keydown\.(ctrl|meta)|patient|Patient/);
        expect(Object.values(ACTION_LABELS)).toEqual(['Freigeben', 'Zurückweisen', 'Fertigstellen', 'Archivieren']);
        expect(VIEW_ACTIONS).toEqual(['accept', 'reject', 'release', 'archive']);
        expect(TEXT.newerRevision).toContain('neuere Fassung');
    });

    it('requires a client, a dictation id and a user id', () => {
        expect(() => scopedClient(null)).toThrow('client is required');
        expect(() => createReviewViewController({ client: fakeClient(server()), currentUser: user })).toThrow('dictationId is required');
        expect(() => createReviewViewController({ client: fakeClient(server()), dictationId: 'd1', currentUser: {} })).toThrow('currentUser.id is required');
    });

    it('maps the review state to the workflow row', () => {
        const row = toWorkflowRow('d1', server().review);
        expect(row.allowedActions).toEqual(['accept', 'reject', 'release', 'archive']);
        expect(row.binding.textRevisionId).toBe('r1');
        expect(row.binding.contextRevisionId).toBe('c1');
        expect(toWorkflowRow('d1', null).allowedActions).toEqual([]);
    });
});

describe('compose-review loading and revision display', () => {
    it('names the reviewed text, audio and context revision and shows the rule and past decisions', async () => {
        const { c } = await opened();
        expect(c.status.value).toBe('ready');
        expect(c.revisionSummary()).toEqual({ text: 'r1', audio: 'a1', context: 'c1' });
        expect(c.confirmText('accept')).toContain('Textrevision r1');
        expect(c.confirmText('accept')).toContain('Diktat d1');
        expect(c.policy.state.required).toBe(true);
        expect(c.policy.state.decisions[0].state).toBe('rejected');
        expect(c.policy.state.decisions[0].reason).toBe('Dosis unklar');
        expect(c.isNewer()).toBe(false);
        c.dispose();
    });

    it('shows no review job when the server has none and offers nothing', async () => {
        const { c } = await opened(server(), { 'GET /dictations/d1/review': () => fail('http', 404, 'nicht gefunden') });
        expect(c.status.value).toBe('none');
        expect(c.actions()).toEqual([]);
        expect(await c.request('accept')).toMatchObject({ ok: false });
        c.dispose();
    });

    it('reports a permission refusal neutrally and a load failure with retry', async () => {
        const denied = await opened(server(), { 'GET /dictations/d1/review': () => fail('permission', 403, 'x') });
        expect(denied.c.status.value).toBe('denied');
        const failing = await opened(server(), { 'GET /dictations/d1/review': () => fail('network', 0, 'x') });
        expect(failing.c.status.value).toBe('error');
        expect(failing.c.actions()).toEqual([]);
    });

    it('offers only the allowed actions in the fixed order; a read-only user gets none', async () => {
        const model = server();
        model.review.allowed_actions = ['archive', 'bogus', 'accept'];
        const { c } = await opened(model);
        expect(c.actions().map((a) => a.id)).toEqual(['accept', 'archive']);
        const ro = server();
        ro.review.allowed_actions = [];
        const read = await opened(ro);
        expect(read.c.actions()).toEqual([]);
        expect(read.c.readOnly()).toBe(true);
        expect(await read.c.request('accept')).toMatchObject({ ok: false, code: 'permission' });
        expect(read.client.commands).toHaveLength(0);
    });

    it('keeps accept, release and archive as three separate actions', async () => {
        const { c } = await opened();
        const ids = c.actions().map((a) => a.id);
        expect(ids).toContain('accept');
        expect(ids).toContain('release');
        expect(ids).toContain('archive');
        expect(c.actions().find((a) => a.id === 'accept').label).toBe('Freigeben');
        expect(c.actions().find((a) => a.id === 'release').label).toBe('Fertigstellen');
    });
});

describe('compose-review accept flow', () => {
    it('needs an explicit confirmation, then sends the decision bound to the reviewed text revision', async () => {
        const { c, client } = await opened();
        expect(await c.request('accept')).toEqual({ ok: true });
        expect(c.confirming.value).toBe('accept');
        expect(client.commands).toHaveLength(0);
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: true, action: 'accept' });
        expect(client.commands).toHaveLength(1);
        const { body } = client.commands[0];
        expect(body.command).toBe('decide_acceptance');
        expect(body.payload).toMatchObject({ dictation_id: 'd1', text_revision_id: 'r1', decision: 'accept' });
        expect(body.expected_revision).toBe(5);
        expect(c.confirming.value).toBeNull();
        c.dispose();
    });

    it('reloads the authoritative state after the decision', async () => {
        const { c, client } = await opened();
        const before = client.calls.filter((x) => x.path.endsWith('/review')).length;
        await c.request('accept');
        await c.confirm();
        expect(client.calls.filter((x) => x.path.endsWith('/review')).length).toBe(before + 1);
    });

    it('cancel drops the confirmation without sending', async () => {
        const { c, client } = await opened();
        await c.request('accept');
        c.cancel();
        expect(c.confirming.value).toBeNull();
        expect(await c.confirm()).toMatchObject({ ok: false, code: 'none' });
        expect(client.commands).toHaveLength(0);
    });

    it('release stays a separate step that needs both checks and sends only the bound revisions', async () => {
        const { c, client } = await opened();
        await c.request('release');
        expect(await c.confirm()).toMatchObject({ ok: false, code: 'unconfirmed' });
        expect(client.calls.some((x) => x.method === 'POST' && x.path.endsWith('/releases'))).toBe(false);
        c.contentChecked.value = true;
        c.contextChecked.value = true;
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: true, action: 'release' });
        const post = client.calls.find((x) => x.method === 'POST' && x.path.endsWith('/releases'));
        expect(post.options.body).toMatchObject({ original_version_id: 'r1', context_revision_id: 'c1', confirmed_content: true, confirmed_context: true });
        expect(client.commands).toHaveLength(0);
    });

    it('archive is its own action and does not accept or release', async () => {
        const { c, client } = await opened();
        await c.request('archive');
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: true, action: 'archive' });
        expect(client.calls.some((x) => x.path.endsWith('/archive'))).toBe(true);
        expect(client.calls.some((x) => x.path.endsWith('/releases'))).toBe(false);
        expect(client.commands).toHaveLength(0);
    });
});

describe('compose-review reject flow', () => {
    it('refuses to send without a reason, marks the field invalid and focuses it', async () => {
        const focusReason = vi.fn();
        const { c, client } = await opened(server(), {}, { focusReason });
        await c.request('reject');
        c.reason.value = '   ';
        expect(await c.confirm()).toMatchObject({ ok: false, code: 'reason', message: TEXT.reasonRequired });
        expect(c.reasonInvalid.value).toBe(true);
        expect(c.error.value).toBe(TEXT.reasonRequired);
        expect(focusReason).toHaveBeenCalledTimes(1);
        expect(client.commands).toHaveLength(0);
        expect(c.confirming.value).toBe('reject');
    });

    it('sends the trimmed reason with the return decision for the reviewed revision', async () => {
        const { c, client } = await opened();
        await c.request('reject');
        c.reason.value = '  Dosis unklar  ';
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: true, action: 'reject' });
        expect(client.commands).toHaveLength(1);
        expect(client.commands[0].body.payload).toMatchObject({ decision: 'return', reason: 'Dosis unklar', text_revision_id: 'r1' });
    });

    it('shows a refused reject without losing the reason', async () => {
        const { c } = await opened(server(), { command: () => fail('validation', 422, 'Begründung ungültig') });
        await c.request('reject');
        c.reason.value = 'zu kurz';
        const res = await c.confirm();
        expect(res.ok).toBe(false);
        expect(c.error.value).toBe('Begründung ungültig');
        expect(c.reason.value).toBe('zu kurz');
    });
});

describe('compose-review blockers (W11)', () => {
    const withMarkers = () => {
        const m = server();
        m.review.review_markers = [{ id: 'm1', label: 'Dosis prüfen' }, { id: 'm2' }];
        return m;
    };

    it('lists open review markers as blockers', async () => {
        const { c } = await opened(withMarkers());
        expect(c.markers()).toEqual([{ id: 'm1', label: 'Dosis prüfen' }, { id: 'm2', label: TEXT.blockerFallback }]);
    });

    it('disables release while markers are open and sends nothing', async () => {
        const { c, client } = await opened(withMarkers());
        const release = c.actions().find((a) => a.id === 'release');
        expect(release.disabled).toBe(true);
        expect(release.why).toBe(TEXT.blockers);
        expect(await c.request('release')).toMatchObject({ ok: false, code: 'blocked' });
        expect(c.confirming.value).toBeNull();
        expect(client.calls.some((x) => x.path.endsWith('/releases'))).toBe(false);
    });

    it('keeps the other decisions available while markers are open', async () => {
        const { c } = await opened(withMarkers());
        for (const id of ['accept', 'reject', 'archive']) expect(c.actions().find((a) => a.id === id).disabled).toBe(false);
    });

    it('enables release once no marker is open', async () => {
        const { c } = await opened();
        expect(c.actions().find((a) => a.id === 'release').disabled).toBe(false);
    });
});

describe('compose-review newer revision (Wiedervorlage)', () => {
    it('flags a text head newer than the reviewed binding at load and blocks every action', async () => {
        const model = server();
        model.head = 'r2';
        const { c, client } = await opened(model);
        expect(c.isNewer()).toBe(true);
        expect(c.newer.value).toEqual({ revisionId: 'r2' });
        expect(c.actions().every((a) => a.disabled && a.why === TEXT.wiedervorlage)).toBe(true);
        expect(await c.request('accept')).toMatchObject({ ok: false, code: 'blocked' });
        expect(client.commands).toHaveLength(0);
    });

    it('blocks the confirm of A when an autosave created B after the confirmation opened', async () => {
        const { c, client, model } = await opened();
        await c.request('accept');
        model.head = 'r2';
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: false, code: 'newer', message: TEXT.newerRevision });
        expect(c.isNewer()).toBe(true);
        expect(client.commands).toHaveLength(0);
        expect(c.error.value).toBe(TEXT.newerRevision);
    });

    it('checks the head again before opening a confirmation', async () => {
        const { c, model } = await opened();
        model.head = 'r3';
        expect(await c.request('reject')).toMatchObject({ ok: false, code: 'newer' });
        expect(c.confirming.value).toBeNull();
    });

    it('reloading adopts the newer revision as the one under review', async () => {
        const model = server();
        const { c } = await opened(model);
        model.head = 'r2';
        model.review.binding.text_revision_id = 'r2';
        await c.checkNewer();
        expect(c.isNewer()).toBe(true);
        await c.load();
        expect(c.isNewer()).toBe(false);
        expect(c.revisionSummary().text).toBe('r2');
        expect(c.actions().some((a) => !a.disabled)).toBe(true);
    });

    it('treats a server conflict as a Wiedervorlage and keeps the dialog message', async () => {
        const { c } = await opened(server(), { command: () => fail('conflict', 409, 'x', 'STALE_VERSION') });
        await c.request('accept');
        const res = await c.confirm();
        expect(res).toMatchObject({ ok: false, code: 'conflict', message: TEXT.conflict });
        expect(c.isNewer()).toBe(true);
        expect(c.actions().every((a) => a.disabled)).toBe(true);
    });
});

describe('compose-review offline, release of other revisions and cleanup', () => {
    it('offers no decision while offline', async () => {
        const { c, client } = await opened(server(), {}, { isOnline: () => false });
        expect(c.actions().every((a) => a.disabled && a.why === TEXT.offline)).toBe(true);
        expect(await c.request('accept')).toMatchObject({ ok: false, code: 'blocked' });
        expect(client.commands).toHaveLength(0);
    });

    it('never builds a decision for a revision other than the bound one', async () => {
        const { c, client } = await opened();
        await c.request('accept');
        await c.confirm();
        expect(client.commands[0].body.payload.text_revision_id).toBe('r1');
        expect(JSON.stringify(client.commands[0].body)).not.toContain('r2');
    });

    it('dispose clears the clinical data held in memory and stops further work', async () => {
        const { c, client } = await opened();
        c.dispose();
        expect(c.editor.text.value).toBe('');
        expect(c.review.value).toBeNull();
        expect(c.reviewed.value).toBeNull();
        expect(c.status.value).toBe('idle');
        const calls = client.calls.length;
        expect(await c.load()).toMatchObject({ ok: false, code: 'cancelled' });
        expect(client.calls.length).toBe(calls);
        c.dispose();
    });

    it('forwards close and correct to the page without deciding anything', async () => {
        const onClose = vi.fn();
        const onCorrect = vi.fn();
        const { c, client } = await opened(server(), {}, { onClose, onCorrect });
        expect(c.close()).toBe(true);
        expect(c.correct()).toBe(true);
        expect(onClose).toHaveBeenCalledWith({ dictationId: 'd1' });
        expect(onCorrect).toHaveBeenCalledWith({ dictationId: 'd1' });
        expect(client.commands).toHaveLength(0);
    });

    it('shows the current word from the player position without any request', async () => {
        const { c, client } = await opened();
        const el = Object.assign(new EventTarget(), { currentTime: 0 });
        c.bindAudio(el);
        const calls = client.calls.length;
        el.currentTime = 1.2;
        el.dispatchEvent(new Event('timeupdate'));
        expect(c.alignment.currentWord.value).toMatchObject({ id: 'l2' });
        expect(client.calls.length).toBe(calls);
        c.bindAudio(null);
        c.dispose();
    });
});


describe('compose-review acceptance state (v8-74 route)', () => {
    const route = 'GET /dictations/d1/acceptance-state';
    const heads = { text_revision_id: 'r1', audio_revision_id: 'a1', context_revision_id: 'c1' };
    const states = {
        offen: { state: 'offen', label: 'Noch keine Entscheidung', current: heads },
        angenommen: { state: 'angenommen', label: 'Abgenommen', current: heads },
        wiedervorlage: { state: 'wiedervorlage', label: 'Wiedervorlage: Text, Audio oder Kontext hat sich nach der Abnahme geändert', current: heads },
        zurueckgegeben: { state: 'zurueckgegeben', label: 'Zurückgegeben', current: heads },
    };
    const withState = (body) => opened(server(), { [route]: () => ok(body) });

    it('reads the route of the dictation and keeps the other reads unchanged', async () => {
        const { c, client } = await withState(states.offen);
        expect(client.calls.filter((x) => x.path.endsWith('/dictations/d1/acceptance-state'))).toHaveLength(1);
        expect(c.status.value).toBe('ready');
        c.dispose();
    });

    it('renders offen as Abzunehmen when acceptance is required', async () => {
        const { c } = await withState(states.offen);
        expect(c.acceptanceView()).toEqual({ state: 'offen', required: true, label: TEXT.toAccept, tone: 'warn', acceptedRevision: null, moved: false, reason: null });
    });

    it('renders offen without a requirement as no decision yet and not required', async () => {
        const { c } = await opened(server(), {
            [route]: () => ok(states.offen),
            'GET /dictations/d1/acceptance-policy': () => ok({ version: 2, acceptance_required: false, approver_ids: [], decisions: [] }),
        });
        expect(c.acceptanceView()).toMatchObject({ state: 'offen', required: false, label: TEXT.noDecisionYet });
    });

    it('renders angenommen as Abgenommen and names the accepted revision', async () => {
        const { c } = await withState(states.angenommen);
        expect(c.acceptanceView()).toMatchObject({ state: 'angenommen', label: TEXT.accepted, acceptedRevision: 'r1', moved: false });
    });

    it('renders wiedervorlage as Abzunehmen again with the moved note and no accepted revision', async () => {
        const { c } = await withState(states.wiedervorlage);
        expect(c.acceptanceView()).toMatchObject({ state: 'wiedervorlage', label: TEXT.toAccept, moved: true, acceptedRevision: null });
    });

    it('renders zurueckgegeben as Zurückgewiesen; the reason only when the response has one', async () => {
        const without = await withState(states.zurueckgegeben);
        expect(without.c.acceptanceView()).toMatchObject({ state: 'zurueckgegeben', label: TEXT.rejected, reason: null });
        const withReason = await withState({ ...states.zurueckgegeben, reject_reason: 'Dosis unklar' });
        expect(withReason.c.acceptanceView().reason).toBe('Dosis unklar');
        const blank = await withState({ ...states.zurueckgegeben, reject_reason: '' });
        expect(blank.c.acceptanceView().reason).toBeNull();
    });

    it('ignores a reject reason outside the returned state', async () => {
        const { c } = await withState({ ...states.angenommen, reject_reason: 'alt' });
        expect(c.acceptanceView().reason).toBeNull();
    });

    it('a 403 shows the existing denied state and offers nothing', async () => {
        const { c, client } = await opened(server(), { [route]: () => fail('permission', 403, 'x') });
        expect(c.status.value).toBe('denied');
        expect(c.acceptanceView()).toBeNull();
        expect(c.actions()).toEqual([]);
        expect(client.commands).toHaveLength(0);
    });

    it('shows no panel and still loads when the route is absent (404) or fails', async () => {
        for (const res of [fail('http', 404, 'nicht gefunden'), fail('network', 0, 'x')]) {
            const { c } = await opened(server(), { [route]: () => res });
            expect(c.status.value).toBe('ready');
            expect(c.acceptanceView()).toBeNull();
            expect(c.actions().length).toBeGreaterThan(0);
        }
    });

    it('keeps accept and reject on the workflow command and reloads the state afterwards', async () => {
        const { c, client } = await withState(states.offen);
        await c.request('accept');
        await c.confirm();
        expect(client.commands).toHaveLength(1);
        expect(client.commands[0].body).toMatchObject({ command: 'decide_acceptance', payload: { decision: 'accept', text_revision_id: 'r1' } });
        expect(client.calls.filter((x) => x.path.endsWith('/acceptance-state'))).toHaveLength(2);
        await c.request('reject');
        c.reason.value = 'Dosis unklar';
        await c.confirm();
        expect(client.commands[1].body.payload).toMatchObject({ decision: 'return', reason: 'Dosis unklar' });
    });

    it('template shows the panel parts only from the view model and uses no patient field', () => {
        const body = JSON.stringify(Object.values(ReviewView.components).map((c) => c.template));
        for (const id of ['acceptance-state', 'acceptance-required', 'acceptance-revision', 'acceptance-moved', 'acceptance-reason']) {
            expect(body).toContain('data-testid=\\"' + id + '\\"');
        }
        expect(body).toContain('c.acceptanceView().reason');
    });

    it('dispose clears the acceptance state', async () => {
        const { c } = await withState(states.angenommen);
        c.dispose();
        expect(c.acceptance.value).toBeNull();
        expect(c.acceptanceView()).toBeNull();
    });
});
