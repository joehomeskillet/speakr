import { describe, expect, it, vi } from 'vitest';
import { LABELS, createRecorderPhases, createRecorderPhasesComponent } from '../../../static/js/clinic/recorder-phases-ui.js';

function recorder(upload) {
    return {
        startRecording: vi.fn(async () => {}),
        pauseRecording: vi.fn(),
        resumeRecording: vi.fn(),
        stopRecording: vi.fn(async () => {}),
        discardRecording: vi.fn(async () => {}),
        uploadRecordedAudio: vi.fn(upload || (async () => ({ id: 'rec-1' }))),
    };
}

async function toReview(machine) {
    machine.setTitle('Bericht');
    await machine.start();
    machine.pause();
    machine.resume();
    await machine.stop();
}

describe('recorder phases', () => {
    it('requires a title before recording', async () => {
        const rec = recorder();
        const m = createRecorderPhases({ recorder: rec });
        expect((await m.start()).code).toBe('invalid_phase');
        m.setTitle('   ');
        expect(m.getState().phase).toBe('title');
        expect(m.announcement()).toBe(LABELS.titleHint);
        expect(rec.startRecording).not.toHaveBeenCalled();
    });

    it('runs title, record, pause, resume, stop, rewind, play, send', async () => {
        const rec = recorder();
        const player = { currentTime: 9, play: vi.fn() };
        const m = createRecorderPhases({ recorder: rec, player });
        await toReview(m);
        expect(m.getState().phase).toBe('review');
        m.rewind();
        m.play();
        expect(player.currentTime).toBe(0);
        expect(player.play).toHaveBeenCalledOnce();
        const out = await m.send();
        expect(out.ok).toBe(true);
        expect(rec.uploadRecordedAudio).toHaveBeenCalledWith({ title: 'Bericht' });
        expect(m.getState().phase).toBe('sent');
        expect(m.announcement()).toBe(LABELS.sent);
    });

    it('does not show sent when the upload rejects, then retries', async () => {
        let calls = 0;
        const rec = recorder(async () => {
            calls += 1;
            if (calls === 1) throw new Error('offline');
            return { id: 7 };
        });
        const m = createRecorderPhases({ recorder: rec });
        await toReview(m);
        expect((await m.send()).code).toBe('no_receipt');
        expect(m.getState().phase).toBe('failed');
        expect(m.announcement()).toBe(LABELS.failed);
        expect(m.controls().find((c) => c.id === 'send').label).toBe(LABELS.retry);
        expect((await m.send()).ok).toBe(true);
        expect(m.getState().phase).toBe('sent');
    });

    it('treats an upload without receipt as failed', async () => {
        const m = createRecorderPhases({ recorder: recorder(async () => undefined) });
        await toReview(m);
        await m.send();
        expect(m.getState().phase).toBe('failed');
    });

    it('refuses every action when unauthorized', async () => {
        const rec = recorder();
        const m = createRecorderPhases({ recorder: rec, authorized: () => false });
        m.setTitle('x');
        expect((await m.start()).code).toBe('unauthorized');
        expect((await m.cancel()).code).toBe('unauthorized');
        expect(m.controls().every((c) => c.disabled)).toBe(true);
        expect(m.announcement()).toBe(LABELS.unauthorized);
        expect(rec.startRecording).not.toHaveBeenCalled();
    });

    it('blocks cancel while sending and still records the late receipt', async () => {
        let release;
        const rec = recorder(() => new Promise((resolve) => { release = resolve; }));
        const m = createRecorderPhases({ recorder: rec });
        await toReview(m);
        const pending = m.send();
        expect((await m.cancel()).code).toBe('invalid_phase');
        release({ id: 'late' });
        expect((await pending).ok).toBe(true);
        expect(m.getState().phase).toBe('sent');
    });

    it('cancel discards the recording and clears the title', async () => {
        const rec = recorder();
        const switchContext = vi.fn();
        const m = createRecorderPhases({ recorder: rec, api: { switchContext } });
        await toReview(m);
        expect((await m.cancel()).ok).toBe(true);
        expect(rec.discardRecording).toHaveBeenCalledOnce();
        expect(switchContext).toHaveBeenCalledOnce();
        expect(m.getState()).toMatchObject({ phase: 'title', title: '' });
    });

    it('maps phases to token surfaces without hex colours', async () => {
        const m = createRecorderPhases({ recorder: recorder() });
        expect(m.surface().tokens.color).toMatch(/^--/);
        await toReview(m);
        expect(m.surface().status).toBe('pending');
    });

    it('exports a component with labelled, keyboard-native controls', () => {
        const vue = {
            h: (tag, props, children) => ({ tag, props, children }),
            defineComponent: (def) => def,
            shallowRef: (value) => ({ value }),
            computed: (fn) => ({ get value() { return fn(); } }),
        };
        const component = createRecorderPhasesComponent(vue);
        const tree = component.setup({ options: { recorder: recorder() } })();
        const [label, input, group, status] = tree.children;
        expect(label.props.for).toBe(input.props.id);
        expect(input.props['aria-required']).toBe('true');
        expect(group.children.every((b) => b.tag === 'button' && b.props.type === 'button')).toBe(true);
        expect(status.props['aria-live']).toBe('polite');
    });
});
