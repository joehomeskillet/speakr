import { describe, expect, it, vi } from 'vitest';
import { createSections, createTrainingController, TEXT } from '../../../static/js/clinic/compose-asr-profiles.js';

const ok = (data) => ({ outcome: 'ok', data });

function clientWith(manifests, runs) {
    return {
        request: vi.fn(async (path, opts) => {
            if (opts && opts.method === 'POST') return ok({ state: 'scheduled', job_id: 12 });
            if (String(path).includes('/manifest')) {
                const kind = new URL(path, 'http://clinic.local').searchParams.get('kind');
                return ok(manifests[kind]);
            }
            return ok({ runs });
        }),
    };
}

const unavailable = {
    language_model: { capability: 'capability_unavailable', learning_claimed: false, items: [], exclusions: [] },
    acoustic: { capability: 'capability_unavailable', learning_claimed: false, items: [], exclusions: [] },
};

describe('PAR-ASR-004 training capability panel', () => {
    it('keeps text and acoustic adaptation as two choices and does not call a word list training', () => {
        const view = createTrainingController({ client: clientWith(unavailable, []) }).view();
        expect(view.kinds.map((kind) => kind.value)).toEqual(['language_model', 'acoustic']);
        expect(view.capabilities.map((row) => row.value)).toEqual(['language_model', 'acoustic']);
        expect(TEXT.wordListHint).toContain('Wortliste');
        expect(TEXT.wordListHint.toLowerCase()).not.toContain('training');
        expect(TEXT.subline.toLowerCase()).not.toContain('training');
    });

    it('shows an unverified capability as unavailable and not as adaptation after a run starts', async () => {
        const runs = [
            { job_id: 11, kind: 'language_model', scope: 'author', items: 4, state: 'completed' },
            { job_id: 10, kind: 'acoustic', scope: 'author', items: 4, state: 'completed' },
        ];
        const controller = createTrainingController({
            client: clientWith(unavailable, runs),
            profileClient: { request: vi.fn(async () => ok({ profile: { version: 3 } })) },
        });
        expect(await controller.start()).toBe(true);
        const view = controller.view();
        expect(view.capabilities.map((row) => row.status)).toEqual([TEXT.unavailable, TEXT.unavailable]);
        expect(view.runs.map((run) => run.adaptation)).toEqual([TEXT.noAdaptation, TEXT.noAdaptation]);
        expect(view.runs.map((run) => run.stateLabel).join(' ')).not.toContain('Erledigt');
        const template = createSections({}).training.template;
        expect(template).toContain('data-testid="adaptation-capabilities"');
        expect(template).toContain('data-testid="adaptation-gap"');
        expect(template.indexOf('adaptation-capabilities')).toBeLessThan(template.indexOf('scheduleHeading'));
    });

    it('names a proven capability without turning the job status into that proof', async () => {
        const manifests = {
            language_model: { capability: 'runtime_passed', learning_claimed: true, items: [{}], exclusions: [] },
            acoustic: unavailable.acoustic,
        };
        const controller = createTrainingController({ client: clientWith(manifests, [
            { job_id: 9, kind: 'language_model', items: 2, state: 'completed' },
        ]) });
        await controller.load();
        const view = controller.view();
        expect(view.capabilities.map((row) => row.proven)).toEqual([true, false]);
        expect(view.runs[0].stateLabel).toBe('Abgeschlossen');
        expect(view.runs[0].adaptation).toBe('');
    });
});
