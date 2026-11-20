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
        const onScroll = (event) => c.setScroll(event && event.target ? event.target.scrollTop : 0);
        if (typeof V.onMounted === 'function' && props.autoStart) {
            V.onMounted(async () => {
                await c.start();
                if (typeof V.nextTick === 'function') await V.nextTick();
                if (scroller.value) scroller.value.scrollTop = c.scrollTop();
            });
        }
        if (typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(() => c.dispose());
        return { c, scroller, onScroll, t: TEXT };
    },
    template: `
<section class="clinic-recordings-view w-full" data-testid="clinic-recordings-view" aria-labelledby="crv-title">
  <div class="flex flex-wrap items-center gap-2">
    <h1 id="crv-title" class="text-lg font-semibold">{{ t.title }}</h1>
    <button type="button" class="btn px-3 py-2 border rounded" style="min-height: 2.75rem"
            :disabled="c.state.loading" @click="c.load()">{{ t.refresh }}</button>
  </div>
  <form class="flex flex-wrap items-end gap-2 mt-3" role="search" :aria-label="t.filters" data-testid="filters"
        @submit.prevent="c.applyFilters()">
    <label class="flex flex-col text-sm">{{ t.authorId }}
      <input type="text" inputmode="numeric" class="px-2 py-2 border rounded" data-testid="filter-author" v-model="c.state.draft.author_id">
    </label>
    <label class="flex flex-col text-sm">{{ t.poolId }}
      <input type="text" inputmode="numeric" class="px-2 py-2 border rounded" data-testid="filter-pool" v-model="c.state.draft.pool_id">
    </label>
    <label class="flex flex-col text-sm">{{ t.status }}
      <select class="px-2 py-2 border rounded" data-testid="filter-status" v-model="c.state.draft.status">
        <option value="">{{ t.statusAny }}</option>
        <option v-for="(label, key) in c.statusOptions" :key="key" :value="key">{{ label }}</option>
      </select>
    </label>
    <label class="flex flex-col text-sm">{{ t.from }}
      <input type="date" class="px-2 py-2 border rounded" data-testid="filter-from" v-model="c.state.draft.from">
    </label>
    <label class="flex flex-col text-sm">{{ t.to }}
      <input type="date" class="px-2 py-2 border rounded" data-testid="filter-to" v-model="c.state.draft.to">
    </label>
    <button type="submit" class="btn px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="apply">{{ t.apply }}</button>
    <button type="button" class="btn px-3 py-2 border rounded" style="min-height: 2.75rem" data-testid="reset"
            @click="c.resetFilters()">{{ t.reset }}</button>
  </form>
  <div v-if="c.state.denied" role="alert" class="mt-3" data-testid="denied-state">{{ t.denied }}</div>
  <template v-else>
    <div v-if="c.state.error" role="alert" class="mt-3" data-testid="load-error">
      <span v-if="c.state.stale">{{ t.stale }} </span>{{ c.state.error }}
      <button type="button" class="btn px-3 py-2 border rounded" style="min-height: 2.75rem" @click="c.load()">{{ t.retry }}</button>
    </div>
    <div v-if="c.state.loading" role="status" aria-live="polite" class="mt-3" data-testid="loading-state">{{ t.loading }}</div>
    <p v-else-if="c.state.loaded && !c.state.rows.length && !c.state.error" class="mt-3" data-testid="empty-state">
      {{ c.hasFilters() ? t.noMatch : t.empty }}
    </p>
    <div v-if="c.state.rows.length" ref="scroller" class="mt-3 overflow-y-auto" style="max-height: 70vh" data-testid="scroller" @scroll="onScroll">
      <section v-for="g in c.groups()" :key="'d:' + g.day" class="mb-4" :aria-label="g.label">
        <h2 class="font-semibold py-2" data-testid="day-heading">{{ g.label }}</h2>
        <table class="w-full hidden md:table" data-testid="table">
          <thead>
            <tr>
              <th scope="col" class="text-left px-3 py-2">{{ t.time }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.author }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.pool }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.lease }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.status }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.duration }}</th>
              <th scope="col" class="text-left px-3 py-2">{{ t.caseId }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in g.rows" :key="r.key" data-row :data-id="r.dictationId">
              <td class="px-3 py-2">
                <a href="#" data-testid="open-link" :aria-label="t.open + ': ' + r.time + ', ' + r.author" @click.prevent="c.open(r)">{{ r.time }}</a>
                <span v-if="r.isFallback" class="text-xs ml-1" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip">{{ r.fallbackLabel }}</span>
              </td>
              <td class="px-3 py-2" data-cell="author">{{ r.author }}</td>
              <td class="px-3 py-2" data-cell="pool">{{ r.poolText }}</td>
              <td class="px-3 py-2" data-cell="lease">{{ r.leaseHolder }}</td>
              <td class="px-3 py-2"><span class="px-2 py-1 rounded text-xs" :style="r.statusStyle" :title="r.processing">{{ r.status }}</span></td>
              <td class="px-3 py-2">{{ r.duration }}</td>
              <td class="px-3 py-2" data-cell="case">{{ r.caseDisplay }}</td>
            </tr>
          </tbody>
        </table>
        <ul class="md:hidden list-none p-0 m-0" data-testid="cards">
          <li v-for="r in g.rows" :key="'c:' + r.key" class="border rounded p-3 mb-2" data-card :data-id="r.dictationId">
            <a href="#" data-testid="open-link" :aria-label="t.open + ': ' + r.time + ', ' + r.author" @click.prevent="c.open(r)">{{ r.time }}</a>
            <span v-if="r.isFallback" class="text-xs ml-1" data-testid="fallback" :title="r.fallbackTip" :aria-label="r.fallbackTip">{{ r.fallbackLabel }}</span>
            <dl class="mt-1 break-words">
              <div><dt class="inline font-semibold">{{ t.author }}: </dt><dd class="inline">{{ r.author }}</dd></div>
              <div><dt class="inline font-semibold">{{ t.pool }}: </dt><dd class="inline">{{ r.poolText }}</dd></div>
              <div><dt class="inline font-semibold">{{ t.lease }}: </dt><dd class="inline">{{ r.leaseHolder }}</dd></div>
              <div v-if="r.caseDisplay"><dt class="inline font-semibold">{{ t.caseId }}: </dt><dd class="inline">{{ r.caseDisplay }}</dd></div>
              <div><dt class="inline font-semibold">{{ t.duration }}: </dt><dd class="inline">{{ r.duration }}</dd></div>
            </dl>
            <span class="px-2 py-1 rounded text-xs" :style="r.statusStyle">{{ r.status }}</span>
          </li>
        </ul>
      </section>
      <button v-if="c.state.nextCursor" type="button" class="btn px-3 py-2 border rounded" style="min-height: 2.75rem"
              data-testid="load-more" :disabled="c.state.loadingMore" @click="c.loadMore()">
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
