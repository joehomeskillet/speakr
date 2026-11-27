"""PAR-OPS-008: real admin retry with synthetic jobs and no background workers."""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page, expect

import v8_browser_support as support
from v8_page_navigation import show_detail

SEED = '''
with app.app_context():
    from datetime import datetime, timedelta, timezone
    from src.models import ProcessingJob
    rec = Recording.query.filter_by(title="Synthetisch").one()
    operator = User.query.filter_by(email=emails["admin"]).one()
    other = Recording(user_id=operator.id, title="Synthetic queue diagnostics")
    db.session.add(other)
    db.session.flush()
    now = datetime.now(timezone.utc)
    db.session.add_all([
        ProcessingJob(id=11, user_id=rec.user_id, recording_id=rec.id, job_type="transcribe",
                      status="failed", error_message="corrupt_audio: synthetic private detail",
                      completed_at=now),
        ProcessingJob(id=22, user_id=other.user_id, recording_id=other.id, job_type="transcribe", status="processing",
                      started_at=now - timedelta(hours=1)),
        ProcessingJob(id=33, user_id=other.user_id, recording_id=other.id, job_type="summarize", status="queued",
                      created_at=now - timedelta(minutes=2)),
    ])
    db.session.commit()
'''


def _open_admin_jobs(page: Page, base: str) -> Locator:
    page.goto(base + "/admin#/verarbeitung")
    return page.get_by_test_id("clinic-jobs-view")


def _screenshots(browser: Browser, base: str, out: Path, *, rows: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for surface, role in (("admin", "admin"), ("dictation", "author")):
        state = support.login(browser, base, support.ROLES[role])
        for size, (width, height) in support.VIEWPORTS.items():
            for theme in support.THEMES:
                with browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                         **support.theme_context_options(theme)) as context:
                    context.add_init_script(support.theme_init_script(theme))
                    page = context.new_page()
                    if surface == "admin":
                        view = _open_admin_jobs(page, base)
                    else:
                        show_detail(page, base)
                        page.get_by_test_id("clinic-dictation-link-jobs").click()
                        view = page.get_by_test_id("clinic-jobs-view")
                    expect(page.locator("html")).to_have_class(
                        re.compile(r"\bdark\b" if theme == "dark" else r"^(?!.*\bdark\b)"))
                    expect(view.get_by_test_id("status")).to_have_text("")
                    target = view.get_by_test_id("failure-cause" if rows else "empty-state")
                    expect(target).to_be_visible()
                    if rows:
                        expect(target).to_have_text("Audiodatei beschädigt")
                    target.scroll_into_view_if_needed()
                    label = "rows" if rows else "empty"
                    page.screenshot(path=out / f"ops008-{surface}-{label}-{size}-{theme}.png", full_page=True)


def test_admin_requeues_failed_job(browser: Browser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKR_SKIP_BACKGROUND_TASKS", "true")
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", "http://127.0.0.1:1/v1")
    output = os.environ.get("P153_ARTIFACTS")
    seeded = support.SERVER.replace("server = make_server", SEED + "\nserver = make_server")
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    with support.serve_app(jobs_dir, seeded) as base:
        if output:
            _screenshots(browser, base, Path(output), rows=True)
        state = support.login(browser, base, support.ROLES["admin"])
        with browser.new_context(storage_state=state) as context:
            page = context.new_page()
            view = _open_admin_jobs(page, base)
            failed = view.get_by_role("row").filter(has_text="Audiodatei beschädigt")
            expect(failed).to_contain_text("Fehlgeschlagen", timeout=support.SHOW_TIMEOUT_MS)
            expect(view).not_to_contain_text("synthetic private detail")
            expect(view.get_by_test_id("stuck-ids")).to_have_text("Hängende Aufträge: 22")
            expect(view.get_by_test_id("oldest-queued-age")).to_be_visible()
            with page.expect_response(lambda r: r.url.endswith("/jobs/11/retry") and r.request.method == "POST") as retry:
                failed.get_by_role("button", name="Erneut versuchen, Auftrag 11", exact=True).click()
            assert retry.value.ok
            receipt = retry.value.json()
            assert receipt["job_id"] == 11 and receipt["status"] == "queued"
            expect(failed).to_have_count(0)
            expect(view.get_by_test_id("empty-state")).to_be_visible()
            health = page.request.get(base + "/api/clinic/v1/jobs/health")
            assert health.ok
            assert any(job["id"] == receipt["new_job_id"] and job["state"] == "queued"
                       for job in health.json()["jobs"])
    if output:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with support.serve_app(empty_dir, support.SERVER) as base:
            _screenshots(browser, base, Path(output), rows=False)
