import { SPEEDS, TEMPO_DEFAULT } from './playback-tempo-ui.js';

const forTextarea = (text) => text.replace(/\r\n?/g, '\n');
// Match clinic.revisions.normalise_text before using offsets against the stored text.
export const canonicalReadAloudText = (text) => forTextarea(text.normalize('NFC'));

function rawOffset(source, offset) {
    // A textarea counts CRLF as one character. Rich-text runs can store both characters.
    for (const newline of source.matchAll(/\r\n/g)) {
        if (newline.index >= offset) break;
        offset += 1;
    }
    return offset;
}

function rangeFor(source, text, start, end) {
    const codePointAt = (offset) => {
        const left = canonicalReadAloudText(source.slice(0, offset));
        const right = canonicalReadAloudText(source.slice(offset));
        // A split inside an emoji or NFC composition does not have a canonical boundary.
        if (/[\uD800-\uDBFF][\uDC00-\uDFFF]/.test(source.slice(Math.max(0, offset - 1), offset + 1))
            || left + right !== text) return null;
        return Array.from(left).length;
    };
    const first = codePointAt(start);
    if (first === null) return null;
    if (start === end) return { cursor: first };
    const last = codePointAt(end);
    return last === null ? null : { start: first, end: last };
}

function selection({ nodes, input, text }) {
    if (!input?.isConnected) return null;
    const { selectionStart, selectionEnd } = input;
    if (input.hasAttribute('data-read-aloud-text')) {
        return input.closest('details')?.open && input.value === text
            ? rangeFor(text, text, selectionStart, selectionEnd) : null;
    }
    if (!nodes) return null;
    const nodeId = input.closest('[data-node-id]')?.dataset.nodeId;
    let source = '';
    let prefix, activeRun;
    for (const [i, node] of nodes.entries()) {
        if (i) source += '\n';
        if (node.kind === 'graphic') source += '[graphic: ' + node.alt_text + ']';
        else for (const run of node.runs) {
            if (node.id === nodeId && run.id === input.dataset.runId && input.value === forTextarea(run.text)) {
                prefix = source.length;
                activeRun = run.text;
            }
            source += run.text;
        }
    }
    if (prefix === undefined) return null;
    return rangeFor(source, text, prefix + rawOffset(activeRun, selectionStart), prefix + rawOffset(activeRun, selectionEnd));
}

const NO_VOICE = 'Keine lokale Stimme verfügbar.';
const FAILED = 'Vorlesen nicht möglich. Bitte erneut versuchen.';
const SAVE_FIRST = 'Bitte Änderungen speichern oder den aktuellen Text neu laden.';
const TARGET = { minWidth: '48px', minHeight: '48px' };
const SELECT = { ...TARGET, border: '1px solid var(--v8-text-2)', borderRadius: 'var(--ui-radius-sm)',
    backgroundColor: 'var(--surface)', color: 'var(--v8-text)', padding: 'var(--ui-space-2) var(--ui-space-6) var(--ui-space-2) var(--ui-space-2)' };
const TEXT_AREA = { ...SELECT, display: 'block', boxSizing: 'border-box', width: '100%', resize: 'vertical',
    padding: '8px', marginBottom: '8px' };

export const ReadAloud = {
    name: 'ClinicReadAloud',
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        read: { type: Function, required: true },
        rememberSelection: { type: Function, required: true },
    },
    setup(props) {
        const { ref, computed, onMounted, onBeforeUnmount, watch } = globalThis.Vue;
        const audio = ref(null);
        const voice = ref('');
        const available = ref('');
        const speed = ref(TEMPO_DEFAULT);
        const active = ref(false);
        const message = ref('Lokale Stimme wird geprüft …');
        const text = computed(() => props.read().text ?? '');
        const hasText = computed(() => Boolean(text.value.trim()));
        let generation = 0;
        let url;
        let disposed = false;

        function stop(note) {
            generation += 1;
            active.value = false;
            if (url) {
                audio.value.pause();
                audio.value.removeAttribute('src');
                audio.value.load();
                URL.revokeObjectURL(url);
                url = null;
            }
            if (note) message.value = note;
        }
        onMounted(async () => {
            try {
                const res = await props.client.request('/tts/capability?details=1');
                if (disposed) return;
                available.value = res.outcome === 'ok' && res.data?.available ? res.data.voice || '' : '';
                voice.value = available.value;
                message.value = available.value ? '' : res.outcome === 'ok' ? NO_VOICE
                    : 'Die lokale Stimme konnte nicht geprüft werden.';
            } catch {
                if (!disposed) message.value = 'Die lokale Stimme konnte nicht geprüft werden.';
            }
        });
        onBeforeUnmount(() => { disposed = true; stop(); });
        watch([() => props.read().revision, () => props.read().unavailable], ([, unavailable]) => {
            if (active.value) stop(unavailable ? SAVE_FIRST : 'Vorlesen gestoppt. Der Text hat sich geändert.');
        });

        async function toggle() {
            if (active.value) { stop('Vorlesen gestoppt.'); return; }
            if (!voice.value || !hasText.value) return;
            const snapshot = props.read();
            if (snapshot.unavailable || !snapshot.revision) { message.value = SAVE_FIRST; return; }
            const range = selection(snapshot);
            if (!range) { message.value = 'Bitte Text markieren oder den Cursor in den Text setzen.'; return; }
            const spoken = Array.from(snapshot.text).slice(range.start ?? range.cursor, range.end);
            if (spoken.length > 2000) { message.value = 'Bitte einen kürzeren Text markieren.'; return; }
            if (!spoken.join('').trim()) { message.value = 'Ab hier gibt es keinen Text zum Vorlesen.'; return; }
            const request = ++generation;
            active.value = true;
            message.value = 'Vorlesen wird vorbereitet …';
            try {
                const res = await props.client.request('/tts/speak', { method: 'POST', wav: true, body: {
                    dictation_id: props.dictationId, text_revision_id: snapshot.revision, voice: voice.value, ...range,
                } });
                if (request !== generation) return;
                if (props.read().revision !== snapshot.revision || props.read().unavailable) { stop(SAVE_FIRST); return; }
                if (res.outcome !== 'ok' || !(res.data instanceof Blob)) {
                    stop(res.code === 'CAPABILITY_UNAVAILABLE' ? NO_VOICE : res.code === 'REVISION_CONFLICT' ? SAVE_FIRST : FAILED);
                    return;
                }
                url = URL.createObjectURL(res.data);
                audio.value.src = url;
                audio.value.playbackRate = speed.value;
                await audio.value.play();
                if (request === generation) message.value = 'Text wird vorgelesen.';
            } catch {
                if (request === generation) stop(FAILED);
            }
        }

        function changeVoice() {
            stop(voice.value ? 'Deutsche Stimme eingeschaltet.' : 'Stimme ausgeschaltet.');
        }
        function changeSpeed() {
            audio.value.playbackRate = speed.value;
            message.value = 'Geschwindigkeit: ' + speedLabel(speed.value) + '.';
        }
        const speedLabel = (speed) => speed.toLocaleString('de-DE') + '×';
        return { audio, available, voice, speed, active, message, text, hasText,
            toggle, stop, changeVoice, changeSpeed, speedLabel, SPEEDS, TARGET, SELECT, TEXT_AREA };
    },
    template: `
<div class="clinic-live-read-aloud" role="group" aria-label="Text vorlesen" data-testid="live-read-aloud"
     style="display:flex;flex-wrap:wrap;align-items:end;gap:8px 12px;padding:12px 16px;border-top:1px solid var(--v8-line-2)">
  <details v-if="voice && hasText" style="flex-basis:100%;min-width:0">
    <summary :style="TARGET" style="padding:12px 0;cursor:pointer">Text zum Vorlesen markieren</summary>
    <textarea data-read-aloud-text aria-readonly="true" rows="3" inputmode="none" spellcheck="false"
      aria-label="Gespeicherter Text" :value="text" :style="TEXT_AREA" @focus="rememberSelection"
      @beforeinput.prevent @input="$event.target.value = text"></textarea>
  </details>
  <button type="button" class="button" :style="TARGET" :disabled="!active && (!voice || !hasText)" @click="toggle">
    {{ active ? 'Vorlesen stoppen' : 'Vorlesen' }}
  </button>
  <label style="display:grid;gap:4px">Stimme
    <select v-model="voice" aria-label="Stimme" :style="SELECT" :disabled="!available" @change="changeVoice">
      <option v-if="available" :value="available">Deutsch</option><option value="">Aus</option>
    </select>
  </label>
  <label style="display:grid;gap:4px">Geschwindigkeit
    <select v-model="speed" aria-label="Geschwindigkeit" :style="SELECT" :disabled="!available" @change="changeSpeed">
      <option v-for="rate in SPEEDS" :key="rate" :value="rate">{{ speedLabel(rate) }}</option>
    </select>
  </label>
  <audio ref="audio" preload="none" @ended="stop('Vorlesen beendet.')" @error="active && stop('Die Stimme konnte nicht abgespielt werden.')"></audio>
  <p role="status" style="flex-basis:100%;margin:0">
    {{ message || (hasText ? 'Text markieren oder den Cursor setzen.' : 'Noch kein Text zum Vorlesen.') }}
  </p>
</div>`,
};
