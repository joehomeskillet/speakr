"""Support code of the V8 browser acceptance harness (v8-60).

Everything that is not a test lives here: the synthetic seed of the throwaway app, the page
registry P01..P21, the viewport and theme matrix, login, axe-core and screenshot helpers.
Synthetic data only; no patient data, no private fixtures.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = Path(os.environ.get("V8_BROWSER_OUT") or Path(__file__).resolve().parent / "out")
# Findings of the whole run (axe violations, overflow, browser diagnostics), printed at the end.
FINDINGS: list[dict] = []
PASSWORD = "Synthetic-pass-v860!"
LOGIN_TIMEOUT_MS = 30000
SHOW_TIMEOUT_MS = 30000

# Fixed matrix of the V8 test plan (V8-GATE-21): three sizes, light and dark.
VIEWPORTS = {"1440x900": (1440, 900), "1280x720": (1280, 720), "390x844": (390, 844)}
THEMES = ("light", "dark")
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

# Synthetic roles. "lead" is an administrator of the typists' staff pool group (a team admin who
# is not a site admin); "typist" holds the pool grant play/edit/release, so the correction view opens.
ROLES = {
    "author": "author-v860@local.test",
    "typist": "typist-v860@local.test",
    "lead": "lead-v860@local.test",
    "admin": "admin-v860@local.test",
}

SERVER = r'''
import json, sys, uuid
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity, pools, workflow
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.revisions import add_asr_revision
from src.init_db import initialize_database
from src.models import Group, GroupMembership, Recording, User

port, password, emails = int(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])


def user(name, email, admin=False):
    row = User(username=name, email=email, password=identity.hash_password(password), is_admin=admin)
    db.session.add(row)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only"))
    return row


with app.app_context():
    initialize_database(app)
    db.create_all()
    admin = user("admin-v860", emails["admin"], admin=True)
    author, typist, lead = (user("author-v860", emails["author"]), user("typist-v860", emails["typist"]),
                            user("lead-v860", emails["lead"]))
    pool = ClinicAuthorPool(name="Autoren V860", created_by=admin.id)
    group = Group(name="Schreibdienst V860", created_by=admin.id)
    db.session.add_all([pool, group])
    db.session.flush()
    staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add(staff)
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff.id, created_by=admin.id))
    db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="member"))
    db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))
    db.session.commit()
    pools.add_author_member(pool.id, author.id, actor_id=admin.id)
    pools.allow(typist.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("play", "edit", "release"))
    rec = Recording(user_id=author.id, title="Synthetisch", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec)
    db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool.id, version_counter=0)
    db.session.add(dic)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dic.id, content_hash="o" * 64, size_bytes=1000,
                              storage_key="orig/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:synthetic")
    db.session.add(original)
    db.session.flush()
    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")
    init_audio_revision(dic, original, 4000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=author.id, pool_id=pool.id, assignee_user_id=typist.id,
        status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
server = make_server("127.0.0.1", port, app, threaded=True)
print("READY", flush=True)
server.serve_forever()
'''


def server_env(tmp: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp / 'v860.db'}",
           "UPLOAD_FOLDER": str(tmp / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false", "CLINIC_MODE": "1"}
    return env


def build_commit() -> str:
    """Short commit hash of the checked-out build; "-dirty" when tracked files are modified."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=30).stdout.strip()

    commit = git("rev-parse", "--short", "HEAD") or "nocommit"
    return commit + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def axe_source_path() -> Path | None:
    """Local axe-core build, never downloaded: $AXE_CORE_JS, project node_modules, global pa11y copy."""
    candidates = [os.environ.get("AXE_CORE_JS"), ROOT / "node_modules/axe-core/axe.min.js",
                  "/usr/lib/node_modules/pa11y/node_modules/axe-core/axe.min.js",
                  "/usr/local/lib/node_modules/axe-core/axe.min.js", "/usr/lib/node_modules/axe-core/axe.min.js"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


# --- login -------------------------------------------------------------------------------------

def login(browser, base: str, email: str) -> dict:
    """Log in through the real login form and return the Playwright storage state."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        page.goto(base + "/login")
        page.fill("input[name=email]", email)
        page.fill("input[name=password]", PASSWORD)
        page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
        page.wait_for_load_state("load")
        assert "/login" not in page.url, f"login failed for {email}"
        return context.storage_state()
    finally:
        context.close()


# --- page preparation: one function per mounted view -------------------------------------------

def _wait_loading_overlay_gone(page) -> None:
    """The app covers the page with a loading overlay until it has mounted."""
    page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=SHOW_TIMEOUT_MS)


def _show_worklist(page, base: str) -> None:
    page.goto(base + "/")
    _wait_loading_overlay_gone(page)
    page.locator("[data-testid=clinic-worklist-view]").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


def _click_noting(page, locator, label: str) -> None:
    """Real click; when another element intercepts it (a layout finding), note that and dispatch the click."""
    try:
        locator.click(timeout=5000)
    except Exception as exc:  # playwright TimeoutError: the target is covered or not actionable
        page.v8_notes.append(f"click on {label} intercepted: {str(exc).splitlines()[-1][:160]}")
        locator.dispatch_event("click")


def _show_correction(page, base: str) -> None:
    _show_worklist(page, base)
    row = page.locator("[data-row]").first
    row.wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)
    _click_noting(page, row, "worklist row")
    _click_noting(page, page.locator("[data-testid=clinic-worklist-view]").get_by_role("button", name="Öffnen", exact=True),
                  "button Öffnen")
    page.locator("[data-testid=clinic-correction-view]").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)
    page.locator("[data-testid=text]").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


def _show_resources(page, base: str) -> None:
    page.goto(base + "/")
    _wait_loading_overlay_gone(page)
    nav = page.locator("[data-testid=nav-clinic-resources]")
    nav.wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)
    _click_noting(page, nav, "nav entry Ressourcen")
    page.locator(".clinic-resources-view").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


def _open_admin(page, base: str) -> None:
    page.goto(base + "/admin")
    page.locator("[data-testid=admin-tab-clinic]").click()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


def _show_admin_accounts(page, base: str) -> None:
    _open_admin(page, base)
    page.locator("#acc-title").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


def _show_admin_pools(page, base: str) -> None:
    _open_admin(page, base)
    page.locator("[data-testid=clinic-panel-pools]").click()
    page.locator("[data-testid=clinic-view-pools]").wait_for(state="visible", timeout=SHOW_TIMEOUT_MS)


# --- page registry -----------------------------------------------------------------------------

@dataclass(frozen=True)
class PageEntry:
    """One V8 page. "mounted" pages have a "show" function and the roles that reach them."""

    page_id: str
    title: str
    view: str = ""
    roles: tuple[str, ...] = ()
    show: Callable | None = None

    @property
    def mounted(self) -> bool:
        return self.show is not None


# For the mounted entries "view" names the clinic view that carries the page's function at
# clinic/main. That is a statement about the mount, not about conformity to the V8 page spec.
PAGES: tuple[PageEntry, ...] = (
    PageEntry("P01", "Aufnahmen"),
    PageEntry("P02", "Arbeitsliste", "worklist", ("author", "typist", "lead", "admin"), _show_worklist),
    PageEntry("P03", "Diktatdetails"),
    PageEntry("P04", "Neue Aufnahme"),
    PageEntry("P05", "Live-Diktat"),
    PageEntry("P06", "Schreibplatz", "correction", ("typist",), _show_correction),
    PageEntry("P07", "Prüfen und Freigeben"),
    PageEntry("P08", "Archiv"),
    PageEntry("P09", "Dateien und Geräteimport"),
    PageEntry("P10", "Schreibkräfte-Pools", "admin/pools", ("admin",), _show_admin_pools),
    PageEntry("P11", "Diktierende und Autorenpools"),
    PageEntry("P12", "Benutzer und Rollen", "admin/accounts", ("admin",), _show_admin_accounts),
    PageEntry("P13", "Wortschatz", "resources", ("author", "typist", "lead", "admin"), _show_resources),
    PageEntry("P14", "Sprachbefehle und Vorlagen"),
    PageEntry("P15", "ASR und Sprachprofile"),
    PageEntry("P16", "Ausgabe und Integrationen"),
    PageEntry("P17", "Verarbeitung"),
    PageEntry("P18", "Aktivität und Audit"),
    PageEntry("P19", "Einstellungen"),
    PageEntry("P20", "Anmeldung und Zugriff"),
    PageEntry("P21", "Offline und Wiederherstellung"),
)


# --- checks ------------------------------------------------------------------------------------

def theme_context_options(theme: str) -> dict:
    return {"color_scheme": theme, "locale": "de-CH", "timezone_id": "Europe/Zurich", "reduced_motion": "reduce"}


def theme_init_script(theme: str) -> str:
    """The app keeps the choice in localStorage 'darkMode' and sets the 'dark' class on <html>."""
    return f"try {{ localStorage.setItem('darkMode', '{'true' if theme == 'dark' else 'false'}'); }} catch (e) {{}}"


def run_axe(page, axe_js: str) -> dict:
    # evaluate, not a script tag: a page content security policy cannot block it
    page.evaluate(axe_js)
    return page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations', 'incomplete']})",
        AXE_TAGS)


def horizontal_overflow(page) -> dict:
    return page.evaluate(
        "() => ({document: document.documentElement.scrollWidth > innerWidth + 1,"
        " body: document.body.scrollWidth > innerWidth + 1,"
        " scrollWidth: document.documentElement.scrollWidth, innerWidth: innerWidth})")


def write_result(stem: str, payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{stem}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
