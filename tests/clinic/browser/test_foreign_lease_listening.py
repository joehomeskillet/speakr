"""A verified listener can open work in progress without taking the writer's lease."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def _post(ctx, url, body):
    token = ctx.request.get(url.split("/api/clinic/")[0] + "/api/csrf-token").json()["csrf_token"]
    return ctx.request.post(url, data=body, headers={"X-CSRF-Token": token, "Idempotency-Key": str(uuid4())})


@contextmanager
def _hold_lease(writer, url):
    locked = _post(writer, url, {})
    assert locked.ok
    proof = {key: locked.json()[key] for key in ("lease_id", "generation")}
    try:
        yield proof
    finally:
        assert _post(writer, url + "/release", proof).ok


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_foreign_lease_allows_listening_without_edits(browser, app_base, role_states, width, height):
    writer = browser.new_context(storage_state=role_states["typist"])
    listener = browser.new_context(storage_state=role_states["admin"], has_touch=width == 390,
                                   viewport={"width": width, "height": height})
    row = writer.request.get(app_base + "/api/clinic/v1/worklist/rows").json()["items"][0]
    url = app_base + "/api/clinic/v1/dictations/" + row["dictation_id"] + "/leases"
    try:
        with _hold_lease(writer, url) as proof:
            current = listener.request.get(url).json()
            assert current["held"] and current["may_listen"] and not current["may_acquire"]
            page = listener.new_page()
            writes = []
            page.on("request", lambda req: writes.append(req.url) if req.method == "POST" and url in req.url else None)
            page.goto(app_base + "/#/arbeitsliste")

            def press(control):
                if width == 390:
                    control.tap()
                else:
                    control.click()

            press(page.locator(f'[data-row][data-id="{row["dictation_id"]}"] .titlecell'))
            press(page.get_by_role("button", name="Anhören", exact=True))
            editor = page.get_by_test_id("clinic-correction-view")
            expect(editor.get_by_test_id("text")).to_have_value("Anamnese Husten seit drei Tagen.")
            assert editor.get_by_test_id("text").evaluate("(el) => el.readOnly")
            expect(editor.get_by_test_id("play-toggle")).to_be_visible()
            expect(editor.locator('[data-action="acquire"]')).to_have_count(0)
            press(page.get_by_test_id("clinic-correction-back"))
            expect(page.get_by_test_id("clinic-worklist-view")).to_be_visible()
            assert writes == []
            assert _post(listener, url, {}).status == 404
            after = listener.request.get(url).json()
            assert (after["holder_user_id"], after["generation"]) == (current["holder_user_id"], proof["generation"])
        page = listener.new_page()
        page.goto(app_base + "/#/aufnahmen")
        with page.expect_response(lambda res: res.url == url and res.request.method == "GET") as inspection:
            page.locator(f'[data-row][data-id="{row["dictation_id"]}"] .record-title').click()
        assert inspection.value.json()["may_acquire"] is False
        expect(page.get_by_test_id("text")).to_have_value("Anamnese Husten seit drei Tagen.")
        expect(page.locator('[data-action="acquire"]')).to_have_count(0)
    finally:
        writer.close()
        listener.close()
