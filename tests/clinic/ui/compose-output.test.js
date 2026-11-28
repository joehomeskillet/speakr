import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

import {
    OutputView,
    createOutputViewController,
    registerOutputView,
    targetView,
} from '../../../static/js/clinic/compose-output.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(__dirname, '../../../static/js/clinic/compose-output.js'), 'utf8');

const ok = (data, status = 200) => ({ outcome: 'ok', status, data });
const fail = (outcome, status, code) => ({ outcome, status, code: code || null, message: '' });

const TARGETS = (released, kis = true) => ({
    dictation_id: 'd1',
    released_revision_id: released,
    targets: [
        { id: 'txt', channel: 'file', available: Boolean(released), reason: released ? null : 'not_released' },
        { id: 'docx', channel: 'file', available: Boolean(released), reason: released ? null : 'not_released' },
        { id: 'kis', channel: 'kis', available: Boolean(released) && kis, reason: released ? (kis ? null : 'kis_unavailable') : 'not_released' },
        { id: 'clipboard', channel: 'clipboard', available: true, reason: null },
    ],
});

const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const ODT_MIME = 'application/vnd.oasis.opendocument.text';

function fakeClient({ targets, exportResult, templates, templateDetails } = {}) {
    const calls = [];
    return {
        calls,
        request: vi.fn(async (path, options) => {
            const method = String((options && options.method) || 'GET').toUpperCase();
            calls.push({ method, path, options });
            if (method === 'GET' && path.split('?')[0].endsWith('/output-targets')) return targets;
            if (method === 'GET' && path === '/templates') return templates || ok({ items: [] });
            if (method === 'GET' && path.startsWith('/templates/')) {
                const id = decodeURIComponent(path.slice('/templates/'.length));
                if (typeof templateDetails === 'function') return templateDetails(id);
                return (templateDetails && templateDetails[id]) || fail('http', 404);
            }
            if (method === 'POST' && path.endsWith('/exports')) return typeof exportResult === 'function' ? exportResult(options) : exportResult;
            if (method === 'GET' && path.includes('/exports/')) return ok({ id: 'j1', status: 'delivered', format: 'kis' });
            return fail('http', 404);
        }),
        command: vi.fn(),
        switchContext: vi.fn(),
    };
}

const job = (extra = {}) => ({ id: 'j1', kind: 'file', status: 'ready', format: 'docx', revision_id: 'r1', filename: 'x.docx', ...extra });
const posts = (client) => client.calls.filter((c) => c.method === 'POST');

async function loaded(client, extra = {}) {
    let n = 0;
    const c = createOutputViewController({ client, dictationId: 'd1', newKey: () => 'key-' + (++n), ...extra });
    await c.load();
    return c;
}

describe('compose-output', () => {
    it('requests all file formats for the output page and labels RTF and ODT', async () => {
        const data = TARGETS('r1');
        data.targets.push(...['rtf', 'odt'].map((id) => ({ id, channel: 'file', available: true })));
        const client = fakeClient({ targets: ok(data) });
        const c = await loaded(client, { includeAllFileFormats: true });

        expect(client.calls[0].path).toBe('/dictations/d1/output-targets?file_formats=all');
        expect(c.targets.value.find((target) => target.id === 'rtf').label).toBe('Rich-Text-Dokument (RTF)');
        expect(c.targets.value.find((target) => target.id === 'odt').label).toBe('OpenDocument-Text (ODT)');
    });

    it('offers a ready file download independently of other target failures', async () => {
        const data = TARGETS('r1');
        data.targets.push({ id: 'rtf', channel: 'file', available: true });
        const client = fakeClient({
            targets: ok(data),
            exportResult: ({ body }) => body.format === 'rtf'
                ? ok(job({ id: 'rtf-job', format: 'rtf', filename: 'x.rtf' }), 201)
                : ok(job({ id: 'docx-job', status: 'failed' }), 201),
        });
        const c = await loaded(client);

        expect(c.downloadUrl('rtf')).toBeNull();
        await c.exportTo('rtf');
        await c.exportTo('docx');
        expect(c.downloadUrl('rtf')).toBe('/api/clinic/v1/dictations/d1/exports/rtf-job/download');
        expect(c.downloadUrl('docx')).toBeNull();
        expect(c.downloadUrl('kis')).toBeNull();
        expect(c.results.value.rtf.label).toBe('Datei erzeugt');
        c.markDownloadStarted('rtf');
        expect(c.results.value.rtf).toMatchObject({ phase: 'ready', label: 'Download gestartet' });
        expect(c.results.value.docx).toMatchObject({ phase: 'failed', tone: 'error' });
        expect(c.results.value.kis).toBeUndefined();
        expect(c.canExport('rtf')).toBe(false);
        await c.exportTo('rtf');
        expect(posts(client).map(({ options }) => options.body.format)).toEqual(['rtf', 'docx']);
    });

    it('offers an existing ready file job without starting it again', async () => {
        const data = TARGETS('r1');
        data.targets.push({ id: 'odt', channel: 'file', available: true, status: 'ready', job_id: 'odt-job' });
        const client = fakeClient({ targets: ok(data) });
        const c = await loaded(client);

        expect(c.downloadUrl('odt')).toBe('/api/clinic/v1/dictations/d1/exports/odt-job/download');
        expect(c.canExport('odt')).toBe(false);
        await c.exportTo('odt');
        expect(posts(client)).toHaveLength(0);
    });

    it('offers a new render for a stale DOCX job', async () => {
        const data = TARGETS('r1');
        const docx = data.targets.find((target) => target.id === 'docx');
        Object.assign(docx, { status: 'stale', job_id: 'old-docx' });
        const client = fakeClient({ targets: ok(data), exportResult: ok(job(), 201) });
        const c = await loaded(client);

        expect(c.downloadUrl('docx')).toBeNull();
        expect(c.results.value.docx.label).toBe('Datei bitte neu erzeugen.');
        await c.exportTo('docx');
        expect(posts(client)).toHaveLength(1);
        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/j1/download');
    });

    it('offers export only for the released revision', async () => {
        const client = fakeClient({ targets: ok(TARGETS(null)) });
        const c = await loaded(client);
        expect(c.releasedRevisionId.value).toBeNull();
        expect(c.canExport('docx')).toBe(false);
        expect(await c.exportTo('docx')).toBeNull();
        expect(posts(client)).toHaveLength(0);

        const rel = await loaded(fakeClient({ targets: ok(TARGETS('r1')) }));
        expect(rel.releasedRevisionId.value).toBe('r1');
        expect(rel.canExport('docx')).toBe(true);
        expect(rel.canExport('clipboard')).toBe(false);
    });

    it('sends the released revision id and format', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: ok(job(), 201) });
        const c = await loaded(client);
        await c.exportTo('docx');
        expect(posts(client)[0].path).toBe('/dictations/d1/exports');
        expect(posts(client)[0].options.body).toEqual({ revision_id: 'r1', format: 'docx' });
        expect(c.results.value.docx.phase).toBe('ready');
    });

    it('the same click twice sends one idempotency key', async () => {
        let release;
        const gate = new Promise((r) => { release = r; });
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: async () => { await gate; return ok(job(), 201); } });
        const c = await loaded(client);
        const first = c.exportTo('docx');
        const second = c.exportTo('docx');
        release();
        await Promise.all([first, second]);
        expect(posts(client)).toHaveLength(1);
        await c.exportTo('docx');
        const keys = posts(client).map((p) => p.options.idempotencyKey);
        expect(new Set(keys)).toEqual(new Set(['key-1']));
    });

    it('reuses the key after an unknown network result', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('network', null) });
        const c = await loaded(client);
        await c.exportTo('docx');
        await c.exportTo('docx');
        expect(posts(client).map((p) => p.options.idempotencyKey)).toEqual(['key-1', 'key-1']);
    });

    it('takes a new key after a conflict', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('conflict', 409, 'NOT_RELEASED') });
        const c = await loaded(client);
        await c.exportTo('docx');
        expect(c.results.value.docx.phase).toBe('conflict');
        await c.exportTo('docx');
        expect(posts(client).map((p) => p.options.idempotencyKey)).toEqual(['key-1', 'key-2']);
    });

    it('renders a disabled KIS adapter as unavailable, never as success', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1', false)) });
        const c = await loaded(client);
        const kis = c.targets.value.find((t) => t.id === 'kis');
        expect(kis.available).toBe(false);
        expect(kis.reason).toBe('nicht verfügbar');
        expect(c.canExport('kis')).toBe(false);
        expect(await c.exportTo('kis')).toBeNull();
        expect(posts(client)).toHaveLength(0);
    });

    it('maps a 503 from the export route to unavailable', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: fail('network', 503, 'KIS_UNAVAILABLE') });
        const c = await loaded(client);
        await c.exportTo('kis');
        expect(c.results.value.kis.phase).toBe('unavailable');
        expect(c.results.value.kis.label).toBe('nicht verfügbar');
    });

    it('shows UNKNOWN as unknown and reconciles by reading the status only', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')), exportResult: ok({ id: 'j1', kind: 'kis', status: 'unknown', format: 'kis' }, 201) });
        const c = await loaded(client);
        await c.exportTo('kis');
        expect(c.results.value.kis.phase).toBe('unknown');
        await c.refreshStatus('kis');
        expect(c.results.value.kis.phase).toBe('delivered');
        expect(posts(client)).toHaveLength(1);
    });

    it('clipboard copy uses the existing module with the released revision', async () => {
        const client = fakeClient({ targets: ok(TARGETS('r1')) });
        client.request.mockImplementation(async (path, options) => {
            if (path.endsWith('/output-targets')) return ok(TARGETS('r1'));
            if (path.endsWith('/outcome')) return ok({ status: 'CONFIRMED' });
            return ok({ 'text/plain': 'Text', 'text/html': '<p>Text</p>' });
        });
        const writeText = vi.fn(async () => {});
        const c = await loaded(client, { clipboard: { writeText, write: vi.fn() } });
        const copier = c.clipboardCopy();
        await copier.copy('plain');
        expect(writeText).toHaveBeenCalledWith('Text');
        expect(copier.state.value.phase).toBe('copied');
        const paths = client.request.mock.calls.map((x) => x[0]);
        expect(paths).toContain('/dictations/d1/clipboard/r1');
        expect(c.clipboardCopy()).toBe(copier);
    });

    it('offers no clipboard copy without a released revision', async () => {
        const c = await loaded(fakeClient({ targets: ok(TARGETS(null)) }));
        expect(c.clipboardCopy()).toBeNull();
    });

    it('fails closed when the targets cannot be loaded', async () => {
        const c = await loaded(fakeClient({ targets: fail('permission', 403) }));
        expect(c.phase.value).toBe('failed');
        expect(c.targets.value).toEqual([]);
    });

    it('requires a dictation id and registers the view', () => {
        expect(() => createOutputViewController({ client: {}, dictationId: '' })).toThrow();
        const app = { component: vi.fn() };
        expect(registerOutputView(app)).toBe(app);
        expect(app.component).toHaveBeenCalledWith('clinic-output-view', OutputView);
        expect(targetView({ id: 'docx', channel: 'file', available: true }, null).available).toBe(false);
    });

    it('uses no browser storage and no patient fields', () => {
        expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie/);
        expect(source).not.toMatch(/patient/i);
    });

    it('loads authorized templates filtered by MIME type and exposes Vorlage/Ausgabemodus defaults', async () => {
        const data = TARGETS('r1');
        data.targets.push({ id: 'odt', channel: 'file', available: true });
        const client = fakeClient({
            targets: ok(data),
            templates: ok({
                items: [
                    { id: 'tpl-docx-2', name: 'Arztbrief', scope: 'pool', version: 2, mime_type: DOCX_MIME },
                    { id: 'tpl-odt-1', name: 'Verlaufsbericht', scope: 'user', version: 1, mime_type: ODT_MIME },
                    { id: 'tpl-other', name: 'Ignoriert', scope: 'system', version: 1, mime_type: 'text/plain' },
                ],
            }),
            templateDetails: {
                'tpl-docx-2': ok({ id: 'tpl-docx-2', insertion_mark: '{{DIKTAT}}' }),
                'tpl-odt-1': ok({ id: 'tpl-odt-1', insertion_mark: null }),
            },
        });
        const c = await loaded(client, { includeAllFileFormats: true, includeTemplates: true });

        expect(c.selectedTemplates.value).toEqual({ docx: '', odt: '' });
        expect(c.selectedModes.value).toEqual({ docx: 'formatted', odt: 'plain' });
        expect(c.templatesFor('docx').map((t) => t.id)).toEqual(['tpl-docx-2']);
        expect(c.templatesFor('odt').map((t) => t.id)).toEqual(['tpl-odt-1']);
        expect(c.templatesFor('txt')).toEqual([]);

        await c.setTemplate('docx', 'tpl-docx-2');
        expect(c.selectedTemplateDetail('docx')).toMatchObject({ insertion_mark: '{{DIKTAT}}' });
        await c.setTemplate('odt', 'tpl-odt-1');
        expect(c.selectedTemplateDetail('odt')).toMatchObject({ insertion_mark: null });
    });

    it('keeps plain exports working when the template catalog fails to load', async () => {
        const client = fakeClient({
            targets: ok(TARGETS('r1')),
            templates: fail('network', 503),
            exportResult: ok(job(), 201),
        });
        const c = await loaded(client, { includeTemplates: true });
        expect(c.phase.value).toBe('ready');
        expect(c.templatesFailed.value).toBe(true);
        expect(c.templatesFor('docx')).toEqual([]);

        await c.exportTo('docx');
        expect(posts(client)[0].options.body).toEqual({ revision_id: 'r1', format: 'docx' });
        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/j1/download');
    });

    it('sends template_id and text_mode for DOCX and ODT and preserves/rotates idempotency keys', async () => {
        const data = TARGETS('r1');
        data.targets.push({ id: 'odt', channel: 'file', available: true });
        let failOnce = true;
        const client = fakeClient({
            targets: ok(data),
            templates: ok({
                items: [
                    { id: 'tpl-docx-1', name: 'Brief', scope: 'user', version: 1, mime_type: DOCX_MIME },
                    { id: 'tpl-odt-1', name: 'ODT-Brief', scope: 'user', version: 1, mime_type: ODT_MIME },
                ],
            }),
            templateDetails: {
                'tpl-docx-1': ok({ id: 'tpl-docx-1', insertion_mark: '{{DIKTAT}}' }),
                'tpl-odt-1': ok({ id: 'tpl-odt-1', insertion_mark: '{{DIKTAT}}' }),
            },
            exportResult: ({ body }) => {
                if (body.format === 'docx' && body.text_mode === 'formatted' && failOnce) {
                    failOnce = false;
                    return fail('network', null);
                }
                return ok(job({ id: 'job-' + body.format + '-' + body.text_mode, format: body.format }), 201);
            },
        });
        const c = await loaded(client, { includeAllFileFormats: true, includeTemplates: true });

        await c.setTemplate('docx', 'tpl-docx-1');
        await c.exportTo('docx');
        expect(c.results.value.docx.phase).toBe('network');
        await c.exportTo('docx');
        expect(c.results.value.docx.phase).toBe('ready');

        c.setTextMode('docx', 'plain');
        expect(c.canExport('docx')).toBe(true);
        expect(c.downloadUrl('docx')).toBeNull();
        expect(c.previousDownloads.value.docx.url).toBe('/api/clinic/v1/dictations/d1/exports/job-docx-formatted/download');
        await c.exportTo('docx');

        await c.setTemplate('odt', 'tpl-odt-1');
        await c.exportTo('odt');

        const sent = posts(client);
        expect(sent.map((p) => p.options.body)).toEqual([
            { revision_id: 'r1', format: 'docx', template_id: 'tpl-docx-1', text_mode: 'formatted' },
            { revision_id: 'r1', format: 'docx', template_id: 'tpl-docx-1', text_mode: 'formatted' },
            { revision_id: 'r1', format: 'docx', template_id: 'tpl-docx-1', text_mode: 'plain' },
            { revision_id: 'r1', format: 'odt', template_id: 'tpl-odt-1', text_mode: 'plain' },
        ]);
        expect(sent[0].options.idempotencyKey).toBe(sent[1].options.idempotencyKey);
        expect(sent[2].options.idempotencyKey).not.toBe(sent[0].options.idempotencyKey);
    });

    it('reports 422 template mark errors and 409 macro errors in German without claiming success', async () => {
        const data = TARGETS('r1');
        data.targets.push({ id: 'odt', channel: 'file', available: true });
        const client = fakeClient({
            targets: ok(data),
            templates: ok({
                items: [
                    { id: 'tpl-bad-mark', name: 'Ohne Marke', scope: 'user', version: 1, mime_type: DOCX_MIME },
                    { id: 'tpl-macro', name: 'Mit Makro', scope: 'user', version: 1, mime_type: DOCX_MIME },
                    { id: 'tpl-good', name: 'Gültig', scope: 'user', version: 1, mime_type: DOCX_MIME },
                    { id: 'tpl-odt', name: 'ODT', scope: 'user', version: 1, mime_type: ODT_MIME },
                ],
            }),
            templateDetails: {
                'tpl-bad-mark': ok({ id: 'tpl-bad-mark', insertion_mark: '{{FEHLT}}' }),
                'tpl-macro': ok({ id: 'tpl-macro', insertion_mark: '{{DIKTAT}}' }),
                'tpl-good': ok({ id: 'tpl-good', insertion_mark: '{{DIKTAT}}' }),
                'tpl-odt': ok({ id: 'tpl-odt', insertion_mark: '{{DIKTAT}}' }),
            },
            exportResult: ({ body }) => {
                if (body.template_id === 'tpl-bad-mark') return fail('validation', 422, 'TEMPLATE_MARK_ERROR');
                if (body.template_id === 'tpl-macro') return fail('conflict', 409, 'TEMPLATE_MACRO');
                if (body.format === 'odt' && body.text_mode === 'formatted') return fail('validation', 422, 'TEMPLATE_MARK_ERROR');
                return ok(job({ id: 'good-job' }), 201);
            },
        });
        const c = await loaded(client, { includeAllFileFormats: true, includeTemplates: true });

        await c.setTemplate('docx', 'tpl-bad-mark');
        await c.exportTo('docx');
        expect(c.results.value.docx).toMatchObject({
            phase: 'validation',
            tone: 'error',
            alert: true,
            job: null,
        });
        expect(c.results.value.docx.label).toContain('Diktattext');
        expect(c.results.value.docx.label).toContain('bleibt unverändert erhalten');
        expect(c.downloadUrl('docx')).toBeNull();
        expect(c.canExport('docx')).toBe(true);
        expect(c.results.value.txt).toBeUndefined();

        await c.setTemplate('docx', 'tpl-macro');
        await c.exportTo('docx');
        expect(c.results.value.docx).toMatchObject({ phase: 'conflict', tone: 'error', alert: true, job: null });
        expect(c.results.value.docx.label).toContain('Makro');

        await c.setTemplate('docx', 'tpl-good');
        expect(c.results.value.docx).toBeUndefined();
        await c.exportTo('docx');
        expect(c.results.value.docx.phase).toBe('ready');
        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/good-job/download');

        await c.setTemplate('odt', 'tpl-odt');
        c.setTextMode('odt', 'formatted');
        await c.exportTo('odt');
        expect(c.results.value.odt).toMatchObject({ phase: 'validation', tone: 'error', alert: true });
        expect(c.results.value.odt.label).toContain('Nur Text');
        expect(c.downloadUrl('odt')).toBeNull();

        // A distinct key was used after each failure/correction.
        expect(new Set(posts(client).map((p) => p.options.idempotencyKey)).size).toBe(4);
    });

    it('reopens a ready plain export on load and unlocks export when choosing a template', async () => {
        const data = TARGETS('r1');
        const docx = data.targets.find((t) => t.id === 'docx');
        Object.assign(docx, { status: 'ready', job_id: 'plain-docx-job' });
        const client = fakeClient({
            targets: ok(data),
            templates: ok({
                items: [{ id: 'tpl-1', name: 'Brief', scope: 'system', version: 3, mime_type: DOCX_MIME }],
            }),
            templateDetails: { 'tpl-1': ok({ id: 'tpl-1', insertion_mark: '{{DIKTAT}}' }) },
            exportResult: ok(job({ id: 'tpl-docx-job' }), 201),
        });
        const c = await loaded(client, { includeAllFileFormats: true, includeTemplates: true });

        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/plain-docx-job/download');
        expect(c.canExport('docx')).toBe(false);

        await c.setTemplate('docx', 'tpl-1');
        expect(c.canExport('docx')).toBe(true);
        expect(c.downloadUrl('docx')).toBeNull();
        expect(c.previousDownloads.value.docx.url).toBe('/api/clinic/v1/dictations/d1/exports/plain-docx-job/download');

        await c.setTemplate('docx', '');
        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/plain-docx-job/download');
        expect(c.previousDownloads.value.docx).toBeUndefined();

        await c.setTemplate('docx', 'tpl-1');
        await c.exportTo('docx');
        expect(c.downloadUrl('docx')).toBe('/api/clinic/v1/dictations/d1/exports/tpl-docx-job/download');
        expect(c.previousDownloads.value.docx).toBeUndefined();
    });

    it('declares Vorlage, Ausgabemodus and German alert markup in OutputView', () => {
        for (const snippet of [
            'TEXT.template', 'TEXT.noTemplate', 'TEXT.textMode', 'TEXT.modeFormatted', 'TEXT.modePlain',
            'role="alert"', 'note burgundy', 'template-select-', 'mode-select-',
        ]) {
            expect(OutputView.template).toContain(snippet);
        }
    });
});
