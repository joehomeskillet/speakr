"""V8 browser acceptance (UI8-008): P01–P21 per authorized role, viewport and theme.

Hard failures: the view does not show, an uncaught JavaScript exception, the theme is not applied.
Findings (axe violations, horizontal overflow, console errors, failed requests) are written to the
result JSON and summarised at the end of the run; V8_STRICT=1 turns them into failures.
Screenshots: <out>/<build commit>__<page>_<view>_<role>_<viewport>_<theme>.png
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

import v8_browser_support as support

MOUNTED = [(p, role, size, theme) for p in support.PAGES if p.mounted
           for role in p.roles for size in support.VIEWPORTS for theme in support.THEMES]
PROFILE_VIEWPORTS = {**support.VIEWPORTS, "320x700": (320, 700)}


@pytest.fixture(scope="module")
def app_base(tmp_path_factory):
    source = support.SERVER.replace("server = make_server", support.navigation.MATRIX_SEED + "\nserver = make_server")
    source = ("import os\nos.environ['SPEAKR_SKIP_BACKGROUND_TASKS'] = 'true'\n"
              "os.environ['TRANSCRIPTION_BASE_URL'] = 'http://127.0.0.1:9/v1'\n" + source)
    with support.serve_app(tmp_path_factory.mktemp("v8-matrix21"), source) as base:
        yield base


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


def _capture_profile(page, proof):
    # Freeze the rendered DOM and keep local styles available for Hallmark's file render.
    proof.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=proof.with_suffix(".png"), full_page=True, animations="disabled", caret="hide")
    document = page.evaluate(r"""(root) => {
        const snapshot = document.documentElement.cloneNode(true);
        snapshot.querySelectorAll('script, meta[name="csrf-token"]').forEach(node => node.remove());
        snapshot.querySelectorAll('link[href], img[src]').forEach(node => {
            const attr = node.matches('link') ? 'href' : 'src';
            const url = new URL(node.getAttribute(attr), location.href);
            if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
                node.setAttribute(attr, root + url.pathname);
                node.removeAttribute('srcset');
            } else if (node.matches('link[rel="stylesheet"]')) node.remove();
        });
        return '<!doctype html>\n' + snapshot.outerHTML;
    }""", support.ROOT.as_uri())
    proof.with_suffix(".html").write_text(document, encoding="utf-8")


@pytest.mark.parametrize("state", ["empty", "populated"])
@pytest.mark.parametrize("size,theme", [(size, theme) for size in PROFILE_VIEWPORTS for theme in support.THEMES])
def test_p15_profile_layout(browser, app_base, role_states, size, theme, state):
    width, height = PROFILE_VIEWPORTS[size]
    with browser.new_context(viewport={"width": width, "height": height}, storage_state=role_states["admin"],
                             **support.theme_context_options(theme)) as context:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        if state == "populated":
            page.route("**/api/clinic/v1/asr-profiles", lambda route: route.fulfill(json={
                "version": 7, "profile": {"id": "p", "name": "Klinik Deutsch", "version": 7},
                "profiles": [{"id": "p", "name": "Klinik Deutsch", "mode": "both"}],
                "source": "headset", "live_profile_id": "p", "batch_profile_id": "p",
                "engine": {"configured": "gpu", "actual": "cpu"}, "prompts_enabled": False,
                "output_format": {"container": "webm", "codec": "opus", "sample_rate": 48000},
                "capabilities": {"configured": ["batch", "live", "alignment"], "proven": ["batch"]},
            }))
        support.navigation.show_asr(page, app_base)
        page.wait_for_load_state("networkidle")
        view = page.locator(".clinic-asr-profiles-view")
        expect(view.get_by_text("Ausgabeformat", exact=True)).to_be_attached()
        assert page.locator("html").evaluate("(html) => html.classList.contains('dark')") == (theme == "dark")

        proof = support.OUT_DIR / f"P15-{size}-{theme}-{state}"
        _capture_profile(page, proof)
        nav = page.get_by_test_id("clinic-v8-nav")
        toggle = page.get_by_test_id("clinic-admin-nav-toggle")
        if width == 1440:
            nav.screenshot(path=proof.with_name(proof.name + "-sidebar").with_suffix(".png"),
                           animations="disabled", caret="hide")
        if width <= 390:
            expect(toggle).to_have_text("Bereich: Sprachprofile")
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(toggle).to_be_in_viewport(ratio=1)
            assert toggle.bounding_box()["height"] >= 48
            expect(nav).to_be_hidden()
            toggle.press("Enter")
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(nav.get_by_role("button", name="Sprachprofile", exact=True)).to_have_attribute("aria-current", "page")
            assert nav.locator(".cadm-nav__label").all_text_contents() == [
                "Konten und Pools", "Inhalte", "Richtlinien", "Betrieb", "System",
            ]
            assert nav.evaluate("""nav => {
                const bounds = nav.getBoundingClientRect();
                return nav.scrollWidth <= nav.clientWidth && bounds.left >= 0 && bounds.right <= innerWidth &&
                    [...nav.querySelectorAll('.cadm-nav__item, .cadm-nav__label')].every(node => {
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        const text = range.getBoundingClientRect();
                        const item = node.getBoundingClientRect();
                        return text.width > 0 && text.left >= item.left && text.right <= item.right &&
                            (!node.matches('.cadm-nav__item') || item.height >= 48);
                    });
            }""")
            _capture_profile(page, proof.with_name(proof.name + "-navigation"))
            nav.get_by_role("group", name="Richtlinien").evaluate(
                "node => node.scrollIntoView({block: 'start', behavior: 'instant'})"
            )
            page.screenshot(path=proof.with_name(proof.name + "-navigation-bottom").with_suffix(".png"),
                            animations="disabled", caret="hide")
            nav.get_by_role("button", name="Sprachprofile", exact=True).press("Escape")
            expect(toggle).to_be_focused()
            expect(toggle).to_have_attribute("aria-expanded", "false")
        else:
            expect(toggle).to_be_hidden()
            expect(nav).to_be_visible()

        config = view.get_by_role("region", name="Profilkonfiguration")
        current = view.get_by_role("region", name="Profilstand")
        evidence = view.get_by_test_id("capabilities")
        for region in (config, current, evidence):
            expect(region).to_be_visible()
        left, right, table = [region.bounding_box() for region in (config, current, evidence)]
        if width <= 390:
            assert left["x"] == right["x"] == table["x"]
            assert left["width"] == right["width"] == table["width"]
            assert left["y"] + left["height"] <= right["y"]
        else:
            assert left["y"] == right["y"]
            assert abs(left["width"] - right["width"]) <= 1
            assert left["x"] < right["x"]
            assert abs(table["x"] + table["width"] - right["x"] - right["width"]) <= 1
        assert max(left["y"] + left["height"], right["y"] + right["height"]) <= table["y"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert view.evaluate("""root => [...root.querySelectorAll('.panel *')].every(node => {
            if (!node.matches('section, div, p, form, details')) return true;
            const style = getComputedStyle(node);
            const border = ['Top', 'Right', 'Bottom', 'Left'].every(edge => parseFloat(style['border' + edge + 'Width']) > 0);
            return !border && style.boxShadow === 'none';
        })""")

        primary = config.get_by_role("button", name="Profilvorgabe übernehmen", exact=True)
        expect(primary).to_be_visible()
        expect(view.locator(".primary:visible")).to_have_count(1)
        assert primary.bounding_box()["height"] >= 48
        if width <= 390:
            assert primary.bounding_box()["width"] == config.locator(".asr-save-actions").bounding_box()["width"]
        if width == 390:
            expect(primary).to_be_in_viewport(ratio=1)
        expect(view.get_by_text("Ihr Gerät sendet Aufnahmen an Speakr.", exact=False)).to_be_visible()
        if state == "empty":
            expect(config.get_by_text("Für Ihr Konto ist noch kein Sprachprofil gespeichert.", exact=True)).to_be_visible()
            expect(config.get_by_role("combobox")).to_have_count(0)
            expect(primary).to_be_enabled()
        else:
            expect(view.get_by_label("Profil beim Diktieren (live)", exact=True)).to_have_value("p")
            expect(view.get_by_label("Profil für nachträgliche Erkennung", exact=True)).to_have_value("p")
            expect(current.get_by_text("Klinik Deutsch", exact=True)).to_be_visible()
            expect(current.get_by_text("Ausweichlösung aktiv: CPU statt GPU.", exact=True)).to_be_visible()
            expect(evidence.get_by_role("cell", name="Batchtranskription", exact=True)).to_be_visible()
            expect(primary).to_be_disabled()
        expect(evidence.get_by_role("cell", name="Aussprachetraining", exact=True)).to_be_visible()

        details = current.locator("details")
        expect(details.locator("summary")).to_have_text("Technische Details")
        for label in ("Ausgabeformat", "Modellhash", "Checkpoint", "Cloudumleitung", "Eingabehinweise (Prompts)"):
            expect(view.get_by_text(label, exact=True)).to_have_count(1)
            expect(details.get_by_text(label, exact=True)).to_be_hidden()
        if table["y"] + table["height"] > height:
            current.evaluate("node => node.scrollIntoView({block: 'start', behavior: 'instant'})")
            page.screenshot(path=proof.with_name(proof.name + "-profilstand").with_suffix(".png"),
                            animations="disabled", caret="hide")
        details.locator("summary").focus()
        page.keyboard.press("Enter")
        expect(details.get_by_text("Eingabehinweise (Prompts)", exact=True)).to_be_visible()
        if state == "populated":
            expect(details.get_by_text("webm · opus · 48000 Hz", exact=True)).to_be_visible()


def test_p15_first_profile_uses_existing_save_and_vocabulary_link(browser, app_base, role_states):
    with browser.new_context(storage_state=role_states["admin"]) as context:
        page = context.new_page()
        support.navigation.show_asr(page, app_base)
        expect(page.get_by_text("Für Ihr Konto ist noch kein Sprachprofil gespeichert.", exact=True)).to_be_visible()
        primary = page.get_by_role("button", name="Profilvorgabe übernehmen", exact=True)
        with page.expect_response(lambda res: res.url.endswith("/asr-profiles/selection")) as response:
            primary.click()
        assert response.value.ok
        assert response.value.request.method == "PUT"
        assert response.value.request.post_data_json["expected_version"] == 0
        expect(page.get_by_role("status").filter(has_text="Profilvorgabe gespeichert.")).to_be_visible()
        page.get_by_role("radio", name="Aufnahmegerät", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Ungespeicherte Änderungen")).to_be_visible()
        with page.expect_response(lambda res: res.url.endswith("/asr-profiles/selection")) as response:
            primary.click()
        assert response.value.ok
        assert response.value.request.post_data_json["source"] == "recorder"
        expect(page.get_by_role("status").filter(has_text="Profilvorgabe gespeichert.")).to_be_visible()

        config = page.get_by_role("region", name="Profilkonfiguration")
        config.get_by_text("Quellen und Speichern einstellen", exact=True).click()
        mode = config.get_by_label("Quellenzuordnung", exact=True)
        expect(mode).to_have_value("shared")
        mode.select_option("separate")
        config.get_by_label("Quelle für Hintergrund-Erkennung", exact=True).select_option("headset")
        config.get_by_label("Speicherregel", exact=True).select_option("ask")
        feedback = config.locator('.asr-save-feedback[role="status"]')
        expect(feedback).not_to_contain_text("Profilvorgabe gespeichert.")
        with page.expect_response(lambda res: res.url.endswith("/asr-profiles/selection")) as response:
            primary.click()
        assert response.value.ok
        updates = response.value.request.post_data_json
        assert (updates["profile_mode"], updates["batch_source"], updates["save_rule"]) == ("separate", "headset", "ask")
        expect(feedback).to_contain_text("Profilvorgabe gespeichert.")
        expect(mode).to_have_value("separate")

        page.get_by_role("link", name="Wortschatz prüfen", exact=True).click()
        expect(page).to_have_url(app_base + "/admin#/wortschatz")
        expect(page.get_by_role("heading", name="Wortschatz", exact=True)).to_be_visible()
