/**
 * Metadata column definition editor and preview (PAR-CFG-005).
 *
 * Server contract (src/clinic/routes_metadata_fields.py, registered by x29):
 * definitions come from GET /api/clinic/v1/metadata-fields and are saved with
 * PUT /api/clinic/v1/metadata-fields/<name> through api-client (no parity
 * command). The version is an opaque content hash sent back as
 * expected_version; HTTP 409 (stale_version) marks the row stale. If the
 * endpoint is not registered yet, load() returns code "endpoint_missing".
 * Drafts apply the same bounds as src/clinic/routes_columns.py: display types
 * from DISPLAY_TYPES, field names matching the server identifier rule, and an
 * XML path restricted to plain element names (no DOCTYPE/entity, no axis,
 * wildcard or predicate syntax). Preview rows are HTML-escaped and refuse
 * fields that are unavailable, inactive or not readable for the caller.
 * Clinical values stay in memory and are never written to browser storage.
 * Status colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

const FIELDS = '/metadata-fields';

// Server contract provided by src/clinic/routes_metadata_fields.py (x28).
export const SERVER_CONTRACT = Object.freeze({
    implemented: true,
    provider: 'x28',
    list: Object.freeze({ method: 'GET', path: '/api/clinic/v1' + FIELDS, item: Object.freeze(['name', 'label', 'field_type', 'xml_path', 'version']) }),
    save: Object.freeze({ method: 'PUT', path: '/api/clinic/v1' + FIELDS + '/<name>', payload: Object.freeze(['label', 'field_type', 'xml_path', 'display_format']), expected: 'expected_version' }),
});

export const DISPLAY_TYPES = Object.freeze(['string', 'integer', 'decimal', 'boolean', 'date', 'datetime']);
export const MAX_XML_PATH = 256;
export const MAX_FIELDS = 30;
export const MAX_FORMAT = 64;

const FIELD_NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const XML_PATH = /^[A-Za-z_][A-Za-z0-9_.-]*(\/[A-Za-z_][A-Za-z0-9_.-]*){0,7}$/;
const RESERVED = Object.freeze(['constructor', 'prototype', '__proto__']);

export const LABELS = Object.freeze({
    title: 'Metadatenspalten',
    list: 'Spaltendefinitionen',
    label: 'Bezeichnung',
    displayType: 'Anzeigetyp',
    xmlPath: 'XML-Pfad',
    displayFormat: 'Anzeigeformat',
    name: 'Spaltenname',
    create: 'Neue Spalte anlegen',
    add: 'Anlegen',
    save: 'Speichern',
    preview: 'Vorschau Spalten',
    empty: 'Kein Wert',
});

export const TYPE_LABELS = Object.freeze({
    string: 'Text',
    integer: 'Ganzzahl',
    decimal: 'Dezimalzahl',
    boolean: 'Ja/Nein',
    date: 'Datum',
    datetime: 'Datum und Uhrzeit',
});

const REASONS = Object.freeze({
    unknown_field: 'Die Spalte ist unbekannt.',
    bad_display_type: 'Dieser Anzeigetyp wird nicht unterstützt.',
    bad_xml_path: 'Der XML-Pfad ist ungültig oder zu lang.',
    bad_key: 'Der Spaltenname ist ungültig.',
    bad_display_format: 'Das Anzeigeformat ist ungültig oder zu lang.',
    exists: 'Eine Spalte mit diesem Namen existiert bereits.',
    bad_label: 'Die Bezeichnung darf nicht leer sein.',
    too_many: 'Es sind höchstens 30 Spalten möglich.',
    unavailable: 'Die Spalte ist nicht verfügbar.',
    unauthorized: 'Keine Berechtigung für diese Spalte.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    validation: 'Die Eingabe wurde vom Server abgelehnt.',
    failure: 'Das Speichern ist fehlgeschlagen.',
    endpoint_missing: 'Die Spaltendefinitionen sind serverseitig noch nicht verfügbar.',
});

// Keyboard states: Enter saves the focused row, Escape reverts it.
export const KEYS = Object.freeze({ save: 'Enter', revert: 'Escape' });

export function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

function refuse(code) {
    return { ok: false, code: code, message: REASONS[code] || REASONS.failure };
}

/**
 * @param {{client?: object, reactive?: function}} [options]
 */
export function createColumnsEditor(options) {
    const client = (options && options.client) || createClinicApiClient();
    const wrap = (options && options.reactive) || ((value) => value);
    const state = wrap({
        loaded: false,
        fields: [], // { name, label, displayType, xmlPath, displayFormat, version, available, readable, active, stale, saved }
        status: 'normal',
    });

    function find(name) {
        return state.fields.find((row) => row.name === name) || null;
    }

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    async function load() {
        setStatus('processing');
        const row = await client.request(FIELDS);
        if (row.outcome !== 'ok') {
            if (row.status === 404 || row.status === 405) {
                setStatus('warning');
                return refuse('endpoint_missing');
            }
            setStatus(row.outcome === 'permission' ? 'warning' : 'error');
            return { ok: false, code: row.outcome === 'permission' ? 'permission' : 'failure', message: row.message };
        }
        const items = (row.data && row.data.fields) || [];
        state.fields = items.slice(0, MAX_FIELDS).filter((item) => (
            item && typeof item.name === 'string' && FIELD_NAME.test(item.name) && !RESERVED.includes(item.name)
        )).map((item) => {
            const draft = {
                name: item.name,
                label: String(item.label || ''),
                displayType: item.field_type,
                xmlPath: item.xml_path || '',
                displayFormat: item.display_format || '',
                version: item.version,
                available: item.available !== false,
                readable: item.readable !== false,
                active: item.is_active !== false,
                stale: false,
            };
            draft.saved = { label: draft.label, displayType: draft.displayType, xmlPath: draft.xmlPath, displayFormat: draft.displayFormat };
            return draft;
        });
        state.loaded = true;
        setStatus('success');
        return { ok: true };
    }

    function editable(name) {
        const row = find(name);
        if (!row) return [null, refuse('unknown_field')];
        if (!row.available || !row.active) return [null, refuse('unavailable')];
        if (!row.readable) return [null, refuse('unauthorized')];
        return [row, null];
    }

    function setDisplayType(name, type) {
        const [row, refusal] = editable(name);
        if (refusal) return refusal;
        if (!DISPLAY_TYPES.includes(type)) return refuse('bad_display_type');
        row.displayType = type;
        return { ok: true };
    }

    function setXmlPath(name, path) {
        const [row, refusal] = editable(name);
        if (refusal) return refusal;
        const clean = typeof path === 'string' ? path.trim() : '';
        if (clean === '') {
            row.xmlPath = '';
            return { ok: true };
        }
        if (clean.length > MAX_XML_PATH || !XML_PATH.test(clean)) return refuse('bad_xml_path');
        row.xmlPath = clean;
        return { ok: true };
    }

    function cleanFormat(format) {
        const clean = typeof format === 'string' ? format.trim() : '';
        // eslint-disable-next-line no-control-regex
        if (clean.length > MAX_FORMAT || /[\u0000-\u001f\u007f]/.test(clean)) return null;
        return clean;
    }

    function setDisplayFormat(name, format) {
        const [row, refusal] = editable(name);
        if (refusal) return refusal;
        const clean = cleanFormat(format);
        if (clean === null) return refuse('bad_display_format');
        row.displayFormat = clean;
        return { ok: true };
    }

    /**
     * Create a new column definition with PUT /metadata-fields/<name> and no
     * expected_version (the server creates it only if the name is unused).
     * draft: { name, label, displayType, xmlPath, displayFormat }.
     */
    async function create(draft) {
        const input = draft || {};
        const name = typeof input.name === 'string' ? input.name.trim() : '';
        if (!FIELD_NAME.test(name) || RESERVED.includes(name)) return refuse('bad_key');
        if (find(name)) return refuse('exists');
        if (state.fields.length >= MAX_FIELDS) return refuse('too_many');
        const label = typeof input.label === 'string' ? input.label.trim() : '';
        if (!label || label.length > 128) return refuse('bad_label');
        const type = input.displayType || 'string';
        if (!DISPLAY_TYPES.includes(type)) return refuse('bad_display_type');
        const path = typeof input.xmlPath === 'string' ? input.xmlPath.trim() : '';
        if (path !== '' && (path.length > MAX_XML_PATH || !XML_PATH.test(path))) return refuse('bad_xml_path');
        const format = cleanFormat(input.displayFormat);
        if (format === null) return refuse('bad_display_format');
        setStatus('processing');
        const body = { label: label, field_type: type, xml_path: path || null };
        if (format) body.display_format = format;
        const result = await client.request(FIELDS + '/' + name, { method: 'PUT', body: body });
        if (result.outcome === 'ok') {
            const data = result.data || {};
            const row = {
                name: name,
                label: label,
                displayType: type,
                xmlPath: path,
                displayFormat: format,
                version: typeof data.version === 'string' ? data.version : undefined,
                available: true,
                readable: true,
                active: true,
                stale: false,
            };
            row.saved = { label: label, displayType: type, xmlPath: path, displayFormat: format };
            state.fields.push(row);
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            setStatus('warning');
            return refuse('exists');
        }
        if (result.outcome === 'validation') {
            setStatus('error');
            return refuse('validation');
        }
        setStatus(result.outcome === 'permission' ? 'warning' : 'error');
        return refuse(result.outcome === 'permission' ? 'permission' : 'failure');
    }

    function setLabel(name, label) {
        const [row, refusal] = editable(name);
        if (refusal) return refusal;
        const clean = typeof label === 'string' ? label.trim() : '';
        if (!clean || clean.length > 128) return refuse('bad_label');
        row.label = clean;
        return { ok: true };
    }

    /**
     * Preview projected columns as the worklist would show them. `projected`
     * is the server projection ({key,label,display_type,display_format,value}).
     * Keys without an available, readable, active definition are rejected
     * before anything is rendered. Values and labels come back HTML-escaped.
     */
    function preview(projected) {
        const rows = Array.isArray(projected) ? projected : [];
        if (rows.length > MAX_FIELDS) return refuse('too_many');
        const out = [];
        for (const item of rows) {
            const key = item && item.key;
            if (typeof key !== 'string' || !FIELD_NAME.test(key) || RESERVED.includes(key)) return refuse('bad_key');
            const [, refusal] = editable(key);
            if (refusal) return refusal;
            if (!DISPLAY_TYPES.includes(item.display_type)) return refuse('bad_display_type');
            const empty = item.value == null || item.value === '';
            out.push({
                key: key,
                label: escapeHtml(item.label),
                displayType: item.display_type,
                value: empty ? escapeHtml(LABELS.empty) : escapeHtml(item.value),
                empty: empty,
                ariaLabel: escapeHtml(item.label) + ' (' + TYPE_LABELS[item.display_type] + ')',
            });
        }
        return { ok: true, rows: out };
    }

    async function save(name) {
        const [row, refusal] = editable(name);
        if (refusal) return refusal;
        if (row.stale) return refuse('stale_version');
        setStatus('processing');
        const body = {
            label: row.label,
            field_type: row.displayType,
            xml_path: row.xmlPath || null,
            expected_version: row.version,
        };
        if (row.displayFormat || row.saved.displayFormat) body.display_format = row.displayFormat || null;
        const result = await client.request(FIELDS + '/' + row.name, { method: 'PUT', body: body });
        if (result.outcome === 'ok') {
            const data = result.data || {};
            if (typeof data.version === 'string') row.version = data.version;
            row.saved = { label: row.label, displayType: row.displayType, xmlPath: row.xmlPath, displayFormat: row.displayFormat };
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            row.stale = true;
            setStatus('warning');
            return refuse('stale_version');
        }
        if (result.outcome === 'validation') {
            setStatus('error');
            return refuse('validation');
        }
        setStatus(result.outcome === 'permission' ? 'warning' : 'error');
        return refuse(result.outcome === 'permission' ? 'permission' : 'failure');
    }

    function revert(name) {
        const row = find(name);
        if (!row) return refuse('unknown_field');
        row.label = row.saved.label;
        row.displayType = row.saved.displayType;
        row.xmlPath = row.saved.xmlPath;
        row.displayFormat = row.saved.displayFormat;
        return { ok: true };
    }

    /** Keyboard handler for a row: Enter saves, Escape reverts. */
    function onKey(name, event) {
        if (!event) return Promise.resolve({ ok: false });
        if (event.key === KEYS.save) return save(name);
        if (event.key === KEYS.revert) return Promise.resolve(revert(name));
        return Promise.resolve({ ok: false });
    }

    function reset() {
        state.fields = [];
        state.loaded = false;
        state.status = 'normal';
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, create, setDisplayType, setXmlPath, setDisplayFormat, setLabel, preview, save, revert, onKey, reset };
}

/**
 * Vue composable. `vue` supplies { reactive } (the global Vue build or an
 * import). The returned editor shares one reactive state object.
 */
export function useColumnsEditor(vue, options) {
    const editor = createColumnsEditor(Object.assign({}, options, { reactive: vue.reactive }));
    return Object.assign(editor, { labels: LABELS, keys: KEYS, displayTypes: DISPLAY_TYPES, typeLabels: TYPE_LABELS });
}

/**
 * Component definition for the admin mount (w226). Markup carries labels and
 * roles; the colours are CSS variables from tokens.js.
 */
export function columnsComponent(vue, options) {
    return {
        name: 'ClinicColumns',
        setup() {
            const editor = useColumnsEditor(vue, options);
            editor.load();
            const draft = vue.reactive({ name: '', label: '', displayType: 'string', xmlPath: '', displayFormat: '' });
            const feedback = vue.reactive({ message: '', ok: false });
            async function submit() {
                const result = await editor.create(draft);
                feedback.ok = !!result.ok;
                feedback.message = result.ok ? 'Die Spalte wurde angelegt.' : result.message;
                if (result.ok) Object.assign(draft, { name: '', label: '', displayType: 'string', xmlPath: '', displayFormat: '' });
            }
            return { editor, draft, feedback, submit };
        },
        template: `
<section role="region" aria-labelledby="clinic-col-title" :aria-busy="editor.state.status === 'processing'" class="v8-page v8-admin-page">
  <header class="pagehead">
    <div>
      <div class="eyebrow">Verwaltung</div>
      <h1 id="clinic-col-title">${LABELS.title}</h1>
      <p class="subline">Bezeichnung, Anzeigetyp und XML-Pfad der Metadatenspalten festlegen.</p>
    </div>
  </header>
  <section class="panel" aria-labelledby="clinic-col-list-title">
    <h2 id="clinic-col-list-title">Spaltenübersicht</h2>
    <p v-if="editor.state.status === 'processing'" role="status" class="text-sm text-[var(--text-muted)]">Spalten werden geladen.</p>
    <p v-else-if="editor.state.loaded && !editor.state.fields.length" role="status" class="empty">Keine Spalten vorhanden.</p>
    <form class="mb-4 flex flex-wrap items-end gap-3 [&>label]:min-w-[11rem] [&>label]:flex-1 max-sm:[&>button]:w-full" aria-label="${LABELS.create}" @submit.prevent="submit">
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.name}</span>
        <input type="text" class="field font-mono" v-model="draft.name" maxlength="64" required>
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.label}</span>
        <input type="text" class="field" v-model="draft.label" maxlength="128" required>
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.displayType}</span>
        <select class="field" v-model="draft.displayType">
          <option v-for="type in editor.displayTypes" :key="type" :value="type">{{ editor.typeLabels[type] }}</option>
        </select>
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.xmlPath}</span>
        <input type="text" class="field font-mono" v-model="draft.xmlPath" maxlength="${MAX_XML_PATH}">
      </label>
      <label>
        <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.displayFormat}</span>
        <input type="text" class="field font-mono" v-model="draft.displayFormat" maxlength="${MAX_FORMAT}">
      </label>
      <button type="submit" class="btn btn--primary" :disabled="editor.state.status === 'processing'">${LABELS.add}</button>
      <p v-if="feedback.message" role="status" class="basis-full text-sm" :class="feedback.ok ? 'text-[var(--text-muted)]' : 'text-[var(--danger)]'">{{ feedback.message }}</p>
    </form>
    <ul v-if="editor.state.fields.length" role="list" aria-label="${LABELS.list}" class="grid gap-3">
      <li v-for="row in editor.state.fields" :key="row.name" class="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" @keydown.enter.prevent="editor.save(row.name)" @keydown.esc="editor.revert(row.name)">
        <p class="mb-3 font-mono text-xs text-[var(--text-muted)]">{{ row.name }}</p>
        <div class="flex flex-wrap items-end gap-3 [&>label]:min-w-[11rem] [&>label]:flex-1 max-sm:[&>button]:w-full">
          <label>
            <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.label}</span>
            <input type="text" class="field" :value="row.label" :disabled="!row.available || !row.readable || !row.active" @change="editor.setLabel(row.name, $event.target.value)">
          </label>
          <label>
            <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.displayType}</span>
            <select class="field" :value="row.displayType" :disabled="!row.available || !row.readable || !row.active" @change="editor.setDisplayType(row.name, $event.target.value)">
              <option v-for="type in editor.displayTypes" :key="type" :value="type">{{ editor.typeLabels[type] }}</option>
            </select>
          </label>
          <label>
            <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.xmlPath}</span>
            <input type="text" class="field font-mono" :value="row.xmlPath" maxlength="${MAX_XML_PATH}" :disabled="!row.available || !row.readable || !row.active" @change="editor.setXmlPath(row.name, $event.target.value)">
          </label>
          <label>
            <span class="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">${LABELS.displayFormat}</span>
            <input type="text" class="field font-mono" :value="row.displayFormat" maxlength="${MAX_FORMAT}" :disabled="!row.available || !row.readable || !row.active" @change="editor.setDisplayFormat(row.name, $event.target.value)">
          </label>
          <button type="button" class="btn btn--primary" :disabled="!row.available || !row.readable || !row.active || row.stale" @click="editor.save(row.name)">${LABELS.save}</button>
        </div>
      </li>
    </ul>
  </section>
</section>`,
    };
}
