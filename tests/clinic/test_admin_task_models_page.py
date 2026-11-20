"""The regular clinic administration entry for local ASR and summary models."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_wp_mount_admin import ADMIN_EMAIL, LEAD_EMAIL, TEAM_PAGE, _login, browser, live_server  # noqa: F401

ENDPOINT = "/api/clinic/v1/admin/task-models"


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_model_selection_reachable_and_saved(live_server, browser, width, height):
    base, _ = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/admin")
    nav = page.get_by_role("navigation", name="Verwaltung", exact=True)
    link = nav.get_by_role("link", name="Modellwahl", exact=True)
    expect(link).to_have_count(1)
    link.focus()
    link.press("Enter")
    expect(link).to_have_attribute("aria-current", "page")
    expect(page).to_have_url(base + "/admin#/system/modelle")
    panel = page.get_by_role("region", name="Modellwahl", exact=True)
    expect(panel.get_by_role("heading", name="Transkription")).to_be_visible()
    expect(panel.get_by_label("GPU-Modell", exact=True)).to_be_visible()
    expect(panel.get_by_label("CPU-Modell", exact=True)).to_be_visible()
    summary = panel.get_by_label("Textmodell", exact=True)
    submit = panel.get_by_role("button", name="Modellwahl speichern")
    expect(submit).to_be_disabled()
    previous = summary.input_value()
    sample = f"qa-local-summary-{width}"
    cpu = panel.get_by_label("CPU-Modell", exact=True)
    previous_cpu = cpu.input_value()
    cpu.fill(f"qa-cpu-{width}")
    summary.fill(sample)
    with page.expect_response(lambda r: r.url.endswith(ENDPOINT) and r.request.method == "PUT") as saved:
        submit.click()
    assert saved.value.status == 200
    expect(panel.get_by_role("status")).to_have_text("Modellwahl gespeichert.")
    models = saved.value.json()["models"]
    assert models["summary"]["override"] == sample
    assert models["asr_cpu"]["override"] == f"qa-cpu-{width}"

    page.reload()
    expect(summary).to_have_value(sample)
    expect(cpu).to_have_value(f"qa-cpu-{width}")
    expect(submit).to_be_disabled()
    expect(panel.locator("#effective-summary")).to_contain_text("Hier festgelegt")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    out = os.environ.get("SPEAKR_MODEL_SCREENSHOTS")
    if out:
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(Path(out) / f"modellwahl-{width}.png"), full_page=True)
        if width == 390:
            submit.scroll_into_view_if_needed()
            page.screenshot(path=str(Path(out) / "modellwahl-390-speichern.png"))
    cpu.fill(previous_cpu)
    summary.fill(previous)
    submit.click()
    expect(panel.get_by_role("status")).to_have_text("Modellwahl gespeichert.")
    assert not errors


def test_model_selection_handles_unavailable_and_conflict(live_server, browser):
    base, _ = live_server
    page, _ = _login(browser, base, ADMIN_EMAIL)
    page.route("**" + ENDPOINT, lambda route: route.fulfill(status=503, json={}))
    page.goto(base + "/admin#/system/modelle")
    panel = page.get_by_role("region", name="Modellwahl", exact=True)
    expect(panel.get_by_role("alert")).to_contain_text("nicht geladen")
    page.unroute("**" + ENDPOINT)
    panel.get_by_role("button", name="Neu laden").click()
    summary = panel.get_by_label("Textmodell", exact=True)
    saved_value = summary.input_value()
    summary.fill("synthetic-conflicting-draft")
    page.route("**" + ENDPOINT, lambda r: r.fulfill(status=409, json={"error": "stale_revision"}))
    submit = panel.get_by_role("button", name="Modellwahl speichern")
    submit.click()
    expect(panel.get_by_role("alert")).to_contain_text("zwischenzeitlich geändert")
    expect(summary).to_have_value("synthetic-conflicting-draft")
    expect(submit).to_be_disabled()
    page.unroute("**" + ENDPOINT)
    panel.get_by_role("button", name="Neu laden").click()
    expect(summary).to_have_value(saved_value)
    expect(submit).to_be_disabled()


def test_model_selection_site_admin_only(live_server, browser):
    base, _ = live_server
    page, _ = _login(browser, base, LEAD_EMAIL)
    page.goto(base + TEAM_PAGE)
    nav = page.get_by_role("navigation", name="Klinik-Verwaltung", exact=True)
    expect(nav).to_be_visible()
    expect(nav.get_by_role("link", name="Modellwahl")).to_have_count(0)
    assert page.goto(base + "/admin#/system/modelle").status == 403
    assert page.request.get(base + ENDPOINT).status == 403
