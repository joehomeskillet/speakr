"""Request coverage for the main page served by the real Flask app in both modes."""

import subprocess
import sys
import time
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from test_wp_mount_correction import PASSWORD, ROOT, _env, _launch


SERVER = """
import sys
from werkzeug.serving import make_server
from src.app import app, bcrypt, db
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.init_db import initialize_database
from src.models import SystemSetting, User

with app.app_context():
    initialize_database(app)
    user = User(username='boot-test', email='boot@local.test',
                password=bcrypt.generate_password_hash(sys.argv[1]).decode())
    db.session.add(user)
    db.session.flush()
    if clinic_mode_enabled():
        db.session.add(ClinicAccountPolicy(user_id=user.id, status='active', login_policy='local_only'))
    db.session.commit()
    SystemSetting.set_setting('enable_folders', True, setting_type='boolean')

server = make_server('127.0.0.1', 0, app, threaded=True)
print(f'READY {server.server_port}', flush=True)
server.serve_forever()
"""

BOOT_PATHS = {"/api/recordings", "/api/folders", "/speakers", "/api/recordings/job-queue-status"}
CHANGES_PATH = "/api/v1/recordings/changes"
ROWS_PATH = "/api/clinic/v1/worklist/rows"
KEPT_PATHS = {"/api/tags", "/api/user/token-budget"}


@pytest.fixture(params=[True, False], ids=["clinic", "upstream"])
def boot_server(tmp_path, request):
    clinic = request.param
    env = _env(tmp_path, clinic)
    env.update(SPEAKR_SKIP_BACKGROUND_TASKS="true", CLINIC_ORIGINALS_ROOT=str(tmp_path / "originals"))
    log = tmp_path / "server.log"
    with log.open("w") as output, subprocess.Popen(
        [sys.executable, "-c", SERVER, PASSWORD], cwd=ROOT, env=env,
        stdout=output, stderr=subprocess.STDOUT,
    ) as process:
        try:
            deadline = time.monotonic() + 60
            while True:
                lines = log.read_text().splitlines()
                ready = next((line for line in lines if line.startswith("READY ")), None)
                if ready:
                    break
                assert process.poll() is None and time.monotonic() < deadline, "\n".join(lines[-20:])
                time.sleep(0.1)
            yield f"http://127.0.0.1:{ready.split()[1]}", clinic
        finally:
            process.terminate()
            process.wait(timeout=20)


def test_main_page_boot_requests(boot_server):
    base, clinic = boot_server
    with sync_playwright() as playwright, _launch(playwright) as browser:
        page = browser.new_page()
        page.clock.install()
        page.goto(base + "/login")
        page.evaluate("localStorage.setItem('selectedFolder', '42')")
        page.fill("input[name=email]", "boot@local.test")
        page.fill("input[name=password]", PASSWORD)
        requests, responses = [], {}
        page.on("request", lambda request: requests.append(urlsplit(request.url).path))
        page.on("response", lambda response: responses.update({urlsplit(response.url).path: response.status}))
        page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
        page.wait_for_url(base + "/**")
        page.wait_for_function("document.querySelector('#app')?.style.opacity === '1'")
        assert page.locator("#app").get_attribute("data-clinic-mode") == str(clinic)
        assert {path: responses.get(path) for path in KEPT_PATHS} == dict.fromkeys(KEPT_PATHS, 200)

        if not clinic:
            assert BOOT_PATHS <= set(requests)
            return

        page.get_by_test_id("clinic-recordings-view").wait_for(state="visible")
        assert not set(requests) & BOOT_PATHS, requests
        page.get_by_test_id("nav-clinic-worklist").click()
        page.wait_for_function("document.querySelector('[data-testid=last-update]')?.textContent.includes('Zuletzt aktualisiert:')")
        assert CHANGES_PATH not in requests

        with page.expect_response(lambda response: urlsplit(response.url).path == ROWS_PATH) as refresh:
            page.clock.fast_forward(31_000)
        assert refresh.value.status == 200
        assert not set(requests) & (BOOT_PATHS | {CHANGES_PATH}), requests
