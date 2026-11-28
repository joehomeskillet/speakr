/**
 * Worklist refresh layer: manual refresh, snapshot or change-feed polling,
 * out-of-order response discard and logout teardown on top of useWorklist.
 * Reuses ui-token-contract and api-client; clinical data stays in memory.
 */

import { FOCUS_INDICATOR, TOKEN_CONTRACT_VERSION, TokenContractError } from './tokens.js';
import { createClinicApiClient } from './api-client.js';
import {
    ROLE_AUTHOR,
    computeStatusTotals,
    filterRowsByRole,
    formatAudioDuration,
    formatPriority,
    resolveStatusSurface,
    useWorklist,
} from './worklist-ui.js';

export const WORKLIST_REFRESH_UI_VERSION = 1;

const CHANGES_URL = '/api/v1/recordings/changes';
const MAX_PRIME_PAGES = 200;
const MAX_BACKOFF_FACTOR = 8;
const MESSAGE_FAILED = 'Aktualisierung fehlgeschlagen. Die angezeigten Daten können veraltet sein.';
const MESSAGE_DENIED = 'Keine Berechtigung für diese Aktion.';

export function formatLastUpdate(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return 'Noch nicht aktualisiert';
    const pad = (n) => String(n).padStart(2, '0');
    return `Zuletzt aktualisiert: ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function rowId(row) {
    const id = row && (row.dictation_id != null ? row.dictation_id : row.id);
    return id == null ? null : String(id);
}

/** Default reader of the recording-change feed (same-origin session cookie, no storage). */
export async function fetchRecordingChanges(cursor, { signal, limit = 200 } = {}) {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set('cursor', cursor);
    const fetchFn = (typeof window !== 'undefined' && typeof window.originalFetch === 'function')
        ? window.originalFetch : globalThis.fetch;
    try {
        const response = await fetchFn(`${CHANGES_URL}?${query.toString()}`, {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json' },
            signal,
        });
        if (response.status === 401 || response.status === 403) return { outcome: 'permission', status: response.status };
        if (response.status === 410) return { outcome: 'expired', status: 410 };
        if (!response.ok) return { outcome: 'network', status: response.status };
        return { outcome: 'ok', status: response.status, data: await response.json() };
    } catch (error) {
        if (error && error.name === 'AbortError') return { outcome: 'cancelled' };
        return { outcome: 'network' };
    }
}

export function useWorklistRefresh(options = {}) {
    const client = options.apiClient || createClinicApiClient();
    const worklist = options.worklist || useWorklist({ ...options, apiClient: client });
    const tokenVersion = options.tokenVersion !== undefined ? options.tokenVersion : TOKEN_CONTRACT_VERSION;
    const intervalMs = options.intervalMs !== undefined ? options.intervalMs : 30000;
    const { fetchChanges = fetchRecordingChanges } = options;
    const { rowsPath, onSnapshot } = options;
    const reloadOnChanges = fetchChanges === fetchRecordingChanges || Boolean(rowsPath);
    const mapRecording = options.mapRecording || ((recording) => recording);
    const now = options.now || (() => new Date());

    const make = options.ref || (typeof window !== 'undefined' && window.Vue && window.Vue.ref)
        || ((init) => { let v = init; return { get value() { return v; }, set value(n) { v = n; } }; });
    const lastUpdate = make(null);
    const lastUpdateLabel = make(formatLastUpdate(null));
    const hasLoaded = make(false);
    const isRefreshing = make(false);
    const refreshError = make(null);
    const isRunning = make(false);

    let cursor = options.initialCursor || null;
    let invalidated = false;
    let generation = 0; // bumped on every new request and on stop: older answers are discarded
    let controller = null;
    let timer = null;
    let failures = 0;

    const nextRequest = () => {
        generation += 1;
        if (controller && typeof controller.abort === 'function') controller.abort();
        controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        return { seen: generation, signal: controller ? controller.signal : undefined };
    };
    const stale = (seen) => seen !== generation;

    const touch = () => {
        const at = now();
        lastUpdate.value = at;
        lastUpdateLabel.value = formatLastUpdate(at);
        refreshError.value = null;
    };

    const dropToPermissionLoss = (message) => {
        worklist.clearClinicalData();
        hasLoaded.value = false;
        worklist.isPermissionLost.value = true;
        worklist.errorMessage.value = message || MESSAGE_DENIED;
        stop();
        onSnapshot?.(null);
    };

    const commitRows = (next) => {
        const selectedId = rowId(worklist.selectedRow.value);
        worklist.rows.value = next;
        worklist.totals.value = computeStatusTotals(next, { version: tokenVersion });
        if (selectedId === null) return;
        const idx = next.findIndex((r) => rowId(r) === selectedId);
        // A row that left the list takes its detail view with it.
        if (idx === -1) worklist.selectRow(null);
        else worklist.selectRow(idx);
    };

    // Custom delta readers supply their own rows and retain their local scope rules.
    const applyChanges = (changes) => {
        const role = { role: worklist.currentRole.value, userId: worklist.currentUserId.value };
        let list = [...worklist.rows.value];
        for (const change of Array.isArray(changes) ? changes : []) {
            if (!change || typeof change !== 'object') continue;
            if (change.type === 'delete') {
                const id = change.id == null ? null : String(change.id);
                list = list.filter((r) => rowId(r) !== id);
            } else if (change.type === 'upsert' && change.recording) {
                const row = mapRecording(change.recording);
                const id = rowId(row);
                const idx = list.findIndex((r) => rowId(r) === id);
                const allowed = filterRowsByRole([row], role).length > 0;
                if (!allowed) {
                    if (idx !== -1) list.splice(idx, 1);
                } else if (idx === -1) list.push(row);
                else list[idx] = row;
            }
        }
        commitRows(list);
    };

    /** Reload the current worklist snapshot, including on manual refresh and cursor expiry. */
    const refresh = async () => {
        const { seen } = nextRequest();
        isRefreshing.value = true;
        try {
            const response = await client.request(rowsPath ? rowsPath() : '/worklist/rows');
            if (stale(seen)) return { outcome: 'discarded' };
            if (response.outcome === 'permission' || response.status === 401 || response.status === 403) {
                dropToPermissionLoss(response.message);
                return response;
            }
            if (response.outcome === 'ok' && response.data) {
                const raw = response.data.items || response.data;
                const items = Array.isArray(raw) ? raw : [];
                worklist.isPermissionLost.value = false;
                worklist.errorMessage.value = null;
                commitRows(items);
                invalidated = false;
                touch();
                onSnapshot?.(response.data);
                hasLoaded.value = true;
                return response;
            }
            if (response.outcome === 'cancelled') return response;
            refreshError.value = response.message || MESSAGE_FAILED;
            return response;
        } catch (error) {
            if (stale(seen)) return { outcome: 'discarded' };
            if (error instanceof TokenContractError && error.code === 'unauthorized') {
                dropToPermissionLoss(MESSAGE_DENIED);
            } else {
                refreshError.value = MESSAGE_FAILED;
            }
            return { outcome: 'error', error };
        } finally {
            if (!stale(seen)) isRefreshing.value = false;
        }
    };

    /** Reads the feed from the stored cursor, or reloads rows without a feed. */
    const pollChanges = async () => {
        if (!fetchChanges) return refresh();
        const { seen, signal } = nextRequest();
        isRefreshing.value = true;
        try {
            let response = await fetchChanges(cursor, { signal });
            for (;;) {
                if (stale(seen)) return { outcome: 'discarded' };
                if (response.outcome === 'permission') {
                    dropToPermissionLoss();
                    return response;
                }
                if (response.outcome === 'expired') {
                    cursor = null;
                    await primeCursor(seen);
                    if (stale(seen)) return { outcome: 'discarded' };
                    isRefreshing.value = false;
                    return refresh();
                }
                if (response.outcome === 'cancelled') return response;
                if (response.outcome !== 'ok' || !response.data) {
                    failures += 1;
                    refreshError.value = MESSAGE_FAILED;
                    return response;
                }
                failures = 0;
                if (reloadOnChanges) {
                    // Reload the authorized projection when feed rows cannot represent this list scope.
                    invalidated ||= Array.isArray(response.data.changes) && response.data.changes.length > 0;
                } else {
                    applyChanges(response.data.changes);
                }
                if (response.data.next_cursor) cursor = response.data.next_cursor;
                if (!response.data.has_more) {
                    if (invalidated) return refresh();
                    touch();
                    return response;
                }
                response = await fetchChanges(cursor, { signal });
            }
        } finally {
            if (!stale(seen)) isRefreshing.value = false;
        }
    };

    /** Walks the feed once without applying it, so later polls only see newer changes. */
    async function primeCursor(seen) {
        let next = cursor;
        for (let page = 0; page < MAX_PRIME_PAGES; page += 1) {
            const response = await fetchChanges(next, {});
            if (stale(seen) || response.outcome !== 'ok' || !response.data) return false;
            if (response.data.next_cursor) next = response.data.next_cursor;
            if (!response.data.has_more) {
                cursor = next;
                return true;
            }
        }
        return false;
    }

    const schedule = () => {
        if (!isRunning.value || timer !== null || !(intervalMs > 0)) return;
        const delay = intervalMs * Math.min(MAX_BACKOFF_FACTOR, 2 ** failures);
        timer = setTimeout(async () => {
            timer = null;
            if (!isRunning.value) return;
            await pollChanges();
            schedule();
        }, delay);
    };

    const start = async () => {
        if (isRunning.value) return;
        isRunning.value = true;
        const seen = generation;
        if (fetchChanges && !cursor) await primeCursor(seen);
        if (!isRunning.value) return;
        const response = await refresh();
        schedule();
        return response;
    };

    /** Stops polling and aborts pending requests; late answers are discarded. */
    function stop() {
        isRunning.value = false;
        generation += 1;
        if (controller && typeof controller.abort === 'function') controller.abort();
        controller = null;
        if (timer !== null) clearTimeout(timer);
        timer = null;
        isRefreshing.value = false;
    }

    /** Logout: stop everything and drop all clinical data from memory. */
    const logout = () => {
        stop();
        cursor = null;
        invalidated = false;
        worklist.clearClinicalData();
        hasLoaded.value = false;
        worklist.errorMessage.value = null;
        client.switchContext({ accountId: null, dictationId: null });
    };

    return {
        ...worklist,
        lastUpdate, lastUpdateLabel, hasLoaded, isRefreshing, refreshError, isRunning,
        refresh, pollChanges, start, stop, logout,
    };
}

export const ClinicWorklistRefresh = Object.freeze({
    name: 'ClinicWorklistRefresh',
    props: {
        role: { type: String, default: ROLE_AUTHOR },
        userId: { type: [Number, String], default: null },
        accountId: { type: [Number, String], default: null },
        tokenVersion: { type: Number, default: TOKEN_CONTRACT_VERSION },
        initialRows: { type: Array, default: () => [] },
        apiClient: { type: Object, default: null },
        intervalMs: { type: Number, default: 30000 },
        autoStart: { type: Boolean, default: true },
    },
    setup(props, { emit }) {
        const refreshing = useWorklistRefresh({
            role: props.role,
            userId: props.userId,
            accountId: props.accountId,
            tokenVersion: props.tokenVersion,
            initialRows: props.initialRows,
            apiClient: props.apiClient || undefined,
            intervalMs: props.intervalMs,
            onOpen: (row, mode) => emit('open', { row, mode }),
        });
        const vue = typeof window !== 'undefined' ? window.Vue : null;
        if (vue && typeof vue.onMounted === 'function' && props.autoStart) vue.onMounted(() => refreshing.start());
        if (vue && typeof vue.onBeforeUnmount === 'function') vue.onBeforeUnmount(() => refreshing.stop());
        return {
            ...refreshing,
            focusIndicator: FOCUS_INDICATOR,
            formatPriority,
            formatAudioDuration,
            resolveStatusSurface: (s) => resolveStatusSurface(s, { version: props.tokenVersion }),
        };
    },
    template: `
<div class="clinic-worklist-refresh" data-testid="clinic-worklist-refresh">
    <div class="clinic-refresh-bar" data-testid="refresh-bar">
        <button type="button" class="clinic-refresh-button" data-testid="refresh-button"
                :disabled="isRefreshing" :aria-busy="isRefreshing ? 'true' : 'false'"
                :style="{ background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border-primary)', outline: focusIndicator.outline }"
                @click="refresh()">Aktualisieren</button>
        <span class="clinic-last-update" data-testid="last-update" role="status" aria-live="polite">{{ lastUpdateLabel }}</span>
        <span v-if="refreshError" class="clinic-refresh-error" data-testid="refresh-error" role="alert">{{ refreshError }}</span>
    </div>
    <div v-if="isPermissionLost" class="clinic-worklist-permission-loss" data-testid="permission-loss-state" role="alert">{{ errorMessage || 'Keine Berechtigung für diese Aktion.' }}</div>
    <div v-else-if="isEmpty" class="clinic-worklist-empty" data-testid="empty-state">Keine Diktate vorhanden.</div>
    <table v-else class="clinic-worklist-table" role="grid" aria-label="Arbeitsliste">
        <thead>
            <tr>
                <th scope="col">Titel</th><th scope="col">Status</th><th scope="col">Priorität</th>
                <th scope="col">Frist</th><th scope="col">Dauer</th>
            </tr>
        </thead>
        <tbody>
            <tr v-for="(row, idx) in rows" :key="row.dictation_id || row.id || idx" tabindex="0" class="clinic-worklist-row"
                :class="{ 'is-selected': selectedIndex === idx }" :aria-selected="selectedIndex === idx"
                @click="selectRow(row)" @dblclick="openRow(row)" @keydown="onKeyDown($event, row)">
                <td>{{ row.title || '-' }}</td>
                <td><span class="clinic-row-status">{{ (resolveStatusSurface(row.status)).label }}</span></td>
                <td>{{ formatPriority(row.priority) }}</td>
                <td>{{ row.deadline_at || row.deadline || '-' }}</td>
                <td>{{ formatAudioDuration(row.duration_seconds || row.audio_seconds) }}</td>
            </tr>
        </tbody>
    </table>
</div>`.trim(),
});
