import { describe, expect, it } from 'vitest';
import { recordingLabel, RecordingsView } from '../../../static/js/clinic/compose-recordings.js';
import { dictationLabel, WorklistView } from '../../../static/js/clinic/compose-worklist.js';
import { toViewRow } from '../../../static/js/clinic/recordings-ui.js';

const item = {
    dictation_id: '00000000-0000-4000-8000-000000000001',
    day: '2026-11-20', sort_time: '2026-11-20T08:30:00Z', time_source: 'recorded',
    title: 'PRIVATE_TITLE', notes: 'PRIVATE_NOTE', recipient: 'PRIVATE_RECIPIENT',
    patient_name: 'PRIVATE_NAME', workflow_status: 'neu_angelegt', processing_status: 'COMPLETED',
};

describe('safe primary list headings', () => {
    it('uses the bound case for a worklist heading and retains the date without one', () => {
        const datedRow = { ...item, created_at: item.sort_time };
        expect(dictationLabel({ ...datedRow, case_display: 'SYN-0002' })).toBe('Fall-ID SYN-0002');
        expect(dictationLabel({ ...datedRow, case_display: null })).toBe('Diktat vom 20.11.2026, 09:30');
    });

    it('prefers the approved case display, including long identifiers, without reading free text', () => {
        const case_display = 'SYNTHETIC-' + 'LONG-CASE-'.repeat(12);
        const row = toViewRow({ ...item, case_display });
        expect(recordingLabel(row)).toBe('Fall-ID ' + case_display);
        expect(JSON.stringify(row)).not.toContain('PRIVATE_');
        expect(row.reference).toBe('#00000000…0001');
    });

    it('uses supplied date, time and provenance when no case display exists', () => {
        const recorded = recordingLabel(toViewRow(item));
        expect(recorded).toBe('Aufnahme · Freitag, 20.11.2026, 09:30');
        for (const [time_source, prefix] of [['imported', 'Importdatum'], ['created', 'Erstelldatum']]) {
            expect(recordingLabel(toViewRow({ ...item, time_source }))).toBe(prefix + ' · Freitag, 20.11.2026, 09:30');
        }
        expect(recordingLabel(toViewRow({ dictation_id: '1' }))).toBe('Diktat · Ohne Datum');
    });

    it('renders safe headings first, references second and both named status axes', () => {
        expect(RecordingsView.template).toContain('class="record-title">{{ recordingLabel(r) }}');
        expect(RecordingsView.template).toContain('<span>Diktat {{ r.reference }}</span>');
        expect(WorklistView.template).toContain('<strong>{{ c.dictationLabel(item.row) }}</strong>');
        expect(WorklistView.template).toContain('class="cl-row-reference">{{ c.referenceLabel(item.row) }}');
        for (const template of [RecordingsView.template, WorklistView.template]) {
            expect(template).toContain('Arbeitsstand:');
            expect(template).toContain('Spracherkennung:');
            expect(template).toContain('Vollständige Diktatkennung');
        }
        expect(RecordingsView.template).toContain(':aria-describedby=');
        expect(dictationLabel({ ...item, created_at: item.sort_time })).toBe('Diktat vom 20.11.2026, 09:30');
        expect(dictationLabel(item)).toBe('Diktat');
    });
});
