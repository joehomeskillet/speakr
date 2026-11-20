/**
 * Playback gain for the correction view (PAR-COR-008).
 *
 * The gain is applied in the Web Audio playback graph only
 * (audio element -> GainNode -> destination). Source samples, the original
 * audio and every derived file are never read back, rewritten or uploaded.
 * An analyser taps the signal before the gain node, so automatic mode never
 * feeds its own output back into the level measurement. No clinical data is
 * stored: state lives in memory and no network call is made.
 *
 * The numbers mirror src/clinic/playback_gain.py (AgcConfig): target -20 dBFS,
 * gain bounds -12 dB .. +18 dB, peak ceiling -1 dBFS, silence -70 dBFS,
 * attack 0.5 (gain falls fast), release 0.1 (gain rises slowly), 400 ms window.
 * The server owns the authoritative curve; this module is the live-preview
 * counterpart for the browser.
 *
 * Exports:
 *   AGC                          frozen constants mirroring AgcConfig
 *   levelOf(samples)             { rmsDbfs, peakDbfs } of a Float32Array / array in [-1, 1]
 *   nextGainDb(prev, level, cfg) one smoothed automatic gain step in dB
 *   clampGainDb(db, peakDbfs)    manual bound plus clipping protection
 *   usePlaybackGain(options, vue)
 *       options.audio             media element or adapter (required)
 *       options.createAudioContext optional factory returning an AudioContext
 *       options.setInterval / clearInterval  optional timer injection
 *       returns { state, gain, setMode('auto'|'manual'), setGain(db), step(), start(), stop(), dispose() }
 *       `state` and `gain` are Vue refs; `gain.value` is the applied gain in dB.
 *   PlaybackGain                 Vue options component (props: state, controller)
 *   registerPlaybackGain(app)    registers it as `clinic-playback-gain`
 */

import { surfaceFor } from './tokens.js';

export const AGC = Object.freeze({
    targetDbfs: -20,
    maxGainDb: 18,
    minGainDb: -12,
    windowMs: 400,
    attack: 0.5,
    release: 0.1,
    silenceDbfs: -70,
    peakCeilingDbfs: -1,
});

const MIN_DBFS = -96;
export const MODES = Object.freeze(['auto', 'manual']);

const MESSAGES = Object.freeze({
    ready: 'Wiedergabeverstärkung bereit.',
    unavailable: 'Wiedergabeverstärkung ist in diesem Browser nicht verfügbar.',
    invalid: 'Ungültiger Verstärkungswert.',
    invalidMode: 'Ungültiger Modus.',
    limited: 'Verstärkung begrenzt: Übersteuerungsschutz aktiv.',
    bounded: 'Verstärkung auf den zulässigen Bereich begrenzt.',
    failed: 'Wiedergabeverstärkung konnte nicht angewendet werden.',
});

function dbfs(linear) {
    return linear <= 0 ? MIN_DBFS : Math.max(MIN_DBFS, 20 * Math.log10(linear));
}

/** RMS and peak of one analysis window, in dBFS (full scale = 1.0). */
export function levelOf(samples) {
    let sumSq = 0;
    let peak = 0;
    for (let i = 0; i < samples.length; i += 1) {
        const s = samples[i];
        sumSq += s * s;
        if (Math.abs(s) > peak) peak = Math.abs(s);
    }
    const rms = samples.length ? Math.sqrt(sumSq / samples.length) : 0;
    return { rmsDbfs: dbfs(rms), peakDbfs: dbfs(peak) };
}

/** One automatic step; same rules as gain_curve() in playback_gain.py. */
export function nextGainDb(prevDb, level, cfg = AGC) {
    if (level.rmsDbfs <= cfg.silenceDbfs) return prevDb;
    let target = cfg.targetDbfs - level.rmsDbfs;
    target = Math.max(cfg.minGainDb, Math.min(cfg.maxGainDb, target));
    target = Math.min(target, cfg.peakCeilingDbfs - level.peakDbfs);
    const gap = target - prevDb;
    return prevDb + gap * (gap < 0 ? cfg.attack : cfg.release);
}

/** Manual value within the bounds; lowered further when the last peak would clip. */
export function clampGainDb(db, peakDbfs = null, cfg = AGC) {
    let out = Math.max(cfg.minGainDb, Math.min(cfg.maxGainDb, db));
    if (typeof peakDbfs === 'number' && peakDbfs > MIN_DBFS) {
        out = Math.min(out, cfg.peakCeilingDbfs - peakDbfs);
    }
    return out;
}

function snapshot(s) {
    return {
        mode: s.mode,
        gainDb: s.gainDb,
        minDb: AGC.minGainDb,
        maxDb: AGC.maxGainDb,
        peakDbfs: s.peakDbfs,
        available: s.available,
        message: s.note || (s.available ? MESSAGES.ready : MESSAGES.unavailable),
        surface: surfaceFor(s.available ? (s.note ? 'warning' : 'normal') : 'error'),
    };
}

/**
 * @param {object} options see the header comment
 * @param {object} [vue] Vue namespace (window.Vue in the app)
 */
export function usePlaybackGain(options, vue) {
    const V = vue || globalThis.Vue;
    const opts = options || {};
    const s = { mode: 'auto', gainDb: 0, peakDbfs: null, note: '', available: false };
    let ctx = null;
    let gainNode = null;
    let analyser = null;
    let buffer = null;
    let timer = null;

    try {
        const make = opts.createAudioContext;
        if (opts.audio && typeof make === 'function') {
            ctx = make();
            const source = ctx.createMediaElementSource(opts.audio);
            gainNode = ctx.createGain();
            analyser = ctx.createAnalyser();
            source.connect(analyser);
            source.connect(gainNode);
            gainNode.connect(ctx.destination);
            gainNode.gain.value = 1;
            buffer = new Float32Array(analyser.fftSize || 2048);
            s.available = true;
        }
    } catch (err) {
        s.available = false;
        s.note = MESSAGES.failed;
    }

    const state = V.shallowRef(snapshot(s));
    const gain = V.shallowRef(s.gainDb);

    function publish() {
        state.value = snapshot(s);
        gain.value = s.gainDb;
        return state.value;
    }

    function apply(db) {
        s.gainDb = db;
        if (gainNode) gainNode.gain.value = 10 ** (db / 20);
    }

    function setMode(mode) {
        if (!MODES.includes(mode)) { s.note = MESSAGES.invalidMode; return publish(); }
        s.mode = mode;
        s.note = '';
        return publish();
    }

    function setGain(db) {
        if (typeof db !== 'number' || !Number.isFinite(db)) { s.note = MESSAGES.invalid; return publish(); }
        if (!s.available) { s.note = MESSAGES.unavailable; return publish(); }
        const bounded = clampGainDb(db, s.peakDbfs);
        const inRange = Math.max(AGC.minGainDb, Math.min(AGC.maxGainDb, db));
        s.note = bounded < inRange ? MESSAGES.limited : (inRange !== db ? MESSAGES.bounded : '');
        s.mode = 'manual';
        apply(bounded);
        return publish();
    }

    /** Measure the current window and, in auto mode, move the gain one step. */
    function step() {
        if (!s.available || !analyser) return publish();
        try {
            analyser.getFloatTimeDomainData(buffer);
        } catch (err) {
            s.note = MESSAGES.failed;
            return publish();
        }
        const level = levelOf(buffer);
        s.peakDbfs = level.peakDbfs;
        if (s.mode === 'auto') {
            s.note = '';
            apply(nextGainDb(s.gainDb, level));
        } else if (clampGainDb(s.gainDb, level.peakDbfs) < s.gainDb) {
            s.note = MESSAGES.limited;
            apply(clampGainDb(s.gainDb, level.peakDbfs));
        }
        return publish();
    }

    function stop() {
        if (timer !== null) (opts.clearInterval || globalThis.clearInterval)(timer);
        timer = null;
    }

    function start() {
        stop();
        if (s.available) timer = (opts.setInterval || globalThis.setInterval)(step, AGC.windowMs);
    }

    function dispose() {
        stop();
        try { if (gainNode) gainNode.disconnect(); if (analyser) analyser.disconnect(); } catch (err) { /* already detached */ }
        gainNode = null;
        analyser = null;
        buffer = null;
        s.available = false;
        publish();
    }

    if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(dispose);
    return { state, gain, setMode, setGain, step, start, stop, dispose };
}

/** Labelled controls: native radio-like buttons and a native range input (arrow keys, Home, End). */
export const PlaybackGain = {
    name: 'ClinicPlaybackGain',
    props: {
        state: { type: Object, required: true },
        controller: { type: Object, required: true },
    },
    methods: {
        onInput(event) { this.controller.setGain(Number(event.target.value)); },
    },
    template: `
        <section class="clinic-playback-gain" role="group" aria-label="Wiedergabeverstärkung"
            :data-mode="state.mode" style="display: grid; gap: 8px">
            <p role="status" aria-live="polite" class="hint" style="margin: 0">{{ state.message }}</p>
            <div class="actions" style="margin: 0">
                <button type="button" class="small" :aria-pressed="state.mode === 'auto'" :disabled="!state.available"
                    @click="controller.setMode('auto')">Automatisch</button>
                <button type="button" class="small" :aria-pressed="state.mode === 'manual'" :disabled="!state.available"
                    @click="controller.setMode('manual')">Manuell</button>
            </div>
            <label class="inline-label" style="display: flex; align-items: center; gap: 10px">Verstärkung
                <input type="range" style="flex: 1; min-width: 80px" :min="state.minDb" :max="state.maxDb" step="1" :value="Math.round(state.gainDb)"
                    :disabled="!state.available" :aria-valuetext="Math.round(state.gainDb) + ' Dezibel'"
                    @input="onInput">
                <output>{{ Math.round(state.gainDb) }} dB</output>
            </label>
        </section>`,
};

export function registerPlaybackGain(app) {
    app.component('clinic-playback-gain', PlaybackGain);
    return app;
}