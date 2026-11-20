// Marked is already shipped with Speakr. Render its tokens as fixed Vue elements;
// model-provided strings only become text nodes, including raw HTML and link labels.
const SummaryText = {
    props: { text: { type: String, required: true } },
    render() {
        const h = globalThis.Vue.h;
        const lexer = globalThis.marked?.lexer;
        const blocks = lexer ? lexer(this.text) : [];
        const headingBase = blocks.reduce((level, t) => t.type === 'heading' ? Math.min(level, t.depth) : level, 6);
        function renderToken(t) {
            const children = () => t.tokens ? t.tokens.map(renderToken) : t.text;
            switch (t.type) {
            case 'heading': return h('h' + Math.min(6, 3 + t.depth - headingBase), children());
            case 'list': return h(t.ordered ? 'ol' : 'ul', t.items.map(i => h('li', i.tokens.map(renderToken))));
            case 'paragraph': return h('p', children());
            case 'strong': return h('strong', children());
            case 'em': return h('em', children());
            case 'br': return h('br');
            case 'text':
            case 'link': return children();
            case 'image':
            case 'codespan': return t.text;
            case 'space': return '\n';
            default: return t.raw || '';
            }
        }
        return h('div', { class: 'clinic-summary-text' }, blocks.length ? blocks.map(renderToken) : this.text);
    },
};

const PROPS = {
    client: { type: Object, required: true },
    dictationId: { type: String, required: true },
    expanded: { type: Boolean, default: false },
    textRevisionId: { type: String, default: null },
    dirty: { type: Boolean, default: false },
};

const isPending = (status) => ['waiting_for_transcription', 'queued', 'processing'].includes(status);

const SummaryBody = {
    components: { SummaryText },
    props: PROPS,
    setup(props) {
        const { ref, watch, onBeforeUnmount } = globalThis.Vue;
        const data = ref(null);
        const busy = ref(false);
        const error = ref('');
        const path = '/dictations/' + encodeURIComponent(props.dictationId) + '/summary';
        const submitting = ref(false);
        let open = false;
        let disposed = false;
        let timer;
        const messages = {
            empty: 'Für dieses Diktat liegt noch keine KI-Zusammenfassung vor.',
            waiting_for_transcription: 'Die Zusammenfassung wird nach der Transkription automatisch erstellt.',
            queued: 'Die KI-Zusammenfassung wartet auf die Verarbeitung.',
            processing: 'Die KI-Zusammenfassung wird erstellt …',
            failed: 'Die Erstellung ist fehlgeschlagen.',
            stale: 'Der Bezug zur aktuellen Textfassung ist nicht bestätigt.',
            ready: '',
        };

        async function refresh(generate = false) {
            if (disposed || !open || busy.value) return;
            const active = isPending(data.value?.status);
            if (generate && (data.value?.may_generate !== true || active || error.value)) return;
            const revision = props.textRevisionId;
            clearTimeout(timer);
            busy.value = true;
            submitting.value = generate;
            error.value = '';
            try {
                const result = await props.client.request(path, generate ? { method: 'POST', body: {} } : {});
                if (disposed || revision !== props.textRevisionId) return;
                if (result.outcome === 'ok' && Object.hasOwn(messages, result.data?.status)) {
                    data.value = result.data;
                    if (open && isPending(data.value.status)) {
                        timer = setTimeout(() => refresh(), 2500);
                    }
                } else {
                    if (!generate || [401, 403, 404].includes(result.status)) data.value = null;
                    const errors = {
                        403: 'Für diese Aktion fehlt die Berechtigung.',
                        404: 'Für dieses Diktat ist keine KI-Zusammenfassung verfügbar.',
                        409: 'Zum Erstellen wird ein aktueller Diktattext benötigt.',
                        503: 'Der lokale KI-Dienst ist derzeit nicht verfügbar.',
                    };
                    error.value = errors[result.status] || 'Der Stand der KI-Zusammenfassung konnte nicht geladen werden.';
                }
            } catch {
                if (disposed || revision !== props.textRevisionId) return;
                if (!generate) data.value = null;
                error.value = 'Der Stand der KI-Zusammenfassung konnte nicht geladen werden.';
            } finally {
                // A newer GET owns the busy state. An in-flight POST must settle before reading its outcome.
                if (!disposed && (generate || revision === props.textRevisionId)) {
                    busy.value = false;
                    submitting.value = false;
                    if (revision !== props.textRevisionId) refresh();
                }
            }
        }

        watch(() => props.textRevisionId, () => {
            clearTimeout(timer);
            data.value = null;
            error.value = '';
            if (!submitting.value) {
                busy.value = false;
                refresh();
            }
        });

        function toggle(event) {
            open = event.currentTarget.open;
            clearTimeout(timer);
            if (open) refresh();
        }
        onBeforeUnmount(() => { disposed = true; clearTimeout(timer); });
        return { data, error, busy, submitting, messages, toggle, refresh, isPending };
    },
    template: `
<details class="panel clinic-dictation-summary" :open="expanded" @toggle="toggle">
  <summary>KI-Zusammenfassung</summary>
  <div class="clinic-summary-body">
    <p v-if="error" role="alert">{{ error }}</p>
    <p role="status" class="hint">{{ submitting ? 'Erstellung wird angefordert …' : (data ? messages[data.status] : (busy ? 'Stand wird geladen …' : '')) }}</p>
    <SummaryText v-if="data?.text" :text="data.text" />
    <p v-if="dirty" class="hint">Die Zusammenfassung verwendet die zuletzt gespeicherte Textfassung.</p>
    <button v-if="error" type="button" class="button" :disabled="busy" @click="refresh()">Stand erneut laden</button>
    <button v-else-if="data?.may_generate && !isPending(data.status)"
            type="button" class="button" :disabled="busy" @click="refresh(true)">
      {{ data.text ? 'Zusammenfassung neu erstellen' : 'Zusammenfassung erstellen' }}
    </button>
  </div>
</details>`,
};


// Replacing the inner instance cancels its poll and discards pending responses for the old dictation.
export const DictationSummary = {
    name: 'ClinicDictationSummary',
    props: PROPS,
    components: { SummaryBody },
    template: `<SummaryBody :key="dictationId" v-bind="$props" />`,
};
