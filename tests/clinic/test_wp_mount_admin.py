"""Mount of the clinic administration panels in the existing admin pages (w226).

PAR-ADM-001, PAR-ADM-002, PAR-ADM-004, PAR-CFG-001, PAR-CFG-002; R-051.

The real application is started in a child process (real Flask app, throwaway
SQLite database, synthetic users, one synthetic author pool with two members;
no patient data). With CLINIC_MODE on, headless Chromium logs in through the
login form and uses the "Klinik" tab of /admin: the panel navigation, one
permission change saved through the grants panel (Speichern), and the removal
of a pool membership. A team admin who is not a site admin opens
/group-management: the panels the API does not authorise are hidden and a read
of another user's rights shows the denied state (the stock /group-management route
is policy-disabled in clinic mode, so the unchanged template is served on a test-only
route behind the real guard). With CLINIC_MODE off both pages
carry no clinic markup and load no clinic module.

The browser cases need Playwright and its Chromium (requirements-dev.lock);
without them they error. The render cases use the real templates without a browser.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic-pass-226!"
ADMIN_EMAIL = "w226admin@local.test"
LEAD_EMAIL = "w226lead@local.test"
TEAM_PAGE = "/__w226/group-management"  # test-only route, see HARNESS
PANELS = ["Konten", "Pools", "Fachrechte", "Verzeichnis", "Vorlagen", "Kategorien", "Spalten"]

SERVER = r'''
import json, sys
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity, pools
from src.init_db import initialize_database
from src.models import Group, GroupMembership, User

port = int(sys.argv[1])
#HARNESS#
with app.app_context():
    initialize_database(app)

    def account(name, email, admin=False):
        user = User(username=name, email=email, password=identity.hash_password(sys.argv[2]), is_admin=admin)
        db.session.add(user); db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", login_policy="local_only"))
        return user

    admin = account("w226admin", sys.argv[3], True)
    lead = account("w226lead", sys.argv[4])
    typist = account("w226typist", "w226typist@local.test")
    other = account("w226other", "w226other@local.test")
    group = Group(name="Team 226")
    db.session.add(group); db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))
    db.session.commit()
    pool = pools.create_author_pool("Pool 226", actor_id=admin.id)
    pools.add_author_member(pool.id, typist.id, actor_id=admin.id)
    pools.add_author_member(pool.id, other.id, actor_id=admin.id)
    ids = dict(admin=admin.id, lead=lead.id, typist=typist.id, other=other.id)
server = make_server("127.0.0.1", port, app, threaded=True)
print("READY " + json.dumps(ids), flush=True)
server.serve_forever()
'''

HARNESS = r'''
# In clinic mode the stock /group-management route is policy-disabled (internal sharing). This harness serves
# the unchanged group-admin.html on a test-only route, classified AUTH, so the template mount runs behind the real guard.
from flask import render_template
from flask_login import login_required
from src.clinic import route_policy
from src.clinic.config import clinic_mode_enabled

def team_page():
    return render_template('group-admin.html', title='Group Management', groups_title='Teams',
                           global_retention_days=0, clinic_mode=clinic_mode_enabled(), user_language='de')

app.add_url_rule('/__w226/group-management', 'w226_team', login_required(team_page))
route_policy.ROUTE_POLICY['w226_team'] = route_policy.RouteRule(route_policy.AUTH)
'''
SERVER = SERVER.replace('#HARNESS#', HARNESS)

RENDER = r'''
import sys
from src.app import app, db
from src.clinic import identity
from src.init_db import initialize_database
from src.models import Group, GroupMembership, User

#HARNESS#
with app.app_context():
    initialize_database(app)
    admin = User(username="r226a", email="r226a@local.test", password="x", is_admin=True)
    lead = User(username="r226l", email="r226l@local.test", password="x", is_admin=False)
    db.session.add_all([admin, lead]); db.session.flush()
    group = Group(name="Render 226")
    db.session.add(group); db.session.flush()
    db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))
    db.session.commit()
    ids = {"/admin": admin.id, "/__w226/group-management": lead.id}
out = {}
for path, uid in ids.items():
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    out[path] = client.get(path).get_data(as_text=True)
import json
sys.stdout.write("BODY:" + json.dumps(out))
'''
RENDER = RENDER.replace('#HARNESS#', HARNESS)


def _env(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w226.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    return env


def _render(tmp_path: Path, clinic: bool) -> dict:
    done = subprocess.run([sys.executable, "-c", RENDER], cwd=ROOT, env=_env(tmp_path, clinic),
                          capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    return json.loads(done.stdout.split("BODY:", 1)[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("w226")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp / "server.log"  # a file, not a pipe: nobody drains a pipe while the browser runs
    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port), PASSWORD, ADMIN_EMAIL, LEAD_EMAIL], cwd=ROOT,
                                env=_env(tmp, True), stdout=out, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 120
            while "READY" not in log.read_text():
                assert proc.poll() is None, "server exited before it was ready: " + log.read_text()[-2000:]
                assert time.time() < deadline, "server start timed out"
                time.sleep(0.2)
            line = next(row for row in log.read_text().splitlines() if row.startswith("READY"))
            yield f"http://127.0.0.1:{port}", json.loads(line[len("READY "):])
        finally:
            proc.terminate()
            proc.wait(timeout=20)


@pytest.fixture()
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE: a Chromium build other than the one Playwright expects.
        instance = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            yield instance
        finally:
            instance.close()


def _login(browser, base: str, email: str):
    page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
    js_errors: list[str] = []
    page.on("pageerror", lambda exc: js_errors.append(str(exc)))
    page.goto(base + "/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
    page.wait_for_load_state("load")
    assert "/login" not in page.url
    return page, js_errors


def _panel_names(page) -> list[str]:
    return [b.inner_text().strip() for b in page.locator("[data-testid^=clinic-panel-]").all()]


def test_admin_tab_panels_apply_and_membership_removal(live_server, browser):
    base, ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin")
    page.locator("[data-testid=admin-tab-clinic]").click()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)

    # The panels are registered in the Klinik section; the first one (accounts) is mounted.
    assert _panel_names(page) == PANELS
    page.locator("#acc-title").wait_for(state="visible", timeout=20000)

    # Pool membership removal: confirm dialog, then one member is left (checked on the server).
    page.locator("[data-testid=clinic-panel-pools]").click()
    view = page.locator("[data-testid=clinic-view-pools]")
    remove = view.get_by_role("button", name="Entfernen")
    remove.first.wait_for(state="visible", timeout=20000)
    assert remove.count() == 2
    remove.first.click()
    view.get_by_role("alertdialog").wait_for(state="visible", timeout=20000)
    view.get_by_role("button", name="Ja, entfernen").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=clinic-view-pools] article li button').length === 1",
                           timeout=20000)
    members = page.evaluate("fetch('/api/clinic/v1/pools').then(r => r.json())"
                            ".then(d => d.author_pools.map(p => p.member_user_ids.length))")
    assert members == [1]

    # Save one permission through the grants panel (Speichern) for the typist; the entry is listed afterwards.
    page.locator("[data-testid=clinic-panel-grants]").click()
    view = page.locator("[data-testid=clinic-view-grants]")
    view.locator("#clinic-grants-user").fill(str(ids["typist"]))
    view.get_by_role("button", name="Fachrechte laden").click()
    view.locator("#grants-title").wait_for(state="visible", timeout=20000)
    view.get_by_text("Keine Einträge").wait_for(state="visible", timeout=20000)
    view.get_by_label("Ansehen").check()
    view.get_by_role("button", name="Speichern").click()
    view.locator("ul[aria-labelledby=grants-existing] li", has_text="Ansehen").wait_for(state="visible", timeout=20000)
    stored = page.evaluate(f"fetch('/api/clinic/v1/identity/users/{ids['typist']}/grants').then(r => r.json())")
    assert [(i["effect"], i["scope"], i["actions"]) for i in stored["items"]] == [("allow", "own", ["view"])]
    assert not js_errors, js_errors


def test_team_admin_sees_only_authorised_panels_and_denied_state(live_server, browser):
    base, ids = live_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)

    # A team admin who is not a site admin never reaches the full administration (existing clinic policy).
    assert page.goto(base + "/admin").status == 403

    page.goto(base + TEAM_PAGE)
    try:
        page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    except Exception as exc:
        raise AssertionError(f"clinic section not shown; page errors {js_errors}; body {page.inner_text('body')[:400]!r}") from exc
    page.locator("[data-testid=clinic-panel-pools]").wait_for(state="visible", timeout=20000)
    names = _panel_names(page)
    assert "Pools" in names and "Fachrechte" in names
    assert "Konten" not in names and "Verzeichnis" not in names
    withheld = page.locator("[data-testid=clinic-admin-withheld]").inner_text()
    assert "Konten" in withheld and "Verzeichnis" in withheld

    # Own rights are readable (read-only); another user's rights show the denied state.
    page.locator("[data-testid=clinic-panel-grants]").click()
    view = page.locator("[data-testid=clinic-view-grants]")
    view.get_by_text("Nur Lesezugriff").wait_for(state="visible", timeout=20000)
    view.locator("#clinic-grants-user").fill(str(ids["typist"]))
    view.get_by_role("button", name="Fachrechte laden").click()
    view.get_by_text("Keine Berechtigung für diese Aktion.").wait_for(state="visible", timeout=20000)
    assert not js_errors, js_errors


def test_clinic_pages_carry_the_mount(tmp_path):
    pages = _render(tmp_path, True)
    admin, team = pages["/admin"], pages[TEAM_PAGE]
    assert 'data-testid="admin-tab-clinic"' in admin and 'data-mode="admin"' in admin
    assert 'data-mode="team"' in team and "admin-tab-clinic" not in team
    for out in (admin, team):
        assert 'id="clinic-admin-root"' in out
        assert 'type="module"' in out and "/static/js/clinic/accounts-ui.js" in out


def test_clinic_mode_off_pages_have_no_clinic_markup(tmp_path):
    for path, out in _render(tmp_path, False).items():
        assert "clinic-admin-root" not in out, path
        assert "/static/js/clinic/" not in out, path
        assert "admin-tab-clinic" not in out and "Klinik" not in out, path


def test_mount_is_self_contained_in_both_templates():
    for name in ("admin.html", "group-admin.html"):
        src = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert src.count("createClinicApiClient()") == 1, "one shared api client"
        assert "createApp(Root)" in src and src.count("{% if clinic_mode %}") >= 2
        assert re.search(r"<script type=\"module\">[^<]*import \{ createClinicApiClient \}", src)
        # a separate app: the upstream app is still created from its own inline setup
        assert src.count("}).mount('#app');") == 1
