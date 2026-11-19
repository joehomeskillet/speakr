import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
    RichTextEditor, exportLosses, sanitizePaste, useRichTextEditor,
} from '../../../static/js/clinic/richtext-editor-ui.js';

function sample() {
    return {
        schema_version: 1,
        nodes: [
            { id: 'n1', kind: 'paragraph', level: 0, runs: [
                { id: 'a', text: 'Hallo', bold: false, italic: false, underline: false, audio_anchor_id: 'L1' },
                { id: 'b', text: ' Welt', bold: false, italic: false, underline: false, audio_anchor_id: 'L2' },
            ] },
            { id: 'n2', kind: 'heading', level: 1, runs: [
                { id: 'c', text: 'Titel', bold: false, italic: false, underline: false, audio_anchor_id: null },
            ] },
        ],
    };
}

function make(response, extra) {
    const client = { request: vi.fn(async () => response || { outcome: 'ok', data: { version: 4 } }) };
    const editor = useRichTextEditor({
        client, savePath: '/dictations/d1/richtext', document: sample(), version: 3,
        newId: (() => { let i = 0; return () => `new${++i}`; })(), ...(extra || {}),
    });
    return { client, editor };
}

describe('richtext editor ui', () => {
    it('keeps node and run IDs through edits and selection', () => {
        const { editor } = make();
        editor.select('n1', 'b');
        editor.editRun('n1', 'b', ' Welt!');
        editor.toggleFormat('bold');
        const ids = editor.doc.value.nodes.flatMap((n) => [n.id, ...n.runs.map((r) => r.id)]);
        expect(ids).toEqual(['n1', 'a', 'b', 'n2', 'c']);
        expect(editor.selection.value).toEqual({ nodeId: 'n1', runId: 'b' });
        expect(editor.doc.value.nodes[0].runs[1].bold).toBe(true);
    });

    it('marks edited and inserted text unaligned and leaves other anchors', () => {
        const { editor } = make();
        editor.editRun('n1', 'a', 'Hallo,');
        const id = editor.insertRun('n1', 1, 'neu');
        const runs = editor.doc.value.nodes[0].runs;
        expect(runs.map((r) => r.id)).toEqual(['a', id, 'b']);
        expect(runs[0].audio_anchor_id).toBeNull();
        expect(runs[1].audio_anchor_id).toBeNull();
        expect(runs[2].audio_anchor_id).toBe('L2');
    });

    it('sanitizes pasted markup and control characters', () => {
        expect(sanitizePaste('a<script>x</script>b\u0000c<img src=x')).toEqual({ text: 'axbcimg src=x', changed: true });
        expect(sanitizePaste('plain\ntext')).toEqual({ text: 'plain\ntext', changed: false });
        const { editor } = make();
        editor.paste('n1', 'a', '<b>fett</b>\u0007');
        expect(editor.doc.value.nodes[0].runs[0].text).toBe('Hallofett');
        expect(editor.announcement.value).toBe('Eingefügter Text wurde bereinigt.');
    });

    it('surfaces export losses before export', () => {
        const { editor } = make();
        expect(editor.losses.value.map((l) => l.code)).toEqual(['heading', 'plain_unformatted', 'html_anchors']);
        editor.toggleFormat('underline', 'n1', 'a');
        expect(editor.losses.value.map((l) => l.code)).toContain('underline');
        expect(exportLosses({ nodes: [] })).toEqual([]);
    });

    it('saves with If-Match and idempotency key, then advances version', async () => {
        const { client, editor } = make();
        editor.editRun('n1', 'a', 'Neu');
        expect(await editor.save()).toBe('saved');
        const [path, options] = client.request.mock.calls[0];
        expect(path).toBe('/dictations/d1/richtext');
        expect(options.ifMatch).toBe('3');
        expect(options.body.expected_version).toBe(3);
        expect(options.idempotencyKey.length).toBeGreaterThanOrEqual(8);
        expect(editor.version.value).toBe(4);
        expect(editor.dirty.value).toBe(false);
    });

    it('stale version keeps local text and replace loads the current snapshot', async () => {
        const { editor } = make({ outcome: 'conflict' });
        editor.editRun('n1', 'a', 'Lokal');
        expect(await editor.save()).toBe('stale');
        expect(editor.doc.value.nodes[0].runs[0].text).toBe('Lokal');
        expect(editor.replace(sample(), 5)).toBe(true);
        expect(editor.version.value).toBe(5);
        expect(editor.replace({ schema_version: 2, nodes: [] }, 6)).toBe(false);
    });

    it('unauthorized response and canEdit false block edits', async () => {
        const { editor } = make({ outcome: 'permission' });
        editor.editRun('n1', 'a', 'x');
        expect(await editor.save()).toBe('denied');
        expect(editor.editRun('n1', 'b', 'y')).toBe(false);
        const ro = make(null, { canEdit: false }).editor;
        expect(ro.insertRun('n1', 0, 'z')).toBeNull();
        expect(ro.readOnly.value).toBe(true);
    });

    it('retry after network failure reuses the idempotency key', async () => {
        const { client, editor } = make();
        client.request.mockResolvedValueOnce({ outcome: 'network' });
        editor.editRun('n1', 'a', 'x');
        expect(await editor.save()).toBe('failed');
        expect(await editor.save()).toBe('saved');
        const keys = client.request.mock.calls.map((c) => c[1].idempotencyKey);
        expect(keys[0]).toBe(keys[1]);
    });

    it('rejects an invalid document and writes nothing to browser storage', () => {
        const writes = [];
        globalThis.localStorage = { setItem: (...a) => writes.push(a) };
        const bad = useRichTextEditor({ document: { schema_version: 1, nodes: [{ id: 'x y', runs: [] }] } });
        expect(bad.status.value).toBe('failed');
        expect(bad.readOnly.value).toBe(true);
        make().editor.editRun('n1', 'a', 'Q');
        expect(writes).toEqual([]);
        delete globalThis.localStorage;
    });

    it('component exposes labels, toolbar and live region without hard colours', () => {
        expect(RichTextEditor.template).toContain('role="toolbar"');
        expect(RichTextEditor.template).toContain('aria-live="polite"');
        expect(RichTextEditor.template).toContain('aria-pressed');
        const source = readFileSync(new URL('../../../static/js/clinic/richtext-editor-ui.js', import.meta.url), 'utf8');
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    });
});
