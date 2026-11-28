"""Use P18, reload it and reread the committed import-source version from the real server."""
import os
import re
import sqlite3
from pathlib import Path
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


def test_change_import_options_independently(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/approved/synthetic")
    server = support.SERVER.replace("    db.session.commit()\nserver =", SOURCE + "\n    db.session.commit()\nserver =")
    with support.serve_app(tmp_path, server) as base:
        api = base + "/api/clinic/v1/imports/sources"
        with browser.new_context(storage_state=support.login(browser, base, support.ROLES["admin"])) as admin:
            page = admin.new_page()
            original, = admin.request.get(api).json()["items"]
            endpoint = api + "/" + original["id"]
            support._open_shell_view(page, base, "#/import")
            edit = page.get_by_role("button", name="Gerät test-recorder zuordnen")
            save = page.get_by_role("button", name="Speichern", exact=True)
            auto = page.get_by_role("checkbox", name="Automatisch versenden")
            eol = page.get_by_role("checkbox", name="Nur beendete Diktate (EOL)")
            edit.click()
            expect(auto).not_to_be_checked()
            expect(eol).to_be_checked()
            eol.uncheck()
            page.get_by_role("button", name="Abbrechen", exact=True).click()
            edit.click()
            expect(eol).to_be_checked()
            save.click()
            expect(page.get_by_role("status").filter(has_text="Keine Änderungen zum Speichern")).to_be_visible()

            version = original["policy_version"]
            for checkbox, key, checked in ((eol, "eol_filter_enabled", False), (auto, "autosend_enabled", True)):
                checkbox.set_checked(checked)
                with page.expect_response(lambda r: r.url == endpoint and r.request.method == "PUT") as put:
                    save.click()
                assert put.value.status == 200
                assert put.value.request.post_data_json == {"expected_version": version, key: checked}
                version += 1
                updated = put.value.json()
                assert updated["policy_version"] == version
                for routing_key in ("target_author_user_id", "target_pool_id"):
                    assert updated[routing_key] == original[routing_key]
                expect(page.get_by_role("status").filter(has_text="Importzuordnung gespeichert")).to_be_visible()
                page.reload()
                edit.click()
                expect(eol).not_to_be_checked()
                expect(auto).to_be_checked(checked=key == "autosend_enabled")

            headers = {k: v for k, v in put.value.request.all_headers().items() if k == "x-csrf-token"}
            headers["idempotency-key"] = str(uuid4())
            assert admin.request.put(endpoint, headers=headers, data={
                "expected_version": version, "autosend_enabled": False}).status == 200
            eol.check()
            with page.expect_response(lambda r: r.url == endpoint and r.request.method == "PUT") as stale:
                save.click()
            assert stale.value.status == 409
            expect(page.get_by_role("status").filter(has_text="inzwischen geändert")).to_be_visible()
            edit.click()
            expect(auto).not_to_be_checked()
            expect(eol).not_to_be_checked()

        with browser.new_context(storage_state=support.login(browser, base, support.ROLES["author"])) as member:
            page = member.new_page()
            with page.expect_response(lambda r: r.url == api) as denied:
                support._open_shell_view(page, base, "#/import")
            assert denied.value.status == 403
            expect(page.get_by_role("heading", name="Importzuordnung")).to_have_count(0)


def test_import_inventory_badges_and_visual_matrix(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/approved/synthetic")
    source = support.SERVER.replace("    db.session.commit()\nserver =", SOURCE + "\n    db.session.commit()\nserver =")
    labels = {"philips_eol": "EOL", "file_stability": "EOL nicht belegt", "unknown": "EOL unbekannt"}
    rows = [{"source_id": "test-recorder", "relative_path": f"Diktat-{i}.dss", "size": 1024,
             "snapshot_version": f"s{i}", "mtime_utc": "2026-11-20T07:00:00Z", "completion_source": provenance}
            for i, provenance in enumerate(labels, start=1)]
    output = os.environ.get("P217_ARTIFACTS")
    sizes = support.VIEWPORTS if output else {"390x844": support.VIEWPORTS["390x844"]}
    with support.serve_app(tmp_path, source) as base:
        state = support.login(browser, base, support.ROLES["admin"])
        for size, (width, height) in sizes.items():
            for theme in support.THEMES if output else ("light",):
                with browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                         **support.theme_context_options(theme)) as context:
                    context.add_init_script(support.theme_init_script(theme))
                    context.route("**/api/clinic/v1/imports/inventory*", lambda route: route.fulfill(json={"items": rows}))
                    page = context.new_page()
                    support._open_shell_view(page, base, "#/import")
                    expect(page.locator("html")).to_have_class(
                        re.compile(r"\bdark\b" if theme == "dark" else r"^(?!.*\bdark\b)"))
                    table = page.get_by_test_id("inventory-list")
                    for item in rows:
                        row = table.get_by_role("row").filter(has_text=item["relative_path"])
                        badge = row.get_by_text(labels[item["completion_source"]], exact=True)
                        expect(badge).to_be_visible()
                        box = badge.bounding_box()
                        assert box["x"] + box["width"] <= width
                    assert table.evaluate("el => el.scrollWidth <= el.parentElement.clientWidth + 1")
                    page.get_by_role("button", name="Gerät test-recorder zuordnen").click()
                    for label in ("Automatisch versenden", "Nur beendete Diktate (EOL)"):
                        control = page.get_by_text(label, exact=True)
                        assert control.bounding_box()["height"] >= 48
                    _capture(page, output, f"import-{size}-{theme}-rows")
                    context.unroute("**/api/clinic/v1/imports/inventory*")
                    context.route("**/api/clinic/v1/imports/sources", lambda route: route.fulfill(
                        json={"items": [], "authors": [], "pools": []}))
                    page.reload()
                    expect(page.get_by_test_id("inventory-empty")).to_be_visible()
                    expect(page.get_by_text("Keine lokal freigegebene Quelle zur Verwaltung vorhanden.")).to_be_visible()
                    _capture(page, output, f"import-{size}-{theme}-empty")


def _capture(page, output, stem):
    assert not support.horizontal_overflow(page)["document"]
    if output:
        path = Path(output)
        support._wait_loading_overlay_gone(page)
        html = page.evaluate('''() => {
            const snapshot = document.documentElement.cloneNode(true);
            snapshot.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
            snapshot.querySelectorAll('link[href^="/static/"], img[src^="/static/"]').forEach(el => {
                const attr = el.tagName === 'LINK' ? 'href' : 'src';
                el.setAttribute(attr, '../..' + el.getAttribute(attr));
            });
            const checked = Array.from(document.querySelectorAll('input[type="checkbox"]'), el => el.checked);
            snapshot.querySelectorAll('input[type="checkbox"]').forEach((el, i) => el.toggleAttribute('checked', checked[i]));
            return '<!doctype html>\\n' + snapshot.outerHTML;
        }''')
        (path / f"{stem}.html").write_text(html, encoding="utf-8")
        pane = page.get_by_test_id("clinic-menu-view")
        page.add_style_tag(content='html, body, #app { height: auto !important; overflow: visible !important; }')
        pane.evaluate("el => { el.style.overflow = 'visible' }")
        page.screenshot(path=path / f"{stem}.png", full_page=True, animations="disabled", caret="hide")
