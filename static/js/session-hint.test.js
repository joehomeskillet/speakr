/**
 * Tests for static/js/session-hint.js: which responses count as a lost
 * session, and that the banner appears only once. The classic script is
 * evaluated against a minimal window/document stub.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'session-hint.js'), 'utf8');

function load() {
    const appended = [];
    const el = () => ({ style: {}, setAttribute() {}, appendChild() {}, addEventListener() {} });
    const win = { location: { href: 'https://x.test/', reload() {} }, i18n: null };
    const doc = { body: { appendChild: (node) => appended.push(node) }, createElement: el };
    new Function('window', 'document', source)(win, doc);
    return { hint: win.sessionHint, appended };
}

const response = (props) => ({
    status: 200, ok: true, redirected: false, url: '', headers: { get: () => 'application/json' }, ...props,
});

describe('session hint', () => {
    let ctx;
    beforeEach(() => { ctx = load(); });

    it('flags 401 on API calls', () => {
        expect(ctx.hint.isSessionLost('/api/recordings', response({ status: 401, ok: false }))).toBe(true);
    });

    it('flags a redirect to the login form', () => {
        expect(ctx.hint.isSessionLost('/api/x', response({ redirected: true, url: 'https://x.test/login?next=/' }))).toBe(true);
    });

    it('flags HTML answered to an /api/ call', () => {
        expect(ctx.hint.isSessionLost('/api/x', response({ headers: { get: () => 'text/html; charset=utf-8' } }))).toBe(true);
    });

    it('ignores normal JSON, 403 and 500', () => {
        expect(ctx.hint.isSessionLost('/api/x', response({}))).toBe(false);
        expect(ctx.hint.isSessionLost('/api/x', response({ status: 403, ok: false }))).toBe(false);
        expect(ctx.hint.isSessionLost('/api/x', response({ status: 500, ok: false }))).toBe(false);
    });

    it('ignores the login endpoint itself and non-API urls', () => {
        expect(ctx.hint.isSessionLost('/login', response({ status: 401, ok: false }))).toBe(false);
        expect(ctx.hint.isSessionLost('/static/a.js', response({ status: 401, ok: false }))).toBe(false);
    });

    it('shows the banner once for many failures', () => {
        for (let i = 0; i < 3; i++) ctx.hint.inspect('/api/x', response({ status: 401, ok: false }));
        expect(ctx.appended).toHaveLength(1);
    });
});
