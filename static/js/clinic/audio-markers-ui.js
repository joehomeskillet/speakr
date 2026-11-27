/**
 * Revision-bound audio markers for the correction view (PAR-COR-007, PAR-COR-009).
 *
 * Lists the SECTION / INDEX markers of one dictation and creates, moves, removes
 * them or marks a phrase of the text, only through the existing correction
 * endpoints (POST markers, markers/<id>/move, markers/<id>/delete). Every marker
 * belongs to the audio revision it was placed on. A marker of an older audio
 * revision stays visible as historical (read-only, its position refers to the
 * old timeline) and cannot be changed or navigated; new markers are only placed
 * on the audio revision the editor currently holds. Clinical text and labels stay
 * in memory only; nothing is written to browser storage. Colours come from
 * tokens.js, requests from the injected api-client.js client. A retry after an
 * unknown outcome reuses the idempotency key so it replays instead of duplicating.
 *
 * Exports (consumed by w212 compose, w286 compare):
 *   useAudioMarkers({ editor, alignment, client, dictationId, getPositionMs?, seek?, audioElement?, vue? })
 *     `editor` is the useCorrectionEditor() result of w149 (audio, lease, canEdit),
 *     `alignment` the useCorrectionAlignment() result of w150 (selection, links, status).
 *     `getPositionMs()` or `audioElement.currentTime` gives the play position.
 *     state refs  markers, status ('idle'|'loading'|'ready'|'error'), error, busy,
 *                 announcement
 *     methods     refresh(), create({ type?, positionMs?, label? }), move(id, positionMs),
 *                 remove(id), markPhrase(label?), goTo(id), dispose()
 *     `markers` entries: { id, type, typeLabel, positionMs, timeLabel, label, version,
 *                 audioRevisionId, anchorOffset, ownership ('current'|'historical'),
 *                 historical, events }. Results are { ok, code?, message, marker? };
 *     codes: readOnly, stale, historical, invalid, unaligned, noSeek, denied,
 *     notFound, locked, failed.
 *   AudioMarkers: Vue options object, props editor, alignment, client, dictationId,
 *     getPositionMs, seek, audioElement, vue.
 *   registerAudioMarkers(app): app.component('clinic-audio-markers', ...).
 *
 * Keyboard: every control is a native button (Tab, Enter, Space); moving uses
 * the buttons "Eine Sekunde früher/später" and "Zur Abspielposition".
 */

import { createClinicApiClient } from './api-client.js';
import { FOCUS_INDICATOR, surfaceFor } from './tokens.js';

export const LABELS = Object.freeze({
    title: 'Marken im Audio',
    list: 'Liste der Marken',
    section: 'Abschnittsmarke setzen',
    index: 'Indexmarke setzen',
    indexName: 'Name des Index',
    indexNamePlaceholder: 'z. B. Befund',
    markPhrase: 'Ausgewählte Textstelle markieren',
    goTo: 'Zur Marke springen',
    earlier: 'Eine Sekunde früher',
    later: 'Eine Sekunde später',
    toPlayhead: 'Zur Abspielposition',
    remove: 'Marke entfernen',
    retry: 'Erneut versuchen',
    refresh: 'Marken neu laden',
    empty: 'Keine Marken vorhanden.',
    loading: 'Marken werden geladen …',
    ready: 'Marken geladen.',
    current: 'Aktuelle Audio-Version',
    historical: 'Frühere Audio-Version, nur lesbar',
    SECTION: 'Abschnitt',
    INDEX: 'Index',
    created: 'Marke gesetzt.',
    moved: 'Marke verschoben.',
    removed: 'Marke entfernt.',
    seeked: 'Audio an der Marke.',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
    stale: 'Der Stand ist veraltet. Bitte Marken neu laden.',
    historicalRefused: 'Marken früherer Audio-Versionen sind nur lesbar.',
    invalid: 'Ungültige Position für die Marke.',
    unaligned: 'Die Auswahl hat keine Audio-Zuordnung.',
    noSeek: 'Kein Audio-Player verbunden.',
    denied: 'Keine Berechtigung für die Marken.',
    notFound: 'Das Diktat oder die Marke wurde nicht gefunden.',
    locked: 'Das Diktat wird gerade von jemand anderem bearbeitet.',
    failed: 'Die Aktion ist fehlgeschlagen.',
    busy: 'Bitte warten, eine Aktion läuft.',
});

const STEP_MS = 1000;

function fail(code, extra) {
    return { ok: false, code, message: LABELS[code === 'historical' ? 'historicalRefused' : code] || LABELS.failed, ...(extra || {}) };
}

function errorFor(res) {
    if (res.status === 404) return fail('notFound');
    if (res.status === 423) return fail('locked');
    if (res.outcome === 'permission') return fail('denied');
    if (res.outcome === 'conflict') return fail('stale');
    if (res.outcome === 'validation') return fail('invalid');
    return fail('failed');
}

function defaultKey() {
    return globalThis.crypto.randomUUID();
}

function timeLabel(ms) {
    const total = Math.floor(ms / 100) / 10;
    const minutes = Math.floor(total / 60);
    const seconds = (total - minutes * 60).toFixed(1).padStart(4, '0');
    return minutes + ':' + seconds;
}

function makeRefs(vue) {
    const V = vue || globalThis.Vue;
    const ref = V && V.ref ? V.ref : (value) => ({ value });
    const shallow = V && V.shallowRef ? V.shallowRef : ref;
    const computed = V && V.computed ? V.computed : (fn) => ({ get value() { return fn(); } });
    return { ref, shallow, computed };
}

/** @param {{editor: object, alignment?: object, client?: object, dictationId: string, getPositionMs?: Function, seek?: Function, audioElement?: object, newKey?: Function, vue?: object}} options */
export function useAudioMarkers(options) {
    const opts = options || {};
    const editor = opts.editor;
    const alignment = opts.alignment || null;
    const client = opts.client || createClinicApiClient();
    const newKey = opts.newKey || defaultKey;
    const refs = makeRefs(opts.vue);
    const base = '/dictations/' + encodeURIComponent(String(opts.dictationId));

    const raw = refs.shallow([]);
    const status = refs.ref('idle');
    const error = refs.shallow(null);
    const busy = refs.ref(false);
    const announcement = refs.ref('');

    let seq = 0;
    let disposed = false;
    let pending = null; // { digest, key } kept after an unknown outcome so a retry replays

    function say(message) { announcement.value = message; }

    const markers = refs.computed(() => {
        const rev = editor.audio.value.revisionId;
        return raw.value.map((m) => {
            const historical = !rev || m.audio_revision_id !== rev;
            return {
                id: m.id,
                type: m.marker_type,
                typeLabel: LABELS[m.marker_type] || m.marker_type,
                positionMs: m.position_ms,
                timeLabel: timeLabel(m.position_ms),
                label: typeof m.label === 'string' ? m.label : '',
                version: m.version,
                audioRevisionId: m.audio_revision_id,
                anchorOffset: Number.isInteger(m.anchor_offset) ? m.anchor_offset : null,
                ownership: historical ? 'historical' : 'current',
                historical,
                events: Array.isArray(m.events) ? m.events.length : 0,
            };
        }).sort((a, b) => a.positionMs - b.positionMs);
    });

    function lease() {
        const snap = editor.lease && editor.lease.value;
        return snap && Number.isInteger(snap.generation) ? snap.generation : null;
    }

    function playheadMs() {
        let seconds = null;
        if (typeof opts.getPositionMs === 'function') return opts.getPositionMs();
        if (opts.audioElement && typeof opts.audioElement.currentTime === 'number') seconds = opts.audioElement.currentTime;
        return seconds === null || !Number.isFinite(seconds) ? null : Math.round(seconds * 1000);
    }

    function writeGuard() {
        if (disposed) return fail('failed');
        if (busy.value) return fail('busy');
        const generation = lease();
        if (!editor.canEdit.value || generation === null) return fail('readOnly');
        if (editor.audio.value.status !== 'ready' || !editor.audio.value.revisionId) return fail('stale');
        return { ok: true, generation };
    }

    function say_(result) { say(result.message); return result; }

    /** Load the live markers (historical ones included, flagged by audio revision). */
    async function refresh() {
        if (disposed) return fail('failed');
        const mine = ++seq;
        status.value = 'loading';
        error.value = null;
        say(LABELS.loading);
        const res = await client.request(base + '/correction');
        if (mine !== seq || disposed || res.outcome === 'cancelled') return { ok: false, code: 'cancelled', message: '' };
        if (res.outcome !== 'ok' || !res.data || !Array.isArray(res.data.markers)) {
            const problem = res.outcome === 'ok' ? fail('failed') : errorFor(res);
            status.value = 'error';
            error.value = problem;
            raw.value = [];
            return say_(problem);
        }
        raw.value = res.data.markers.filter((m) => m && !m.deleted && typeof m.id === 'string' && Number.isInteger(m.position_ms));
        status.value = 'ready';
        say(LABELS.ready);
        return { ok: true };
    }

    // One request with a stable key per identical payload; keeps the key after an unknown outcome.
    async function send(path, body, okMessage) {
        const digest = path + JSON.stringify(body);
        if (!pending || pending.digest !== digest) pending = { digest, key: newKey() };
        busy.value = true;
        const res = await client.request(base + '/correction' + path, { method: 'POST', idempotencyKey: pending.key, body });
        busy.value = false;
        if (disposed || res.outcome === 'cancelled') return { ok: false, code: 'cancelled', message: '' };
        if (res.outcome === 'ok' && res.data && typeof res.data.id === 'string') {
            pending = null;
            const next = raw.value.filter((m) => m.id !== res.data.id);
            if (!res.data.deleted) next.push(res.data);
            raw.value = next;
            return say_({ ok: true, message: okMessage, marker: res.data });
        }
        if (res.outcome === 'network' || res.outcome === 'UNKNOWN') return say_(fail('failed'));
        pending = null;
        const problem = errorFor(res);
        if (problem.code === 'stale') await refresh();
        return say_(problem);
    }

    /** Place a SECTION or INDEX marker on the current audio revision. */
    async function create(params) {
        const guard = writeGuard();
        if (!guard.ok) return say_(guard);
        const p = params || {};
        const type = p.type === 'INDEX' ? 'INDEX' : 'SECTION';
        const at = p.positionMs !== undefined ? p.positionMs : playheadMs();
        if (!Number.isInteger(at) || at < 0) return say_(fail('invalid'));
        const body = { position_ms: at, audio_revision_id: editor.audio.value.revisionId, lease_generation: guard.generation, marker_type: type };
        if (typeof p.label === 'string' && p.label !== '') body.label = p.label;
        if (Number.isInteger(p.anchorOffset)) body.anchor_offset = p.anchorOffset;
        return send('/markers', body, LABELS.created);
    }

    function findLive(id) {
        const hit = markers.value.find((m) => m.id === id);
        if (!hit) return fail('notFound');
        if (hit.historical) return fail('historical');
        return hit;
    }

    /** Move a marker of the current audio revision to another position. */
    async function move(id, positionMs) {
        const guard = writeGuard();
        if (!guard.ok) return say_(guard);
        const hit = findLive(id);
        if (hit.ok === false) return say_(hit);
        if (!Number.isInteger(positionMs) || positionMs < 0) return say_(fail('invalid'));
        const body = { position_ms: positionMs, expected_version: hit.version, lease_generation: guard.generation };
        return send('/markers/' + encodeURIComponent(id) + '/move', body, LABELS.moved);
    }

    /** Remove (soft delete) a marker of the current audio revision; its history stays on the server. */
    async function remove(id) {
        const guard = writeGuard();
        if (!guard.ok) return say_(guard);
        const hit = findLive(id);
        if (hit.ok === false) return say_(hit);
        const body = { expected_version: hit.version, lease_generation: guard.generation };
        return send('/markers/' + encodeURIComponent(id) + '/delete', body, LABELS.removed);
    }

    /** INDEX marker for the selected phrase: audio time of its first aligned word, text offset as anchor. */
    async function markPhrase(label) {
        if (!alignment) return say_(fail('unaligned'));
        if (alignment.status.value !== 'ready') return say_(fail('stale'));
        const { start, end } = alignment.selection.value;
        const link = alignment.links.value.find((l) => (end > start ? l.start < end && l.end > start : l.start <= start && start < l.end));
        if (!link) return say_(fail('unaligned'));
        const phrase = typeof label === 'string' && label !== '' ? label : editor.text.value.slice(start, end);
        return create({ type: 'INDEX', positionMs: link.audioStartMs, label: phrase, anchorOffset: Math.max(start, link.start) });
    }

    /** Seek the player to a marker of the current audio revision. */
    function goTo(id) {
        const hit = findLive(id);
        if (hit.ok === false) return say_(hit);
        const seconds = hit.positionMs / 1000;
        if (typeof opts.seek === 'function') opts.seek(seconds);
        else if (opts.audioElement && typeof opts.audioElement === 'object') opts.audioElement.currentTime = seconds;
        else return say_(fail('noSeek'));
        return say_({ ok: true, seconds, message: LABELS.seeked });
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        seq += 1;
        pending = null;
        raw.value = [];
    }

    const writable = refs.computed(() => Boolean(editor.canEdit.value) && lease() !== null
        && editor.audio.value.status === 'ready' && Boolean(editor.audio.value.revisionId));

    return { markers, status, error, busy, announcement, writable, refresh, create, move, remove, markPhrase, goTo, dispose };
}

/** Vue component. Props: editor, alignment, client, dictationId, getPositionMs, seek, audioElement, vue. */
export const AudioMarkers = {
    name: 'ClinicAudioMarkers',
    props: {
        editor: { type: Object, required: true },
        alignment: { type: Object, default: null },
        client: { type: Object, default: null },
        dictationId: { type: String, required: true },
        getPositionMs: { type: Function, default: null },
        seek: { type: Function, default: null },
        audioElement: { type: Object, default: null },
        vue: { type: Object, default: null },
    },
    setup(props) {
        const V = props.vue || globalThis.Vue;
        const api = useAudioMarkers({
            editor: props.editor, alignment: props.alignment || undefined, client: props.client || undefined,
            dictationId: props.dictationId, getPositionMs: props.getPositionMs || undefined,
            seek: props.seek || undefined, audioElement: props.audioElement || undefined, vue: props.vue || undefined,
        });
        if (V && typeof V.onMounted === 'function') V.onMounted(() => { api.refresh(); });
        if (V && typeof V.onBeforeUnmount === 'function') V.onBeforeUnmount(api.dispose);
        const inputStyle = { flex: '1 1 8rem', minWidth: '0', padding: '5px 8px', fontSize: '12px', border: '1px solid var(--line)', borderRadius: '7px', background: 'var(--surface)', color: 'var(--v8-text)' };
        const surfaces = { problem: surfaceFor('error'), historical: surfaceFor('warning'), current: surfaceFor('processing') };
        const focusStyle = { '--focus-outline': FOCUS_INDICATOR.outline, '--focus-offset': FOCUS_INDICATOR.outlineOffset };
        function styleFor(marker) {
            const s = marker.historical ? surfaces.historical : surfaces.current;
            return { background: s.background, color: s.color, border: '1px solid ' + s.border };
        }
        // Free-text index name; kept in memory only, cleared after a successful create.
        const indexName = V && typeof V.ref === 'function' ? V.ref('') : { value: '' };
        async function createIndex() {
            const name = String(indexName.value || '').trim();
            const result = await api.create({ type: 'INDEX', label: name });
            if (result && result.ok) indexName.value = '';
            return result;
        }
        function nudge(marker, delta) { return api.move(marker.id, Math.max(0, marker.positionMs + delta)); }
        function toPlayhead(marker) { return api.move(marker.id, props.getPositionMs ? props.getPositionMs() : Math.round(((props.audioElement && props.audioElement.currentTime) || 0) * 1000)); }
        const { writable } = api;
        return { ...api, LABELS, STEP_MS, surfaces, focusStyle, inputStyle, styleFor, nudge, toPlayhead, writable, indexName, createIndex };
    },
    template: `
<section class="clinic-audio-markers v8-page" :aria-label="LABELS.title" :aria-busy="status === 'loading' || busy" :data-status="status">
  <h3>{{ LABELS.title }}</h3>
  <div v-if="status === 'error'" class="note" role="alert"
    :style="{ background: surfaces.problem.background, color: surfaces.problem.color, border: '1px solid ' + surfaces.problem.border }">
    {{ error && error.message }}
    <button type="button" :style="focusStyle" @click="refresh()">{{ LABELS.retry }}</button>
  </div>
  <div class="clinic-audio-markers-actions actions" role="group" aria-label="Audiomarken verwalten">
    <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="create({ type: 'SECTION' })">{{ LABELS.section }}</button>
    <label class="clinic-marker-name" style="display:flex;align-items:center;gap:6px;font-size:12px;flex:1 1 220px;min-width:0">
      <span>{{ LABELS.indexName }}</span>
      <input type="text" v-model="indexName" maxlength="200" autocomplete="off" :style="[focusStyle, inputStyle]"
        :placeholder="LABELS.indexNamePlaceholder" :disabled="busy || !writable" @keydown.enter.prevent="createIndex()" />
    </label>
    <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="createIndex()">{{ LABELS.index }}</button>
    <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="markPhrase()">{{ LABELS.markPhrase }}</button>
    <button type="button" :style="focusStyle" @click="refresh()">{{ LABELS.refresh }}</button>
  </div>
  <p v-if="!writable" class="note">{{ LABELS.readOnly }}</p>
  <p v-if="status === 'ready' && markers.length === 0" class="note">{{ LABELS.empty }}</p>
  <ul class="clinic-marker-list" role="list" :aria-label="LABELS.list">
    <li v-for="m in markers" :key="m.id" :data-ownership="m.ownership" :style="styleFor(m)">
      <div class="clinic-marker-heading"><strong>{{ m.typeLabel }}</strong>
      <span class="clinic-marker-time">{{ m.timeLabel }}</span></div>
      <p v-if="m.label" class="clinic-marker-label">{{ m.label }}</p>
      <span class="clinic-marker-revision">{{ m.historical ? LABELS.historical : LABELS.current }}</span>
      <div v-if="!m.historical" class="actions" role="group" :aria-label="m.typeLabel + ' · ' + m.timeLabel">
        <button type="button" :style="focusStyle" @click="goTo(m.id)">{{ LABELS.goTo }}</button>
        <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="nudge(m, -STEP_MS)">{{ LABELS.earlier }}</button>
        <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="nudge(m, STEP_MS)">{{ LABELS.later }}</button>
        <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="toPlayhead(m)">{{ LABELS.toPlayhead }}</button>
        <button type="button" :style="focusStyle" :disabled="busy || !writable" @click="remove(m.id)">{{ LABELS.remove }}</button>
      </div>
    </li>
  </ul>
  <div class="clinic-constituent-status" role="status" aria-live="polite">{{ status === 'error' ? '' : announcement }}</div>
</section>`,
};

export function registerAudioMarkers(app) {
    app.component('clinic-audio-markers', AudioMarkers);
    return app;
}
