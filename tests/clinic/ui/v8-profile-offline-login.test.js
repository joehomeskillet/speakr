import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { SETTINGS_TEMPLATE, createPedalSettingsComponent, createConsentComponent } from '../../../static/js/clinic/compose-settings.js';
import { OfflineView } from '../../../static/js/clinic/compose-offline.js';

const ROOT = new URL('../../../', import.meta.url);
const read = (rel) => readFileSync(new URL(rel, ROOT), 'utf8');

// Structure of the V8 prototype pages einstellungen.html (P19), offline.html (P21), anmeldung.html (P20):
// page head with eyebrow, h1 and subline, then panels. Data, test ids and German labels stay with the modules.
describe('v8-118 settings page structure', () => {
    it('opens with the prototype page head and puts every section into a panel', () => {
        expect(SETTINGS_TEMPLATE).toContain('class="pagehead"');
        expect(SETTINGS_TEMPLATE).toContain('class="eyebrow"');
        expect(SETTINGS_TEMPLATE).toContain('<h1>{{ labels.title }}</h1>');
        expect(SETTINGS_TEMPLATE).toContain('class="subline"');
        expect(SETTINGS_TEMPLATE).toContain('class="grid equal"');
        expect(SETTINGS_TEMPLATE.match(/class="panel[ "]/g).length).toBeGreaterThanOrEqual(3);
    });

    it('keeps the origin rows, the empty state and the section mounts', () => {
        expect(SETTINGS_TEMPLATE).toContain('data-testid="clinic-settings-origins"');
        expect(SETTINGS_TEMPLATE).toContain('data-testid="clinic-settings-empty"');
        expect(SETTINGS_TEMPLATE).toContain(':data-key="r.key" :data-origin="r.origin"');
        expect(SETTINGS_TEMPLATE).toContain('<component :is="pedal">');
        expect(SETTINGS_TEMPLATE).toContain('<component :is="consent"');
    });

    it('does not use the app field class on wrappers (it would add its own border) nor demo text', () => {
        for (const tpl of [SETTINGS_TEMPLATE, createPedalSettingsComponent({ ref: (v) => ({ value: v }), onBeforeUnmount() {} }).template,
            createConsentComponent({}, {}).template]) {
            expect(tpl).not.toMatch(/class="field[ "]/);
            expect(tpl).not.toMatch(/Gestaltungsvorschau|Beispieldaten|V8\.0/);
        }
    });

    it('renders pedal and consent as panels with their test ids', () => {
        const pedal = createPedalSettingsComponent({ ref: (v) => ({ value: v }), onBeforeUnmount() {} }).template;
        const consent = createConsentComponent({}, {}).template;
        expect(pedal).toContain('class="panel col-span-full"');
        expect(pedal).toContain('data-testid="clinic-settings-pedal"');
        expect(pedal).toContain('<table');
        expect(consent).toContain('class="panel"');
        expect(consent).toContain('data-testid="clinic-settings-consent-state"');
        expect(consent).toContain('class="keyvalues"');
    });
});

describe('v8-118 offline page structure', () => {
    const tpl = OfflineView.template;

    it('has the prototype page head and two panels (drafts table, connection and check)', () => {
        expect(tpl).toContain('class="pagehead"');
        expect(tpl).toContain('class="eyebrow"');
        expect(tpl).toContain('<h1 id="clinic-offline-title">');
        expect(tpl).toContain('class="subline"');
        expect(tpl.match(/<section class="panel"/g)).toHaveLength(2);
        expect(tpl).toContain('class="table-wrap"');
        expect(tpl).toContain('class="keyvalues"');
    });

    it('keeps every test id of the module', () => {
        for (const id of ['profile', 'connection', 'notice', 'no-drafts', 'draft', 'restore', 'discard', 'discard-confirm',
            'discard-yes', 'discard-no', 'usage', 'unlock', 'lock', 'refresh', 'logout']) {
            expect(tpl).toContain('data-testid="' + id + '"');
        }
    });

    it('shows no demo content and keeps draft content out of the template', () => {
        expect(tpl).not.toMatch(/Gestaltungsvorschau|Beispieldaten|simuliert|Revisionskonflikt/);
        expect(tpl).not.toMatch(/class="field[ "]/);
    });
});

describe('v8-118 login page (clinic branch)', () => {
    const html = read('templates/login.html');
    const [, clinicBranch, upstreamBranch] = html.split(/\{% if clinic_login %\}\n|\{% else %\}\n(?=<body)/);

    it('links clinic-v8.css and the v8-ui root only for the clinic form', () => {
        expect(html).toMatch(/\{% if clinic_login %\}<link rel="stylesheet" href="\{\{ url_for\('static', filename='css\/clinic-v8.css'/);
        expect(clinicBranch).toContain('<body class="v8-ui">');
        expect(upstreamBranch).not.toContain('v8-ui');
        expect(upstreamBranch).not.toContain('clinic');
    });

    it('has the page head, the two panels, the password toggle and no remember checkbox', () => {
        expect(clinicBranch).toContain('class="pagehead"');
        expect(clinicBranch).toContain('<div class="eyebrow">Zugriff</div>');
        expect(clinicBranch.match(/<section class="panel"/g)).toHaveLength(2);
        expect(clinicBranch).toContain('data-password-toggle="password"');
        expect(clinicBranch).toContain('js/clinic/login-ui.js');
        expect(clinicBranch).not.toContain('remember');
        expect(clinicBranch).not.toMatch(/Gestaltungsvorschau|Beispieldaten|Demo/);
    });

    it('keeps the CSRF token, the SSO form and the admin login of the upstream page', () => {
        expect(clinicBranch.match(/form\.hidden_tag\(\)/g).length).toBeGreaterThanOrEqual(2);
        expect(clinicBranch).toContain("url_for('auth.sso_login')");
        expect(clinicBranch).toContain('id="admin-login-form"');
    });
});
