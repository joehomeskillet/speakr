"""Mount of the clinic worklist in the normal page (w207).

PAR-WF-001, PAR-REC-006; R-041, V4-013.

The real application is started in a child process (real Flask app, throwaway
SQLite database, synthetic users and dictations, no patient data).
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
TYPIST = "typist207@local.test"
STRANGER = "stranger207@local.test"

SERVER = r'''
import sys
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity, pools, workflow
from src.clinic.models import ClinicContextRevision, ClinicDictation
from src.init_db import initialize_database
from src.models import Recording, User

port, author_email, password, typist_email, stranger_email = sys.argv[1:]


def add_user(username, email, name, *, admin=False):
    user = User(username=username, email=email, name=name,
                password=identity.hash_password(password), is_admin=admin)
    db.session.add(user)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", login_policy="local_only"))
    return user


with app.app_context():
    initialize_database(app)
    admin = add_user("admin207", "admin207@local.test", "Testverwaltung", admin=True)
    author = add_user("w207", author_email, "Autor Beispiel")
    typist = add_user("typist207", typist_email, "Schreibkraft Nord")
    add_user("stranger207", stranger_email, "Unbeteiligte Person")
    pool = pools.ClinicAuthorPool(name="Synthetischer Autorenpool", created_by=admin.id)
    db.session.add(pool)
    db.session.flush()
    for case, title, notes, assignee in (
        ("SYN-2071", "Stimmprobe", "Rückfrage vor Abschluss <b>Probe</b>", typist),
        ("SYN-2072", "Kontrollbefund", "Interne Zweitlesung", author),
    ):
        rec = Recording(user_id=author.id, title=title, notes=notes, status="COMPLETED", original_filename="s.wav")
        db.session.add(rec)
        db.session.flush()
        dic = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool.id, version_counter=0)
        db.session.add(dic)
        db.session.flush()
        db.session.add(ClinicContextRevision(dictation_id=dic.id, case_ref=case))
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dic.id, author_user_id=author.id, pool_id=pool.id, status=workflow.TO_WRITE,
            assignee_user_id=assignee.id, audio_seconds=30))
    db.session.commit()
    pools.allow(typist.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("view",))
server = make_server("127.0.0.1", int(port), app, threaded=True)
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
        proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port), EMAIL, PASSWORD, TYPIST, STRANGER], cwd=ROOT,
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

            # Aufnahmen is the landing view after login (v8-03); the worklist is the second tab.
            page.goto(live_server + "/")
            page.locator("[data-testid=nav-clinic-worklist]").click()
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


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_worklist_scoped_columns_search_and_refresh(live_server, theme):
    from playwright.sync_api import expect, sync_playwright

    cases = {
        "SYN-2071": ("Stimmprobe", "Rückfrage vor Abschluss <b>Probe</b>", "Schreibkraft Nord"),
        "SYN-2072": ("Kontrollbefund", "Interne Zweitlesung", "Autor Beispiel"),
    }
    rows_url = f"{live_server}/api/clinic/v1/worklist/rows"
    output = os.environ.get("P068_BROWSER_OUT")

    def assert_items(response, expected):
        assert response.ok, response.status
        data = response.json()
        assert data["total"] == len(expected)
        assert {row["case_display"]: (row["title"], row["notes"], row["recipient"]) for row in data["items"]} == {
            case: cases[case] for case in expected
        }

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            for email, visible in ((EMAIL, set(cases)), (TYPIST, {"SYN-2071"}), (STRANGER, set())):
                with browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=theme) as context:
                    context.add_init_script(f"localStorage.setItem('darkMode', '{str(theme == 'dark').lower()}')")
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(live_server + "/login")
                    page.fill("input[name=email]", email)
                    page.fill("input[name=password]", PASSWORD)
                    page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
                    page.get_by_test_id("nav-clinic-worklist").click()
                    view = page.get_by_test_id("clinic-worklist-view")
                    expect(view).to_be_visible()
                    expect(page.locator("html.dark")).to_have_count(int(theme == "dark"))
                    rows = view.locator("[data-row]")
                    expect(rows).to_have_count(len(visible))

                    def assert_columns():
                        for case in sorted(visible):
                            row = rows.filter(has=page.locator(".titletext strong", has_text=f"Fall-ID {case}"))
                            expect(row.locator(".titletext strong")).to_have_text(f"Fall-ID {case}")
                            for label, value in zip(("Titel", "Anmerkung", "Empfänger"), cases[case], strict=True):
                                cell = row.locator(f'td[data-label="{label}"]')
                                expect(cell).to_be_visible(timeout=5000)
                                expect(cell).to_have_text(value)
                        expect(rows.locator('td[data-label="Anmerkung"] b')).to_have_count(0)

                    assert_columns()
                    with page.expect_response(lambda response: response.url == rows_url) as pending:
                        view.get_by_test_id("refresh-button").click()
                    assert_items(pending.value, visible)
                    expect(rows).to_have_count(len(visible))
                    assert_columns()

                    if output and email != TYPIST:
                        state = "rows" if visible else "empty"
                        if not visible:
                            expect(view.get_by_test_id("empty-state")).to_contain_text("Keine Diktate")
                        for width, height in ((1440, 900), (1280, 720), (390, 844)):
                            page.set_viewport_size({"width": width, "height": height})
                            assert_columns()
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                            stem = f"p068-{width}x{height}-{theme}-{state}"
                            if width == 390:
                                page.screenshot(path=Path(output) / f"{stem}-top.png", animations="disabled")
                                target = rows.first if visible else view.get_by_test_id("empty-state")
                                target.evaluate("element => element.scrollIntoView({block: 'center'})")
                                expect(target).to_be_in_viewport()
                            elif width == 1280 and visible:
                                rows.last.scroll_into_view_if_needed()
                            page.screenshot(path=Path(output) / f"{stem}.png", animations="disabled")

                    search = view.get_by_role("searchbox", name="Metadatensuche")
                    for term, case in (("Stimmprobe", "SYN-2071"), ("Zweitlesung", "SYN-2072"),
                                       ("Schreibkraft Nord", "SYN-2071")):
                        with page.expect_response(lambda response: response.url.startswith(rows_url + "?")
                                                  and "search=" in response.url) as pending:
                            search.fill(term)
                            search.press("Enter")
                        expected = [case] if case in visible else []
                        assert_items(pending.value, expected)
                        expect(rows.locator(".titletext strong")).to_have_text([f"Fall-ID {item}" for item in expected])
                    assert not errors, errors
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
