/**
 * Composed clinic recordings view (V8 P01 Aufnahmen): full-width chronological
 * list in day groups. Desktop shows a table, a phone (below 768 px) cards of
 * the same rows from the same server query; there is no client-side
 * re-filtering or re-sorting. Mounting into the page and the shared api-client
 * come from the page (package v8-03). Opening a row emits `open` with
 * { dictationId, mode }, the payload of the worklist.
 *
 * Owner decision W16: no patient name, title or notes are read or shown; the
 * case is referenced by Fall-ID (case_display) when the server supplies it.
 * Colours come from tokens.js. Nothing is written to browser storage or URLs.
 */

import { RECORDINGS_PAGE_SIZE, TEXT, createRecordingsController } from './recordings-ui.js';

export const COMPOSE_RECORDINGS_VERSION = 1;
export { RECORDINGS_PAGE_SIZE, createRecordingsController };

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
        const filtersOpen = V.ref(true); // open by default; the phone layout lets the user collapse it
        const onScroll = (event) => c.setScroll(event && event.target ? event.target.scrollTop : 0);
        if (typeof V.onMounted === 'function' && props.autoStart) {
            V.onMounted(async () => {
                await c.start();
                if (typeof V.nextTick === 'function') await V.nextTick();
                if (scroller.value) scroller.value.scrollTop = c.scrollTop();
            });
        }
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        function rowContext(id) {
            const row = c.state.rows.find((entry) => entry.dictationId === id);
            return row && { dictator: row.author, pool: row.poolText };
        }
        return { c, scroller, onScroll, filtersOpen, t: TEXT, rowContext };
    },
    template: `
<section class="clinic-recordings-view cl-view" data-testid="clinic-recordings-view" aria-labelledby="crv-title">
  <div class="cl-head">
    <h1 id="crv-title" class="cl-title">{{ t.title }}</h1>
    <span v-if="c.state.loaded" class="cl-sub" data-testid="result-count">{{ c.countLabel() }}</span>
    <span class="cl-toolbar__spacer" aria-hidden="true"></span>
    <button type="button" class="btn btn--ghost btn--icon" :aria-label="t.refresh" :title="t.refresh"
            :disabled="c.state.loading" @click="c.load()"><i class="fa-solid fa-rotate" :class="c.state.loading ? 'cl-spin' : ''" aria-hidden="true"></i></button>
    <button type="button" class="btn btn--ghost cl-filters-toggle" data-testid="filters-toggle" aria-controls="crv-filters"
            :aria-expanded="filtersOpen ? 'true' : 'false'" @click="filtersOpen = !filtersOpen">
      <i class="fa-solid fa-sliders" aria-hidden="true"></i>{{ t.filters }}
      <span v-if="c.activeFilterCount()" class="cl-badge"><span class="cl-sr-only">{{ t.activeFilters }}: </span>{{ c.activeFilterCount() }}</span>
    </button>
  </div>
  <form id="crv-filters" class="cl-filterbar" role="search" :aria-label="t.filters" data-testid="filters"
        :data-collapsed="filtersOpen ? 'false' : 'true'" @submit.prevent="c.applyFilters()">
    <label class="cl-label">{{ t.authorId }}
      <input type="text" class="field" data-testid="filter-author" list="crv-authors" autocomplete="off"
             :placeholder="t.authorAny" :value="c.picker.author" @input="c.pick('author', $event.target.value)">
      <datalist id="crv-authors"><option v-for="o in c.options.authors" :key="o.value" :value="o.label"></option></datalist>
    </label>
    <label class="cl-label">{{ t.poolId }}
      <input type="text" class="field" data-testid="filter-pool" list="crv-pools" autocomplete="off"
             :placeholder="t.poolAny" :value="c.picker.pool" @input="c.pick('pool', $event.target.value)">
      <datalist id="crv-pools"><option v-for="o in c.options.pools" :key="o.value" :value="o.label"></option></datalist>
    </label>
    <label class="cl-label">{{ t.status }}
      <select class="field" data-testid="filter-status" v-model="c.state.draft.status">
        <option value="">{{ t.statusAny }}</option>
        <option v-for="(label, key) in c.statusOptions" :key="key" :value="key">{{ label }}</option>
      </select>
    </label>
    <label class="cl-label">{{ t.from }}
      <input type="date" class="field" data-testid="filter-from" v-model="c.state.draft.from">
    </label>
    <label class="cl-label">{{ t.to }}
      <input type="date" class="field" data-testid="filter-to" v-model="c.state.draft.to">
    </label>
    <div class="cl-filterbar__actions">
      <button type="submit" class="btn btn--primary" data-testid="apply">{{ t.apply }}</button>
      <button type="button" class="btn btn--ghost" data-testid="reset" @click="c.resetFilters()">{{ t.reset }}</button>
    </div>
  </form>
  <div v-if="c.state.denied" role="alert" class="cl-alert" data-testid="denied-state">{{ t.denied }}</div>
  <template v-else>
    <div v-if="c.state.error" role="alert" class="cl-alert" :style="c.errorStyle" data-testid="load-error">
      <span><span v-if="c.state.stale">{{ t.stale }} </span>{{ c.state.error }}</span>
      <button type="button" class="btn btn--ghost btn--sm" @click="c.load()">{{ t.retry }}</button>
    </div>
    <div v-if="c.state.loading" class="cl-state" role="status" aria-live="polite" data-testid="loading-state">
      <span class="cl-state__icon"><i class="fa-solid fa-spinner cl-spin" aria-hidden="true"></i></span>
      <p class="cl-state__body">{{ t.loading }}</p>
    </div>
    <div v-else-if="c.state.loaded && !c.state.rows.length && !c.state.error" class="cl-state" data-testid="empty-state">
      <span class="cl-state__icon"><i class="fa-regular fa-folder-open" aria-hidden="true"></i></span>
      <p class="cl-state__title">{{ c.hasFilters() ? t.noMatch : t.empty }}</p>
      <button v-if="c.hasFilters()" type="button" class="btn btn--ghost" @click="c.resetFilters()">{{ t.reset }}</button>
    </div>
    <div v-if="c.state.rows.length" ref="scroller" class="cl-scroller" data-testid="scroller" @scroll="onScroll">
      <section v-for="(g, gi) in c.groups()" :key="'d:' + g.day" class="cl-day" :aria-label="g.label">
        <h2 class="cl-day__heading" data-testid="day-heading">{{ g.label }}<span class="cl-day__count">{{ g.rows.length }}</span></h2>
        <div class="cl-tablewrap hidden md:block">
          <table class="cl-table cl-table--fixed w-full hidden md:table" data-testid="table">
            <colgroup>
              <col class="c-time"><col class="c-author"><col class="c-pool"><col class="c-lease"><col class="c-status"><col class="c-duration"><col class="c-case">
            </colgroup>
            <thead :class="gi > 0 ? 'cl-sr-only' : ''">
              <tr>
                <th scope="col">{{ t.time }}</th>
                <th scope="col">{{ t.author }}</th>
                <th scope="col">{{ t.pool }}</th>
                <th scope="col">{{ t.lease }}</th>
                <th scope="col">{{ t.status }}</th>
                <th scope="col" class="cl-num">{{ t.duration }}</th>
                <th scope="col">{{ t.caseId }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in g.rows" :key="r.key" class="cl-row" data-row :data-id="r.dictationId">
                <td class="cl-nowrap">
                  <a href="#" class="cl-link" data-testid="open-link" :aria-label="t.open + ': ' + r.time + ', ' + r.author" @click.prevent="c.open(r)">{{ r.time }}</a>
                  <span v-if="r.isFallback" class="cl-fallback" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip">{{ r.fallbackLabel }}</span>
                </td>
                <td data-cell="author" :title="r.author">{{ r.author }}</td>
                <td data-cell="pool" :title="r.poolText" :class="r.pools.length ? '' : 'cl-muted'">{{ r.poolText }}</td>
                <td data-cell="lease" :class="r.leaseHolder === '–' ? 'cl-muted' : ''">{{ r.leaseHolder }}</td>
                <td><span class="pill-status cl-pill" :style="r.statusStyle" :title="r.processing">{{ r.status }}</span></td>
                <td class="cl-num">{{ r.duration }}</td>
                <td data-cell="case">{{ r.caseDisplay }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul class="cl-cards md:hidden list-none p-0 m-0" data-testid="cards">
          <li v-for="r in g.rows" :key="'c:' + r.key" class="cl-card" data-card :data-id="r.dictationId">
            <div class="cl-card__top">
              <span>
                <a href="#" class="cl-link" data-testid="open-link" :aria-label="t.open + ': ' + r.time + ', ' + r.author" @click.prevent="c.open(r)">{{ r.time }}</a>
                <span v-if="r.isFallback" class="cl-fallback" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip">{{ r.fallbackLabel }}</span>
              </span>
              <span class="pill-status cl-pill" :style="r.statusStyle">{{ r.status }}</span>
            </div>
            <dl>
              <dt>{{ t.author }}</dt><dd>{{ r.author }}</dd>
              <dt>{{ t.pool }}</dt><dd>{{ r.poolText }}</dd>
              <dt>{{ t.lease }}</dt><dd>{{ r.leaseHolder }}</dd>
              <template v-if="r.caseDisplay"><dt>{{ t.caseId }}</dt><dd>{{ r.caseDisplay }}</dd></template>
              <dt>{{ t.duration }}</dt><dd>{{ r.duration }}</dd>
            </dl>
          </li>
        </ul>
      </section>
      <div v-if="c.state.nextCursor" class="cl-more">
        <button type="button" class="btn btn--ghost" data-testid="load-more" :disabled="c.state.loadingMore" @click="c.loadMore()">
          {{ c.state.loadingMore ? t.loadingMore : t.loadMore }}
        </button>
      </div>
    </div>
  </template>
</section>`,
};

export function registerRecordingsView(app) {
    app.component('clinic-recordings-view', RecordingsView);
    return app;
}
