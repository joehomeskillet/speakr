"""V8 prototype foundation: scoped stylesheet, header and page frame (v8-110).

The stylesheet static/css/clinic-v8.css is a scoped port of the V8 prototype styles. It is linked only in
CLINIC_MODE; the clinic shell root carries the class v8-ui. The header is the prototype topbar: brand, the two
primary tabs as pill tabs, the menu "Verwaltung & Seiten", the accent button "Neue Aufnahme" and the avatar with
the account menu. Synthetic data only; the browser cases reuse the synthetic server of the w213 mount test.
Set V8_SHOT_DIR to a directory to also write screenshots of the header and the empty page frame.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "static/css/clinic-v8.css"

_spec = importlib.util.spec_from_file_location("clinic_shell_nav_fixtures", ROOT / "tests/clinic/test_wp_v8_shell_nav.py")
_nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nav)
live_server = _nav.live_server
_render = _nav._render
_launch = _nav._launch
_login = _nav._login
_stub = _nav._stub_recordings_api
_mc = _nav._mc

ADMIN_RENDER = r'''
import sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
with app.app_context():
    initialize_database(app)
    u = User(username="adm110", email="adm110@local.test", password="x", is_admin=True)
    db.session.add(u); db.session.commit(); uid = u.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True
sys.stdout.write("BODY:" + c.get("/admin").get_data(as_text=True))
'''


def _render_admin(tmp_path: Path, clinic: bool) -> str:
    done = subprocess.run([sys.executable, "-c", ADMIN_RENDER], cwd=ROOT, env=_mc._env(tmp_path, clinic),
                          capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    return done.stdout.split("BODY:", 1)[1]


def test_clinic_pages_link_the_stylesheet_and_set_the_root_class(tmp_path):
    index = _render(tmp_path, True)
    assert "css/clinic-v8.css" in index
    assert re.search(r'id="app"[^>]*class="[^"]*\bv8-ui\b', index, re.S)
    assert 'class="topbar"' in index and 'data-testid="clinic-primary-nav"' in index
    admin = _render_admin(tmp_path, True)
    assert "css/clinic-v8.css" in admin
    assert re.search(r'<body class="[^"]*\bv8-ui\b', admin)


def test_upstream_pages_have_no_stylesheet_and_no_root_class(tmp_path):
    index = _render(tmp_path, False)
    admin = _render_admin(tmp_path, False)
    for out in (index, admin):
        assert "clinic-v8" not in out
        assert "v8-ui" not in out
    assert "switchToUploadView" in index


def test_every_rule_is_scoped_under_the_root_class():
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(), flags=re.S)
    unscoped = []
    # walk the top-level blocks; descend into at-rules other than keyframes
    def walk(chunk):
        pos = 0
        while pos < len(chunk):
            brace = chunk.find("{", pos)
            if brace < 0:
                break
            head = chunk[pos:brace].strip()
            level, end = 1, brace + 1
            while level and end < len(chunk):
                level += {"{": 1, "}": -1}.get(chunk[end], 0)
                end += 1
            inner = chunk[brace + 1:end - 1]
            if head.startswith("@keyframes"):
                pass
            elif head.startswith("@"):
                walk(inner)
            else:
                for sel in re.split(r",(?![^(]*\))", head):
                    sel = sel.strip()
                    if not (sel.startswith(".v8-ui") or sel.startswith(":where(.v8-ui)") or sel.startswith(".dark .v8-ui")
                            or sel.startswith('[data-theme="dark"] .v8-ui')):
                        unscoped.append(sel)
            pos = end
    walk(text)
    assert not unscoped, unscoped[:10]
    assert ":root" not in text and not re.search(r"(^|[\s,}])(html|body)\b[^{]*\{", text)
    assert ".dark .v8-ui" in text  # a dark variable set exists


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_header_and_empty_frame(live_server, width, height):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    shots = os.environ.get("V8_SHOT_DIR")
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, width, height)
            _stub(page, dictation)
            page.goto(base + "/")
            page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
            page.wait_for_function("getComputedStyle(document.querySelector('#app')).opacity === '1'", timeout=10000)
            assert page.locator("#app.v8-ui").count() == 1
            assert page.locator('link[href*="clinic-v8.css"]').count() >= 1
            header = page.locator("header.topbar")
            assert header.count() == 1
            # Two pill tabs, the first current.
            tabs = page.locator("[data-testid=clinic-primary-nav] button")
            assert [t.strip() for t in tabs.all_inner_texts()] == ["Aufnahmen", "Arbeitsliste"]
            assert page.locator("[data-testid=nav-recordings]").get_attribute("aria-current") == "page"
            # The menu lists the pages of the app; the separate action row above Aufnahmen is gone (no visible buttons).
            page.locator("details.v6-admin summary").click()
            menu = page.locator(".v6-menu")
            menu.wait_for(state="visible", timeout=10000)
            for label in ("Neue Aufnahme", "Live-Diktat", "Archiv", "Dateien und Geräteimport", "Einstellungen",
                          "Offline und Wiederherstellung"):
                assert menu.get_by_text(label, exact=True).count() == 1, label
            if shots:
                page.screenshot(path=f"{shots}/header-menu-{width}.png")
            page.keyboard.press("Escape")
            menu.wait_for(state="hidden", timeout=10000)
            visible_quick = page.locator("[data-testid=clinic-menu-actions] button").evaluate_all(
                "els => els.filter(e => { const r = e.getBoundingClientRect(); return r.width > 8 || r.height > 8 }).length")
            assert visible_quick == 0
            # The avatar shows the initials and opens the account menu.
            avatar = page.locator("[data-user-menu-toggle]").first
            assert avatar.inner_text().strip() == "T2"
            avatar.click()
            account = page.locator("[data-user-menu-dropdown]")
            account.wait_for(state="visible", timeout=10000)
            for label in ("Einstellungen", "Konto", "Abmelden"):
                assert account.get_by_text(label, exact=True).count() == 1, label
            if shots:
                page.screenshot(path=f"{shots}/account-menu-{width}.png")
            page.keyboard.press("Escape")
            page.mouse.click(5, 300)
            account.wait_for(state="hidden", timeout=10000)
            if shots:
                page.screenshot(path=f"{shots}/frame-{width}.png")
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
