"""V8 browser acceptance (UI8-008): P01–P21 per authorized role, viewport and theme.

Hard failures: the view does not show, an uncaught JavaScript exception, the theme is not applied.
Findings (axe violations, horizontal overflow, console errors, failed requests) are written to the
result JSON and summarised at the end of the run; V8_STRICT=1 turns them into failures.
Screenshots: <out>/<build commit>__<page>_<view>_<role>_<viewport>_<theme>.png
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

import v8_browser_support as support

MOUNTED = [(p, role, size, theme) for p in support.PAGES if p.mounted
           for role in p.roles for size in support.VIEWPORTS for theme in support.THEMES]


@pytest.fixture(scope="module")
def app_base(tmp_path_factory):
    source = support.SERVER.replace("server = make_server", support.navigation.MATRIX_SEED + "\nserver = make_server")
    source = ("import os\nos.environ['SPEAKR_SKIP_BACKGROUND_TASKS'] = 'true'\n"
              "os.environ['TRANSCRIPTION_BASE_URL'] = 'http://127.0.0.1:9/v1'\n" + source)
    with support.serve_app(tmp_path_factory.mktemp("v8-matrix21"), source) as base:
        yield base


FIRMWARE_SEED = '''
with app.app_context():
    from datetime import datetime, timezone
    from src.clinic.agent_channel import ClinicAgent, ClinicAgentInventory
    from src.clinic.firmware_catalog import set_target

    set_target("LFH9600", "1.10", 0)
    set_target("LFH9620", "2.1", 0)
    agent = ClinicAgent(display_name="Test-Import", allowed_sources='["test-usb"]',
                        credential_fingerprint="ab" * 32)
    db.session.add(agent)
    db.session.flush()
    for source, device, version in (("Recorder 1", "LFH9600", "1.10.0"),
                                    ("Recorder 2", "LFH9620", "2.0"),
                                    ("Recorder 3", "LFH9600", None)):
        db.session.add(ClinicAgentInventory(
            agent_id=agent.id, source_id=source, root_id="test-usb", relative_path="test.dss",
            size=1000, mtime_utc=datetime(2026, 11, 20, tzinfo=timezone.utc), sha256="c" * 64,
            snapshot_version="1.10", device_type=device, firmware_version=version))
    db.session.commit()
'''


@pytest.fixture(scope="module", params=("rows", "empty"))
def firmware_base(request, tmp_path_factory, app_base):
    label = request.param
    if label == "empty":
        yield label, app_base
        return
    source = support.SERVER.replace("server = make_server", FIRMWARE_SEED + "\nserver = make_server")
    with support.serve_app(tmp_path_factory.mktemp("p168-firmware"), source) as base:
        yield label, base


@pytest.mark.parametrize("size", support.VIEWPORTS)
@pytest.mark.parametrize("theme", support.THEMES)
def test_p09_firmware(browser, firmware_base, size, theme, axe_js):
    label, base = firmware_base
    width, height = support.VIEWPORTS[size]
    state = support.login(browser, base, support.ROLES["admin"])
    with browser.new_context(viewport={"width": width, "height": height}, storage_state=state,
                             **support.theme_context_options(theme)) as context:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        errors, firmware_requests = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda req: firmware_requests.append((req.method, req.url))
                if "/firmware" in req.url else None)
        support._open_shell_view(page, base, "#/import")
        panel = page.get_by_test_id("firmware-panel")
        expect(panel.get_by_test_id("firmware-blocked")).to_have_text("Firmware-Flash gesperrt (WG-IMP-010)")
        if label == "rows":
            catalog = panel.get_by_test_id("firmware-catalog")
            expect(catalog.get_by_role("row", name="LFH9600 1.10")).to_be_visible()
            expect(catalog.get_by_role("row", name="LFH9620 2.1")).to_be_visible()
            comparison = panel.get_by_test_id("firmware-comparison")
            expect(comparison.get_by_role("row", name="Recorder 1 LFH9600 Gemeldet: 1.10.0 Im Katalog: 1.10 Aktuell")).to_be_visible()
            expect(comparison.get_by_role("row", name="Recorder 2 LFH9620 Gemeldet: 2.0 Im Katalog: 2.1 Älter")).to_be_visible()
            expect(comparison.get_by_role("row", name="Recorder 3 LFH9600 Gemeldet: Keine Angabe Im Katalog: 1.10 Unbekannt")).to_be_visible()
        else:
            expect(panel.get_by_test_id("firmware-catalog-empty")).to_be_visible()
            expect(panel.get_by_test_id("firmware-reports-empty")).to_be_visible()
        expect(panel.get_by_role("button")).to_have_count(0)
        assert firmware_requests == [("GET", base + "/api/clinic/v1/imports/firmware")]
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
        assert panel.locator(".table-wrap").evaluate_all("els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")
        assert not errors, errors
        if axe_js:
            page.evaluate(axe_js)
            violations = panel.evaluate("(el, tags) => axe.run(el, {runOnly: {type: 'tag', values: tags}})",
                                        support.AXE_TAGS)["violations"]
            assert not violations, violations
        if output := os.environ.get("P168_ARTIFACTS"):
            out = Path(output)
            out.mkdir(parents=True, exist_ok=True)
            stem = out / f"p09-{label}-{size}-{theme}"
            panel.scroll_into_view_if_needed()
            page.screenshot(path=stem.with_suffix(".png"), full_page=True, animations="disabled")
            stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        if size == "390x844":
            page.set_viewport_size({"width": 320, "height": height})
            assert panel.locator(".table-wrap").evaluate_all(
                "els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")


def _case_id(case) -> str:
    page, role, size, theme = case
    return f"{page.page_id}-{page.view.replace('/', '_')}-{role}-{size}-{theme}"


@pytest.mark.parametrize("case", MOUNTED, ids=_case_id)
def test_mounted_page(case, browser, app_base, role_states, axe_js, commit, strict):
    page_entry, role, size, theme = case
    width, height = support.VIEWPORTS[size]
    stem = f"{commit}__{_case_id(case)}"
    state = {"cookies": [], "origins": []} if role == "anonymous" else role_states[role]
    context = browser.new_context(viewport={"width": width, "height": height}, storage_state=state,
                                  **support.theme_context_options(theme))
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        page.v8_notes = []  # layout findings of the page preparation
        js_errors: list[str] = []
        diagnostics: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.on("console", lambda m: diagnostics.append(f"console: {m.text}") if m.type == "error" else None)
        page.on("response", lambda r: diagnostics.append(f"{r.status} {r.url}")
                if r.status >= 400 and not r.url.endswith("/favicon.ico") else None)

        page_entry.show(page, app_base)
        page.wait_for_load_state("networkidle")

        # Theme: the page must carry the requested mode, not just the browser preference.
        is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
        assert is_dark == (theme == "dark"), f"theme {theme} not applied (html.dark={is_dark})"

        support.OUT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(support.OUT_DIR / f"{stem}.png"), full_page=True, animations="disabled", caret="hide")

        diagnostics.extend(page.v8_notes)
        overflow = support.horizontal_overflow(page)
        axe = support.run_axe(page, axe_js) if axe_js else None
        violations = axe["violations"] if axe else []
        counts = {"axe_violations": len(violations), "overflow": int(overflow["document"] or overflow["body"]),
                  "diagnostics": len(diagnostics), "js_errors": len(js_errors)}
        support.write_result(stem, {
            "page": page_entry.page_id, "view": page_entry.view, "role": role, "viewport": size, "theme": theme,
            "commit": commit, "axe": "not run: no local axe-core" if axe is None else axe,
            "url": page.url, "dictation_id": getattr(page, "v8_dictation_id", None),
            "overflow": overflow, "diagnostics": diagnostics, "js_errors": js_errors})
        if any(counts.values()):
            support.FINDINGS.append({"case": _case_id(case), "counts": {k: v for k, v in counts.items() if v}})

        assert not js_errors, js_errors  # hard: uncaught exceptions
        if strict:
            assert not violations, [v["id"] for v in violations]
            assert not (overflow["document"] or overflow["body"]), overflow
            assert not diagnostics, diagnostics
    except Exception as exc:
        support.OUT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(support.OUT_DIR / f"{stem}__failure.png"), full_page=True, animations="disabled")
        support.write_result(stem + "__failure", {
            "page": page_entry.page_id, "view": page_entry.view, "role": role, "viewport": size, "theme": theme,
            "url": page.url, "error": str(exc), "diagnostics": diagnostics, "js_errors": js_errors})
        raise
    finally:
        context.close()


def test_all_v8_pages_are_active():
    assert [p.page_id for p in support.PAGES if p.mounted] == [f"P{n:02d}" for n in range(1, 22)]
    pages = {p.page_id: p for p in support.PAGES}
    for page_id in ("P01", "P02", "P13"):
        assert pages[page_id].roles == ("author", "typist", "lead", "admin")
    assert pages["P06"].roles == ("typist",)
    assert pages["P10"].roles == pages["P12"].roles == ("admin",)
    assert pages["P20"].roles == ("anonymous",)
    assert len(MOUNTED) == 180


def test_axe_core_available(axe_js):
    if axe_js is None:
        raise pytest.skip.Exception("no local axe-core (set AXE_CORE_JS or install axe-core in node_modules); nothing is downloaded")
