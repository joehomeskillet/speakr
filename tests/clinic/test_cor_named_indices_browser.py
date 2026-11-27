"""PAR-COR-007: free-text index names in the marks drawer, against Flask, the real lease and a rendered WAV."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_p06_audio_edit_browser import _edit, _select
from test_wp_rest27_repeat_ui import _repeat_server


@pytest.fixture
def audio_server(tmp_path_factory):
    yield from _repeat_server(tmp_path_factory)


def _drawer(page, name):
    if page.locator("#ccv-drawer[data-open=true]").count():
        page.get_by_test_id("drawer-close").click()
    page.locator(f"[data-context-toggle={name}]").click()


def _marks(page):
    _drawer(page, "marks")
    return page.locator(".clinic-audio-markers")


def _open(page):
    page.get_by_test_id("open-link").first.click()


def _is_marker_post(r):
    return r.url.endswith("/markers") and r.request.method == "POST"


def _place(page, panel, seconds, name):
    page.evaluate("s => { document.querySelector('.clinic-correction-view audio').currentTime = s }", seconds)
    panel.get_by_label("Name des Index", exact=True).fill(name)
    with page.expect_response(_is_marker_post) as ack:
        panel.get_by_role("button", name="Indexmarke setzen", exact=True).click()
    assert ack.value.status == 200, ack.value.text()
    body = ack.value.request.post_data_json
    assert body["marker_type"] == "INDEX" and body["label"] == name
    expect(panel.get_by_label("Name des Index", exact=True)).to_have_value("")


def _shots(page, name):
    folder = os.environ.get("COR007_ARTIFACTS")
    if not folder:
        return
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    for width, height in [(1440, 900), (1280, 720), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        for scheme in ("light", "dark"):
            page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", scheme == "dark")
            page.screenshot(path=str(out / f"{name}-{width}-{scheme}.png"))
    page.set_viewport_size({"width": 1400, "height": 900})
    page.evaluate("document.documentElement.classList.remove('dark')")


def test_named_indices_survive_reopen_seek_remove_and_become_historical(audio_server):
    base, _, _ = audio_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page = mount._login(browser, base, "author213@local.test")
            _open(page)
            page.locator("[data-action=acquire]").click()
            panel = _marks(page)
            expect(panel.get_by_label("Name des Index", exact=True)).to_be_enabled()
            expect(panel.get_by_text("Keine Marken vorhanden.")).to_be_visible()
            _shots(page, "empty")
            _place(page, panel, 1, "Befund")
            _place(page, panel, 2.5, "Diagnose")
            items = panel.locator("li")
            expect(items).to_have_count(2)
            expect(items.nth(0)).to_contain_text("Befund")
            expect(items.nth(1)).to_contain_text("Diagnose")
            _shots(page, "rows")

            # Reopen: both names come back from the server and can be jumped to.
            page.get_by_test_id("close").click()
            _open(page)
            panel = _marks(page)
            items = panel.locator("li")
            expect(items).to_have_count(2)
            expect(items.nth(1)).to_contain_text("Diagnose")
            items.nth(1).get_by_role("button", name="Zur Marke springen", exact=True).click()
            assert abs(page.evaluate("document.querySelector('.clinic-correction-view audio').currentTime") - 2.5) < 0.2

            # Removing an index leaves the audio revision untouched.
            source = page.locator(".clinic-correction-view audio").get_attribute("src")
            page.locator("[data-action=acquire]").click()
            expect(items.nth(0).get_by_role("button", name="Marke entfernen", exact=True)).to_be_enabled()
            items.nth(0).get_by_role("button", name="Marke entfernen", exact=True).click()
            expect(items).to_have_count(1)
            expect(items.nth(0)).to_contain_text("Diagnose")
            assert page.locator(".clinic-correction-view audio").get_attribute("src") == source

            # Audio editing: the remaining index is kept as historical and cannot be jumped to.
            _drawer(page, "options")
            page.get_by_test_id("audio-edit-open").click()
            _select(page, 1, 2)
            _edit(page, "Ausschneiden", key="Delete")
            panel = _marks(page)
            old = panel.locator("li[data-ownership=historical]")
            expect(old).to_have_count(1)
            expect(old).to_contain_text("Diagnose")
            expect(old.get_by_role("button", name="Zur Marke springen", exact=True)).to_have_count(0)
            _shots(page, "historical")
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_name_input_without_lease_sends_nothing_and_needs_no_aligned_text(audio_server):
    base, _, _ = audio_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page = mount._login(browser, base, "author213@local.test")
            sent = []
            page.on("request", lambda r: sent.append(r.url) if r.url.endswith("/markers") and r.method == "POST" else None)
            replies = []
            page.on("response", lambda r: replies.append((r.status, r.text())) if r.url.endswith("/markers") and r.request.method == "POST" else None)
            _open(page)
            panel = _marks(page)
            name = panel.get_by_label("Name des Index", exact=True)
            expect(name).to_be_disabled()
            expect(panel.get_by_role("button", name="Indexmarke setzen", exact=True)).to_be_disabled()
            page.evaluate("document.querySelector('.clinic-correction-view audio').currentTime = 1")
            assert sent == []

            # With the lease the fixture has no alignment, yet a named index at the play position works.
            page.locator("[data-action=acquire]").click()
            expect(name).to_be_enabled()
            page.wait_for_timeout(1500)
            name.fill("Ohne Zuordnung")
            name.press("Enter")
            page.wait_for_timeout(1500)
            assert len(sent) == 1, (panel.locator("[role=status]").inner_text(), name.input_value(), sent, replies)
            assert replies and replies[0][0] == 200, replies
            expect(panel.locator("li")).to_have_count(1)
            expect(panel.locator("li")).to_contain_text("Ohne Zuordnung")
            assert len(sent) == 1
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
