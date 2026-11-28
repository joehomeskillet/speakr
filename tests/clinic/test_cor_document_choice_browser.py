from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from test_wp_mount_correction import SERVER, TYPIST, _launch, _live_correction_server, _login, _open_from_worklist

FOREIGN_ID = "f1000000-0000-4000-8000-000000000064"
SEED = '''
    from src.clinic.presets import save_template
    foreign = save_template("Fremde Vorlage", b"Vertraulich", scope="user", scope_id=stranger.id, mime_type="text/plain")
    foreign.id = "f1000000-0000-4000-8000-000000000064"
    if WITH_ROWS:
        save_template("Vorlage der Autorin", b"Befund: [[CURSOR]] Schluss", scope="user",
                      scope_id=author.id, mime_type="text/plain", insertion_mark="[[CURSOR]]")
        save_template("Vorlage des Teams", b"Teamtext", scope="pool", scope_id=pool.id, mime_type="text/plain")
    db.session.commit()
'''


def capture(page: Page, state: str) -> None:
    if not (target := os.environ.get("P064_ARTIFACTS")):
        return
    dest = Path(target)
    dest.mkdir(parents=True, exist_ok=True)
    view = page.get_by_test_id("toolbar" if state == "editor-closed" else "template-menu")
    for theme in ("light", "dark"):
        page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", theme == "dark")
        for width, height in ((1440, 900), (1280, 720), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            view.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
            assert view.evaluate("el => el.scrollWidth === el.clientWidth")
            targets = view.locator(".button, .snippet-option") if state != "editor-closed" else view.locator("[data-tool=template]")
            for target in targets.all():
                assert target.evaluate("el => el.offsetWidth >= 48 && el.offsetHeight >= 48")
            label = f"{state}-{width}x{height}-{theme}"
            page.screenshot(path=dest / f"{label}.png", animations="disabled")
            html = page.evaluate('''async () => {
                const root = document.documentElement.cloneNode(true);
                for (const link of root.querySelectorAll('link[rel=stylesheet]')) {
                    const response = await fetch(link.href);
                    if (!response.ok) continue;
                    const style = document.createElement('style');
                    style.textContent = await response.text();
                    const tokensImport = "@import './tokens.css';";
                    if (style.textContent.includes(tokensImport)) {
                        const tokens = await fetch(new URL('tokens.css', link.href).href);
                        if (tokens.ok) {
                            const content = await tokens.text();
                            style.textContent = style.textContent.replace(tokensImport, () => content);
                        }
                    }
                    link.replaceWith(style);
                }
                root.querySelectorAll('script, meta[name="csrf-token"], input[name="csrf_token"]').forEach(el => el.remove());
                const source = document.querySelector('[data-testid=text]');
                root.querySelector('[data-testid=text]').textContent = source.value;
                return '<!DOCTYPE html>' + root.outerHTML;
            }''')
            (dest / f"{label}.html").write_text(html, encoding="utf-8")


@pytest.mark.parametrize("with_rows", [True, False], ids=["author-and-team", "no-templates"])
def test_case_document_choice(tmp_path: Path, with_rows: bool) -> None:
    seed = SEED.replace("WITH_ROWS", str(with_rows))
    source = SERVER.replace('\nserver = make_server', seed + '\nserver = make_server')
    with _live_correction_server(tmp_path, source) as (base, did), sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, TYPIST)
            _open_from_worklist(page, base)
            editor = page.get_by_test_id("text")
            page.locator("[data-action=acquire]").click()
            expect(editor).to_be_editable()
            editor.evaluate("el => el.setSelectionRange(0, 8)")
            original = editor.input_value()
            correction = f"{base}/api/clinic/v1/dictations/{did}/correction"
            before = page.request.get(correction).json()
            workspace = page.locator(".editor-workspace")
            lease = page.get_by_test_id("lease-bar")
            if with_rows:
                capture(page, "editor-closed")
            lease_label = lease.inner_text()
            seen = len(page.responses)
            trigger = page.get_by_role("button", name="Vorlage oder Datei einfügen")
            trigger.click()
            menu = page.get_by_test_id("template-menu")
            expect(menu.get_by_role("heading", level=3)).to_be_focused()
            expect(menu.get_by_role("button", name="Fremde Vorlage")).to_have_count(0)
            foreign_url = f"{base}/api/clinic/v1/dictations/{did}/templates/{FOREIGN_ID}/preview"
            assert page.request.get(foreign_url).status == 404

            def choose() -> tuple[str, int]:
                if with_rows:
                    expect(menu.get_by_role("button", name="Vorlage des Teams")).to_be_visible()
                    menu.get_by_role("button", name="Vorlage der Autorin").click()
                    return "Befund:  Schluss", 8
                expect(menu.get_by_text("Für dieses Diktat sind keine Vorlagen verfügbar.")).to_be_visible()
                menu.get_by_label("Datei auswählen").set_input_files({
                    "name": "probe.rtf", "mimeType": "application/rtf", "buffer": b"{\\rtf1 \\ul Dateitext\\ulnone}",
                })
                expect(menu.get_by_text("Unterstreichung gehört nicht zum freigegebenen Format.")).to_be_visible()
                return "Dateitext", 9

            if with_rows:
                expect(menu.get_by_role("button", name="Vorlage der Autorin")).to_be_visible()
            else:
                expect(menu.get_by_text("Für dieses Diktat sind keine Vorlagen verfügbar.")).to_be_visible()
            capture(page, "rows" if with_rows else "empty")
            preview, caret = choose()
            expect(menu.get_by_role("region", name="Vorschau")).to_have_text(preview)
            expect(menu.get_by_role("heading", name="Vorschau", exact=True)).to_be_focused()
            capture(page, "template-preview" if with_rows else "file-preview")
            expect(editor).to_have_value(original)
            expect(workspace).to_have_attribute("data-dirty", "false")
            if with_rows:
                menu.get_by_role("button", name="Vorlage des Teams").click()
                expect(menu.get_by_role("region", name="Vorschau")).to_have_text("Teamtext")
                page.keyboard.press("Escape")
            else:
                menu.get_by_role("button", name="Abbrechen").click()
            expect(menu).to_have_count(0)
            expect(trigger).to_be_focused()
            expect(editor).to_have_value(original)
            assert editor.evaluate("el => [el.selectionStart, el.selectionEnd]") == [0, 8]
            expect(workspace).to_have_attribute("data-dirty", "false")
            assert lease.inner_text() == lease_label
            server_draft = page.request.get(correction).json()
            assert (server_draft["text"], server_draft["text_revision_id"]) == (original, before["text_revision_id"])
            assert not any("/leases" in r or "/correction/save" in r for r in page.responses[seen:])

            expect(page.locator('[data-tool="undo"]')).to_have_attribute("aria-disabled", "true")
            trigger.click()
            choose()
            insert = menu.get_by_role("button", name="Text einfügen", exact=True)
            expect(insert).to_be_visible()
            page.locator("[data-action=release]").click()
            expect(menu).to_have_count(0)
            expect(editor).not_to_be_editable()
            expect(editor).to_have_value(original)
            expect(page.get_by_role("status").filter(has_text="Bitte öffnen Sie die Vorschau erneut.")).to_be_visible()
            page.locator("[data-action=acquire]").click()
            expect(editor).to_be_editable()
            editor.evaluate("el => el.setSelectionRange(0, 8)")
            trigger.click()
            choose()
            insert.evaluate("el => { el.click(); el.click(); }")
            combined = preview + original[8:]
            expect(editor).to_have_value(combined)
            expect(editor).to_be_focused()
            assert editor.evaluate("el => el.selectionStart") == caret
            page.get_by_test_id("save").click()
            expect(page.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
            assert page.request.get(correction).json()["text"] == combined
            page.locator('[data-tool="undo"]').click()
            expect(editor).to_have_value(original)
            assert editor.evaluate("el => [el.selectionStart, el.selectionEnd]") == [0, 8]
            page.get_by_test_id("save").click()
            expect(page.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
            page.get_by_test_id("clinic-correction-back").click()
            _open_from_worklist(page, base)
            expect(editor).to_have_value(original)

            page.locator("[data-action=acquire]").click()
            expect(editor).to_be_editable()
            editor.evaluate("el => el.setSelectionRange(0, 8)")
            trigger.click()
            choose()
            insert.click()
            editor.press("End")
            page.keyboard.insert_text(" Ergänzt.")
            expect(editor).to_have_value(combined + " Ergänzt.")
            page.get_by_test_id("save").click()
            expect(page.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
            page.get_by_test_id("clinic-correction-back").click()
            _open_from_worklist(page, base)
            expect(editor).to_have_value(combined + " Ergänzt.")
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
