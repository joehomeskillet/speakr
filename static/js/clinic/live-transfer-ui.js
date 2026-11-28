import { capabilities } from './clipboard-ui.js';
import { createOutputViewController } from './compose-output.js';

const TOUCH = { minHeight: '48px', minWidth: '48px' };
const SELECT = { ...TOUCH, borderColor: 'var(--v8-text-2)', width: 'auto', maxWidth: '100%' };
const DISABLED = { backgroundColor: 'var(--v8-s1)', color: 'var(--v8-text-2)', cursor: 'not-allowed', opacity: 1 };
const FILE_FORMATS = ['txt', 'docx', 'rtf', 'odt'];

const LiveTransferPanel = {
    props: ['client', 'dictationId', 'vue'],
    setup(props) {
        const V = props.vue || globalThis.Vue;
        let mounted = true;
        V.onBeforeUnmount(() => { mounted = false; });
        const output = createOutputViewController({
            client: props.client, dictationId: props.dictationId, vue: V,
            includeAllFileFormats: true, requireRelease: true, isCurrent: () => mounted,
        });
        const formats = V.computed(() => output.targets.value.filter((t) =>
            t.channel === 'file' && FILE_FORMATS.includes(t.id)));
        const clipboard = V.computed(() => output.clipboardCopy());
        const selected = V.ref('txt');
        const browserCanCopy = capabilities().plain;
        const canCopy = V.computed(() => Boolean(clipboard.value) && browserCanCopy && !clipboard.value.state.value.busy);
        const hasFile = V.computed(() => formats.value.some((file) => file.available));
        const canPrepare = V.computed(() => output.canExport(selected.value));
        void output.load();
        return { output, formats, clipboard, selected, browserCanCopy, canCopy, hasFile, canPrepare,
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
    <div class="actions" style="margin-block:12px">
      <button type="button" class="button" :class="{ primary: canCopy }" :style="[touch, !canCopy && disabledStyle]"
        :disabled="!canCopy"
        @click="clipboard.copy('plain')">Text kopieren</button>
      <button v-if="clipboard?.state.value.phase === 'unreported'" type="button" class="button" :style="touch"
        @click="clipboard.retryReport()">Ergebnis erneut melden</button>
    </div>
    <p v-if="clipboard && !browserCanCopy">Dieser Browser unterstützt das Kopieren hier nicht.</p>
    <p v-if="clipboard" role="status">{{ clipboard.state.value.message }}</p>
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
    props: ['client', 'dictationId', 'vue'],
    setup(props) {
        return { open: (props.vue || globalThis.Vue).ref(false), touch: TOUCH };
    },
    template: `
<div data-testid="live-transfer" style="margin-block-end:16px">
  <button type="button" class="button" :style="touch" :aria-expanded="open" aria-controls="live-transfer-panel"
    @click="open = !open">{{ open ? 'Ausgabemöglichkeiten ausblenden' : 'Freigegebene Fassung ausgeben' }}</button>
  <LiveTransferPanel v-if="open" :client="client" :dictation-id="dictationId" :vue="vue" style="margin-block-start:12px" />
</div>`,
};
