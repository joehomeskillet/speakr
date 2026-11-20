/**
 * Versioned template manager with insertion-marker preview (PAR-CFG-002).
 *
 * Templates come from GET /api/clinic/v1/templates and are append-only on the
 * server: a save creates a new version, it never replaces an older one. The
 * history therefore stays in memory next to the current version. Saves send
 * the latest known version as If-Match and one Idempotency-Key per draft, so a
 * retry after a failure replays instead of duplicating. Marker rules mirror
 * validate_marker in src/clinic/routes_templates.py. In previews %A (author)
 * and %T (title) are substituted by position, as in src/clinic/output.py.
 * Template content and sample text stay in memory and never reach browser
 * storage. Status colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

const TEMPLATES = '/templates';
const MAX_MARKER = 128;

export const LABELS = Object.freeze({
    title: 'Vorlagen',
    list: 'Vorlagen und Versionen',
    history: 'Versionsverlauf',
    marker: 'Einfügemarke',
    preview: 'Vorschau der Einfügung',
    assign: 'Zuweisen',
    save: 'Neue Version speichern',
    scope: 'Bereich',
});

const REASONS = Object.freeze({
    empty_marker: 'Die Einfügemarke darf nicht leer sein.',
    invalid_marker: 'Die Einfügemarke ist ungültig.',
    missing_marker: 'Keine Einfügemarke: Der Text wird am Ende angehängt.',
    marker_not_found: 'Die Einfügemarke kommt im Beispieltext nicht vor.',
    unknown_placeholder: 'Unbekannter Platzhalter in der Vorschau.',
    unknown_template: 'Die Vorlage ist unbekannt.',
    invalid_draft: 'Name, Inhalt und Bereich sind erforderlich.',
    too_large: 'Die Vorlage ist zu gross.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    scope_error: 'Keine Berechtigung für diesen Bereich.',
    permission: 'Keine Berechtigung für diese Aktion.',
    failure: 'Die Aktion ist fehlgeschlagen.',
});

// Keyboard states: arrows move the selection, Enter previews, Escape clears.
export const KEYS = Object.freeze({ next: 'ArrowDown', prev: 'ArrowUp', preview: 'Enter', clear: 'Escape' });

function refuse(code) {
    return { ok: false, code: code, message: REASONS[code] || REASONS.failure };
}

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    const hex = () => Math.floor(Math.random() * 16).toString(16);
    return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, hex);
}

/** Same rules as the server: null is allowed, text must be trimmed, <= 128, no control chars or script tags. */
export function validateMarker(marker) {
    if (marker === null || marker === undefined) return { ok: true };
    if (typeof marker !== 'string' || marker.trim() === '') return refuse('empty_marker');
    // eslint-disable-next-line no-control-regex
    if (marker.length > MAX_MARKER || marker !== marker.trim() || /[\u0000-\u001f]/.test(marker)) return refuse('invalid_marker');
    if (/<\/?script/i.test(marker)) return refuse('invalid_marker');
    return { ok: true };
}

/** Positional placeholders: %A author, %T title, %% literal percent sign. */
export function expandPlaceholders(text, values) {
    const vals = values || {};
    let bad = false;
    const out = String(text || '').replace(/%(.?)/g, (all, letter) => {
        if (letter === '%') return '%';
        if (letter === 'A') return vals.author || '';
        if (letter === 'T') return vals.title || '';
        bad = true;
        return all;
    });
    return bad ? null : out;
}

async function sha256Hex(bytes) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

function toBase64(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
}

function scopeKey(row) {
    return row.scope + '|' + (row.scopeId == null ? '' : row.scopeId) + '|' + row.name;
}

/**
 * @param {{client?: object, reactive?: Function}} [options]
 */
export function createTemplatesManager(options) {
    const client = (options && options.client) || createClinicApiClient();
    const wrap = (options && options.reactive) || ((value) => value);
    const state = wrap({
        loaded: false,
        items: [], // every known version: { id, name, scope, scopeId, version, mimeType, sha256, marker }
        selectedId: null,
        stale: false,
        status: 'normal',
    });
    let pending = null; // { digest, key } of the draft that is being (re)tried

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    function fromSummary(item, prior) {
        return {
            id: item.id,
            name: item.name,
            scope: item.scope,
            scopeId: item.scope_id !== undefined ? item.scope_id : (prior ? prior.scopeId : null),
            version: item.version,
            mimeType: item.mime_type,
            sha256: item.content_sha256,
            marker: item.insertion_mark !== undefined ? item.insertion_mark : (prior ? prior.marker : null),
        };
    }

    function find(id) {
        return state.items.find((row) => row.id === id) || null;
    }

    function latestFor(name, scope, scopeId) {
        const key = scope + '|' + (scopeId == null ? '' : scopeId) + '|' + name;
        return state.items.filter((row) => scopeKey(row) === key).reduce((top, row) => (!top || row.version > top.version ? row : top), null);
    }

    // Merge, never replace: older versions the server omitted stay in the history.
    function merge(rows) {
        for (const item of rows) {
            const prior = find(item.id);
            const next = fromSummary(item, prior);
            if (prior) Object.assign(prior, next);
            else state.items.push(next);
        }
    }

    function failure(row, fallback) {
        if (row.status === 412 || row.status === 409 || row.outcome === 'conflict') {
            state.stale = true;
            setStatus('warning');
            return refuse('stale_version');
        }
        if (row.status === 422) { setStatus('error'); return refuse('invalid_marker'); }
        if (row.status === 413) { setStatus('error'); return refuse('too_large'); }
        if (row.status === 403 || row.outcome === 'permission') {
            setStatus('warning');
            return refuse(fallback === 'scope_error' ? 'scope_error' : 'permission');
        }
        setStatus('error');
        return refuse('failure');
    }

    async function load() {
        setStatus('processing');
        const row = await client.request(TEMPLATES);
        if (row.outcome !== 'ok') return failure(row, 'permission');
        merge((row.data && row.data.items) || []);
        state.loaded = true;
        state.stale = false;
        setStatus('success');
        return { ok: true };
    }

    function select(id) {
        const row = find(id);
        if (!row) return refuse('unknown_template');
        state.selectedId = id;
        return { ok: true, template: row };
    }

    /** Versions of one template line, newest first. Older versions stay visible. */
    function history(id) {
        const row = find(id);
        if (!row) return [];
        return state.items.filter((it) => scopeKey(it) === scopeKey(row)).sort((a, b) => b.version - a.version);
    }

    /** Warnings for a template: missing/invalid marker, stale list. */
    function issues(id) {
        const row = find(id);
        if (!row) return [refuse('unknown_template')];
        const out = [];
        if (row.marker === null || row.marker === undefined) out.push({ code: 'missing_marker', message: REASONS.missing_marker });
        else if (!validateMarker(row.marker).ok) out.push({ code: 'invalid_marker', message: REASONS.invalid_marker });
        if (state.stale) out.push({ code: 'stale_version', message: REASONS.stale_version });
        const top = latestFor(row.name, row.scope, row.scopeId);
        if (top && top.version > row.version) out.push({ code: 'stale_version', message: REASONS.stale_version });
        return out;
    }

    /**
     * Preview the insertion into a plain-text sample. Without a marker the text
     * is appended, like the server. %A/%T in the dictation text expand by position.
     */
    function previewInsertion(id, sample, dictationText, values) {
        const row = find(id);
        if (!row) return refuse('unknown_template');
        const insert = expandPlaceholders(dictationText, values);
        if (insert === null) return refuse('unknown_placeholder');
        const base = String(sample || '');
        const mark = row.marker;
        if (!mark) return { ok: true, found: false, text: base + '\n' + insert, warning: 'missing_marker' };
        const check = validateMarker(mark);
        if (!check.ok) return check;
        const at = base.indexOf(mark);
        if (at < 0) return { ok: true, found: false, text: base + '\n' + insert, warning: 'marker_not_found' };
        return { ok: true, found: true, text: base.slice(0, at) + insert + base.slice(at + mark.length) };
    }

    /**
     * Save a new version. draft: { name, scope, scopeId?, mimeType, bytes: Uint8Array, marker?, notes? }.
     * The version in If-Match is the latest known version of the same line (0 = new).
     */
    async function save(draft) {
        const d = draft || {};
        if (!d.name || !d.scope || !d.mimeType || !(d.bytes instanceof Uint8Array)) return refuse('invalid_draft');
        const marker = d.marker === undefined ? null : d.marker;
        const check = validateMarker(marker);
        if (!check.ok) return check;
        if (state.stale) return refuse('stale_version');
        const top = latestFor(d.name, d.scope, d.scopeId == null ? null : d.scopeId);
        const expected = top ? top.version : 0;
        const sha = await sha256Hex(d.bytes);
        const digest = [d.name, d.scope, d.scopeId, sha, marker, d.notes || '', expected].join('|');
        if (!pending || pending.digest !== digest) pending = { digest: digest, key: newKey() };
        const scope = { scope: d.scope };
        if (d.scopeId != null) scope.scope_id = d.scopeId;
        const body = { name: d.name, scope: scope, mime_type: d.mimeType, content_base64: toBase64(d.bytes), content_sha256: sha };
        if (marker !== null) body.insertion_mark = marker;
        if (d.notes) body.notes = d.notes;
        setStatus('processing');
        const row = await client.request(TEMPLATES, { method: 'POST', body: body, idempotencyKey: pending.key, ifMatch: '"' + expected + '"' });
        if (row.outcome !== 'ok') {
            const refusal = failure(row, 'scope_error');
            if (refusal.code === 'stale_version') pending = null; // a changed base needs a new key
            return refusal;
        }
        pending = null;
        const data = row.data || {};
        merge([Object.assign({}, data, { scope_id: d.scopeId == null ? null : d.scopeId, insertion_mark: marker })]);
        state.selectedId = data.id || state.selectedId;
        setStatus('success');
        return { ok: true, template: find(data.id) };
    }

    /** Assign an existing template to another scope; the original stays untouched. */
    async function assign(id, scope, scopeId) {
        const row = find(id);
        if (!row) return refuse('unknown_template');
        const target = { scope: scope };
        if (scopeId != null) target.scope_id = scopeId;
        setStatus('processing');
        const res = await client.request(TEMPLATES + '/' + encodeURIComponent(id) + '/assign', { method: 'POST', body: { scope: target } });
        if (res.outcome !== 'ok') return failure(res, 'scope_error');
        const data = res.data || {};
        merge([Object.assign({}, data, { scope_id: scopeId == null ? null : scopeId, insertion_mark: row.marker })]);
        setStatus('success');
        return { ok: true, template: find(data.id) };
    }

    /** Keyboard handler for the template list. */
    function onKey(event) {
        if (!event) return { ok: false };
        const at = state.items.findIndex((row) => row.id === state.selectedId);
        if (event.key === KEYS.next && state.items.length) return select(state.items[Math.min(at + 1, state.items.length - 1)].id);
        if (event.key === KEYS.prev && state.items.length) return select(state.items[Math.max(at - 1, 0)].id);
        if (event.key === KEYS.clear) { state.selectedId = null; return { ok: true }; }
        return { ok: false };
    }

    function reset() {
        state.items = [];
        state.selectedId = null;
        state.loaded = false;
        state.stale = false;
        state.status = 'normal';
        pending = null;
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, select, history, issues, previewInsertion, save, assign, onKey, reset };
}

/**
 * Vue composable. `vue` supplies { reactive } (the global Vue build or an
 * import). The returned manager shares one reactive state object.
 */
export function useTemplatesManager(vue, options) {
    const manager = createTemplatesManager(Object.assign({}, options, { reactive: vue.reactive }));
    return Object.assign(manager, { labels: LABELS, keys: KEYS });
}

/**
 * Component definition for the admin mount (w226). Markup carries labels and
 * roles; the colours are CSS variables from tokens.js.
 */
export function templatesComponent(vue, options) {
    return {
        name: 'ClinicTemplates',
        setup() {
            const manager = useTemplatesManager(vue, options);
            manager.load();
            return { manager };
        },
        template: `
<section role="region" aria-labelledby="clinic-tpl-title" :aria-busy="manager.state.status === 'processing'" class="space-y-4">
  <header>
    <h2 id="clinic-tpl-title" class="text-lg font-semibold text-[var(--text-primary)]">${LABELS.title}</h2>
    <p class="mt-1 text-sm text-[var(--text-muted)]">Vorlagen mit Versionen und Einfügemarke. Jede Änderung legt eine neue Version an.</p>
  </header>
  <p v-if="manager.state.status === 'processing'" role="status" class="text-sm text-[var(--text-muted)]">{{ manager.state.loaded ? 'Wird verarbeitet …' : 'Vorlagen werden geladen.' }}</p>
  <p v-else-if="manager.state.loaded && !manager.state.items.length" role="status" class="rounded-lg border border-dashed border-[var(--border-primary)] p-4 text-sm text-[var(--text-muted)]">Keine Vorlagen vorhanden.</p>
  <ul role="listbox" aria-label="${LABELS.list}" tabindex="0" class="grid gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--border-focus)]" @keydown="manager.onKey($event)">
    <li v-for="row in manager.state.items" :key="row.id" role="option" :aria-selected="row.id === manager.state.selectedId"
        class="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-4 py-3 text-sm hover:bg-[var(--bg-tertiary)] aria-selected:border-[var(--border-focus)] aria-selected:bg-[var(--bg-tertiary)]"
        @click="manager.select(row.id)">
      <span class="font-medium text-[var(--text-primary)]">{{ row.name }}</span>
      <span class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-2.5 py-0.5 text-xs text-[var(--text-primary)]">v{{ row.version }}</span>
      <span class="ml-auto text-xs text-[var(--text-muted)]">${LABELS.scope}: {{ row.scope }}</span>
    </li>
  </ul>
  <div v-if="manager.state.selectedId" role="group" aria-label="${LABELS.history}" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 space-y-3">
    <h3 class="text-base font-semibold text-[var(--text-primary)]">${LABELS.history}</h3>
    <div class="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
      <ul role="list" class="divide-y divide-[var(--border-primary)] text-sm">
        <li v-for="row in manager.history(manager.state.selectedId)" :key="row.id" class="flex flex-wrap items-center gap-x-3 px-4 py-2 text-[var(--text-secondary)]"><span class="inline-flex items-center rounded-full bg-[var(--bg-tertiary)] px-2.5 py-0.5 text-xs text-[var(--text-primary)]">v{{ row.version }}</span><span>${LABELS.marker}:</span><code class="rounded bg-[var(--bg-tertiary)] px-1.5 text-[var(--text-primary)]">{{ row.marker || '-' }}</code></li>
      </ul>
    </div>
    <ul role="alert" aria-label="Hinweise" class="space-y-2 empty:hidden">
      <li v-for="issue in manager.issues(manager.state.selectedId)" :key="issue.code" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-sm text-[var(--text-secondary)]">{{ issue.message }}</li>
    </ul>
  </div>
</section>`,
    };
}
