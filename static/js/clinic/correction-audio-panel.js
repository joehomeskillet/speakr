/** P06 controls for editing the current audio revision under the held writing lease. */
import { createAudioEditComponent, useAudioEdit } from './audio-edit-ui.js';

export const CorrectionAudioPanel = {
    name: 'ClinicCorrectionAudioPanel',
    components: { AudioEditActions: createAudioEditComponent() },
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        lease: { type: Object, required: true },
        position: { type: Function, required: true },
        visible: { type: Boolean, required: true },
    },
    emits: ['revision'],
    setup(props, { emit }) {
        const V = globalThis.Vue;
        const expanded = V.ref(false);
        const start = V.ref('');
        const end = V.ref('');
        const file = V.shallowRef(null);
        const captureState = V.ref('idle');
        const notice = V.ref('');
        const { state, controller } = useAudioEdit({
            client: props.client, dictationId: props.dictationId, lease: props.lease,
            sampleRate: 1000, // the selection fields supply milliseconds
            onRevision: (id) => emit('revision', id),
        }, V);
        let capture = null;
        let attempt = 0;
        const mime = 'audio/webm;codecs=opus';
        const canRecord = Boolean(globalThis.navigator?.mediaDevices?.getUserMedia
            && globalThis.MediaRecorder?.isTypeSupported(mime));

        function choose() {
            const toMs = (value) => value === '' ? NaN : Math.round(Number(value) * 1000);
            const first = toMs(start.value);
            const last = end.value === '' ? first : toMs(end.value);
            const valid = Number.isSafeInteger(first) && Number.isSafeInteger(last)
                && first >= 0 && last >= first && last <= state.value.durationMs;
            controller.select(valid ? first : NaN, valid ? last : NaN);
        }
        function mark(key) {
            const ms = props.position();
            if (!Number.isFinite(ms)) return;
            (key === 'start' ? start : end).value = ms / 1000;
            choose();
        }
        function resetCapture() {
            ++attempt;
            const previous = capture;
            capture = null;
            if (previous) {
                if (previous.state !== 'inactive') previous.stop();
                previous.stream.getTracks().forEach(track => track.stop());
            }
            captureState.value = 'idle';
        }
        function close() {
            expanded.value = false;
            resetCapture();
            file.value = null;
            start.value = '';
            end.value = '';
            notice.value = '';
        }
        async function toggle() {
            if (expanded.value) { close(); return; }
            expanded.value = true;
            await controller.load();
        }
        async function record() {
            if (!props.lease.canWrite() || !canRecord || captureState.value !== 'idle') return;
            const run = ++attempt;
            captureState.value = 'requesting';
            notice.value = '';
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                if (run !== attempt || !props.lease.canWrite()) {
                    stream.getTracks().forEach(track => track.stop());
                    return;
                }
                const recorder = new MediaRecorder(stream, { mimeType: mime });
                const chunks = [];
                capture = recorder;
                recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
                recorder.onstop = () => {
                    stream.getTracks().forEach(track => track.stop());
                    if (run !== attempt) return;
                    capture = null;
                    captureState.value = 'idle';
                    if (chunks.length) file.value = new File(chunks, 'Nachtrag.webm', { type: mime });
                    else notice.value = 'Die Aufnahme enthält keinen Ton.';
                };
                recorder.onerror = () => {
                    if (run !== attempt) return;
                    resetCapture();
                    notice.value = 'Das Mikrofon konnte die Aufnahme nicht abschließen.';
                };
                recorder.start();
                file.value = null;
                captureState.value = 'recording';
            } catch (error) {
                stream?.getTracks().forEach(track => track.stop());
                if (run !== attempt) return;
                resetCapture();
                notice.value = 'Mikrofon nicht verfügbar. Eine Audiodatei kann ausgewählt werden.';
            }
        }
        function stop() {
            if (capture?.state === 'recording') {
                captureState.value = 'stopping';
                capture.stop();
            }
        }
        V.watch(() => props.visible, visible => { if (!visible) close(); });
        V.watch(() => state.value.revisionId, () => { start.value = ''; end.value = ''; });
        V.onBeforeUnmount(close);
        return { expanded, state, controller, start, end, file, notice, captureState, canRecord,
            choose, mark, toggle, record, stop };
    },
    template: `
    <section class="panel correction-audio-panel" aria-label="Audiobearbeitung">
      <button type="button" class="button small" :aria-expanded="String(expanded)"
              @click="toggle" data-testid="audio-edit-open">{{ expanded ? 'Audiobearbeitung schließen' : 'Audio bearbeiten' }}</button>
      <div v-if="expanded" class="audio-edit-fields" data-testid="audio-edit-panel">
        <p class="hint">Auswahl in Sekunden. Einfügen und Überschreiben beginnen am Startpunkt.</p>
        <fieldset :disabled="state.busy || !state.writable">
          <legend>Zeitauswahl</legend>
          <div class="audio-edit-range"><label>Start (s) <input type="number" min="0" step="0.001" :max="state.durationMs / 1000"
                 v-model="start" @input="$nextTick(choose)" /></label>
          <button type="button" class="button small" @click="mark('start')">Abspielposition als Start</button></div>
          <div class="audio-edit-range"><label>Ende (s) <input type="number" min="0" step="0.001" :max="state.durationMs / 1000"
                 v-model="end" @input="$nextTick(choose)" /></label>
          <button type="button" class="button small" @click="mark('end')">Abspielposition als Ende</button></div>
        </fieldset>
        <fieldset :disabled="state.busy || !state.writable">
          <legend>Audio für Einfügen oder Überschreiben</legend>
          <label>Audiodatei (WAV oder WebM/Opus)
            <input type="file" accept=".wav,.webm" :disabled="captureState !== 'idle'"
                   @change="file = $event.target.files[0] || null" />
          </label>
          <button v-if="canRecord && captureState === 'idle'" type="button" class="button small" @click="record">Nachtrag aufnehmen</button>
          <button v-if="captureState === 'recording'" type="button" class="button small" @click="stop">Aufnahme beenden</button>
          <span v-if="captureState === 'requesting'" role="status">Mikrofon wird geöffnet …</span>
          <p v-if="file" class="hint" role="status">Bereit: {{ file.name }}</p>
          <p v-if="notice" role="status">{{ notice }}</p>
        </fieldset>
        <AudioEditActions :state="state" :controller="controller" :file="captureState === 'idle' ? file : null" />
      </div>
    </section>`,
};
