/**
 * Composed clinic recordings view (V8 P01 Aufnahmen): one responsive list of
 * record rows in day groups (the grid of .recording-row changes at the
 * prototype breakpoints). The server filters and sorts; only the free-text
 * search runs client-side over the loaded rows. Mounting into the page and the shared api-client
 * come from the page (package v8-03). Opening a row emits `open` with
 * { dictationId, mode }, the payload of the worklist.
 *
 * Owner decision W16: no patient name, free-text title or notes are read or shown; the
 * case is referenced by Fall-ID (case_display) when the server supplies it.
 * Colours come from tokens.js. Nothing is written to browser storage or URLs.
 */

import { PERIODS, RECORDINGS_PAGE_SIZE, TEXT, createRecordingsController, formatDay } from './recordings-ui.js';

export const COMPOSE_RECORDINGS_VERSION = 1;
export { RECORDINGS_PAGE_SIZE, createRecordingsController };

/** Use the list-approved case display, capture label or timestamp and its provenance. */
export function recordingLabel(row) {
    if (row.caseDisplay || row.captureLabel) return row.heading;
    const provenance = row.fallbackText || (row.isFallback ? 'Diktat' : 'Aufnahme');
    return provenance + ' · ' + formatDay(row.day) + (row.datetime ? ', ' + row.time : '');
}

export const RecordingsView = {
    name: 'ClinicRecordingsView',
    emits: ['open'],
    props: {
        client: { type: Object, required: true },
        currentUser: { type: Object, required: true },
        vue: { type: Object, default: null },
        memory: { type: Object, default: null },
        autoStart: { type: Boolean, default: true },
    },
    setup(props, ctx) {
        const c = createRecordingsController({
            client: props.client,
            currentUser: props.currentUser,
            vue: props.vue || undefined,
            memory: props.memory || undefined,
            onOpen: (payload) => { if (ctx && typeof ctx.emit === 'function') ctx.emit('open', payload); },
        });
        const V = props.vue || (typeof window !== 'undefined' ? window.Vue : null);
        if (!V) throw new Error('Vue is required to mount the recordings view');
        const scroller = V.ref(null);
        const root = V.ref(null);
        const filtersOpen = V.ref(true);
        const onScroll = (event) => c.setScroll(event && event.target ? event.target.scrollTop : 0);
        // The V8 shell scrolls its content area (or the document), not the list: find the element that really scrolls.
        let host = null;
        const isWindow = (h) => typeof window !== 'undefined' && h === window;
        const hostScrollTop = () => (isWindow(host) ? window.scrollY : host.scrollTop);
        const onHostScroll = () => c.setScroll(hostScrollTop());
        const findHost = () => {
            const start = root.value;
            if (start && typeof getComputedStyle === 'function') {
                for (let n = start.parentElement; n; n = n.parentElement) {
                    const oy = getComputedStyle(n).overflowY;
                    if (oy === 'auto' || oy === 'scroll') return n;
                }
                return typeof window !== 'undefined' ? window : null;
            }
            return scroller.value || null;
        };
        const restoreScroll = () => {
            if (!host) return;
            if (isWindow(host)) window.scrollTo(0, c.scrollTop());
            else host.scrollTop = c.scrollTop();
        };
        const active = V.ref(false);
        const pageVisible = V.ref(typeof document !== 'undefined' && !document.hidden);
        const visibilityChanged = () => { pageVisible.value = !document.hidden; };
        const shouldPoll = () => active.value && pageVisible.value && !c.state.denied
            && c.visibleRows().some(row => row.processingActive);
        if (props.autoStart && typeof V.watch === 'function') {
            let pending = false;
            V.watch(shouldPoll, (enabled, previous, onCleanup) => {
                if (!enabled) return;
                const timer = setInterval(async () => {
                    if (pending || !shouldPoll()) return;
                    pending = true;
                    try { await c.refreshIfChanged(shouldPoll); }
                    finally { pending = false; }
                }, 5000); // Same cadence as the active job queue.
                onCleanup(() => clearInterval(timer));
            });
        }
        if (typeof V.onMounted === 'function' && props.autoStart) {
            V.onMounted(async () => {
                active.value = true;
                if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibilityChanged);
                await c.start();
                if (typeof V.nextTick === 'function') await V.nextTick();
                host = findHost();
                if (host && typeof host.addEventListener === 'function') host.addEventListener('scroll', onHostScroll, { passive: true });
                restoreScroll();
            });
        }
        if (typeof V.onActivated === 'function' && props.autoStart) {
            V.onActivated(async () => {
                active.value = true;
                if (await c.refreshIfChanged()) {
                    await V.nextTick();
                    restoreScroll();
                }
            });
        }
        if (typeof V.onDeactivated === 'function') V.onDeactivated(() => { active.value = false; });
        if (typeof V.onBeforeUnmount === 'function') {
            V.onBeforeUnmount(() => {
                active.value = false;
                if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibilityChanged);
                if (host && typeof host.removeEventListener === 'function') host.removeEventListener('scroll', onHostScroll);
                c.dispose();
            });
        }
        function rowContext(id) {
            const row = c.state.rows.find((entry) => entry.dictationId === id);
            return row && { dictator: row.author, pool: row.poolText };
        }
        return { c, rowContext, recordingLabel, scroller, root, onScroll, filtersOpen, t: TEXT, periods: PERIODS };
    },
    template: `
<section ref="root" class="clinic-recordings-view cl-view content-inner v8-page" data-testid="clinic-recordings-view" aria-labelledby="crv-title">
  <div class="page-head recordings-head">
    <div>
      <div class="eyebrow">{{ t.eyebrow }}</div>
      <div class="hline"><h1 id="crv-title" class="cl-title" tabindex="-1">{{ t.title }}</h1><span v-if="c.state.loaded" class="count-pill" data-testid="total-count">{{ c.totalLabel() }}</span></div>
      <p class="subtitle">{{ t.subtitle }}</p>
    </div>
    <div class="page-actions">
      <button type="button" class="btn btn--ghost btn--icon" :aria-label="t.refresh" :title="t.refresh"
              :disabled="c.state.loading" @click="c.load()"><i class="fa-solid fa-rotate" :class="c.state.loading ? 'cl-spin' : ''" aria-hidden="true"></i></button>
      <button type="button" class="btn btn--ghost md:hidden" data-testid="filters-toggle" aria-controls="crv-role-filters"
              :aria-expanded="filtersOpen ? 'true' : 'false'" @click="filtersOpen = !filtersOpen">
        <i class="fa-solid fa-sliders" aria-hidden="true"></i>{{ t.roleFilters }}
        <span v-if="c.activeFilterCount()" class="count-pill"><span class="cl-sr-only">{{ t.activeFilters }}: </span>{{ c.activeFilterCount() }}</span>
      </button>
    </div>
  </div>
  <!-- Filter card (prototype record-toolbar). The server filters by author, pool, status and period; the search runs over the loaded rows. -->
  <form id="crv-filters" role="search" :aria-label="t.filters" data-testid="filters"
        @submit.prevent="c.hasDraftChanges() && c.applyFilters()">
    <div class="record-toolbar">
      <label class="record-search" :title="t.loadedOnly">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        <input type="search" style="border: 0; padding: 0; min-height: 0; height: auto; background: transparent; box-shadow: none; font-size: 13px" data-testid="filter-search" :aria-label="t.searchLabel" :placeholder="t.searchPlaceholder" autocomplete="off" v-model="c.state.query">
      </label>
      <select class="record-select" style="appearance: auto; width: auto; font-size: 12px; cursor: default"
              data-testid="filter-scope" disabled :aria-label="t.scopeLabel" :title="t.scopeHint">
        <option value="">{{ t.scopeAll }}</option>
      </select>
      <select class="record-select" style="appearance: auto; width: auto; font-size: 12px" data-testid="filter-status" :aria-label="t.statusLabel" v-model="c.state.draft.status" @change="c.applyFilters()">
        <option value="">{{ t.statusAny }}</option>
        <option v-for="(label, key) in c.statusOptions" :key="key" :value="key">{{ label }}</option>
      </select>
      <select class="record-select" style="appearance: auto; width: auto; font-size: 12px" data-testid="filter-period" :aria-label="t.periodLabel" :value="c.picker.period"
              @change="c.setPeriod($event.target.value); if ($event.target.value !== 'custom') c.applyFilters()">
        <option v-for="p in periods" :key="p.key" :value="p.key">{{ p.label }}</option>
      </select>
    </div>
    <div id="crv-role-filters" class="record-role-filters" :data-collapsed="!filtersOpen">
      <label style="position: relative"><span>{{ t.poolId }}</span>
        <input type="text" class="record-select" style="font-size: 12px; border-color: var(--line)" data-testid="filter-pool" list="crv-pools" autocomplete="off"
               :placeholder="t.poolAny" :value="c.picker.pool" @input="c.pick('pool', $event.target.value)">
        <svg style="position: absolute; right: 11px; bottom: 14px; pointer-events: none" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        <datalist id="crv-pools"><option v-for="o in c.options.pools" :key="o.value" :value="o.label"></option></datalist>
      </label>
      <label style="position: relative"><span>{{ t.authorId }}</span>
        <input type="text" class="record-select" style="font-size: 12px; border-color: var(--line)" data-testid="filter-author" list="crv-authors" autocomplete="off"
               :placeholder="t.authorAny" :value="c.picker.author" @input="c.pick('author', $event.target.value)">
        <svg style="position: absolute; right: 11px; bottom: 14px; pointer-events: none" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        <datalist id="crv-authors"><option v-for="o in c.options.authors" :key="o.value" :value="o.label"></option></datalist>
      </label>
    </div>
    <div v-if="c.canReset() || c.hasDraftChanges()" class="record-role-filters">
      <template v-if="c.picker.period === 'custom'">
        <label><span>{{ t.from }}</span>
          <input type="date" class="record-select" style="font-size: 12px" data-testid="filter-from" v-model="c.state.draft.from">
        </label>
        <label><span>{{ t.to }}</span>
          <input type="date" class="record-select" style="font-size: 12px" data-testid="filter-to" v-model="c.state.draft.to">
        </label>
      </template>
      <div class="page-actions">
        <button v-if="c.hasDraftChanges()" type="submit" class="btn btn--primary primary" style="font-size: 12px; min-height: 40px" data-testid="apply">{{ t.apply }}</button>
        <button v-if="c.canReset()" type="button" class="btn btn--ghost" style="font-size: 12px; min-height: 40px" data-testid="reset" @click="c.resetFilters()">{{ t.reset }}</button>
      </div>
    </div>
  </form>
  <div v-if="c.state.denied" role="alert" class="cl-alert" data-testid="denied-state">{{ t.denied }}</div>
  <template v-else>
    <div v-if="c.state.error" role="alert" class="cl-alert" :style="c.errorStyle" data-testid="load-error">
      <span><span v-if="c.state.stale">{{ t.stale }} </span>{{ c.state.error }}</span>
      <button type="button" class="btn btn--ghost btn--sm" @click="c.load()">{{ t.retry }}</button>
    </div>
    <div v-if="c.state.loaded && !c.state.error" class="record-list-meta">
      <p role="status" aria-live="polite" data-testid="result-count">{{ c.countLabel() }}</p>
      <div class="record-order"><span class="sort-label">{{ t.sortLabel }}</span>
        <select class="record-select sort-select" style="appearance: auto; width: auto; cursor: default" data-testid="sort-select" disabled :aria-label="t.sortSelect" :title="t.sortHint"><option value="newest">{{ t.sortNewest }}</option></select></div>
    </div>
    <p v-if="c.searchPartial()" class="sort-label" role="status" data-testid="search-hint">{{ t.searchLoadedHint }}</p>
    <div v-if="c.state.loading" class="record-empty" role="status" aria-live="polite" data-testid="loading-state">
      <i class="fa-solid fa-spinner cl-spin" aria-hidden="true"></i>
      <p>{{ t.loading }}</p>
    </div>
    <div v-else-if="c.state.loaded && !c.visibleRows().length && !c.state.error" class="record-empty" data-testid="empty-state">
      <div class="empty-icon" aria-hidden="true">◉</div>
      <h2 class="font-bold">{{ c.searchPartial() ? t.noMatchLoaded : (c.hasFilters() || c.hasQuery() ? t.noMatch : t.empty) }}</h2>
      <p>{{ c.searchPartial() ? t.searchLoadedHint : (c.hasFilters() || c.hasQuery() ? t.noMatchHint : t.emptyHint) }}</p>
      <button v-if="c.hasFilters() || c.hasQuery()" type="button" class="primary" @click="c.resetFilters()">{{ t.resetFilters }}</button>
    </div>
    <!-- One responsive list for desktop and phone: the grid of .recording-row changes at the prototype breakpoints.
         The former copies (table "hidden md:table" and cards) are gone; the role cells keep their data-cell markers. -->
    <div v-if="c.visibleRows().length" ref="scroller" class="record-timeline" data-testid="scroller">
      <section v-for="g in c.groups()" :key="'d:' + g.day" class="date-group" :aria-label="g.caption ? g.title + ', ' + g.caption : g.title">
        <div class="date-heading">
          <h2 data-testid="day-heading">{{ g.title }}</h2>
          <span v-if="g.caption" class="date-caption">{{ g.caption }}</span>
          <span class="group-count">{{ g.countText }}</span>
        </div>
        <div class="record-group-list" data-testid="cards">
          <div v-for="r in g.rows" :key="r.key" class="cl-reference-row">
          <button type="button" class="recording-row" data-row data-card :data-id="r.dictationId" data-testid="open-link"
             :disabled="!r.dictationId" :aria-label="r.ariaLabel" :aria-describedby="'crv-next-' + r.key + (r.processing ? ' crv-processing-' + r.key : '')" @click.prevent="c.open(r)">
            <time class="record-time" :datetime="r.datetime || null">{{ r.time }}</time>
            <span class="record-icon" aria-hidden="true">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h10l4 4v14H5zM15 3v6h5M9 12h6M9 16h6"/></svg>
            </span>
            <span class="record-text">
              <span class="record-title">{{ recordingLabel(r) }}</span>
              <span v-if="r.ownTitle" class="record-subtitle" data-testid="own-title">Titel: {{ r.ownTitle }}</span>
              <span class="record-roles">
                <span class="record-role"><span class="record-role-label">{{ t.author }}:</span> <span class="dictator-name" data-cell="author" :title="r.author">{{ r.author }}</span></span>
                <span class="record-role"><span class="record-role-label">{{ t.pool }}:</span> <span class="pool-name" data-cell="pool" :title="r.poolText">{{ r.poolText }}</span></span>
                <span v-if="r.hasLease" class="record-role"><span class="record-role-label">{{ t.lease }}:</span> <span class="dictator-name" data-cell="lease">{{ r.leaseHolder }}</span></span>
              </span>
              <span class="record-subtitle" :class="r.isFallback ? 'date-fallback' : ''"><span>Diktat {{ r.reference }}</span><span v-if="r.fallbackText" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip"> · {{ r.fallbackText }}</span><span v-else-if="r.isFallback" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip"> · {{ r.fallbackLabel }}</span></span>
              <span v-if="r.processing" :id="'crv-processing-' + r.key" class="cl-processing" data-testid="recording-text-state">{{ r.processing }}</span>
            </span>
            <span class="status" :id="'crv-next-' + r.key" :class="r.needsAction ? 'wartet' : r.statusClass"
                   data-testid="recording-next-step" :data-action-needed="r.needsAction ? 'true' : 'false'"><span>{{ r.nextStep }}</span></span>
            <span class="record-source" hidden></span>
            <span class="record-duration">{{ r.duration }}</span>
            <span class="record-chevron" aria-hidden="true">›</span>
          </button>
          <details v-if="r.dictationId" class="cl-id-disclosure" @keydown.esc.stop.prevent="$event.currentTarget.open = false; $event.currentTarget.querySelector('summary').focus()">
            <summary :aria-label="'Kennung anzeigen: ' + r.heading + ' ' + r.reference"><i class="fa-solid fa-circle-info" aria-hidden="true"></i></summary>
            <label class="cl-id-value">Vollständige Diktatkennung
              <textarea readonly rows="2" :value="r.dictationId" @focus="$event.target.select()" aria-label="Vollständige Diktatkennung"></textarea>
              <span>Zum Kopieren markieren.</span>
            </label>
          </details>
          </div>
        </div>
      </section>
    </div>
    <div v-if="c.state.loaded && c.state.nextCursor" class="cl-more">
      <button type="button" class="btn btn--ghost" data-testid="load-more" :disabled="c.state.loadingMore" @click="c.loadMore()">
        {{ c.state.loadingMore ? t.loadingMore : t.loadMore }}
      </button>
    </div>
  </template>
</section>`,
};

export function registerRecordingsView(app) {
    app.component('clinic-recordings-view', RecordingsView);
    return app;
}
