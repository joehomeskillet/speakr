/**
 * Playback tempo and seek controls for the correction view.
 *
 * Exports:
 *   - TEMPO_MIN / TEMPO_MAX / TEMPO_STEP / TEMPO_DEFAULT / SPEEDS: the values of
 *     src/clinic/playback_tempo.py (0.5 to 2.0 in steps of 0.1, default 1.0).
 *   - clampTempo(value), stepTempo(current, direction), seekMs(positionMs, deltaMs, durationMs):
 *     browser mirrors of the server arithmetic (tenths in integers, bounded positions).
 *   - usePlaybackTempo(options): controller with setRate(), faster(), slower(), skip(seconds),
 *     rewind(seconds), rate, state, dispose(). Options: audio (HTMLMediaElement-like adapter:
 *     currentTime, duration, playbackRate, preservesPitch, addEventListener/removeEventListener),
 *     onSync (called with { reason: 'seeked' | 'ratechange', positionMs, rate } so the composer can
 *     re-sync the current phrase), vue (object with ref; defaults to window.Vue).
 *   - PlaybackTempo: Vue options component (props: audio, stepSeconds, onSync) with labelled buttons,
 *     a polite status region and the shared focus ring.
 *   - registerPlaybackTempo(app): registers the component as `clinic-playback-tempo`.
 *
 * Skip and rewind step sizes are not defined by the server module, so the caller passes them
 * explicitly. Nothing is written to web storage and no clinical data is held here.
 */

import { surfaceFor } from './tokens.js';
import { vTip } from './correction-toolbar-ui.js';

export const TEMPO_MIN = 0.5;
export const TEMPO_MAX = 2.0;
export const TEMPO_STEP = 0.1;
export const TEMPO_DEFAULT = 1.0;

const MIN_TENTHS = Math.round(TEMPO_MIN * 10);
const MAX_TENTHS = Math.round(TEMPO_MAX * 10);

export const SPEEDS = Object.freeze(
    Array.from({ length: MAX_TENTHS - MIN_TENTHS + 1 }, (_, i) => (MIN_TENTHS + i) / 10),
);

export const LABELS = Object.freeze({
    group: 'Wiedergabe',
    slower: 'Langsamer',
    faster: 'Schneller',
    reset: 'Normale Geschwindigkeit',
    rewind: 'Zurückspulen',
    skip: 'Vorspulen',
    speed: 'Geschwindigkeit',
});

const MESSAGES = Object.freeze({
    idle: '',
    ok: '',
    invalid_type: 'Ungültiger Wert.',
    not_finite: 'Der Wert muss eine endliche Zahl sein.',
    negative_seconds: 'Die Sprungweite darf nicht negativ sein.',
    no_audio: 'Keine Audiodatei geladen.',
    no_duration: 'Die Dauer der Aufnahme ist unbekannt.',
    pitch_unsupported: 'Tonhöhenerhaltung wird von diesem Browser nicht unterstützt.',
});

function isNumber(value) {
    return typeof value === 'number' && !Number.isNaN(value);
}

function checkNumber(value) {
    if (typeof value === 'boolean' || typeof value !== 'number') return 'invalid_type';
    if (!Number.isFinite(value)) return 'not_finite';
    return null;
}

/** Clamp to [0.5, 2.0] and round to two decimals; throws a coded Error for invalid input. */
export function clampTempo(value) {
    const code = checkNumber(value);
    if (code) throw Object.assign(new Error(code), { code });
    const clamped = Math.max(TEMPO_MIN, Math.min(TEMPO_MAX, value));
    return Math.round(clamped * 100) / 100;
}

/** One step of 0.1 up (+1) or down (-1), in integer tenths so ten steps land exactly on the bound. */
export function stepTempo(current, direction) {
    const code = checkNumber(current);
    if (code) throw Object.assign(new Error(code), { code });
    if (direction !== 1 && direction !== -1) {
        throw Object.assign(new Error('invalid_direction'), { code: 'invalid_direction' });
    }
    const tenths = Math.max(MIN_TENTHS, Math.min(MAX_TENTHS, Math.round(current * 10) + direction));
    return tenths / 10;
}

/** Position after a relative move, clamped to [0, durationMs]. */
export function seekMs(positionMs, deltaMs, durationMs) {
    for (const v of [positionMs, deltaMs, durationMs]) {
        if (!Number.isInteger(v)) throw Object.assign(new Error('invalid_type'), { code: 'invalid_type' });
    }
    if (durationMs < 0) throw Object.assign(new Error('negative_duration'), { code: 'negative_duration' });
    return Math.max(0, Math.min(durationMs, positionMs + deltaMs));
}

function surfaceKey(code) {
    return code === 'pitch_unsupported' ? 'warning' : code === 'ok' || code === 'idle' ? 'normal' : 'error';
}

/**
 * Controller around an injected audio element/adapter.
 * Every operation returns { ok, code, rate, positionMs }; failures change no playback state.
 */
export function usePlaybackTempo(options) {
    const opts = options || {};
    const audio = opts.audio || null;
    const vue = opts.vue || (typeof window !== 'undefined' ? window.Vue : undefined);
    const ref = (vue && vue.ref) || ((value) => ({ value }));
    const rate = ref(TEMPO_DEFAULT);
    const state = ref(view('idle', true));
    const events = ['seeked', 'ratechange'];
    let disposed = false;

    function pitchSupported() {
        return !!audio && 'preservesPitch' in audio;
    }
    function view(code, pitch) {
        return {
            code,
            message: MESSAGES[code] || '',
            surface: surfaceFor(surfaceKey(code)),
            pitchPreserved: pitch,
        };
    }
    function positionMs() {
        return audio && isNumber(audio.currentTime) && Number.isFinite(audio.currentTime)
            ? Math.round(audio.currentTime * 1000) : 0;
    }
    function result(ok, code) {
        state.value = view(code, pitchSupported() ? audio.preservesPitch !== false : false);
        return { ok, code, rate: rate.value, positionMs: positionMs() };
    }
    function sync(reason) {
        if (disposed || typeof opts.onSync !== 'function') return;
        opts.onSync({ reason, positionMs: positionMs(), rate: audio ? audio.playbackRate : rate.value });
    }
    const listeners = {
        seeked: () => sync('seeked'),
        ratechange: () => sync('ratechange'),
    };
    if (audio && typeof audio.addEventListener === 'function') {
        for (const name of events) audio.addEventListener(name, listeners[name]);
    }

    function setRate(value) {
        if (disposed || !audio) return result(false, 'no_audio');
        const code = checkNumber(value);
        if (code) return result(false, code);
        const next = clampTempo(value);
        if (pitchSupported()) audio.preservesPitch = true;
        if ('webkitPreservesPitch' in audio) audio.webkitPreservesPitch = true;
        if ('mozPreservesPitch' in audio) audio.mozPreservesPitch = true;
        audio.playbackRate = next;
        rate.value = next;
        return result(true, pitchSupported() ? 'ok' : 'pitch_unsupported');
    }

    function move(seconds, sign) {
        if (disposed || !audio) return result(false, 'no_audio');
        const code = checkNumber(seconds);
        if (code) return result(false, code);
        if (seconds < 0) return result(false, 'negative_seconds');
        const duration = audio.duration;
        if (!isNumber(duration) || !Number.isFinite(duration) || duration < 0) {
            return result(false, 'no_duration');
        }
        const target = seekMs(positionMs(), sign * Math.round(seconds * 1000), Math.round(duration * 1000));
        audio.currentTime = target / 1000;
        return result(true, 'ok');
    }

    return {
        rate,
        state,
        setRate,
        faster: () => (audio ? setRate(stepTempo(rate.value, 1)) : result(false, 'no_audio')),
        slower: () => (audio ? setRate(stepTempo(rate.value, -1)) : result(false, 'no_audio')),
        skip: (seconds) => move(seconds, 1),
        rewind: (seconds) => move(seconds, -1),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (audio && typeof audio.removeEventListener === 'function') {
                for (const name of events) audio.removeEventListener(name, listeners[name]);
            }
        },
    };
}

const CLOCK_LABELS = Object.freeze({ play: 'Wiedergabe starten', pause: 'Wiedergabe anhalten', position: 'Abspielposition' });

const clock = (seconds) => {
    const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
    const pad = (n) => String(n).padStart(2, '0');
    return pad(Math.floor(total / 60)) + ':' + pad(total % 60);
};

const MEDIA_EVENTS = Object.freeze(['timeupdate', 'durationchange', 'loadedmetadata', 'play', 'pause', 'ended', 'seeked']);

/**
 * Vue component. `stepSeconds` is required because the server defines no step size.
 * Renders the V8 player controls (play, rewind, skip, scrubber, time, speed) as the children of the
 * `compact-player` row of the composed view; the root is transparent to layout (display: contents).
 */
export const PlaybackTempo = {
    name: 'PlaybackTempo',
    directives: { tip: vTip },
    props: {
        audio: { type: Object, default: null },
        stepSeconds: { type: Number, required: true },
        onSync: { type: Function, default: null },
    },
    setup(props) {
        const tempo = usePlaybackTempo({ audio: props.audio, onSync: props.onSync });
        const styleOf = (s) => ({ background: s.background, color: s.color, borderColor: s.border });
        const V = globalThis.Vue;
        const position = V.ref(0);
        const duration = V.ref(0);
        const playing = V.ref(false);
        const media = props.audio;
        const sync = () => {
            if (!media) return;
            position.value = Number.isFinite(media.currentTime) ? media.currentTime : 0;
            duration.value = Number.isFinite(media.duration) ? media.duration : 0;
            playing.value = media.paused === false;
        };
        if (media && typeof media.addEventListener === 'function') {
            for (const name of MEDIA_EVENTS) media.addEventListener(name, sync);
        }
        sync();
        const detach = () => {
            if (media && typeof media.removeEventListener === 'function') {
                for (const name of MEDIA_EVENTS) media.removeEventListener(name, sync);
            }
        };
        const toggle = () => {
            if (!media) return;
            if (media.paused === false) media.pause();
            else if (typeof media.play === 'function') Promise.resolve(media.play()).catch(() => {});
        };
        const scrub = (event) => {
            if (!media) return;
            media.currentTime = Number(event.target.value);
            sync();
            if (props.onSync) props.onSync({ reason: 'seeked', positionMs: Math.round(media.currentTime * 1000), rate: tempo.rate.value });
        };
        // Right click and Delete/Backspace on the speed select return to normal speed, in the audio and the visible selection.
        const resetRate = (event) => {
            tempo.setRate(TEMPO_DEFAULT);
            if (event && event.target) event.target.value = String(TEMPO_DEFAULT);
        };
        const resetKey = (event) => {
            if (event.key !== 'Delete' && event.key !== 'Backspace') return;
            event.preventDefault();
            resetRate(event);
        };
        return {
            resetRate, resetKey,
            tempo, state: tempo.state, rate: tempo.rate, labels: LABELS, clockLabels: CLOCK_LABELS, speeds: SPEEDS, styleOf,
            position, duration, playing, toggle, scrub, detach, clock,
        };
    },
    beforeUnmount() {
        this.detach();
        this.tempo.dispose();
    },
    template: `
        <div role="group" aria-label="Wiedergabe" class="tempo-controls" style="display: contents">
            <div class="player-controls">
                <span class="tool-wrap" v-tip="{ label: playing ? clockLabels.pause : clockLabels.play }">
                    <button type="button" class="icon-button" :disabled="!audio" :aria-label="playing ? clockLabels.pause : clockLabels.play"
                        data-testid="play-toggle" @click="toggle()">
                        <svg v-if="!playing" class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m8 4 12 8-12 8z" fill="currentColor" stroke="none"/></svg>
                        <svg v-else class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7 4v16M17 4v16" stroke-width="5"/></svg>
                    </button>
                </span>
                <span class="tool-wrap" v-tip="{ label: labels.rewind }">
                    <button type="button" class="icon-button" :disabled="!audio" :aria-label="labels.rewind" @click="tempo.rewind(stepSeconds)">
                        <svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 8a9 9 0 1 1 1 11M4 3v5h5"/><text x="9" y="17" font-size="10" fill="currentColor" stroke="none" font-family="Arial,sans-serif">{{ stepSeconds }}</text></svg>
                    </button>
                </span>
                <span class="tool-wrap" v-tip="{ label: labels.skip }">
                    <button type="button" class="icon-button" :disabled="!audio" :aria-label="labels.skip" @click="tempo.skip(stepSeconds)">
                        <svg class="ui-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 8a9 9 0 1 0-1 11m1-16v5h-5"/><text x="9" y="17" font-size="10" fill="currentColor" stroke="none" font-family="Arial,sans-serif">{{ stepSeconds }}</text></svg>
                    </button>
                </span>
            </div>
            <span class="player-divider" aria-hidden="true"></span>
            <input type="range" min="0" :max="duration || 0" step="0.01" :value="position" :disabled="!audio || !duration"
                :aria-label="clockLabels.position" :aria-valuetext="clock(position) + ' von ' + clock(duration)" @input="scrub">
            <span class="time" data-player-time>{{ clock(position) }} / {{ clock(duration) }}</span>
            <select style="font-size: 11px" :disabled="!audio" :value="rate" :aria-label="labels.speed" :title="labels.speed + ' (Rechtsklick oder Entf: ' + labels.reset + ')'"
                aria-keyshortcuts="Delete Backspace" data-testid="tempo-select"
                @change="tempo.setRate(Number($event.target.value))" @contextmenu.prevent="resetRate($event)" @keydown="resetKey($event)">
                <option v-for="s in speeds" :key="s" :value="s">{{ s.toFixed(1) }}×</option>
            </select>
            <p role="status" aria-live="polite" class="sr-only" :style="styleOf(state.surface)">{{ state.message }}</p>
        </div>`,
};

export function registerPlaybackTempo(app) {
    app.component('clinic-playback-tempo', PlaybackTempo);
    return app;
}