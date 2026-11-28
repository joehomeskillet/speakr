import { describe, expect, it } from 'vitest';
import { toViewRow } from '../../../static/js/clinic/recordings-ui.js';
import { statusBadge } from '../../../static/js/clinic/compose-worklist.js';
import { createJobsViewController } from '../../../static/js/clinic/compose-jobs.js';

describe('processing state reaches list and scoped jobs consumers', () => {
    it.each([
        ['QUEUED', 'PENDING', 'Text wird erstellt', 'Transkription wartet'],
        ['PROCESSING', 'RUNNING', 'Text wird erstellt', 'Transkription läuft'],
        ['FAILED', 'FAILED', 'Text konnte nicht erstellt werden', 'Transkription fehlgeschlagen'],
        ['COMPLETED', 'SUCCEEDED', 'Text erstellt', 'Transkription abgeschlossen'],
    ])('%s has the expected German label in each list', (processing, asr, recordingsLabel, worklistLabel) => {
        const item = { dictation_id: 'd', workflow_status: 'neu_angelegt', processing_status: processing };
        if (asr === 'SUCCEEDED') item.text_created = true;
        expect(toViewRow(item).processing).toBe(recordingsLabel);
        expect(statusBadge({ status: 'neu_angelegt', asr_state: asr }).label).toContain(worklistLabel);
    });

    it('only shows text as created if the recordings API confirms it', () => {
        expect(toViewRow({ dictation_id: 'd', processing_status: 'COMPLETED' }).processing).toBe('');
    });

    it('scopes all job states and never offers retry on a nonfailed job', async () => {
        const jobs = ['queued', 'processing', 'completed', 'failed'].map((state, i) => ({
            id: i + 1, dictation_id: 'mine', job_type: 'transcribe', state, version: 0,
        }));
        const c = createJobsViewController({ dictationId: 'mine', canRetry: true, client: {
            request: async () => ({ outcome: 'ok', data: { jobs: [...jobs,
                { id: 99, dictation_id: 'foreign', state: 'failed' }], recent_failures: [jobs[3]], counts: {} } }),
        } });
        await c.load();
        expect(c.rows.value.map(r => r.id)).toEqual([1, 2, 3, 4]);
        expect(c.rows.value.map(r => c.canRetryJob(r))).toEqual([false, false, false, true]);
        c.setFilter('processing');
        expect(c.rows.value.map(r => r.id)).toEqual([2]);
        expect(c.filterLabel('completed')).toBe('Abgeschlossen');
    });
});
