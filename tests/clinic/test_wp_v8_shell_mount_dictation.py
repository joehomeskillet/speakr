"""Mount of the V8 dictation views in the clinic shell (v8-70).

Details, Pruefen und Freigeben, Ausgabe, Verarbeitung and Verlauf are reached from an opened dictation
(page-level controls next to the back button), set the hash routes #/diktat/details, #/diktat/pruefen,
#/diktat/ausgabe, #/diktat/verarbeitung and #/diktat/verlauf, and close back to the tab the dictation was
opened from. Leaving the open correction editor with unsaved text saves first (the editor's own close).
With CLINIC_MODE off the page carries none of the new markup.

The real application runs in a child process with synthetic data only (see test_wp_mount_correction.py,
whose server, login and render helpers are reused). Browser cases need Playwright and its Chromium.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from test_wp_mount_correction import (  # noqa: F401,F811  (live_server is a fixture)
    TYPIST, WORD, _launch, _login, _open_from_worklist, _render, live_server)

ROOT = Path(__file__).resolve().parents[2]

# (link suffix, hash, selector of the module root, German page-level control label)
VIEWS = [
    ("detail", "#/diktat/details", "[data-testid=clinic-detail-view]", "Details"),
    ("review", "#/diktat/pruefen", "[data-testid=clinic-review-view]", "Prüfen und Freigeben"),
    ("output", "#/diktat/ausgabe", "[data-testid=output-view]", "Ausgabe"),
    ("jobs", "#/diktat/verarbeitung", "[data-testid=clinic-jobs-view]", "Verarbeitung"),
    ("activity", "#/diktat/verlauf", ".clinic-activity-view", "Verlauf"),
]


@pytest.mark.parametrize("view,hash_,root,label", VIEWS, ids=[v[0] for v in VIEWS])
def test_view_is_reachable_sets_hash_and_returns_to_origin(live_server, view, hash_, root, label):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, TYPIST)
            _open_from_worklist(page, base)
            link = page.locator(f"[data-testid=clinic-dictation-link-{view}]")
            assert link.inner_text().strip() == label

            link.click()
            page.locator(root).first.wait_for(state="visible", timeout=30000)
            assert page.locator("[data-testid=clinic-correction-view]").count() == 0
            assert page.evaluate("window.location.hash") == hash_
            # No patient data and no dictation id in the URL or the title (W16).
            assert dictation not in page.url
            assert dictation not in page.title()
            assert page.locator("[data-testid=clinic-dictation-mount]").count() == 1

            # Back returns to the tab the dictation was opened from (the worklist).
            back = page.locator("[data-testid=clinic-dictation-back]")
            assert back.inner_text().strip() == "Zurück zur Arbeitsliste"
            back.click()
            page.locator("[data-testid=clinic-worklist-view]").wait_for(state="visible", timeout=20000)
            assert page.locator("[data-testid=clinic-dictation-mount]").count() == 0
            assert page.evaluate("window.location.hash") == "#/arbeitsliste"
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_dictation_views_switch_among_each_other_and_back_into_the_editor(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, TYPIST)
            _open_from_worklist(page, base)
            page.locator("[data-testid=clinic-dictation-link-output]").click()
            page.locator("[data-testid=output-view]").wait_for(state="visible", timeout=30000)
            page.locator("[data-testid=clinic-dictation-link-review]").click()
            page.locator("[data-testid=clinic-review-view]").wait_for(state="visible", timeout=30000)
            assert page.evaluate("window.location.hash") == "#/diktat/pruefen"
            assert dictation in page.locator("[data-testid=clinic-review-view]").inner_text()

            # "Korrigieren" of the review reopens the editor; the origin tab is kept.
            page.locator("[data-testid=clinic-review-view] [data-testid=correct]").click()
            page.locator("[data-testid=clinic-correction-view]").wait_for(state="visible", timeout=30000)
            assert page.evaluate("window.location.hash") == "#/diktat"
            assert page.locator("[data-testid=clinic-correction-back]").inner_text().strip() == "Zurück zur Arbeitsliste"
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_leaving_the_editor_with_unsaved_text_saves_first(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, TYPIST)
            writes: list[tuple[float, str]] = []
            page.on("request", lambda r: writes.append((time.monotonic(), f"{r.method} {r.url}"))
                    if r.method != "GET" and f"/dictations/{dictation}/correction" in r.url else None)
            _open_from_worklist(page, base)
            text = page.locator("[data-testid=text]")
            text.wait_for(state="visible", timeout=30000)
            page.locator("[data-action=acquire]").click()
            page.wait_for_function("!document.querySelector('[data-testid=text]').readOnly", timeout=20000)
            text.click()
            text.press("End")
            text.type(" " + WORD)
            page.wait_for_function("document.querySelector('[data-testid=save-state]').dataset.state === 'unsaved'")
            assert not writes

            page.locator("[data-testid=clinic-dictation-link-detail]").click()
            page.locator("[data-testid=clinic-detail-view]").wait_for(state="visible", timeout=30000)
            shown = time.monotonic()
            assert writes, "the editor was left without a save request"
            assert writes[0][0] <= shown
            stored = page.evaluate(
                "fetch('/api/clinic/v1/dictations/%s/correction', {credentials: 'same-origin'}).then(r => r.json())" % dictation)
            assert stored["text"].endswith(WORD)
            assert page.evaluate("window.location.hash") == "#/diktat/details"
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_clinic_page_declares_the_dictation_views(tmp_path):
    out = _render(tmp_path, True)
    assert "V8 dictation views (v8-70)" in out
    for tag in ("clinic-detail-view", "clinic-review-view", "clinic-output-view", "clinic-jobs-view", "clinic-activity-view"):
        assert f"<{tag}" in out, tag
    assert "data-testid=\"clinic-dictation-back\"" in out


def test_clinic_mode_off_page_has_none_of_the_markup(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    for marker in ("V8 dictation views", "clinic-dictation", "clinic-detail-view", "clinic-review-view",
                   "clinic-output-view", "clinic-jobs-view", "clinic-activity-view", "#/diktat"):
        assert marker not in out, marker


def test_import_graph_is_clinic_gated_and_dynamic():
    src = (ROOT / "static/js/app.modular.js").read_text(encoding="utf-8")
    assert not re.search(r"^import .*clinic/", src, re.M), "clinic code must not be a static import"
    for module in ("detail", "review", "output", "jobs", "activity"):
        assert f"import('./clinic/compose-{module}.js')" in src
    assert "registerDictationViews(app)" in src
    assert src.count("// V8 dictation views (v8-70)") >= 4
