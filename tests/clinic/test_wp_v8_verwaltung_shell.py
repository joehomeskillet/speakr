"""Verwaltung inside the clinic shell (v8-114; V8 P10 to P18, DESIGN_SYSTEM section 1).

The administration pages are views of the clinic shell (hash routes #/verwaltung/<page>), reached from the header menu
"Verwaltung & Seiten", with the breadcrumb Speakr > Verwaltung > <page>. Visibility follows the probe rule of
templates/admin.html: a GET the API authorises; a page is withheld when the API answers 403.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway SQLite database, synthetic users
only, headless Chromium): the site admin (w226admin) and a team lead without site admin rights (w226lead).
The browser cases need Playwright, its Chromium, the gitignored static/vendor and a built static/css/tailwind.css.
"""

from __future__ import annotations

from pathlib import Path

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, LEAD_EMAIL, _login, browser, live_server,
)

# The imported fixtures are used by name as test arguments.
_FIXTURES = (browser, live_server)

ROOT = Path(__file__).resolve().parents[2]

# view id -> (hash route, label in menu and breadcrumb, heading the page component renders)
PAGES = {
    "clinic-admin-pools": ("#/verwaltung/schreibkraefte-pools", "Schreibkräfte-Pools", "Schreibkräfte-Pools"),
    "clinic-admin-authors": ("#/verwaltung/diktierende", "Diktierende und Autorenpools", "Diktierende und Autorenpools"),
    "clinic-admin-users": ("#/verwaltung/benutzer", "Benutzer und Rollen", "Benutzer und Rollen"),
    "clinic-admin-vocabulary": ("#/verwaltung/wortschatz", "Wortschatz", "Wortschatz"),
    "clinic-admin-commands": ("#/verwaltung/befehle-vorlagen", "Sprachbefehle und Vorlagen", "Befehle und Vorlagen"),
    "clinic-admin-asr": ("#/verwaltung/sprachprofile", "ASR und Sprachprofile", "Sprachprofile"),
    "clinic-admin-import": ("#/verwaltung/import", "Dateien und Geräteimport", "Dateien und Geräteimport"),
    "clinic-admin-jobs": ("#/verwaltung/verarbeitung", "Verarbeitung", "Verarbeitung"),
    "clinic-admin-activity": ("#/verwaltung/aktivitaet", "Aktivität und Audit", "Aktivität und Audit"),
}
# Pages with a menu entry in components/header-controls.html (Dateien und Geräteimport keeps its own entry /#/import).
MENU_PAGES = [view for view in PAGES if view != "clinic-admin-import"]


def _grant_statistics(page):
    # The statistics route needs an explicit grant (statistics_access); the admin of the harness has none, so the API refuses
    # the probe of "Aktivität und Audit". Reachability of that page is checked with the route answered.
    page.route("**/api/clinic/v1/statistics/aggregates*", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))


def _mounted(page):
    page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)


def _shell(page, view):
    return page.locator(f"[data-testid=clinic-admin-view][data-admin-view={view}]")


def _wait_page(page, view):
    _hash, _label, heading = PAGES[view]
    shell = _shell(page, view)
    shell.wait_for(state="visible", timeout=30000)
    shell.locator("h1, h2").filter(has_text=heading).first.wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid=clinic-admin-denied]").count() == 0
    return shell


def _open_from_menu(page, view):
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.locator(f"a[data-clinic-admin-link={view}]").click()


def test_every_page_is_reachable_by_hash_with_breadcrumb_as_admin(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    _grant_statistics(page)
    for view, (route, label, _heading) in PAGES.items():
        page.goto(base + "/" + route)
        _mounted(page)
        shell = _wait_page(page, view)
        crumb = shell.locator("[data-testid=clinic-admin-breadcrumb]")
        assert [t.strip() for t in crumb.locator("a, span").all_inner_texts()] == ["Speakr", "›", "Verwaltung", "›", label]
        assert page.evaluate("window.location.hash") == route
        # The primary tabs stay in the header; neither is the current page.
        assert page.locator("[data-testid=clinic-primary-nav] [aria-current=page]").count() == 0
    assert not js_errors, js_errors


def test_every_menu_entry_opens_its_page(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    _grant_statistics(page)
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    for view in MENU_PAGES:
        _open_from_menu(page, view)
        _wait_page(page, view)
        assert page.evaluate("window.location.hash") == PAGES[view][0]
    # Browser back returns to the previous page, the breadcrumb link to Aufnahmen.
    page.go_back()
    _wait_page(page, MENU_PAGES[-2])
    _shell(page, MENU_PAGES[-2]).locator("[data-testid=clinic-admin-breadcrumb] a").click()
    page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
    assert not js_errors, js_errors


def test_pools_authors_users_use_the_prototype_page_structure(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    for view in ("clinic-admin-pools", "clinic-admin-authors", "clinic-admin-users"):
        page.goto(base + "/" + PAGES[view][0])
        _mounted(page)
        shell = _wait_page(page, view)
        assert shell.locator(".pagehead .eyebrow").text_content().strip() == "Verwaltung"
        assert shell.locator(".pagehead h1").count() == 1
        assert shell.locator(".pagehead .subline").count() == 1
        shell.locator(".stats .stat").first.wait_for(state="visible", timeout=30000)
        assert shell.locator(".stats .stat").count() == 4
    # Pools: table card with search and reset, status pill and an edit icon button per row.
    page.goto(base + "/" + PAGES["clinic-admin-pools"][0])
    _mounted(page)
    shell = _shell(page, "clinic-admin-pools")
    shell.locator(".filterbar input[type=search]").wait_for(state="visible", timeout=30000)
    # The harness has no staff pool: the page's own "Pool hinzufügen" action creates one.
    shell.locator("[data-testid=empty-state]").wait_for(state="visible", timeout=30000)
    shell.locator("[data-testid=create-open]").click()
    shell.locator("#cap-create-name").fill("Schreibpool Test")
    shell.locator("[data-testid=create-panel] button[type=submit]").click()
    shell.locator("[data-testid=pool-list] .tag").first.wait_for(state="visible", timeout=30000)
    assert shell.locator("[data-testid=pool-list] tbody tr").count() == 1
    assert shell.locator("[data-testid=pool-list] tbody tr strong").inner_text().strip() == "Schreibpool Test"
    shell.locator("[data-testid=pool-list] .icon-button").click()
    shell.locator("#cap-rename").wait_for(state="visible", timeout=30000)
    shell.locator("[data-testid=pool-filter]").fill("zzz-kein-treffer")
    shell.locator("[data-testid=filter-empty]").wait_for(state="visible", timeout=10000)
    shell.locator("[data-testid=pool-filter-reset]").click()
    assert shell.locator("[data-testid=filter-empty]").count() == 0
    # Authors: the filter bar filters the table rows.
    page.goto(base + "/" + PAGES["clinic-admin-authors"][0])
    _mounted(page)
    shell = _shell(page, "clinic-admin-authors")
    shell.locator("table tbody tr").first.wait_for(state="visible", timeout=30000)
    rows = shell.locator("table tbody tr").count()
    assert rows >= 2
    shell.locator("#authors-search").fill("w226typist")
    assert shell.locator("table tbody tr").count() == 1
    shell.locator(".filterbar .icon-button").click()
    assert shell.locator("table tbody tr").count() == rows
    assert not js_errors, js_errors


def test_activity_is_withheld_when_the_api_refuses_the_statistics_probe(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)  # no statistics grant: the real API answers 403
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.wait_for_function("document.querySelector('a[data-clinic-admin-link=clinic-admin-activity]').hidden", timeout=30000)
    assert page.locator("a[data-clinic-admin-link=clinic-admin-pools]").is_visible()
    page.goto(base + "/" + PAGES["clinic-admin-activity"][0])
    _mounted(page)
    page.locator("[data-testid=clinic-admin-denied]").wait_for(state="visible", timeout=30000)
    assert not js_errors, js_errors


def test_withheld_page_is_hidden_and_not_shown_on_403(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.route("**/admin/users*", lambda route: route.fulfill(status=403, content_type="application/json", body="{}"))
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.locator("a[data-clinic-admin-link=clinic-admin-pools]").wait_for(state="visible", timeout=30000)
    page.wait_for_function("document.querySelector('a[data-clinic-admin-link=clinic-admin-users]').hidden", timeout=30000)
    assert page.locator("a[data-clinic-admin-link=clinic-admin-authors]").is_hidden()
    assert page.locator("a[data-clinic-admin-link=clinic-admin-pools]").is_visible()
    page.goto(base + "/" + PAGES["clinic-admin-users"][0])
    _mounted(page)
    page.locator("[data-testid=clinic-admin-denied]").wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid=clinic-users-view]").count() == 0
    assert not js_errors, js_errors


def test_non_admin_gets_no_admin_page_and_no_menu_entry(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)
    page.goto(base + "/" + PAGES["clinic-admin-users"][0])
    _mounted(page)
    page.locator("[data-testid=clinic-admin-denied]").wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid=clinic-users-view]").count() == 0
    assert page.locator("[data-testid=clinic-admin-breadcrumb]").inner_text().replace("\n", " ").endswith("Benutzer und Rollen")
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    assert page.locator("a[data-clinic-admin-link]").count() == 0
    assert not js_errors, js_errors


def test_admin_menu_entries_and_view_markup_exist_only_in_clinic_mode():
    index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    block = index[index.index("V8 Verwaltung views (v8-114): the administration pages"):index.index("end V8 Verwaltung views (v8-114)")]
    assert "clinic-admin-view" in block
    # The block sits inside the {% if clinic_mode %} section of the shell views.
    before = index[:index.index("V8 Verwaltung views (v8-114): the administration pages")]
    assert before.rfind("{% if clinic_mode %}") > before.rfind("{% endif %}") or "{% endif %}" not in before[before.rfind("{% if clinic_mode %}"):]
    header = (ROOT / "templates" / "components" / "header-controls.html").read_text(encoding="utf-8")
    assert header.index("data-clinic-admin-link") > header.index("{% if clinic_mode %}")
    assert header.index("data-clinic-admin-link") < header.index("{% else %}")
