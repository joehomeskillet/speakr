import { describe, expect, it, vi } from 'vitest';
import { formatDictationReference } from '../../../static/js/clinic/worklist-ui.js';
import { createRecordingsController, createRecordingsMemory, toViewRow } from '../../../static/js/clinic/recordings-ui.js';
import { createWorklistViewController } from '../../../static/js/clinic/compose-worklist.js';

// Deliberately identical abbreviations: display references must never become keys.
const ids = ['12345678-1234-4123-8123-123456789abc', '12345678-5678-4567-8567-987654329abc'];
const items = ids.map(dictation_id => ({ dictation_id, status: 'zu_schreiben', workflow_status: 'zu_schreiben' }));
const ok = data => ({ outcome: 'ok', status: 200, data });

describe('display-only list references', () => {
    it('keeps short references intact and abbreviates long identifiers deterministically', () => {
        expect(formatDictationReference('4524')).toBe('#4524');
        expect(formatDictationReference(ids[0])).toBe('#12345678…9abc');
        expect(formatDictationReference(ids[1])).toBe('#12345678…9abc');
        for (const empty of [null, undefined, '']) expect(formatDictationReference(empty)).toBe('');
    });

    it('retains verified case display and ignores title and notes', () => {
        const row = toViewRow({ ...items[0], case_display: 'F-77', title: 'Hidden title', notes: 'Hidden note' });
        expect(row.heading).toBe('Fall-ID F-77');
        expect(row.reference).toBe('#12345678…9abc');
        expect(JSON.stringify(row)).not.toMatch(/Hidden title|Hidden note/);
        expect(row.dictationId).toBe(ids[0]);
        expect(toViewRow(items[0]).ariaLabel).toContain('Diktat #12345678…9abc');
    });

    it('opens both recordings by complete ID despite a display collision and rejects absent IDs', async () => {
        const onOpen = vi.fn();
        const c = createRecordingsController({
            client: { request: vi.fn(async () => ok({ items, next_cursor: null })) },
            currentUser: { id: 7, role: 'typist' }, memory: createRecordingsMemory(), onOpen,
        });
        await c.start();
        for (const row of c.state.rows) expect(c.open(row)).toBe(true);
        expect(onOpen.mock.calls.map(([payload]) => payload.dictationId)).toEqual(ids);
        expect(c.open(toViewRow({}))).toBe(false);
        expect(c.open(null)).toBe(false);
        c.dispose();
    });

    it('keeps worklist checkbox selection and opening keyed by complete ID', async () => {
        const onOpen = vi.fn();
        const c = createWorklistViewController({
            client: { request: vi.fn(async path => ok(path.startsWith('/worklist/rows')
                ? { items, total: 2 } : { items: [] })), switchContext: vi.fn() },
            currentUser: { id: 7, role: 'staff' }, storage: null, intervalMs: 0, onOpen,
            fetchChanges: vi.fn(async () => ok({ changes: [], next_cursor: 'c1', has_more: false })),
        });
        await c.start();
        expect(c.referenceLabel(items[0])).toBe(c.referenceLabel(items[1]));
        c.toggleChecked(items[0]);
        expect(c.isChecked(items[1])).toBe(false);
        for (const row of items) {
            expect(c.open(row)).toBe(true);
            expect(c.selectedId.value).toBe(row.dictation_id);
        }
        expect(onOpen.mock.calls.map(([payload]) => payload.dictationId)).toEqual(ids);
        c.select(null);
        expect(c.open()).toBe(false);
        c.dispose();
    });
});
