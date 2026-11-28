"""PAR-ADM-001: account-note editing through the mounted administration UI."""

import os
from pathlib import Path

from playwright.sync_api import expect

import v8_browser_support as support

ORIGINAL_NOTE = "Ursprüngliche Anmerkung für Alpha"
SAVED_NOTE = "Neue Anmerkung für Alpha\nZweite Zeile <synthetisch>"
SHOTS = os.environ.get("P118_ARTIFACTS")


def _replace(source, old, new):
    result = source.replace(old, new, 1)
    assert result != source
    return result


def _server_source():
    source = _replace(
        support.SERVER,
        "from src.clinic.revisions import add_asr_revision",
        "from src.clinic.revisions import add_asr_revision\nfrom src.clinic.presets import set_preset",
    )
    source = _replace(
        source,
        'db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only"))',
        'db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only", '
        'is_identity_admin=admin))',
    )
    source = _replace(source, 'user("author-v860", emails["author"])', 'user("Alpha", emails["author"])')
    membership = 'db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))'
    return _replace(
        source,
        membership,
        membership + f'\n    set_preset("account_note", {ORIGINAL_NOTE!r}, scope="user", scope_id=author.id)',
    )


def _open_accounts(page, base):
    page.goto(base + "/admin#/benutzer")
    view = page.get_by_test_id("clinic-users-view")
    expect(view.get_by_role("heading", name="Benutzer und Rollen", exact=True)).to_be_visible(
        timeout=support.SHOW_TIMEOUT_MS
    )
    expect(view.get_by_role("table")).to_contain_text("Alpha", timeout=support.SHOW_TIMEOUT_MS)
    return view


def _open_editor(view):
    expect(view.get_by_role("table")).to_contain_text("Alpha", timeout=support.SHOW_TIMEOUT_MS)
    edit = view.get_by_role("button", name="Bearbeiten: Alpha", exact=True)
    if not edit.is_visible():
        view.get_by_role("button", name="Kontodetails: Alpha", exact=True).click()
    expect(edit).to_be_visible()
    edit.click()
    note = view.get_by_label("Anmerkung", exact=True)
    expect(note).to_be_visible()
    return note


def _shoot(browser, state, base):
    if not SHOTS:
        return
    out = Path(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            context = browser.new_context(
                storage_state=state,
                viewport={"width": width, "height": height},
                **support.theme_context_options(theme),
            )
            try:
                context.add_init_script(support.theme_init_script(theme))
                for label in ("rows", "empty"):
                    page = context.new_page()
                    try:
                        view = _open_accounts(page, base)
                        if label == "rows":
                            note = _open_editor(view)
                            expect(note).to_have_value(SAVED_NOTE)
                            view.locator(".cadm-editor").scroll_into_view_if_needed()
                        else:
                            view.get_by_label("Suche", exact=True).fill("Kein Konto P118")
                            expect(view.get_by_text("Keine passenden Konten.", exact=True)).to_be_visible()
                            expect(view.get_by_role("table")).to_have_count(0)
                        is_dark = page.locator("html").evaluate("element => element.classList.contains('dark')")
                        assert is_dark is (theme == "dark")
                        page.screenshot(path=str(out / f"account-note-{label}-{size}-{theme}.png"), full_page=True)
                        if label == "rows":
                            view.locator(".cadm-form__actions").scroll_into_view_if_needed()
                            page.screenshot(path=str(out / f"account-note-actions-{size}-{theme}.png"), full_page=True)
                    finally:
                        page.close()
            finally:
                context.close()


def test_cancel_save_reopen_and_separate_disable_confirmation(browser, tmp_path):
    with support.serve_app(tmp_path, _server_source()) as base:
        state = support.login(browser, base, support.ROLES["admin"])
        context = browser.new_context(storage_state=state, viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            writes = []
            page.on(
                "request",
                lambda request: writes.append(request)
                if request.method == "PUT" and "/admin/users/" in request.url
                else None,
            )
            view = _open_accounts(page, base)
            note = _open_editor(view)
            expect(note).to_have_value(ORIGINAL_NOTE)
            note.fill("Diese Änderung wird verworfen")
            view.get_by_role("button", name="Abbrechen", exact=True).click()
            expect(view.get_by_label("Anmerkung", exact=True)).to_have_count(0)
            assert writes == []

            note = _open_editor(view)
            expect(note).to_have_value(ORIGINAL_NOTE)
            note.fill(SAVED_NOTE)
            with page.expect_response(
                lambda response: response.request.method == "PUT" and "/admin/users/" in response.url
            ) as saved:
                view.get_by_role("button", name="Speichern und schließen", exact=True).click()
            assert saved.value.ok
            assert saved.value.request.post_data_json["account_note"] == SAVED_NOTE
            assert "status" not in saved.value.request.post_data_json

            note = _open_editor(view)
            expect(note).to_have_value(SAVED_NOTE)
            expect(view.locator(".cadm-editor").get_by_role("button", name="Deaktivieren")).to_have_count(0)
            view.get_by_role("button", name="Abbrechen", exact=True).click()
            page.reload()
            view = page.get_by_test_id("clinic-users-view")
            note = _open_editor(view)
            expect(note).to_have_value(SAVED_NOTE, timeout=support.SHOW_TIMEOUT_MS)
            view.get_by_role("button", name="Abbrechen", exact=True).click()

            writes.clear()
            disable = view.get_by_role("button", name="Deaktivieren: Alpha", exact=True)
            disable.click()
            dialog = page.get_by_role("alertdialog", name="Konto deaktivieren", exact=True)
            expect(dialog).to_be_visible()
            expect(dialog).to_contain_text("Alpha")
            dialog.get_by_role("button", name="Abbrechen", exact=True).click()
            expect(dialog).to_have_count(0)
            assert writes == []

            disable.click()
            with page.expect_response(
                lambda response: response.request.method == "PUT" and "/admin/users/" in response.url
            ) as disabled:
                dialog.get_by_role("button", name="Deaktivieren", exact=True).click()
            assert disabled.value.ok
            assert disabled.value.request.post_data_json["status"] == "disabled"
            assert "account_note" not in disabled.value.request.post_data_json
            expect(view.get_by_role("button", name="Reaktivieren: Alpha", exact=True)).to_be_visible()

            _shoot(browser, state, base)
        finally:
            context.close()
