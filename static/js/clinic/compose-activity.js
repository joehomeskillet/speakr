/**
 * Clinic activity and audit view (V8 P18, PAR-WF-006, PAR-OPS-005/006).
 *
 * Two sections, both read-only:
 *   Verlauf    one dictation's ordered history, rendered by the existing
 *              history-ui component (ids, actions, revision references, no text).
 *   Statistik  aggregate counts and audio lengths from the scoped statistics
 *              route (GET /statistics/aggregates, package v8-30). Date bounds
 *              are sent as YYYY-MM-DD; the server reads them as Europe/Zurich
 *              midnight, window [von, bis). A missing length is shown as
 *              "Nicht erhoben", never as zero.
 *
 * No patient data, no transcript text, nothing in browser storage. Colours come
 * from tokens.js, requests from the shared clinic api client.
 *
 * Contract for the mount package:
 *   ActivityView                 Vue options object, props client, currentUser, dictationId, vue
 *   registerActivityView(app)    app.component('clinic-activity-view', ActivityView)
 *
 * Not available yet (no route exists): a cross-dictation audit list with actor,
 * action and object filters, and a CSV download. The export is a server command.
 */

import { surfaceFor } from './tokens.js';
import { V8_PAGE } from './compose-import.js';
import { createHistoryComponent } from './history-ui.js';
import { formatAudioDuration } from './worklist-ui.js';

export const STATISTICS_PATH = '/statistics/aggregates';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

export const LABELS = Object.freeze({
    title: 'Aktivität und Audit',
    eyebrow: 'Verwaltung',
    eyebrowScoped: 'Diktat',
    subline: 'Verlauf eines Diktats und Statistik des eigenen Berechtigungsbereichs. Es werden nur Kennungen, Aktionen und Revisionsbezüge gezeigt, kein Diktattext.',
    sublineScoped: 'Wer hat was an diesem Diktat geändert? Ereignisse mit Akteur, Aktion und betroffener Version.',
    historyTitle: 'Verlauf eines Diktats',
    historyOnlyTitle: 'Diktat / Verlauf',
    dictationId: 'Diktat-ID',
    showHistory: 'Verlauf anzeigen',
    historyInvalid: 'Bitte eine gültige Diktat-ID eingeben.',
    statsTitle: 'Statistik',
    timeBasis: 'Zeitbasis',
    from: 'Von (einschliesslich)',
    to: 'Bis (ausschliesslich)',
    grain: 'Zeitraster',
    groupBy: 'Gruppieren nach',
    load: 'Statistik laden',
    retry: 'Erneut versuchen',
    loading: 'Statistik wird geladen …',
    empty: 'Keine Diktate im gewählten Zeitraum und Berechtigungsbereich.',
    denied: 'Keine Berechtigung für diese Statistik.',
    failed: 'Die Statistik konnte nicht geladen werden.',
    stale: 'Die Berechtigungen haben sich geändert. Bitte neu laden.',
    deniedUser: 'Kein Zugriff. Bitte anmelden.',
    notCollected: 'Nicht erhoben',
    incomplete: 'unvollständig',
    complete: 'vollständig',
    provenance: 'Herkunft der Zahlen',
    zone: 'Zeitzone',
    window: 'Zeitfenster',
    asOf: 'Stand',
    excluded: 'Ohne Zeitstempel (nicht gezählt)',
    count: 'Anzahl',
    total: 'Gesamtdauer',
    average: 'Durchschnitt',
    known: 'Mit bekannter Dauer',
    state: 'Vollständigkeit',
    unassigned: 'nicht zugewiesen',
    historyEmpty: 'Diktat-ID eingeben, um den Verlauf anzuzeigen.',
    noExport: 'CSV-Export: nur über den autorisierten Serverbefehl, nicht in dieser Ansicht.',
});

export const TIME_BASES = Object.freeze([
    Object.freeze({ value: 'created', label: 'Eingang des Diktats' }),
    Object.freeze({ value: 'completed', label: 'Fertigstellung (Freigabe)' }),
]);
export const GRAINS = Object.freeze([
    Object.freeze({ value: 'day', label: 'Tag' }),
    Object.freeze({ value: 'week', label: 'Woche' }),
    Object.freeze({ value: 'month', label: 'Monat' }),
]);
export const GROUPS = Object.freeze([
    Object.freeze({ value: 'status', label: 'Status' }),
    Object.freeze({ value: 'author', label: 'Diktiert von' }),
    Object.freeze({ value: 'typist', label: 'Schreibkraft' }),
    Object.freeze({ value: 'period', label: 'Zeitraum' }),
]);
const STATUS_LABELS = Object.freeze({ open: 'Offen', in_review: 'In Prüfung', released: 'Freigegeben' });

function userIdOf(user) {
    return user && user.id != null && String(user.id) !== '' ? String(user.id) : null;
}

function validDay(text) {
    const m = DAY.exec(text);
    if (!m) return false;
    const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
    return d.getUTCFullYear() === Number(m[1]) && d.getUTCMonth() === Number(m[2]) - 1 && d.getUTCDate() === Number(m[3]);
}

/** Pure: form values to a query string, or an error text. */
export function buildStatisticsQuery(form) {
    const f = form || {};
    const from = String(f.from || '').trim();
    const to = String(f.to || '').trim();
    const groups = GROUPS.map((g) => g.value).filter((v) => (f.groupBy || []).includes(v));
    const period = groups.includes('period');
    if (!TIME_BASES.some((b) => b.value === (f.timeBasis || 'created'))) return { error: 'Ungültige Zeitbasis.' };
    if ((from && !validDay(from)) || (to && !validDay(to))) return { error: 'Datum bitte als JJJJ-MM-TT eingeben.' };
    if (from && to && from >= to) return { error: 'Das Enddatum muss nach dem Startdatum liegen.' };
    if (period && (!from || !to)) return { error: 'Für die Gruppierung nach Zeitraum sind Start- und Enddatum nötig.' };
    if (period && !GRAINS.some((g) => g.value === f.grain)) return { error: 'Bitte ein Zeitraster wählen.' };
    const params = new URLSearchParams();
    params.set('time_basis', f.timeBasis || 'created');
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    if (period) params.set('grain', f.grain);
    if (groups.length) params.set('group_by', groups.join(','));
    return { query: params.toString() };
}

function groupValue(key, value) {
    if (key === 'status') return STATUS_LABELS[value] || String(value);
    if (key === 'author_user_id' || key === 'typist_user_id') {
        return value === null || value === undefined ? LABELS.unassigned : 'Benutzer ' + value;
    }
    return value === null || value === undefined ? '-' : String(value);
}

const GROUP_COLUMNS = Object.freeze({
    status: ['status', 'Status'],
    author: ['author_user_id', 'Diktiert von'],
    typist: ['typist_user_id', 'Schreibkraft'],
    period: ['period_start', 'Zeitraum ab'],
});

function seconds(value) {
    return value === null || value === undefined ? LABELS.notCollected : formatAudioDuration(value);
}

/** Pure: the route's aggregate body to a display model, or null when the shape is wrong. */
export function buildStatisticsView(data) {
    if (!data || !Array.isArray(data.buckets) || !Array.isArray(data.group_by) || typeof data.zone !== 'string') return null;
    const columns = data.group_by.filter((g) => GROUP_COLUMNS[g]).map((g) => ({ key: GROUP_COLUMNS[g][0], label: GROUP_COLUMNS[g][1] }));
    const basis = TIME_BASES.find((b) => b.value === data.time_basis);
    return {
        provenance: {
            timeBasis: basis ? basis.label : String(data.time_basis),
            zone: data.zone,
            window: data.window || null,
            start: data.start || null,
            end: data.end || null,
            asOf: data.as_of || null,
            excluded: Number.isInteger(data.excluded_missing_timestamp) ? data.excluded_missing_timestamp : 0,
        },
        columns,
        rows: data.buckets.map((b, i) => ({
            key: i,
            cells: columns.map((c) => groupValue(c.key, b[c.key])),
            count: b.count,
            total: seconds(b.total_seconds),
            average: seconds(b.average_seconds),
            known: b.known_duration_count,
            incomplete: b.incomplete === true,
            stateLabel: b.incomplete === true ? LABELS.incomplete : LABELS.complete,
            surface: b.incomplete === true ? 'warning' : 'normal',
        })),
    };
}

/**
 * Framework-free controller: account scope, statistics request and dictation id.
 * @param {{client: object, currentUser: object, makeState?: (o: object) => object}} deps
 */
export function createActivityController(deps) {
    const client = deps.client;
    const make = deps.makeState || ((o) => o);
    const state = make({
        userId: null, denied: true, epoch: 0,
        form: { timeBasis: 'created', from: '', to: '', grain: '', groupBy: [] },
        status: 'idle', message: '', view: null,
        dictationInput: '', dictationId: null, historyMessage: '',
    });
    let seq = 0;
    let disposed = false;

    function reset() {
        seq += 1;
        state.status = 'idle';
        state.message = '';
        state.view = null;
        state.dictationInput = '';
        state.dictationId = null;
        state.historyMessage = '';
        state.form = { timeBasis: 'created', from: '', to: '', grain: '', groupBy: [] };
    }

    function setUser(user) {
        if (disposed) return;
        const id = userIdOf(user);
        if (id === state.userId) return;
        state.userId = id;
        state.denied = id === null;
        state.epoch += 1;
        reset();
        client.switchContext({ accountId: id, dictationId: null });
    }

    async function load() {
        if (disposed || state.denied) return null;
        const built = buildStatisticsQuery(state.form);
        if (built.error) {
            state.status = 'invalid';
            state.message = built.error;
            state.view = null;
            return null;
        }
        const mine = (seq += 1);
        state.status = 'loading';
        state.message = '';
        const res = await client.request(STATISTICS_PATH + '?' + built.query);
        if (mine !== seq || res.outcome === 'cancelled') return res;
        // Never keep figures after a refusal or a malformed response.
        state.view = null;
        if (res.outcome === 'ok') {
            const view = buildStatisticsView(res.data);
            if (!view) {
                state.status = 'error';
                state.message = LABELS.failed;
            } else {
                state.view = view;
                state.status = view.rows.length ? 'ready' : 'empty';
            }
        } else if (res.outcome === 'permission') {
            state.status = 'denied';
            state.message = LABELS.denied;
        } else if (res.outcome === 'conflict') {
            state.status = 'stale';
            state.message = LABELS.stale;
        } else {
            state.status = 'error';
            state.message = LABELS.failed;
        }
        return res;
    }

    function toggleGroup(value) {
        const at = state.form.groupBy.indexOf(value);
        state.form.groupBy = at === -1 ? state.form.groupBy.concat(value) : state.form.groupBy.filter((v) => v !== value);
    }

    // Accepts a canonical UUID only; the history route refuses anything else.
    function openHistory(text) {
        if (disposed || state.denied) return false;
        const id = String(text === undefined ? state.dictationInput : text).trim().toLowerCase();
        if (!UUID.test(id)) {
            state.dictationId = null;
            state.historyMessage = LABELS.historyInvalid;
            return false;
        }
        state.historyMessage = '';
        state.dictationId = id;
        client.switchContext({ accountId: state.userId, dictationId: id });
        return true;
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        client.switchContext({ accountId: null, dictationId: null });
    }

    setUser(deps.currentUser);
    return { state, setUser, load, toggleGroup, openHistory, dispose };
}

function surfaceStyle(name) {
    const s = surfaceFor(name);
    return { background: s.background, color: s.color, borderColor: s.border };
}

export const ACTIVITY_TEMPLATE = `
<div class="${V8_PAGE} clinic-activity-view">
  <p v-if="state.denied" class="note warning" role="alert">${LABELS.deniedUser}</p>
  <template v-else>
    <div class="pagehead">
      <div>
        <div class="eyebrow">{{ historyOnly ? '${LABELS.eyebrowScoped}' : '${LABELS.eyebrow}' }}</div>
        <h1>{{ historyOnly ? '${LABELS.historyOnlyTitle}' : '${LABELS.title}' }}</h1>
        <p class="subline">{{ historyOnly ? '${LABELS.sublineScoped}' : '${LABELS.subline}' }}</p>
      </div>
    </div>
    <div class="stack">
    <section role="region" aria-label="${LABELS.historyTitle}" data-testid="history-section" class="panel [&_.clinic-history>h2]:sr-only [&_ol]:m-0 [&_ol]:p-0 [&_ol]:list-none [&_ol]:grid [&_ol]:gap-3 [&_li]:rounded-lg [&_li]:p-4 [&_li]:border [&_li]:text-xs [&_li]:leading-relaxed [&_dl]:grid [&_dl]:grid-cols-[max-content_1fr] [&_dl]:gap-x-4 [&_dl]:gap-y-1 [&_dl]:mt-2 [&_dl]:mb-0 [&_dt]:text-[var(--muted)] [&_dd]:m-0 [&_dd]:break-all [&_nav]:flex [&_nav]:items-center [&_nav]:gap-3 [&_nav]:mt-4 [&_nav]:text-xs">
      <h2 v-if="!historyOnly">${LABELS.historyTitle}</h2>
      <form v-if="!historyOnly" class="filterbar" @submit.prevent="openHistory()">
        <div class="field search">
          <label for="act-dictation">${LABELS.dictationId}</label>
          <input id="act-dictation" type="text" autocomplete="off" spellcheck="false" v-model="state.dictationInput" />
        </div>
        <button type="submit" class="primary" aria-label="${LABELS.showHistory}">${LABELS.showHistory}</button>
      </form>
      <p v-if="state.historyMessage" class="note warning" role="alert">{{ state.historyMessage }}</p>
      <component v-if="state.dictationId" :is="historyComponent" :client="client" :dictation-id="state.dictationId"
        :key="state.epoch + ':' + state.dictationId"></component>
      <p v-else-if="!historyOnly && !state.historyMessage" class="empty">${LABELS.historyEmpty}</p>
    </section>
    <section v-if="!historyOnly" role="region" aria-label="${LABELS.statsTitle}" data-testid="statistics-section" class="panel" :aria-busy="state.status === 'loading'">
      <h2>${LABELS.statsTitle}</h2>
      <form @submit.prevent="load()">
        <div class="filterbar">
          <div class="field">
            <label for="act-basis">${LABELS.timeBasis}</label>
            <select id="act-basis" v-model="state.form.timeBasis">
              <option v-for="b in timeBases" :key="b.value" :value="b.value">{{ b.label }}</option>
            </select>
          </div>
          <div class="field">
            <label for="act-from">${LABELS.from}</label>
            <input id="act-from" type="date" v-model="state.form.from" />
          </div>
          <div class="field">
            <label for="act-to">${LABELS.to}</label>
            <input id="act-to" type="date" v-model="state.form.to" />
          </div>
          <div class="field">
            <label for="act-grain">${LABELS.grain}</label>
            <select id="act-grain" v-model="state.form.grain">
              <option value="">-</option>
              <option v-for="g in grains" :key="g.value" :value="g.value">{{ g.label }}</option>
            </select>
          </div>
        </div>
        <fieldset class="field" style="border: 0; padding: 0; min-width: 0">
          <legend class="visually-hidden">${LABELS.groupBy}</legend>
          <span>${LABELS.groupBy}</span>
          <div class="chiprow">
            <label v-for="g in groups" :key="g.value" class="inline-label">
              <input type="checkbox" :checked="state.form.groupBy.includes(g.value)" @change="toggleGroup(g.value)" /> {{ g.label }}
            </label>
          </div>
        </fieldset>
        <div class="actions">
          <button type="submit" class="primary" aria-label="${LABELS.load}">${LABELS.load}</button>
        </div>
      </form>
      <p v-if="state.status === 'loading'" class="result-count" role="status" style="margin-top: 14px">${LABELS.loading}</p>
      <p v-else-if="state.status === 'invalid'" class="note warning" role="alert">{{ state.message }}</p>
      <div v-else-if="state.status === 'denied' || state.status === 'stale' || state.status === 'error'" class="note warning" role="alert">
        <p>{{ state.message }}</p>
        <button v-if="state.status !== 'denied'" type="button" class="small" aria-label="${LABELS.retry}" @click="load()">${LABELS.retry}</button>
      </div>
      <p v-else-if="state.status === 'empty'" class="empty" role="status" style="margin-top: 14px">${LABELS.empty}</p>
      <template v-if="state.view && (state.status === 'ready' || state.status === 'empty')">
        <dl class="metadata" aria-label="${LABELS.provenance}" style="margin-top: 18px">
          <div><dt>${LABELS.timeBasis}</dt><dd>{{ state.view.provenance.timeBasis }}</dd></div>
          <div><dt>${LABELS.zone}</dt><dd>{{ state.view.provenance.zone }}</dd></div>
          <div><dt>${LABELS.window}</dt><dd>{{ state.view.provenance.window }} {{ state.view.provenance.start }} - {{ state.view.provenance.end }}</dd></div>
          <div><dt>${LABELS.asOf}</dt><dd>{{ state.view.provenance.asOf }}</dd></div>
          <div><dt>${LABELS.excluded}</dt><dd>{{ state.view.provenance.excluded }}</dd></div>
        </dl>
        <div v-if="state.status === 'ready'" class="table-wrap">
          <table aria-label="${LABELS.statsTitle}">
            <thead><tr>
              <th v-for="c in state.view.columns" :key="c.key" scope="col">{{ c.label }}</th>
              <th scope="col">${LABELS.count}</th><th scope="col">${LABELS.total}</th><th scope="col">${LABELS.average}</th>
              <th scope="col">${LABELS.known}</th><th scope="col">${LABELS.state}</th>
            </tr></thead>
            <tbody><tr v-for="r in state.view.rows" :key="r.key">
              <td v-for="(cell, n) in r.cells" :key="n">{{ cell }}</td>
              <td>{{ r.count }}</td><td>{{ r.total }}</td><td>{{ r.average }}</td><td>{{ r.known }}</td>
              <td><span class="tag dot" :class="r.surface === 'warning' ? 'wait' : ''">{{ r.stateLabel }}</span></td>
            </tr></tbody>
          </table>
        </div>
      </template>
      <p class="hint" style="margin: 14px 0 0">${LABELS.noExport}</p>
    </section>
    </div>
  </template>
</div>`;

export const ActivityView = {
    name: 'ClinicActivityView',
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, default: null },
        dictationId: { type: String, default: '' },
        vue: { type: Object, default: null },
    },
    template: ACTIVITY_TEMPLATE,
    setup(props) {
        const Vue = props.vue || globalThis.Vue;
        const ctrl = createActivityController({
            client: props.client,
            currentUser: props.currentUser,
            makeState: (o) => Vue.reactive(o),
        });
        const historyComponent = Vue.markRaw(createHistoryComponent(Vue));
        Vue.watch(() => userIdOf(props.currentUser), () => ctrl.setUser(props.currentUser));
        // History-only mode: with a dictation id the view shows that dictation's history and no account statistics.
        const historyOnly = Vue.computed(() => Boolean(props.dictationId));
        Vue.watch(() => props.dictationId, (id) => { if (id) ctrl.openHistory(id); });
        if (props.dictationId) ctrl.openHistory(props.dictationId);
        Vue.onBeforeUnmount(() => ctrl.dispose());
        return {
            state: ctrl.state,
            client: props.client,
            historyOnly,
            load: () => (historyOnly.value ? null : ctrl.load()),
            toggleGroup: ctrl.toggleGroup,
            openHistory: ctrl.openHistory,
            historyComponent,
            timeBases: TIME_BASES,
            grains: GRAINS,
            groups: GROUPS,
            errorStyle: surfaceStyle('error'),
            rowStyle: surfaceStyle,
        };
    },
};

export function registerActivityView(app) {
    app.component('clinic-activity-view', ActivityView);
}