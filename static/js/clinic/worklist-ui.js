/**
 * Role-scoped clinic worklist UI module.
 * Renders author and staff views with status totals, counts, and audio duration.
 * Reuses ui-token-contract and api-client, keeping all clinical data in memory.
 */

import {
    FOCUS_INDICATOR,
    STATUSES,
    TOKEN_CONTRACT_VERSION,
    TokenContractError,
    surfaceFor,
} from './tokens.js';
import { createClinicApiClient } from './api-client.js';

export const WORKLIST_UI_VERSION = 1;

export const ROLE_AUTHOR = 'author';
export const ROLE_STAFF = 'staff';
export const ROLE_TYPIST = 'typist';

export const STATUS_LABELS = Object.freeze({
    aufgabe: 'Aufgabe',
    neu_angelegt: 'Neu angelegt',
    vorbereitung_offline: 'In Vorbereitung [Offline]',
    vorbereitung_online: 'In Vorbereitung [Online]',
    zu_schreiben: 'Zu schreiben',
    zu_korrigieren: 'Zu korrigieren',
    in_bearbeitung: 'In Bearbeitung',
    abzunehmen: 'Abzunehmen',
    abgenommen: 'Abgenommen',
    fertiggestellt: 'Fertiggestellt',
    zu_archivieren: 'Zu archivieren',
    zu_loeschen: 'Zu Löschen',
});

export const PRIORITY_LABELS = Object.freeze({
    0: 'Bei Gelegenheit',
    1: 'Normal',
    128: 'Dringend',
});

const STATUS_TO_SURFACE = Object.freeze({
    aufgabe: 'pending',
    neu_angelegt: 'pending',
    vorbereitung_offline: 'pending',
    vorbereitung_online: 'pending',
    zu_schreiben: 'pending',
    zu_korrigieren: 'pending',
    in_bearbeitung: 'processing',
    abzunehmen: 'pending',
    abgenommen: 'success',
    fertiggestellt: 'success',
    zu_archivieren: 'warning',
    zu_loeschen: 'warning',
    done: 'success',
    completed: 'success',
    failed: 'error',
    error: 'error',
});

export function formatPriority(code) {
    if (code === null || code === undefined || code === '') return 'Normal';
    const key = Number(code);
    return Object.prototype.hasOwnProperty.call(PRIORITY_LABELS, key) ? PRIORITY_LABELS[key] : 'Normal';
}

export function formatAudioDuration(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function resolveStatusSurface(status, context = {}) {
    const key = String(status || '').trim().toLowerCase();
    const mapped = Object.prototype.hasOwnProperty.call(STATUS_TO_SURFACE, key) ? STATUS_TO_SURFACE[key] : key;
    const surface = surfaceFor(mapped, context);
    const label = Object.prototype.hasOwnProperty.call(STATUS_LABELS, key) ? STATUS_LABELS[key] : surface.label;
    return { ...surface, workflowStatus: key, label };
}

export function filterRowsByRole(items, { role, userId } = {}) {
    if (!Array.isArray(items)) return [];
    if (!role && userId == null) return [...items];
    const normalizedRole = String(role || '').toLowerCase();
    const uid = userId != null ? Number(userId) : null;

    return items.filter((row) => {
        if (!row || typeof row !== 'object') return false;
        const authorId = row.author_user_id != null ? Number(row.author_user_id) : (row.author != null ? Number(row.author) : null);
        const assigneeId = row.assignee_user_id != null ? Number(row.assignee_user_id) : (row.assignee != null ? Number(row.assignee) : null);

        if (normalizedRole === ROLE_AUTHOR) {
            return uid == null || authorId === uid;
        }
        if (normalizedRole === ROLE_STAFF || normalizedRole === ROLE_TYPIST) {
            return uid == null || assigneeId == null || assigneeId === uid;
        }
        return true;
    });
}

export function computeStatusTotals(items, context = {}) {
    if (!Array.isArray(items)) {
        return { groups: [], totalCount: 0, totalAudioSeconds: 0, formattedTotalDuration: formatAudioDuration(0) };
    }
    const buckets = new Map();
    let grandTotalSeconds = 0;

    for (const row of items) {
        if (!row || typeof row !== 'object') continue;
        const rawStatus = String(row.status || 'aufgabe').trim().toLowerCase();
        const duration = Math.max(0, Number(row.duration_seconds || row.audio_seconds || 0));
        grandTotalSeconds += duration;

        if (!buckets.has(rawStatus)) {
            buckets.set(rawStatus, { status: rawStatus, count: 0, totalAudioSeconds: 0, dictationIds: [] });
        }
        const b = buckets.get(rawStatus);
        b.count += 1;
        b.totalAudioSeconds += duration;
        const id = row.dictation_id || row.id;
        if (id) b.dictationIds.push(String(id));
    }

    const groups = [];
    for (const [status, data] of buckets.entries()) {
        let surface = null;
        try {
            surface = resolveStatusSurface(status, context);
        } catch (error) {
            if (error instanceof TokenContractError && (error.code === 'unauthorized' || error.code === 'stale_version')) {
                throw error;
            }
            surface = {
                status: 'normal',
                label: STATUS_LABELS[status] || status,
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                border: 'var(--border-primary)',
                tokens: Object.freeze({ background: '--bg-secondary', color: '--text-primary', border: '--border-primary' }),
                focus: FOCUS_INDICATOR,
            };
        }
        groups.push({
            status,
            label: (surface && surface.label) || STATUS_LABELS[status] || status,
            count: data.count,
            totalAudioSeconds: data.totalAudioSeconds,
            formattedDuration: formatAudioDuration(data.totalAudioSeconds),
            finished: status === 'fertiggestellt' || status === 'done' || status === 'success',
            dictationIds: Object.freeze([...data.dictationIds]),
            surface,
        });
    }

    return {
        groups,
        totalCount: items.length,
        totalAudioSeconds: grandTotalSeconds,
        formattedTotalDuration: formatAudioDuration(grandTotalSeconds),
    };
}

export function getOpenMode(row, role) {
    if (row && row.open_mode) return row.open_mode;
    const norm = String(role || '').toLowerCase();
    if (norm === ROLE_AUTHOR) return 'author_case';
    return (row && row.status === 'zu_schreiben') ? 'player' : 'correction';
}

function resolveReactiveHelpers(options = {}) {
    const vue = options.Vue || (typeof window !== 'undefined' ? window.Vue : null);
    const ref = options.ref || (vue && vue.ref) || ((init) => {
        let val = init;
        return { get value() { return val; }, set value(v) { val = v; } };
    });
    const computed = options.computed || (vue && vue.computed) || ((fn) => ({ get value() { return fn(); } }));
    return { ref, computed };
}

export function useWorklist(options = {}) {
    const { ref, computed } = resolveReactiveHelpers(options);
    const client = options.apiClient || createClinicApiClient();
    const tokenVersion = options.tokenVersion !== undefined ? options.tokenVersion : TOKEN_CONTRACT_VERSION;

    const currentRole = ref(options.role || ROLE_AUTHOR);
    const currentUserId = ref(options.userId != null ? Number(options.userId) : null);
    const currentAccount = ref(options.accountId != null ? String(options.accountId) : null);

    const initialFiltered = filterRowsByRole(options.initialRows || [], {
        role: currentRole.value,
        userId: currentUserId.value,
    });

    const rows = ref(initialFiltered);
    const totals = ref(computeStatusTotals(initialFiltered, { version: tokenVersion }));
    const selectedRow = ref(null);
    const selectedIndex = ref(-1);
    const isLoading = ref(false);
    const isPermissionLost = ref(false);
    const errorMessage = ref(null);

    const isEmpty = computed(() => !isLoading.value && rows.value.length === 0);

    const clearClinicalData = () => {
        rows.value = [];
        totals.value = computeStatusTotals([], { version: tokenVersion });
        selectedRow.value = null;
        selectedIndex.value = -1;
    };

    const selectRow = (target) => {
        if (typeof target === 'number') {
            if (target >= 0 && target < rows.value.length) {
                selectedIndex.value = target;
                selectedRow.value = rows.value[target];
            }
        } else if (target && typeof target === 'object') {
            const idx = rows.value.indexOf(target);
            if (idx !== -1) {
                selectedIndex.value = idx;
                selectedRow.value = target;
            } else {
                const targetId = target.dictation_id || target.id;
                const found = rows.value.findIndex((r) => (r.dictation_id || r.id) === targetId);
                if (found !== -1) {
                    selectedIndex.value = found;
                    selectedRow.value = rows.value[found];
                } else {
                    selectedRow.value = target;
                    selectedIndex.value = -1;
                }
            }
        } else {
            selectedRow.value = null;
            selectedIndex.value = -1;
        }
    };

    const selectNext = () => {
        if (!rows.value.length) return;
        selectRow(selectedIndex.value < rows.value.length - 1 ? selectedIndex.value + 1 : 0);
    };

    const selectPrevious = () => {
        if (!rows.value.length) return;
        selectRow(selectedIndex.value > 0 ? selectedIndex.value - 1 : rows.value.length - 1);
    };

    const openRow = (row) => {
        const target = row || selectedRow.value;
        if (!target) return null;
        const mode = getOpenMode(target, currentRole.value);
        if (typeof options.onOpen === 'function') options.onOpen(target, mode);
        return { row: target, mode };
    };

    const onKeyDown = (event, row) => {
        if (!event) return;
        if (event.key === 'ArrowDown') {
            if (typeof event.preventDefault === 'function') event.preventDefault();
            selectNext();
        } else if (event.key === 'ArrowUp') {
            if (typeof event.preventDefault === 'function') event.preventDefault();
            selectPrevious();
        } else if (event.key === 'Enter' || event.key === ' ') {
            if (typeof event.preventDefault === 'function') event.preventDefault();
            if (row) selectRow(row);
            return openRow(selectedRow.value || row);
        }
    };

    const switchAccount = (nextAccountId) => {
        const nextId = nextAccountId != null ? String(nextAccountId) : null;
        currentAccount.value = nextId;
        client.switchContext({ accountId: nextId, dictationId: null });
        clearClinicalData();
        isPermissionLost.value = false;
        errorMessage.value = null;
    };

    const loadWorklist = async (params = {}) => {
        isLoading.value = true;
        errorMessage.value = null;
        isPermissionLost.value = false;

        try {
            const query = new URLSearchParams();
            if (params.page) query.set('page', String(params.page));
            if (params.pageSize) query.set('page_size', String(params.pageSize));
            if (params.filterId) query.set('filter_id', String(params.filterId));
            if (params.search) query.set('search', String(params.search));

            const qs = query.toString();
            const response = await client.request(`/worklist/rows${qs ? '?' + qs : ''}`);

            if (response.outcome === 'permission' || response.status === 401 || response.status === 403) {
                clearClinicalData();
                isPermissionLost.value = true;
                errorMessage.value = response.message || 'Keine Berechtigung für diese Aktion.';
                return response;
            }

            if (response.outcome === 'ok' && response.data) {
                const raw = response.data.items || response.data;
                const items = Array.isArray(raw) ? raw : [];
                const scoped = filterRowsByRole(items, { role: currentRole.value, userId: currentUserId.value });
                rows.value = scoped;
                totals.value = computeStatusTotals(scoped, { version: tokenVersion });
                if (selectedRow.value) selectRow(selectedRow.value);
                return response;
            }

            errorMessage.value = response.message || 'Die Verbindung ist fehlgeschlagen.';
            return response;
        } catch (error) {
            if (error instanceof TokenContractError && error.code === 'unauthorized') {
                clearClinicalData();
                isPermissionLost.value = true;
                errorMessage.value = 'Keine Berechtigung für diese Aktion.';
            } else {
                errorMessage.value = (error && error.message) || 'Fehler beim Laden der Arbeitsliste.';
            }
            return { outcome: 'error', error };
        } finally {
            isLoading.value = false;
        }
    };

    return {
        rows, totals, selectedRow, selectedIndex, isLoading, isEmpty, isPermissionLost, errorMessage,
        currentAccount, currentRole, currentUserId, focusIndicator: FOCUS_INDICATOR,
        selectRow, selectNext, selectPrevious, openRow, onKeyDown, switchAccount, loadWorklist, clearClinicalData,
    };
}

export const ClinicWorklist = Object.freeze({
    name: 'ClinicWorklist',
    props: {
        role: { type: String, default: ROLE_AUTHOR },
        userId: { type: [Number, String], default: null },
        accountId: { type: [Number, String], default: null },
        tokenVersion: { type: Number, default: TOKEN_CONTRACT_VERSION },
        initialRows: { type: Array, default: () => [] },
        apiClient: { type: Object, default: null },
    },
    setup(props, { emit }) {
        const worklist = useWorklist({
            role: props.role,
            userId: props.userId,
            accountId: props.accountId,
            tokenVersion: props.tokenVersion,
            initialRows: props.initialRows,
            apiClient: props.apiClient,
            onOpen: (row, mode) => emit('open', { row, mode }),
        });

        return {
            ...worklist,
            formatPriority,
            formatAudioDuration,
            resolveStatusSurface: (s) => resolveStatusSurface(s, { version: props.tokenVersion }),
        };
    },
    template: `
<div class="clinic-worklist" data-testid="clinic-worklist">
    <header class="clinic-worklist-totals" data-testid="status-totals" role="region" aria-label="Statusübersicht">
        <div class="clinic-totals-summary">
            <span class="clinic-total-count">{{ totals.totalCount }} Diktate</span>
            <span class="clinic-total-duration">({{ totals.formattedTotalDuration }})</span>
        </div>
        <div class="clinic-totals-groups">
            <span v-for="g in totals.groups" :key="g.status" class="clinic-status-badge" :data-status="g.status"
                  :style="{ background: g.surface.background, color: g.surface.color, border: '1px solid ' + g.surface.border, outline: g.surface.focus.outline }">
                {{ g.label }}: {{ g.count }} ({{ g.formattedDuration }})
            </span>
        </div>
    </header>
    <div v-if="isLoading" class="clinic-worklist-loading" data-testid="loading-state" role="status" aria-live="polite">Laden...</div>
    <div v-else-if="isPermissionLost" class="clinic-worklist-permission-loss" data-testid="permission-loss-state" role="alert">{{ errorMessage || 'Keine Berechtigung für diese Aktion.' }}</div>
    <div v-else-if="isEmpty" class="clinic-worklist-empty" data-testid="empty-state">Keine Diktate vorhanden.</div>
    <table v-else class="clinic-worklist-table" role="grid" aria-label="Arbeitsliste">
        <thead>
            <tr>
                <th scope="col">Titel</th><th scope="col">Status</th><th scope="col">Autor</th>
                <th scope="col">Priorität</th><th scope="col">Frist</th><th scope="col">Bearbeiter</th>
                <th scope="col">Erstellt</th><th scope="col">Dauer</th>
            </tr>
        </thead>
        <tbody>
            <tr v-for="(row, idx) in rows" :key="row.dictation_id || row.id || idx" tabindex="0" class="clinic-worklist-row"
                :class="{ 'is-selected': selectedIndex === idx }" :aria-selected="selectedIndex === idx"
                @click="selectRow(row)" @dblclick="openRow(row)" @keydown="onKeyDown($event, row)">
                <td>{{ row.title || '-' }}</td>
                <td><span class="clinic-row-status">{{ (resolveStatusSurface(row.status)).label }}</span></td>
                <td>{{ row.author_user_id || row.author || '-' }}</td>
                <td>{{ formatPriority(row.priority) }}</td>
                <td>{{ row.deadline_at || row.deadline || '-' }}</td>
                <td>{{ row.assignee_user_id || row.assignee || 'Unzugewiesen' }}</td>
                <td>{{ row.created_at || row.created || '-' }}</td>
                <td>{{ formatAudioDuration(row.duration_seconds || row.audio_seconds) }}</td>
            </tr>
        </tbody>
    </table>
</div>`.trim(),
});
