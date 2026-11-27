export const RECORD_LEVEL_TEMPLATE = `
  <div style="display: grid; gap: var(--ui-space-2); width: min(100%, 18rem)">
    <div data-testid="record-level" role="meter" aria-label="Browser-Aufnahmepegel"
         aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.round(level * 100)"
         aria-describedby="record-level-caption"
         style="height: .5rem; border-radius: var(--ui-radius-sm); background: var(--ui-border); outline: 1px solid var(--ui-text-muted); overflow: hidden">
      <span aria-hidden="true"
            style="display: block; width: 100%; height: 100%; background: var(--brand-accent); transform-origin: left center"
            :style="{ transform: 'scaleX(' + level + ')' }"></span>
    </div>
    <small id="record-level-caption" data-testid="record-level-caption" style="color: var(--ui-text-muted); font-size: .875rem">Browser-Aufnahmepegel · nicht der Windows-Mixer</small>
  </div>`;

export function createRecordLevelController({
    level,
    readLevel,
    requestAnimationFrame: requestFrame = globalThis.requestAnimationFrame?.bind(globalThis),
    cancelAnimationFrame: cancelFrame = globalThis.cancelAnimationFrame?.bind(globalThis),
}) {
    let running = false;
    let frame = null;
    let generation = 0;

    function sample() {
        const value = Number(readLevel());
        level.value = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
    }

    function schedule() {
        if (!running || frame !== null || !requestFrame) return;
        const scheduledGeneration = generation;
        frame = requestFrame(() => {
            if (!running || scheduledGeneration !== generation) return;
            frame = null;
            sample();
            schedule();
        });
    }

    function start() {
        if (running) return;
        running = true;
        generation += 1;
        sample();
        schedule();
    }

    function stop() {
        running = false;
        generation += 1;
        if (frame !== null) {
            if (cancelFrame) cancelFrame(frame);
            frame = null;
        }
        level.value = 0;
    }

    function sync(phase) {
        if (phase === 'recording') start();
        else stop();
    }

    return { start, stop, sync };
}
