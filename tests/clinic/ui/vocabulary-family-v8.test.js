import { describe, expect, it, vi } from 'vitest';
import { vocabularyEnumLabel, VOCABULARY_TEMPLATE } from '../../../static/js/clinic/vocabulary-ui.js';
import { createVocabularyReviewController, REVIEW_TEMPLATE } from '../../../static/js/clinic/vocabulary-review-ui.js';
import { APP_ACTIONS, commandErrorLabel, commandStepLabel, createVoiceCommandsComponent, validateDefinition } from '../../../static/js/clinic/voice-commands-ui.js';
import { createPanels, ResourcesView } from '../../../static/js/clinic/compose-resources.js';

describe('V8 vocabulary family shared presentation', () => {
    it('labels every search mode and status without changing API values', () => {
        expect(['contains', 'prefix', 'suffix', 'exact', 'active', 'suppressed', 'all'].map(vocabularyEnumLabel))
            .toEqual(['Enthält', 'Beginnt mit', 'Endet mit', 'Genau gleich', 'Aktiv', 'Unterdrückt', 'Alle']);
        expect(vocabularyEnumLabel('FUTURE')).toBe('Unbekannt');
        expect(VOCABULARY_TEMPLATE).toContain(':value="mode"');
        expect(VOCABULARY_TEMPLATE).toContain('class="table-wrap"');
    });

    it('uses the same templates from the resource mount and direct module factory', () => {
        const Vue = {
            reactive: (v) => v, shallowRef: (value) => ({ value }), markRaw: (v) => v,
            computed: (get) => ({ get value() { return get(); } }), watch() {}, onBeforeUnmount() {},
        };
        const client = { switchContext: vi.fn() };
        const mount = ResourcesView.setup({ client, currentUser: { id: 7 }, vue: Vue });
        expect(mount.activeComponent.value.template).toBe(VOCABULARY_TEMPLATE);
        mount.select('commands');
        expect(mount.activeComponent.value.template).toBe(createVoiceCommandsComponent(Vue).template);
        expect(createPanels(Vue).review.template).toBe(REVIEW_TEMPLATE);
        for (const template of [VOCABULARY_TEMPLATE, REVIEW_TEMPLATE, mount.activeComponent.value.template]) {
            expect(template).toContain('v8-page');
            expect(template).toContain('class="actions"');
            expect(template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        }
    });

    it('presents review history by word and German decision while retaining protocol identifiers', async () => {
        const client = {
            request: vi.fn(async () => ({ outcome: 'ok', data: { items: [
                { id: 'proposal-1', written_form: 'Teamnotiz', status: 'PENDING', revision: 2 },
            ] } })),
            command: vi.fn(async () => ({ outcome: 'ok', data: { revision: 3 } })),
        };
        const c = createVocabularyReviewController({ client, scope: { type: 'user', id: 7 } });
        await c.load();
        await c.defer('proposal-1');
        expect(c.view().history[0]).toMatchObject({ itemId: 'proposal-1', writtenForm: 'Teamnotiz', actionLabel: 'Zurückgestellt', statusLabel: 'Zurückgestellt' });
        expect(client.command.mock.calls[0][0].payload).toEqual({ proposal_id: 'proposal-1', decision: 'defer' });
        c.state.items[0].status = 'FUTURE';
        expect(c.view().items[0].statusLabel).toBe('Unbekannter Status');
    });

    it('shows readable command previews and validation without changing executable steps', () => {
        for (const step of Object.keys(APP_ACTIONS)) {
            expect(commandStepLabel(step)).not.toBe(step);
            expect(commandStepLabel(step)).not.toContain('_');
        }
        expect(commandStepLabel('text:Bonjour')).toBe('Text einfügen: Bonjour');
        const errors = validateDefinition({ kind: 'macro', phrase: '', action_ids: [] });
        expect(errors.map(commandErrorLabel)).toEqual(['Bitte eine Auslösephrase eingeben.', 'Bitte mindestens eine Aktion eingeben.']);
        for (const code of ['macro_cycle', 'macro_too_long', 'unknown_action', 'confirmation_required', 'stale_version']) {
            expect(commandErrorLabel(code)).not.toContain('_');
        }
        const template = createVoiceCommandsComponent({}).template;
        expect(template).toContain('errorLabel(error)');
        expect(template).toContain('role="alert"');
        expect(template).toContain('@click="confirm"');
    });
});
