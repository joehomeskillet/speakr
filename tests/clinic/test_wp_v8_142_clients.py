"""Real P10/P11 mounts and local visual proof; role writes and membership readback use the real v8-133 routes."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server  # noqa: F401


def test_real_clients_names_and_lead_toggle(live_server, browser):
    base, ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(base + "/admin#/schreibkraefte-pools")
    view = page.get_by_test_id("clinic-admin-pools-view")
    view.get_by_test_id("create-open").click()
    view.locator("#cap-create-name").fill("Schreibdienst Nord")
    view.get_by_test_id("create-panel").get_by_role("button", name="Schreibkräfte-Pool anlegen").click()
    view.get_by_role("button", name="Schreibkräfte-Pool bearbeiten: Schreibdienst Nord").click()
    view.get_by_label("Benutzer nach Name suchen", exact=True).fill("w226typist")
    view.get_by_role("button", name="Hinzufügen: w226typist", exact=True).click()
    expect(view.get_by_test_id("member-list")).to_contain_text("w226typist")
    toggle = view.get_by_role("checkbox", name="Leitung: w226typist", exact=True)
    expect(toggle).to_be_enabled()
    expect(toggle).not_to_be_checked()

    def set_role(role):
        with page.expect_response(lambda r: r.url.endswith("/role") and r.request.method == "PUT") as changed:
            toggle.set_checked(role == "lead")
        response = changed.value
        assert response.status == 200
        assert response.request.headers.get("x-csrf-token")
        assert response.request.post_data_json == {"role": role}
        assert response.json()["role"] == role
        # A fresh app mount must read the persisted role from the scoped membership endpoint.
        with page.expect_response(lambda r: r.url == base + "/api/clinic/v1/pools" and r.request.method == "GET") as loaded:
            page.reload()
        pool = next(p for p in loaded.value.json()["staff_pools"] if p["name"] == "Schreibdienst Nord")
        assert pool["members"] == [{"user_id": ids["typist"], "role": role}]
        view.get_by_role("button", name="Schreibkräfte-Pool bearbeiten: Schreibdienst Nord").click()
        expect(toggle).to_be_enabled()
        expect(toggle).to_be_checked(checked=role == "lead")

    set_role("lead")
    set_role("member")

    # A server refusal must restore the confirmed role, not leave a misleading checked box.
    page.route("**/api/clinic/v1/pool-admin/pools/staff/*/members/*/role",
               lambda route: route.fulfill(status=403, json={"code": "forbidden"}))
    with page.expect_response(lambda response: response.url.endswith('/role') and response.status == 403):
        toggle.click()
    expect(view.get_by_role("alert")).to_contain_text("Keine Berechtigung")
    expect(toggle).not_to_be_checked()
    page.unroute("**/api/clinic/v1/pool-admin/pools/staff/*/members/*/role")
    set_role("lead")

    proof = Path(os.environ.get("V8_BROWSER_OUT") or Path(__file__).resolve().parents[2] / "artifacts" / "v8-142")
    proof.mkdir(parents=True, exist_ok=True)
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        view.get_by_test_id("member-list").locator("..").screenshot(path=str(proof / f"p10-members-{width}.png"))
        page.evaluate("document.querySelectorAll('*').forEach(element => { if (element.scrollTop) element.scrollTop = 0; })")
        page.screenshot(path=str(proof / f"p10-{width}.png"), full_page=True)
    page.goto(base + "/#/verwaltung/diktierende")
    authors = page.get_by_test_id("clinic-authors-view")
    expect(authors.get_by_role("rowheader", name="w226typist", exact=False).first).to_be_visible()
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        page.evaluate("document.querySelectorAll('*').forEach(element => { if (element.scrollTop) element.scrollTop = 0; })")
        page.screenshot(path=str(proof / f"p11-{width}.png"), full_page=True)
    assert any("/admin/users" in url for url in requests)
    assert any("/api/clinic/v1/pools" in url for url in requests)
    assert not any("/api/clinic/v1/admin/" in url for url in requests)
    assert not errors, errors
    (proof / "requests.json").write_text(json.dumps(requests, indent=2))
