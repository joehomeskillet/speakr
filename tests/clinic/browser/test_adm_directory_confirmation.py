"""PAR-ADM-009 in the real admin mount with a synthetic, versioned test provider."""

import os
import re
import sqlite3
from pathlib import Path

import pytest
from playwright.sync_api import expect

import v8_browser_support as support

DIRECTORY = "/api/clinic/v1/directory/"
SEED = r'''
from pathlib import Path
from src.clinic.directory_diff import DirectoryMembership, DirectorySnapshot
from src.clinic.directory_provider import PROVIDER_EXTENSION
from src.clinic.directory_reconcile import DirectoryEntry
from src.clinic.entra import ClinicExternalIdentity

with app.app_context():
    admin = User.query.filter_by(username="admin-v860").one()
    author = User.query.filter_by(username="author-v860").one()
    typist = User.query.filter_by(username="typist-v860").one()
    pool = ClinicAuthorPool.query.filter_by(name="Autoren V860").one()
    db.session.get(identity.ClinicAccountPolicy, admin.id).is_identity_admin = True
    author.name = "Alter Anzeigename"
    for person, subject in ((author, "directory-author"), (typist, "directory-typist")):
        db.session.add(ClinicExternalIdentity(
            user_id=person.id, issuer=identity.DIRECTORY_ISSUER, tenant_id=identity.DIRECTORY_SOURCE,
            subject=subject, object_id=subject, status="active", created_by=admin.id))
    db.session.commit()
    current = pool.id
    replacement = pools.create_author_pool("Ersatzgruppe", actor_id=admin.id).id
    local_only = pools.create_author_pool("Interne Gruppe", actor_id=admin.id).id
    pools.add_author_member(current, typist.id, actor_id=admin.id)
    pools.add_author_member(local_only, typist.id, actor_id=admin.id)

snapshot = DirectorySnapshot(identity.DIRECTORY_SOURCE, "test-r1", (
    DirectoryEntry("directory-author", "dir-author", "Neuer Anzeigename", "neu@example.test", True),
    DirectoryEntry("directory-typist", "dir-typist", "typist-v860", None, True),
    DirectoryEntry("directory-new", "dir-new", "Neue Person", None, True),
    DirectoryEntry("directory-other", "dir-other", "Zweite Person", None, True),
), (DirectoryMembership("directory-author", "author", replacement),))
mode = Path(app.config["UPLOAD_FOLDER"]).with_name("directory-mode")

def test_provider():
    value = mode.read_text() if mode.exists() else "rows"
    if value == "empty":
        return DirectorySnapshot(identity.DIRECTORY_SOURCE, "test-r2", ())
    return snapshot if value == "rows" else None

app.extensions[PROVIDER_EXTENSION] = test_provider
'''


@pytest.fixture
def directory_world(tmp_path):
    server = support.SERVER.replace('server = make_server(', SEED + '\nserver = make_server(')
    with support.serve_app(tmp_path, server) as base:
        yield base, tmp_path


def _state(path):
    with sqlite3.connect(path / "v860.db") as db:
        return {
            "accounts": db.execute('SELECT username, name, email FROM user ORDER BY id').fetchall(),
            "links": db.execute('SELECT subject, user_id FROM clinic_external_identity ORDER BY subject').fetchall(),
            "members": db.execute('''
                SELECT u.username, p.name FROM clinic_author_pool_member m
                JOIN user u ON u.id = m.user_id JOIN clinic_author_pool p ON p.id = m.pool_id
                ORDER BY u.username, p.name
            ''').fetchall(),
            "policies": db.execute('SELECT user_id, status FROM clinic_account_policy ORDER BY user_id').fetchall(),
        }


def _open(page, base):
    page.goto(base + "/admin#/verzeichnis")
    view = page.get_by_test_id("clinic-view-directory")
    expect(view.get_by_text("WG-ADM-009 offen", exact=False)).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    return view


def _scope(view):
    view.get_by_role("checkbox", name="Diktiergruppe Autoren V860").check()
    view.get_by_role("checkbox", name="Diktiergruppe Ersatzgruppe").check()
    expect(view.get_by_role("checkbox", name="Diktiergruppe Interne Gruppe")).not_to_be_checked()
    view.get_by_role("button", name="Vorschau laden").click()


def _new_link(view):
    return view.get_by_role("button", name="Appkonto anlegen für Neue Person (dir-new)")


def _changes(view):
    email = view.get_by_role("checkbox", name="E-Mail-Adresse", exact=False)
    expect(email).not_to_be_checked()
    expect(email).to_have_accessible_name(
        "E-Mail-Adresse für author-v860 von author-v860@local.test auf neu@example.test ändern")
    view.get_by_role("checkbox", name="Anzeigename", exact=False).uncheck()
    add = view.get_by_role("checkbox", name="author-v860 zu Diktiergruppe Ersatzgruppe hinzufügen")
    email.press("Space")
    expect(email).to_be_checked()
    return add


def test_directory_confirmations_write_only_selected_entries(browser, directory_world):
    base, path = directory_world
    storage = support.login(browser, base, support.ROLES["admin"])
    with browser.new_context(storage_state=storage) as context:
        page = context.new_page()
        writes = []
        errors = []
        page.on("request", lambda req: writes.append(req.post_data_json)
                if req.method == "POST" and req.url.endswith((DIRECTORY + "apply", DIRECTORY + "link-new")) else None)
        page.on("pageerror", lambda error: errors.append(str(error)))
        view = _open(page, base)
        before = _state(path)
        _scope(view)
        _new_link(view).click()
        dialog = view.get_by_role("alertdialog", name="Kontoanlage bestätigen")
        expect(dialog.get_by_role("heading")).to_be_focused()
        expect(dialog.get_by_text("Für Neue Person (dir-new)", exact=False)).to_be_visible()
        page.keyboard.press("Tab")
        expect(dialog.get_by_role("button", name="Abbrechen")).to_be_focused()
        dialog.get_by_role("button", name="Abbrechen").click()
        expect(_new_link(view)).to_be_focused()
        assert writes == [] and _state(path) == before

        replacement = _changes(view)
        review = view.get_by_role("button", name="Auswahl prüfen")
        review.click()
        dialog = view.get_by_role("alertdialog", name="Änderungen bestätigen")
        expect(dialog.get_by_text("verliert die letzte Zuordnung", exact=False)).to_have_count(0)
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        expect(review).to_be_focused()
        assert writes == [] and _state(path) == before

        replacement.uncheck()
        view.get_by_role("checkbox", name="typist-v860 aus Diktiergruppe Autoren V860 entfernen").uncheck()
        review.click()
        expect(dialog.get_by_text("verliert die letzte Zuordnung", exact=False)).to_have_text(
            "author-v860 verliert die letzte Zuordnung zu einer Diktiergruppe.")
        expect(dialog.get_by_role("listitem")).to_have_count(2)
        expect(dialog.get_by_text("neu@example.test", exact=False)).to_be_visible()
        with page.expect_response(lambda response: response.url.endswith(DIRECTORY + "apply")) as applied:
            dialog.get_by_role("button", name="Auswahl übernehmen").click()
        assert applied.value.status == 200
        expect(view.get_by_text("Die gewählten Änderungen wurden übernommen.")).to_be_visible()
        after = _state(path)
        assert after["links"] == before["links"] and after["policies"] == before["policies"]
        assert after["members"] == [row for row in before["members"] if row[0] != "author-v860"]
        assert after["accounts"] == [
            (username, name, "neu@example.test" if username == "author-v860" else email)
            for username, name, email in before["accounts"]
        ]
        assert len(writes) == 1
        assert writes[0]["actions"] == [{"kind": "update_email", "object_id": "directory-author"}]
        assert [(m["kind"], m["subject_id"]) for m in writes[0]["memberships"]] == [("remove", "directory-author")]

        view.get_by_role("button", name="Vorschau laden").click()
        _new_link(view).click()
        dialog = view.get_by_role("alertdialog", name="Kontoanlage bestätigen")
        confirm = dialog.get_by_role("button", name="Anlegen und verknüpfen")
        with page.expect_response(lambda response: response.url.endswith(DIRECTORY + "link-new")) as linked:
            confirm.evaluate("button => { button.click(); button.click(); }")
        assert linked.value.status == 201
        expect(view.get_by_text("Das Appkonto wurde angelegt und verknüpft.", exact=False)).to_be_visible()
        final = _state(path)
        new_id = linked.value.json()["user_id"]
        assert set(final["links"]) - set(after["links"]) == {("directory-new", new_id)}
        assert set(final["policies"]) - set(after["policies"]) == {(new_id, "pending_activation")}
        assert len(final["accounts"]) == len(after["accounts"]) + 1
        assert final["members"] == after["members"]
        assert len(writes) == 2
        assert writes[1]["object_id"] == "directory-new"
        assert writes[1]["managed_pools"] == writes[0]["managed_pools"]
        assert not errors, errors


def _capture(page, view, out, state, size, theme):
    assert not support.horizontal_overflow(page)["document"]
    for target in view.locator("button:visible, .cadm-check:visible").all():
        assert target.bounding_box()["height"] >= 48, target.inner_text()
    page.screenshot(path=out / f"directory-{state}-{size}-{theme}.png", full_page=True)
    if size == "390x844" and theme == "light":
        html = page.evaluate('''staticRoot => {
            const root = document.documentElement.cloneNode(true);
            root.querySelectorAll('script, input[type="hidden"], meta[name*="csrf"]').forEach(node => node.remove());
            for (const node of root.querySelectorAll('link[href]')) {
                const path = new URL(node.getAttribute('href'), location.href).pathname;
                if (path.startsWith('/static/')) node.setAttribute('href', staticRoot + path);
                else node.remove();
            }
            for (const node of root.querySelectorAll('img[src]')) {
                const path = new URL(node.getAttribute('src'), location.href).pathname;
                if (path.startsWith('/static/')) node.setAttribute('src', staticRoot + path);
                const srcset = node.getAttribute('srcset');
                if (srcset) node.setAttribute('srcset', srcset.replaceAll('/static/', staticRoot + '/static/'));
            }
            const dialog = root.querySelector('.clinic-directory-review dialog[open]');
            if (dialog) {
                dialog.removeAttribute('open');
                const script = document.createElement('script');
                script.textContent = "document.querySelector('.clinic-directory-review dialog').showModal()";
                root.querySelector('body').append(script);
            }
            return '<!doctype html>\\n' + root.outerHTML;
        }''', os.path.relpath(support.ROOT, out.resolve()))
        (out / f"directory-{state}.html").write_text(html, encoding="utf-8")


def test_directory_visual_states(browser, directory_world):
    base, path = directory_world
    storage = support.login(browser, base, support.ROLES["admin"])
    evidence = os.environ.get("V8_BROWSER_OUT")
    out = Path(evidence) if evidence else path
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            with browser.new_context(storage_state=storage, viewport={"width": width, "height": height},
                                     **support.theme_context_options(theme)) as context:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                mode = path / "directory-mode"
                mode.write_text("rows")
                view = _open(page, base)
                if theme == "dark":
                    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
                else:
                    expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
                _scope(view)
                _changes(view).uncheck()
                _capture(page, view, out, "rows", size, theme)
                _new_link(view).click()
                expect(view.get_by_role("alertdialog", name="Kontoanlage bestätigen")).to_be_visible()
                _capture(page, view, out, "link", size, theme)
                page.keyboard.press("Escape")
                view.get_by_role("button", name="Auswahl prüfen").click()
                expect(view.get_by_text("verliert die letzte Zuordnung", exact=False)).to_be_visible()
                _capture(page, view, out, "confirmation", size, theme)
                page.keyboard.press("Escape")
                mode.write_text("empty")
                view.get_by_role("button", name="Vorschau laden").click()
                expect(view.get_by_text("Keine Änderungen.", exact=True)).to_be_visible()
                _capture(page, view, out, "empty", size, theme)
                mode.write_text("unavailable")
                page.reload()
                expect(view.get_by_text("Der Verzeichnisabgleich ist nicht aktiviert.")).to_be_visible()
                _capture(page, view, out, "unavailable", size, theme)
