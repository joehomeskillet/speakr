"""Mount of the clinic worklist in the normal page (w207).

PAR-WF-001, PAR-REC-006; R-041, V4-013.

The real application is started in a child process (real Flask app, throwaway
SQLite database, synthetic user and one synthetic dictation, no patient data).
With CLINIC_MODE on, headless Chromium logs in through the login form, opens
``/`` and must land on the worklist, see the synthetic row and open it.
Unauthenticated access redirects to the login page. With CLINIC_MODE off the
page has no "Arbeitsliste" entry and loads no clinic script.

The browser case needs Playwright and its Chromium (requirements-dev.lock);
without them it errors. The rendered-page and import-graph cases use the real
templates and source without a browser.
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
PASSWORD = "Synthetic-pass-207!"
EMAIL = "w207@local.test"

SERVER = r'''
import sys, uuid
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity
from src.init_db import initialize_database
from src.models import Recording, User
from src.clinic import workflow
from src.clinic.models import ClinicDictation

port = int(sys.argv[1])
with app.app_context():
    initialize_database(app)
    user = User(username="w207", email=sys.argv[2],
                password=identity.hash_password(sys.argv[3]), is_admin=False)
    db.session.add(user); db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", login_policy="local_only"))
    rec = Recording(user_id=user.id, title="Synthetisch", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec); db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=user.id, version_counter=0)
    db.session.add(dic); db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=user.id, pool_id=str(uuid.uuid4()), status=workflow.TO_WRITE, audio_seconds=30))
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
    u = User(username="r207", email="r207@local.test", password="x", is_admin=False)
    db.session.add(u); db.session.commit(); uid = u.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True
sys.stdout.write("BODY:" + c.get("/").get_data(as_text=True))
'''


def _env(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w207.db'}",
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
    tmp = tmp_path_factory.mktemp("w207")
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


def test_browser_login_worklist_row_and_open(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE: a Chromium build other than the one Playwright expects.
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            errors, js_errors = [], []  # js_errors: uncaught exceptions only; network refusals of upstream calls are not mount failures
            page.on("pageerror", lambda exc: (errors.append(str(exc)), js_errors.append(str(exc))))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)

            # Unauthenticated: redirected to the login page.
            page.goto(live_server + "/")
            assert "/login" in page.url

            page.fill("input[name=email]", EMAIL)
            page.fill("input[name=password]", PASSWORD)
            page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
            page.wait_for_load_state("load")
            assert "/login" not in page.url

            # The worklist is the landing view after login.
            page.goto(live_server + "/")
            view = page.locator("[data-testid=clinic-worklist-view]")
            try:
                view.wait_for(state="visible", timeout=20000)
            except Exception as exc:
                raise AssertionError(f"worklist not shown; page problems: {errors}") from exc
            nav = page.locator("[data-testid=nav-clinic-worklist]")
            assert nav.inner_text().strip() == "Arbeitsliste"

            # Leave the list and come back through the navigation entry.
            page.locator("[data-testid=nav-recordings]").click()
            view.wait_for(state="detached", timeout=20000)
            nav.click()
            view.wait_for(state="visible", timeout=20000)

            # Synthetic row, opened by click: the detail panel switches to the dictation.
            row = page.locator("[data-row]").first
            row.wait_for(state="visible", timeout=20000)
            row.click()
            page.locator("#cwv-detail-title").wait_for(state="visible", timeout=20000)
            assert row.get_attribute("aria-selected") == "true"

            # W16: no patient name or ID in the worklist.
            assert "patient" not in view.inner_text().lower()
            assert not js_errors, js_errors
        finally:
            browser.close()


def test_clinic_page_declares_entry_and_flag(tmp_path):
    out = _render(tmp_path, True)
    assert 'data-clinic-mode="True"' in out
    assert "Arbeitsliste" in out
    assert "<clinic-worklist-view" in out


def test_clinic_mode_off_page_has_no_entry(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    assert "Arbeitsliste" not in out
    assert "clinic-worklist-view" not in out


def test_import_graph_is_clinic_gated_and_dynamic():
    src = (ROOT / "static/js/app.modular.js").read_text(encoding="utf-8")
    assert not re.search(r"^import .*clinic/", src, re.M), "clinic code must not be a static import"
    assert "import('./clinic/compose-worklist.js')" in src
    assert "dataset?.clinicMode === 'True'" in src
    assert src.count("createClinicApiClient()") == 1, "one shared api-client"
    assert "registerWorklistView(app)" in src
