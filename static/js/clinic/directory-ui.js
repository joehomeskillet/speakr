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
const GROUP_KIND = { author: 'Diktiergruppe', staff: 'Schreibgruppe' };
export const WINDOWS_GAP = 'WG-ADM-009 offen. Lokale Windows-Konten dieses Arbeitsplatzes sind im Browser nicht verfügbar.';

export const LABELS = Object.freeze({
    title: 'Verzeichnisabgleich',
    description: 'Prüfen Sie die Unterschiede zwischen dem Verzeichnis und den Appkonten. Erst Ihre Bestätigung übernimmt die Auswahl.',
    reload: 'Vorschau laden',
    additions: 'Neue Personen im Verzeichnis',
    modifications: 'Änderungen',
    disabled: 'Konten zur Deaktivierung',
    memberships: 'Mitgliedschaften in Arbeitsgruppen',
    empty: 'Keine Änderungen.',
    review: 'Auswahl prüfen',
    confirmTitle: 'Änderungen bestätigen',
    confirm: 'Auswahl übernehmen',
    cancel: 'Abbrechen',
    retry: 'Erneut versuchen',
    local: 'Lokale Konten verwalten',
    providers: 'Anbieterstatus',
    unresolved: WINDOWS_GAP + ' IWA, LDAP und Entra sind nicht verifiziert.',
    linkRequired: 'Das Verzeichnis unterstützt hier keine Kontoanlage.',
    link: 'Appkonto anlegen',
    linkConfirm: 'Anlegen und verknüpfen',
    linkTitle: 'Kontoanlage bestätigen',
});

const REASONS = Object.freeze({
    stale: 'Der Verzeichnisstand hat sich geändert. Bitte die Vorschau neu laden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    unavailable: 'Das Verzeichnis ist nicht verfügbar.',
    nothing_selected: 'Es ist nichts ausgewählt.',
    no_preview: 'Es liegt keine Vorschau vor.',
    not_confirmed: 'Die Bestätigung fehlt.',
    processing: 'Der Vorgang läuft bereits.',
    link_uncertain: 'Die Kontoanlage konnte nicht bestätigt werden. Laden Sie die Vorschau neu, um den Stand zu prüfen.',
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

function actionText(action) {
    const { kind, account_label: account, before, after } = action;
    if (!account) return action.detail || action.object_id;
    if (kind === 'create_pending_user') return `${after} (${account})`;
    if (kind === 'deactivate') return `${account} deaktivieren`;
    const field = kind === 'update_email' ? 'E-Mail-Adresse' : 'Anzeigename';
    return `${field} für ${account} von ${before || 'leer'} auf ${after} ändern`;
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
    const state = wrap({
        status: 'normal',
        preview: null, // { version, hash, source, ... }; memory only
        selected: {}, // key -> true
        confirming: false,
        linking: null,
        managedPools: Array.isArray(opts.managedPools) ? opts.managedPools : [],
        stale: false,
        message: '',
        success: '',
        capability: null,
    });

    function setStatus(status) {
        state.status = surfaceFor(status).status;
    }

    function fail(row, code = failureCode(row)) {
        if (code === 'stale' || code === 'link_uncertain') {
            state.stale = true;
            state.confirming = false;
            state.linking = null;
        }
        setStatus(code === 'failure' || code === 'unavailable' ? 'error' : 'warning');
        state.message = REASONS[code];
        return refuse(code);
    }

    async function load() {
        if (state.status === 'processing') return refuse('processing');
        cancel();
        setStatus('processing');
        state.preview = null;
        state.message = '';
        state.success = '';
        const cap = await client.request(BASE + '/capability');
        if (cap.outcome === 'ok') state.capability = cap.data;
        const row = await client.request(BASE + '/preview', { method: 'POST', body: { managed_pools: state.managedPools } });
        // A failed reload leaves no usable preview.
        if (row.outcome !== 'ok') return fail(row);
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
        if (state.status === 'processing' || state.stale || !selectable(key)) return;
        if (state.selected[key]) delete state.selected[key];
        else state.selected[key] = true;
        cancel();
    }

    function choosePool({ kind, id }, checked) {
        if (state.status === 'processing') return;
        const keep = state.managedPools.filter((pool) => pool.kind !== kind || pool.id !== id);
        state.managedPools = checked ? [...keep, { kind, id }] : keep;
        state.preview = null;
        state.success = '';
        cancel();
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
                .filter((a) => state.selected[actionKey(a)]),
            memberships: g.memberships.filter((m) => state.selected[memberKey(m)]),
        };
    }

    function lastMemberships(memberships) {
        const remaining = new Map();
        for (const m of memberships) {
            const key = `${m.user_id}:${m.pool_kind}`;
            const count = remaining.get(key)?.count ?? m.current_count;
            remaining.set(key, { member: m, count: count + (m.kind === 'add' ? 1 : -1) });
        }
        return [...remaining.values()].filter(({ count }) => count === 0).map(({ member }) => member);
    }

    /** First step: show what will be applied. No request is made. */
    function review(objectId) {
        if (state.status === 'processing') return refuse('processing');
        if (!state.preview) return refuse('no_preview');
        if (state.stale) return refuse('stale');
        const link = objectId === undefined || !state.preview.link_new_available ? null
            : groupPreview(state.preview).additions.find((a) => a.object_id === objectId);
        const { actions, memberships } = selection();
        const count = objectId === undefined ? actions.length + memberships.length : Number(Boolean(link));
        if (!count) return refuse('nothing_selected');
        state.linking = link;
        state.message = '';
        state.confirming = true;
        return { ok: true, count };
    }

    function cancel() {
        if (state.status === 'processing') return;
        state.confirming = false;
        state.linking = null;
    }

    /** Second step: explicit confirmation bound to version and hash. */
    async function apply() {
        if (!state.preview) return refuse('no_preview');
        if (state.stale) return refuse('stale');
        if (!state.confirming) return refuse('not_confirmed');
        if (state.status === 'processing') return refuse('processing');
        const { version, hash } = state.preview;
        const { actions, memberships } = selection();
        const link = state.linking;
        const changes = link ? { object_id: link.object_id } : {
            actions: actions.map(({ kind, object_id }) => ({ kind, object_id })),
            memberships: memberships.map(({ kind, subject_id, pool_kind, pool_id }) => ({ kind, subject_id, pool_kind, pool_id })),
        };
        setStatus('processing');
        state.message = '';
        const row = await client.request(BASE + (link ? '/link-new' : '/apply'), {
            method: 'POST',
            body: { version, hash, managed_pools: state.managedPools, ...changes },
        });
        if (row.outcome !== 'ok') {
            const code = failureCode(row);
            return fail(row, link && row.outcome === 'network' && code === 'failure' ? 'link_uncertain' : code);
        }
        // Applied: the old preview is obsolete, a reload is required for the next round.
        state.preview = null;
        state.selected = {};
        state.success = link ? 'Das Appkonto wurde angelegt und verknüpft. Es wartet auf die Freischaltung.'
            : 'Die gewählten Änderungen wurden übernommen.';
        setStatus('success');
        cancel();
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
        state.linking = null;
        state.stale = false;
        state.message = '';
        state.success = '';
        state.capability = null;
        state.status = 'normal';
        client.switchContext({ accountId: null, dictationId: null });
    }

    return { state, load, toggle, review, cancel, apply, onKey, reset, choosePool, selection, lastMemberships,
        groups: () => groupPreview(state.preview) };
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
        const poolOptions = () => ctrl.state.capability?.pool_options || [];
        const poolChosen = (option) => ctrl.state.managedPools.some((p) => p.kind === option.kind && p.id === option.id);
        const poolKind = (kind) => GROUP_KIND[kind];
        function membershipText(m) {
            const pool = poolOptions().find((p) => p.id === m.pool_id && p.kind === m.pool_kind);
            const label = `${poolKind(m.pool_kind)} ${pool?.name || m.pool_id}`;
            return m.kind === 'add' ? `${m.account_label} zu ${label} hinzufügen`
                : `${m.account_label} aus ${label} entfernen`;
        }
        async function openConfirmation(dialog, objectId) {
            if (!ctrl.review(objectId).ok) return;
            await vue.nextTick();
            dialog.showModal();
            dialog.querySelector('h3').focus();
        }
        function closeConfirmation(dialog) {
            if (ctrl.state.status === 'processing') return;
            ctrl.cancel();
            dialog.close();
        }
        async function submitConfirmation(dialog, reload) {
            await ctrl.apply();
            if (!ctrl.state.confirming) {
                dialog.close();
                await vue.nextTick();
                reload.focus();
            } else {
                await vue.nextTick();
                dialog.querySelector('[role="alert"]')?.focus();
            }
        }
        return { ctrl, state: ctrl.state, labels: LABELS, allowed, key: { a: actionKey, m: memberKey },
            hasChanges, poolOptions, poolChosen, poolKind, actionText, membershipText,
            openConfirmation, closeConfirmation, submitConfirmation };
    },
    template: `
<section role="region" class="cadm-panel clinic-directory-review" aria-labelledby="clinic-dir-title" :aria-busy="state.status === 'processing'">
  <header class="cadm-panel__head">
    <div>
      <h2 id="clinic-dir-title" class="cadm-title clinic-directory-heading">${LABELS.title}</h2>
      <p class="cadm-desc">${LABELS.description}</p>
    </div>
    <div class="cadm-panel__tools">
      <button ref="reload" type="button" class="btn" :class="state.preview ? 'btn--ghost' : 'btn--primary'" :disabled="!allowed || state.status === 'processing'" @click="ctrl.load()"><i class="fas fa-sync-alt" aria-hidden="true"></i><span>{{ state.message && !state.stale ? '${LABELS.retry}' : '${LABELS.reload}' }}</span></button>
      <button type="button" class="btn btn--ghost" @click="$emit('open-accounts')"><i class="fas fa-users-cog" aria-hidden="true"></i><span>${LABELS.local}</span></button>
    </div>
  </header>
  <p role="note" class="banner banner--warn"><i class="fas fa-exclamation-triangle" aria-hidden="true"></i><span>${LABELS.unresolved}</span></p>
  <p v-if="state.message && !state.confirming" role="alert" class="banner banner--danger">{{ state.message }}</p>
  <p v-if="state.success" role="status" class="banner banner--info">{{ state.success }}</p>
  <p v-if="state.preview && !state.stale && !hasChanges()" class="cadm-empty">${LABELS.empty}</p>
  <fieldset v-if="poolOptions().length" class="cadm-fieldset" :disabled="state.status === 'processing'">
    <legend>Arbeitsgruppen für den Abgleich</legend>
    <div class="w-full">Wählen Sie nur Gruppen, deren Mitglieder dieses Verzeichnis verwaltet. Laden Sie danach die Vorschau.</div>
    <label v-for="p in poolOptions()" :key="p.kind + ':' + p.id" class="cadm-check">
      <input type="checkbox" :checked="poolChosen(p)" @change="ctrl.choosePool(p, $event.target.checked)">
      <span>{{ poolKind(p.kind) }} {{ p.name }}</span>
    </label>
  </fieldset>
  <div v-if="state.preview && !state.stale && hasChanges()" class="cadm-section">
    <section v-if="ctrl.groups().additions.length" class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.additions} <span class="pill-status pill-status--pending">{{ ctrl.groups().additions.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.additions}" class="cadm-rows">
        <li v-for="a in ctrl.groups().additions" :key="key.a(a)">
          <span>{{ actionText(a) }}</span>
          <button v-if="state.preview.link_new_available" type="button" class="btn btn--ghost"
            :aria-label="labels.link + ' für ' + actionText(a)" @click="openConfirmation($refs.confirmDialog, a.object_id)">${LABELS.link}</button>
          <span v-else>${LABELS.linkRequired}</span>
        </li>
      </ul>
    </section>
    <template v-for="group in ['modifications', 'disabled']" :key="group">
      <section v-if="ctrl.groups()[group].length" class="cadm-section">
        <h3 class="cadm-subtitle">{{ labels[group] }} <span class="pill-status pill-status--pending">{{ ctrl.groups()[group].length }}</span></h3>
        <ul role="list" :aria-label="labels[group]" class="cadm-rows">
          <li v-for="a in ctrl.groups()[group]" :key="key.a(a)">
            <label class="cadm-check"><input type="checkbox" :checked="state.selected[key.a(a)]" @change="ctrl.toggle(key.a(a))">
              <span>{{ actionText(a) }}</span>
            </label>
          </li>
        </ul>
      </section>
    </template>
    <section v-if="ctrl.groups().memberships.length" class="cadm-section">
      <h3 class="cadm-subtitle">${LABELS.memberships} <span class="pill-status pill-status--pending">{{ ctrl.groups().memberships.length }}</span></h3>
      <ul role="list" aria-label="${LABELS.memberships}" class="cadm-rows">
        <li v-for="m in ctrl.groups().memberships" :key="key.m(m)">
          <label class="cadm-check"><input type="checkbox" :checked="state.selected[key.m(m)]" @change="ctrl.toggle(key.m(m))">
            <span>{{ membershipText(m) }}</span>
          </label>
        </li>
      </ul>
    </section>
    <div v-if="ctrl.groups().modifications.length || ctrl.groups().disabled.length || ctrl.groups().memberships.length" class="cadm-form__actions">
      <button type="button" class="btn btn--primary" :disabled="!Object.keys(state.selected).length" @click="openConfirmation($refs.confirmDialog)">${LABELS.review}</button>
    </div>
  </div>
  <dialog ref="confirmDialog" role="alertdialog" aria-labelledby="clinic-dir-confirm-title" :aria-busy="state.status === 'processing'"
    @cancel.prevent="closeConfirmation($refs.confirmDialog)">
    <template v-if="state.confirming">
      <h3 id="clinic-dir-confirm-title" class="cadm-subtitle" tabindex="-1">{{ state.linking ? labels.linkTitle : labels.confirmTitle }}</h3>
      <p v-if="state.linking">Für {{ actionText(state.linking) }} wird ein Appkonto angelegt und mit dem Verzeichniseintrag verbunden. Das Konto wartet danach auf die Freischaltung.</p>
      <template v-else>
        <ul class="cadm-rows cadm-rows--plain" aria-label="Gewählte Änderungen">
          <li v-for="a in ctrl.selection().actions" :key="key.a(a)">{{ actionText(a) }}</li>
          <li v-for="m in ctrl.selection().memberships" :key="key.m(m)">{{ membershipText(m) }}</li>
        </ul>
        <p v-for="m in ctrl.lastMemberships(ctrl.selection().memberships)" :key="key.m(m)" class="banner banner--warn">
          {{ m.account_label }} verliert die letzte Zuordnung zu einer {{ poolKind(m.pool_kind) }}.
        </p>
      </template>
      <p v-if="state.message" tabindex="-1" role="alert" class="banner banner--danger">{{ state.message }}</p>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn--ghost" :disabled="state.status === 'processing'" @click="closeConfirmation($refs.confirmDialog)">${LABELS.cancel}</button>
        <button type="button" class="btn btn--primary" :disabled="state.status === 'processing'" @click="submitConfirmation($refs.confirmDialog, $refs.reload)">{{ state.linking ? labels.linkConfirm : labels.confirm }}</button>
      </div>
    </template>
  </dialog>
</section>`,
};

export function registerDirectoryReview(app) {
    app.component('clinic-directory-review', DirectoryReview);
    return app;
}
