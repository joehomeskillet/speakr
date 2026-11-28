"""Use P18, reload it and reread the committed import-source version from the real server."""
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect

import v8_browser_support as support

SHOTS = os.environ.get("P149_ARTIFACTS")


def _save_dom(page, path):
    # Keep the mounted DOM and local styles usable after the test server shuts down.
    static = os.path.relpath(support.ROOT / "static", path.parent)
    html = page.evaluate("""staticPath => {
        const root = document.documentElement.cloneNode(true);
        root.querySelectorAll('script, [name*=csrf]').forEach(node => node.remove());
        for (const node of root.querySelectorAll('link[href], img[src]')) {
            const attribute = node.tagName === 'LINK' ? 'href' : 'src';
            const url = new URL(node.getAttribute(attribute), location.href);
            if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
                node.setAttribute(attribute, staticPath + url.pathname.slice(7));
            }
        }
        for (const input of root.querySelectorAll('[data-testid="import-source-routing"] input')) {
            input.setAttribute('value', document.getElementById(input.id).value);
        }
        for (const option of root.querySelectorAll('[data-testid="import-source-routing"] option')) {
            const live = document.getElementById(option.closest('select').id);
            if (live) option.toggleAttribute('selected', live.options[option.index].selected);
        }
        return '<!doctype html>\\n' + root.outerHTML;
    }""", static)
    path.write_text(html, encoding="utf-8")


def _capture(browser, state, base, label, open_state=None):
    if not SHOTS:
        return
    out = Path(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            context = browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                          **support.theme_context_options(theme))
            try:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                support._open_shell_view(page, base, "#/import")
                expect(page.get_by_test_id("import-source-routing")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
                section = page.get_by_test_id("import-source-routing")
                if open_state:
                    open_state(section)
                page.mouse.move(1, 1)
                section.evaluate("node => node.scrollIntoView({block: 'start'})")
                bad_targets = section.locator("button:visible, input:visible, select:visible").evaluate_all("""controls =>
                    controls.filter(control => {
                        const rect = control.getBoundingClientRect();
                        return rect.width < 48 || rect.height < 48 || rect.left < 0 || rect.right > innerWidth;
                    }).map(control => control.id || control.textContent.trim())""")
                assert bad_targets == [], (label, size, theme, bad_targets)
                stem = f"import-dropfolders-{label}-{size}-{theme}"
                page.screenshot(path=str(out / f"{stem}.png"))
                submit = section.locator("button[type=submit]")
                if submit.count() and submit.evaluate("node => node.getBoundingClientRect().bottom > innerHeight"):
                    submit.evaluate("node => node.form.scrollIntoView({block: 'end'})")
                    page.screenshot(path=str(out / f"{stem}-actions.png"))
                if size == "1440x900" and theme == "light":
                    _save_dom(page, out / f"import-dropfolders-{label}.html")
            finally:
                context.close()


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


def test_dropfolder_create_edit_remove_lifecycle(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/approved/synthetic")
    with support.serve_app(tmp_path, support.SERVER) as base:
        member = browser.new_context(storage_state=support.login(browser, base, support.ROLES["author"]))
        member_page = member.new_page()
        support._open_shell_view(member_page, base, "#/import")
        expect(member_page.get_by_test_id("clinic-import-view")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
        expect(member_page.get_by_test_id("import-source-routing")).to_have_count(0)
        member.close()

        admin_state = support.login(browser, base, support.ROLES["admin"])
        _capture(browser, admin_state, base, "empty")
        _capture(browser, admin_state, base, "form", lambda section: section.get_by_test_id("add-dropfolder").click())

        admin = browser.new_context(storage_state=admin_state, viewport={"width": 1440, "height": 900})
        page = admin.new_page()
        api = base + "/api/clinic/v1/imports/sources"
        initial = admin.request.get(api).json()
        assert initial["items"] == []
        actor = next(a["id"] for a in initial["authors"] if a["name"] == "author-v860")
        pool = next(p["id"] for p in initial["pools"] if p["author_id"] == actor)

        support._open_shell_view(page, base, "#/import")
        routing = page.get_by_test_id("import-source-routing")
        expect(routing.get_by_test_id("source-routing-empty")).to_be_visible()

        routing.get_by_test_id("add-dropfolder").click()
        form = routing.get_by_test_id("dropfolder-create-form")
        expect(form).to_be_visible()

        # Disallowed root keeps the draft and shows an error without creating a row.
        expect(form.get_by_label("Kurzname")).to_be_focused()
        form.get_by_label("Kurzname").fill("labor-eingang")
        form.get_by_label("Ordnerpfad").fill("/outside/bad")
        form.get_by_label("Diktiert von").select_option(str(actor))
        form.get_by_label("Autorengruppe").select_option(pool)
        form.get_by_label("Dateimuster").fill("*.ds2")
        form.get_by_label("Priorität").select_option("128")
        form.get_by_label("Sprache", exact=True).select_option("1033")
        form.get_by_label("Spracherkennungsprofil (optional)").fill("profile-one")
        with page.expect_response(lambda r: r.url == api and r.request.method == "POST") as bad_post:
            form.get_by_test_id("save-new-dropfolder").click()
        assert bad_post.value.status == 403
        expect(routing.get_by_test_id("source-routing-error")).to_be_visible()
        expect(routing.get_by_test_id("source-routing-error")).to_be_focused()
        expect(form.get_by_label("Kurzname")).to_have_value("labor-eingang")
        expect(form.get_by_label("Dateimuster")).to_have_value("*.ds2")

        form.get_by_label("Ordnerpfad").fill("/approved/synthetic/eingang")
        with page.expect_response(lambda r: r.url == api and r.request.method == "POST") as created_post:
            form.get_by_test_id("save-new-dropfolder").click()
        assert created_post.value.status == 201
        created = created_post.value.json()
        expect(routing.get_by_test_id("source-routing-notice")).to_contain_text("Ordner angelegt")

        page.reload()
        routing = page.get_by_test_id("import-source-routing")
        details = routing.get_by_test_id("folder-details-labor-eingang")
        expect(details).to_be_visible()
        expect(details).to_contain_text("/approved/synthetic/eingang")
        expect(details).to_contain_text("Autoren V860")
        expect(details).to_contain_text("*.ds2")
        expect(details).to_contain_text("Dringend")
        expect(details).to_contain_text("Englisch")
        expect(details).to_contain_text("profile-one")
        expect(routing.get_by_text("Diktiert von · author-v860", exact=True)).to_be_visible()

        _capture(browser, admin_state, base, "rows")
        _capture(browser, admin_state, base, "edit", lambda section: section.get_by_role(
            "button", name="Ordner labor-eingang bearbeiten").click())
        _capture(browser, admin_state, base, "remove", lambda section: section.get_by_test_id("remove-dropfolder").click())

        # Duplicate source_ref retains the create draft on 409 conflict.
        routing.get_by_test_id("add-dropfolder").click()
        form = routing.get_by_test_id("dropfolder-create-form")
        form.get_by_label("Kurzname").fill("labor-eingang")
        form.get_by_label("Ordnerpfad").fill("/approved/synthetic/dup")
        form.get_by_label("Diktiert von").select_option(str(actor))
        with page.expect_response(lambda r: r.url == api and r.request.method == "POST") as dup_post:
            form.get_by_test_id("save-new-dropfolder").click()
        assert dup_post.value.status == 409
        expect(routing.get_by_test_id("source-routing-error")).to_contain_text("existiert bereits")
        expect(form.get_by_label("Kurzname")).to_have_value("labor-eingang")
        form.get_by_test_id("cancel-new-dropfolder").click()
        expect(routing.get_by_test_id("add-dropfolder")).to_be_focused()

        # Edit every folder field, clear pool and ASR profile, and verify persistence after reload.
        routing.get_by_role("button", name="Ordner labor-eingang bearbeiten").click()
        edit_form = routing.get_by_test_id("source-edit-form")
        edit_form.get_by_label("Ordnerpfad").fill("/approved/synthetic/neu")
        edit_form.get_by_label("Autorengruppe").select_option(label="Keine Gruppe vorgeben")
        edit_form.get_by_label("Dateimuster").fill("*.wav")
        edit_form.get_by_label("Priorität").select_option("0")
        edit_form.get_by_label("Sprache", exact=True).select_option("1031")
        edit_form.get_by_label("Spracherkennungsprofil (optional)").fill("")
        with page.expect_response(lambda r: r.url == api + "/" + created["id"] and r.request.method == "PUT") as put:
            edit_form.get_by_role("button", name="Speichern", exact=True).click()
        assert put.value.status == 200
        assert put.value.json()["policy_version"] == 2
        expect(routing.get_by_test_id("source-routing-notice")).to_contain_text("Ordnerkonfiguration gespeichert")

        page.reload()
        routing = page.get_by_test_id("import-source-routing")
        details = routing.get_by_test_id("folder-details-labor-eingang")
        expect(details).to_contain_text("/approved/synthetic/neu")
        expect(details).to_contain_text("Keine Gruppe vorgegeben")
        expect(details).to_contain_text("*.wav")
        expect(details).to_contain_text("Bei Gelegenheit")
        expect(details).to_contain_text("Deutsch")
        expect(details).to_contain_text("Standard")

        # Stale PUT refreshes the row and shows a conflict notice.
        routing.get_by_role("button", name="Ordner labor-eingang bearbeiten").click()
        csrf = admin.request.get(base + "/api/csrf-token").json()["csrf_token"]
        assert admin.request.put(
            api + "/" + created["id"],
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())},
            data={"expected_version": 2, "file_pattern": "*.ogg"},
        ).status == 200
        edit_form = routing.get_by_test_id("source-edit-form")
        edit_form.get_by_label("Dateimuster").fill("*.mp3")
        with page.expect_response(lambda r: r.url == api + "/" + created["id"] and r.request.method == "PUT") as stale_put:
            edit_form.get_by_role("button", name="Speichern", exact=True).click()
        assert stale_put.value.status == 409
        expect(routing.get_by_test_id("source-routing-error")).to_contain_text("inzwischen geändert")
        expect(routing.get_by_test_id("folder-details-labor-eingang")).to_contain_text("*.ogg")

        # Cancel removal keeps the folder; confirmed removal deletes it and persists across reload.
        routing.get_by_test_id("remove-dropfolder").click()
        expect(routing.get_by_test_id("confirm-remove-box")).to_be_visible()
        expect(routing.get_by_test_id("cancel-remove-dropfolder")).to_be_focused()
        routing.get_by_test_id("cancel-remove-dropfolder").click()
        expect(routing.get_by_test_id("confirm-remove-box")).to_have_count(0)
        expect(routing.get_by_test_id("remove-dropfolder")).to_be_focused()
        expect(routing.get_by_test_id("folder-details-labor-eingang")).to_be_visible()

        routing.get_by_test_id("remove-dropfolder").click()
        with page.expect_response(lambda r: r.url == api + "/" + created["id"] and r.request.method == "DELETE") as deleted:
            routing.get_by_test_id("confirm-remove-dropfolder").click()
        assert deleted.value.status == 204
        expect(routing.get_by_test_id("source-routing-notice")).to_contain_text("Ordnerkonfiguration entfernt")
        expect(routing.get_by_test_id("source-routing-empty")).to_be_visible()

        page.reload()
        expect(page.get_by_test_id("import-source-routing").get_by_test_id("source-routing-empty")).to_be_visible()
        assert admin.request.get(api).json()["items"] == []
        admin.close()
