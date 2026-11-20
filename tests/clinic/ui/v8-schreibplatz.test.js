import { describe, expect, it } from 'vitest';

import { CorrectionView, TEXT } from '../../../static/js/clinic/compose-correction.js';
import { MARKER_TEXT, createCorrectionToolbar } from '../../../static/js/clinic/correction-toolbar-ui.js';
import { createCorrectionToolbar as reExported } from '../../../static/js/clinic/correction-editor-ui.js';
import { PlaybackTempo } from '../../../static/js/clinic/playback-tempo-ui.js';

const body = () => CorrectionView.components.CorrectionBody.template;

function fakeEditor(text) {
    const st = { text, start: 0, end: 0 };
    return {
        st,
        read: () => ({ ...st }),
        write: (v) => { st.text = v.text; st.start = v.start; st.end = v.end; },
        type(t) { st.text = t; st.start = st.end = t.length; },
    };
}

describe('V8 Schreibplatz markup (prototype structure, test ids kept)', () => {
    it('follows the prototype: page head, editor card, meta strip, player bar, toolbar row, text area', () => {
        const t = body();
        for (const marker of [
            'class="pagehead"', 'class="head-actions"', 'class="editor-workspace"', 'class="editor-heading"', 'class="document-title"',
            'class="document-id"', 'class="document-context"', 'class="player compact-player"', 'editorbar symbol-toolbar',
            'toolbar-group', 'class="writing-surface"', 'class="editor"', 'icon-button save-button', 'class="editor-status"',
        ]) expect(t).toContain(marker);
        expect(TEXT.toWorklist).toBe('Zur Arbeitsliste');
        expect(t).toContain('TEXT.dictatedBy');
        expect(t).toContain('TEXT.pool');
        expect(t).toContain('TEXT.editing');
    });

    it('keeps every test id the mounted view and its browser tests rely on', () => {
        const t = body();
        for (const id of [
            'clinic-correction-view', 'close', 'lease-bar', 'toolbar', 'text', 'save', 'save-state', 'resume', 'reload',
            'seek-selection', 'player-bar', 'alignment', 'conflict-choice', 'keep-mine', 'take-server', 'compare', 'diff',
            'load-error', 'loading-state', 'pedal-status', 'alignment-notice',
        ]) expect(t).toContain('data-testid="' + id + '"');
        expect(t.match(/data-testid="save"/g)).toHaveLength(1);
        expect(t.match(/data-testid="text"/g)).toHaveLength(1);
        expect(t).toContain('clinic-correction-modes');
        expect(t).toContain('role="radiogroup"');
    });

    it('is labelled in German, has no hard colours and no patient data', () => {
        const t = body();
        expect(t).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(t).not.toMatch(/patient|Patient|Fall-ID|Titel|Empf/);
        expect(t).toContain('aria-label');
    });

    it('offers the player controls inside the tempo component and keeps its accessible group', () => {
        const t = PlaybackTempo.template;
        expect(t).toContain('data-testid="play-toggle"');
        expect(t).toContain('type="range"');
        expect(t).toContain('data-player-time');
        expect(t).toContain('player-controls');
        expect(t).toContain('aria-label="Wiedergabe"');
    });
});

describe('toolbar additions for the V8 toolbar row', () => {
    it('inserts the review marker at the caret as one undo step', () => {
        const editor = fakeEditor('Text');
        editor.st.start = editor.st.end = 4;
        const toolbar = createCorrectionToolbar({ editor });
        expect(toolbar.insertMarker()).toBe(true);
        expect(editor.st.text).toBe('Text' + MARKER_TEXT);
        toolbar.undo();
        expect(editor.st.text).toBe('Text');
    });

    it('blocks the marker without a lease', () => {
        const editor = fakeEditor('Text');
        const toolbar = createCorrectionToolbar({ editor, canEdit: () => false });
        expect(toolbar.insertMarker()).toBe(false);
        expect(editor.st.text).toBe('Text');
    });

    it('reset drops undo and redo after the text was replaced from outside', () => {
        const editor = fakeEditor('alt');
        const toolbar = createCorrectionToolbar({ editor });
        editor.type('alt neu');
        toolbar.notifyChange();
        expect(toolbar.getState().canUndo).toBe(true);
        editor.type('Serverstand');
        toolbar.reset();
        expect(toolbar.getState().canUndo).toBe(false);
        expect(toolbar.undo()).toBe(false);
        expect(editor.st.text).toBe('Serverstand');
    });

    it('is reachable through the editor module the composed view already imports', () => {
        expect(reExported).toBe(createCorrectionToolbar);
    });
});
