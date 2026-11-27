import { describe, expect, it, vi } from 'vitest';
import { useCorrectionSave } from '../../../static/js/clinic/correction-save-ui.js';

const ref = (value) => ({ value });
const lines = ['🩺 A\u0308rztin', 'Plan bleibt.', 'Plan bleibt.'];
const text = lines.join('\n').normalize('NFC');
const snapshot = {
    schema_version: 1,
    nodes: lines.map((line, i) => ({ id: `p${i}`, kind: 'paragraph', level: 0, runs: [
        { id: `r${i}`, text: line, bold: false, italic: false, underline: false, audio_anchor_id: null },
    ] })),
};
const links = text.split('\n').map((line, i, all) => {
    const start = all.slice(0, i).reduce((offset, part) => offset + part.length + 1, 0);
    return { id: `l${i}`, text_start: start, text_end: start + line.length,
        audio_start_ms: i * 2000, audio_end_ms: i * 2000 + 1000 };
});

async function open(current = { document: snapshot, document_version: 2, links }, options = {}) {
    const editor = { text: ref(text), textRevisionId: ref('rev'), dirty: ref(false), etag: ref('"rev.g7"'),
        canEdit: ref(true), lease: ref({ status: 'held', generation: 7 }), audio: ref({ revisionId: 'audio' }) };
    const lease = { getState: () => editor.lease.value, subscribe: vi.fn(), setUnsaved: vi.fn() };
    const seek = vi.fn();
    const base = { text, text_revision_id: 'rev', etag: '"rev.g7"', audio_revision_id: 'audio', ...current };
    const client = { request: vi.fn(async (_path, request) => request ? {
        outcome: 'ok', data: { text_revision_id: 'rev', etag: '"rev.g7"', state_version: 8,
            document: request.body.document, document_version: request.body.expected_document_version + 1 },
    } : { outcome: 'ok', data: base }) };
    const saver = useCorrectionSave({ editor, lease, client, dictationId: 'd', document: true,
        seek, autosaveMs: 0, ...options });
    await saver.loadResume();
    return { saver, editor, lease, client, seek, doc: saver.document };
}

describe('correction document adapter', () => {
    it('uses the same undo, dirty and atomic save path for typing, formatting and moving', async () => {
        const { doc, editor, saver, lease, client } = await open();
        doc.select('p1', 'r1');
        expect(doc.toggleFormat('bold')).toBe(true);
        expect(editor.text.value).toBe(text);
        expect(saver.state.value).toBe('unsaved');
        expect(lease.setUnsaved).toHaveBeenLastCalledWith(text);
        expect(doc.editRun('p0', 'r0', 'Manuell geändert.')).toBe(true);
        expect(doc.moveParagraph(1)).toBe(true);
        expect(doc.undo()).toBe(true);
        expect(doc.undo()).toBe(true);
        expect(doc.doc.value.nodes[1].runs[0].bold).toBe(true);
        expect(doc.undo()).toBe(true);
        expect(doc.doc.value).toEqual(snapshot);
        expect(doc.redo()).toBe(true);
        expect(doc.moveParagraph(1)).toBe(true);
        expect(doc.redo()).toBe(false);
        expect(doc.selection.value).toEqual({ nodeId: 'p1', runId: 'r1' });

        expect((await saver.save()).state).toBe('saved');
        const [path, request] = client.request.mock.lastCall;
        expect(path).toBe('/dictations/d/correction/save');
        expect(request.ifMatch).toBe('"rev.g7"');
        expect(request.body).toMatchObject({ text, expected_document_version: 2, lease_generation: 7 });
        expect(request.body.document.nodes.map((node) => node.id)).toEqual(['p0', 'p2', 'p1']);
        expect(request.body.document.nodes[2].runs[0].bold).toBe(true);
        expect(request.body.move_map).toEqual([
            { segment_id: 'r2', old_start: 23, old_end: 35, new_start: 10, new_end: 22 },
            { segment_id: 'r1', old_start: 10, old_end: 22, new_start: 23, new_end: 35 },
        ]);
        expect(editor.dirty.value).toBe(false);
    });

    it('follows original audio by run ID without touching the document, selection or dirty state', async () => {
        const { doc, editor, seek } = await open();
        doc.select('p1', 'r1');
        doc.moveParagraph(1);
        expect(doc.seekToSegment('r1')).toBe(true);
        expect(seek).toHaveBeenCalledWith(2);
        expect(doc.seekToSegment('r2')).toBe(true);
        expect(seek).toHaveBeenCalledWith(4);
        const document = doc.doc.value;
        const selection = doc.selection.value;
        expect(doc.highlightAt(2.5)).toBe('r1');
        expect(doc.activeRunId.value).toBe('r1');
        expect(doc.highlightAt(null)).toBeNull();
        expect(doc.highlightAt(3.5)).toBeNull();
        expect(doc.doc.value).toBe(document);
        expect(doc.selection.value).toBe(selection);
        expect(editor.dirty.value).toBe(true);
        doc.editRun('p1', 'r1', 'Neu eingegeben.');
        expect(doc.seekToSegment('r1')).toBe(false);
        expect(doc.highlightAt(2.5)).toBeNull();
        editor.audio.value = { revisionId: 'another-audio' };
        expect(doc.highlightAt(4.5)).toBeNull();
    });

    it('keeps newer same-text edits dirty and reconciles the original body and key after a lost answer', async () => {
        const { client, doc, saver, editor } = await open(undefined, { reconcileFirst: true });
        doc.select('p0', 'r0');
        doc.toggleFormat('bold');
        client.request.mockResolvedValueOnce({ outcome: 'network' });
        expect((await saver.save()).retryable).toBe(true);
        const request = client.request.mock.lastCall[1];
        doc.toggleFormat('italic');
        expect((await saver.save()).state).toBe('unsaved');
        expect(client.request.mock.lastCall[1]).toEqual(request);
        expect(editor.dirty.value).toBe(true);
        expect((await saver.save()).state).toBe('saved');
        const latest = client.request.mock.lastCall[1];
        expect(latest.idempotencyKey).not.toBe(request.idempotencyKey);
        expect(latest.body.expected_document_version).toBe(3);
        expect(latest.body.document.nodes[0].runs[0]).toMatchObject({ bold: true, italic: true });
    });

    it('seeds legacy audio provenance before the first move, even with autosave disabled', async () => {
        const { client, doc, saver, editor } = await open({ links });
        const [first] = doc.doc.value.nodes;
        doc.select(first.id, first.runs[0].id);
        doc.moveParagraph(1);
        expect((await saver.save()).state).toBe('saved');
        const writes = client.request.mock.calls.slice(1).map((call) => call[1].body);
        expect(writes).toHaveLength(2);
        expect(writes[0]).toMatchObject({ text, expected_document_version: 0 });
        expect(writes[0]).not.toHaveProperty('move_map');
        expect(writes[1]).toMatchObject({ expected_document_version: 1, expected_state_version: 8 });
        expect(writes[1].move_map.some((move) => move.segment_id === first.runs[0].id)).toBe(true);
        expect(editor.dirty.value).toBe(false);
    });

    it('keeps separate audio segments in one legacy paragraph when the user edits one', async () => {
        const start = links[1].text_start;
        const { doc, seek } = await open({ links: [
            { ...links[1], text_end: start + 4 },
            { ...links[1], text_start: start + 5, audio_start_ms: 3000, audio_end_ms: 3500 },
        ] });
        const node = doc.doc.value.nodes[1];
        expect(node.runs.map((run) => run.text)).toEqual(['Plan', ' ', 'bleibt.']);
        doc.editRun(node.id, node.runs[0].id, 'Termin');
        expect(doc.seekToSegment(node.runs[0].id)).toBe(false);
        expect(doc.seekToSegment(node.runs[2].id)).toBe(true);
        expect(seek).toHaveBeenCalledWith(3);
    });

    it('restores the server document after conflict and blocks history mutations after lease loss', async () => {
        const { client, doc, saver, editor } = await open();
        doc.select('p0', 'r0');
        doc.toggleFormat('bold');
        client.request.mockResolvedValueOnce({ outcome: 'conflict', status: 409 });
        expect((await saver.save()).state).toBe('conflict');
        expect(doc.doc.value.nodes[0].runs[0].bold).toBe(true);
        expect((await saver.takeServer()).state).toBe('saved');
        expect(doc.doc.value).toEqual(snapshot);
        expect(doc.undo()).toBe(false);
        doc.select('p0', 'r0');
        doc.toggleFormat('italic');
        editor.canEdit.value = false;
        expect(doc.undo()).toBe(false);
        expect(doc.moveParagraph(1)).toBe(false);
        expect(doc.toggleFormat('bold')).toBe(false);
    });

    it('keeps the draft on the newest server snapshot when the user chooses their own changes', async () => {
        const { client, doc, saver } = await open();
        doc.select('p0', 'r0');
        doc.toggleFormat('bold');
        client.request.mockResolvedValueOnce({ outcome: 'conflict', status: 409 });
        client.request.mockResolvedValueOnce({ outcome: 'ok', data: {
            text, text_revision_id: 'rev', etag: '"rev.g7"', document: snapshot, document_version: 3,
            audio_revision_id: 'audio', links, state: { version: 9, audio_position_ms: 15 },
        } });
        expect((await saver.save()).state).toBe('conflict');
        expect((await saver.keepMine()).state).toBe('unsaved');
        expect((await saver.save()).state).toBe('saved');
        const { body } = client.request.mock.lastCall[1];
        expect(body).toMatchObject({ expected_document_version: 3, expected_state_version: 9 });
        expect(body.document.nodes[0].runs[0].bold).toBe(true);
    });
});
