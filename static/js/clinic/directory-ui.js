/**
 * Directory review (PAR-ADM-009, PAR-ADM-010).
 *
 * Shows the versioned server preview of directory changes (additions,
 * modifications, disabled identities) and applies a selection only after an
 * explicit confirmation bound to the preview version and hash. A stale
 * version or state (409) forces a reload; nothing is applied silently and
 * nothing is deleted (accounts are disabled only). Directory data stays in
 * memory. Provider state (IWA/LDAP/Entra) and local Windows enumeration are
 * shown as unresolved. Status colours come from tokens.js only.
 */

import { createClinicApiClient } from './api-client.js';
import { surfaceFor } from './tokens.js';

const BASE = '/directory';

export const LABELS = Object.freeze({
    title: 'Verzeichnisabgleich',
    description: 'Vergleicht lokale Konten mit dem Verzeichnis. Vorschau laden, Auswahl prüfen und erst nach der Bestätigung anwenden.',
    reload: 'Vorschau laden',
    additions: 'Neue Identitäten',
    modifications: 'Änderungen',
    disabled: 'Zu deaktivierende Identitäten',
    memberships: 'Poolzuordnungen',
    empty: 'Keine Änderungen.',
    review: 'Auswahl prüfen',
    confirmTitle: 'Änderungen bestätigen',
    confirm: 'Verbindlich anwenden',
    cancel: 'Zurück',
    retry: 'Erneut versuchen',
    local: 'Lokale Konten verwalten',
    providers: 'Anbieterstatus',
    unresolved: 'Nicht aktiviert / nicht verifiziert: IWA, LDAP und Entra sind nicht verifiziert. '
        + 'Die Auswahl lokaler Windows-Konten bleibt offen.',
    linkRequired: 'Verknüpfung nötig (lokale Konten verwalten)',
    archiveOnly: 'Es wird nichts gelöscht, Konten werden nur deaktiviert.',
});

const REASONS = Object.freeze({
    stale: 'Der Verzeichnisstand hat sich geändert. Bitte die Vorschau neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    unavailable: 'Das Verzeichnis ist nicht verfügbar.',
    nothing_selected: 'Es ist nichts ausgewählt.',
    no_preview: 'Es liegt keine Vorschau vor.',
    not_confirmed: 'Die Bestätigung fehlt.',
    failure: 'Der Vorgang ist fehlgeschlagen. Bitte erneut versuchen.',
});

export const KEYS = Object.freeze({ toggle: [' ', 'Enter'], cancel: 'Escape' });

const STALE_CODES = ['stale_version', 'stale_state', 'REVISION_CONFLICT'];

function refuse(code) {
    return { ok: false, code: code, message: REASONS[code] || REASONS.failure };
}

function failureCode(row) {
    if (row.outcome === 'permission') return 'permission';
    if (row.outcome === 'conflict' && STALE_CODES.includes(row.code)) return 'stale';
    if (row.code === 'ldap_capability_unavailable') return 'unavailable';
    return 'failure';
}

function actionKey(a) {
    return 'a:' + a.kind + ':' + a.object_id;
}

function memberKey(m) {
    return 'm:' + [m.kind, m.subject_id, m.pool_kind, m.pool_id].join(':');
}

/** Group the preview into the three review lists plus pool changes. */
export function groupPreview(preview) {
    const actions = (preview && preview.actions) || [];
    return {
        additions: actions.filter((a) => a.kind === 'create_pending_user'),
        modifications: actions.filter((a) => a.kind === 'update_display_name' || a.kind === 'update_email'),
        disabled: actions.filter((a) => a.kind === 'deactivate'),
        memberships: (preview && preview.membership_changes) || [],
    };
}

/**
 * @param {{client?: object, managedPools?: object[], reactive?: Function}} [options]
 */
export function createDirectoryReview(options) {
    const opts = options || {};
    const client = opts.client || createClinicApiClient();
    const wrap = opts.reactive || ((value) => value);
    const managedPools = Array.isArray(opts.managedPools) ? opts.managedPools : [];
    const state = wrap({
        status: 'normal',
        preview: null, // { version, hash, source, ... }; memory only
        selected: {}, // key -> true
        confirming: false,
        stale: false,
        message: '',
        capability: null,
    });

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    function fail(row) {
        const code = failureCode(row);
        if (code === 'stale') {
            state.stale = true;
            state.confirming = false;
        }
        setStatus(code === 'failure' || code === 'unavailable' ? 'error' : 'warning');
        state.message = REASONS[code];
        return refuse(code);
    }

    async function load() {
        setStatus('processing');
        state.confirming = false;
        const cap = await client.request(BASE + '/capability');
        if (cap.outcome === 'ok') state.capability = cap.data;
        const row = await client.request(BASE + '/preview', { method: 'POST', body: { managed_pools: managedPools } });
        if (row.outcome !== 'ok') {
            // Keep a stale preview visible but unusable; fail() sets the reload requirement.
            return fail(row);
        }
        state.preview = row.data;
        state.stale = false;
        state.message = '';
        state.selected = {};
        const groups = groupPreview(row.data);
        for (const a of [...groups.modifications, ...groups.disabled]) {
            if (a.kind !== 'update_email') state.selected[actionKey(a)] = true;
        }
        for (const m of groups.memberships) state.selected[memberKey(m)] = true;
        setStatus('success');
        return { ok: true };
    }

    function toggle(key) {
        if (state.stale || !(key in state.selected) && !selectable(key)) return;
        if (state.selected[key]) delete state.selected[key];
        else state.selected[key] = true;
        state.confirming = false;
    }

    function selectable(key) {
        if (!state.preview) return false;
        const g = groupPreview(state.preview);
        return [...g.modifications, ...g.disabled].some((a) => actionKey(a) === key)
            || g.memberships.some((m) => memberKey(m) === key);
    }

    function selection() {
        const g = groupPreview(state.preview);
        return {
            actions: [...g.modifications, ...g.disabled]
                .filter((a) => state.selected[actionKey(a)])
                .map((a) => ({ kind: a.kind, object_id: a.object_id })),
            memberships: g.memberships
                .filter((m) => state.selected[memberKey(m)])
                .map((m) => ({ kind: m.kind, subject_id: m.subject_id, pool_kind: m.pool_kind, pool_id: m.pool_id })),
        };
    }

    /** First step: show what will be applied. No request is made. */
    function review() {
        if (!state.preview) return refuse('no_preview');
        if (state.stale) return refuse('stale');
        const sel = selection();
        if (sel.actions.length + sel.memberships.length === 0) return refuse('nothing_selected');
        state.confirming = true;
        return { ok: true, count: sel.actions.length + sel.memberships.length };
    }

    function cancel() {
        state.confirming = false;
    }

    /** Second step: explicit confirmation bound to version and hash. */
    async function apply() {
        if (!state.preview) return refuse('no_preview');
        if (state.stale) return refuse('stale');
        if (!state.confirming) return refuse('not_confirmed');
        const sel = selection();
        setStatus('processing');
        const row = await client.request(BASE + '/apply', {
            method: 'POST',
            body: {
                version: state.preview.version,
                hash: state.preview.hash,
                managed_pools: managedPools,
                actions: sel.actions,
                memberships: sel.memberships,
            },
        });
        if (row.outcome !== 'ok') return fail(row);
        // Applied: the old preview is obsolete, a reload is required for the next round.
        state.preview = null;
        state.selected = {};
        state.confirming = false;
        state.message = '';
        setStatus('success');
        return { ok: true, applied: row.data };
    }

    function onKey(key, event) {
        if (!event) return { ok: false };
        if (KEYS.toggle.includes(event.key)) {
            toggle(key);
            return { ok: true };
        }
        if (event.key === KEYS.cancel && state.confirming) {
            cancel();
            return { ok: true };
        }
        return { ok: false };
    }

    function reset() {
        state.preview = null;
        state.selected = {};
        state.confirming = false;
        state.stale = false;
        state.message = '';
        state.capability = null;
        state.status = 'normal';
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, toggle, review, cancel, apply, onKey, reset, groups: () => groupPreview(state.preview) };
}

export function useDirectoryReview(vue, options) {
    const ctrl = createDirectoryReview(Object.assign({}, options, { reactive: vue.reactive }));
    return Object.assign(ctrl, { labels: LABELS, keys: KEYS });
}

/**
 * Vue options object for the admin mount (w226). Props: client, currentUser,
 * vue (defaults to the global Vue build), managedPools.
 */
export const DirectoryReview = {
    name: 'ClinicDirectoryReview',
    props: {
        client: { type: Object, default: null },
        currentUser: { type: Object, default: null },
        vue: { type: Object, default: null },
        managedPools: { type: Array, default: () => [] },
    },
    emits: ['open-accounts'],
    setup(props) {
        const vue = props.vue || globalThis.Vue;
        const ctrl = useDirectoryReview(vue, { client: props.client || undefined, managedPools: props.managedPools });
        const allowed = !props.currentUser || props.currentUser.is_admin !== false;
        if (allowed) ctrl.load();
        else {
            ctrl.state.status = surfaceFor('warning').status;
            ctrl.state.message = REASONS.permission;
        }
        const hasChanges = () => Object.values(ctrl.groups()).some((list) => list.length > 0);
        return { ctrl, state: ctrl.state, labels: LABELS, allowed, key: { a: actionKey, m: memberKey }, hasChanges };
    },
    template: `
<section role="region" class="cadm-panel" aria-labelledby="clinic-dir-title" :aria-busy="state.status === 'processing'">
  <header class="cadm-panel__head">
    <div>
      <h2 id="clinic-dir-title" class="cadm-title">${LABELS.title}</h2>
      <p class="cadm-desc">${LABELS.description}</p>
    </div>
    <div class="cadm-panel__tools">
      <button type="button" class="btn btn--primary" :disabled="!allowed || state.status === 'processing'" @click="ctrl.load()"><i class="fas fa-sync-alt" aria-hidden="true"></i><span>{{ state.message && !state.stale ? '${LABELS.retry}' : '${LABELS.reload}' }}</span></button>
      <button type="button" class="btn btn--ghost" @click="$emit('open-accounts')"><i class="fas fa-users-cog" aria-hidden="true"></i><span>${LABELS.local}</span></button>
    </div>
  </header>
  <p role="note" class="banner banner--warn"><i class="fas fa-exclamation-triangle" aria-hidden="true"></i><span>${LABELS.unresolved}</span></p>
  <p class="banner banner--info"><i class="fas fa-info-circle" aria-hidden="true"></i><span>${LABELS.archiveOnly}</span></p>
  <p v-if="state.message" role="alert" class="banner banner--danger"><i class="fas fa-exclamation-circle" aria-hidden="true"></i><span>{{ state.message }}</span></p>
  <div v-if="state.preview && !state.stale" class="cadm-section">
    <p class="cadm-hint">{{ state.preview.source }} · {{ state.preview.version }}</p>
    <p v-if="!hasChanges()" class="cadm-empty">${LABELS.empty}</p>
    <section class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.additions} <span class="pill-status pill-status--pending">{{ ctrl.groups().additions.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.additions}" class="cadm-rows" :class="{ 'cadm-rows--empty': !ctrl.groups().additions.length }">
        <li v-if="!ctrl.groups().additions.length">–</li>
        <li v-for="a in ctrl.groups().additions" :key="key.a(a)"><span>{{ a.detail || a.object_id }}</span><span class="pill-status pill-status--warn">${LABELS.linkRequired}</span></li>
      </ul>
    </section>
    <section class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.modifications} <span class="pill-status pill-status--pending">{{ ctrl.groups().modifications.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.modifications}" class="cadm-rows" :class="{ 'cadm-rows--empty': !ctrl.groups().modifications.length }">
        <li v-if="!ctrl.groups().modifications.length">–</li>
        <li v-for="a in ctrl.groups().modifications" :key="key.a(a)"><label class="cadm-check"><input type="checkbox" :checked="state.selected[key.a(a)]" @change="ctrl.toggle(key.a(a))" @keydown="ctrl.onKey(key.a(a), $event)"> {{ a.detail || a.object_id }}</label></li>
      </ul>
    </section>
    <section class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.disabled} <span class="pill-status pill-status--pending">{{ ctrl.groups().disabled.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.disabled}" class="cadm-rows" :class="{ 'cadm-rows--empty': !ctrl.groups().disabled.length }">
        <li v-if="!ctrl.groups().disabled.length">–</li>
        <li v-for="a in ctrl.groups().disabled" :key="key.a(a)"><label class="cadm-check"><input type="checkbox" :checked="state.selected[key.a(a)]" @change="ctrl.toggle(key.a(a))" @keydown="ctrl.onKey(key.a(a), $event)"> {{ a.detail || a.object_id }}</label></li>
      </ul>
    </section>
    <section class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.memberships} <span class="pill-status pill-status--pending">{{ ctrl.groups().memberships.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.memberships}" class="cadm-rows" :class="{ 'cadm-rows--empty': !ctrl.groups().memberships.length }">
        <li v-if="!ctrl.groups().memberships.length">–</li>
        <li v-for="m in ctrl.groups().memberships" :key="key.m(m)"><label class="cadm-check"><input type="checkbox" :checked="state.selected[key.m(m)]" @change="ctrl.toggle(key.m(m))" @keydown="ctrl.onKey(key.m(m), $event)"> {{ m.kind }} {{ m.pool_kind }} {{ m.pool_id }} ({{ m.subject_id }})</label></li>
      </ul>
    </section>
    <div class="cadm-form__actions">
      <button type="button" class="btn btn--primary" @click="ctrl.review()">${LABELS.review}</button>
    </div>
    <div v-if="state.confirming" role="alertdialog" aria-label="${LABELS.confirmTitle}" class="banner banner--warn cadm-dialog" @keydown.esc="ctrl.cancel()">
      <h3 class="cadm-subtitle">${LABELS.confirmTitle}</h3>
      <button type="button" class="btn btn--danger btn--sm" @click="ctrl.apply()">${LABELS.confirm}</button>
      <button type="button" class="btn btn--ghost btn--sm" @click="ctrl.cancel()">${LABELS.cancel}</button>
    </div>
  </div>
</section>`,
};

export function registerDirectoryReview(app) {
    app.component('clinic-directory-review', DirectoryReview);
    return app;
}
