"""Create and revoke the separate original-audio grant through the mounted administration."""
from playwright.sync_api import expect

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server  # noqa: F401


def test_admin_can_grant_and_revoke_original_audio_without_general_export(live_server, browser, tmp_path):
    base, ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin")
    users = page.get_by_test_id("clinic-v8-view-users")
    users.get_by_role("button", name="Fachrechte", exact=True).click()
    users.get_by_label("Benutzer", exact=True).select_option(str(ids["typist"]))
    rights = users.get_by_role("region", name="Fachrechte", exact=True)
    audio = rights.get_by_role("checkbox", name="Originalaudio exportieren", exact=True)
    expect(audio).to_be_visible()
    audio.check()
    expect(rights.get_by_role("checkbox", name="Exportieren", exact=True)).not_to_be_checked()
    endpoint = f"/api/clinic/v1/identity/users/{ids['typist']}/grants"
    with page.expect_response(lambda r: r.url.endswith(endpoint) and r.request.method == "POST") as applied:
        rights.get_by_role("button", name="Speichern").click()
    assert applied.value.status == 200
    assert applied.value.request.post_data_json["actions"] == ["audio_export"]
    row = rights.locator("ul[aria-labelledby=grants-existing] li", has_text="Originalaudio exportieren")
    expect(row).to_be_visible()
    snapshot = page.request.get(base + endpoint + "?entries=1").json()
    grant, = snapshot["items"]
    assert grant["actions"] == ["audio_export"] and grant["scope"] == "own"
    page.screenshot(path=tmp_path / "grant-original-audio.png")
    with page.expect_response(lambda r: r.url.endswith(endpoint + "/" + grant["id"]) and r.request.method == "DELETE") as removed:
        row.get_by_role("button", name="Entziehen").click()
    assert removed.value.status == 200
    expect(row).to_have_count(0)
    assert page.request.get(base + endpoint).json()["items"] == []
    assert not js_errors
