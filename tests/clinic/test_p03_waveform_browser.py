"""P03 uses verified revision peaks as its only visible seek control."""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_p03_detail_playback_browser import _detail, detail_server  # noqa: F401


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
@pytest.mark.parametrize("detail_server", ["variable_envelope"], indirect=True)
def test_wave_transport_and_theme_use_actual_cut_revision(detail_server, width, height):
    base, did, _ = detail_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page, panel, head, calls = _detail(browser, base, mount.TYPIST, did, {"width": width, "height": height})
            assert head.status == 200
            rev = head.json()["revision_id"]
            play = panel.get_by_role("button", name="Wiedergabe starten")
            slider = panel.get_by_role("slider", name="Abspielposition")
            expect(play).to_be_visible()
            expect(slider).to_be_visible()
            expect(panel.locator("audio")).to_be_hidden()
            assert not panel.locator("audio").evaluate("a => a.controls")
            expect(panel.locator("audio")).to_have_js_property("duration", 3)
            assert ("GET", f"/api/clinic/v1/dictations/{did}/audio-revisions/{rev}/peaks") in calls
            heights = panel.locator("svg line").evaluate_all("nodes => nodes.map(n => n.y2.baseVal.value - n.y1.baseVal.value)")
            assert 5 < len(heights) <= (50 if width == 390 else 105) and len(set(heights)) > 4
            play.click()
            page.wait_for_function("document.querySelector('.clinic-detail-view audio').currentTime > .1")
            panel.get_by_role("button", name="Wiedergabe pausieren").click()
            expect(panel.locator("audio")).to_have_js_property("paused", True)
            track = slider.bounding_box()
            slider.click(position={"x": int(track["width"] * .65), "y": int(track["height"] / 2)})
            media = panel.locator("audio")
            page.wait_for_function("document.querySelector('.clinic-detail-view audio').currentTime > 1.5")
            prior = media.evaluate("a => a.currentTime")
            slider.press("ArrowLeft")
            page.wait_for_function("t => document.querySelector('.clinic-detail-view audio').currentTime < t", arg=prior)
            # A little under five seconds remains in this fixture, so the key reaches the start.
            expect(media).to_have_js_property("currentTime", 0)
            slider.click(position={"x": int(track["width"] * .65), "y": int(track["height"] / 2)})
            assert panel.locator("line.played").count() > 0
            colors = []
            for theme in ("light", "dark"):
                if theme == "dark":
                    menu = page.locator("[data-user-menu-toggle]:visible")
                    menu.click()
                    page.locator("[data-user-menu-dropdown] .v8-theme").click()
                    menu.click()
                    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
                colors.append(panel.evaluate("e => getComputedStyle(e).backgroundColor"))
                if folder := os.environ.get("P03_WAVE_ARTIFACTS"):
                    out = Path(folder)
                    out.mkdir(exist_ok=True, parents=True)
                    page.screenshot(path=str(out / f"p03-{theme}-{width}.png"))
                    panel.screenshot(path=str(out / f"p03-wave-{theme}-{width}.png"))
            assert colors[0] != colors[1]
            assert all(method == "GET" for method, _ in calls)
            assert not page.js_errors
        finally:
            browser.close()
