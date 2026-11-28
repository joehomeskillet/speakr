import { describe, expect, it, vi } from 'vitest';
import {
    FONT_SIZE, PRIMARY_TOOLS, applyReplacements, createCorrectionToolbar, createMenuNav,
    createTextHistory, findMatches, mountCorrectionToolbar,
} from '../../../static/js/clinic/correction-toolbar-ui.js';

function fakeEditor(text) {
    const st = { text, start: 0, end: 0 };
    return {
        st,
        read: () => ({ ...st }),
        write: (v) => { st.text = v.text; st.start = v.start; st.end = v.end; },
        select: (a, b) => { st.start = a; st.end = b; },
        type(t) { st.text = t; st.start = st.end = t.length; },
    };
}

function make(text, extra) {
    const editor = fakeEditor(text);
    const toolbar = createCorrectionToolbar({ editor, ...(extra || {}) });
    return { editor, toolbar };
}

class Node {
    constructor(tag, doc) { this.tag = tag; this.ownerDocument = doc; this.attrs = {}; this.children = []; this.handlers = {}; this.textContent = ''; this.parent = null; }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
    get firstChild() { return this.children[0] || null; }
    addEventListener(t, fn) { (this.handlers[t] = this.handlers[t] || []).push(fn); }
    focus() { this.ownerDocument.active = this; }
    fire(type, init) {
        const e = { type, key: '', defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...(init || {}) };
        for (let n = this; n; n = n.parent) {
            (n.handlers[type] || []).forEach((fn) => fn(e));
            if (e.stopped) break;
        }
        return e;
    }
    all() { return this.children.filter((c) => !c.textNode).flatMap((c) => [c, ...c.all()]); }
}
function fakeDoc() {
    const doc = { active: null, createElement: (t) => new Node(t, doc), createTextNode: (t) => ({ text: t, textNode: true }) };
    return doc;
}
function mount(toolbar, opts) {
    const doc = fakeDoc();
    const host = new Node('div', doc);
    const view = mountCorrectionToolbar(host, toolbar, opts);
    return { doc, host, view };
}

describe('text history', () => {
    it('undoes and redoes, restoring text and selection', () => {
        const { editor, toolbar } = make('eins');
        editor.type('eins zwei');
        toolbar.notifyChange();
        editor.type('eins zwei drei');
        toolbar.undo();
        expect(editor.st.text).toBe('eins');
        toolbar.redo();
        expect(editor.st.text).toBe('eins zwei drei');
    });

    it('discards the redo branch when a new edit follows an undo', () => {
        const h = createTextHistory({ text: 'a', start: 1, end: 1 });
        h.record({ text: 'ab', start: 2, end: 2 });
        h.record({ text: 'abc', start: 3, end: 3 });
        expect(h.undo().text).toBe('ab');
        expect(h.canRedo()).toBe(true);
        h.record({ text: 'abX', start: 3, end: 3 });
        expect(h.canRedo()).toBe(false);
        expect(h.redo()).toBeNull();
        expect(h.undo().text).toBe('ab');
        expect(h.undo().text).toBe('a');
    });

    it('undoes a confirmed insertion back to the unsaved draft and captured selection', () => {
        const { editor, toolbar } = make('Text');
        editor.type('Neuer Text');
        toolbar.notifyChange();
        toolbar.insertText('Befund', { text: 'Neuer Text', start: 0, end: 5 }, 3);
        expect(editor.st).toMatchObject({ text: 'Befund Text', start: 3, end: 3 });
        expect(toolbar.undo()).toBe(true);
        expect(editor.st).toMatchObject({ text: 'Neuer Text', start: 0, end: 5 });
    });

    it('discards redo through the controller when typing after undo', () => {
        const { editor, toolbar } = make('x');
        toolbar.insertParagraph();
        toolbar.undo();
        expect(toolbar.getState().canRedo).toBe(true);
        editor.type('xy');
        toolbar.notifyChange();
        expect(toolbar.getState().canRedo).toBe(false);
        expect(toolbar.redo()).toBe(false);
        expect(editor.st.text).toBe('xy');
    });

    it('coalesces consecutive typing into one step', () => {
        const { editor, toolbar } = make('');
        for (const t of ['a', 'ab', 'abc']) { editor.type(t); toolbar.notifyChange(); }
        expect(toolbar.history.size()).toBe(2);
        toolbar.undo();
        expect(editor.st.text).toBe('');
    });

    it('bounds the history', () => {
        const h = createTextHistory({ text: '', start: 0, end: 0 }, 3);
        for (let i = 1; i <= 10; i++) h.record({ text: 'x'.repeat(i), start: i, end: i });
        expect(h.size()).toBe(4);
    });
});

describe('search and replace', () => {
    it('finds literal matches with optional case and unicode safely', () => {
        expect(findMatches('a.b a.b', 'a.b', true)).toHaveLength(2);
        expect(findMatches('aXb', 'a.b', true)).toHaveLength(0);
        expect(findMatches('Links links', 'links', true)).toHaveLength(1);
        expect(findMatches('Links links', 'links', false)).toHaveLength(2);
        expect(findMatches('\u{1F600} ä ä', 'ä', false)).toEqual([{ start: 3, end: 4 }, { start: 5, end: 6 }]);
        expect(findMatches('abc', '', false)).toEqual([]);
    });

    it('steps through matches cyclically without changing the text', () => {
        const { editor, toolbar } = make('ab ab ab');
        toolbar.setQuery('ab');
        expect(toolbar.step(1).start).toBe(0);
        expect(toolbar.step(1).start).toBe(3);
        expect(toolbar.step(1).start).toBe(6);
        expect(toolbar.step(1).start).toBe(0);
        expect(toolbar.step(-1).start).toBe(6);
        expect(editor.st.text).toBe('ab ab ab');
        expect(toolbar.history.size()).toBe(1);
    });

    it('replace all is exactly one undo step', () => {
        const { editor, toolbar } = make('links und links und links');
        editor.st.start = editor.st.end = 25;
        toolbar.setQuery('links');
        toolbar.setReplacement('rechts');
        expect(toolbar.replaceAll()).toBe(true);
        expect(editor.st.text).toBe('rechts und rechts und rechts');
        expect(toolbar.history.size()).toBe(2);
        toolbar.undo();
        expect(editor.st.text).toBe('links und links und links');
        expect(toolbar.getState().canUndo).toBe(false);
        toolbar.redo();
        expect(editor.st.text).toBe('rechts und rechts und rechts');
    });

    it('replaces the selected match only, as one step', () => {
        const { editor, toolbar } = make('aa bb aa');
        toolbar.setQuery('aa');
        toolbar.setReplacement('c');
        toolbar.step(1);
        toolbar.step(1);
        expect(toolbar.replaceCurrent()).toBe(true);
        expect(editor.st.text).toBe('aa bb c');
        expect(editor.st.start).toBe(7);
        toolbar.undo();
        expect(editor.st.text).toBe('aa bb aa');
    });

    it('maps the caret through replacements', () => {
        expect(applyReplacements('aaXbb', [{ start: 0, end: 2 }], 'z', 5)).toEqual({ text: 'zXbb', caret: 4 });
        expect(applyReplacements('aaXbb', [{ start: 3, end: 5 }], 'zzz', 1)).toEqual({ text: 'aaXzzz', caret: 1 });
    });

    it('does nothing and reports zero matches for an unknown query', () => {
        const { editor, toolbar } = make('abc');
        toolbar.setQuery('q');
        expect(toolbar.replaceAll()).toBe(false);
        expect(toolbar.step(1)).toBeNull();
        expect(editor.st.text).toBe('abc');
    });
});

describe('font size', () => {
    it('starts at 16 and stays within 14..22 in steps of 2', () => {
        const seen = [];
        const { editor, toolbar } = make('t', { onFontSize: (px) => seen.push(px) });
        expect(toolbar.getState().fontSize).toBe(16);
        for (let i = 0; i < 6; i++) toolbar.fontUp();
        expect(toolbar.getState().fontSize).toBe(FONT_SIZE.max);
        expect(toolbar.getState().fontMax).toBe(true);
        for (let i = 0; i < 10; i++) toolbar.fontDown();
        expect(toolbar.getState().fontSize).toBe(FONT_SIZE.min);
        expect(seen).toEqual([18, 20, 22, 20, 18, 16, 14]);
        expect(toolbar.setFontSize(99)).toBe(22);
        expect(toolbar.setFontSize(15)).toBe(14);
        expect(editor.st.text).toBe('t');
        expect(toolbar.history.size()).toBe(1);
    });
});

describe('focus mode', () => {
    it('keeps text, selection, caret and the lease notice, even if the layout switch loses them', () => {
        const { editor, toolbar } = make('Befund links', {
            getLeaseNotice: () => 'Bearbeitung durch Sie',
            onFocusMode: () => { editor.st.start = editor.st.end = 0; },
        });
        editor.st.start = 7;
        editor.st.end = 12;
        expect(toolbar.toggleFocus()).toBe(true);
        expect(editor.st).toMatchObject({ text: 'Befund links', start: 7, end: 12 });
        expect(toolbar.getState().leaseNotice).toBe('Bearbeitung durch Sie');
        expect(toolbar.toggleFocus()).toBe(false);
        expect(editor.st).toMatchObject({ start: 7, end: 12 });
        expect(toolbar.history.size()).toBe(1);
    });

    it('Escape closes the search panel first and focus mode last, keeping text', () => {
        const { editor, toolbar } = make('Text');
        toolbar.toggleFocus();
        toolbar.openSearch();
        expect(toolbar.escape(false)).toBe('search');
        expect(toolbar.getState().focusMode).toBe(true);
        expect(toolbar.escape(false)).toBe('focus');
        expect(toolbar.getState().focusMode).toBe(false);
        expect(toolbar.escape(false)).toBeNull();
        expect(editor.st.text).toBe('Text');
    });

    it('Escape reports the open menu before anything else', () => {
        const { toolbar } = make('Text');
        toolbar.toggleFocus();
        expect(toolbar.escape(true)).toBe('menu');
        expect(toolbar.getState().focusMode).toBe(true);
    });
});

describe('lease loss', () => {
    it('blocks mutating commands and shortcuts but not view tools', () => {
        let allowed = false;
        const { editor, toolbar } = make('abc', { canEdit: () => allowed });
        toolbar.setQuery('b');
        toolbar.setReplacement('X');
        expect(toolbar.replaceAll()).toBe(false);
        expect(toolbar.insertParagraph()).toBe(false);
        expect(toolbar.shortcut({ ctrlKey: true, key: 'z' })).toBe(true);
        expect(editor.st.text).toBe('abc');
        expect(toolbar.getState().readOnlyReason).toMatch(/Schreibgeschützt/);
        expect(toolbar.fontUp()).toBe(18);
        expect(toolbar.toggleFocus()).toBe(true);
        allowed = true;
        expect(toolbar.replaceAll()).toBe(true);
    });
});

describe('shortcuts', () => {
    it('maps only the documented combinations', () => {
        const { toolbar } = make('abc');
        expect(toolbar.shortcut({ ctrlKey: true, key: 'f' })).toBe(true);
        expect(toolbar.getState().searchOpen).toBe(true);
        expect(toolbar.shortcut({ metaKey: true, shiftKey: true, key: 'F' })).toBe(true);
        expect(toolbar.getState().focusMode).toBe(true);
        expect(toolbar.shortcut({ ctrlKey: true, key: 'Enter' })).toBe(true);
        expect(toolbar.shortcut({ ctrlKey: true, key: 'p' })).toBe(false);
        expect(toolbar.shortcut({ key: 'z' })).toBe(false);
        expect(toolbar.shortcut({ ctrlKey: true, altKey: true, key: 'z' })).toBe(false);
    });
});

describe('overflow menu keyboard model', () => {
    it('opens with arrows, wraps, supports Home/End and activates', () => {
        const nav = createMenuNav(() => 3);
        expect(nav.key('ArrowDown')).toEqual({ handled: true, action: 'open' });
        expect(nav.active()).toBe(0);
        expect(nav.key('ArrowDown').index).toBe(1);
        expect(nav.key('End').index).toBe(2);
        expect(nav.key('ArrowDown').index).toBe(0);
        expect(nav.key('ArrowUp').index).toBe(2);
        expect(nav.key('Home').index).toBe(0);
        expect(nav.key('Enter')).toEqual({ handled: true, action: 'activate', index: 0 });
        expect(nav.key(' ').action).toBe('activate');
        expect(nav.key('Escape')).toEqual({ handled: true, action: 'close' });
        expect(nav.isOpen()).toBe(false);
        expect(nav.key('x')).toEqual({ handled: false });
        nav.key('ArrowUp');
        expect(nav.active()).toBe(2);
    });
});

describe('mounted toolbar', () => {
    it('gives every button an accessible name and hides decorative icons', () => {
        const { toolbar } = make('abc', { onShowShortcuts: () => {} });
        const { host, view } = mount(toolbar);
        view.openMenu(0);
        const buttons = host.all().filter((n) => n.tag === 'button');
        expect(buttons.length).toBeGreaterThanOrEqual(PRIMARY_TOOLS.length + 5);
        for (const b of buttons) expect((b.getAttribute('aria-label') || '').trim().length).toBeGreaterThan(0);
        for (const i of host.all().filter((n) => n.tag === 'i')) expect(i.getAttribute('aria-hidden')).toBe('true');
        for (const t of PRIMARY_TOOLS) expect(view.buttons[t.id].getAttribute('aria-label')).toBe(t.name);
        expect(host.all().find((n) => n.tag === 'input' && !n.getAttribute('aria-label'))).toBeUndefined();
    });

    it('wires the tooltip primitive with name and shortcut and detaches it on destroy', () => {
        const { toolbar } = make('abc');
        const detach = vi.fn();
        const attachTooltip = vi.fn(() => detach);
        const { view } = mount(toolbar, { attachTooltip });
        expect(attachTooltip).toHaveBeenCalledWith(view.buttons.undo, 'Rückgängig (Strg/Cmd+Z)');
        expect(attachTooltip).toHaveBeenCalledWith(view.buttons.fontUp, 'Schrift vergrössern');
        view.destroy();
        expect(detach).toHaveBeenCalledTimes(PRIMARY_TOOLS.length);
    });

    it('opens the menu with the arrow key, moves focus, activates with Enter and returns focus', () => {
        const { editor, toolbar } = make('ab');
        const { doc, view } = mount(toolbar);
        const more = view.buttons.more;
        more.fire('keydown', { key: 'ArrowDown' });
        expect(view.isMenuOpen()).toBe(true);
        expect(more.getAttribute('aria-expanded')).toBe('true');
        expect(doc.active.getAttribute('data-item')).toBe('paragraph');
        view.menu.children[0].fire('keydown', { key: 'ArrowUp' });
        expect(doc.active.getAttribute('data-item')).toBe('paragraph');
        view.menu.children[0].fire('keydown', { key: 'Enter' });
        expect(view.isMenuOpen()).toBe(false);
        expect(doc.active).toBe(more);
        expect(editor.st.text).toBe('\n\nab');
    });

    it('closes the menu with Escape, returns focus and keeps focus mode', () => {
        const { editor, toolbar } = make('Text');
        const { host, doc, view } = mount(toolbar);
        view.buttons.focus.fire('click');
        expect(view.buttons.focus.getAttribute('aria-pressed')).toBe('true');
        expect(host.getAttribute('data-focus-mode')).toBe('true');
        view.buttons.more.fire('click');
        expect(view.isMenuOpen()).toBe(true);
        const first = view.menu.children[0].fire('keydown', { key: 'Escape' });
        expect(first.defaultPrevented).toBe(true);
        expect(view.isMenuOpen()).toBe(false);
        expect(doc.active).toBe(view.buttons.more);
        expect(toolbar.getState().focusMode).toBe(true);
        view.buttons.more.fire('keydown', { key: 'Escape' });
        expect(toolbar.getState().focusMode).toBe(false);
        expect(editor.st.text).toBe('Text');
    });

    it('does not handle Escape that a tooltip already consumed', () => {
        const { toolbar } = make('Text');
        const { view } = mount(toolbar);
        view.buttons.focus.fire('click');
        view.buttons.undo.fire('keydown', { key: 'Escape', defaultPrevented: true });
        expect(toolbar.getState().focusMode).toBe(true);
    });

    it('keeps disabled commands focusable and points to the reason', () => {
        let allowed = false;
        const { toolbar } = make('abc', { canEdit: () => allowed, getLeaseNotice: () => 'Bearbeitung durch Dr. Muster' });
        const { host, view } = mount(toolbar);
        toolbar.notifyChange();
        expect(view.buttons.undo.getAttribute('aria-disabled')).toBe('true');
        expect(view.buttons.undo.getAttribute('disabled')).toBeNull();
        expect(view.buttons.undo.getAttribute('aria-describedby')).toBe('clinic-toolbar-reason');
        const byId = (id) => host.all().find((n) => n.getAttribute('data-testid') === id);
        expect(byId('toolbar-reason').textContent).toMatch(/Schreibgeschützt/);
        expect(byId('lease-notice').textContent).toBe('Bearbeitung durch Dr. Muster');
        expect(view.panelButtons.replaceAll.getAttribute('aria-disabled')).toBe('true');
        allowed = true;
        toolbar.refresh();
        expect(view.buttons.undo.getAttribute('aria-describedby')).toBeNull();
    });

    it('runs search and replace-all from the panel as one undo step', () => {
        const { editor, toolbar } = make('a a a');
        const { view } = mount(toolbar);
        view.buttons.search.fire('click');
        expect(view.buttons.search.getAttribute('aria-expanded')).toBe('true');
        view.query.value = 'a';
        view.query.fire('input');
        view.replacement.value = 'b';
        view.replacement.fire('input');
        view.panelButtons.replaceAll.fire('click');
        expect(editor.st.text).toBe('b b b');
        view.buttons.undo.fire('click');
        expect(editor.st.text).toBe('a a a');
    });

    it('disables the font buttons at the bounds and shows the size', () => {
        const { toolbar } = make('abc');
        const { host, view } = mount(toolbar);
        const size = () => host.all().find((n) => n.getAttribute('data-testid') === 'font-size').textContent;
        expect(size()).toBe('16 px');
        view.buttons.fontUp.fire('click'); view.buttons.fontUp.fire('click'); view.buttons.fontUp.fire('click');
        expect(size()).toBe('22 px');
        expect(view.buttons.fontUp.getAttribute('aria-disabled')).toBe('true');
    });
});
