"""V8 browser acceptance (UI8-008): mounted views per role, viewport and theme; the rest is xfail.

Hard failures: the view does not show, an uncaught JavaScript exception, the theme is not applied.
Findings (axe violations, horizontal overflow, console errors, failed requests) are written to the
result JSON and summarised at the end of the run; V8_STRICT=1 turns them into failures.
Screenshots: <out>/<build commit>__<page>_<view>_<role>_<viewport>_<theme>.png
"""

from __future__ import annotations

import pytest

import v8_browser_support as support

MOUNTED = [(p, role, size, theme) for p in support.PAGES if p.mounted
           for role in p.roles for size in support.VIEWPORTS for theme in support.THEMES]
NOT_MOUNTED = [p for p in support.PAGES if not p.mounted]


def _case_id(case) -> str:
    page, role, size, theme = case
    return f"{page.page_id}-{page.view.replace('/', '_')}-{role}-{size}-{theme}"


@pytest.mark.parametrize("case", MOUNTED, ids=_case_id)
def test_mounted_page(case, browser, app_base, role_states, axe_js, commit, strict):
    page_entry, role, size, theme = case
    width, height = support.VIEWPORTS[size]
    stem = f"{commit}__{_case_id(case)}"
    context = browser.new_context(viewport={"width": width, "height": height}, storage_state=role_states[role],
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
            "overflow": overflow, "diagnostics": diagnostics, "js_errors": js_errors})
        if any(counts.values()):
            support.FINDINGS.append({"case": _case_id(case), "counts": {k: v for k, v in counts.items() if v}})

        assert not js_errors, js_errors  # hard: uncaught exceptions
        if strict:
            assert not violations, [v["id"] for v in violations]
            assert not (overflow["document"] or overflow["body"]), overflow
            assert not diagnostics, diagnostics
    finally:
        context.close()


@pytest.mark.parametrize("page_entry", [
    pytest.param(p, id=p.page_id, marks=pytest.mark.xfail(reason="not mounted", run=False)) for p in NOT_MOUNTED])
def test_unmounted_page(page_entry):
    raise AssertionError(f"{page_entry.page_id} {page_entry.title}: not mounted")


def test_axe_core_available(axe_js):
    if axe_js is None:
        raise pytest.skip.Exception("no local axe-core (set AXE_CORE_JS or install axe-core in node_modules); nothing is downloaded")
