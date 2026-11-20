"""Unified clinic administration in /admin (v8-fable-admin-unify).

/admin is the central administration. The composed V8 pages carry the former single panels
(Konten and Fachrechte inside "Benutzer und Rollen", the membership editor inside
"Diktierende und Autorenpools", Vorlagen inside "Befehle und Vorlagen"), so the grouped
navigation lists every function once. #/verwaltung/<page> deep links select the page and
back/forward follow the hash; the header menu points at /admin and the clinic shell
redirects a site admin from the old /#/verwaltung/<page> hashes to /admin. The write
operations that previously ran through the single panels (pool membership removal, grant
write) run through the composed pages here, against the real API.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway
SQLite database, synthetic users only, headless Chromium).
"""

from __future__ import annotations

import re

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, _login, browser, live_server,
)

# The imported fixtures are used by name as test arguments.
_FIXTURES = (browser, live_server)

# Every entry of the grouped navigation, each exactly once (no Konten/Pools/Fachrechte/
# Vorlagen duplicates of the composed pages).
NAV_LABELS = [
    "Benutzer und Rollen", "Schreibkräfte-Pools", "Diktierende und Autorenpools", "Verzeichnis",
    "Kategorien", "Regeln", "Spalten", "Wortschatz", "Befehle und Vorlagen",
    "Abnahme",
    "Vorgaben", "Sprachprofile", "Import", "Verarbeitung", "Aktivität und Audit",
]
MERGED = {"Konten", "Pools", "Fachrechte", "Vorlagen"}


def _nav_labels(page) -> list[str]:
    nav = page.locator("[data-testid=clinic-v8-nav]")
    return [b.inner_text().strip() for b in nav.locator("button").all()]


def _open_admin(browser, base, hash_part=""):
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin" + hash_part)
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    return page, js_errors


def test_navigation_lists_every_function_once_and_lands_on_users(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open_admin(browser, base)
    page.locator("[data-testid=clinic-v8-view-users]").wait_for(state="visible", timeout=20000)
    labels = _nav_labels(page)
    assert sorted(labels) == sorted(set(labels)), labels  # no label twice
    # Statistics requires a separate grant. Directory status remains available to
    # explain the required identity permission without loading the directory preview.
    assert set(labels) == set(NAV_LABELS) - {"Aktivität und Audit"}, labels
    assert not (set(labels) & MERGED), labels
    withheld = page.locator("[data-testid=clinic-admin-withheld]").inner_text()
    assert "Aktivität und Audit" in withheld
    # Landing page is Benutzer und Rollen (it carries the former Konten panel).
    current = page.locator("[data-testid=clinic-v8-nav] [aria-current=page]")
    assert current.count() == 1 and current.inner_text().strip() == "Benutzer und Rollen"
    seen_requests = []
    page.on("request", lambda request: seen_requests.append(request.url.removeprefix(base)))
    page.get_by_role("button", name="Verzeichnis", exact=True).click()
    directory = page.get_by_test_id("clinic-view-directory")
    directory.get_by_text("Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.").wait_for()
    assert directory.get_by_role("button", name="Vorschau laden").count() == 0
    assert "/api/clinic/v1/directory/preview" not in seen_requests
    directory.get_by_role("button", name="Lokale Konten verwalten").click()
    page.get_by_test_id("clinic-v8-view-users").wait_for(state="visible")
    assert current.count() == 1 and current.inner_text().strip() == "Benutzer und Rollen"
    assert not js_errors, js_errors


def test_deep_link_selects_the_page_and_back_forward_follow_the_hash(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open_admin(browser, base, "#/schreibkraefte-pools")
    page.locator("[data-testid=clinic-v8-view-admin-pools]").wait_for(state="visible", timeout=20000)
    page.locator("[data-testid=clinic-v8-panel-users]").click()
    page.locator("[data-testid=clinic-v8-view-users]").wait_for(state="visible", timeout=20000)
    assert page.evaluate("window.location.hash") == "#/benutzer"
    page.go_back()
    page.locator("[data-testid=clinic-v8-view-admin-pools]").wait_for(state="visible", timeout=20000)
    assert page.evaluate("window.location.hash") == "#/schreibkraefte-pools"
    page.go_forward()
    page.locator("[data-testid=clinic-v8-view-users]").wait_for(state="visible", timeout=20000)
    assert not js_errors, js_errors


def test_header_menu_points_at_admin_and_the_old_hashes_redirect_a_site_admin(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/#/aufnahmen")
    page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    links = page.locator("[data-testid=clinic-pages-menu] a[data-clinic-admin-link]")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert len(hrefs) == 8 and all(h.startswith("/admin#/") for h in hrefs), hrefs
    # The old shell hash forwards a site admin to the same page below /admin.
    page.goto(base + "/#/verwaltung/benutzer")
    page.wait_for_url(re.compile(r"/admin#/benutzer$"), timeout=20000)
    page.locator("[data-testid=clinic-v8-view-users]").wait_for(state="visible", timeout=20000)
    assert not js_errors, js_errors


def test_membership_removal_runs_through_the_authors_page(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open_admin(browser, base, "#/diktierende")
    view = page.locator("[data-testid=clinic-v8-view-authors]")
    view.locator("details summary", has_text="Autorenpools und Schreibkräfte-Pools").click()
    remove = view.get_by_role("button", name="Entfernen")
    remove.first.wait_for(state="visible", timeout=20000)
    assert remove.count() == 2
    remove.first.click()
    view.get_by_role("alertdialog").wait_for(state="visible", timeout=20000)
    view.get_by_role("button", name="Ja, entfernen").click()
    page.wait_for_function(
        "document.querySelectorAll('[data-testid=clinic-v8-view-authors] article li button').length === 1",
        timeout=20000)
    members = page.evaluate("fetch('/api/clinic/v1/pools').then(r => r.json())"
                            ".then(d => d.author_pools.map(p => p.member_user_ids.length))")
    assert members == [1]
    assert not js_errors, js_errors


def test_grant_write_runs_through_the_users_page(live_server, browser):
    base, ids = live_server
    page, js_errors = _open_admin(browser, base, "#/benutzer")
    view = page.locator("[data-testid=clinic-v8-view-users]")
    view.get_by_role("button", name="Fachrechte").click()
    view.locator("#usr-pick").wait_for(state="visible", timeout=20000)
    view.locator("#usr-pick").select_option(str(ids["typist"]))
    view.locator("#grants-title").wait_for(state="visible", timeout=20000)
    view.get_by_text("Keine Einträge").wait_for(state="visible", timeout=20000)
    view.get_by_label("Ansehen").check()
    view.get_by_role("button", name="Speichern").click()
    view.locator("ul[aria-labelledby=grants-existing] li", has_text="Ansehen").wait_for(state="visible", timeout=20000)
    stored = page.evaluate(f"fetch('/api/clinic/v1/identity/users/{ids['typist']}/grants').then(r => r.json())")
    assert [(i["effect"], i["scope"], i["actions"]) for i in stored["items"]] == [("allow", "own", ["view"])]
    assert not js_errors, js_errors
