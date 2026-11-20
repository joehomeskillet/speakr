/**
 * Accessible tooltip primitive (WCAG 1.4.13: dismissable, hoverable, persistent).
 *
 * - Shows on pointer hover and on keyboard focus.
 * - Stays while the pointer moves from the trigger onto the tooltip.
 * - Escape closes it without moving focus.
 * - The trigger gets aria-describedby only while the tooltip is shown.
 * - The tooltip is a supplement: essential information must also be in the
 *   visible label or an aria-label of the trigger.
 *
 * Styling lives in static/css/primitives.css (.tooltip).
 */

export const HIDE_DELAY_MS = 150;

let counter = 0;

export function createTooltip(trigger, text, options) {
    const opts = options || {};
    const doc = opts.document || trigger.ownerDocument || globalThis.document;
    const setTimer = opts.setTimeout || globalThis.setTimeout;
    const clearTimer = opts.clearTimeout || globalThis.clearTimeout;
    const delay = opts.hideDelay === undefined ? HIDE_DELAY_MS : opts.hideDelay;
    const id = opts.id || 'clinic-tooltip-' + (++counter);

    let node = null;
    let timer = null;
    let focused = false;
    let hovered = false;

    function cancelHide() {
        if (timer !== null) {
            clearTimer(timer);
            timer = null;
        }
    }

    function show() {
        cancelHide();
        if (node) return;
        node = doc.createElement('div');
        node.setAttribute('id', id);
        node.setAttribute('role', 'tooltip');
        node.className = 'tooltip';
        node.textContent = text;
        // The pointer may travel onto the tooltip without closing it.
        node.addEventListener('pointerenter', onTooltipEnter);
        node.addEventListener('pointerleave', onLeave);
        doc.body.appendChild(node);
        trigger.setAttribute('aria-describedby', id);
    }

    function hide() {
        cancelHide();
        focused = false;
        hovered = false;
        if (!node) return;
        node.remove();
        node = null;
        trigger.removeAttribute('aria-describedby');
    }

    function scheduleHide() {
        cancelHide();
        timer = setTimer(function () {
            timer = null;
            if (!focused && !hovered) hide();
        }, delay);
    }

    function onEnter() { hovered = true; show(); }
    function onTooltipEnter() { hovered = true; cancelHide(); }
    function onLeave() { hovered = false; if (!focused) scheduleHide(); }
    function onFocus() { focused = true; show(); }
    function onBlur() { focused = false; if (!hovered) scheduleHide(); }
    function onKeydown(event) {
        if (event.key === 'Escape' && node) hide();
    }

    trigger.addEventListener('pointerenter', onEnter);
    trigger.addEventListener('pointerleave', onLeave);
    trigger.addEventListener('focus', onFocus);
    trigger.addEventListener('blur', onBlur);
    trigger.addEventListener('keydown', onKeydown);

    return {
        id: id,
        isVisible: function () { return node !== null; },
        show: show,
        hide: hide,
        destroy: function () {
            hide();
            trigger.removeEventListener('pointerenter', onEnter);
            trigger.removeEventListener('pointerleave', onLeave);
            trigger.removeEventListener('focus', onFocus);
            trigger.removeEventListener('blur', onBlur);
            trigger.removeEventListener('keydown', onKeydown);
        },
    };
}
