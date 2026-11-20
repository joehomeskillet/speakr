import { describe, expect, it } from 'vitest';
import { buildPage, createHistoryComponent } from '../../../static/js/clinic/history-ui.js';

const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const h = (tag, props, children) => ({ tag, props, children });

function textContent(node) {
    if (typeof node === 'string') return node;
    if (Array.isArray(node)) return node.map(textContent).filter(Boolean).join(' ');
    return node?.children ? textContent(node.children) : '';
}

function renderEntries(events, page = 1, pageSize) {
    const { HistoryEntry } = createHistoryComponent({ h }).components || {};
    expect(HistoryEntry).toBeDefined();
    return buildPage(events, page, { pageSize }).rows.map((entry) => (
        textContent(HistoryEntry.render.call({ entry }))
    ));
}

describe('dictation history labels', () => {
    it('renders the server revision, author and release bindings in German', () => {
        const rows = renderEntries([
            { kind: 'revision', id: id(1), revision_kind: 'ASR', parent_revision_id: null },
            { kind: 'revision', id: id(2), revision_kind: 'HUMAN', parent_revision_id: id(1),
                author: { id: 42, display_name: 'Mara Muster' }, created_at: '2026-07-15T10:30:00+00:00' },
            { kind: 'release', id: id(3), action: 'released', audio_revision_id: id(4),
                transcript_revision_id: id(2), context_revision_id: null },
            { kind: 'release', id: id(5), action: 'superseded', release_id: id(3) },
        ]);
        expect(rows[0]).toContain('Automatische Transkription Version 1');
        expect(rows[1]).toContain('Manuelle Korrektur');
        expect(rows[1]).toContain('Akteur Mara Muster');
        expect(rows[1]).toContain('Zeit 15.07.2026 12:30');
        expect(rows[1]).toContain('Version 2');
        expect(rows[1]).toContain('Vorgängerversion 1');
        expect(rows[2]).toContain('Audioversion Zugeordnet');
        expect(rows[2]).toContain('Textversion 2');
        expect(rows[2]).not.toContain('Kontextversion');
        expect(rows[3]).toContain('Freigabe ersetzt');
        expect(rows.join(' ')).not.toMatch(/00000000-|revision_id|release_id|Nicht verfügbar|\b42\b/);
    });

    it('keeps the predecessor reference across pages', () => {
        const [row] = renderEntries([
            { kind: 'routing', id: id(1), action: 'work_state_transferred' },
            { kind: 'revision', id: id(2), revision_kind: 'ASR' },
            { kind: 'revision', id: id(3), revision_kind: 'HUMAN', parent_revision_id: id(2) },
        ], 2, 2);
        expect(row).toContain('Version 2');
        expect(row).toContain('Vorgängerversion 1');
    });

    it('omits redacted values, unknown codes, opaque actors and invalid dates', () => {
        const rows = renderEntries([
            { kind: 'revision', id: id(1), redacted: true, revision_kind: 'HUMAN',
                author: { display_name: 'Verborgener Name' }, parent_revision_id: id(3), version: 4 },
            { kind: 'workflow', id: id(2), action: 'internal_action', actor: 'internal_actor',
                actor_label: id(3), at: id(4) },
            { kind: 'workflow', id: id(5), action: 'recording.archived', actor_label: 'Alex Muster' },
            { kind: 'private_kind', id: id(6) },
        ]);
        expect(rows[0]).toBe('Manuelle Korrektur');
        expect(rows[1]).toBe('Ablauf');
        expect(rows[2]).toBe('Archiviert Akteur Alex Muster');
        expect(rows[3]).toBe('Ereignis');
    });
});
