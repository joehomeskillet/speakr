"""Use P18, reload it and reread the committed import-source version from the real server."""
import sqlite3
from uuid import uuid4

from playwright.sync_api import expect

import v8_browser_support as support


SOURCE = """
    from src.clinic import import_source_config
    import_source_config.create_source_config(actor_user_id=admin.id, source_kind="device",
        source_ref="test-recorder", approved_root="/approved/synthetic",
        target_author_user_id=admin.id)
"""


def test_change_existing_import_routing(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/approved/synthetic")
    server = support.SERVER.replace("    db.session.commit()\nserver =", SOURCE + "\n    db.session.commit()\nserver =")
    with support.serve_app(tmp_path, server) as base:
        admin = browser.new_context(storage_state=support.login(browser, base, support.ROLES["admin"]))
        page = admin.new_page()
        api = base + "/api/clinic/v1/imports/sources"
        before = admin.request.get(api).json()
        item, = before["items"]
        assert "approved_root" not in item
        assert (item["autosend_enabled"], item["eol_filter_enabled"]) == (False, True)
        actor = next(a["id"] for a in before["authors"] if a["name"] == "author-v860")
        pool = next(p["id"] for p in before["pools"] if p["author_id"] == actor)
        support._open_shell_view(page, base, "#/import")
        edit = page.get_by_role("button", name="Gerät test-recorder zuordnen")
        page.get_by_text("Geräteautor am Philips LFH9600 oder LFH9620", exact=True).click()
        expect(page.get_by_role("link", name="Philips-Handbuch, Seite 22 (PDF, Englisch)")).to_be_visible()
        edit.click()
        author = page.get_by_label("Importautor")
        author.select_option(str(actor))
        page.get_by_label("Autorenpool").select_option(pool)
        with page.expect_response(lambda r: r.url == api + "/" + item["id"] and r.request.method == "PUT") as put:
            page.get_by_role("button", name="Speichern", exact=True).click()
        assert put.value.status == 200
        expect(page.get_by_role("status").filter(has_text="Importzuordnung gespeichert")).to_be_visible()
        page.reload()
        expect(page.get_by_text("Importautor · author-v860", exact=True)).to_be_visible()
        edit.click()
        expect(author).to_have_value(str(actor))
        expect(page.get_by_label("Autorenpool")).to_have_value(pool)
        updated, = admin.request.get(api).json()["items"]
        assert (updated["target_author_user_id"], updated["target_pool_id"], updated["policy_version"]) == (
            actor, pool, item["policy_version"] + 1)
        assert (updated["autosend_enabled"], updated["eol_filter_enabled"]) == (False, True)

        headers = {k: v for k, v in put.value.request.all_headers().items()
                   if k in {"x-csrf-token", "idempotency-key"}}
        body = put.value.request.post_data_json
        endpoint = api + "/" + item["id"]
        assert admin.request.put(endpoint, headers=headers, data=body).json() == updated
        headers["idempotency-key"] = str(uuid4())
        assert admin.request.put(endpoint, headers=headers, data=body).status == 409
        assert admin.request.put(endpoint, headers=headers, data={**body, "approved_root": "/extra"}).status == 400
        assert admin.request.get(api).json()["items"] == [updated]
        member = browser.new_context(storage_state=support.login(browser, base, support.ROLES["author"]))
        assert member.request.get(api).status == 403
        headers["x-csrf-token"] = member.request.get(base + "/api/csrf-token").json()["csrf_token"]
        assert member.request.put(endpoint, headers=headers, data=body).status == 403
        headers["idempotency-key"] = str(uuid4())
        denied = {"expected_version": updated["policy_version"], "autosend_enabled": True,
                  "eol_filter_enabled": False}
        assert member.request.put(endpoint, headers=headers, data=denied).status == 403
        member.close()
        with sqlite3.connect(tmp_path / "v860.db") as conn:
            [(target,)] = conn.execute("select target_user_id from audit_event where event = ?",
                                       ("admin.setting.changed",))
            assert target == actor
        headers["x-csrf-token"] = admin.request.get(base + "/api/csrf-token").json()["csrf_token"]
        headers["idempotency-key"] = str(uuid4())
        toggled = admin.request.put(endpoint, headers=headers, data=denied).json()
        assert (toggled["autosend_enabled"], toggled["eol_filter_enabled"], toggled["policy_version"]) == (
            True, False, updated["policy_version"] + 1)
        assert (toggled["target_author_user_id"], toggled["target_pool_id"]) == (actor, pool)
        page.reload()
        expect(page.get_by_text("Importautor · author-v860", exact=True)).to_be_visible()
        reloaded, = admin.request.get(api).json()["items"]
        assert reloaded == toggled
        headers["idempotency-key"] = str(uuid4())
        combined = {**body, "expected_version": reloaded["policy_version"],
                    "autosend_enabled": False, "eol_filter_enabled": True}
        restored = admin.request.put(endpoint, headers=headers, data=combined).json()
        assert (restored["autosend_enabled"], restored["eol_filter_enabled"]) == (False, True)
        assert restored["policy_version"] == reloaded["policy_version"] + 1
        headers["idempotency-key"] = str(uuid4())
        assert admin.request.put(endpoint, headers=headers, data={
            "expected_version": restored["policy_version"], "autosend_enabled": "yes",
            "eol_filter_enabled": False}).status == 400
        assert admin.request.get(api).json()["items"] == [restored]
        with sqlite3.connect(tmp_path / "v860.db") as conn:
            targets = [row[0] for row in conn.execute(
                "select target_user_id from audit_event where event = ?", ("admin.setting.changed",))]
            assert targets == [actor, actor, actor]
        admin.close()
