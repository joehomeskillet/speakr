import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createTooltip } from '../../../static/js/clinic/tooltip.js';

// Minimal DOM double: vitest runs in the node environment here.
class FakeNode {
    constructor() { this.attrs = {}; this.handlers = {}; this.parent = null; this.children = []; this.className = ''; this.textContent = ''; }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
    removeEventListener(t, fn) { this.handlers[t] = (this.handlers[t] || []).filter((f) => f !== fn); }
    emit(t, ev) { (this.handlers[t] || []).slice().forEach((fn) => fn(ev || {})); }
    appendChild(c) { c.parent = this; this.children.push(c); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
}

function setup() {
    const doc = { body: new FakeNode(), createElement: () => new FakeNode() };
    const trigger = new FakeNode();
    const timers = [];
    const api = {
        document: doc,
        setTimeout: (fn) => { timers.push(fn); return timers.length; },
        clearTimeout: (i) => { timers[i - 1] = null; },
    };
    const tip = createTooltip(trigger, 'Hinweis', api);
    const flush = () => timers.splice(0).forEach((fn) => fn && fn());
    return { doc, trigger, tip, flush };
}

describe('tooltip primitive', () => {
    it('shows on hover and sets aria-describedby', () => {
        const { doc, trigger, tip } = setup();
        trigger.emit('pointerenter');
        expect(tip.isVisible()).toBe(true);
        expect(doc.body.children[0].textContent).toBe('Hinweis');
        expect(doc.body.children[0].getAttribute('role')).toBe('tooltip');
        expect(trigger.getAttribute('aria-describedby')).toBe(tip.id);
        expect(doc.body.children[0].getAttribute('id')).toBe(tip.id);
    });

    it('shows on focus and hides after blur, removing aria-describedby', () => {
        const { trigger, tip, flush } = setup();
        trigger.emit('focus');
        expect(tip.isVisible()).toBe(true);
        trigger.emit('blur');
        flush();
        expect(tip.isVisible()).toBe(false);
        expect(trigger.getAttribute('aria-describedby')).toBe(null);
    });

    it('hides on Escape at once', () => {
        const { doc, trigger, tip } = setup();
        trigger.emit('focus');
        trigger.emit('keydown', { key: 'Enter' });
        expect(tip.isVisible()).toBe(true);
        trigger.emit('keydown', { key: 'Escape' });
        expect(tip.isVisible()).toBe(false);
        expect(doc.body.children.length).toBe(0);
        expect(trigger.getAttribute('aria-describedby')).toBe(null);
    });

    it('stays when the pointer moves from the trigger onto the tooltip', () => {
        const { doc, trigger, tip, flush } = setup();
        trigger.emit('pointerenter');
        trigger.emit('pointerleave');
        doc.body.children[0].emit('pointerenter');
        flush();
        expect(tip.isVisible()).toBe(true);
        doc.body.children[0].emit('pointerleave');
        flush();
        expect(tip.isVisible()).toBe(false);
    });

    it('hides after the pointer leaves without entering the tooltip', () => {
        const { trigger, tip, flush } = setup();
        trigger.emit('pointerenter');
        trigger.emit('pointerleave');
        expect(tip.isVisible()).toBe(true);
        flush();
        expect(tip.isVisible()).toBe(false);
    });

    it('stays while keyboard focus remains after the pointer left', () => {
        const { trigger, tip, flush } = setup();
        trigger.emit('focus');
        trigger.emit('pointerenter');
        trigger.emit('pointerleave');
        flush();
        expect(tip.isVisible()).toBe(true);
    });

    it('destroy removes listeners and the tooltip', () => {
        const { trigger, tip } = setup();
        trigger.emit('focus');
        tip.destroy();
        expect(tip.isVisible()).toBe(false);
        trigger.emit('pointerenter');
        expect(tip.isVisible()).toBe(false);
    });
});

describe('primitives.css contract', () => {
    const css = readFileSync(new URL('../../../static/css/primitives.css', import.meta.url), 'utf8');

    it('gives .btn--icon a 44px hit area on coarse pointers', () => {
        const m = css.match(/@media \(pointer: coarse\)\s*\{([\s\S]*?)\n\}/);
        expect(m).not.toBe(null);
        expect(m[1]).toMatch(/\.btn--icon[^{]*\{[^}]*min-width:\s*44px/);
        expect(m[1]).toMatch(/\.btn--icon[^{]*\{[^}]*min-height:\s*44px/);
    });

    it('keeps the outline on .btn/.field/.tab/.toggle focus-visible', () => {
        const m = css.match(/\.field:focus-visible,\s*\.btn:focus-visible,[^{]*\{([^}]*)\}/);
        expect(m).not.toBe(null);
        expect(m[1]).not.toMatch(/outline:\s*none/);
        expect(m[1]).toMatch(/outline:\s*2px solid var\(--border-focus\)/);
    });

    it('defines the tooltip style', () => {
        expect(css).toMatch(/\.tooltip\s*\{/);
    });
});
