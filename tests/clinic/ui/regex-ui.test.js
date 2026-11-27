import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRegexRulesModel, createRegexRulesComponent, LABELS } from '../../../static/js/clinic/regex-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, code: null, correlationId: null, message: '', data });
const bad = (outcome, code) => ({ outcome, status: 400, code, correlationId: null, message: '' });

const PREVIEW = {
    resulting_fields: { title: 'Bericht A', priority: 128 },
    matches: [{ rule_name: 'r', target_field: 'title', matched_text: 'x', extracted_value: 'Bericht A', applied_value: 'Bericht A' }],
    truncated: false,
    rule_version: 'v1hash',
    input_digest: 'd1hash',
};

function model(request, current) {
    const client = { request };
    const m = createRegexRulesModel(client, { dictationId: 'd-1', currentFields: current || { title: 'Alt' } });
    m.setRule(0, 'pattern', 'Bericht (\\w+)');
    m.setRule(0, 'target_field', 'title');
    m.setText('Bericht A');
    return m;
}

describe('regex ui model', () => {
    it('previews old and new values and sends the exact input', async () => {
        const request = vi.fn(async () => ok(PREVIEW));
        const m = model(request);
        await m.preview();
        expect(request.mock.calls[0][0]).toBe('/regex/preview');
        expect(request.mock.calls[0][1].body).toMatchObject({ dictation_id: 'd-1', text: 'Bericht A' });
        expect(m.state.phase).toBe('preview');
        expect(m.state.preview.rows).toEqual([
            { field: 'priority', oldValue: '', newValue: '128' },
            { field: 'title', oldValue: 'Alt', newValue: 'Bericht A' },
        ]);
        expect(m.state.preview.ruleVersion).toBe('v1hash');
    });

    it('requires an explicit apply bound to the previewed version and digest', async () => {
        const request = vi.fn(async (path) => (path === '/regex/preview' ? ok(PREVIEW) : ok({ applied_fields: {} })));
        const m = model(request);
        expect(m.canApply()).toBe(false);
        expect(await m.apply()).toBeNull();
        await m.preview();
        expect(request).toHaveBeenCalledTimes(1);
        expect(m.canApply()).toBe(true);
        await m.apply();
        const sent = request.mock.calls[1][1].body;
        expect(request.mock.calls[1][0]).toBe('/regex/apply');
        expect(sent).toMatchObject({ rule_version: 'v1hash', input_digest: 'd1hash', text: 'Bericht A' });
        expect(m.state.phase).toBe('applied');
    });

    it('discards the preview after any edit', async () => {
        const m = model(async () => ok(PREVIEW));
        await m.preview();
        m.setText('Bericht B');
        expect(m.state.preview).toBeNull();
        expect(m.canApply()).toBe(false);
        await m.preview();
        m.setRule(0, 'pattern', 'x');
        expect(m.state.phase).toBe('idle');
    });

    it('shows invalid pattern and forbidden target as validation failures', async () => {
        const m = model(async () => bad('validation', 'INVALID_PAYLOAD'));
        await m.preview();
        expect(m.state.failure.message).toBe(LABELS.invalidPattern);
        expect(m.state.failure.retryable).toBe(false);
        const f = model(async () => bad('permission', 'POLICY_DENIED'));
        await f.preview();
        expect(f.state.failure.message).toBe(LABELS.forbiddenTarget);
        expect(f.failureSurface().tokens.border).toBe('--border-danger');
    });

    it('handles unauthorized', async () => {
        const m = model(async () => bad('permission', 'FORBIDDEN'));
        await m.preview();
        expect(m.state.failure.message).toBe(LABELS.denied);
        expect(m.canApply()).toBe(false);
    });

    it('shows a combined message for a POLICY_DENIED authorization denial', async () => {
        const m = model(async () => bad('permission', 'POLICY_DENIED'));
        await m.preview();
        expect(m.state.failure.message).toContain('Keine Berechtigung');
        expect(m.state.failure.message).toContain('Zielfeld nicht erlaubt');
        expect(m.canApply()).toBe(false);
    });

    it('drops the preview on a stale version at apply', async () => {
        const m = model(async (path) => (path === '/regex/preview' ? ok(PREVIEW) : bad('conflict', 'REVISION_CONFLICT')));
        await m.preview();
        await m.apply();
        expect(m.state.failure.message).toBe(LABELS.stale);
        expect(m.state.preview).toBeNull();
        expect(m.canApply()).toBe(false);
    });

    it('retries a network failure of apply with the same snapshot', async () => {
        let applies = 0;
        const request = vi.fn(async (path) => {
            if (path === '/regex/preview') return ok(PREVIEW);
            applies += 1;
            return applies === 1 ? bad('network', 'NETWORK') : ok({ applied_fields: {} });
        });
        const m = model(request);
        await m.preview();
        await m.apply();
        expect(m.state.phase).toBe('failed');
        expect(m.state.failure.retryable).toBe(true);
        await m.retry();
        expect(m.state.phase).toBe('applied');
        expect(request.mock.calls[2][1].body).toEqual(request.mock.calls[1][1].body);
    });

    it('keeps clinical text out of browser storage', async () => {
        const writes = [];
        globalThis.localStorage = { setItem: (...a) => writes.push(a) };
        globalThis.sessionStorage = { setItem: (...a) => writes.push(a) };
        const m = model(async () => ok(PREVIEW));
        await m.preview();
        expect(writes).toEqual([]);
        delete globalThis.localStorage;
        delete globalThis.sessionStorage;
    });

    it('uses an injected translator with German fallback', () => {
        const t = (key) => (key === 'clinic.regex.apply' ? 'Apply' : key);
        const m = createRegexRulesModel({ request: vi.fn() }, { dictationId: 'd', t });
        expect(m.label('apply')).toBe('Apply');
        expect(m.label('preview')).toBe(LABELS.preview);
    });
});

describe('central regex rules', () => {
    const savedRule = {
        id: 'central-1', name: 'Titel übernehmen', pattern: 'Bericht (\\w+)', target_field: 'title',
        priority_order: 100, replacement: null, max_input_length: 5,
    };

    it('loads and saves without a dictation, and preserves the saved rule if an invalid edit is refused', async () => {
        const request = vi.fn()
            .mockResolvedValueOnce(ok({ rules: [] }))
            .mockResolvedValueOnce(ok(savedRule))
            .mockResolvedValueOnce(bad('validation', 'INVALID_PAYLOAD'))
            .mockResolvedValueOnce(ok({ rules: [savedRule] }));
        const create = () => createRegexRulesModel({ request }, { manageRules: true, dictationId: '' });
        const m = create();
        await m.loadRules();
        expect(m.state.rules).toEqual([]);
        m.addRule();
        for (const key of ['name', 'pattern', 'target_field']) m.setRule(0, key, savedRule[key]);
        m.setRule(0, 'max_input_length', '0');
        expect(m.canSave(0)).toBe(false);
        m.setRule(0, 'max_input_length', '5');
        await m.saveRule(0);
        const { id, ...body } = savedRule;
        expect(request).toHaveBeenNthCalledWith(2, '/regex/rules', { method: 'POST', body });
        expect(m.state.notice).toBe('Regel gespeichert.');
        expect(m.canSave(0)).toBe(false);
        m.setRule(0, 'pattern', '(');
        await m.saveRule(0);
        expect(request).toHaveBeenNthCalledWith(3, '/regex/rules', { method: 'POST', body: { ...savedRule, pattern: '(' } });
        expect(m.state.failure.message).toBe(LABELS.invalidRule);
        expect(m.state.rules[0].pattern).toBe('(');
        expect(m.state.savedRules[0].pattern).toBe(savedRule.pattern);
        const reopened = create();
        await reopened.loadRules();
        expect(reopened.state.rules[0]).toMatchObject({ id, pattern: savedRule.pattern, max_input_length: 5 });
    });

    it('tests an unsaved limit inline but only applies a preview of saved rules', async () => {
        const truncated = { ...PREVIEW, resulting_fields: {}, truncated: true };
        const request = vi.fn()
            .mockResolvedValueOnce(ok({ rules: [savedRule] }))
            .mockResolvedValueOnce(ok(truncated))
            .mockResolvedValueOnce(ok(PREVIEW))
            .mockResolvedValueOnce(ok({ applied_fields: PREVIEW.resulting_fields }));
        const m = createRegexRulesModel({ request }, { manageRules: true, dictationId: 'd-2' });
        await m.loadRules();
        m.setRule(0, 'max_input_length', '3');
        m.setText('Vorher: Bericht A');
        await m.preview();
        expect(request.mock.calls[1][1].body).toMatchObject({ dictation_id: 'd-2', rules: [{ max_input_length: 3 }] });
        expect(m.state.preview).toMatchObject({ truncated: true, rows: [] });
        expect(await m.apply()).toBeNull();
        m.setRule(0, 'max_input_length', '5');
        await m.preview();
        const previewBody = { dictation_id: 'd-2', rule_ids: [savedRule.id], text: 'Vorher: Bericht A' };
        expect(request.mock.calls[2][1].body).toEqual(previewBody);
        expect(m.canApply()).toBe(true);
        await m.apply();
        expect(request.mock.calls[3]).toEqual(['/regex/apply', { method: 'POST', body: {
            ...previewBody, rule_version: PREVIEW.rule_version, input_digest: PREVIEW.input_digest,
        } }]);
    });

    it('does not repeat an unconfirmed creation or discard its warning on edit', async () => {
        const request = vi.fn()
            .mockResolvedValueOnce(ok({ rules: [] }))
            .mockResolvedValueOnce(bad('network', 'NETWORK'));
        const m = createRegexRulesModel({ request }, { manageRules: true });
        await m.loadRules();
        m.addRule();
        m.setRule(0, 'name', 'Beispiel');
        m.setRule(0, 'pattern', 'Bericht');
        m.setRule(0, 'target_field', 'title');
        await m.saveRule(0);
        m.setText('Anderer Text');
        await m.retry();
        await m.saveRule(0);
        expect(m.state.failure.message).toContain('Laden Sie die Seite neu');
        expect(request).toHaveBeenCalledTimes(2);
    });
});

describe('regex ui component', () => {
    it('exports an accessible template without hard-coded colours', () => {
        const component = createRegexRulesComponent({ reactive: (x) => x });
        expect(component.template).toContain('role="alert"');
        expect(component.template).toContain('aria-live="polite"');
        expect(component.template).toContain('scope="col"');
        expect(component.template).toContain('@keydown');
        const source = readFileSync(new URL('../../../static/js/clinic/regex-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/\brgba?\(/);
    });
});
