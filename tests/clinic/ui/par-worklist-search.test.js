import { describe, expect, it, vi } from 'vitest';
import {
    createWorklistViewController, searchMetadata, searchSegments,
} from '../../../static/js/clinic/compose-worklist.js';

const matches = (text, query) => searchSegments(text, query).filter((part) => part.match).map((part) => part.text);

describe('PAR-WF-003 metadata highlighting', () => {
    it('marks every literal, case-insensitive term, including repeated and overlapping matches', () => {
        expect(matches('Befund, befund und Termin', 'BEFUND termin')).toEqual(['Befund', 'befund', 'Termin']);
        expect(matches('Banana', 'ana')).toEqual(['anana']);
        expect(matches('a+b [x] .*', '[x] .*')).toEqual(['[x]', '.*']);
        expect(searchSegments('Titel', ' \t ')).toEqual([{ text: 'Titel', match: false }]);
        expect(searchSegments(null, 'Titel')).toEqual([]);
    });

    it('normalizes accents and preserves sharp-s and following match boundaries', () => {
        expect(matches('A\u0308rztin Straße STRASSE Ende', 'ärztin STRASSE ende')).toEqual(['Ärztin', 'Straße', 'STRASSE', 'Ende']);
        expect(matches('😀 Straße!', 'ss')).toEqual(['ß']);
        expect(searchSegments('😀 Straße!', 'ss').map((part) => part.text).join('')).toBe('😀 Straße!');
    });

    it('exposes only matching searched metadata, never unsearched or inactive fields', () => {
        const row = { title: 'Befund', notes: 'Befund morgen', recipient: 'Team', transcript: 'Befund geheim' };
        expect(searchMetadata(row, 'BEFUND').map((field) => field.key)).toEqual(['title', 'notes']);
        expect(searchMetadata(row, 'Team').map((field) => field.key)).toEqual(['recipient']);
        expect(searchMetadata(row, '')).toEqual([]);
        expect(searchMetadata(row, 'geheim')).toEqual([]);
        expect(searchMetadata({}, 'Befund')).toEqual([]);
        expect(matches('<img src=x onerror=alert(1)>', 'img')).toEqual(['img']);
    });

    it('uses the applied search and only server-returned rows; clearing and denial remove matches', async () => {
        let allowed = true;
        const own = { dictation_id: 'own', author_user_id: 7, status: 'zu_schreiben', title: 'Befund', notes: 'Befund morgen' };
        const client = {
            switchContext: vi.fn(),
            request: vi.fn(async () => allowed
                ? { outcome: 'ok', status: 200, data: { items: [own], total: 1 } }
                : { outcome: 'permission', status: 403 }),
        };
        const c = createWorklistViewController({ client, currentUser: { id: 7 }, storage: null, intervalMs: 0 });
        try {
            await c.applyFilter(null, 'Befund');
            expect(client.request.mock.calls[0][0]).toContain('search=Befund');
            expect(c.displayed.value.filter((item) => item.kind === 'row').map((item) => item.row)).toEqual([own]);
            expect(c.searchMetadata(own).map((field) => field.key)).toEqual(['title', 'notes']);
            c.filters.state.query = 'morgen';
            expect(c.searchMetadata(own)[0].segments).toEqual([{ text: 'Befund', match: true }]);
            await c.applyFilter(null, '');
            expect(c.searchMetadata(own)).toEqual([]);
            allowed = false;
            await c.applyFilter(null, 'Befund');
            expect(c.displayed.value).toEqual([]);
            expect(c.worklist.isPermissionLost.value).toBe(true);
        } finally {
            c.dispose();
        }
    });
});
