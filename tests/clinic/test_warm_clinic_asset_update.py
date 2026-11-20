"""A normal reload in a controlled clinic tab must use the next deployed UI."""
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright

from test_wp_mount_admin import ADMIN_EMAIL, LEAD_EMAIL, PASSWORD, ROOT, SERVER, _env


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


@contextmanager
def _profile(playwright, folder):
    context = playwright.chromium.launch_persistent_context(
        str(folder), executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
    )
    try:
        yield context
    finally:
        context.close()


def _sign_in(index, base):
    index.goto(base + "/")
    if "/login" in index.url:
        index.fill("input[name=email]", ADMIN_EMAIL)
        index.fill("input[name=password]", PASSWORD)
        index.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
        index.wait_for_load_state("load")
    assert "/login" not in index.url


def _assert_static_cache_only(page):
    assert page.evaluate("""async () => {
        const snapshots = await Promise.all((await caches.keys()).map(async key => (await (await caches.open(key)).keys())));
        return snapshots.flat().every(request => new URL(request.url).pathname.startsWith('/static/'));
    }""")


def _assert_offline_pages(index, admin, base):
    # The HTTP server has stopped. Only the actual worker can answer these navigations.
    assert index.evaluate("window.testShellBuild") == admin.evaluate("window.testAdminBuild") == "new"
    for page, path in ((index, "/"), (admin, "/admin")):
        assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--test-build').trim()") == "new"
        response = page.goto(base + path, wait_until="domcontentloaded")
        assert response.status == 503
        assert page.title() == "Offline - Speakr"
        _assert_static_cache_only(page)


@pytest.mark.parametrize("legacy", [True, False], ids=["from-product-version", "from-fingerprinted-build"])
def test_same_release_updates_previously_cached_shell_css_and_admin(tmp_path, legacy):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    profile = tmp_path / "profile"
    css = "() => getComputedStyle(document.documentElement).getPropertyValue('--test-build').trim()"
    with sync_playwright() as playwright:
        with _profile(playwright, profile) as context:
            index = context.pages[0]
            with _deployment(tmp_path / "old", port, "old", legacy=legacy):
                _sign_in(index, base)
                index.wait_for_function("() => navigator.serviceWorker.controller")
                index.reload()
                index.wait_for_function("() => window.testShellBuild === 'old'")
                assert index.evaluate(css) == "old"
                admin = context.new_page()
                admin.goto(base + "/admin")
                admin.wait_for_function("() => window.testAdminBuild === 'old'")
                assert admin.evaluate(css) == "old"
                index.wait_for_function("""async () => {
                    const cache = await caches.open((await caches.keys()).find(key => key.startsWith('speakr-static-')));
                    const paths = (await cache.keys()).map(request => new URL(request.url).pathname);
                    return ['/static/css/clinic-v8.css', '/static/js/app.modular.js', '/static/js/clinic/accounts-ui.js']
                        .every(path => paths.includes(path));
                }""")
                index.wait_for_load_state("networkidle")
                admin.wait_for_load_state("networkidle")
                [old_cache] = index.evaluate("() => caches.keys()")
                old_controller = index.evaluate("navigator.serviceWorker.controller.scriptURL")

            with _deployment(tmp_path / "new", port, "new"):
                admin.reload()
                index.reload()
                admin.wait_for_function("() => window.testAdminBuild === 'new'")
                index.wait_for_function("() => window.testShellBuild === 'new'")
                assert admin.evaluate(css) == index.evaluate(css) == "new"
                version = index.locator("meta[name=app-version]").get_attribute("content")
                cache = "speakr-static-" + version
                assert version.startswith((ROOT / "VERSION").read_text().strip() + "-")
                assert old_cache != cache
                # The installed update may wait for both clients to exit.
                index.wait_for_function("""async version => {
                    const r = await navigator.serviceWorker.getRegistration();
                    return r?.waiting?.state === 'installed' && new URL(r.waiting.scriptURL).searchParams.get('v') === version;
                }""", arg=version)
                assert all(tab.evaluate("navigator.serviceWorker.controller.scriptURL") == old_controller for tab in (index, admin))
            _assert_offline_pages(index, admin, base)

        # Last old client has exited. Reopen the same persistent profile, preserving the registration,
        # authentication state and Cache Storage. No unregister, forced reload or cache clearing.
        with _profile(playwright, profile) as context:
            index = context.pages[0]
            with _deployment(tmp_path / "reconnected", port, "new"):
                _sign_in(index, base)
                index.wait_for_function("""async version => {
                    const r = await navigator.serviceWorker.getRegistration();
                    return r?.active && navigator.serviceWorker.controller?.scriptURL === r.active.scriptURL &&
                        new URL(r.active.scriptURL).searchParams.get('v') === version;
                }""", arg=version)
                index.reload()
                admin = context.new_page()
                admin.goto(base + "/admin")
                admin.wait_for_function("() => window.testAdminBuild === 'new'")
                index.wait_for_function("() => window.testShellBuild === 'new'")
                assert admin.evaluate(css) == index.evaluate(css) == "new"
                assert index.evaluate("() => caches.keys()") == [cache]
                sheet = index.locator('link[href*="/clinic-v8.css?"]').first.get_attribute("href")
                index.wait_for_function("async url => Boolean(await caches.match(url))", arg=sheet)
            for tab in (index, admin):
                assert "--test-build: new" in tab.evaluate("async url => (await fetch(url)).text()", sheet)
            _assert_offline_pages(index, admin, base)
