"""Capture synthetic persona views on Speakr staging with Playwright."""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

STAGING_HOST = "speakr.joelduss.xyz"
STAGING_ORIGIN = f"https://{STAGING_HOST}"
DEFAULT_CREDENTIALS_DIR = Path("/root/.config/speakr-staging")
VIEWPORTS = ((390, 844), (1440, 900))
TYPISTS = {"schreibkraft", "schreibkraft_ambulant", "leitung_schreibdienst"}
VIEWS = {"#/neu": "clinic-recording", "#/aufnahmen": "clinic-recordings", "#/arbeitsliste": "clinic-worklist"}


class CaptureError(Exception):
    """A safe-to-display capture error."""


@dataclass(repr=False, frozen=True)
class Credentials:
    username: str
    email: str | None
    password: str
    url: str


def read_credentials(path: Path) -> Credentials:
    values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    url = values.get("url", "")
    if url not in {STAGING_ORIGIN, STAGING_ORIGIN + "/"}:
        raise CaptureError("credential URL must match Speakr staging")
    if not values.get("username", "").startswith("p-") or not values.get("password"):
        raise CaptureError("synthetic persona credentials required")
    return Credentials(values["username"], values.get("email") or None, values["password"], url.rstrip("/"))


def validate_routes(routes: list[str]) -> list[str]:
    if not routes or any(not re.fullmatch(r"#/(?:[a-z0-9-]+/)*[a-z0-9-]+", route) for route in routes):
        raise CaptureError("own-view requires local hash routes such as #/neu")
    return routes


def routes_for(pid: str, routes: list[str]) -> list[str]:
    return list(dict.fromkeys(
        "#/arbeitsliste" if pid in TYPISTS and route in {"#/neu", "#/aufnahmen"} else route for route in routes
    ))


def _staging_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc == STAGING_HOST


def _staging_only(route: "Route") -> None:
    if _staging_url(route.request.url):
        route.continue_()
    else:
        route.abort()


class LoginRateLimited(CaptureError):
    """The staging login limiter asked the panel to wait."""


def _sign_in(page: "Page", credentials: Credentials) -> None:
    login = f"{credentials.url}/login"
    response = page.goto(login)
    if response and response.status == 429:
        raise LoginRateLimited("staging login rate limit reached")
    form = page.locator("form:has(input[name=password]):visible")
    if page.url != login or form.evaluate("form => form.action") != login:
        raise CaptureError("unexpected staging login form")
    form.locator("input[name=email], input[name=username]").fill(credentials.email or credentials.username)
    form.locator("input[name=password]").fill(credentials.password)
    with page.expect_response(lambda response: response.url == login and response.request.method == "POST") as posted:
        form.locator("[type=submit]:visible").click()
    if posted.value.status == 429:
        raise LoginRateLimited("staging login rate limit reached")
    if not 300 <= posted.value.status < 400:
        raise CaptureError("staging rejected the persona login")
    page.wait_for_url(f"{credentials.url}/", wait_until="domcontentloaded")


def _screenshots(page: "Page", routes: list[str], out: Path, credentials: Credentials) -> list[Path]:
    masks = [page.get_by_text(value, exact=False) for value in (credentials.username, credentials.email) if value]
    masks.append(page.locator("input[type=password]"))
    paths = []
    for route in routes:
        target = f"{credentials.url}/{route}"
        page.goto(target)
        page.locator('#app[data-clinic-mode="True"]:not([v-cloak])').wait_for()
        view = page.get_by_test_id(f"{VIEWS[route]}-view") if route in VIEWS else page.locator("#app")
        view.wait_for()
        if page.url != target:
            raise CaptureError("requested staging view was not reached")
        for width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            page.locator('.app-loading-overlay:visible, html.app-loading, body.app-loading').first.wait_for(state="hidden")
            view.get_by_test_id("loading-state").wait_for(state="hidden")
            path = out / f"{route[2:].replace('/', '_')}-{width}x{height}.png"
            page.screenshot(path=path, mask=masks, animations="disabled")
            paths.append(path)
    return paths


def capture_views(ids: list[str], routes: list[str], out: Path) -> tuple[dict[str, list[Path]], dict[str, str]]:
    from playwright.sync_api import Error, sync_playwright

    validate_routes(routes)
    directory = Path(os.environ.get("PERSONA_PANEL_CREDENTIALS_DIR", DEFAULT_CREDENTIALS_DIR)).expanduser().resolve()
    accounts: dict[str, Credentials] = {}
    views: dict[str, list[Path]] = {}
    notes: dict[str, str] = {}
    for pid in ids:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", pid):
            raise CaptureError("invalid persona ID")
        try:
            accounts[pid] = read_credentials(directory / f"persona-{pid}.txt")
        except FileNotFoundError:
            notes[pid] = "skipped: no staging account"
        except (OSError, UnicodeError, CaptureError) as error:
            notes[pid] = f"capture failed: {error if isinstance(error, CaptureError) else type(error).__name__}"
    if not accounts:
        return views, notes
    try:
        with sync_playwright() as playwright:
            executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
            if not executable and not Path(playwright.chromium.executable_path).is_file():
                executable = which("chromium")
            browser = playwright.chromium.launch(
                args=["--no-sandbox"] if os.geteuid() == 0 else [], executable_path=executable or None,
            )
            try:
                for pid, credentials in accounts.items():
                    context = browser.new_context(service_workers="block")
                    try:
                        context.route("**/*", _staging_only)
                        page = context.new_page()
                        try:
                            _sign_in(page, credentials)
                        except LoginRateLimited:
                            time.sleep(60)
                            _sign_in(page, credentials)
                        folder = out / pid
                        folder.mkdir(parents=True)
                        views[pid] = _screenshots(page, routes_for(pid, routes), folder, credentials)
                    except (Error, CaptureError, OSError) as error:
                        notes[pid] = f"capture failed: {error if isinstance(error, CaptureError) else type(error).__name__}"
                    finally:
                        context.close()
            finally:
                browser.close()
    except Error as error:
        raise CaptureError(f"browser unavailable: {type(error).__name__}") from None
    return views, notes
