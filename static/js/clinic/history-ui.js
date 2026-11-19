/**
 * Ordered dictation history (PAR-WF-006). Clinical data stays in memory only.
 * Events keep server order; pagination slices that order and never re-sorts.
 * Colours come from the token contract, requests from the clinic API client.
 */

import { TokenContractError, surfaceFor } from './tokens.js';

export const PAGE_SIZE = 20;

const TEXT = {
    'history_title': 'Verlauf',
    'history_empty': 'Keine Ereignisse vorhanden.',
    'history_loading': 'Verlauf wird geladen …',
    'history_retry': 'Erneut versuchen',
    'history_prev': 'Vorherige Seite',
    'history_next': 'Nächste Seite',
    'history_page': 'Seite {page} von {pages}',
    'history_actor': 'Akteur',
    'history_time': 'Zeit',
    'history_version': 'Version',
    'history_unavailable': 'Nicht verfügbar',
    'history_stale': 'Der Stand ist veraltet. Bitte neu laden.',
    'history_denied': 'Keine Berechtigung für diesen Verlauf.',
    'history_failed': 'Der Verlauf konnte nicht geladen werden.',
    'history_kind_routing': 'Weiterleitung',
    'history_kind_revision': 'Korrektur',
    'history_kind_workflow': 'Ablauf',
    'history_kind_release': 'Freigabe',
    'history_kind_output': 'Ausgabe',
    'history_kind_acceptance': 'Abnahme',
};

const LINKS = ['parent_revision_id', 'audio_revision_id', 'transcript_revision_id', 'context_revision_id', 'release_id'];

export function translate(key, params) {
    let text = TEXT[key] || key;
    for (const name of Object.keys(params || {})) text = text.split('{' + name + '}').join(String(params[name]));
    return text;
}

function surfaceName(event) {
    if (event.action === 'superseded') return 'warning';
    if (event.kind === 'release' || event.kind === 'acceptance' || event.kind === 'output') return 'success';
    if (event.kind === 'routing') return 'processing';
    return 'normal';
}

function surfaceOrNormal(name) {
    try {
        return surfaceFor(name);
    } catch (error) {
        if (error instanceof TokenContractError) return surfaceFor('normal');
        throw error;
    }
}

function row(event, index, t) {
    const hidden = event.redacted === true;
    const surface = surfaceOrNormal(surfaceName(event));
    const links = hidden ? [] : LINKS.filter((key) => key in event).map((key) => ({
        key: key,
        available: event[key] !== null && event[key] !== undefined && event[key] !== '',
        value: event[key] ? String(event[key]) : null,
    }));
    const version = event.version ?? event.revision_id ?? event.id;
    return {
        index: index,
        key: String(event.kind) + ':' + String(event.id ?? index),
        kind: event.kind,
        kindLabel: t('history_kind_' + event.kind),
        action: event.action || event.revision_kind || null,
        actor: hidden ? null : (event.actor_label ?? event.actor ?? null),
        at: event.at || null,
        version: hidden || version === undefined || version === null ? null : String(version),
        links: links,
        style: { background: surface.background, color: surface.color, borderColor: surface.border },
        statusLabel: surface.label,
    };
}

/** Pure model: ordered rows for one page. Order is the input order. */
export function buildPage(events, page, options) {
    const opts = options || {};
    const t = opts.t || translate;
    const size = opts.pageSize || PAGE_SIZE;
    const list = Array.isArray(events) ? events : [];
    const pages = Math.max(1, Math.ceil(list.length / size));
    const current = Math.min(Math.max(1, Number.isInteger(page) ? page : 1), pages);
    const start = (current - 1) * size;
    return {
        page: current,
        pages: pages,
        total: list.length,
        rows: list.slice(start, start + size).map((event, i) => row(event, start + i, t)),
    };
}

const STATE_MESSAGE = {
    permission: 'history_denied',
    conflict: 'history_stale',
};

/**
 * Composable. `vue` defaults to the global Vue build used by the app.
 * `client` is a clinic API client (request(path)).
 */
export function useDictationHistory(client, dictationId, vue, t) {
    const V = vue || globalThis.Vue;
    const tr = t || translate;
    const events = V.ref([]);
    const page = V.ref(1);
    const status = V.ref('idle'); // idle | loading | ready | error | denied | stale
    const message = V.ref('');
    let seq = 0;

    const view = V.computed(() => buildPage(events.value, page.value, { t: tr }));

    async function load() {
        const mine = (seq += 1);
        status.value = 'loading';
        message.value = '';
        const res = await client.request('/dictations/' + encodeURIComponent(dictationId) + '/history');
        if (mine !== seq || res.outcome === 'cancelled') return res;
        if (res.outcome === 'ok'
            && res.data && res.data.dictation_id === dictationId && Array.isArray(res.data.events)) {
            events.value = res.data.events;
            status.value = 'ready';
            return res;
        }
        // Never keep clinical rows after a refusal or a mismatching response.
        events.value = [];
        page.value = 1;
        if (res.outcome === 'permission') status.value = 'denied';
        else if (res.outcome === 'conflict' || res.outcome === 'ok') status.value = 'stale';
        else status.value = 'error';
        message.value = tr(STATE_MESSAGE[res.outcome] || (res.outcome === 'ok' ? 'history_stale' : 'history_failed'));
        return res;
    }

    function goto(n) {
        page.value = buildPage(events.value, n, { t: tr }).page;
    }

    return {
        events: events, page: page, status: status, message: message, view: view,
        load: load, retry: load,
        next: () => goto(page.value + 1),
        prev: () => goto(page.value - 1),
        goto: goto,
    };
}

/** Vue component definition (runtime template). Props: client, dictationId. */
export function createHistoryComponent(vue) {
    const V = vue || globalThis.Vue;
    return {
        name: 'ClinicHistory',
        props: { client: { type: Object, required: true }, dictationId: { type: String, required: true } },
        setup(props) {
            const h = useDictationHistory(props.client, props.dictationId, V);
            V.onMounted(h.load);
            return Object.assign({ t: translate }, h);
        },
        template: `
<section class="clinic-history" :aria-label="t('history_title')" :aria-busy="status === 'loading'">
  <h2>{{ t('history_title') }}</h2>
  <p v-if="status === 'loading'" role="status">{{ t('history_loading') }}</p>
  <div v-else-if="status !== 'ready' && status !== 'idle'" role="alert">
    <p>{{ message }}</p>
    <button v-if="status !== 'denied'" type="button" @click="retry">{{ t('history_retry') }}</button>
  </div>
  <p v-else-if="view.total === 0" role="status">{{ t('history_empty') }}</p>
  <ol v-else class="clinic-history-list">
    <li v-for="r in view.rows" :key="r.key" tabindex="0" :style="r.style" class="clinic-history-item">
      <strong>{{ r.kindLabel }}</strong><span v-if="r.action"> · {{ r.action }}</span>
      <dl>
        <dt>{{ t('history_actor') }}</dt><dd>{{ r.actor || t('history_unavailable') }}</dd>
        <dt>{{ t('history_time') }}</dt><dd><time :datetime="r.at">{{ r.at || t('history_unavailable') }}</time></dd>
        <dt>{{ t('history_version') }}</dt><dd>{{ r.version || t('history_unavailable') }}</dd>
        <template v-for="l in r.links" :key="l.key"><dt>{{ l.key }}</dt><dd>{{ l.available ? l.value : t('history_unavailable') }}</dd></template>
      </dl>
    </li>
  </ol>
  <nav v-if="status === 'ready' && view.pages > 1" :aria-label="t('history_title')">
    <button type="button" :disabled="view.page <= 1" @click="prev">{{ t('history_prev') }}</button>
    <span aria-live="polite">{{ t('history_page', { page: view.page, pages: view.pages }) }}</span>
    <button type="button" :disabled="view.page >= view.pages" @click="next">{{ t('history_next') }}</button>
  </nav>
</section>`,
    };
}
