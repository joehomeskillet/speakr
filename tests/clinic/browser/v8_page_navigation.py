"""Read-only navigation and content proofs for the mounted V8 pages."""

from uuid import UUID

from playwright.sync_api import expect

import v8_browser_support as support


# Only the matrix needs a processing row. Keep other browser scenarios' seed unchanged.
MATRIX_SEED = '''
with app.app_context():
    from src.models import ProcessingJob
    rec = Recording.query.filter_by(title="Synthetisch").one()
    db.session.add(ProcessingJob(recording_id=rec.id, user_id=rec.user_id,
                                job_type="transcribe", status="completed"))
    db.session.commit()
'''


def show_detail(page, base):
    support._show_recordings(page, base)
    row = page.get_by_test_id("clinic-recordings-view").get_by_test_id("open-link")
    expect(row).to_have_count(1, timeout=support.SHOW_TIMEOUT_MS)
    page.v8_dictation_id = str(UUID(row.get_attribute("data-id")))
    support._click_noting(page, row, "synthetic recording")
    show_dictation_tab(page, "detail", "detail")
    view = page.get_by_test_id("clinic-detail-view")
    expect(view.get_by_test_id("author")).to_have_text("author-v860")
    expect(view.get_by_test_id("pool")).to_have_text("Schreibdienst V860")
    expect(view.get_by_test_id("status-axes")).to_be_visible()


def show_dictation_tab(page, tab, endpoint):
    with page.expect_response(lambda r: r.request.method == "GET" and
                             r.url.endswith(f"/dictations/{page.v8_dictation_id}/{endpoint}")) as response:
        support._click_noting(page, page.get_by_test_id("clinic-dictation-link-" + tab), tab)
    assert response.value.status == 200, f"{endpoint}: HTTP {response.value.status}"
    return response.value.url


def show_review(page, base):
    show_detail(page, base)
    review_url = show_dictation_tab(page, "review", "review")
    # The clicked page may navigate while Chromium discards the previous response body.
    # Read the expected revision independently with the same signed-in browser context.
    expected = page.request.get(review_url)
    assert expected.status == 200
    data = expected.json()
    view = page.get_by_test_id("clinic-review-view")
    expect(view.get_by_test_id("dictation-id")).to_contain_text(page.v8_dictation_id)
    expect(view.get_by_test_id("review-text")).to_contain_text("Anamnese Husten seit drei Tagen.")
    expect(view.get_by_test_id("reviewed")).to_contain_text(data["binding"]["text_revision_id"])


def show_output(page, base):
    show_detail(page, base)
    show_dictation_tab(page, "output", "output-targets")
    view = page.get_by_test_id("output-view")
    expect(view.get_by_test_id("not-released")).to_be_visible()
    expect(view.locator("[data-target]").first).to_be_visible()
    for button in view.locator("[data-target] button").all():
        expect(button).to_be_disabled()


def show_jobs(page, base):
    show_detail(page, base)
    support._click_noting(page, page.get_by_test_id("clinic-dictation-link-jobs"), "Verarbeitung")
    view = page.get_by_test_id("clinic-jobs-view")
    expect(view.get_by_role("heading", name="Aufträge dieses Diktats", exact=True)).to_be_visible()
    expect(view.get_by_test_id("job-count")).to_have_text("1")
    expect(view.get_by_test_id("job-list").locator("tbody tr")).to_have_count(1)
    expect(view.get_by_test_id("job-list")).to_contain_text("Abgeschlossen")


def show_activity(page, base):
    show_detail(page, base)
    show_dictation_tab(page, "activity", "history")
    history = page.get_by_test_id("history-section").locator(".clinic-history")
    expect(history.locator(".clinic-history-item").first).to_be_visible()
    expect(history).to_contain_text("Automatische Transkription")


def show_recording(page, base):
    support._open_shell_view(page, base, "#/neu")
    view = page.get_by_test_id("clinic-recording-view")
    expect(view.get_by_role("heading", name="Neue Aufnahme", exact=True)).to_be_visible()
    expect(view.get_by_test_id("author")).to_have_count(0)
    expect(view.get_by_role("link", name="oder Datei hochladen", exact=True)).to_be_visible()
    expect(view.get_by_test_id("rec-start")).to_be_visible()


def show_live(page, base):
    support._open_shell_view(page, base, "#/live")
    choices = page.locator("#clinic-live-choices option")
    expect(choices).to_have_count(1, timeout=support.SHOW_TIMEOUT_MS)
    page.get_by_test_id("clinic-live-id").fill(choices.get_attribute("value"))
    support._click_noting(page, page.get_by_test_id("clinic-live-open"), "Live-Diktat öffnen")
    view = page.get_by_test_id("clinic-live-view")
    expect(view.get_by_role("heading", name="Live-Diktat", exact=True)).to_be_visible()
    expect(view.get_by_test_id("live-controls")).to_be_visible()
    expect(view.get_by_test_id("live-caption")).to_be_visible()


def show_archive(page, base):
    support._open_shell_view(page, base, "#/archiv")
    view = page.get_by_test_id("clinic-archive-view")
    expect(view.get_by_test_id("archive-filters")).to_be_visible()
    expect(view.get_by_test_id("archive-empty")).to_be_visible()


def show_import(page, base):
    support._open_shell_view(page, base, "#/import")
    view = page.get_by_test_id("clinic-import-view")
    expect(view.get_by_role("heading", name="Dateien und Geräteimport", exact=False)).to_be_visible()
    expect(view.get_by_test_id("intents-empty")).to_be_visible()
    expect(view.get_by_test_id("inventory-empty")).to_be_visible()


def show_authors(page, base):
    page.goto(base + "/admin#/diktierende")
    view = page.get_by_test_id("clinic-authors-view")
    expect(view.get_by_test_id("author-pool-cards")).to_contain_text("Autoren V860")
    expect(view.get_by_role("table")).to_contain_text("author-v860")


def show_vocabulary(page, base):
    # Retain the original resources-menu navigation proof for all four existing roles.
    support._show_resources(page, base)
    support._open_shell_view(page, base, "#/verwaltung/wortschatz")
    view = page.get_by_test_id("clinic-vocabulary-view")
    expect(view.get_by_role("heading", name="Wortschatz", exact=True)).to_be_visible()
    expect(view.get_by_role("tabpanel").get_by_test_id("empty")).to_be_visible()


def show_commands(page, base):
    support._open_shell_view(page, base, "#/verwaltung/befehle-vorlagen")
    view = page.get_by_test_id("clinic-commands-templates-view")
    expect(view.get_by_role("heading", name="Befehlsübersicht", exact=True)).to_be_visible()
    expect(view.get_by_role("status")).to_have_text("Noch keine Sprachbefehle vorhanden.")


def show_asr(page, base):
    page.goto(base + "/admin#/sprachprofile")
    expect(page.get_by_role("heading", name="Sprachprofile", exact=True)).to_be_visible()
    expect(page.get_by_test_id("capabilities")).to_be_visible()
    expect(page.get_by_test_id("training")).to_be_visible()


def show_settings(page, base):
    support._open_shell_view(page, base, "#/einstellungen")
    expect(page.get_by_role("heading", name="Einstellungen", exact=True)).to_be_visible()
    expect(page.get_by_test_id("clinic-settings-origins")).to_be_visible()
    expect(page.get_by_test_id("clinic-settings-pedal")).to_be_visible()


def show_login(page, base):
    page.goto(base + "/login")
    expect(page).to_have_url(base + "/login")
    form = page.locator('form[action="/login"]')
    expect(form.locator("input[name=email]")).to_be_visible()
    expect(form.locator("input[name=password]")).to_be_visible()
    expect(form.locator("[type=submit]")).to_be_visible()
    expect(page.get_by_role("heading", name="Identität und Zugriff", exact=True)).to_be_visible()


def show_offline(page, base):
    support._open_shell_view(page, base, "#/offline")
    expect(page.get_by_role("heading", name="Offline und Wiederherstellung", exact=True)).to_be_visible()
    expect(page.get_by_test_id("connection")).to_contain_text("Online")
    expect(page.get_by_test_id("drafts-unavailable")).to_be_visible()
