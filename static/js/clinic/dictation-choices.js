import { buildPath, formatTime, formatDay } from './recordings-ui.js';

// Choices contain only metadata already visible through the recordings policy.
export function createDictationChoices({ client, makeState = (s) => s, authorId = null }) {
    const state = makeState({ rows: [], cursor: null, loading: false, message: '' });
    let generation = 0;
    function clear() {
        generation += 1;
        Object.assign(state, { rows: [], cursor: null, loading: false, message: '' });
    }
    async function load(more = false) {
        const cursor = more ? state.cursor : null;
        if (more && (!cursor || state.loading)) return;
        const mine = ++generation;
        if (!more) state.rows = [];
        state.loading = true;
        state.message = '';
        const result = await client.request(buildPath(authorId == null ? {} : { author_id: authorId }, cursor, 50));
        if (mine !== generation) return;
        state.loading = false;
        if (result.outcome !== 'ok' || !Array.isArray(result.data?.items)) {
            state.rows = [];
            state.cursor = null;
            state.message = result.outcome === 'permission' ? 'Keine Berechtigung für diese Auswahl.' : 'Auswahl konnte nicht geladen werden. Bitte erneut laden.';
            return;
        }
        const seen = new Set(state.rows.map((r) => r.id));
        for (const row of result.data.items) {
            if (!row.dictation_id || seen.has(row.dictation_id)) continue;
            if (authorId != null && (String(row.dictated_by?.id) !== String(authorId)
                || ['fertiggestellt', 'zu_archivieren', 'zu_loeschen'].includes(row.workflow_status))) continue;
            seen.add(row.dictation_id);
            const label = [row.dictated_by?.display_name || 'Name nicht verfügbar',
                row.case_display, formatDay(row.day), formatTime(row.sort_time)].filter(Boolean).join(' · ');
            state.rows.push({ id: row.dictation_id, label: `${label} · Aufnahme ${state.rows.length + 1}` });
        }
        state.cursor = result.data.next_cursor || null;
        if (!state.rows.length) state.message = 'Keine passenden Diktate im eigenen Berechtigungsbereich.';
    }
    function resolve(text) {
        const value = String(text || '').trim();
        const match = state.rows.find((r) => r.label === value);
        // Legacy direct IDs remain optional; the destination still checks permissions.
        return match ? match.id : (/^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : null);
    }
    return { state, load, clear, resolve };
}
