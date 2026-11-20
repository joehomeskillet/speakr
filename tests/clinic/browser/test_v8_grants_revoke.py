"""Admin changes a named pool grant while a second clinic session reads its audio revision."""

from playwright.sync_api import expect


def test_named_grant_and_revoke_change_next_protected_request(app_base, browser, role_states):
    admin = browser.new_context(storage_state=role_states["admin"])
    lead = browser.new_context(storage_state=role_states["lead"])
    author = browser.new_context(storage_state=role_states["author"])
    try:
        page = admin.new_page()
        page.goto(app_base + "/admin")
        page.get_by_test_id("admin-tab-clinic").click()
        page.get_by_test_id("clinic-panel-grants").click()
        view = page.get_by_test_id("clinic-view-grants")
        view.get_by_label("Benutzername").fill("lead-v860")
        view.get_by_role("button", name="Fachrechte laden").click()
        view.get_by_label("Geltungsbereich").select_option("pool")
        view.get_by_label("Autorenpool").select_option(label="Autoren V860")
        view.get_by_label("Abspielen").check()

        api = app_base + "/api/clinic/v1"
        row = author.request.get(api + "/recordings").json()["items"][0]
        audio = api + f"/dictations/{row['dictation_id']}/audio-revisions/current"
        assert lead.request.get(audio).status == 404
        with page.expect_response(lambda r: r.url.endswith("/grants") and r.request.method == "POST") as saved:
            view.get_by_role("button", name="Speichern").click()
        assert saved.value.status == 200
        revision = saved.value.json()["revision"]
        revoke = view.get_by_role("listitem").filter(has_text="Autoren V860").get_by_role("button", name="Entziehen")
        expect(revoke).to_be_enabled()
        assert lead.request.get(audio).status == 200

        with page.expect_response(lambda r: r.request.method == "DELETE") as removed:
            revoke.click()
        assert removed.value.status == 200
        assert removed.value.json()["revision"] == revision + 1
        expect(view.get_by_text("Keine Einträge")).to_be_visible()
        assert lead.request.get(audio).status == 404
    finally:
        author.close()
        lead.close()
        admin.close()
