/**
 * Metadata column definition editor and preview (PAR-CFG-005).
 *
 * Definitions come from GET /api/clinic/v1/metadata-fields and are saved through
 * the parity command channel with the field version as expected revision.
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

export const DISPLAY_TYPES = Object.freeze(['string', 'integer', 'decimal', 'boolean', 'date', 'datetime']);
export const MAX_XML_PATH = 256;
export const MAX_FIELDS = 30;

const FIELD_NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const XML_PATH = /^[A-Za-z_][A-Za-z0-9_.-]*(\/[A-Za-z_][A-Za-z0-9_.-]*){0,7}$/;
const RESERVED = Object.freeze(['constructor', 'prototype', '__proto__']);

export const LABELS = Object.freeze({
    title: 'Metadatenspalten',
    list: 'Spaltendefinitionen',
    label: 'Bezeichnung',
    displayType: 'Anzeigetyp',
    xmlPath: 'XML-Pfad',
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
    bad_label: 'Die Bezeichnung darf nicht leer sein.',
    too_many: 'Es sind höchstens 30 Spalten möglich.',
    unavailable: 'Die Spalte ist nicht verfügbar.',
    unauthorized: 'Keine Berechtigung für diese Spalte.',
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    failure: 'Das Speichern ist fehlgeschlagen.',
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

function newKey() {
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'col-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

/**
 * @param {{client?: object, reactive?: function}} [options]
 */
export function createColumnsEditor(options) {
    const client = (options && options.client) || createClinicApiClient();
    const wrap = (options && options.reactive) || ((value) => value);
    const state = wrap({
        loaded: false,
        fields: [], // { name, label, displayType, xmlPath, version, available, readable, active, stale, saved }
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
                version: item.version,
                available: item.available !== false,
                readable: item.readable !== false,
                active: item.is_active !== false,
                stale: false,
            };
            draft.saved = { label: draft.label, displayType: draft.displayType, xmlPath: draft.xmlPath };
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
        const key = newKey();
        const result = await client.command(
            {
                command: 'save_metadata_field',
                request_id: key,
                expected_revision: row.version,
                payload: {
                    name: row.name,
                    label: row.label,
                    field_type: row.displayType,
                    xml_path: row.xmlPath || null,
                },
            },
            { idempotencyKey: key, ifMatch: '"' + row.version + '"' },
        );
        if (result.outcome === 'ok') {
            const data = result.data || {};
            if (Number.isInteger(data.revision)) row.version = data.revision;
            row.saved = { label: row.label, displayType: row.displayType, xmlPath: row.xmlPath };
            setStatus('success');
            return { ok: true };
        }
        if (result.outcome === 'conflict') {
            row.stale = true;
            setStatus('warning');
            return refuse('stale_version');
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

    return { state, load, setDisplayType, setXmlPath, setLabel, preview, save, revert, onKey, reset };
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
            return { editor };
        },
        template: `
<section role="region" aria-labelledby="clinic-col-title" :aria-busy="editor.state.status === 'processing'">
  <h2 id="clinic-col-title">${LABELS.title}</h2>
  <ul role="list" aria-label="${LABELS.list}">
    <li v-for="row in editor.state.fields" :key="row.name" @keydown.enter.prevent="editor.save(row.name)" @keydown.esc="editor.revert(row.name)">
      <label>${LABELS.label}
        <input type="text" :value="row.label" :disabled="!row.available || !row.readable || !row.active" @change="editor.setLabel(row.name, $event.target.value)">
      </label>
      <label>${LABELS.displayType}
        <select :value="row.displayType" :disabled="!row.available || !row.readable || !row.active" @change="editor.setDisplayType(row.name, $event.target.value)">
          <option v-for="type in editor.displayTypes" :key="type" :value="type">{{ editor.typeLabels[type] }}</option>
        </select>
      </label>
      <label>${LABELS.xmlPath}
        <input type="text" :value="row.xmlPath" maxlength="${MAX_XML_PATH}" :disabled="!row.available || !row.readable || !row.active" @change="editor.setXmlPath(row.name, $event.target.value)">
      </label>
      <button type="button" :disabled="!row.available || !row.readable || !row.active || row.stale" @click="editor.save(row.name)">${LABELS.save}</button>
    </li>
  </ul>
</section>`,
    };
}
