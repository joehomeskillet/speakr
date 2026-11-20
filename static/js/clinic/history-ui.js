/**
 * Ordered dictation history (PAR-WF-006). Clinical data stays in memory only.
 * Events keep server order; pagination slices that order and never re-sorts.
 * Colours come from the token contract, requests from the clinic API client.
 */

import { formatMoment } from './compose-archive.js';
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
    'history_linked': 'Zugeordnet',
    'history_stale': 'Der Stand ist veraltet. Bitte neu laden.',
    'history_denied': 'Keine Berechtigung für diesen Verlauf.',
    'history_failed': 'Der Verlauf konnte nicht geladen werden.',
    'history_kind_routing': 'Weiterleitung',
    'history_kind_revision': 'Korrektur',
    'history_kind_workflow': 'Ablauf',
    'history_kind_release': 'Freigabe',
    'history_kind_output': 'Ausgabe',
    'history_kind_acceptance': 'Abnahme',
    'history_kind_unknown': 'Ereignis',
    'history_action_asr': 'Automatische Transkription',
    'history_action_human': 'Manuelle Korrektur',
    'history_action_transfer': 'Arbeitsstand weitergeleitet',
    'history_action_archive': 'Archiviert',
    'history_action_restore': 'Aus Archiv wiederhergestellt',
    'history_action_release': 'Freigegeben',
    'history_action_supersede': 'Freigabe ersetzt',
    'history_link_parent': 'Vorgängerversion',
    'history_link_audio': 'Audioversion',
    'history_link_text': 'Textversion',
    'history_link_context': 'Kontextversion',
    'history_link_release': 'Freigabe',
};

const LINKS = {
    parent_revision_id: 'history_link_parent',
    audio_revision_id: 'history_link_audio',
    transcript_revision_id: 'history_link_text',
    context_revision_id: 'history_link_context',
    release_id: 'history_link_release',
};
const ACTIONS = new Map([
    ['ASR', 'history_action_asr'],
    ['HUMAN', 'history_action_human'],
    ['correction', 'history_action_human'],
    ['work_state_transferred', 'history_action_transfer'],
    ['recording.archived', 'history_action_archive'],
    ['recording.unarchived', 'history_action_restore'],
    ['released', 'history_action_release'],
    ['superseded', 'history_action_supersede'],
]);
const UUID = /[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}/i;

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

function displayName(event) {
    return [event.author?.display_name, event.actor_label, event.actor?.display_name]
        .map((name) => typeof name === 'string' ? name.trim() : '')
        .find((name) => name && !UUID.test(name) && !/^\d+$/.test(name));
}

function displayFields(event, revisions, links, t) {
    const fields = [];
    const at = event.at || event.created_at;
    const time = formatMoment(at);
    if (time) fields.push({ label: t('history_time'), value: time, datetime: at });
    if (event.redacted === true) return fields;

    const actor = displayName(event);
    if (actor) fields.push({ label: t('history_actor'), value: actor });
    if (event.kind === 'revision') {
        fields.push({ label: t('history_version'), value: String(revisions.get(event.id)) });
    }
    for (const link of links) {
        if (!link.available) continue;
        const revision = link.key === 'parent_revision_id' || link.key === 'transcript_revision_id'
            ? revisions.get(link.value) : undefined;
        if (link.key === 'parent_revision_id' && revision === undefined) continue;
        fields.push({ label: t(LINKS[link.key]), value: revision === undefined ? t('history_linked') : String(revision) });
    }
    return fields;
}

function row(event, index, revisions, t) {
    const hidden = event.redacted === true;
    const surface = surfaceOrNormal(surfaceName(event));
    const links = hidden ? [] : Object.keys(LINKS).filter((key) => key in event).map((key) => ({
        key: key,
        available: event[key] !== null && event[key] !== undefined && event[key] !== '',
        value: event[key] ? String(event[key]) : null,
    }));
    const version = event.version ?? event.revision_id ?? event.id;
    const kindKey = 'history_kind_' + event.kind;
    const action = event.action || event.revision_kind || null;
    return {
        index: index,
        key: String(event.kind) + ':' + String(event.id ?? index),
        kind: event.kind,
        kindLabel: t(kindKey),
        action: action,
        actor: hidden ? null : (event.actor_label ?? event.actor ?? null),
        at: event.at || null,
        version: hidden || version === undefined || version === null ? null : String(version),
        links: links,
        style: { background: surface.background, color: surface.color, borderColor: surface.border },
        statusLabel: surface.label,
        // Only these presentation fields reach the DOM. Raw keys and links stay in the model.
        displayHeading: t(ACTIONS.get(action) || (Object.hasOwn(TEXT, kindKey) ? kindKey : 'history_kind_unknown')),
        displayFields: displayFields(event, revisions, links, t),
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
    // Number visible text revisions in lineage order across all pages.
    const revisions = new Map();
    for (const event of list) {
        if (event.kind === 'revision' && event.redacted !== true) revisions.set(event.id, revisions.size + 1);
    }
    return {
        page: current,
        pages: pages,
        total: list.length,
        rows: list.slice(start, start + size).map((event, i) => row(event, start + i, revisions, t)),
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
    const HistoryEntry = {
        props: { entry: { type: Object, required: true } },
        render() {
            const { displayHeading, displayFields } = this.entry;
            const { h } = V;
            return [
                h('strong', { class: 'block text-sm font-semibold' }, displayHeading),
                displayFields.length ? h('dl', { class: '!mt-1 !flex flex-wrap !gap-x-3 gap-y-1 text-xs' },
                    displayFields.map(({ label, value, datetime }) => h('div', { class: 'flex min-w-0 flex-wrap gap-x-1', key: label }, [
                        h('dt', { class: 'text-ui-text-muted' }, label),
                        h('dd', { class: 'min-w-0 !break-normal' }, datetime ? [h('time', { datetime }, value)] : value),
                    ]))) : null,
            ];
        },
    };
    return {
        name: 'ClinicHistory',
        components: { HistoryEntry },
        props: { client: { type: Object, required: true }, dictationId: { type: String, required: true } },
        setup(props) {
            const h = useDictationHistory(props.client, props.dictationId, V);
            V.onMounted(h.load);
            return Object.assign({ t: translate }, h);
        },
        template: `
<section class="clinic-history mt-2 min-w-0 text-ui-text" :aria-label="t('history_title')" :aria-busy="status === 'loading'">
  <p v-if="status === 'loading'" class="text-sm text-ui-text-muted" role="status">{{ t('history_loading') }}</p>
  <div v-else-if="status !== 'ready' && status !== 'idle'" class="notice notice--warn" role="alert">
    <div class="notice__body">
      <p class="notice__message">{{ message }}</p>
      <div v-if="status !== 'denied'" class="notice__actions">
        <button class="btn btn--sm btn--ghost" type="button" @click="retry">{{ t('history_retry') }}</button>
      </div>
    </div>
  </div>
  <p v-else-if="view.total === 0" class="rounded-ui-sm border border-ui-border px-3 py-2 text-sm text-ui-text-muted" role="status">
    {{ t('history_empty') }}
  </p>
  <ol v-else class="clinic-history-list !ml-2 !gap-0" role="list">
    <li v-for="r in view.rows" :key="r.key" tabindex="0"
        class="clinic-history-item group !border-0 !p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ui-text">
      <div class="relative break-words border-l border-ui-border pb-4 pl-4 group-last:pb-0">
        <span aria-hidden="true" class="absolute -left-1.5 top-1.5 h-2.5 w-2.5 rounded-full border border-ui-text-muted bg-ui-surface"></span>
        <history-entry :entry="r" />
      </div>
    </li>
  </ol>
  <nav v-if="status === 'ready' && view.pages > 1" class="mt-3 flex flex-wrap items-center gap-2" :aria-label="t('history_title')">
    <button class="btn btn--sm btn--ghost" type="button" :disabled="view.page <= 1" @click="prev">{{ t('history_prev') }}</button>
    <span class="text-sm text-ui-text-muted" aria-live="polite">{{ t('history_page', { page: view.page, pages: view.pages }) }}</span>
    <button class="btn btn--sm btn--ghost" type="button" :disabled="view.page >= view.pages" @click="next">{{ t('history_next') }}</button>
  </nav>
</section>`,
    };
}
