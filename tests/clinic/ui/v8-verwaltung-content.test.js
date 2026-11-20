import { describe, expect, it } from 'vitest';
import { VOCABULARY_VIEW_TEMPLATE } from '../../../static/js/clinic/compose-vocabulary.js';
import { COMMANDS_TEMPLATES_TEMPLATE } from '../../../static/js/clinic/compose-commands-templates.js';
import { ASR_PROFILES_TEMPLATE, createSections, createTrainingController } from '../../../static/js/clinic/compose-asr-profiles.js';

const head = ['class="pagehead"', 'class="eyebrow"', 'class="subline"', 'class="head-actions"'];

describe('V8 prototype structure of the content Verwaltung pages', () => {
    it.each([
        ['vocabulary', VOCABULARY_VIEW_TEMPLATE],
        ['commands and templates', COMMANDS_TEMPLATES_TEMPLATE],
        ['asr profiles', ASR_PROFILES_TEMPLATE],
    ])('%s renders the V8 page head under the v8-page root', (_name, template) => {
        expect(template).toContain('v8-page');
        for (const part of head) expect(template).toContain(part);
        expect(template.match(/<h1[ >]/g)).toHaveLength(1);
    });

    it('uses the prototype tab bar and panel card, keeping tab roles', () => {
        for (const template of [VOCABULARY_VIEW_TEMPLATE, COMMANDS_TEMPLATES_TEMPLATE]) {
            expect(template).toContain('class="tabs" role="tablist"');
            expect(template).toContain('role="tab"');
            expect(template).toContain('class="panel"');
        }
    });

    it('keeps the reload control in the head actions of the commands view', () => {
        expect(COMMANDS_TEMPLATES_TEMPLATE).toMatch(/head-actions">[\s\S]*data-testid="reload"/);
    });

    it('asr sections are cards with table wrappers and status tags', () => {
        const Vue = { shallowRef: (v) => ({ value: v }), onMounted() {}, onBeforeUnmount() {} };
        const s = createSections(Vue);
        for (const part of [s.consent, s.training, s.capabilities]) expect(part.template).toContain('class="panel"');
        expect(s.training.template).toContain('table-wrap');
        expect(s.capabilities.template).toContain('table-wrap');
        expect(s.capabilities.template).toContain('class="tag"');
    });

    it('maps run states to prototype tag variants', () => {
        const c = createTrainingController({ client: { request: async () => ({ outcome: 'ok', data: {} }) } });
        c.state.runs = [
            { id: 'a', kind: 'acoustic', items: 1, state: 'queued' },
            { id: 'b', kind: 'acoustic', items: 1, state: 'failed' },
            { id: 'c', kind: 'acoustic', items: 1, state: 'completed' },
        ];
        expect(c.view().runs.map((r) => r.stateTag)).toEqual(['wait', 'error', '']);
    });
});
