"""P-075: one synthetic browser journey through upload, assignment, release and TXT."""

from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from struct import pack
from typing import cast
from urllib.parse import urlsplit
import wave

from playwright.sync_api import Browser, Page, Response, StorageState, expect
import pytest

import v8_browser_support as support


API = "/api/clinic/v1"
CASE = "SYN-0075"
ASR_TEXT = "Synthetische Erkennung für P-075."
AUTHOR_TEXT = "Synthetischer Entwurf, vom Autor korrigiert."
FINAL_TEXT = "Synthetischer Prüftext, von der Schreibkraft korrigiert."

# Keep role/pool setup, but remove the pre-seeded dictation. Only the ASR result is synthetic;
# upload, storage, audio rendering and all workflow mutations use the registered app.
SERVER = support.SERVER.split("    rec = Recording(", 1)[0] + f'''\
    pools.allow(lead.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("view",))
    pools.allow(author.id, "own", actor_id=admin.id, actions=("edit", "export"))

from datetime import datetime, timezone
from flask import request
from src.models import ProcessingJob

@app.after_request
def synthetic_asr_for_upload(response):
    if request.method != "POST" or request.path != "/upload" or response.status_code != 202:
        return response
    receipt = response.get_json()
    target = db.session.get(ClinicDictation, receipt["dictation_id"])
    original = ClinicOriginal.query.filter_by(dictation_id=target.id).one()
    add_asr_revision(target, original, {ASR_TEXT!r}, "model:synthetic-p075")
    recording = db.session.get(Recording, receipt["id"])
    job = ProcessingJob.query.filter_by(recording_id=recording.id, job_type="transcribe").one()
    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    recording.status = "COMPLETED"
    db.session.commit()
    return response

server = make_server("127.0.0.1", port, app, threaded=True)
print("READY", flush=True)
server.serve_forever()
'''


def response_at(page: Page, path: str, method: str = "GET"):
    return page.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == method)


def check(response: Response, status: int = 200):
    assert response.status == status, f"{response.request.method} {response.url}: {response.status} {response.text()}"
    return response.json()


def save_text(page: Page, path: str, value: str):
    text = page.get_by_test_id("text")
    expect(text).to_be_editable()
    with response_at(page, path + "/correction/save", "POST") as saved:
        text.fill(value)
        page.get_by_test_id("save").click()
    receipt = check(saved.value)
    expect(page.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
    return receipt


def test_par_author_to_export(browser: Browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    monkeypatch.delenv("CLINIC_KIS_DELIVERY_ENABLED", raising=False)
    monkeypatch.setenv("CLINIC_ORIGINALS_ROOT", str(tmp_path / "originals"))
    monkeypatch.setenv("SPEAKR_SKIP_BACKGROUND_TASKS", "true")
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", "http://127.0.0.1:9/v1")
    wav = tmp_path / "synthetic-p075.wav"
    with wave.open(str(wav), "wb") as tone:
        tone.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        tone.writeframes((pack("<h", 2000) * 40 + pack("<h", -2000) * 40) * 600)

    with support.serve_app(tmp_path, SERVER) as base, ExitStack() as contexts:
        pages = {}
        for role in ("author", "lead", "typist"):
            state = cast(StorageState, support.login(browser, base, support.ROLES[role]))
            session = contexts.enter_context(browser.new_context(
                storage_state=state, viewport={"width": 1440, "height": 900},
            ))
            pages[role] = session.new_page()
        author, lead, typist = (pages[role] for role in ("author", "lead", "typist"))

        author.goto(base + "/#/neu")
        author.get_by_test_id("mode-file").click()
        author.locator("#crv-file").set_input_files(wav)
        with response_at(author, "/upload", "POST") as uploaded:
            author.get_by_test_id("file-upload").click()
        receipt = check(uploaded.value, 202)
        did = receipt["dictation_id"]
        assert receipt["use_speech_recognition"] is True
        expect(author.get_by_test_id("created-id")).to_have_text(did)
        path = f"{API}/dictations/{did}"
        row = f'[data-id="{did}"]'
        recording_row = author.locator(f'[data-testid="open-link"]{row}')

        with response_at(author, API + "/recordings") as listed:
            author.get_by_test_id("nav-recordings").click()
        assert any(item["dictation_id"] == did for item in check(listed.value)["items"])
        with response_at(author, path + "/correction") as opened:
            recording_row.click()
        initial = check(opened.value)
        assert (initial["dictation_id"], initial["text"]) == (did, ASR_TEXT)
        expect(author.get_by_test_id("text")).to_be_editable()
        detail_tab = author.get_by_test_id("clinic-dictation-link-detail")
        with response_at(author, path + "/audio/derived") as media, \
                response_at(author, path + "/audio-revisions/current") as audio_head, \
                response_at(author, path + "/detail") as details:
            detail_tab.click()
        detail = check(details.value)
        assert detail["dictation_id"] == did
        assert "send" in detail["allowed_actions"] and "release" not in detail["allowed_actions"]
        audio_id = check(audio_head.value)["revision_id"]
        assert audio_id == initial["audio_revision_id"] == detail["binding"]["audioRevisionId"]
        assert media.value.status in (200, 206)
        assert media.value.headers["x-audio-revision"] == audio_id
        player = author.get_by_test_id("detail-playback").get_by_label("Diktat anhören", exact=True)
        expect(player).to_have_js_property("duration", 3)
        panel = author.get_by_test_id("detail-playback")
        panel.get_by_role("button", name="Wiedergabe starten").click()
        author.wait_for_function("document.querySelector('[data-testid=detail-playback] audio').currentTime > 0.1")
        panel.get_by_role("button", name="Wiedergabe pausieren").click()
        expect(player).to_have_js_property("paused", True)

        edit = author.get_by_role("region", name="Bearbeitungsstatus").get_by_role("button", name="Bearbeiten", exact=True)
        with response_at(author, path + "/leases", "POST") as acquired:
            edit.click()
        assert check(acquired.value)["holder_user_id"] == detail["author"]["user_id"]
        context = author.get_by_test_id("context-bind")
        expect(context).to_contain_text("Simulation (MockKIS, nur Staging)")
        context.get_by_label("Synthetische Fall-ID").fill(CASE)
        with response_at(author, path + "/context", "POST") as bound, \
                response_at(author, path + "/detail") as bound_detail:
            context.get_by_role("button", name="Fall-ID verknüpfen").click()
        assert check(bound.value, 201)["case_display"] == CASE
        binding = check(bound_detail.value)
        context_id = binding["binding"]["contextRevisionId"]
        assert context_id and binding["case_display"] == CASE
        expect(author.get_by_test_id("case-id")).to_have_text(CASE)

        with response_at(author, path + "/correction") as editor:
            author.get_by_test_id("open-editor").click()
        assert check(editor.value)["text_revision_id"] == initial["text_revision_id"]
        author_revision = save_text(author, path, AUTHOR_TEXT)["text_revision_id"]
        assert author_revision != initial["text_revision_id"]
        with response_at(author, path + "/detail") as after_save:
            detail_tab.click()
        assert check(after_save.value)["binding"]["textRevisionId"] == author_revision
        with response_at(author, path + "/correction") as reopened:
            author.get_by_test_id("open-editor").click()
        stored = check(reopened.value)
        assert (stored["dictation_id"], stored["text_revision_id"], stored["text"]) == (did, author_revision, AUTHOR_TEXT)
        expect(author.get_by_test_id("text")).to_have_value(AUTHOR_TEXT)
        with response_at(author, path + "/leases/release", "POST") as relinquished, \
                response_at(author, path + "/detail") as to_send:
            detail_tab.click()
        check(relinquished.value)
        assert check(to_send.value)["binding"]["contextRevisionId"] == context_id
        workflow = author.locator(".clinic-workflow-actions")
        workflow.get_by_role("button", name="Einreichen", exact=True).click()
        with response_at(author, path + "/send", "POST") as sent:
            workflow.get_by_role("button", name="Bestätigen").click()
        sent_receipt = check(sent.value)
        assert (sent_receipt["dictation_id"], sent_receipt["status"]) == (did, "zu_korrigieren")
        with response_at(author, API + "/recordings") as sent_list:
            author.get_by_test_id("nav-recordings").click()
        item = next(item for item in check(sent_list.value)["items"] if item["dictation_id"] == did)
        assert (item["workflow_status"], item["case_display"]) == ("zu_korrigieren", CASE)
        expect(recording_row).to_contain_text("Zu korrigieren", ignore_case=True)
        assert author.request.get(base + path + "/assignment/candidates").status == 403

        rows_path = API + "/worklist/rows"
        candidates_path = path + "/assignment/candidates"
        with response_at(lead, rows_path) as unassigned:
            lead.goto(base + "/#/arbeitsliste")
        item = next(item for item in check(unassigned.value)["items"] if item["dictation_id"] == did)
        assert item["case_display"] == CASE and item["assignee_user_id"] is None
        with response_at(lead, candidates_path) as candidates, response_at(lead, path + "/leases") as lead_rights:
            lead.get_by_test_id("clinic-worklist-view").locator(f"[data-row]{row}").click()
        candidate = next(person for person in check(candidates.value) if person["display_name"] == "typist-v860")
        rights = check(lead_rights.value)
        assert rights["may_acquire"] is False and rights["may_listen"] is False
        lead.get_by_role("button", name="Zuweisen", exact=True).click()
        dialog = lead.get_by_role("dialog", name="Diktat zuweisen")
        dialog.get_by_label("Schreibkraft", exact=True).select_option(str(candidate["id"]))
        with response_at(lead, path + "/assignment", "POST") as assigned:
            dialog.get_by_role("button", name="Zuweisen", exact=True).click()
        assignment = check(assigned.value)
        assert (assignment["dictation_id"], assignment["assignee_user_id"]) == (did, candidate["id"])
        assert assignment["version"] > sent_receipt["version"]
        expect(lead.get_by_text("Zugewiesen an: typist-v860", exact=True)).to_be_visible()

        with response_at(typist, rows_path) as assigned_list:
            typist.goto(base + "/#/arbeitsliste")
        item = next(item for item in check(assigned_list.value)["items"] if item["dictation_id"] == did)
        assert (item["case_display"], item["assignee_user_id"]) == (CASE, candidate["id"])
        worklist = typist.get_by_test_id("clinic-worklist-view")
        work_row = worklist.locator(f"[data-row]{row}")
        with response_at(typist, candidates_path) as forbidden_candidates:
            work_row.click()
        assert forbidden_candidates.value.status == 403
        expect(worklist.get_by_role("button", name="Zuweisen", exact=True)).to_have_count(0)
        with response_at(typist, path + "/correction") as assigned_editor:
            worklist.get_by_test_id("open-dictation").click()
        prior = check(assigned_editor.value)
        assert (prior["dictation_id"], prior["text_revision_id"], prior["audio_revision_id"]) == (did, author_revision, audio_id)
        expect(typist.get_by_test_id("text")).to_have_value(AUTHOR_TEXT)
        revision = save_text(typist, path, FINAL_TEXT)["text_revision_id"]
        assert revision != author_revision

        with response_at(typist, path + "/review") as reviewed:
            typist.get_by_test_id("clinic-dictation-link-review").click()
        review = typist.get_by_test_id("clinic-review-view")
        expect(review.get_by_role("group", name="Prüftext")).to_have_text(FINAL_TEXT)
        snapshot = check(reviewed.value)
        assert snapshot["dictation_id"] == did and "release" in snapshot["allowed_actions"]
        versions = (audio_id, revision, context_id)
        bound = snapshot["binding"]
        assert (bound["audio_revision_id"], bound["text_revision_id"], bound["context_revision_id"]) == versions
        digest = sha256(FINAL_TEXT.encode("utf-8")).hexdigest()
        assert bound["release"]["text_sha256"] == digest
        for reader in (author, lead):
            readable = reader.request.get(base + path + "/review")
            assert readable.status == 200 and "release" not in readable.json()["allowed_actions"]
        with response_at(typist, path + "/leases", "POST") as review_lease:
            review.get_by_test_id("begin-review").click()
        held = check(review_lease.value)
        assert held["holder_user_id"] == candidate["id"]
        review.get_by_role("button", name="Fertigstellen", exact=True).click()
        boxes = review.get_by_role("alertdialog").get_by_role("checkbox")
        expect(boxes).to_have_count(2)
        for box in boxes.all():
            box.check()
        with response_at(typist, path + "/releases", "POST") as released, \
                response_at(typist, path + "/review") as finished:
            review.get_by_test_id("confirm").click()
        submitted = released.value.request.post_data_json
        assert all(submitted[key] == value for key, value in bound["release"].items())
        assert (submitted["lease_id"], submitted["lease_generation"]) == (held["lease_id"], held["generation"])
        assert submitted["confirmed_content"] is True and submitted["confirmed_context"] is True
        release = check(released.value, 201)
        assert (release["original_version_id"], release["transcript_revision_id"], release["context_revision_id"]) == versions
        assert (release["dictation_id"], release["payload_sha256"]) == (did, digest)
        assert check(finished.value)["status"] == "fertiggestellt"

        targets_path = path + "/output-targets"
        recording_row.click()
        with response_at(author, targets_path) as targets:
            author.get_by_test_id("clinic-dictation-link-output").click()
        available = check(targets.value)
        assert (available["dictation_id"], available["released_revision_id"]) == (did, revision)
        assert next(target for target in available["targets"] if target["id"] == "txt")["available"] is True
        txt = author.get_by_test_id("output-view").locator('[data-target="txt"]')
        with response_at(author, path + "/exports", "POST") as exported:
            txt.get_by_role("button", name="Ausgabe starten").click()
        output = check(exported.value, 201)
        assert (output["status"], output["format"], output["dictation_id"]) == ("ready", "txt", did)
        assert (output["release_id"], output["revision_id"]) == (release["release_id"], revision)
        url = base + path + f'/exports/{output["id"]}/download'
        with author.expect_download() as downloaded:
            txt.get_by_role("link", name="Herunterladen").click()
        download = downloaded.value
        assert (download.url, download.suggested_filename) == (url, output["filename"])
        file = tmp_path / "released.txt"
        download.save_as(file)
        data = file.read_bytes()
        # Attachment navigation is a download event, not a normal page response.
        attachment = author.request.get(url)
        assert attachment.status == 200 and attachment.body() == data
        assert sha256(data).hexdigest() == attachment.headers["x-content-sha256"] == output["sha256"]
        header, body = data.decode("utf-8").split("\n\n", 1)
        assert body == FINAL_TEXT
        assert {f"Diktat-ID: {did}", f'Freigabe-ID: {release["release_id"]}', f"Audiorevision: {audio_id}",
                f"Textrevision: {revision}", f"Kontextrevision: {context_id}", f"Text-Hash: {digest}"} <= set(header.splitlines())
        for nonexporter in (lead, typist):
            denied = nonexporter.request.get(base + targets_path)
            assert denied.status == 200
            assert next(t for t in denied.json()["targets"] if t["id"] == "txt")["reason"] == "forbidden"
            assert nonexporter.request.get(url).status == 404

        with response_at(typist, rows_path) as final_list:
            typist.get_by_test_id("clinic-dictation-back").click()
        final = next(item for item in check(final_list.value)["items"] if item["dictation_id"] == did)
        assert (final["status"], final["case_display"], final["assignee_user_id"]) == ("fertiggestellt", CASE, candidate["id"])
        expect(work_row).to_contain_text("Fertiggestellt", ignore_case=True)
