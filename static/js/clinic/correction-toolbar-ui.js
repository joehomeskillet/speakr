/**
 * Icon toolbar for the clinic correction editor (V8 P06, UI-ED cases).
 *
 * Framework-free and DOM-free at its core: createCorrectionToolbar() is a
 * controller that works on a plain text editor port, so it is unit-testable
 * without a browser. mountCorrectionToolbar() builds the buttons, the
 * search/replace panel and the overflow menu from controller state.
 *
 * Editor port (supplied by the mount package, wraps the editor text area):
 *   read()                  -> { text, start, end }  current text and selection
 *   write({ text, start, end })  set text and selection (no focus change)
 *   select(start, end)      optional, selection only; falls back to write()
 *
 * Behaviour (EDITOR_AUFTRAG_V8.md sections 3 and 4): undo/redo with redo discard
 * on a new edit; "Alle ersetzen" is one undo step; font 14..22 px, step 2,
 * start 16, display only; focus mode keeps text, selection, caret and the lease
 * notice, Escape closes menu, then search panel, then focus mode; the overflow
 * menu takes ArrowUp/Down, Home/End, Enter/Space, Escape; on lease loss mutating
 * commands and shortcuts are blocked and the reason stays reachable.
 *
 * Wiring hook for the later mount package (v8-43..v8-45 own compose-correction.js):
 *   const toolbar = createCorrectionToolbar({ editor, canEdit: () => c.canEdit.value,
 *       getLeaseNotice: () => c.leaseNotice.value, onFontSize, onFocusMode });
 *   const view = mountCorrectionToolbar(hostElement, toolbar, { attachTooltip });
 *   call toolbar.notifyChange() from the text area "input" handler and
 *   toolbar.refresh() whenever the lease changes; view.destroy() on unmount.
 *   attachTooltip(element, text) is the tooltip primitive of package v8-12
 *   (static/js/clinic/tooltip.js, not on clinic/main yet) and returns a detach
 *   function. Without it only the accessible name is set.
 *
 * Nothing here touches browser storage. Clinical text lives in the editor only.
 */

export const FONT_SIZE = Object.freeze({ min: 14, max: 22, step: 2, start: 16 });
export const HISTORY_LIMIT = 500;
const PARAGRAPH_BREAK = '\n\n';

export const TEXT = Object.freeze({
    toolbar: 'Werkzeuge der Korrektur',
    undo: 'Rückgängig',
    redo: 'Wiederholen',
    search: 'Suchen und Ersetzen',
    fontDown: 'Schrift verkleinern',
    fontUp: 'Schrift vergrössern',
    focus: 'Fokusmodus',
    more: 'Weitere Werkzeuge',
    paragraph: 'Absatz einfügen',
    marker: 'Prüfmarke einfügen',
    markerUnavailable: 'Prüfmarken setzt der Server. Das Einfügen aus der Werkzeugleiste ist noch nicht angebunden; es wird kein Text in das Transkript geschrieben.',
    shortcuts: 'Tastenkürzel',
    query: 'Suchen nach',
    replacement: 'Ersetzen durch',
    caseSensitive: 'Gross-/Kleinschreibung beachten',
    prev: 'Vorheriger Treffer',
    next: 'Nächster Treffer',
    replaceOne: 'Ersetzen',
    replaceAll: 'Alle ersetzen',
    closeSearch: 'Suche schliessen',
    searchPanel: 'Suchen und Ersetzen',
    noMatches: 'Keine Treffer',
    readOnly: 'Schreibgeschützt: Bearbeitung zuerst anfordern.',
    fontMin: 'Kleinste Schriftgrösse erreicht.',
    fontMax: 'Grösste Schriftgrösse erreicht.',
    nothingToUndo: 'Nichts zum Rückgängigmachen.',
    nothingToRedo: 'Nichts zum Wiederholen.',
});

const MOD = 'Strg/Cmd+';

export const PRIMARY_TOOLS = Object.freeze([
    { id: 'undo', name: TEXT.undo, shortcut: MOD + 'Z', icon: 'fa-undo', mutating: true },
    { id: 'redo', name: TEXT.redo, shortcut: MOD + 'Umschalt+Z', icon: 'fa-redo', mutating: true },
    { id: 'search', name: TEXT.search, shortcut: MOD + 'F', icon: 'fa-search', toggle: true },
    { id: 'fontDown', name: TEXT.fontDown, shortcut: '', glyph: 'A−' },
    { id: 'fontUp', name: TEXT.fontUp, shortcut: '', glyph: 'A+' },
    { id: 'focus', name: TEXT.focus, shortcut: MOD + 'Umschalt+F', icon: 'fa-expand', toggle: true },
    { id: 'more', name: TEXT.more, shortcut: '', icon: 'fa-ellipsis-h', menu: true },
]);

/** Literal search. The u flag folds case without changing string indices. */
export function findMatches(text, query, caseSensitive) {
    if (typeof text !== 'string' || typeof query !== 'string' || query === '') return [];
    const re = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), caseSensitive ? 'gu' : 'giu');
    const out = [];
    let m;
    while ((m = re.exec(text)) !== null) {
        out.push({ start: m.index, end: m.index + m[0].length });
        if (m[0].length === 0) re.lastIndex += 1;
    }
    return out;
}

/** Replace the given non-overlapping, ascending ranges; maps the caret through the edit. */
export function applyReplacements(text, ranges, replacement, caret) {
    let out = '';
    let last = 0;
    let newCaret = caret;
    let shift = 0;
    for (const r of ranges) {
        out += text.slice(last, r.start) + replacement;
        last = r.end;
        const delta = replacement.length - (r.end - r.start);
        if (caret >= r.end) shift += delta;
        else if (caret > r.start) newCaret = r.start + replacement.length - shift;
    }
    out += text.slice(last);
    return { text: out, caret: Math.max(0, Math.min(out.length, newCaret + shift)) };
}

/** Linear history with a present entry; a new edit after undo discards redo. */
export function createTextHistory(initial, limit) {
    const max = limit || HISTORY_LIMIT;
    let past = [];
    let future = [];
    let present = { ...initial, typing: false };
    return {
        present: () => present,
        canUndo: () => past.length > 0,
        canRedo: () => future.length > 0,
        size: () => past.length + 1 + future.length,
        syncSelection(start, end) { present = { ...present, start, end }; },
        record(entry, options) {
            if (entry.text === present.text) { present = { ...present, start: entry.start, end: entry.end }; return false; }
            const typing = !!(options && options.typing);
            if (!(typing && present.typing && past.length)) {
                past.push(present);
                if (past.length > max) past = past.slice(past.length - max);
            }
            present = { ...entry, typing };
            future = [];
            return true;
        },
        undo() {
            if (!past.length) return null;
            future.push(present);
            present = { ...past.pop(), typing: false };
            return present;
        },
        redo() {
            if (!future.length) return null;
            past.push(present);
            present = { ...future.pop(), typing: false };
            return present;
        },
    };
}

/** Keyboard model of the overflow menu (menu button pattern). */
export function createMenuNav(count) {
    let open = false;
    let active = -1;
    const last = () => Math.max(0, count() - 1);
    return {
        isOpen: () => open,
        active: () => active,
        open(index) { open = true; active = index === 'last' ? last() : (index || 0); },
        close() { open = false; active = -1; },
        /** @returns {{handled: boolean, action?: string, index?: number}} */
        key(key) {
            if (!open) {
                if (key === 'ArrowDown' || key === 'Enter' || key === ' ') { this.open(0); return { handled: true, action: 'open' }; }
                if (key === 'ArrowUp') { this.open('last'); return { handled: true, action: 'open' }; }
                return { handled: false };
            }
            const n = count();
            if (key === 'ArrowDown') { active = n ? (active + 1) % n : -1; return { handled: true, action: 'move', index: active }; }
            if (key === 'ArrowUp') { active = n ? (active - 1 + n) % n : -1; return { handled: true, action: 'move', index: active }; }
            if (key === 'Home') { active = n ? 0 : -1; return { handled: true, action: 'move', index: active }; }
            if (key === 'End') { active = n ? n - 1 : -1; return { handled: true, action: 'move', index: active }; }
            if (key === 'Enter' || key === ' ') return { handled: true, action: 'activate', index: active };
            if (key === 'Escape') { this.close(); return { handled: true, action: 'close' }; }
            if (key === 'Tab') { this.close(); return { handled: false, action: 'close' }; }
            return { handled: false };
        },
    };
}

/**
 * @param {{editor: object, canEdit?: () => boolean, getLeaseNotice?: () => string,
 *          onFontSize?: (px: number) => void, onFocusMode?: (active: boolean) => void,
 *          onShowShortcuts?: () => void, extraTools?: Array<object>}} options
 */
export function createCorrectionToolbar(options) {
    const opts = options || {};
    const editor = opts.editor;
    const canEdit = opts.canEdit || (() => true);
    const listeners = new Set();
    const initial = editor.read();
    let history = createTextHistory({ text: initial.text, start: initial.start, end: initial.end });
    const state = { fontSize: FONT_SIZE.start, focusMode: false, searchOpen: false, query: '', replacement: '', caseSensitive: false, matches: [], current: -1, message: '' };
    function applyEntry(entry) {
        editor.write({ text: entry.text, start: entry.start, end: entry.end });
    }
    function select(start, end) {
        if (typeof editor.select === 'function') editor.select(start, end);
        else editor.write({ ...editor.read(), start, end });
    }
    function emit() {
        listeners.forEach((fn) => fn(snapshot()));
    }
    function recount() {
        state.matches = findMatches(editor.read().text, state.query, state.caseSensitive);
        if (state.current >= state.matches.length) state.current = state.matches.length ? 0 : -1;
    }

    /** Take over typing and selection changes made outside the toolbar. */
    function sync() {
        const cur = editor.read();
        const now = history.present();
        if (cur.text !== now.text) history.record({ text: cur.text, start: cur.start, end: cur.end }, { typing: true });
        else history.syncSelection(cur.start, cur.end);
        return cur;
    }
    function snapshot() {
        const editable = canEdit();
        return {
            ...state,
            matches: state.matches.length,
            canEdit: editable,
            canUndo: editable && history.canUndo(),
            canRedo: editable && history.canRedo(),
            leaseNotice: opts.getLeaseNotice ? opts.getLeaseNotice() : '',
            readOnlyReason: editable ? '' : TEXT.readOnly,
            fontMin: state.fontSize <= FONT_SIZE.min,
            fontMax: state.fontSize >= FONT_SIZE.max,
        };
    }
    function blocked() {
        if (canEdit()) return false;
        state.message = TEXT.readOnly;
        emit();
        return true;
    }
    function commit(text, start, end) {
        history.record({ text, start, end }, { typing: false });
        applyEntry({ text, start, end });
        recount();
        state.message = '';
        emit();
    }
    const api = {
        get history() { return history; },
        /** Drops undo/redo after the editor text was replaced from outside (load, server text). */
        reset() {
            const cur = editor.read();
            history = createTextHistory({ text: cur.text, start: cur.start, end: cur.end });
            recount();
            emit();
        },
        getState: snapshot,
        subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
        /** Call from the text area input handler and when the lease changes. */
        notifyChange() { sync(); recount(); emit(); },
        refresh() { emit(); },
        undo() {
            if (blocked()) return false;
            sync();
            const e = history.undo();
            if (!e) { state.message = TEXT.nothingToUndo; emit(); return false; }
            applyEntry(e); recount(); state.message = ''; emit();
            return true;
        },
        redo() {
            if (blocked()) return false;
            sync();
            const e = history.redo();
            if (!e) { state.message = TEXT.nothingToRedo; emit(); return false; }
            applyEntry(e); recount(); state.message = ''; emit();
            return true;
        },
        insertText(value, range = editor.read(), offset = value.length) {
            if (blocked()) return false;
            sync();
            const start = Math.min(range.start, range.end);
            const end = Math.max(range.start, range.end);
            history.syncSelection(start, end);
            const caret = start + offset;
            commit(range.text.slice(0, start) + value + range.text.slice(end), caret, caret);
            return true;
        },
        insertParagraph() { return api.insertText(PARAGRAPH_BREAK); },
        // Review marks (W11) are server-side flags that block release. The toolbar never writes demo text into
        // the clinical transcript; until it is wired to the server mark path it only explains why it does nothing.
        insertMarker() {
            state.message = TEXT.markerUnavailable;
            emit();
            return false;
        },
        openSearch() {
            state.searchOpen = true;
            sync();
            recount();
            emit();
        },
        closeSearch() { state.searchOpen = false; emit(); },
        toggleSearch() { if (state.searchOpen) api.closeSearch(); else api.openSearch(); },
        setQuery(query) { state.query = String(query); state.current = -1; recount(); emit(); },
        setReplacement(value) { state.replacement = String(value); emit(); },
        setCaseSensitive(on) { state.caseSensitive = !!on; state.current = -1; recount(); emit(); },
        /** Cyclic step through the matches; selects the match without changing text. */
        step(direction) {
            recount();
            const n = state.matches.length;
            if (!n) { emit(); return null; }
            const dir = direction < 0 ? -1 : 1;
            if (state.current < 0) {
                const caret = dir > 0 ? editor.read().end : editor.read().start;
                const at = dir > 0 ? state.matches.findIndex((m) => m.start >= caret) : state.matches.map((m) => m.start < caret).lastIndexOf(true);
                state.current = at >= 0 ? at : (dir > 0 ? 0 : n - 1);
            } else state.current = (state.current + dir + n) % n;
            const m = state.matches[state.current];
            select(m.start, m.end);
            emit();
            return m;
        },
        replaceCurrent() {
            if (blocked()) return false;
            const cur = sync();
            recount();
            if (!state.matches.length) return false;
            let idx = state.matches.findIndex((m) => m.start === Math.min(cur.start, cur.end) && m.end === Math.max(cur.start, cur.end));
            if (idx < 0) idx = state.current >= 0 ? state.current : 0;
            const m = state.matches[idx];
            const r = applyReplacements(cur.text, [m], state.replacement, m.start);
            const caret = m.start + state.replacement.length;
            state.current = idx;
            commit(r.text, caret, caret);
            return true;
        },
        /** All matches in one history entry: a single undo restores the whole text. */
        replaceAll() {
            if (blocked()) return false;
            const cur = sync();
            recount();
            if (!state.matches.length) return false;
            const r = applyReplacements(cur.text, state.matches, state.replacement, cur.end);
            state.current = -1;
            commit(r.text, r.caret, r.caret);
            return true;
        },
        setFontSize(px) {
            const next = Math.max(FONT_SIZE.min, Math.min(FONT_SIZE.max, Math.round(Number(px) || FONT_SIZE.start)));
            state.fontSize = next - ((next - FONT_SIZE.min) % FONT_SIZE.step);
            if (opts.onFontSize) opts.onFontSize(state.fontSize);
            emit();
            return state.fontSize;
        },
        fontDown() {
            if (state.fontSize <= FONT_SIZE.min) { state.message = TEXT.fontMin; emit(); return state.fontSize; }
            state.message = '';
            return api.setFontSize(state.fontSize - FONT_SIZE.step);
        },
        fontUp() {
            if (state.fontSize >= FONT_SIZE.max) { state.message = TEXT.fontMax; emit(); return state.fontSize; }
            state.message = '';
            return api.setFontSize(state.fontSize + FONT_SIZE.step);
        },
        /** Reversible; text, selection and caret are re-applied if the layout switch lost them. */
        toggleFocus(force) {
            const before = editor.read();
            state.focusMode = typeof force === 'boolean' ? force : !state.focusMode;
            if (opts.onFocusMode) opts.onFocusMode(state.focusMode);
            const after = editor.read();
            if (after.text !== before.text) editor.write({ text: before.text, start: before.start, end: before.end });
            else if (after.start !== before.start || after.end !== before.end) select(before.start, before.end);
            emit();
            return state.focusMode;
        },
        /** Overflow menu entries: built-ins plus tools handed in by the mount package. */
        menuItems() {
            const items = [{ id: 'paragraph', name: TEXT.paragraph, shortcut: MOD + 'Enter', mutating: true, run: api.insertParagraph }];
            if (opts.onShowShortcuts) items.push({ id: 'shortcuts', name: TEXT.shortcuts, shortcut: 'F1', run: opts.onShowShortcuts });
            for (const t of opts.extraTools || []) items.push({ mutating: false, shortcut: '', ...t });
            return items;
        },
        /**
         * Escape closes the current tool first (menu, then search panel) and
         * only then focus mode. Never discards text.
         * @returns {'menu'|'search'|'focus'|null}
         */
        escape(menuOpen) {
            if (menuOpen) return 'menu';
            if (state.searchOpen) { api.closeSearch(); return 'search'; }
            if (state.focusMode) { api.toggleFocus(false); return 'focus'; }
            return null;
        },
        /** Editor shortcuts; only a handled combination may suppress the browser default. */
        shortcut(e) {
            const mod = e.ctrlKey || e.metaKey;
            const key = String(e.key || '').toLowerCase();
            if (key === 'f1' && opts.onShowShortcuts) { opts.onShowShortcuts(); return true; }
            if (!mod || e.altKey) return false;
            if (key === 'z') { if (e.shiftKey) api.redo(); else api.undo(); return true; }
            if (key === 'y' && !e.shiftKey) { api.redo(); return true; }
            if (key === 'f' && e.shiftKey) { api.toggleFocus(); return true; }
            if (key === 'f') { api.openSearch(); return true; }
            if (key === 'h' && !e.shiftKey) { api.openSearch(); return true; }
            if (key === 'm' && e.shiftKey) { api.insertMarker(); return true; }
            if (key === 'enter') { api.insertParagraph(); return true; }
            return false;
        },
    };
    return api;
}

function el(doc, tag, attrs, children) {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v === null || v === undefined || v === false) continue;
        node.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of children || []) node.appendChild(typeof c === 'string' ? doc.createTextNode(c) : c);
    return node;
}

/**
 * Build the toolbar into hostElement. Every button has an accessible name,
 * icons are aria-hidden. Disabled commands use aria-disabled so they stay
 * focusable and the reason stays reachable via aria-describedby.
 * @param {{attachTooltip?: (el: object, text: string) => (() => void)}} mountOptions
 */
export function mountCorrectionToolbar(hostElement, toolbar, mountOptions) {
    const mo = mountOptions || {};
    const doc = hostElement.ownerDocument || globalThis.document;
    const detachers = [];
    const buttons = {};
    const nav = createMenuNav(() => toolbar.menuItems().length);
    function tip(node, name, shortcut) {
        const text = shortcut ? name + ' (' + shortcut + ')' : name;
        if (mo.attachTooltip) { const d = mo.attachTooltip(node, text); if (typeof d === 'function') detachers.push(d); }
    }
    function iconButton(tool) {
        const children = tool.glyph
            ? [el(doc, 'span', { 'aria-hidden': 'true' }, [tool.glyph])]
            : [el(doc, 'i', { class: 'fas ' + tool.icon, 'aria-hidden': 'true' })];
        const btn = el(doc, 'button', {
            type: 'button', class: 'icon-button', 'aria-label': tool.name, title: tool.name,
            'data-tool': tool.id, 'aria-keyshortcuts': tool.shortcut ? tool.shortcut.replace('Strg/Cmd+', 'Control+') : null,
            'aria-pressed': tool.toggle && tool.id === 'focus' ? 'false' : null,
            'aria-expanded': tool.id === 'search' || tool.menu ? 'false' : null,
            'aria-haspopup': tool.menu ? 'menu' : null,
        }, children);
        tip(btn, tool.name, tool.shortcut);
        buttons[tool.id] = btn;
        return btn;
    }
    const bar = el(doc, 'div', { class: 'editorbar symbol-toolbar', role: 'toolbar', 'aria-label': TEXT.toolbar, 'data-testid': 'toolbar' });
    for (const tool of PRIMARY_TOOLS) bar.appendChild(iconButton(tool));
    const sizeLabel = el(doc, 'span', { class: 'font-size', 'data-testid': 'font-size', role: 'status' });
    bar.appendChild(sizeLabel);
    const notice = el(doc, 'p', { class: 'hint', role: 'status', 'data-testid': 'lease-notice', 'data-keep-in-focus': 'true' });
    const reason = el(doc, 'p', { class: 'hint', id: 'clinic-toolbar-reason', 'data-testid': 'toolbar-reason' });
    const menu = el(doc, 'div', { role: 'menu', 'aria-label': TEXT.more, hidden: true, 'data-testid': 'overflow-menu' });
    const query = el(doc, 'input', { type: 'text', 'aria-label': TEXT.query, 'data-testid': 'search-query' });
    const repl = el(doc, 'input', { type: 'text', 'aria-label': TEXT.replacement, 'data-testid': 'search-replacement' });
    const caseBox = el(doc, 'input', { type: 'checkbox', 'aria-label': TEXT.caseSensitive, 'data-testid': 'search-case' });
    const count = el(doc, 'span', { role: 'status', 'data-testid': 'search-count' });
    const panelButtons = {};
    const panel = el(doc, 'div', { class: 'editor-find', role: 'search', 'aria-label': TEXT.searchPanel, hidden: true, 'data-testid': 'search-panel' }, [query, caseBox, count, repl]);
    for (const [id, name] of [['prev', TEXT.prev], ['next', TEXT.next], ['replaceOne', TEXT.replaceOne], ['replaceAll', TEXT.replaceAll], ['closeSearch', TEXT.closeSearch]]) {
        const b = el(doc, 'button', { type: 'button', class: 'small', 'aria-label': name, 'data-tool': id }, [name]);
        panelButtons[id] = b;
        panel.appendChild(b);
    }
    hostElement.appendChild(bar);
    hostElement.appendChild(menu);
    hostElement.appendChild(panel);
    hostElement.appendChild(notice);
    hostElement.appendChild(reason);
    let itemNodes = [];
    function renderMenu() {
        itemNodes = toolbar.menuItems().map((item, i) => {
            const node = el(doc, 'button', { type: 'button', role: 'menuitem', tabindex: '-1', 'data-item': item.id, 'aria-label': item.name }, [item.name]);
            node.addEventListener('click', () => activate(i));
            return node;
        });
        menu.children && Array.from(menu.children).forEach((c) => menu.removeChild(c));
        itemNodes.forEach((n) => menu.appendChild(n));
    }
    function focusItem(i) {
        itemNodes.forEach((n, idx) => n.setAttribute('tabindex', idx === i ? '0' : '-1'));
        if (itemNodes[i]) itemNodes[i].focus();
    }
    function openMenu(index) {
        renderMenu();
        nav.open(index);
        menu.removeAttribute('hidden');
        buttons.more.setAttribute('aria-expanded', 'true');
        focusItem(nav.active());
    }
    function closeMenu(returnFocus) {
        nav.close();
        menu.setAttribute('hidden', '');
        buttons.more.setAttribute('aria-expanded', 'false');
        if (returnFocus) buttons.more.focus();
    }
    function activate(i) {
        const item = toolbar.menuItems()[i];
        if (!item) return;
        const locked = item.mutating && !toolbar.getState().canEdit;
        closeMenu(true);
        if (!locked) item.run();
        else toolbar.refresh();
    }
    const commands = {
        undo: () => toolbar.undo(),
        redo: () => toolbar.redo(),
        search: () => toolbar.toggleSearch(),
        fontDown: () => toolbar.fontDown(),
        fontUp: () => toolbar.fontUp(),
        focus: () => toolbar.toggleFocus(),
        more: () => (nav.isOpen() ? closeMenu(true) : openMenu(0)),
    };
    for (const [id, run] of Object.entries(commands)) buttons[id].addEventListener('click', run);
    buttons.more.addEventListener('keydown', (e) => {
        if (nav.isOpen()) return;
        const r = nav.key(e.key);
        if (r.handled) {
            e.preventDefault();
            nav.close();
            openMenu(e.key === 'ArrowUp' ? 'last' : 0);
        }
    });
    menu.addEventListener('keydown', (e) => {
        if (!nav.isOpen()) return;
        const r = nav.key(e.key);
        if (r.action === 'close' && e.key === 'Tab') { closeMenu(false); return; }
        if (!r.handled) return;
        e.preventDefault();
        e.stopPropagation();
        if (r.action === 'move') focusItem(r.index);
        else if (r.action === 'activate') activate(r.index);
        else if (r.action === 'close') closeMenu(true);
    });
    query.addEventListener('input', () => toolbar.setQuery(query.value));
    repl.addEventListener('input', () => toolbar.setReplacement(repl.value));
    caseBox.addEventListener('change', () => toolbar.setCaseSensitive(caseBox.checked));
    panelButtons.prev.addEventListener('click', () => toolbar.step(-1));
    panelButtons.next.addEventListener('click', () => toolbar.step(1));
    panelButtons.replaceOne.addEventListener('click', () => toolbar.replaceCurrent());
    panelButtons.replaceAll.addEventListener('click', () => toolbar.replaceAll());
    panelButtons.closeSearch.addEventListener('click', () => { toolbar.closeSearch(); buttons.search.focus(); });
    hostElement.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (e.defaultPrevented) return; // the tooltip primitive already closed itself
            const what = toolbar.escape(nav.isOpen());
            if (what === 'menu') closeMenu(true);
            if (what) e.preventDefault();
            return;
        }
        if (toolbar.shortcut(e)) e.preventDefault();
    });
    const panelMutating = ['replaceOne', 'replaceAll'];
    function setAria(node, disabled, why) {
        if (disabled) node.setAttribute('aria-disabled', 'true'); else node.removeAttribute('aria-disabled');
        if (disabled && why) node.setAttribute('aria-describedby', 'clinic-toolbar-reason'); else node.removeAttribute('aria-describedby');
    }
    function render(s) {
        setAria(buttons.undo, !s.canUndo, !s.canEdit);
        setAria(buttons.redo, !s.canRedo, !s.canEdit);
        setAria(buttons.fontDown, s.fontMin, false);
        setAria(buttons.fontUp, s.fontMax, false);
        for (const id of panelMutating) setAria(panelButtons[id], !s.canEdit, true);
        buttons.focus.setAttribute('aria-pressed', s.focusMode ? 'true' : 'false');
        buttons.search.setAttribute('aria-expanded', s.searchOpen ? 'true' : 'false');
        if (s.searchOpen) panel.removeAttribute('hidden'); else panel.setAttribute('hidden', '');
        hostElement.setAttribute('data-focus-mode', s.focusMode ? 'true' : 'false');
        sizeLabel.textContent = s.fontSize + ' px';
        count.textContent = s.query ? (s.matches ? (s.current >= 0 ? (s.current + 1) + ' / ' : '') + s.matches : TEXT.noMatches) : '';
        notice.textContent = s.leaseNotice || '';
        reason.textContent = s.readOnlyReason || s.message || '';
    }
    const off = toolbar.subscribe(render);
    render(toolbar.getState());

    return {
        buttons,
        panelButtons,
        menu,
        query,
        replacement: repl,
        openMenu,
        closeMenu,
        isMenuOpen: () => nav.isOpen(),
        destroy() {
            off();
            detachers.forEach((d) => d());
            while (hostElement.firstChild) hostElement.removeChild(hostElement.firstChild);
        },
    };
}

/**
 * Vue directive for the .tool-wrap element around an icon button: renders the tooltip of
 * the V8 stylesheet (.tool-wrap > .tooltip) with the label and the shortcut, links it with
 * aria-describedby, shows it on hover and on keyboard focus and closes it with Escape.
 * Usage: v-tip="{ label, keys }". Text is set with textContent only.
 */
let tipCounter = 0;
export const vTip = {
    mounted(el, binding) {
        const doc = el.ownerDocument;
        const tip = doc.createElement('span');
        tip.className = 'tooltip';
        tip.setAttribute('role', 'tooltip');
        tip.setAttribute('aria-hidden', 'true');
        tip.id = 'clinic-tip-' + (++tipCounter);
        const label = doc.createElement('span');
        label.setAttribute('data-tooltip-label', '');
        tip.appendChild(label);
        const keys = doc.createElement('kbd');
        tip.appendChild(keys);
        el.appendChild(tip);
        const button = el.querySelector('button');
        if (button) button.setAttribute('aria-describedby', tip.id);
        const show = () => { tip.setAttribute('data-visible', 'true'); tip.setAttribute('aria-hidden', 'false'); };
        const hide = () => { tip.removeAttribute('data-visible'); tip.setAttribute('aria-hidden', 'true'); };
        el.addEventListener('mouseenter', show);
        el.addEventListener('mouseleave', hide);
        el.addEventListener('focusin', () => { if (button?.matches(':focus-visible')) show(); });
        el.addEventListener('focusout', hide);
        el.addEventListener('keydown', (event) => { if (event.key === 'Escape') hide(); });
        el._tip = { label, keys };
        vTip.updated(el, binding);
    },
    updated(el, binding) {
        const t = el._tip;
        if (!t) return;
        const v = binding.value || {};
        t.label.textContent = v.label || '';
        t.keys.textContent = v.keys || '';
        t.keys.hidden = !v.keys;
    },
};
