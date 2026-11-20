"""Mount of the V8 administration pages in the clinic admin panel set of /admin (v8-72).

PAR-ADM-001 to PAR-ADM-005, PAR-VOC-001 to PAR-VOC-008, PAR-ASR-002, PAR-OPS-008.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway SQLite database, synthetic users
only, headless Chromium). Each authorised page has one entry in the grouped navigation and renders its real module.
The full-coverage case grants the admin statistics access for one synthetic author.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, LEAD_EMAIL, PANELS, TEAM_PAGE, _login, _panel_names, _render, browser, live_server,
)

# The imported fixtures are used by name as test arguments.
_FIXTURES = (browser, live_server)

ROOT = Path(__file__).resolve().parents[2]

# id -> (label in the navigation row, heading text the module root renders)
V8_PAGES = {
    "users": ("Benutzer und Rollen", "Benutzer und Rollen"),
    "admin-pools": ("Schreibkräfte-Pools", "Schreibkräfte-Pools"),
    "authors": ("Diktierende und Autorenpools", "Diktierende und Autorenpools"),
    "vocabulary": ("Wortschatz", "Wortschatz"),
    "commands-templates": ("Befehle und Vorlagen", "Befehle und Vorlagen"),
    "asr-profiles": ("Sprachprofile", "Sprachprofile"),
    "import": ("Import", "Dateien und Geräteimport"),
    "jobs": ("Verarbeitung", "Verarbeitung"),
    "activity": ("Aktivität und Audit", "Aktivität und Audit"),
}


def _v8_names(page) -> list[str]:
    return [b.inner_text().strip() for b in page.locator("[data-testid^=clinic-v8-panel-]").all()]


@pytest.mark.parametrize("live_server", ["statistics_grant"], indirect=True, scope="module")
def test_site_admin_sees_every_v8_page_and_its_module_root_renders(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin")
    expect(page.get_by_test_id("clinic-v8-view-users").locator("#acc-title")).to_be_visible()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)

    # Both module families share one navigation; every granted route is reachable once.
    assert _panel_names(page) == PANELS
    assert _v8_names(page) == [label for label, _ in V8_PAGES.values()]

    for page_id, (_label, heading) in V8_PAGES.items():
        page.locator(f"[data-testid=clinic-v8-panel-{page_id}]").click()
        view = page.locator(f"[data-testid=clinic-v8-view-{page_id}]")
        view.wait_for(state="visible", timeout=20000)
        view.get_by_text(heading).first.wait_for(state="visible", timeout=20000)
    assert not js_errors, js_errors


def test_users_and_asr_profiles_load_real_data_through_the_admin_client(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    requests: list[str] = []
    page.on("request", lambda req: requests.append(req.url))
    statuses: list[tuple[int, str]] = []
    page.on("response", lambda res: statuses.append((res.status, res.url)))
    page.goto(base + "/admin")
    expect(page.get_by_test_id("clinic-v8-view-users").locator("#acc-title")).to_be_visible()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)

    page.locator("[data-testid=clinic-v8-panel-users]").click()
    users = page.locator("[data-testid=clinic-v8-view-users]")
    users.get_by_text("w226admin").first.wait_for(state="visible", timeout=20000)
    assert users.locator("[role=alert]").count() == 0

    with page.expect_response(lambda r: r.url.endswith("/api/admin/clinic/training/schedule") and r.status == 200):
        page.locator("[data-testid=clinic-v8-panel-asr-profiles]").click()
    asr = page.locator("[data-testid=clinic-v8-view-asr-profiles]")
    asr.get_by_text("Sprachprofile").first.wait_for(state="visible", timeout=20000)
    # The training section is loaded from the real route (the voice-consent section is not wired in this harness).
    assert any(c == 200 and u.endswith("/api/admin/clinic/training/schedule") for c, u in statuses), statuses

    # The admin routes are reached at their real roots, never below the clinic API root.
    assert any(c == 200 and "/admin/users?" in u for c, u in statuses)
    assert not any("/api/clinic/v1/admin/" in url for url in requests), requests
    assert not js_errors, js_errors


def test_probe_403_hides_the_admin_pages_on_the_team_page(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)
    page.route("**/admin/users*", lambda route: route.fulfill(status=403, content_type="application/json", body="{}"))
    page.route("**/api/admin/clinic/training/**", lambda route: route.fulfill(status=403, content_type="application/json", body="{}"))
    page.goto(base + TEAM_PAGE)
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    page.locator("[data-testid=clinic-panel-pools]").wait_for(state="visible", timeout=20000)
    page.get_by_text("Berechtigungen werden geprüft").wait_for(state="hidden", timeout=20000)
    hidden = {"users", "authors", "asr-profiles"}
    assert hidden.isdisjoint({n for n in _v8_ids(page)}), _v8_ids(page)
    assert not js_errors, js_errors


def _v8_ids(page) -> list[str]:
    return [b.get_attribute("data-testid").removeprefix("clinic-v8-panel-") for b in page.locator("[data-testid^=clinic-v8-panel-]").all()]


def test_non_admin_account_never_reaches_the_admin_pages(live_server, browser):
    base, _ids = live_server
    page, _errors = _login(browser, base, LEAD_EMAIL)
    assert page.goto(base + "/admin").status == 403
    assert page.locator("[data-testid^=clinic-v8-panel-]").count() == 0


def test_render_carries_the_v8_modules_only_in_clinic_mode(tmp_path):
    on = _render(tmp_path, True)["/admin"]
    for module in ("compose-admin-pools", "compose-authors", "compose-users", "compose-vocabulary",
                   "compose-commands-templates", "compose-asr-profiles", "compose-import", "compose-jobs",
                   "compose-activity"):
        assert f"/static/js/clinic/{module}.js" in on, module
    (tmp_path / "off").mkdir()
    off = _render(tmp_path / "off", False)["/admin"]
    assert "clinic-admin-root" not in off and "/static/js/clinic/" not in off and "clinic-v8" not in off


def test_admin_template_stays_one_module_block_with_one_client():
    src = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
    assert src.count("createClinicApiClient()") == 1
    assert len(re.findall(r"clinic-[a-z-]+-view'", src)) >= len(V8_PAGES)
