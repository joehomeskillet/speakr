export const DictationSummary = {
    name: 'ClinicDictationSummary',
    props: {
        client: { type: Object, required: true },
        dictationId: { type: String, required: true },
        expanded: { type: Boolean, default: false },
    },
    setup(props) {
        const { ref, onBeforeUnmount } = globalThis.Vue;
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
            queued: 'Die KI-Zusammenfassung wartet auf die Verarbeitung.',
            processing: 'Die KI-Zusammenfassung wird erstellt …',
            failed: 'Die Erstellung ist fehlgeschlagen.',
            stale: 'Die Zusammenfassung bezieht sich auf einen früheren Textstand.',
            ready: '',
        };

        async function refresh(generate = false) {
            if (disposed || !open || busy.value) return;
            const active = ['queued', 'processing'].includes(data.value?.status);
            if (generate && (data.value?.may_generate !== true || active || error.value)) return;
            clearTimeout(timer);
            busy.value = true;
            submitting.value = generate;
            error.value = '';
            try {
                const result = await props.client.request(path, generate ? { method: 'POST', body: {} } : {});
                if (disposed) return;
                if (result.outcome === 'ok' && Object.hasOwn(messages, result.data?.status)) {
                    data.value = result.data;
                    if (open && ['queued', 'processing'].includes(data.value.status)) {
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
                if (!generate) data.value = null;
                error.value = 'Der Stand der KI-Zusammenfassung konnte nicht geladen werden.';
            } finally {
                busy.value = false;
                submitting.value = false;
            }
        }

        function toggle(event) {
            open = event.currentTarget.open;
            clearTimeout(timer);
            if (open) refresh();
        }
        onBeforeUnmount(() => { disposed = true; clearTimeout(timer); });
        return { data, error, busy, submitting, messages, toggle, refresh };
    },
    template: `
<details class="panel clinic-dictation-summary" :open="expanded" @toggle="toggle">
  <summary>KI-Zusammenfassung</summary>
  <div class="clinic-summary-body">
    <p v-if="error" role="alert">{{ error }}</p>
    <p role="status" class="hint">{{ submitting ? 'Erstellung wird angefordert …' : (data ? messages[data.status] : (busy ? 'Stand wird geladen …' : '')) }}</p>
    <p v-if="data?.text" class="clinic-summary-text">{{ data.text }}</p>
    <button v-if="error" type="button" class="button" :disabled="busy" @click="refresh()">Stand erneut laden</button>
    <button v-else-if="data?.may_generate && !['queued', 'processing'].includes(data.status)"
            type="button" class="button" :disabled="busy" @click="refresh(true)">
      {{ data.text ? 'Zusammenfassung neu erstellen' : 'Zusammenfassung erstellen' }}
    </button>
  </div>
</details>`,
};
