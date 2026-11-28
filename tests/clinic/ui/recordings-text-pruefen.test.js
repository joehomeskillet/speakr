import { expect, it } from 'vitest';
import { resolveClinicOpenTarget } from '../../../static/js/clinic/compose-correction.js';
import { RecordingsView } from '../../../static/js/clinic/compose-recordings.js';
import { createRecordingsController, createRecordingsMemory, TEXT } from '../../../static/js/clinic/recordings-ui.js';

it('offers a native Text prüfen action only with an explicit correction grant and keeps details separate', async () => {
    const user = { id: 7, role: 'author' };
    const items = [
        { dictation_id: 'correctable', workflow_status: 'zu_korrigieren', viewer_can_correct: true },
        { dictation_id: 'no-grant', workflow_status: 'zu_korrigieren' },
        { dictation_id: 'writing', workflow_status: 'zu_schreiben', viewer_can_correct: false },
        { dictation_id: 'private-note', workflow_status: 'neu_angelegt', recording_type: 'notiz' },
    ];
    const targets = [];
    const c = createRecordingsController({
        client: { request: async () => ({ outcome: 'ok', data: { items } }) },
        currentUser: user,
        memory: createRecordingsMemory(),
        onOpen: (payload) => targets.push(resolveClinicOpenTarget(payload, { view: 'clinic-recordings', user })),
    });
    await c.start();
    const [correctable, ...others] = c.state.rows;
    expect(correctable.canCorrect).toBe(true);
    expect(TEXT.checkText).toBe('Text prüfen');
    const template = RecordingsView.template;
    const button = template.match(/<button v-if="r\.canCorrect"[^>]*>/)?.[0];
    expect(button).toContain('type="button"');
    expect(button).toContain(`@click="c.open(r, 'correction')"`);
    expect(button).not.toMatch(/tabindex|disabled/);
    expect(template).toContain('{{ t.checkText }}');
    expect(template).toContain(`@click="c.open(r, 'details')"`);
    expect(c.open(correctable, 'correction')).toBe(true);
    for (const row of others) {
        expect(row.canCorrect).toBe(false);
        expect(c.open(row, 'correction')).toBe(false);
    }
    expect(c.open(correctable, 'details')).toBe(true);
    expect(targets).toEqual([
        { surface: 'correction', dictationId: 'correctable', openMode: 'correction' },
        { surface: 'details', dictationId: 'correctable', openMode: null },
    ]);
});
