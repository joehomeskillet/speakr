const STEP = 24;
const ARROWS = {
    ArrowUp: [0, -STEP], ArrowDown: [0, STEP], ArrowLeft: [-STEP, 0], ArrowRight: [STEP, 0],
};

function fitAxis(value, origin, size, editorStart, editorEnd, visibleStart, visibleEnd) {
    const start = Math.max(editorStart, visibleStart);
    const end = Math.min(editorEnd, visibleEnd);
    if (end - start >= size) return Math.min(end - size, Math.max(start, origin + value)) - origin;
    return Math.min(Math.max(editorStart, editorEnd - size), Math.max(editorStart, origin + value)) - origin;
}

export function fitPlayer(offset, dock, player, editor, visible) {
    return {
        x: fitAxis(offset.x, dock.left, player.width, editor.left, editor.right, visible.left, visible.right),
        y: fitAxis(offset.y, dock.top, player.height, editor.top, editor.bottom, visible.top, visible.bottom),
    };
}

export function useCorrectionPlayerPosition(V) {
    const playerDock = V.shallowRef(null);
    let offset = null;
    let drag = null;
    let observer;
    let win;

    function place(requested) {
        const dock = playerDock.value;
        if (!dock) return;
        const editor = dock.parentElement;
        const content = dock.closest('.content')?.getBoundingClientRect();
        const viewport = dock.ownerDocument.documentElement;
        dock.dataset.playerMoved = 'true';
        offset = fitPlayer(requested, dock.getBoundingClientRect(), dock.firstElementChild.getBoundingClientRect(),
            editor.getBoundingClientRect(), {
                left: Math.max(0, content?.left ?? 0), top: Math.max(0, content?.top ?? 0),
                right: Math.min(viewport.clientWidth, content?.right ?? viewport.clientWidth),
                bottom: Math.min(viewport.clientHeight, content?.bottom ?? viewport.clientHeight),
            });
        dock.style.setProperty('--player-x', `${offset.x}px`);
        dock.style.setProperty('--player-y', `${offset.y}px`);
    }

    function endMove(event) {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        const { handle, id } = drag;
        drag = null;
        if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
    }

    function resetPlayer() {
        endMove();
        offset = null;
        const dock = playerDock.value;
        if (!dock) return;
        delete dock.dataset.playerMoved;
        dock.style.removeProperty('--player-x');
        dock.style.removeProperty('--player-y');
    }

    function startMove(event) {
        if (drag || !event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        const handle = event.currentTarget;
        handle.setPointerCapture(event.pointerId);
        drag = { id: event.pointerId, handle, x: event.clientX, y: event.clientY, offset: offset || { x: 0, y: 0 } };
    }

    function movePlayer(event) {
        if (!drag || event.pointerId !== drag.id) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (dx || dy || offset) place({ x: drag.offset.x + dx, y: drag.offset.y + dy });
    }

    function playerPositionKey(event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        if (event.key === 'Home' || event.key === 'Escape') {
            event.preventDefault();
            resetPlayer();
        } else if (Object.hasOwn(ARROWS, event.key)) {
            event.preventDefault();
            const [x, y] = ARROWS[event.key];
            place({ x: (offset?.x ?? 0) + x, y: (offset?.y ?? 0) + y });
        }
    }

    const fit = () => { if (offset) place(offset); };
    function disconnect() {
        observer?.disconnect();
        win?.removeEventListener('resize', fit);
        endMove();
    }
    V.watch?.(playerDock, (dock) => {
        disconnect();
        offset = null;
        if (!dock) return;
        win = dock.ownerDocument.defaultView;
        win.addEventListener('resize', fit);
        observer = new win.ResizeObserver(fit);
        observer.observe(dock);
        observer.observe(dock.parentElement);
    }, { flush: 'post' });
    V.onBeforeUnmount?.(disconnect);
    return { playerDock, startMove, movePlayer, endMove, playerPositionKey, resetPlayer };
}
