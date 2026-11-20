const BASE = '/admin/users';
export const PAGE_SIZE = 15;
const MAX_PER_PAGE = 1000;

function pathFor(page, perPage, { search, sort, order }) {
    const params = new URLSearchParams();
    if (page !== 1) params.set('page', page);
    if (perPage !== PAGE_SIZE) params.set('per_page', perPage);
    if (search) params.set('search', search);
    if (sort && sort !== 'id') params.set('sort', sort);
    if (order === 'desc') params.set('order', order);
    return params.size ? BASE + '?' + params.toString().replaceAll('.', '%2E') : BASE;
}

function pageOf(data) {
    const users = data.users;
    const meta = data.pagination;
    return { users, page: meta?.page ?? 1, pages: meta?.total_pages ?? 1, total: meta?.total ?? users.length };
}

async function requestPage(client, page, perPage, filters) {
    const res = await client.request(pathFor(page, perPage, filters));
    if (res.outcome !== 'ok') return res;
    return Array.isArray(res.data?.users)
        ? { outcome: 'ok', data: pageOf(res.data) }
        : { outcome: 'UNKNOWN' };
}

// The pool pickers and counts need the whole authorized directory. The server caps each response.
export async function readDirectory(client, filters = {}) {
    let res = await requestPage(client, 1, PAGE_SIZE, filters);
    if (res.outcome !== 'ok' || res.data.pages === 1) return res;
    const users = new Map();
    for (let page = 1; ; page += 1) {
        res = await requestPage(client, page, MAX_PER_PAGE, filters);
        if (res.outcome !== 'ok') return res;
        for (const user of res.data.users) users.set(user.id, user);
        if (res.data.page >= res.data.pages) break;
    }
    return { outcome: 'ok', data: { users: [...users.values()] } };
}

export async function readAccountPage(client, page, filters) {
    if (!filters.role && !filters.source) return requestPage(client, page, PAGE_SIZE, filters);
    // Role and sign-in policy have no server filter. Keep the server's search and order.
    const res = await readDirectory(client, filters);
    if (res.outcome !== 'ok') return res;
    const users = res.data.users.filter(user =>
        (!filters.role || user[filters.role]) &&
        (!filters.source || (user.login_policy || 'local_only') === filters.source));
    const pages = Math.max(1, Math.ceil(users.length / PAGE_SIZE));
    const current = Math.min(page, pages);
    return { outcome: 'ok', data: {
        users: users.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE),
        page: current, pages, total: users.length,
    } };
}
