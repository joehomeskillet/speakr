import { describe, expect, it } from 'vitest';
import { ArchiveView, archiveSummary } from '../../../static/js/clinic/compose-archive.js';
import { ICON_PATHS, PHASE_CAPTIONS, RECORDING_TEMPLATE } from '../../../static/js/clinic/compose-recording.js';
import { LiveView } from '../../../static/js/clinic/compose-live.js';
import { ReviewView } from '../../../static/js/clinic/compose-review.js';
import { PHASES, createRecorderPhasesComponent } from '../../../static/js/clinic/recorder-phases-ui.js';
import { createLiveSessionComponent } from '../../../static/js/clinic/live-session-ui.js';

const templates = {
    archive: ArchiveView.template,
    recording: RECORDING_TEMPLATE,
    live: LiveView.components.LiveBody.template,
    review: ReviewView.components.ReviewBody.template,
};

describe('V8 work pages: prototype structure', () => {
    for (const [name, template] of Object.entries(templates)) {
        it(name + ' follows the prototype page head, breadcrumb and panel structure', () => {
            expect(template).toContain('v8-page');
            expect(template).toContain('class="breadcrumb"');
            expect(template).toContain('class="pagehead"');
            expect(template).toContain('class="eyebrow"');
            expect(template).toContain('class="subline"');
            expect(template).toContain('class="head-actions"');
            expect(template).toMatch(/class="(panel|editor-workspace)"/);
        });

        it(name + ' styles every button with the shared button class', () => {
            const buttons = template.match(/<button[^>]*>/g) || [];
            expect(buttons.length).toBeGreaterThan(0);
            for (const tag of buttons) expect(tag).toMatch(/\sclass="[^"]*\bbutton\b/);
        });

        it(name + ' does not use the app field class on wrappers and restates the heading size', () => {
            expect(template).not.toMatch(/class="field[ "]/);
            expect(template).toMatch(/<h1 id="[a-z-]+" style="font-size: clamp\(26px, 4vw, 30px\)/);
            expect(template).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        });
    }

    it('the review page keeps the acceptance state and the confirmation dialog', () => {
        const t = templates.review;
        for (const part of ['data-testid="acceptance-state"', 'role="alertdialog"', 'data-testid="wiedervorlage"', 'data-testid="actions"', 'class="metadata"', 'class="keyvalues"']) {
            expect(t).toContain(part);
        }
    });

    it('keeps the archive open button in its own trailing column', () => {
        expect(templates.archive).toMatch(/<th scope="col"><span class="sr-only">Aktion<\/span><\/th>\s*<\/tr>/);
        expect(templates.archive).toMatch(/<td><button[^>]*data-testid="archive-open"[^>]*>[^<]*<\/button><\/td>\s*<\/tr>/);
    });

    it('distinguishes playback from resuming a recording', () => {
        expect(ICON_PATHS.play).not.toBe(ICON_PATHS.resume);
    });

    it('the live page keeps one polite live region and the saved-state label per phrase', () => {
        const t = templates.live;
        expect(t.match(/aria-live="polite"/g)).toHaveLength(1);
        expect(t).toContain('p.view.label');
        expect(t).toContain('compact-recorder');
    });

    it('the recording page keeps recorder test ids and shows the author read-only', () => {
        const t = templates.recording;
        for (const part of ['data-testid="author"', "'rec-' + b.id", 'data-testid="file-upload"', 'data-testid="created"', 'data-testid="status"']) {
            expect(t).toContain(part);
        }
        expect(t).not.toMatch(/<select|<input[^>]*readonly/);
    });
});

describe('archive summary', () => {
    const row = (author, pool, archived) => ({ dictated_by: { display_name: author }, staff_pools: [{ name: pool }], archived_at: archived });

    it('counts rows, authors and pools and finds the oldest archive date in Zurich time', () => {
        const s = archiveSummary([
            row('A', 'P1', '2026-11-17T14:08:00Z'),
            row('B', 'P2', '2026-11-12T10:41:00Z'),
            row('A', 'P1', '2026-11-16T08:20:00Z'),
        ]);
        expect(s).toEqual({ count: 3, authors: 2, pools: 2, oldest: '12.11.2026' });
    });

    it('shows dashes for an empty list or rows without data', () => {
        expect(archiveSummary([])).toEqual({ count: 0, authors: 0, pools: 0, oldest: '–' });
        expect(archiveSummary([{}, null])).toEqual({ count: 2, authors: 0, pools: 0, oldest: '–' });
        expect(archiveSummary(undefined).count).toBe(0);
    });
});

describe('recorder captions and panels', () => {
    it('names every recorder phase and every file phase', () => {
        for (const phase of [...PHASES, 'idle', 'selected']) expect(typeof PHASE_CAPTIONS[phase]).toBe('string');
    });

    it('the phases panel keeps its children and carries panel and button classes', () => {
        const vue = {
            h: (tag, props, children) => ({ tag, props, children }),
            defineComponent: (def) => def,
            shallowRef: (value) => ({ value }),
            computed: (fn) => ({ get value() { return fn(); } }),
        };
        const component = createRecorderPhasesComponent(vue);
        const tree = component.setup({ options: { recorder: { startRecording() {}, pauseRecording() {}, resumeRecording() {}, stopRecording() {}, discardRecording() {}, uploadRecordedAudio() {} } } })();
        expect(tree.props.class).toContain('panel');
        expect(tree.children).toHaveLength(4);
        expect(tree.children[2].children.every((b) => b.props.class === 'button')).toBe(true);
    });

    it('the live session panel marks unsaved phrases with a tag class and uses button classes', () => {
        const template = createLiveSessionComponent({ computed: (fn) => ({ get value() { return fn(); } }) }).template;
        expect(template).toContain("p.state === 'final_unsaved' ? 'wait'");
        expect(template).toMatch(/<button[^>]*class="button small"/);
    });
});
