import { capabilities } from './clipboard-ui.js';
import { createOutputViewController } from './compose-output.js';

const TOUCH = { minHeight: '48px', minWidth: '48px' };
const SELECT = { ...TOUCH, borderColor: 'var(--v8-text-2)', width: 'auto', maxWidth: '100%', paddingInlineEnd: '36px' };
const DISABLED = { backgroundColor: 'var(--v8-s1)', color: 'var(--v8-text-2)', cursor: 'not-allowed', opacity: 1 };
const FILE_FORMATS = ['txt', 'docx', 'rtf', 'odt'];
const COPY_CONFIRMATION = {
    clear: 'Text kopieren und die Ansicht leeren? Das gespeicherte Diktat bleibt erhalten.',
    close: 'Text kopieren und das Diktat schliessen? Sie können es jederzeit wieder öffnen.',
};

const LiveTransferPanel = {
    props: ['client', 'dictationId', 'vue', 'open', 'transferContext'],
    emits: ['confirmed'],
    setup(props, { emit }) {
        const V = props.vue || globalThis.Vue;
        let mounted = true;
        let attempt = null;
        V.onBeforeUnmount(() => { mounted = false; });
        const output = createOutputViewController({
            client: props.client, dictationId: props.dictationId, vue: V,
            includeAllFileFormats: true, requireRelease: true, liveClipboard: true, isCurrent: () => mounted,
        });
        const formats = V.computed(() => output.targets.value.filter((t) =>
            t.channel === 'file' && FILE_FORMATS.includes(t.id)));
        const clipboard = V.computed(() => output.clipboardCopy());
        const copyState = V.computed(() => clipboard.value?.state.value);
        const copyFormat = V.ref('plain');
        const afterCopy = V.ref('none');
        const selected = V.ref('txt');
        const supported = capabilities();
        const copyLocked = V.computed(() => Boolean(copyState.value?.busy || copyState.value?.retryFormat
            || copyState.value?.canRetryReport));
        const canFollowUp = V.computed(() => props.transferContext() !== null);
        const canCopy = V.computed(() => Boolean(clipboard.value) && supported[copyFormat.value]
            && !copyState.value.busy && (!copyState.value.canRetryReport || copyState.value.retryFormat)
            && (copyState.value.retryFormat || afterCopy.value === 'none' || canFollowUp.value));
        const hasFile = V.computed(() => formats.value.some((file) => file.available));
        const canPrepare = V.computed(() => output.canExport(selected.value));
        V.watch(() => props.open, (open) => {
            if (!open && attempt) attempt.context = null;
            if (open && !copyLocked.value) void output.load();
        }, { flush: 'sync' });

        function settle(copier, result) {
            if (!mounted || clipboard.value !== copier) return;
            if (result.retryFormat || result.canRetryReport) return;
            const confirmed = attempt;
            attempt = null;
            if (result.phase !== 'copied' || !confirmed || confirmed.action === 'none') return;
            afterCopy.value = 'none';
            if (props.open) emit('confirmed', confirmed);
        }

        async function copy() {
            if (!canCopy.value) return;
            const copier = clipboard.value;
            if (!attempt) {
                const action = afterCopy.value;
                if (action !== 'none' && !globalThis.confirm(COPY_CONFIRMATION[action])) return;
                attempt = { action, context: props.transferContext() };
            }
            settle(copier, await copier.copy(copyFormat.value));
        }

        async function retryReport() {
            const copier = clipboard.value;
            settle(copier, await copier.retryReport());
        }

        void output.load();
        return { output, formats, clipboard, copyState, copyFormat, afterCopy, selected,
            supported, copyLocked, canFollowUp, canCopy, copy, retryReport, hasFile, canPrepare,
            touch: TOUCH, selectStyle: SELECT, disabledStyle: DISABLED };
    },
    template: `
<section id="live-transfer-panel" class="panel" aria-labelledby="live-transfer-title" style="margin-block-end:16px; min-width:0">
  <h2 id="live-transfer-title">Freigegebene Fassung ausgeben</h2>
  <p>Der Arbeitsstand kann hier weder kopiert noch als Datei heruntergeladen werden.</p>
  <p v-if="output.phase.value === 'loading'" role="status">Möglichkeiten werden geladen …</p>
  <div v-else-if="output.phase.value === 'failed'">
    <p role="alert">Die Möglichkeiten konnten nicht geladen werden.</p>
    <button type="button" class="button" :style="touch" @click="output.load()">Erneut laden</button>
  </div>
  <template v-else>
    <p v-if="!clipboard && !hasFile" role="status">
      {{ formats[0]?.reason || 'Es steht keine freigegebene Fassung zur Ausgabe bereit.' }}
    </p>
    <p v-else>In einer anderen Anwendung müssen Sie den Text selbst einfügen oder die Datei selbst öffnen. Ob das gelingt, kann Speakr nicht feststellen.</p>
    <div v-if="clipboard" class="actions" style="margin-block:12px; gap:8px 16px">
        <label for="live-copy-format" style="display:grid; gap:4px; min-width:0">
          Kopierformat
          <select id="live-copy-format" v-model="copyFormat" :style="selectStyle" :disabled="copyLocked">
            <option value="plain" :disabled="!supported.plain">Nur Text</option>
            <option value="formatted" :disabled="!supported.formatted">Mit Formatierung</option>
          </select>
        </label>
        <label for="live-copy-after" style="display:grid; gap:4px; min-width:0">
          Nach dem Kopieren
          <select id="live-copy-after" v-model="afterCopy" :style="selectStyle" :disabled="copyLocked">
            <option value="none">Diktat offen lassen</option>
            <option value="clear" :disabled="!canFollowUp">Ansicht leeren</option>
            <option value="close" :disabled="!canFollowUp">Diktat schliessen</option>
          </select>
        </label>
    </div>
    <div class="actions" style="margin-block:12px">
      <button type="button" class="button" :class="{ primary: canCopy }" :style="[touch, !canCopy && disabledStyle]"
        :disabled="!canCopy" @click="copy">Text kopieren</button>
      <button v-if="copyState?.canRetryReport" type="button" class="button" :style="touch"
        :disabled="copyState.busy" @click="retryReport">Ergebnis erneut melden</button>
      <button v-if="copyState?.phase === 'stale' && !copyLocked" type="button" class="button" :style="touch"
        @click="output.load()">Freigegebene Fassung neu laden</button>
    </div>
    <p v-if="clipboard && !supported[copyFormat]">Dieser Browser unterstützt das Kopieren in dieser Form nicht.</p>
    <p v-if="clipboard && !canFollowUp && afterCopy !== 'none'">Offene Änderungen zuerst speichern und die Aufnahme beenden.</p>
    <p v-if="copyState" role="status">{{ copyState.message }}</p>
    <div class="actions" style="margin-block:12px">
      <label for="live-transfer-format">Dateiformat</label>
      <select id="live-transfer-format" v-model="selected" :style="[selectStyle, !hasFile && disabledStyle]" :disabled="!hasFile">
        <option v-for="file in formats" :key="file.id" :value="file.id" :disabled="!file.available">{{ file.label }}</option>
      </select>
      <a v-if="output.downloadUrl(selected)" class="button" :style="touch"
        :href="output.downloadUrl(selected)" download @click="output.markDownloadStarted(selected)">Datei herunterladen</a>
      <button v-else type="button" class="button" :style="[touch, !canPrepare && disabledStyle]" :disabled="!canPrepare"
        @click="output.exportTo(selected)">Datei vorbereiten</button>
    </div>
    <p role="status">{{ output.results.value[selected]?.label }}</p>
    <p v-if="output.downloadUrl(selected)">Ob die Datei gespeichert wurde, kann Speakr nicht feststellen.</p>
  </template>
</section>`,
};

export const LiveTransfer = {
    components: { LiveTransferPanel },
    props: ['client', 'dictationId', 'vue', 'transferContext'],
    emits: ['confirmed'],
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const open = V.ref(false);
        const visited = V.ref(false);
        function toggle() {
            open.value = !open.value;
            visited.value = true;
        }
        return { open, visited, toggle, touch: TOUCH };
    },
    template: `
<div data-testid="live-transfer" style="margin-block-end:16px">
  <button type="button" class="button" :style="touch" :aria-expanded="open" aria-controls="live-transfer-panel"
    @click="toggle">{{ open ? 'Ausgabemöglichkeiten ausblenden' : 'Freigegebene Fassung ausgeben' }}</button>
  <LiveTransferPanel v-if="visited" v-show="open" :open="open" :client="client" :dictation-id="dictationId"
    :transfer-context="transferContext" :vue="vue" style="margin-block-start:12px" @confirmed="$emit('confirmed', $event)" />
</div>`,
};
