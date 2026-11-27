"""The open recordings list follows a real job without page navigation or a UI refresh."""
import subprocess
import sys

from playwright.sync_api import expect

import v8_browser_support as support


def test_open_recordings_follows_recognition(browser, tmp_path):
    # Give the isolated server a pending dictation instead of its completed example.
    seed = support.SERVER.replace(
        '    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")',
        '''    from src.models import ProcessingJob
    db.session.add(ProcessingJob(user_id=author.id, recording_id=rec.id, job_type="transcribe", status="queued"))''',
    ).replace('status=workflow.TO_WRITE, audio_seconds=4', 'status=workflow.PREP_ONLINE, audio_seconds=4')
    worker = '''
import sys
from src.app import app, db
from src.models import ProcessingJob
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision
with app.app_context():
    job = ProcessingJob.query.filter_by(job_type="transcribe").one()
    job.status = sys.argv[1]
    if job.status == "completed":
        add_asr_revision(ClinicDictation.query.one(), ClinicOriginal.query.one(), "Synthetischer Satz.", "local")
    db.session.commit()
'''
    def advance(status):
        subprocess.run([sys.executable, "-c", worker, status], cwd=support.ROOT,
                       env=support.server_env(tmp_path), check=True, capture_output=True, timeout=60)

    with support.serve_app(tmp_path, seed) as base:
        context = browser.new_context(storage_state=support.login(browser, base, support.ROLES["author"]))
        try:
            page = context.new_page()
            navigations = []
            reads = []
            page.on("request", lambda req: reads.append(req.url) if "/api/clinic/v1/recordings?" in req.url else None)
            page.on("domcontentloaded", lambda: navigations.append(page.url))
            page.goto(base + "/#/aufnahmen")
            listing = page.get_by_test_id("clinic-recordings-view")
            row = listing.locator(".recording-row").first
            status = row.get_by_test_id("recording-next-step")
            text_state = row.get_by_test_id("recording-text-state")
            expect(status).to_have_count(1)
            expect(status).to_have_text("Das System bereitet das Diktat vor – Sie müssen nichts tun")
            expect(text_state).to_have_text("Text wird erstellt")
            page.get_by_test_id("nav-clinic-worklist").click()
            expect(page.get_by_test_id("clinic-worklist-view")).to_be_visible()
            background_reads = len(reads)
            page.wait_for_timeout(5500)  # Pass one full 5s poll while the list is kept alive.
            assert len(reads) == background_reads
            page.go_back()
            expect(row).to_be_visible()
            row.focus()
            def shows_processing(response):
                return ("/api/clinic/v1/recordings?" in response.url and response.ok
                        and any(item["processing_status"] == "PROCESSING" for item in response.json()["items"]))

            with page.expect_response(shows_processing, timeout=12000):
                advance("processing")
            expect(text_state).to_have_text("Text wird erstellt")
            advance("completed")
            expect(text_state).to_have_text("Text erstellt", timeout=12000)
            expect(status).to_have_count(1)
            expect(status).to_have_attribute("data-action-needed", "false")
            expect(status).to_contain_text("Sie müssen nichts tun")
            expect(row).not_to_contain_text("Transkription abgeschlossen")
            expect(row).to_be_focused()
            assert len(navigations) == 1
            assert len(reads) >= 3
            terminal_reads = len(reads)
            page.wait_for_timeout(5500)
            assert len(reads) == terminal_reads
        finally:
            context.close()
