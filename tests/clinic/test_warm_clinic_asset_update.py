"""A normal reload in a controlled clinic tab must use the next deployed UI."""
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from test_wp_mount_admin import ADMIN_EMAIL, LEAD_EMAIL, PASSWORD, ROOT, SERVER, _env, _login, browser


@contextmanager
def _deployment(folder, port, marker, *, legacy=False):
    folder.mkdir()
    assets = folder / "static"
    shutil.copytree(ROOT / "static", assets, symlinks=True)
    for name, extra in {
        "css/clinic-v8.css": f":root {{ --test-build: {marker}; }}",
        "js/app.modular.js": f"window.testShellBuild = '{marker}';",
        "js/clinic/accounts-ui.js": f"window.testAdminBuild = '{marker}';",
    }.items():
        with (assets / name).open("a") as output:
            output.write("\n" + extra + "\n")

    setup = "app.static_folder = sys.argv[5]\n"
    if legacy:
        # Previously shipped templates used the product release for every static URL.
        setup += ("from src.config.version import get_version\n"
                  "app.context_processor(lambda: dict(app_version=get_version()))\n")
    script = SERVER.replace("port = int(sys.argv[1])", setup + "port = int(sys.argv[1])")
    env = {**_env(folder, True), "SECRET_KEY": "synthetic-warm-reload-test-only"}
    log = folder / "server.log"
    with log.open("w") as output:
        proc = subprocess.Popen([sys.executable, "-c", script, str(port), PASSWORD, ADMIN_EMAIL, LEAD_EMAIL,
                                 str(assets)], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 120
            while "READY " not in log.read_text():
                assert proc.poll() is None, "test deployment exited during startup"
                assert time.monotonic() < deadline, "test deployment startup timed out"
                time.sleep(.15)
            yield
        finally:
            proc.terminate()
            proc.wait(timeout=20)


@pytest.mark.parametrize("legacy", [True, False], ids=["from-product-version", "from-fingerprinted-build"])
def test_same_release_updates_previously_cached_shell_css_and_admin(browser, tmp_path, legacy):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    css = "() => getComputedStyle(document.documentElement).getPropertyValue('--test-build').trim()"
    with _deployment(tmp_path / "old", port, "old", legacy=legacy):
        index, _ = _login(browser, base, ADMIN_EMAIL)
        index.wait_for_function("() => navigator.serviceWorker.controller")
        index.reload()
        index.wait_for_function("() => window.testShellBuild === 'old'")
        assert index.evaluate(css) == "old"
        admin = index.context.new_page()
        admin.goto(base + "/admin")
        admin.wait_for_function("() => window.testAdminBuild === 'old'")
        assert admin.evaluate(css) == "old"
        # Verify that the incumbent SW really cached all three responses before replacing the server.
        index.wait_for_function("""async () => {
            const cache = await caches.open((await caches.keys()).find(key => key.startsWith('speakr-static-')));
            const paths = (await cache.keys()).map(request => new URL(request.url).pathname);
            return ['/static/css/clinic-v8.css', '/static/js/app.modular.js', '/static/js/clinic/accounts-ui.js']
                .every(path => paths.includes(path));
        }""")
        old_worker = index.evaluate("navigator.serviceWorker.controller.scriptURL")

    with _deployment(tmp_path / "new", port, "new"):
        admin.reload()
        assert admin.evaluate(css) == "new"
        admin.wait_for_function("() => window.testAdminBuild === 'new'")
        admin.wait_for_function("old => navigator.serviceWorker.controller.scriptURL !== old",
                                arg=old_worker, polling=100)
        index.reload()
        assert index.evaluate(css) == "new"
        index.wait_for_function("() => window.testShellBuild === 'new'")
        version = index.locator("meta[name=app-version]").get_attribute("content")
        assert version.startswith((ROOT / "VERSION").read_text().strip() + "-")
        cache = "speakr-static-" + version
        assert index.evaluate("() => caches.keys()") == [cache]
        assert index.evaluate("""async name => (await (await caches.open(name)).keys())
            .every(request => new URL(request.url).pathname.startsWith("/static/"))""", cache)
        sheet = index.locator("link[href*=\"/clinic-v8.css?\"]").first.get_attribute("href")
        index.wait_for_function("async url => Boolean(await caches.match(url))", arg=sheet, polling=100)
        index.context.set_offline(True)
        assert "--test-build: new" in index.evaluate("async url => (await fetch(url)).text()", sheet)
        index.context.close()
