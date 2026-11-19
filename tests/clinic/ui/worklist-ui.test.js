import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
    ClinicWorklist,
    PRIORITY_LABELS,
    ROLE_AUTHOR,
    ROLE_STAFF,
    ROLE_TYPIST,
    STATUS_LABELS,
    WORKLIST_UI_VERSION,
    computeStatusTotals,
    filterRowsByRole,
    formatAudioDuration,
    formatPriority,
    getOpenMode,
    resolveStatusSurface,
    useWorklist,
} from '../../../static/js/clinic/worklist-ui.js';
import {
    FOCUS_INDICATOR,
    TOKEN_CONTRACT_VERSION,
    TokenContractError,
} from '../../../static/js/clinic/tokens.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const sourcePath = join(__dirname, '../../../static/js/clinic/worklist-ui.js');
const source = readFileSync(sourcePath, 'utf8');

function mockApiClient(handler) {
    const context = { accountId: null, dictationId: null };
    return {
        context,
        switchContext: vi.fn((next) => {
            context.accountId = next?.accountId || null;
            context.dictationId = next?.dictationId || null;
        }),
        request: vi.fn(handler || (async () => ({ outcome: 'ok', data: { items: [] } }))),
        command: vi.fn(async () => ({ outcome: 'ok', data: {} })),
    };
}

describe('worklist-ui', () => {
    describe('formatting and helpers', () => {
        it('formats priorities according to clinic codes', () => {
            expect(formatPriority(0)).toBe('Bei Gelegenheit');
            expect(formatPriority(1)).toBe('Normal');
            expect(formatPriority(128)).toBe('Dringend');
            expect(formatPriority(999)).toBe('Normal');
            expect(formatPriority(null)).toBe('Normal');
        });

        it('formats audio duration in mm:ss and hh:mm:ss', () => {
            expect(formatAudioDuration(0)).toBe('00:00');
            expect(formatAudioDuration(45)).toBe('00:45');
            expect(formatAudioDuration(65)).toBe('01:05');
            expect(formatAudioDuration(3665)).toBe('1:01:05');
            expect(formatAudioDuration(-10)).toBe('00:00');
            expect(formatAudioDuration(null)).toBe('00:00');
        });

        it('maps workflow open modes for author and staff', () => {
            const rowAudio = { status: 'zu_schreiben', open_mode: null };
            const rowCorrection = { status: 'zu_korrigieren', open_mode: null };
            const rowExplicit = { status: 'zu_schreiben', open_mode: 'custom_mode' };

            expect(getOpenMode(rowAudio, ROLE_AUTHOR)).toBe('author_case');
            expect(getOpenMode(rowCorrection, ROLE_AUTHOR)).toBe('author_case');
            expect(getOpenMode(rowAudio, ROLE_STAFF)).toBe('player');
            expect(getOpenMode(rowCorrection, ROLE_STAFF)).toBe('correction');
            expect(getOpenMode(rowCorrection, ROLE_TYPIST)).toBe('correction');
            expect(getOpenMode(rowExplicit, ROLE_STAFF)).toBe('custom_mode');
        });
    });

    describe('role-scoped filtering and status totals', () => {
        const syntheticRows = [
            {
                dictation_id: 'd1',
                title: 'Befund 1',
                status: 'zu_schreiben',
                author_user_id: 7,
                assignee_user_id: null,
                priority: 1,
                duration_seconds: 40,
            },
            {
                dictation_id: 'd2',
                title: 'Befund 2',
                status: 'zu_schreiben',
                author_user_id: 7,
                assignee_user_id: 9,
                priority: 128,
                duration_seconds: 25,
            },
            {
                dictation_id: 'd3',
                title: 'Befund 3',
                status: 'fertiggestellt',
                author_user_id: 7,
                assignee_user_id: 9,
                priority: 0,
                duration_seconds: 60,
            },
            {
                dictation_id: 'd4',
                title: 'Fremder Befund',
                status: 'zu_schreiben',
                author_user_id: 8,
                assignee_user_id: 10,
                priority: 1,
                duration_seconds: 50,
            },
            {
                dictation_id: 'd5',
                title: 'Pool Unzugewiesen',
                status: 'zu_korrigieren',
                author_user_id: 8,
                assignee_user_id: null,
                priority: 1,
                duration_seconds: 30,
            },
        ];

        it('shows permitted author view with status totals and audio duration', () => {
            const authorRows = filterRowsByRole(syntheticRows, { role: ROLE_AUTHOR, userId: 7 });
            expect(authorRows.map((r) => r.dictation_id)).toEqual(['d1', 'd2', 'd3']);

            const totals = computeStatusTotals(authorRows);
            expect(totals.totalCount).toBe(3);
            expect(totals.totalAudioSeconds).toBe(125);
            expect(totals.formattedTotalDuration).toBe('02:05');

            const writeGroup = totals.groups.find((g) => g.status === 'zu_schreiben');
            expect(writeGroup).toBeDefined();
            expect(writeGroup.label).toBe('Zu schreiben');
            expect(writeGroup.count).toBe(2);
            expect(writeGroup.totalAudioSeconds).toBe(65);
            expect(writeGroup.formattedDuration).toBe('01:05');
            expect(writeGroup.finished).toBe(false);
            expect(writeGroup.dictationIds).toEqual(['d1', 'd2']);
            expect(writeGroup.surface.status).toBe('pending');

            const doneGroup = totals.groups.find((g) => g.status === 'fertiggestellt');
            expect(doneGroup).toBeDefined();
            expect(doneGroup.label).toBe('Fertiggestellt');
            expect(doneGroup.count).toBe(1);
            expect(doneGroup.totalAudioSeconds).toBe(60);
            expect(doneGroup.finished).toBe(true);
            expect(doneGroup.surface.status).toBe('success');
        });

        it('shows permitted staff view excluding dictations assigned to foreign staff', () => {
            const staffRows = filterRowsByRole(syntheticRows, { role: ROLE_STAFF, userId: 9 });
            // Staff 9 sees: unassigned (d1, d5) + assigned to 9 (d2, d3). Excludes d4 (assigned to 10).
            expect(staffRows.map((r) => r.dictation_id)).toEqual(['d1', 'd2', 'd3', 'd5']);

            const totals = computeStatusTotals(staffRows);
            expect(totals.totalCount).toBe(4);
            expect(totals.totalAudioSeconds).toBe(155);
            expect(totals.formattedTotalDuration).toBe('02:35');

            const corrGroup = totals.groups.find((g) => g.status === 'zu_korrigieren');
            expect(corrGroup).toBeDefined();
            expect(corrGroup.label).toBe('Zu korrigieren');
            expect(corrGroup.count).toBe(1);
            expect(corrGroup.totalAudioSeconds).toBe(30);
        });
    });

    describe('token contract integration & surface resolution', () => {
        it('resolves semantic visual tokens and German labels for workflow statuses', () => {
            const surfaceWrite = resolveStatusSurface('zu_schreiben');
            expect(surfaceWrite.label).toBe('Zu schreiben');
            expect(surfaceWrite.status).toBe('pending');
            expect(surfaceWrite.background).toBe('var(--bg-pending-light)');
            expect(surfaceWrite.color).toBe('var(--text-pending-strong)');
            expect(surfaceWrite.focus).toEqual(FOCUS_INDICATOR);

            const surfaceDone = resolveStatusSurface('fertiggestellt');
            expect(surfaceDone.label).toBe('Fertiggestellt');
            expect(surfaceDone.status).toBe('success');
            expect(surfaceDone.background).toBe('var(--bg-success-light)');

            const surfaceProcessing = resolveStatusSurface('in_bearbeitung');
            expect(surfaceProcessing.label).toBe('In Bearbeitung');
            expect(surfaceProcessing.status).toBe('processing');
        });

        it('raises TokenContractError on unauthorized context without inventing a surface', () => {
            expect(() => resolveStatusSurface('zu_schreiben', { authorized: false }))
                .toThrow(TokenContractError);
            try {
                resolveStatusSurface('zu_schreiben', { authorized: false });
            } catch (err) {
                expect(err.code).toBe('unauthorized');
            }
        });

        it('raises TokenContractError on stale contract version', () => {
            expect(() => resolveStatusSurface('zu_schreiben', { version: TOKEN_CONTRACT_VERSION + 1 }))
                .toThrow(TokenContractError);
            try {
                resolveStatusSurface('zu_schreiben', { version: TOKEN_CONTRACT_VERSION - 1 });
            } catch (err) {
                expect(err.code).toBe('stale_version');
            }
        });

        it('falls back safely for unknown statuses without failing totals', () => {
            const unknownTotals = computeStatusTotals([{ dictation_id: 'x1', status: 'unbekannt_xyz', duration_seconds: 10 }]);
            expect(unknownTotals.groups).toHaveLength(1);
            expect(unknownTotals.groups[0].surface.status).toBe('normal');
            expect(unknownTotals.groups[0].count).toBe(1);
        });

        it('re-evaluating identical status totals produces identical results (retry safety)', () => {
            const rows = [{ dictation_id: 'r1', status: 'zu_schreiben', duration_seconds: 15 }];
            const run1 = computeStatusTotals(rows);
            const run2 = computeStatusTotals(rows);
            expect(run1).toEqual(run2);
        });
    });

    describe('composable state and account switching', () => {
        it('starts in empty state when no initial rows are provided', () => {
            const worklist = useWorklist();
            expect(worklist.rows.value).toEqual([]);
            expect(worklist.isEmpty.value).toBe(true);
            expect(worklist.isLoading.value).toBe(false);
            expect(worklist.isPermissionLost.value).toBe(false);
            expect(worklist.totals.value.totalCount).toBe(0);
        });

        it('loads worklist items and updates status totals', async () => {
            const mockClient = mockApiClient(async () => ({
                outcome: 'ok',
                data: {
                    items: [
                        { dictation_id: 'a1', title: 'Fall A', status: 'zu_schreiben', author_user_id: 7, duration_seconds: 30 },
                        { dictation_id: 'a2', title: 'Fall B', status: 'fertiggestellt', author_user_id: 7, duration_seconds: 45 },
                    ],
                    total: 2,
                },
            }));

            const worklist = useWorklist({
                role: ROLE_AUTHOR,
                userId: 7,
                apiClient: mockClient,
            });

            await worklist.loadWorklist();

            expect(worklist.isLoading.value).toBe(false);
            expect(worklist.isEmpty.value).toBe(false);
            expect(worklist.rows.value).toHaveLength(2);
            expect(worklist.totals.value.totalCount).toBe(2);
            expect(worklist.totals.value.totalAudioSeconds).toBe(75);
            expect(mockClient.request).toHaveBeenCalledTimes(1);
        });

        it('displays permission-loss state and immediately wipes rows without retaining data', async () => {
            const mockClient = mockApiClient();
            const worklist = useWorklist({
                role: ROLE_AUTHOR,
                userId: 7,
                initialRows: [
                    { dictation_id: 'leak1', title: 'Geheimer Befund', status: 'zu_schreiben', author_user_id: 7, duration_seconds: 20 },
                ],
                apiClient: mockClient,
            });

            expect(worklist.rows.value).toHaveLength(1);

            // Simulate permission denied / 401 response
            mockClient.request.mockResolvedValueOnce({
                outcome: 'permission',
                status: 401,
                code: 'UNAUTHORIZED',
                message: 'Keine Berechtigung für diese Aktion.',
            });

            await worklist.loadWorklist();

            expect(worklist.isPermissionLost.value).toBe(true);
            expect(worklist.errorMessage.value).toBe('Keine Berechtigung für diese Aktion.');
            // Crucial: rows must be wiped clean immediately
            expect(worklist.rows.value).toEqual([]);
            expect(worklist.totals.value.totalCount).toBe(0);
            expect(worklist.totals.value.totalAudioSeconds).toBe(0);
            expect(worklist.selectedRow.value).toBeNull();
        });

        it('switches account without retaining rows from previous account', () => {
            const mockClient = mockApiClient();
            const worklist = useWorklist({
                role: ROLE_AUTHOR,
                userId: 7,
                accountId: 'account-1',
                initialRows: [
                    { dictation_id: 'secret-a1', title: 'Patient A', status: 'zu_schreiben', author_user_id: 7, duration_seconds: 50 },
                ],
                apiClient: mockClient,
            });

            expect(worklist.rows.value).toHaveLength(1);
            expect(worklist.totals.value.totalCount).toBe(1);

            // Switch to account 2
            worklist.switchAccount('account-2');

            expect(worklist.currentAccount.value).toBe('account-2');
            expect(mockClient.switchContext).toHaveBeenCalledWith({ accountId: 'account-2', dictationId: null });
            // Rows and totals must be completely cleared
            expect(worklist.rows.value).toEqual([]);
            expect(worklist.totals.value.totalCount).toBe(0);
            expect(worklist.totals.value.totalAudioSeconds).toBe(0);
            expect(worklist.selectedRow.value).toBeNull();
            expect(worklist.selectedIndex.value).toBe(-1);
        });

        it('handles network error outcome gracefully without data corruption', async () => {
            const mockClient = mockApiClient(async () => ({
                outcome: 'network',
                code: 'NETWORK',
                message: 'Die Verbindung ist fehlgeschlagen.',
            }));

            const worklist = useWorklist({ apiClient: mockClient });
            await worklist.loadWorklist();

            expect(worklist.errorMessage.value).toBe('Die Verbindung ist fehlgeschlagen.');
            expect(worklist.isLoading.value).toBe(false);
        });
    });

    describe('keyboard navigation and selection', () => {
        it('supports keyboard navigation with ArrowDown and ArrowUp', () => {
            const rows = [
                { dictation_id: 'k1', title: 'Row 1', status: 'zu_schreiben' },
                { dictation_id: 'k2', title: 'Row 2', status: 'zu_schreiben' },
                { dictation_id: 'k3', title: 'Row 3', status: 'fertiggestellt' },
            ];
            const worklist = useWorklist({ initialRows: rows });

            expect(worklist.selectedIndex.value).toBe(-1);

            // Select next
            worklist.selectNext();
            expect(worklist.selectedIndex.value).toBe(0);
            expect(worklist.selectedRow.value.dictation_id).toBe('k1');

            worklist.selectNext();
            expect(worklist.selectedIndex.value).toBe(1);
            expect(worklist.selectedRow.value.dictation_id).toBe('k2');

            worklist.selectPrevious();
            expect(worklist.selectedIndex.value).toBe(0);

            // Wrapped navigation
            worklist.selectPrevious();
            expect(worklist.selectedIndex.value).toBe(2);
        });

        it('handles keydown events and triggers open on Enter', () => {
            const opened = [];
            const rows = [{ dictation_id: 'k1', status: 'zu_schreiben' }];
            const worklist = useWorklist({
                role: ROLE_AUTHOR,
                initialRows: rows,
                onOpen: (row, mode) => opened.push({ row, mode }),
            });

            const preventDefault = vi.fn();

            // Arrow down key event
            worklist.onKeyDown({ key: 'ArrowDown', preventDefault });
            expect(preventDefault).toHaveBeenCalled();
            expect(worklist.selectedIndex.value).toBe(0);

            // Enter key event
            worklist.onKeyDown({ key: 'Enter', preventDefault }, rows[0]);
            expect(opened).toHaveLength(1);
            expect(opened[0]).toEqual({ row: rows[0], mode: 'author_case' });
        });

        it('exports FOCUS_INDICATOR matching the theme contract', () => {
            const worklist = useWorklist();
            expect(worklist.focusIndicator).toBe(FOCUS_INDICATOR);
            expect(worklist.focusIndicator.outline).toBe('2px solid var(--border-focus)');
        });
    });

    describe('vue component definition & markup', () => {
        it('exports a valid Vue component object with template and props', () => {
            expect(ClinicWorklist.name).toBe('ClinicWorklist');
            expect(typeof ClinicWorklist.setup).toBe('function');
            expect(typeof ClinicWorklist.template).toBe('string');
            expect(ClinicWorklist.props).toBeDefined();

            // Template includes status totals, loading, permission-loss, empty, and table states
            expect(ClinicWorklist.template).toContain('data-testid="status-totals"');
            expect(ClinicWorklist.template).toContain('data-testid="loading-state"');
            expect(ClinicWorklist.template).toContain('data-testid="permission-loss-state"');
            expect(ClinicWorklist.template).toContain('data-testid="empty-state"');
            expect(ClinicWorklist.template).toContain('class="clinic-worklist-table"');
        });

        it('component setup wires worklist state and helper formatters', () => {
            const emit = vi.fn();
            const props = {
                role: ROLE_AUTHOR,
                userId: 7,
                tokenVersion: TOKEN_CONTRACT_VERSION,
                initialRows: [{ dictation_id: 'c1', status: 'fertiggestellt', duration_seconds: 10, author_user_id: 7 }],
            };

            const instance = ClinicWorklist.setup(props, { emit });
            expect(instance.rows.value).toHaveLength(1);
            expect(instance.totals.value.totalCount).toBe(1);
            expect(instance.formatPriority(128)).toBe('Dringend');
            expect(instance.formatAudioDuration(60)).toBe('01:00');
            expect(instance.resolveStatusSurface('fertiggestellt').label).toBe('Fertiggestellt');

            // Trigger open row
            instance.openRow(props.initialRows[0]);
            expect(emit).toHaveBeenCalledWith('open', { row: props.initialRows[0], mode: 'author_case' });
        });
    });

    describe('clinical data in-memory safety', () => {
        it('does not reference browser storage or cookie persistence APIs', () => {
            expect(source).not.toMatch(/localStorage/);
            expect(source).not.toMatch(/sessionStorage/);
            expect(source).not.toMatch(/indexedDB/);
            expect(source).not.toMatch(/document\.cookie/);
        });

        it('does not include unmapped hardcoded hex colors or arbitrary rgb/hsl values', () => {
            expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
            expect(source).not.toMatch(/\brgb\(/);
            expect(source).not.toMatch(/\bhsl\(/);
        });
    });
});
