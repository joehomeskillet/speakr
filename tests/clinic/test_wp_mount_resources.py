"""Mount of the clinic resources view in the normal page (w217).

PAR-VOC-001, PAR-ASR-002; reuses the w207 mount mechanism.

The real application is started in a child process (real Flask app, throwaway
SQLite database, one synthetic user, no patient data). With CLINIC_MODE on,
headless Chromium logs in through the login form, opens ``/``, navigates to
"Ressourcen", loads the own vocabulary list and adds one synthetic entry.
Unauthenticated access to the page and to the vocabulary API is rejected. With
CLINIC_MODE off the page has no "Ressourcen" entry and loads no clinic script.

The browser case needs Playwright and its Chromium (requirements-dev.lock) and
the gitignored static/vendor plus a built static/css/tailwind.css; without them
it errors. The rendered-page and import-graph cases need no browser.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic-pass-217!"
EMAIL = "w217@local.test"
WORD = "Synthwort217"

SERVER = r'''
import sys
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity
from src.init_db import initialize_database
from src.models import User

port = int(sys.argv[1])
with app.app_context():
    initialize_database(app)
    user = User(username="w217", email=sys.argv[2],
                password=identity.hash_password(sys.argv[3]), is_admin=False)
    db.session.add(user); db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", login_policy="local_only"))
    db.session.commit()
server = make_server("127.0.0.1", port, app, threaded=True)
print("READY", flush=True)
server.serve_forever()
'''

RENDER = r'''
import sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
with app.app_context():
    initialize_database(app)
    u = User(username="r217", email="r217@local.test", password="x", is_admin=False)
    db.session.add(u); db.session.commit(); uid = u.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True
sys.stdout.write("BODY:" + c.get("/").get_data(as_text=True))
'''


def _env(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w217.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    return env


def _render(tmp_path: Path, clinic: bool) -> str:
    done = subprocess.run([sys.executable, "-c", RENDER], cwd=ROOT, env=_env(tmp_path, clinic),
                          capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    return done.stdout.split("BODY:", 1)[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("w217")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp / "server.log"  # a file, not a pipe: nobody drains a pipe while the browser runs
    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port), EMAIL, PASSWORD], cwd=ROOT,
                                env=_env(tmp, True), stdout=out, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 120
            while "READY" not in log.read_text():
                assert proc.poll() is None, "server exited before it was ready: " + log.read_text()[-2000:]
                assert time.time() < deadline, "server start timed out"
                time.sleep(0.2)
            yield f"http://127.0.0.1:{port}"
        finally:
            proc.terminate()
            proc.wait(timeout=20)


def test_browser_login_resources_load_and_add_entry(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE: a Chromium build other than the one Playwright expects.
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            errors, js_errors = [], []
            page.on("pageerror", lambda exc: (errors.append(str(exc)), js_errors.append(str(exc))))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)

            # Unauthenticated: the page redirects to login, the API rejects.
            page.goto(live_server + "/")
            assert "/login" in page.url
            denied = ctx.request.get(live_server + "/api/clinic/v1/vocabulary/search?scope=user&q=&mode=contains&status=all",
                                     max_redirects=0)
            assert denied.status in (401, 302), denied.status

            page.fill("input[name=email]", EMAIL)
            page.fill("input[name=password]", PASSWORD)
            page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
            page.wait_for_load_state("load")
            assert "/login" not in page.url

            page.goto(live_server + "/")
            nav = page.locator("[data-testid=nav-clinic-resources]")
            nav.wait_for(state="visible", timeout=20000)
            assert nav.inner_text().strip() == "Ressourcen"
            nav.click()
            view = page.locator(".clinic-resources-view")
            try:
                view.wait_for(state="visible", timeout=20000)
            except Exception as exc:
                raise AssertionError(f"resources view not shown; page problems: {errors}") from exc
            assert nav.get_attribute("aria-current") == "page"

            # Own vocabulary list is loaded (empty), then one synthetic entry is added.
            assert view.get_by_role("tab", name="Vokabular").get_attribute("aria-selected") == "true"
            view.get_by_text("Eigene Liste").first.wait_for(state="visible", timeout=20000)
            view.get_by_text("Keine Einträge").wait_for(state="visible", timeout=20000)
            page.fill("#voc-import", WORD)
            view.get_by_role("button", name="Wortliste importieren").click()
            view.get_by_role("cell", name=WORD).wait_for(state="visible", timeout=20000)

            assert not js_errors, js_errors
        finally:
            browser.close()


def test_clinic_page_declares_entry_and_flag(tmp_path):
    out = _render(tmp_path, True)
    assert 'data-clinic-mode="True"' in out
    assert "Ressourcen" in out
    assert "Arbeitsliste" in out
    assert "<clinic-resources-view" in out


def test_clinic_mode_off_page_has_no_entry(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    assert "Ressourcen" not in out
    assert "clinic-resources-view" not in out


def test_import_graph_is_clinic_gated_and_dynamic():
    src = (ROOT / "static/js/app.modular.js").read_text(encoding="utf-8")
    assert not re.search(r"^import .*clinic/", src, re.M), "clinic code must not be a static import"
    assert "import('./clinic/compose-resources.js')" in src
    assert "dataset?.clinicMode === 'True'" in src
    assert src.count("createClinicApiClient()") == 1, "one shared api-client"
    assert "registerResourcesView(app)" in src
