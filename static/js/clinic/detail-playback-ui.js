/** Read-only transport for the current P03 audio revision. Optional peaks never gate playback. */
import { parsePeaks } from '../modules/asr-editor/timeline-peaks.js';
import { barsFor } from './correction-waveform-ui.js';
import { formatAudioDuration } from './worklist-ui.js';

export const DetailPlayback = {
    name: 'ClinicDetailPlayback',
    props: {
        audio: { type: Object, required: true },
        dictationId: { type: String, required: true },
        vue: { type: Object, required: true },
    },
    emits: ['error'],
    setup(props) {
        const V = props.vue;
        const media = V.ref(null);
        const playButton = V.ref(null);
        const sizes = V.shallowRef({ compact: [], wide: [] });
        const viewport = window.matchMedia('(max-width: 640px)');
        const compact = V.ref(viewport.matches);
        const resize = () => { compact.value = viewport.matches; };
        const bars = V.computed(() => compact.value ? sizes.value.compact : sizes.value.wide);
        const duration = V.ref(0);
        const time = V.ref(0);
        const playing = V.ref(false);
        const failedPlay = V.ref(false);
        const controller = new AbortController();
        const hasWave = V.computed(() => bars.value.length > 0 && duration.value > 0);
        const extent = V.computed(() => Math.max(.01, ...bars.value.map(b => Math.max(-b.y1, b.y2))));
        const progress = V.computed(() => duration.value ? 1000 * time.value / duration.value : 0);

        async function loadWave() {
            const rev = props.audio.revisionId;
            const url = '/api/clinic/v1/dictations/' + encodeURIComponent(props.dictationId)
                + '/audio-revisions/' + encodeURIComponent(rev) + '/peaks';
            try {
                const response = await fetch(url, { signal: controller.signal, credentials: 'same-origin' });
                if (!response.ok || response.headers.get('X-Audio-Revision') !== rev) return;
                const parsed = parsePeaks(await response.arrayBuffer());
                if (!controller.signal.aborted) {
                    sizes.value = { compact: barsFor(parsed, 50), wide: barsFor(parsed, 105) };
                }
            } catch {
                // Decode, network and cancellation failures leave the native audio transport in place.
            }
        }
        function sync() {
            const player = media.value;
            if (!player) return;
            duration.value = Number.isFinite(player.duration) ? player.duration : 0;
            time.value = player.currentTime;
            playing.value = !player.paused && !player.ended;
        }
        async function toggle() {
            const player = media.value;
            if (!player) return;
            failedPlay.value = false;
            if (player.paused) {
                try { await player.play(); }
                catch (error) { if (error.name !== 'AbortError') failedPlay.value = true; }
            } else player.pause();
        }
        function seek(seconds) {
            if (!media.value || !duration.value) return;
            media.value.currentTime = Math.max(0, Math.min(seconds, duration.value));
            sync();
        }
        V.watch(hasWave, available => {
            if (available && media.value === document.activeElement)
                V.nextTick(() => playButton.value?.focus());
        });
        V.onMounted(() => {
            viewport.addEventListener('change', resize);
            loadWave();
        });
        V.onBeforeUnmount(() => {
            controller.abort();
            viewport.removeEventListener('change', resize);
        });
        return { media, playButton, hasWave, bars, extent, progress, time, duration,
            playing, failedPlay, sync, toggle, seek, clock: formatAudioDuration };
    },
    template: `
      <div class="p03-playback">
        <audio ref="media" aria-label="Diktat anhören" :src="audio.src" :controls="!hasWave"
            v-show="!hasWave" controlslist="nodownload noremoteplayback" disableremoteplayback preload="metadata"
            @loadedmetadata="sync" @durationchange="sync" @timeupdate="sync" @seeked="sync"
            @play="sync" @pause="sync" @ended="sync" @error="$emit('error', $event)"></audio>
        <template v-if="hasWave">
          <button ref="playButton" type="button" class="large-play" @click="toggle"
                  :aria-label="playing ? 'Wiedergabe pausieren' : 'Wiedergabe starten'">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="currentColor">
              <path v-if="playing" d="M6 5h4v14H6zm8 0h4v14h-4z" />
              <path v-else d="M8 5v14l11-7z" />
            </svg>
          </button>
          <div class="player-body">
            <div class="p03-wave">
              <svg :viewBox="'0 ' + (-extent * 1.22) + ' 1000 ' + (extent * 2.44)"
                   preserveAspectRatio="none" aria-hidden="true">
                <line v-for="bar in bars" :key="bar.x" :x1="bar.x" :x2="bar.x"
                      :y1="bar.y1" :y2="bar.y2" :class="{ played: bar.x < progress }"
                      vector-effect="non-scaling-stroke" stroke-linecap="round" />
              </svg>
              <span class="p03-playhead" :style="{ left: progress / 10 + '%' }" aria-hidden="true"></span>
              <input type="range" aria-label="Abspielposition" :aria-valuetext="clock(time) + ' von ' + clock(duration)"
                     min="0" :max="duration" step="0.01" :value="time" @input="seek($event.currentTarget.valueAsNumber)"
                     @keydown.left.prevent="seek(time - 5)" @keydown.right.prevent="seek(time + 5)" />
            </div>
            <div class="player-time" aria-hidden="true"><span>{{ clock(time) }}</span><span>{{ clock(duration) }}</span></div>
            <p v-if="failedPlay" role="status" class="hint">Die Wiedergabe konnte nicht gestartet werden.</p>
          </div>
        </template>
      </div>`,
};
