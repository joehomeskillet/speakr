"""Notification choices in the real V8 Settings page and opt-in visual evidence."""

from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import Browser, Locator, Page, Response, Route, expect

import v8_browser_support as support
from v8_page_navigation import show_settings

API = "/api/clinic/v1/preferences/notifications"
LABELS = {
    "acceptance_requested": "Abnahme angefordert",
    "acceptance_rejected": "Abnahme abgelehnt",
    "accepted": "Diktat abgenommen",
    "completed": "Diktat fertiggestellt",
}


def _panel(page: Page) -> Locator:
    panel = page.get_by_role("region", name="Benachrichtigungen", exact=True)
    expect(panel).to_be_visible()
    return panel


def _open_preferences(page: Page, base: str, *, reload: bool = False) -> Response:
    with page.expect_response(lambda r: r.url == base + API and r.request.method == "GET") as loaded:
        if reload:
            page.reload()
        else:
            show_settings(page, base)
    assert loaded.value.ok
    return loaded.value


def _toggle(page: Page, base: str, event: str, enabled: bool, version: int) -> Response:
    checkbox = _panel(page).get_by_role("checkbox", name=LABELS[event], exact=True)
    with page.expect_response(lambda r: r.url == base + API and r.request.method == "PUT") as saved:
        checkbox.click()
    response = saved.value
    assert response.request.post_data_json == {
        "event": event, "enabled": enabled, "expected_version": version,
    }
    return response


def _expect_saved(page: Page, response: Response, event: str, enabled: bool, version: int) -> dict:
    assert response.ok
    data = response.json()
    assert data["version"] == version
    assert data["channels"]["in_app"][event] is enabled
    panel = _panel(page)
    action = "eingeschaltet" if enabled else "ausgeschaltet"
    expect(panel.get_by_role("status")).to_have_text(f"{LABELS[event]}: {action}. Gespeichert.")
    expect(panel.get_by_role("checkbox", name=LABELS[event], exact=True)).to_be_checked(checked=enabled)
    return data


def test_notification_toggles_persist_and_recover_from_stale_settings(
    browser: Browser, app_base: str, role_states: dict,
) -> None:
    context = browser.new_context(storage_state=role_states["author"])
    try:
        worklist = app_base + "/api/clinic/v1/worklist/rows"
        before = context.request.get(worklist)
        assert before.ok
        case, = before.json()["items"]

        current, stale = context.new_page(), context.new_page()
        initial = _open_preferences(current, app_base).json()
        assert _open_preferences(stale, app_base).json() == initial
        version = initial["version"]
        for event, label in LABELS.items():
            enabled = initial["channels"]["in_app"][event]
            expect(_panel(current).get_by_role("checkbox", name=label, exact=True)).to_be_checked(checked=enabled)

        completed = initial["channels"]["in_app"]["completed"]
        requested = initial["channels"]["in_app"]["acceptance_requested"]
        saved = _toggle(current, app_base, "completed", not completed, version)
        saved_choices = _expect_saved(current, saved, "completed", not completed, version + 1)
        assert _open_preferences(current, app_base, reload=True).json() == saved_choices
        expect(_panel(current).get_by_role("checkbox", name=LABELS["completed"], exact=True)).to_be_checked(
            checked=not completed,
        )

        with stale.expect_response(lambda r: r.url == app_base + API and r.request.method == "GET") as refreshed:
            conflict = _toggle(stale, app_base, "acceptance_requested", not requested, version)
        assert conflict.status == 409
        assert conflict.json()["code"] == "stale_version"
        assert refreshed.value.ok and refreshed.value.json() == saved_choices
        panel = _panel(stale)
        expect(panel.get_by_role("status")).to_have_text(
            "Die Einstellungen wurden inzwischen geändert. Bitte wählen Sie erneut.",
        )
        expect(panel.get_by_role("checkbox", name=LABELS["completed"], exact=True)).to_be_checked(checked=not completed)
        expect(panel.get_by_role("checkbox", name=LABELS["acceptance_requested"], exact=True)).to_be_checked(
            checked=requested,
        )
        retried = _toggle(stale, app_base, "acceptance_requested", not requested, version + 1)
        _expect_saved(stale, retried, "acceptance_requested", not requested, version + 2)

        after = context.request.get(worklist)
        assert after.ok
        remaining_case, = after.json()["items"]
        assert (remaining_case["dictation_id"], remaining_case["status"]) == (case["dictation_id"], case["status"])

        # Restore the choices on the shared V8 seed.
        for offset, (event, enabled) in enumerate((("completed", completed), ("acceptance_requested", requested)), 2):
            restored = _toggle(stale, app_base, event, enabled, version + offset)
            _expect_saved(stale, restored, event, enabled, version + offset + 1)
    finally:
        context.close()


def _empty_notification_get(route: Route) -> None:
    if route.request.method != "GET":
        route.fallback()
        return
    response = route.fetch()
    if not response.ok:
        route.fulfill(response=response)
        return
    data = response.json()
    data["channels"]["in_app"] = {}
    route.fulfill(response=response, json=data)


@pytest.mark.parametrize("theme", support.THEMES)
@pytest.mark.parametrize("size", support.VIEWPORTS)
def test_notification_visual_artifacts(
    browser: Browser, app_base: str, role_states: dict, size: str, theme: str,
) -> None:
    out = support.ROOT / ".wp" / "artifacts"
    capture = os.environ.get("WFB_NOTIFICATION_ARTIFACTS") == "1"
    if capture:
        out.mkdir(parents=True, exist_ok=True)
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(
        storage_state=role_states["author"], viewport={"width": width, "height": height},
        **support.theme_context_options(theme),
    )
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        for state in ("rows", "empty"):
            if state == "empty":
                page.route(app_base + API, _empty_notification_get)
            loaded = _open_preferences(page, app_base, reload=state == "empty")
            panel = _panel(page)
            if state == "rows":
                boxes = panel.get_by_role("checkbox")
                expect(boxes).to_have_count(len(LABELS))
                for box in boxes.all():
                    assert box.locator("..").bounding_box()["height"] >= 48
            else:
                assert loaded.json()["channels"]["in_app"] == {}
                expect(panel.get_by_role("checkbox")).to_have_count(0)
                expect(panel.get_by_text(
                    "Für Sie sind keine Benachrichtigungen zur Auswahl verfügbar.", exact=True,
                )).to_be_visible()
            html = page.locator("html")
            if theme == "dark":
                expect(html).to_have_class(re.compile(r"\bdark\b"))
            else:
                expect(html).not_to_have_class(re.compile(r"\bdark\b"))
            assert panel.evaluate("el => el.scrollWidth <= el.clientWidth")
            if capture:
                stem = out / f"wfb-notification-preferences-{state}-{size}-{theme}"
                panel.scroll_into_view_if_needed()
                page.screenshot(path=stem.with_suffix(".png"), animations="disabled")
                stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    finally:
        context.close()
