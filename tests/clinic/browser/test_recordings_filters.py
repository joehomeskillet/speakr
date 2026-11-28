"""P01 adaptive disclosure against the real local app and recordings endpoint."""

import json
import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

import v8_browser_support as support
from test_recordings_pending import _save_rendered_dom
from v8_browser_support import OUT_DIR, _wait_loading_overlay_gone


def measure(page, listing, name):
    """Keep viewport evidence before any interaction can scroll the first row."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    row = listing.locator("[data-row]").first
    expect(row).to_be_visible()
    result = {
        "viewport": page.viewport_size,
        "first_row": row.bounding_box(),
        "filters_visible": listing.get_by_test_id("filters").is_visible(),
        "toggle_visible": listing.get_by_test_id("filters-toggle").is_visible(),
        "horizontal_overflow": page.evaluate("document.documentElement.scrollWidth > innerWidth"),
        "scroll_y": page.evaluate("window.scrollY"),
    }
    page.screenshot(path=str(OUT_DIR / f"{name}.png"), animations="disabled")
    (OUT_DIR / f"{name}.json").write_text(json.dumps(result, indent=2))
    return result


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_filters_first_load_and_adaptive_resize(browser, app_base, role_states, width, height):
    context = browser.new_context(storage_state=role_states["author"], viewport={"width": width, "height": height})
    try:
        page = context.new_page()
        page.goto(app_base + "/#/aufnahmen")
        _wait_loading_overlay_gone(page)
        listing = page.get_by_test_id("clinic-recordings-view")
        # Both starting sizes, then both breakpoint directions without remounting.
        for step, size in enumerate([width, 1440 if width == 390 else 390, width]):
            page.set_viewport_size({"width": size, "height": 844 if size == 390 else 900})
            toggle = listing.get_by_test_id("filters-toggle")
            filters = listing.get_by_test_id("filters")
            result = measure(page, listing, f"p01-start-{width}-step-{step}-{size}")
            assert not result["horizontal_overflow"]
            expect(filters).to_be_visible()
            for control in ["filter-search", "filter-status", "filter-period", "filter-pool", "filter-author"]:
                expect(listing.get_by_test_id(control)).to_be_visible()
            expect(listing.get_by_test_id("apply")).to_have_count(0)
            expect(listing.get_by_test_id("reset")).to_have_count(0)
            toolbar = listing.locator(".record-toolbar").bounding_box()
            roles = listing.locator("#crv-role-filters").bounding_box()
            assert roles["y"] >= toolbar["y"] + toolbar["height"]
            first = result["first_row"]
            assert first["y"] >= 0 and first["y"] + first["height"] <= page.viewport_size["height"]
            if size == 390:
                expect(toggle).to_be_visible()
                expect(toggle).to_have_attribute("aria-expanded", "true")
                toggle.focus()
                page.keyboard.press("Tab")
                expect(listing.get_by_test_id("filter-search")).to_be_focused()
            else:
                expect(toggle).to_be_hidden()
    finally:
        context.close()


def test_filter_toggle_preserves_values_and_server_apply(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states["author"], viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        requests = []
        page.on("request", lambda request: requests.append(request.url) if urlsplit(request.url).path == "/api/clinic/v1/recordings" else None)
        page.goto(app_base + "/#/aufnahmen")
        _wait_loading_overlay_gone(page)
        listing = page.get_by_test_id("clinic-recordings-view")
        row = listing.locator("[data-row]").first
        expect(row).to_be_visible()
        original_id = row.get_attribute("data-id")
        source = page.request.get(app_base + "/api/clinic/v1/recordings").json()["items"][0]
        toggle = listing.get_by_test_id("filters-toggle")
        filters = listing.locator("#crv-role-filters")
        expect(toggle).to_have_attribute("aria-controls", filters.get_attribute("id"))
        expect(filters).to_be_visible()
        count = len(requests)
        toggle.focus()
        toggle.press("Enter")
        expect(filters).to_be_hidden()
        for name in ["filter-search", "filter-status", "filter-period"]:
            expect(listing.get_by_test_id(name)).to_be_visible()
        toggle.press("Enter")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(filters).to_be_visible()
        measure(page, listing, "p01-mobile-expanded")
        page.set_viewport_size({"width": 1440, "height": 900})
        expect(filters).to_be_visible()
        expect(toggle).to_be_hidden()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(filters).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        listing.get_by_test_id("filter-search").fill("author-v860")
        listing.get_by_test_id("filter-author").fill(source["dictated_by"]["display_name"])
        pool = source["staff_pools"][0]
        listing.get_by_test_id("filter-pool").fill(pool["name"])
        listing.get_by_test_id("filter-period").select_option("custom")
        listing.get_by_test_id("filter-from").fill("2020-01-01")
        listing.get_by_test_id("filter-to").fill("2099-12-31")
        toggle.press("Space")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(filters).to_be_hidden()
        expect(toggle).to_be_focused()
        # Desktop exposes the same mounted controls; returning to mobile keeps them collapsed.
        page.set_viewport_size({"width": 1440, "height": 900})
        expect(filters).to_be_visible()
        expect(toggle).to_be_hidden()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(filters).to_be_hidden()
        toggle.click()
        for control, value in [("filter-search", "author-v860"), ("filter-author", source["dictated_by"]["display_name"]), ("filter-pool", pool["name"]), ("filter-from", "2020-01-01"), ("filter-to", "2099-12-31")]:
            expect(listing.get_by_test_id(control)).to_have_value(value)
        assert len(requests) == count  # Disclosure, resizing and draft edits do not apply filters.
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/clinic/v1/recordings") as response:
            listing.get_by_test_id("apply").click()
        assert response.value.ok
        assert parse_qs(urlsplit(response.value.url).query).items() >= {
            "author_id": [str(source["dictated_by"]["id"])], "pool_id": [str(pool["id"])],
            "from": ["2020-01-01"], "to": ["2099-12-31"],
        }.items()
        assert original_id in {item["dictation_id"] for item in response.value.json()["items"]}
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/clinic/v1/recordings") as response:
            listing.get_by_test_id("filter-status").select_option(source["workflow_status"])
        assert parse_qs(urlsplit(response.value.url).query)["status"] == [source["workflow_status"]]
        toggle.click()
        expect(filters).to_be_hidden()
        expect(toggle).to_contain_text("5")
        # Same real row remains keyboard-operable over repeated open/return cycles.
        for key in ["Enter", "Space"]:
            row.focus()
            row.press(key)
            expect(page.get_by_test_id("clinic-correction-view")).to_be_visible()
            page.get_by_test_id("clinic-correction-back").click()
            expect(row).to_be_visible()
            expect(row).to_have_attribute("data-id", original_id)
        toggle.click()
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/clinic/v1/recordings") as response:
            listing.get_by_test_id("reset").click()
        assert response.value.ok
        assert not ({"author_id", "pool_id", "status", "from", "to"} & parse_qs(urlsplit(response.value.url).query).keys())
        expect(listing.get_by_test_id("filter-search")).to_have_value("")
    finally:
        context.close()


RT04_TYPES = {
    "sitzung": "Sitzung",
    "patientengespraech": "Patientengespräch",
    "bericht": "Bericht",
    "notiz": "Persönliche Notiz",
}
RT04_SEED = """
    empty = user("rt04-empty", "rt04-empty@local.test")
    pools.add_author_member(pool.id, empty.id, actor_id=admin.id)
    report = db.session.get(workflow.ClinicWorkflowDictation, dic.id)
    report.status = workflow.DRAFT
    report.assignee_user_id = None
    for kind in ("sitzung", "patientengespraech", "notiz"):
        take = Recording(user_id=author.id, title=f"RT04 {kind}", status="COMPLETED",
                         original_filename="synthetic.wav")
        db.session.add(take)
        db.session.flush()
        private = ClinicDictation(recording_id=take.id, author_user_id=author.id, pool_id=pool.id,
                                  configured_recording_type=kind)
        db.session.add(private)
        db.session.flush()
        db.session.add(workflow.ClinicWorkflowDictation(
            id=private.id, author_user_id=author.id, pool_id=pool.id,
            status=workflow.DRAFT, audio_seconds=4))
    db.session.commit()
"""


@pytest.fixture(scope="module")
def rt04_base(tmp_path_factory):
    # Keep the original report (including its legacy type default) and add three private drafts.
    source = support.SERVER.replace("server = make_server", RT04_SEED + "\nserver = make_server", 1)
    with support.serve_app(tmp_path_factory.mktemp("rt04"), source) as base:
        yield base


@pytest.fixture(scope="module")
def rt04_states(browser, rt04_base):
    return {
        "rows": support.login(browser, rt04_base, support.ROLES["author"]),
        "empty": support.login(browser, rt04_base, "rt04-empty@local.test"),
    }


def _expect_rt04_rows(listing, kinds):
    rows = listing.locator("[data-row]")
    expect(rows).to_have_count(len(kinds))
    for kind in kinds:
        badge = listing.page.get_by_test_id("recording-type").filter(has_text=RT04_TYPES[kind])
        row = rows.filter(has=badge)
        expect(row.get_by_test_id("recording-type")).to_be_visible()
        expect(row.get_by_test_id("recording-type")).to_have_text(RT04_TYPES[kind])
        expect(row).to_have_accessible_description(re.compile(RT04_TYPES[kind]))
        step = row.get_by_test_id("recording-next-step")
        if kind == "bericht":
            expect(step).not_to_have_text("Nur für Sie")
        else:
            expect(step).to_be_visible()
            expect(step).to_have_text("Nur für Sie")
    for marker in ("pending-heading", "pending-shortcut"):
        if "bericht" in kinds:
            expect(listing.get_by_test_id(marker)).to_be_visible()
        else:
            expect(listing.get_by_test_id(marker)).to_have_count(0)


def _expect_rt04_mobile_layout(page, type_filter):
    overflow = support.horizontal_overflow(page)
    assert not (overflow["document"] or overflow["body"]), overflow
    target = type_filter.bounding_box()
    assert target is not None and min(target["width"], target["height"]) >= 48, target
    assert 0 <= target["x"] <= page.viewport_size["width"] - target["width"], target


@pytest.mark.parametrize("scenario", ["rows", "empty"])
@pytest.mark.parametrize("size", support.VIEWPORTS)
@pytest.mark.parametrize("theme", support.THEMES)
def test_recording_type_filter(browser, rt04_base, rt04_states, scenario, size, theme):
    width, height = support.VIEWPORTS[size]
    with browser.new_context(
        storage_state=rt04_states[scenario], viewport={"width": width, "height": height},
        **support.theme_context_options(theme),
    ) as context:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        support._open_shell_view(page, rt04_base, "#/aufnahmen")
        listing = page.get_by_test_id("clinic-recordings-view")
        control = listing.locator('select[data-testid="filter-type"]')
        expect(control).to_be_visible()
        expect(control).to_have_value("")
        expect(control.locator('option[value=""]')).to_have_text("Alle")
        assert set(control.locator("option").evaluate_all("options => options.map(option => option.value)")) == {
            "", *RT04_TYPES,
        }
        expect(control.locator("option")).to_have_count(5)
        if scenario == "rows":
            _expect_rt04_rows(listing, RT04_TYPES)
        else:
            expect(listing.get_by_test_id("empty-state")).to_be_visible()
            _expect_rt04_rows(listing, ())
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
        if size == "390x844":
            _expect_rt04_mobile_layout(page, control)
        if os.environ.get("V8_BROWSER_OUT"):
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            stem = OUT_DIR / f"rt04-p01-{scenario}-{size}-{theme}"
            page.screenshot(path=str(stem.with_suffix(".png")), full_page=True, animations="disabled")
            _save_rendered_dom(page, stem.with_suffix(".html"))
            if scenario == "rows" and size == "390x844":
                last = listing.locator("[data-row]").last
                last.scroll_into_view_if_needed()
                page.screenshot(path=str(stem.with_name(stem.name + "-list").with_suffix(".png")), animations="disabled")
        if scenario == "rows":
            for kind in (*RT04_TYPES, ""):
                with page.expect_response(
                    lambda response: urlsplit(response.url).path == "/api/clinic/v1/recordings"
                ) as response:
                    control.select_option(kind)
                assert response.value.ok
                params = parse_qs(urlsplit(response.value.url).query, keep_blank_values=True)
                assert params.get("recording_type") == ([kind] if kind else None)
                expect(control).to_have_value(kind)
                _expect_rt04_rows(listing, (kind,) if kind else RT04_TYPES)
                if size == "390x844":
                    _expect_rt04_mobile_layout(page, control)
