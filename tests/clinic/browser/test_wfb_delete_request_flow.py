"""PAR-WF-013 (Q-06): browser and visual verification of non-destructive deletion requests."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

from playwright.sync_api import Browser, Locator, Page, StorageState, expect
from pytest import MonkeyPatch

import v8_browser_support as support

SEED_DELETE_REQUEST = """
from pathlib import Path
from src.clinic import policy, release, revisions
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision

with app.app_context():
    target = ClinicDictation.query.one()
    admin_id = User.query.filter_by(is_admin=True).one().id
    orig = ClinicOriginal.query.filter_by(dictation_id=target.id).one()
    orig_file = Path(app.config["UPLOAD_FOLDER"]) / orig.storage_key
    orig_file.parent.mkdir(parents=True, exist_ok=True)
    orig_file.write_bytes(b"RIFF-synthetic-delete-request-bytes")
    rec = db.session.get(Recording, target.recording_id)
    rec.audio_path = str(orig_file)
    text = revisions.add_human_revision(target, revisions.current_revision(target),
                                        "Synthetischer Prüftext.", target.author_user_id,
                                        original_version=orig)
    ctx = ClinicContextRevision(dictation_id=target.id, source_system="test", tenant_scope="test",
                                case_ref="SYN-P125", patient_ref="synthetic")
    db.session.add(ctx)
    db.session.flush()
    db.session.commit()
    pools.allow(target.author_user_id, "own", actor_id=admin_id, actions=("archive", "delete"))
"""


def _open_detail(page: Page, base: str) -> Locator:
    support._show_recordings(page, base)
    row = page.get_by_test_id("clinic-recordings-view").get_by_test_id("open-link").first
    expect(row).to_be_visible()
    row.click()
    page.get_by_test_id("clinic-dictation-link-detail").click()
    view = page.get_by_test_id("clinic-detail-view")
    expect(view.get_by_test_id("actions")).to_be_visible()
    return view


def _open_worklist_selected(page: Page, base: str) -> Locator:
    support._show_worklist(page, base)
    view = page.get_by_test_id("clinic-worklist-view")
    row = view.locator("[data-row]").first
    expect(row).to_be_visible()
    row.click()
    expect(view.locator(".clinic-workflow-actions")).to_be_visible()
    return view


def _capture(page: Page, view: Locator, stem: Path, theme: str, title: str) -> None:
    page.evaluate("document.fonts.ready")
    page.screenshot(path=stem.with_suffix(".png"), animations="disabled")
    markup = view.evaluate("el => el.outerHTML")
    styles = page.evaluate("""() => [...document.querySelectorAll('link[rel=stylesheet]')]
        .map(link => new URL(link.href)).filter(url => url.origin === location.origin &&
            url.pathname.startsWith('/static/')).map(url => url.pathname)""")
    links = "".join(f'<link rel="stylesheet" href="../..{p}">' for p in styles)
    dark = ' class="dark"' if theme == "dark" else ""
    stem.with_suffix(".html").write_text(
        f'<!doctype html><html lang="de-CH"{dark}><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title>{links}</head>'
        f'<body><main class="v8-ui"><div class="content">{markup}</div></main></body></html>',
        encoding="utf-8",
    )


def _save_matrix(browser: Browser, base: str, state: StorageState) -> None:
    out = support.ROOT / ".wp" / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            with browser.new_context(
                storage_state=state,
                viewport={"width": width, "height": height},
                **support.theme_context_options(theme),
            ) as context:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                detail = _open_detail(page, base)
                panel = detail.locator(".clinic-workflow-actions")
                expect(panel.get_by_role("button", name="Löschung beantragen", exact=True)).to_be_visible()
                panel.scroll_into_view_if_needed()
                _capture(page, detail, out / f"p125-detail-{size}-{theme}-rows", theme, "Diktatdetails")

                panel.get_by_role("button", name="Löschung beantragen", exact=True).click()
                expect(panel.get_by_role("alertdialog")).to_be_visible()
                _capture(page, detail, out / f"p125-detail-{size}-{theme}-confirm", theme, "Diktatdetails")
                panel.get_by_role("button", name="Abbrechen", exact=True).click()

                wl = _open_worklist_selected(page, base)
                wl_panel = wl.locator(".clinic-workflow-actions")
                expect(wl_panel.get_by_role("button", name="Löschung beantragen", exact=True)).to_be_visible()
                wl_panel.scroll_into_view_if_needed()
                _capture(page, wl, out / f"p125-worklist-{size}-{theme}-rows", theme, "Arbeitsliste")

                wl.get_by_role("button", name="Abgeschlossen", exact=False).first.click()
                expect(wl.locator("[data-row]")).to_have_count(0)
                _capture(page, wl, out / f"p125-worklist-{size}-{theme}-empty", theme, "Arbeitsliste")


def test_delete_request_browser_flow_and_artifacts(browser: Browser, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    monkeypatch.setenv("SECRET_KEY", "synthetic-p125-browser")
    server = support.SERVER.replace("server = make_server", SEED_DELETE_REQUEST + "\nserver = make_server")
    with support.serve_app(tmp_path, server) as base:
        author_state = cast(StorageState, support.login(browser, base, support.ROLES["author"]))
        typist_state = cast(StorageState, support.login(browser, base, support.ROLES["typist"]))

        if os.environ.get("P125_ARTIFACTS") == "1":
            _save_matrix(browser, base, author_state)

        with browser.new_context(storage_state=typist_state) as typist_ctx:
            t_page = typist_ctx.new_page()
            t_detail = _open_detail(t_page, base)
            expect(t_detail.get_by_role("button", name="Löschung beantragen", exact=True)).to_have_count(0)

        with browser.new_context(storage_state=author_state) as author_ctx:
            page = author_ctx.new_page()
            posts = []
            page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/archive" in r.url else None)
            detail = _open_detail(page, base)
            panel = detail.locator(".clinic-workflow-actions")

            req_btn = panel.get_by_role("button", name="Löschung beantragen", exact=True)
            arc_btn = panel.get_by_role("button", name="Archivieren", exact=True)
            expect(req_btn).to_be_visible()
            expect(arc_btn).to_be_visible()
            box = req_btn.bounding_box()
            assert box is not None and box["height"] >= 48 and box["width"] >= 48

            orig_dir = tmp_path / "uploads" / "orig"
            orig_files = list(orig_dir.iterdir())
            assert len(orig_files) == 1
            orig_file = orig_files[0]
            before_sha = hashlib.sha256(orig_file.read_bytes()).hexdigest()

            # Cancel changes nothing and sends no POST.
            req_btn.click()
            dialog = panel.get_by_role("alertdialog")
            expect(dialog).to_contain_text("Löschantrag")
            expect(dialog).to_contain_text("SYN-P125")
            expect(dialog).to_contain_text("nichts physisch gelöscht")
            panel.get_by_role("button", name="Abbrechen", exact=True).click()
            expect(dialog).to_have_count(0)
            assert posts == []

            # Confirm submits non-destructive deletion request and keeps clinical data and archive state.
            req_btn.click()
            with page.expect_response(lambda r: r.url.endswith("/archive") and r.request.method == "POST") as resp:
                panel.get_by_role("button", name="Löschantrag erfassen", exact=True).click()
            assert resp.value.status == 200
            payload = resp.value.json()
            assert payload["disposition"] == "deletion_requested"
            assert payload["physically_deleted"] is False
            assert payload["status"] == "zu_schreiben"

            expect(panel.get_by_test_id("wf-deletion-requested")).to_contain_text("Löschantrag erfasst")
            expect(panel.get_by_test_id("wf-deletion-requested")).to_contain_text("Archivstatus bleibt unverändert")
            expect(req_btn).to_be_disabled()
            assert hashlib.sha256(orig_file.read_bytes()).hexdigest() == before_sha

            if os.environ.get("P125_ARTIFACTS") == "1":
                out = support.ROOT / ".wp" / "artifacts"
                _capture(page, detail, out / "p125-detail-1440x900-light-confirmed", "light", "Diktatdetails")

            with closing(sqlite3.connect(tmp_path / "v860.db")) as conn:
                assert conn.execute("SELECT disposition, workflow_status FROM clinic_deletion_request").fetchall() == [
                    ("deletion_requested", "zu_schreiben")
                ]
                assert conn.execute("SELECT count(*) FROM clinic_archive_entry").fetchone() == (0,)
                assert conn.execute("SELECT status FROM clinic_workflow_dictation").fetchone() == ("zu_schreiben",)
                assert conn.execute("SELECT count(*) FROM clinic_transcript_revision").fetchone() == (2,)
                assert conn.execute(
                    "SELECT event, details FROM audit_event WHERE event = 'recording.edited' AND details LIKE '%deletion_requested%'"
                ).fetchall() == [("recording.edited", '{"fields": ["deletion_requested"]}')]
                assert conn.execute(
                    "SELECT count(*) FROM audit_event WHERE event IN ('recording.deleted', 'recording.archived')"
                ).fetchone() == (0,)
