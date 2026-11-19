import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
    COLOR_TOKEN_DECISION,
    FOCUS_INDICATOR,
    STATUSES,
    TOKEN_CONTRACT_VERSION,
    TokenContractError,
    surfaceFor,
} from '../../../static/js/clinic/tokens.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const css = fs.readFileSync(path.join(root, 'static/css/styles.css'), 'utf8');
const source = fs.readFileSync(path.join(root, 'static/js/clinic/tokens.js'), 'utf8');

function braceBlock(text, startIndex) {
    const open = text.indexOf('{', startIndex);
    let depth = 0;
    for (let i = open; i < text.length; i += 1) {
        if (text[i] === '{') depth += 1;
        else if (text[i] === '}') {
            depth -= 1;
            if (depth === 0) return text.slice(open + 1, i);
        }
    }
    throw new Error('unclosed block');
}

function propsIn(body) {
    return new Set([...body.matchAll(/(--[A-Za-z0-9-]+)\s*:/g)].map((match) => match[1]));
}

function blockDeclaring(selector, prop) {
    let from = 0;
    while (from < css.length) {
        const at = css.indexOf(selector, from);
        if (at < 0) break;
        const body = braceBlock(css, at);
        if (body.includes(`${prop}:`)) return propsIn(body);
        from = at + selector.length;
    }
    throw new Error(`no ${selector} block declares ${prop}`);
}

const lightProps = blockDeclaring(':root', '--bg-pending-light');
const darkProps = blockDeclaring('.dark', '--bg-pending-light');

describe('clinic status token contract', () => {
    it('records that no clinic colour palette was decided', () => {
        expect(COLOR_TOKEN_DECISION.located).toBe(false);
        expect(COLOR_TOKEN_DECISION.note).toMatch(/not located/);
        expect(COLOR_TOKEN_DECISION.note).toMatch(/No palette was invented/);
        expect(COLOR_TOKEN_DECISION.themes).toEqual(['light', 'dark']);
        expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(source).not.toMatch(/rgb\(|hsl\(/);
    });

    it('maps each status to semantic variables and a visible label', () => {
        const labels = [];
        for (const status of STATUSES) {
            const surface = surfaceFor(status);
            expect(surface.status).toBe(status);
            expect(surface.label.length).toBeGreaterThan(0);
            expect(surface.background).toMatch(/^var\(--bg-[a-z0-9-]+\)$/);
            expect(surface.color).toMatch(/^var\(--text-[a-z0-9-]+\)$/);
            expect(surface.border).toMatch(/^var\(--border-[a-z0-9-]+\)$/);
            for (const token of Object.values(surface.tokens)) {
                expect(lightProps.has(token)).toBe(true);
                expect(darkProps.has(token)).toBe(true);
            }
            labels.push(surface.label);
        }
        expect(new Set(labels).size).toBe(labels.length);
        expect(STATUSES).toEqual(['pending', 'processing', 'success', 'warning', 'error', 'normal']);
    });

    it('uses one keyboard focus indicator in both themes', () => {
        expect(FOCUS_INDICATOR.applies).toBe(':focus-visible');
        expect(FOCUS_INDICATOR.outline).toBe('2px solid var(--border-focus)');
        expect(FOCUS_INDICATOR.outlineOffset).toBe('2px');
        expect(lightProps.has('--border-focus')).toBe(true);
        expect(darkProps.has('--border-focus')).toBe(true);
        for (const status of STATUSES) {
            const surface = surfaceFor(status);
            expect(surface.focus).toBe(FOCUS_INDICATOR);
            expect(surface.focus.outline).not.toContain(surface.background);
            expect(surface.label).not.toMatch(/^var\(/);
        }
    });

    it('keeps legacy status names on the same surfaces', () => {
        expect(surfaceFor('PENDING')).toBe(surfaceFor('pending'));
        expect(surfaceFor('QUEUED')).toBe(surfaceFor('pending'));
        expect(surfaceFor('PROCESSING')).toBe(surfaceFor('processing'));
        expect(surfaceFor('COMPLETED')).toBe(surfaceFor('success'));
        expect(surfaceFor('SUMMARIZING')).toBe(surfaceFor('processing'));
        expect(surfaceFor('pending', null).status).toBe('pending');
        expect(surfaceFor('FAILED')).toBe(surfaceFor('error'));
    });

    it('refuses an unauthorized caller without a colour', () => {
        expect(() => surfaceFor('error', { authorized: false })).toThrow(TokenContractError);
        try {
            surfaceFor('pending', { authorized: false });
        } catch (error) {
            expect(error.code).toBe('unauthorized');
            expect(error.message).toBe('unauthorized');
            expect(error.message).not.toMatch(/#|var\(--/);
        }
    });

    it('refuses a stale contract version', () => {
        expect(() => surfaceFor('success', { version: TOKEN_CONTRACT_VERSION - 1 })).toThrow(TokenContractError);
        try {
            surfaceFor('success', { version: TOKEN_CONTRACT_VERSION + 1 });
        } catch (error) {
            expect(error.code).toBe('stale_version');
        }
        expect(surfaceFor('success', { version: TOKEN_CONTRACT_VERSION }).status).toBe('success');
    });

    it('retries the same tokens and does not invent a surface on failure', () => {
        expect(surfaceFor('error', { attempt: 2 })).toBe(surfaceFor('error', { attempt: 1 }));
        expect(() => surfaceFor('no-such-status')).toThrow(TokenContractError);
        expect(() => surfaceFor('constructor')).toThrow(TokenContractError);
        expect(() => surfaceFor('toString')).toThrow(TokenContractError);
        try {
            surfaceFor('#ff0000', { attempt: 2 });
        } catch (error) {
            expect(error.code).toBe('failure');
            expect(error.message).not.toMatch(/#|var\(--/);
        }
    });
});
