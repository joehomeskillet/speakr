"""Exercise PB-03 at the real author start page using only synthetic recordings."""

import os
import re
from itertools import product

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


SEED = """
    from src.clinic.models import ClinicContextRevision

    empty = user("empty-v860", "empty-v860@local.test")
    pools.add_author_member(pool.id, empty.id, actor_id=admin.id)
    for state, label in ((workflow.DRAFT, "PB03 Testtitel"),
                         (workflow.PREP_OFFLINE, "PB03 Eingang")):
        take = Recording(user_id=author.id, title=label, status="COMPLETED", original_filename="s.wav")
        db.session.add(take)
        db.session.flush()
        draft = ClinicDictation(recording_id=take.id, author_user_id=author.id, pool_id=pool.id)
        db.session.add(draft)
        db.session.flush()
        db.session.add(ClinicContextRevision(dictation_id=draft.id, case_ref="PB03-FALL",
                                             patient_ref="PB03-PATIENT"))
        db.session.add(workflow.ClinicWorkflowDictation(
            id=draft.id, author_user_id=author.id, pool_id=pool.id, status=state, audio_seconds=4))
    db.session.commit()
"""


@pytest.fixture(scope="module")
def pending_base(tmp_path_factory):
    source = support.SERVER.replace("server = make_server", SEED + "\nserver = make_server")
    with support.serve_app(tmp_path_factory.mktemp("pb03"), source) as base:
        yield base


@pytest.fixture(scope="module")
def author_states(browser, pending_base):
    states = {
        "rows": support.login(browser, pending_base, support.ROLES["author"]),
        "empty": support.login(browser, pending_base, "empty-v860@local.test"),
    }
    return states.__getitem__


def _save_rendered_dom(page, path):
    # Preserve the rendered styles when Hallmark opens the snapshot without the app server.
    html = page.evaluate(r"""repo => {
        const root = document.documentElement.cloneNode(true);
        for (const node of root.querySelectorAll('script, link[rel=stylesheet], style, meta[name=csrf-token], input[name=csrf_token]')) node.remove();
        for (const link of root.querySelectorAll('link[href^="/static/"]')) {
            link.setAttribute('href', repo + link.getAttribute('href'));
        }
        const style = document.createElement('style');
        style.textContent = Array.from(document.styleSheets).map(sheet => {
            try {
                const css = Array.from(sheet.cssRules).map(rule => rule.cssText).join('\n');
                return css.replaceAll(/url\("([^"]+)"\)/g, (match, src) => {
                    const url = new URL(src, sheet.href || document.baseURI);
                    return url.origin === location.origin && url.pathname.startsWith('/static/')
                        ? 'url("' + repo + url.pathname + '")' : match;
                });
            }
            catch { return ''; }
        }).join('\n');
        root.querySelector('head').append(style);
        return '<!doctype html>\n' + root.outerHTML;
    }""", os.path.relpath(support.ROOT, path.parent))
    path.write_text(html, encoding="utf-8")


@pytest.mark.parametrize(("scenario", "size", "theme"), list(product(("rows", "empty"), support.VIEWPORTS, support.THEMES)))
def test_p01_pending_start_page(browser, pending_base, author_states, scenario, size, theme):
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(
        viewport={"width": width, "height": height}, storage_state=author_states(scenario),
        **support.theme_context_options(theme),
    )
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        support._open_shell_view(page, pending_base, "")
        view = page.get_by_test_id("clinic-recordings-view")
        shortcut = view.get_by_test_id("pending-shortcut")
        if scenario == "empty":
            expect(view.get_by_test_id("empty-state")).to_be_visible()
            expect(shortcut).to_have_count(0)
            expect(view.get_by_test_id("pending-heading")).to_have_count(0)
        else:
            message = "2 Diktate warten auf Versand oder Bestätigung."
            expect(shortcut).to_have_text(message)
            expect(shortcut).to_have_accessible_name(message)
            assert shortcut.bounding_box()["height"] >= 48
            expect(view.get_by_test_id("scroller").locator("section").first).to_have_attribute("id", "crv-pending")
            pending = view.locator("#crv-pending")
            expect(pending.get_by_test_id("recording-next-step")).to_have_text([
                "Empfang noch offen. Öffnen Sie das Diktat, um den Stand zu sehen.",
                "Sie sind dran: Diktat an den Schreibdienst senden",
            ])
            expect(view.locator("[data-row]")).to_have_count(3)
            expect(pending.get_by_test_id("own-title")).to_contain_text(["PB03 Eingang", "PB03 Testtitel"])
            expect(pending).to_contain_text("PB03-FALL")
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
        assert not support.horizontal_overflow(page)["document"]
        assert not errors
        support.OUT_DIR.mkdir(parents=True, exist_ok=True)
        stem = support.OUT_DIR / f"pb03-p01-{scenario}-{size}-{theme}"
        page.screenshot(path=str(stem.with_suffix(".png")), animations="disabled")
        _save_rendered_dom(page, stem.with_suffix(".html"))
        if scenario == "rows":
            shortcut.focus()
            shortcut.press("Enter")
            heading = view.get_by_test_id("pending-heading")
            expect(heading).to_be_focused()
            assert heading.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
            if size == "390x844":
                for step in view.locator("#crv-pending").get_by_test_id("recording-next-step").all():
                    expect(step).to_be_in_viewport(ratio=1)
                page.screenshot(path=str(stem.with_name(stem.name + "-focused").with_suffix(".png")),
                                animations="disabled")
    finally:
        context.close()


def test_p01_reaches_a_retry_step_when_the_server_does_not_answer(browser, pending_base, author_states):
    context = browser.new_context(storage_state=author_states("rows"), service_workers="block")
    try:
        page = context.new_page()
        request = re.compile(r"/api/clinic/v1/recordings\?")
        page.route(request, lambda route: None)  # Hold the response so the browser deadline is exercised.
        support._open_shell_view(page, pending_base, "")
        view = page.get_by_test_id("clinic-recordings-view")
        expect(view.get_by_test_id("loading-state")).to_be_visible()
        retry = view.get_by_role("button", name="Erneut laden", exact=True)
        expect(retry).to_be_visible(timeout=16000)
        expect(view.get_by_test_id("loading-state")).to_have_count(0)
        expect(view.get_by_test_id("pending-shortcut")).to_have_count(0)
        page.unroute(request)
        retry.click()
        expect(view.get_by_test_id("pending-shortcut")).to_be_visible()
    finally:
        context.close()
