// Start links carry hints for local sign-in and the session-scoped recordings list.
const POOL_ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

function singleParam(url, name) {
    const values = url.searchParams.getAll(name);
    return values.length === 1 ? values[0].trim() : '';
}

function loginName(value) {
    return value && value.length <= 120 && !/[\x00-\x1f\x7f]/.test(value) ? value : '';
}

function poolId(url) {
    const value = singleParam(url, 'pool');
    return POOL_ID.test(value) ? value.toLowerCase() : '';
}

export function applyLoginStartHints(form, hint) {
    const here = new URL(window.location.href);
    const start = new URL(form.dataset.clinicStart, here.origin);
    let source = here;
    try {
        const next = new URL(singleParam(here, 'next'), here.origin);
        if (!here.searchParams.has('user') && !here.searchParams.has('pool')
            && next.origin === start.origin && next.pathname === start.pathname) source = next;
    } catch (error) {
        // A malformed continuation carries no hints.
    }
    const linkedUser = loginName(singleParam(source, 'user'));
    const suggested = linkedUser || loginName(form.dataset.lastLogin);
    const input = form.elements.namedItem('email');
    if (suggested && !input.value) {
        input.value = suggested;
        input.setAttribute('aria-describedby', hint.id);
        hint.textContent = linkedUser ? 'Benutzername aus dem Startlink. Sie können ihn ändern.'
            : 'Zuletzt verwendet. Sie können den Benutzernamen ändern.';
        hint.hidden = false;
    }
    const pool = poolId(source);
    const action = new URL(form.action);
    if (pool && !action.searchParams.has('next')) {
        start.searchParams.set('pool', pool);
        action.searchParams.set('next', start.pathname + start.search);
        form.action = action.href;
    }
}

export async function readStartSelection(client, isRecordingsStart) {
    const url = new URL(window.location.href);
    const explicit = url.searchParams.has('pool');
    const pool = poolId(url);
    if (explicit || url.searchParams.has('user')) {
        url.searchParams.delete('pool');
        url.searchParams.delete('user');
        window.history.replaceState(window.history.state, '', url);
    }
    if (!isRecordingsStart) return null;
    const params = new URLSearchParams({ start_pool: pool });
    const response = await client.request('/preferences/last-selection?' + params);
    const data = response.outcome === 'ok' ? response.data : null;
    const selected = data?.pool || null;
    if (explicit && !(pool && data?.accepted)) {
        const unavailable = 'Die Auswahl aus dem Startlink ist nicht verfügbar.';
        return { pool: selected, notice: selected ? unavailable + ` Die Gruppe „${selected.name}“ bleibt ausgewählt.` : unavailable };
    }
    const notice = !selected ? '' : explicit
        ? `Gruppe „${selected.name}“ aus dem Startlink ausgewählt.`
        : `Zuletzt verwendete Gruppe „${selected.name}“ ausgewählt.`;
    return { pool: selected, notice };
}
