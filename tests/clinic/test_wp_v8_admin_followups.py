"""Follow-ups of the admin mount: Sprachprofile training client and the Abnahme page (v8-92).

PAR-ASR-002 (training list through the admin base), PAR-WF-009 / PAR-WF-010 (acceptance policy publication), W12.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway SQLite database, synthetic users
only, headless Chromium) and seeds one synthetic dictation. The site admin opens Sprachprofile: the training section loads
from /api/admin/clinic/training (never below /api/clinic/v1). The Abnahme page publishes an approver through the real route
(If-Match, CSRF, audit) and is hidden when the admin probe is refused.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import socket
from pathlib import Path

import pytest

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, LEAD_EMAIL, SERVER, TEAM_PAGE, _env, _login, browser,
)

_FIXTURES = (browser,)

ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = os.environ.get("V8_92_SCREENSHOTS")

SEED = '''
    from src.clinic.models import ClinicDictation
    from src.models import Recording
    approvers = [account("w92user%d" % n, "w92user%d@local.test" % n) for n in range(1, 6)]
    rec = Recording(user_id=typist.id, title="Synthetisch", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec); db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=typist.id, pool_id=pool.id, version_counter=0)
    db.session.add(dic); db.session.commit()
    ids = dict(admin=admin.id, lead=lead.id, typist=typist.id, other=other.id, dictation=dic.id,
               approvers=[u.id for u in approvers])
'''
_ANCHOR = "    ids = dict(admin=admin.id, lead=lead.id, typist=typist.id, other=other.id)\n"
assert _ANCHOR in SERVER
ACC_SERVER = SERVER.replace(_ANCHOR, SEED)


@pytest.fixture(scope="module")
def acc_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("w92")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp / "server.log"
    from test_wp_mount_admin import PASSWORD

    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "-c", ACC_SERVER, str(port), PASSWORD, ADMIN_EMAIL, LEAD_EMAIL], cwd=ROOT,
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


def _open_clinic(page, base: str) -> None:
    page.goto(base + "/admin")
    page.locator("[data-testid=admin-tab-clinic]").click()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)


def test_sprachprofile_training_loads_through_the_admin_base(acc_server, browser):
    base, _ids = acc_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    seen: list[tuple[int, str]] = []
    page.on("response", lambda res: seen.append((res.status, res.url)))
    _open_clinic(page, base)
    page.locator("[data-testid=clinic-v8-panel-asr-profiles]").click()
    view = page.locator("[data-testid=clinic-v8-view-asr-profiles]")
    view.locator("[data-testid=training]").wait_for(state="visible", timeout=20000)
    view.locator("[data-testid=training-empty], [data-testid=training] table, [data-testid=training] ul").first.wait_for(
        state="attached", timeout=20000)
    page.wait_for_timeout(500)
    assert view.locator("[data-testid=training-denied]").count() == 0
    assert any(c == 200 and u.endswith("/api/admin/clinic/training/schedule") for c, u in seen), seen
    assert not any("/api/clinic/v1/admin/" in u for _c, u in seen), seen
    assert not js_errors, js_errors


def test_abnahme_page_publishes_an_approver_through_the_real_route(acc_server, browser):
    base, ids = acc_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    _open_clinic(page, base)
    page.locator("[data-testid=clinic-policy-panel-acceptance]").click()
    view = page.locator("[data-testid=clinic-policy-view-acceptance]")
    view.get_by_role("heading", name="Abnahme").wait_for(state="visible", timeout=20000)
    view.locator("[data-testid=acc-empty]").wait_for(state="visible", timeout=20000)

    view.locator("#acc-dictation").fill(ids["dictation"])
    view.get_by_role("button", name="Richtlinie laden").click()
    view.locator("[data-testid=acc-card]").wait_for(state="visible", timeout=20000)
    assert view.locator("[data-testid=acc-version]").inner_text() == "Version 0"
    view.locator("[data-testid=acc-no-approvers]").wait_for(state="visible", timeout=20000)

    approver = ids["approvers"][0]
    view.locator("#acc-approver").fill(str(approver))
    view.get_by_role("button", name="Hinzufügen").click()
    view.locator("[data-testid=acc-approvers] li", has_text=f"Benutzer {approver}").wait_for(state="visible", timeout=20000)
    view.get_by_role("button", name="Speichern").click()
    view.locator("[data-testid=acc-note]", has_text="Gespeichert. Version 1.").wait_for(state="visible", timeout=20000)

    stored = page.evaluate(
        f"fetch('/api/clinic/v1/dictations/{ids['dictation']}/acceptance-policy').then(r => r.json())")
    assert stored == {"version": 1, "acceptance_required": True, "approver_ids": [approver]}

    # An unknown approver is refused by the server (422), nothing is stored.
    view.locator("#acc-approver").fill("999999")
    view.get_by_role("button", name="Hinzufügen").click()
    view.get_by_role("button", name="Speichern").click()
    view.locator("[data-testid=acc-note]", has_text="nicht freigabeberechtigt").wait_for(state="visible", timeout=20000)
    stored = page.evaluate(
        f"fetch('/api/clinic/v1/dictations/{ids['dictation']}/acceptance-policy').then(r => r.json())")
    assert stored["version"] == 1 and stored["approver_ids"] == [approver]
    assert not js_errors, js_errors


def test_abnahme_page_is_hidden_when_the_admin_probe_is_refused(acc_server, browser):
    base, _ids = acc_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)
    page.route("**/admin/users*", lambda route: route.fulfill(status=403, content_type="application/json", body="{}"))
    page.goto(base + TEAM_PAGE)
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    page.locator("[data-testid=clinic-panel-pools]").wait_for(state="visible", timeout=20000)
    page.get_by_text("Berechtigungen werden geprüft").wait_for(state="hidden", timeout=20000)
    assert page.locator("[data-testid^=clinic-policy-panel-]").count() == 0
    assert not js_errors, js_errors


def test_non_admin_account_never_reaches_the_abnahme_page(acc_server, browser):
    base, _ids = acc_server
    page, _errors = _login(browser, base, LEAD_EMAIL)
    assert page.goto(base + "/admin").status == 403
    assert page.locator("[data-testid^=clinic-policy-panel-]").count() == 0


def test_template_keeps_one_client_and_mounts_the_acceptance_module_in_the_clinic_block():
    src = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
    assert src.count("createClinicApiClient()") == 1
    block = src.split("{% if clinic_mode %}\n    <!-- Clinic administration panels", 1)[1]
    assert "js/clinic/acceptance-policy-ui.js" in block and "clinic-policy-panel-" in block
    assert "acceptance-policy-ui.js" not in src.split("{% if clinic_mode %}\n    <!-- Clinic administration panels", 1)[0]
    assert re.search(r"id: 'acceptance', label: 'Abnahme'.*probeVia: adminClient", block)
