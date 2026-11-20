"""Fixtures of the V8 browser acceptance harness: one seeded app process, one browser, role logins."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest

import v8_browser_support as support

def pytest_terminal_summary(terminalreporter):
    if not support.FINDINGS:
        return
    terminalreporter.section("V8 browser findings (report only; V8_STRICT=1 turns them into failures)")
    for item in support.FINDINGS:
        terminalreporter.write_line(f"{item['case']}: " + ", ".join(f"{k}={v}" for k, v in item["counts"].items()))
    terminalreporter.write_line(f"screenshots and JSON results: {support.OUT_DIR}")


@pytest.fixture(scope="session")
def strict() -> bool:
    return os.environ.get("V8_STRICT") == "1"


@pytest.fixture(scope="session")
def commit() -> str:
    return support.build_commit()


@pytest.fixture(scope="session")
def axe_js():
    path = support.axe_source_path()
    if path is None:
        return None  # the axe check is reported as skipped with a reason; nothing is downloaded
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def app_base(tmp_path_factory):
    root = support.ROOT
    if not (root / "static/vendor").is_dir() or not (root / "static/css/tailwind.css").is_file():
        raise pytest.skip.Exception("static/vendor or static/css/tailwind.css missing: see tests/clinic/browser/README.md")
    tmp = tmp_path_factory.mktemp("v860")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp / "server.log"  # a file, not a pipe: nobody drains a pipe while the browser runs
    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "-c", support.SERVER, str(port), support.PASSWORD,
                                 json.dumps(support.ROLES)], cwd=root, env=support.server_env(tmp),
                                stdout=out, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 180
            while "READY" not in log.read_text():
                assert proc.poll() is None, "server exited before it was ready: " + log.read_text()[-3000:]
                assert time.time() < deadline, "server start timed out"
                time.sleep(0.2)
            yield f"http://127.0.0.1:{port}"
        finally:
            proc.terminate()
            proc.wait(timeout=20)


# Module scope: a sync Playwright left running for the session makes every later
# sync_playwright() in the same process fail ("Sync API inside the asyncio loop").
@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE: a Chromium build other than the one Playwright expects.
        instance = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="module")
def role_states(browser, app_base):
    """Storage state per role, logged in once through the real login form."""
    return {role: support.login(browser, app_base, email) for role, email in support.ROLES.items()}
