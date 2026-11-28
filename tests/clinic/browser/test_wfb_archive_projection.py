"""PAR-WF-013: Zurich archive projection and guarded reopen in the real clinic shell."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from uuid import uuid4

from playwright.sync_api import APIRequestContext, APIResponse, Browser, Locator, Page, StorageState, expect
from pytest import MonkeyPatch

import v8_browser_support as support


API = "/api/clinic/v1"
ARCHIVE_STATUS = "zu_archivieren"
COMMANDS = API + "/parity/commands"
REASON = "Synthetischer Nachtrag zur Kontrolle"
READER_EMAIL = "autor-p110-zwei@local.test"

# Reuse the real harness's first dictation; add a second author in the same pool.
# Both recordings are from January. 23:30 UTC on Feb 28 is already March in Zurich.
ARCHIVE_READY = """
from datetime import datetime, timezone
from src.clinic import archive_policy, policy, release, revisions
from src.clinic.models import ClinicContextRevision, ClinicAudioRevision

with app.app_context():
    first = ClinicDictation.query.one()
    pool_id, admin_id = first.pool_id, User.query.filter_by(is_admin=True).one().id
    second_author = user("autor-p110-zwei", "autor-p110-zwei@local.test")
    second_rec = Recording(user_id=second_author.id, title="Synthetisches Diktat zwei",
                           status="COMPLETED", original_filename="synthetisch.wav")
    db.session.add(second_rec)
    db.session.flush()
    second = ClinicDictation(recording_id=second_rec.id, author_user_id=second_author.id, pool_id=pool_id)
    db.session.add(second)
    db.session.flush()
    original = ClinicOriginal(dictation_id=second.id, content_hash="b" * 64, size_bytes=1000,
                              storage_key="orig/p110-zwei", receipt="stored", origin_ref="device:synthetic")
    db.session.add(original)
    db.session.flush()
    add_asr_revision(second, original, "Synthetischer Rohtext zwei.", "model:synthetic")
    init_audio_revision(second, original, 4000)
    # Worklist candidates are own or assigned; viewing still requires a live grant.
    db.session.get(workflow.ClinicWorkflowDictation, first.id).assignee_user_id = second_author.id
    db.session.add(workflow.ClinicWorkflowDictation(id=second.id, author_user_id=second_author.id,
                   pool_id=pool_id, assignee_user_id=first.author_user_id,
                   status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
    pools.add_author_member(pool_id, second_author.id, actor_id=admin_id)
    pools.allow(first.author_user_id, "pool", actor_id=admin_id, pool_id=pool_id, actions=("view",))
    pools.allow(first.author_user_id, "own", actor_id=admin_id, actions=("edit", "reopen", "archive"))
    seed_archive_grant = pools.allow(second_author.id, "own", actor_id=admin_id, actions=("archive",)).id
    pools.allow(second_author.id, "pool", actor_id=admin_id, pool_id=pool_id, actions=("view",))

    for index, (target, hour) in enumerate(((first, 23), (second, 22)), 1):
        recording = db.session.get(Recording, target.recording_id)
        recording.meeting_date = datetime(2026, 1, index, 10, 0)
        text = revisions.add_human_revision(target, revisions.current_revision(target),
                                           "Synthetischer Prüftext.", target.author_user_id)
        context = ClinicContextRevision(dictation_id=target.id, source_system="test", tenant_scope="test",
                                        case_ref=f"SYN-P110-{index}", patient_ref="synthetic")
        db.session.add(context)
        db.session.flush()
        audio = db.session.get(ClinicAudioRevision, release.current_audio_id(target.id))
        release.create_release(target, audio, text, context, text.normalised_text_hash, target.author_user_id,
                               {"allowed": True}, datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc),
                               document_type="report", release_type="clinical_final")
        assert workflow.transition(db.session(), target.id, "complete").status == workflow.DONE
        archived = archive_policy.ArchiveBook().apply(
            db.session(), target.id, action=archive_policy.ARCHIVE, dictation=target, recording=recording,
            subject=policy.build_subject(target.author_user_id), facts=policy.build_facts(recording.id, recording.user_id),
            expected_version=target.version_counter, request_id=f"p110-archive-{index}",
            now=datetime(2026, 2, 28, hour, 30, tzinfo=timezone.utc), confirm=True)
        assert archived.applied and archived.status == workflow.TO_ARCHIVE
        target.version_counter += 1
    db.session.commit()
    pools.revoke(seed_archive_grant, actor_id=admin_id)
"""


def _archives(request: APIRequestContext, base: str, month: str, **filters: str) -> dict:
    response = request.get(base + API + "/recordings", params={"status": ARCHIVE_STATUS, "archive_month": month, **filters})
    assert response.ok, response.text()
    items = response.json()["items"]
    rows = {item["dictation_id"]: item for item in items}
    assert len(rows) == len(items)
    return rows


def _archive_view(page: Page, base: str) -> Locator:
    support._open_shell_view(page, base, "#/archiv")
    view = page.get_by_test_id("clinic-archive-view")
    expect(view.get_by_role("heading", name="Archiv", exact=True)).to_be_visible()
    return view


def _filter(view: Locator, month: str, author_id: str = "") -> None:
    view.get_by_label("Archivmonat", exact=True).fill(month)
    view.get_by_label("Diktiert von", exact=True).select_option(author_id)
    view.get_by_role("button", name="Filter anwenden", exact=True).click()


def _row(view: Locator, did: str) -> Locator:
    return view.locator(f'tr[data-id="{did}"]')


def _artifacts(browser: Browser, base: str, state: StorageState, rows: dict, march_author: str) -> None:
    out = support.ROOT / ".wp" / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            with browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                     **support.theme_context_options(theme)) as context:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                view = _archive_view(page, base)
                expect(page.locator("html.dark")).to_have_count(int(theme == "dark"))
                for did in rows:
                    expect(_row(view, did)).to_be_visible()
                for mode in ("rows", "empty", "confirm"):
                    if mode == "empty":
                        _filter(view, "2026-02", march_author)
                        expect(view.get_by_test_id("archive-empty")).to_be_visible()
                        expect(view.locator("tr[data-row]")).to_have_count(0)
                    if mode == "confirm":
                        _filter(view, "2026-03", march_author)
                        view.get_by_test_id("archive-reopen").click()
                        expect(view.get_by_role("dialog")).to_be_visible()
                    stem = out / f"p110-archive-{size}-{theme}-{mode}"
                    page.evaluate("document.fonts.ready")
                    if mode == "confirm":
                        page.screenshot(path=stem.with_suffix(".png"), animations="disabled")
                    else:
                        content = view.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' content ')][1]")
                        content.evaluate("el => el.scrollTop = 0")
                        page.screenshot(path=stem.with_suffix(".png"), animations="disabled")
                        section = view.get_by_role("region", name="Archivierte Diktate")
                        section.evaluate("el => el.scrollIntoView({block: 'start'})")
                        page.screenshot(path=out / f"{stem.name}-list.png", animations="disabled")
                    # Preserve live filter values without saving session metadata from <head>.
                    markup = view.evaluate("""element => {
                        const clone = element.cloneNode(true);
                        const inputs = element.querySelectorAll('input, select');
                        clone.querySelectorAll('input, select').forEach((control, i) => {
                            if (control.tagName === 'SELECT') {
                                for (const option of control.options) {
                                    option.toggleAttribute('selected', option.value === inputs[i].value);
                                }
                            } else {
                                control.setAttribute('value', inputs[i].value);
                            }
                        });
                        return clone.outerHTML;
                    }""")
                    styles = page.evaluate("""() => [...document.querySelectorAll('link[rel=stylesheet]')]
                        .map(link => new URL(link.href)).filter(url => url.origin === location.origin &&
                            url.pathname.startsWith('/static/')).map(url => url.pathname)""")
                    links = ''.join(f'<link rel="stylesheet" href="../..{path}">' for path in styles)
                    dark = ' class="dark"' if theme == "dark" else ""
                    script = '<script>document.querySelector("dialog[open]").close(); document.querySelector("dialog").showModal()</script>' if mode == "confirm" else ""
                    stem.with_suffix(".html").write_text(
                        f'<!doctype html><html lang="de-CH"{dark}><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1">'
                        f'<title>Archiv</title>{links}</head>'
                        f'<body><main class="v8-ui"><div class="content">{markup}</div></main>{script}</body></html>',
                        encoding="utf-8")


def _post(request: APIRequestContext, base: str, path: str, body: dict, **headers: str) -> APIResponse:
    token = request.get(base + "/api/csrf-token").json()["csrf_token"]
    return request.post(base + path, data=body,
                        headers={"X-CSRF-Token": token, "Idempotency-Key": str(uuid4()), **headers})


def _reopen(request: APIRequestContext, base: str, did: str, revision: int, etag: str, **payload: str) -> APIResponse:
    return _post(request, base, COMMANDS,
                 {"command": "reopen_dictation", "request_id": str(uuid4()), "expected_revision": revision,
                  "payload": {"dictation_id": did, **payload}}, **{"If-Match": etag})


def _evidence(database: Path, did: str) -> tuple:
    with closing(sqlite3.connect(database)) as conn:
        entry = conn.execute("SELECT * FROM clinic_archive_entry WHERE dictation_id = ?", (did,)).fetchall()
        receipt = conn.execute("SELECT * FROM clinic_archive_result WHERE dictation_id = ?", (did,)).fetchall()
        release = conn.execute("SELECT * FROM clinic_release WHERE dictation_id = ?", (did,)).fetchall()
        assert len(entry) == len(receipt) == len(release) == 1
        binding = conn.execute("""SELECT r.transcript_revision_id FROM clinic_archive_entry e
            JOIN clinic_release r ON r.id = e.release_id AND r.dictation_id = e.dictation_id
                 AND r.audio_revision_id = e.audio_revision_id AND r.context_revision_id = e.context_revision_id
                 AND r.transcript_revision_id = e.transcript_revision_id
            WHERE e.dictation_id = ?""", (did,)).fetchone()
        assert binding is not None
        return entry, receipt, release, binding[0]


def test_archive_projection_and_guarded_reopen(browser: Browser, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    monkeypatch.setenv("TRANSCRIPTION_CONNECTOR", "clinic_stub")
    monkeypatch.setenv("SECRET_KEY", uuid4().hex)
    server = support.SERVER.replace("server = make_server", ARCHIVE_READY + "\nserver = make_server")
    with support.serve_app(tmp_path, server) as base:
        writer_state = cast(StorageState, support.login(browser, base, support.ROLES["author"]))
        reader_state = cast(StorageState, support.login(browser, base, READER_EMAIL))
        with browser.new_context(storage_state=writer_state) as writer, \
                browser.new_context(storage_state=reader_state) as reader:
            rows = _archives(writer.request, base, "all")
            assert len(rows) == 2
            recordings = base + API + '/recordings'
            params = {'status': ARCHIVE_STATUS, 'archive_month': 'all', 'limit': '1'}
            first_page = writer.request.get(recordings, params=params).json()
            second_page = writer.request.get(recordings, params={**params, 'cursor': first_page['next_cursor']}).json()
            assert [item['archive_month'] for item in first_page['items'] + second_page['items']] == ['2026-03', '2026-02']
            assert second_page['next_cursor'] is None
            assert writer.request.get(recordings, params={**params, 'archive_month': '2026-13'}).status == 400
            by_author = {row["dictated_by"]["display_name"]: did for did, row in rows.items()}
            march, feb = by_author["author-v860"], by_author["autor-p110-zwei"]
            for did, month in ((march, "2026-03"), (feb, "2026-02")):
                assert rows[did]["archive_month"] == month
                assert rows[did]["archived_at"].startswith("2026-02-28T")
                assert rows[did]["recorded_at"].startswith("2026-01-")
                assert str(rows[did]["archive_author_id"]) == rows[did]["dictated_by"]["id"]
                assert set(_archives(writer.request, base, month)) == {did}
            march_author = str(rows[march]["archive_author_id"])
            feb_author = str(rows[feb]["archive_author_id"])
            assert march_author != feb_author
            assert set(_archives(writer.request, base, "all", author_id=march_author)) == {march}
            assert set(_archives(writer.request, base, "2026-02", author_id=feb_author)) == {feb}
            assert not _archives(writer.request, base, "2026-02", author_id=march_author)
            assert not rows[feb].get("archive_reopen")  # The writer's separate grant is own-only.
            reopen = rows[march]["archive_reopen"]
            reader_rows = _archives(reader.request, base, "all")
            assert reader_rows.keys() == rows.keys()
            assert all(not row.get("archive_reopen") for row in reader_rows.values())

            page = writer.new_page()
            view = _archive_view(page, base)
            for did, month, author_id in ((march, "2026-03", march_author), (feb, "2026-02", feb_author)):
                group = view.locator(f'[data-archive-month="{month}"] [data-author-id="{author_id}"]')
                expect(_row(group, did)).to_be_visible()
            expect(_row(view, feb).get_by_test_id("archive-reopen")).to_have_count(0)
            _filter(view, "2026-02", feb_author)
            expect(_row(view, feb)).to_be_visible()
            expect(_row(view, march)).to_have_count(0)
            _filter(view, "2026-03", march_author)
            expect(_row(view, march)).to_be_visible()
            expect(_row(view, feb)).to_have_count(0)

            reader_view = _archive_view(reader.new_page(), base)
            expect(_row(reader_view, march).get_by_test_id("archive-open")).to_be_visible()
            expect(reader_view.get_by_test_id("archive-reopen")).to_have_count(0)

            if os.environ.get("P110_ARTIFACTS") == "1":
                _artifacts(browser, base, writer_state, rows, march_author)

            database = tmp_path / "v860.db"
            before = _evidence(database, march)
            path = API + f"/dictations/{march}"
            detail_response = writer.request.get(base + path + "/detail")
            assert detail_response.ok, detail_response.text()
            detail = detail_response.json()
            assert detail["status_axes"]["workflow"] == ARCHIVE_STATUS
            assert (reopen["revision"], reopen["etag"]) == (detail["revision"], detail["etag"])
            expected_revision = reopen["revision"] + 1
            assert not writer.request.get(base + path + "/leases").json()["held"]
            for context, revision, etag, payload, status, code in (
                (reader, expected_revision, reopen["etag"], {"reason": REASON}, 403, "POLICY_DENIED"),
                (writer, expected_revision, reopen["etag"], {}, 400, "INVALID_PAYLOAD"),
                (writer, expected_revision, reopen["etag"], {"reason": "  "}, 400, "INVALID_PAYLOAD"),
                (writer, expected_revision - 1, f'"{march}:{detail["revision"] - 1}"',
                 {"reason": REASON}, 409, "REVISION_CONFLICT"),
            ):
                rejected = _reopen(context.request, base, march, revision, etag, **payload)
                assert rejected.status == status, rejected.text()
                assert rejected.json()["code"] == code
            unchanged = writer.request.get(base + path + "/detail").json()
            assert unchanged["etag"] == detail["etag"]
            assert unchanged["status_axes"]["workflow"] == ARCHIVE_STATUS
            assert unchanged["binding"]["textRevisionId"] == before[3]
            assert _evidence(database, march) == before

            _row(view, march).get_by_test_id("archive-reopen").click()
            dialog = page.get_by_role("dialog", name="Diktat weiterbearbeiten", exact=True)
            reason = dialog.get_by_test_id("archive-reopen-reason")
            expect(reason).to_have_js_property("required", True)
            reason.fill(REASON)
            with page.expect_response(lambda r: r.url == base + COMMANDS and r.request.method == "POST") as sent:
                dialog.get_by_test_id("archive-reopen-confirm").click()
            assert sent.value.ok, sent.value.text()
            body = sent.value.request.post_data_json
            assert body is not None and body["command"] == "reopen_dictation"
            assert body["expected_revision"] == expected_revision
            assert body["payload"] == {"dictation_id": march, "reason": REASON}
            assert sent.value.json()["revision"] == expected_revision + 1
            expect(view.get_by_test_id("archive-resumed")).to_contain_text("wieder bearbeitet werden")
            expect(_row(view, march)).to_have_count(0)
            assert set(_archives(writer.request, base, "all")) == {feb}

            changed = writer.request.get(base + path + "/detail").json()
            assert changed["status_axes"]["workflow"] == "zu_korrigieren"
            assert changed["revision"] == detail["revision"] + 1 and changed["etag"] != detail["etag"]
            assert not writer.request.get(base + path + "/leases").json()["held"]
            new_head = changed["binding"]["textRevisionId"]
            assert new_head != before[3]
            with closing(sqlite3.connect(database)) as conn:
                assert conn.execute("""SELECT child.parent_revision_id, child.body = parent.body
                    FROM clinic_transcript_revision child JOIN clinic_transcript_revision parent
                      ON parent.id = child.parent_revision_id AND parent.dictation_id = child.dictation_id
                    WHERE child.id = ? AND child.dictation_id = ?""", (new_head, march)).fetchone() == (before[3], 1)
            assert _evidence(database, march) == before
