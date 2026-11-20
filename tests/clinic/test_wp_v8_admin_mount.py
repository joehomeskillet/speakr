"""Mount of the V8 administration pages in the clinic admin panel set of /admin (v8-72).

PAR-ADM-001 to PAR-ADM-005, PAR-VOC-001 to PAR-VOC-008, PAR-ASR-002, PAR-OPS-008.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway SQLite database, synthetic users
only, headless Chromium). The site admin opens the Klinik tab of /admin: every V8 page must be listed in its own
navigation row and its module root must render. A non-site-admin account never reaches /admin. The existing panel row
(clinic-panel-*) is not touched by the V8 row (clinic-v8-panel-*).
"""

from __future__ import annotations

import re
from pathlib import Path

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, LEAD_EMAIL, PANELS, _login, _panel_names, _render, browser, live_server,
)

# The imported fixtures are used by name as test arguments.
_FIXTURES = (browser, live_server)

ROOT = Path(__file__).resolve().parents[2]

# id -> (label in the navigation row, heading text the module root renders)
V8_PAGES = {
    "admin-pools": ("Schreibkräfte-Pools", "Schreibkräfte-Pools"),
    "authors": ("Diktierende und Autorenpools", "Diktierende und Autorenpools"),
    "users": ("Benutzer und Rollen", "Benutzer und Rollen"),
    "vocabulary": ("Wortschatz", "Wortschatz"),
    "commands-templates": ("Befehle und Vorlagen", "Befehle und Vorlagen"),
    "asr-profiles": ("Sprachprofile", "Sprachprofile"),
    "import": ("Import", "Dateien und Geräteimport"),
    "jobs": ("Verarbeitung", "Verarbeitung"),
    "activity": ("Aktivität und Audit", "Aktivität und Audit"),
}


def _v8_names(page) -> list[str]:
    return [b.inner_text().strip() for b in page.locator("[data-testid^=clinic-v8-panel-]").all()]


def test_site_admin_sees_every_v8_page_and_its_module_root_renders(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin")
    page.locator("[data-testid=admin-tab-clinic]").click()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)

    # The earlier panel row is unchanged; the V8 pages sit in their own row.
    assert _panel_names(page) == PANELS
    assert _v8_names(page) == [label for label, _ in V8_PAGES.values()]

    for page_id, (_label, heading) in V8_PAGES.items():
        page.locator(f"[data-testid=clinic-v8-panel-{page_id}]").click()
        view = page.locator(f"[data-testid=clinic-v8-view-{page_id}]")
        view.wait_for(state="visible", timeout=20000)
        view.get_by_text(heading).first.wait_for(state="visible", timeout=20000)
    assert not js_errors, js_errors


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
