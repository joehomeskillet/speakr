/** Staging-only context binding. Server capability and lease remain authoritative. */
export const isSyntheticCase = (value) => typeof value === 'string' && /^SYN-[0-9]{4,8}$/.test(value);

export function createContextBinding({ client, dictationId, reactive = (v) => v, canWrite, onBound }) {
    const path = '/dictations/' + encodeURIComponent(dictationId) + '/context';
    const state = reactive({ available: false, input: '', busy: false, message: '', error: false, blocked: false });
    let disposed = false;
    let sequence = 0;

    function reset() {
        sequence += 1;
        state.available = false;
        state.input = '';
        state.message = '';
        state.error = false;
        state.blocked = false;
    }

    async function load() {
        const mine = ++sequence;
        state.available = false;
        const res = await client.request(path);
        if (disposed || mine !== sequence) return;
        state.available = res.outcome === 'ok' && res.status === 200
            && res.data && Object.hasOwn(res.data, 'case_display');
    }

    function writable() { return !disposed && state.available && !state.blocked && canWrite(); }

    async function bind() {
        if (!writable() || state.busy) return false;
        state.error = true;
        if (!isSyntheticCase(state.input)) {
            state.message = 'Synthetische Fall-ID eingeben: SYN- und 4 bis 8 Ziffern.';
            return false;
        }
        state.busy = true;
        state.message = '';
        const mine = sequence;
        const res = await client.request(path, { method: 'POST', body: { case_reference: state.input } });
        if (disposed || mine !== sequence) { state.busy = false; return false; }
        state.busy = false;
        if (res.outcome === 'ok' && isSyntheticCase(res.data?.case_display)) {
            state.input = '';
            await onBound();
            if (disposed) return false;
            state.error = false;
            state.message = 'Fall-ID verknüpft.';
            return true;
        }
        if ([401, 403, 404, 503].includes(res.status)) state.available = false;
        state.blocked = true; // An uncertain write must be reconciled, never automatically repeated.
        state.message = res.status === 423 ? 'Bearbeitung zuerst anfordern.'
            : res.status === 409 ? 'Der Stand wurde geändert. Aktuellen Stand laden.'
                : 'Fall-ID nicht bestätigt. Aktuellen Stand laden, bevor Sie erneut verknüpfen.';
        return false;
    }

    return { state, load, bind, writable, reset, refresh: onBound, dispose() { disposed = true; reset(); } };
}

export const CONTEXT_BIND_TEMPLATE = `
<div>
<section v-if="binding.state.available" aria-label="Fall-ID verknüpfen" data-testid="context-bind">
  <p class="hint">Simulation (MockKIS, nur Staging)</p>
  <p v-if="!binding.writable()" role="status">Bearbeitung zuerst anfordern.</p>
  <form @submit.prevent="binding.bind()" :aria-busy="binding.state.busy">
    <label style="display: grid; gap: 6px">
      <span>Synthetische Fall-ID</span>
      <input v-model="binding.state.input" aria-label="Synthetische Fall-ID" autocomplete="off"
        placeholder="SYN-0001" pattern="SYN-[0-9]{4,8}" maxlength="12" required
        :disabled="!binding.writable() || binding.state.busy" style="width: 100%; min-width: 0; box-sizing: border-box">
    </label>
    <button type="submit" class="secondary" style="margin-top: 10px"
      :disabled="!binding.writable() || binding.state.busy">Fall-ID verknüpfen</button>
  </form>
</section>
<p v-if="binding.state.message" :role="binding.state.error ? 'alert' : 'status'">{{ binding.state.message }}</p>
<button v-if="binding.state.blocked" type="button" class="secondary" @click="binding.refresh()">Aktuellen Stand laden</button>
</div>`;
