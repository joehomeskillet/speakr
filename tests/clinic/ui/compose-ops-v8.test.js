import { describe, expect, it } from 'vitest';

import { IMPORT_TEMPLATE, V8_PAGE, statusView } from '../../../static/js/clinic/compose-import.js';
import { JobsView } from '../../../static/js/clinic/compose-jobs.js';
import { ACTIVITY_TEMPLATE } from '../../../static/js/clinic/compose-activity.js';
import { OutputView, createOutputViewController } from '../../../static/js/clinic/compose-output.js';

// Prototype structure of the operations pages (v8-116): page head, panels, table wrappers, tags.
const TEMPLATES = {
    import: IMPORT_TEMPLATE,
    jobs: JobsView.template,
    activity: ACTIVITY_TEMPLATE,
    output: OutputView.template,
};

describe('operations pages follow the V8 prototype structure', () => {
    for (const [name, template] of Object.entries(TEMPLATES)) {
        it(name + ' has page head, eyebrow, panel and the page root class', () => {
            expect(template).toContain('v8-page');
            expect(template).toContain('class="pagehead"');
            expect(template).toContain('class="eyebrow"');
            expect(template).toContain('<h1');
            expect(template).toContain('class="subline"');
            expect(template).toContain('class="panel"');
        });
        it(name + ' no longer uses the old utility button and heading classes', () => {
            expect(template).not.toContain('px-3 py-2 border rounded');
            expect(template).not.toContain('t-heading');
            expect(template).not.toContain('pill-status');
        });
    }

    it('the root class string carries the page marker and heading restore', () => {
        expect(V8_PAGE.split(' ')).toContain('v8-page');
        expect(V8_PAGE).toContain('[&_h1]:text-[30px]');
    });

    it('tables sit in the prototype table wrapper', () => {
        for (const name of ['import', 'jobs', 'activity']) expect(TEMPLATES[name]).toContain('class="table-wrap"');
    });

    it('keeps the test ids of the existing views', () => {
        for (const id of ['clinic-import-view', 'intent-list', 'inventory-list', 'reload', 'retry', 'assign', 'confirm-assign']) {
            expect(IMPORT_TEMPLATE).toContain('data-testid="' + id + '"');
        }
        for (const id of ['clinic-jobs-view', 'job-list', 'state-filter', 'reload', 'retry', 'empty-state', 'status', 'load-error']) {
            expect(JobsView.template).toContain('data-testid="' + id + '"');
        }
        for (const id of ['history-section', 'statistics-section']) expect(ACTIVITY_TEMPLATE).toContain('data-testid="' + id + '"');
        for (const id of ['output-view', 'revision', 'not-released', 'unavailable']) expect(OutputView.template).toContain('data-testid="' + id + '"');
    });
});

describe('status tags', () => {
    it('maps import statuses to the tag modifiers of the shared stylesheet', () => {
        expect(statusView('OPEN').tag).toBe('wait');
        expect(statusView('FAILED').tag).toBe('error');
        expect(statusView('FULFILLED').tag).toBe('');
        expect(statusView('CANCELLED').tag).toBe('neutral');
        expect(statusView('OPEN').style.border).toContain('var(--');
    });

    it('output results carry the tone that selects the tag modifier', async () => {
        const request = async (path, options) => {
            if (path.endsWith('/output-targets')) {
                return { outcome: 'ok', status: 200, data: { released_revision_id: 'r1', targets: [{ id: 'txt', channel: 'file', available: true, reason: null }] } };
            }
            if (options && options.method === 'POST') return { outcome: 'ok', status: 200, data: { id: 'j1', status: 'ready', format: 'txt' } };
            return { outcome: 'http', status: 404 };
        };
        const c = createOutputViewController({ client: { request }, dictationId: 'd1', newKey: () => 'k1' });
        await c.load();
        await c.exportTo('txt');
        expect(c.results.value.txt.tone).toBe('success');
        expect(c.results.value.txt.phase).toBe('ready');
    });
});