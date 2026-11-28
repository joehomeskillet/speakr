"""PAR-ADM-004: pick authors and exclusions by name in the Fachrechte editor.

The page is the real users view and grants editor, mounted with a clinic client
that speaks the grants API shape. An admin saves pool A except Beta, reloads,
then saves only Alpha. A stale revision leaves the ticked names in place. A
foreign account gets no editor and sends no change.
"""

from __future__ import annotations

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / ".wp" / "artifacts"
VIEWPORTS = ((1440, 900), (1280, 720), (390, 844))
THEMES = ("light", "dark")

PAGE = r"""<!doctype html><html lang="de-CH"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/styles.css">
<link rel="stylesheet" href="/static/css/primitives.css">
<link rel="stylesheet" href="/static/css/clinic-v8.css">
<link rel="stylesheet" href="/static/css/clinic-admin.css">
<link rel="stylesheet" href="/static/css/tailwind.css">
<style>html,body{margin:0} main{box-sizing:border-box;max-width:100%;padding:16px}</style>
</head><body class="v8-ui"><main id="app" class="cadm"></main>
<script src="/static/vendor/js/vue.global.js"></script>
<script type="module">
import { UsersView } from '/static/js/clinic/compose-users.js';
const params = new URLSearchParams(location.search);
window.conflict = params.get('conflict') === '1';
window.foreign = params.get('foreign') === '1';
window.posts = [];
const named = params.get('authors') !== '0';
const directory = named ? [
  { id: 11, username: 'Alpha', email: 'alpha@local.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
  { id: 12, username: 'Beta', email: 'beta@local.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
  { id: 13, username: 'Gamma', email: 'gamma@local.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
  { id: 21, username: 'Schreib1', email: 'schreib1@local.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
] : [
  { id: 21, username: 'Schreib1', email: 'schreib1@local.test', is_admin: false, status: 'active', login_policy: 'local_only', is_identity_admin: false, version: 1 },
];
const names = { 11: 'Alpha', 12: 'Beta', 13: 'Gamma', 21: 'Schreib1' };
let revision = 4;
let items = [];
function pageOf(users) {
  return { outcome: 'ok', data: { users, pagination: { page: 1, per_page: users.length || 1, total: users.length, total_pages: 1, has_next: false, has_prev: false } } };
}
function listed(withNames) {
  return items.map((item) => {
    const row = { effect: item.effect, scope: item.scope, actions: item.actions.slice() };
    if (item.pool_id) row.pool_id = item.pool_id;
    if (item.author_ids) row.author_ids = item.author_ids.slice();
    if (withNames) {
      row.id = item.id;
      if (item.author_ids) row.author_names = item.author_ids.map((id) => names[id]).filter(Boolean);
    }
    return row;
  });
}
function request(path, opts) {
  const method = String((opts && opts.method) || 'GET').toUpperCase();
  if (String(path).startsWith('/admin/users')) {
    const users = String(path).includes('sort=username') && !named ? [] : directory;
    return Promise.resolve(pageOf(users));
  }
  if (path === '/pools') {
    return Promise.resolve({ outcome: 'ok', data: { revision: 1, author_pools: [
      { id: 'pool-a', name: 'A', member_user_ids: [11, 12] },
      { id: 'pool-b', name: 'B', member_user_ids: [13] },
    ], staff_pools: [] } });
  }
  if (String(path).includes('/grants')) {
    if (method === 'POST') {
      window.posts.push(opts.body);
      if (window.foreign) return Promise.resolve({ outcome: 'permission', message: 'Keine Berechtigung für diese Aktion.' });
      if (window.conflict) return Promise.resolve({ outcome: 'conflict', message: 'Der Stand ist veraltet.' });
      revision += 1;
      const body = opts.body;
      items.push({ id: 'g-' + revision, effect: body.effect, scope: body.scope, pool_id: body.pool_id, actions: body.actions.slice(), author_ids: body.author_ids ? body.author_ids.slice() : undefined });
      return Promise.resolve({ outcome: 'ok', data: { user_id: 21, revision, items: listed(true) } });
    }
    return Promise.resolve({ outcome: 'ok', data: { user_id: 21, revision, items: listed(String(path).includes('entries=1')) } });
  }
  return Promise.resolve({ outcome: 'validation', message: 'unsupported' });
}
const client = { request, switchContext() {}, command: () => request('/noop') };
Vue.createApp(UsersView, {
  client, adminClient: { request, switchContext() {} },
  currentUser: { id: 1, is_admin: !window.foreign }, vue: Vue,
}).mount('#app');
window.mounted = true;
</script></body></html>"""


@pytest.fixture(scope="module")
def base():
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/proof"):
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def _launch(playwright):
    return playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)


def _open(browser, url, width=1440, height=900):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_function("window.mounted === true")
    expect(page.get_by_role("heading", name="Benutzer und Rollen")).to_be_visible()
    return page, errors


def _fachrechte(page):
    page.get_by_role("button", name="Fachrechte", exact=True).click()
    page.get_by_label("Benutzer", exact=True).select_option(label="Schreib1")
    expect(page.get_by_role("heading", name="Berechtigungen")).to_be_visible()


def _except_beta(page):
    page.get_by_label("Geltungsbereich").select_option("pool")
    page.get_by_label("Autorenpool").select_option(label="A")
    page.get_by_role("checkbox", name="Ansehen", exact=True).check()
    page.get_by_role("checkbox", name="Beta", exact=True).check()


def _theme(page, theme):
    page.evaluate(
        """(theme) => {
            document.documentElement.classList.toggle('dark', theme === 'dark');
            document.documentElement.toggleAttribute('data-theme', theme === 'dark');
            if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
        }""",
        theme,
    )


def _shot(page, name):
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    page.screenshot(path=str(ARTIFACTS / name), full_page=True, animations="disabled")


def test_admin_saves_named_exception_then_only_alpha_and_conflict_keeps_the_draft(base):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page, errors = _open(browser, base + "/proof")
            _fachrechte(page)
            expect(page.get_by_text("Keine Einträge")).to_be_visible()
            _except_beta(page)
            page.get_by_role("button", name="Speichern", exact=True).click()
            expect(page.get_by_text("Ausser Beta", exact=True)).to_be_visible()
            page.get_by_role("button", name="Aktuellen Stand laden").click()
            expect(page.get_by_text("Ausser Beta", exact=True)).to_be_visible()
            page.get_by_label("Geltungsbereich").select_option("all")
            page.get_by_role("checkbox", name="Alpha", exact=True).check()
            page.get_by_role("button", name="Speichern", exact=True).click()
            expect(page.get_by_text("Nur Alpha", exact=True)).to_be_visible()
            page.get_by_role("button", name="Aktuellen Stand laden").click()
            expect(page.get_by_text("Nur Alpha", exact=True)).to_be_visible()
            expect(page.get_by_text("Ausser Beta", exact=True)).to_be_visible()
            posts = page.evaluate("window.posts")
            assert posts[0] == {
                "expected_revision": 4, "effect": "allow", "scope": "pool", "pool_id": "pool-a", "actions": ["view"],
            }
            assert posts[1]["effect"] == "deny" and posts[1]["author_ids"] == [12] and posts[1]["expected_revision"] == 5
            assert posts[2]["scope"] == "all" and posts[2]["author_ids"] == [11] and posts[2]["pool_id"] is None
            for width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                for theme in THEMES:
                    _theme(page, theme)
                    _shot(page, f"adm004-{width}x{height}-{theme}-rows.png")
            assert not errors, errors
            page.close()

            conflict, errors = _open(browser, base + "/proof?conflict=1")
            _fachrechte(conflict)
            _except_beta(conflict)
            conflict.get_by_role("button", name="Speichern", exact=True).click()
            expect(conflict.get_by_text("Der Stand ist veraltet")).to_be_visible()
            expect(conflict.get_by_role("checkbox", name="Beta", exact=True)).to_be_checked()
            expect(conflict.get_by_text("Keine Einträge")).to_be_visible()
            conflict.evaluate("window.conflict = false")
            conflict.get_by_role("button", name="Aktuellen Stand laden").click()
            expect(conflict.get_by_role("checkbox", name="Beta", exact=True)).to_be_checked()
            conflict.get_by_role("button", name="Speichern", exact=True).click()
            expect(conflict.get_by_text("Ausser Beta", exact=True)).to_be_visible()
            assert not errors, errors
            conflict.close()

            foreign, errors = _open(browser, base + "/proof?foreign=1")
            _fachrechte(foreign)
            expect(foreign.get_by_text("Nur Lesezugriff")).to_be_visible()
            expect(foreign.get_by_role("button", name="Speichern")).to_have_count(0)
            expect(foreign.get_by_role("checkbox", name="Alpha")).to_have_count(0)
            expect(foreign.get_by_role("checkbox", name="Beta")).to_have_count(0)
            assert foreign.evaluate("window.posts.length") == 0
            assert not errors, errors
            foreign.close()
        finally:
            browser.close()


def test_empty_author_list_renders_in_both_themes(base):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            for width, height in VIEWPORTS:
                for theme in THEMES:
                    page, errors = _open(browser, base + "/proof?authors=0", width, height)
                    _theme(page, theme)
                    _fachrechte(page)
                    page.get_by_label("Geltungsbereich").select_option("all")
                    expect(page.get_by_text("Keine Benutzer für die Auswahl.")).to_be_visible()
                    expect(page.get_by_text("Keine Einträge")).to_be_visible()
                    expect(page.get_by_role("checkbox", name="Alpha")).to_have_count(0)
                    _shot(page, f"adm004-{width}x{height}-{theme}-empty.png")
                    assert not errors, errors
                    page.close()
        finally:
            browser.close()
