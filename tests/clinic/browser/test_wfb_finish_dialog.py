"""P-107: finish from the real review and correction mounts on synthetic staging."""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import Browser, Locator, Page, Response, StorageState, expect

import v8_browser_support as support

TITLE = "Fertigstellen"
FINISHED = "Das Diktat ist fertiggestellt."
TEXT = "Geprüfter synthetischer Testtext."
CAPTURE = os.environ.get("P107_VISUAL") == "1"
TXT = "Textdatei (TXT)"
DOCX = "Word-Dokument (DOCX)"
READY = "Datei erzeugt"
FAILED = "Datei konnte nicht erzeugt werden."
SEED = """
from datetime import datetime, timezone
from src.clinic import export, revisions
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.models import ClinicContextRevision


def fail_docx(*args, **kwargs):
    raise RuntimeError('Synthetic DOCX renderer failure')


# Only this disposable process injects a failure. The real export route handles and persists it.
export._docx_bytes = fail_docx
with app.app_context():
    target = ClinicDictation.query.one()
    head = revisions.current_revision(target)
    if head is not None:
        revisions.add_human_revision(target, head, "Geprüfter synthetischer Testtext.", target.author_user_id)
    context = ClinicContextRevision(dictation_id=target.id, source_system="test", tenant_scope="test",
                                    case_ref="SYN-0107", patient_ref="synthetic")
    db.session.add(context)
    db.session.flush()
    db.session.add(ClinicContextGrantUse(revision_id=context.id, issuer="kis", audience="speakr",
                   actor_user_id=target.author_user_id, subject_reference="synthetic", purpose="bind_existing",
                   verified_at=datetime.now(timezone.utc), nonce_digest=uuid.uuid4().hex.ljust(64, "0")))
    pools.allow(target.author_user_id, "own", actor_id=User.query.filter_by(is_admin=True).one().id,
                actions=("edit", "release", "export"))
    db.session.commit()
"""


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[str]:
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    source = support.SERVER.replace("server = make_server", SEED + "\nserver = make_server")
    if getattr(request, "param", None) == "empty":
        source = source.replace('    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")\n', '')
    with support.serve_app(tmp_path, source) as base:
        yield base


def _author_state(browser: Browser, base: str) -> StorageState:
    return cast(StorageState, support.login(browser, base, support.ROLES["author"]))


def _recording(page: Page) -> Locator:
    return page.get_by_test_id("clinic-recordings-view").get_by_test_id("open-link")


def _open(page: Page, base: str, mount: str) -> str:
    support._show_recordings(page, base)
    row = _recording(page)
    expect(row).to_have_count(1)
    did = row.get_attribute("data-id")
    assert did
    row.click()
    if mount != "correction":
        page.get_by_test_id(f"clinic-dictation-link-{mount}").click()
    return base + f"/api/clinic/v1/dictations/{did}"


def _open_dialog(page: Page, mount: str, path: str) -> Locator:
    view = page.get_by_test_id(f"clinic-{mount}-view")
    if mount == "review":
        expect(view.get_by_test_id("review-text")).to_contain_text(TEXT)
        acquire = view.get_by_test_id("begin-review")
    else:
        expect(view.get_by_test_id("text")).to_have_value(TEXT)
        acquire = view.get_by_test_id("lease-bar").locator('[data-action="acquire"]')
    with page.expect_response(lambda r: r.url == path + "/leases" and r.request.method == "POST") as lease:
        acquire.click()
    assert lease.value.ok, lease.value.text()
    start = view.get_by_role("button", name=TITLE, exact=True)
    expect(start).to_be_enabled()
    start.click()
    dialog = page.get_by_role("dialog", name=TITLE, exact=True)
    expect(dialog).to_be_visible()
    copy = dialog.inner_text()
    assert "Textrevision:" not in copy
    detail = page.request.get(path + "/detail")
    assert detail.ok, detail.text()
    revision = detail.json()["binding"]["release"]["transcript_revision_id"]
    assert revision not in copy or "Geprüfter Text:" in copy
    return dialog


def _confirm_release(page: Page, path: str, dialog: Locator, *, output: bool) -> str:
    no_output = dialog.get_by_role("button", name="Ohne Ausgabe fertigstellen")
    choose_output = dialog.get_by_role("button", name="Fertigstellen und Ausgabe wählen")
    expect(no_output).to_be_disabled()
    expect(choose_output).to_be_disabled()
    dialog.get_by_role("checkbox", name="Inhalt geprüft").check()
    expect(no_output).to_be_disabled()
    expect(choose_output).to_be_disabled()
    dialog.get_by_role("checkbox", name="Fallkontext geprüft").check()
    with page.expect_response(lambda r: r.url == path + "/releases" and r.request.method == "POST") as result:
        (choose_output if output else no_output).click()
    assert result.value.status == 201, result.value.text()
    expect(dialog.get_by_role("status").filter(has_text=FINISHED)).to_be_visible()
    return result.value.json()["transcript_revision_id"]


def _select_targets(dialog: Locator) -> Locator:
    txt = dialog.get_by_role("checkbox", name=f"{TXT} bestätigen")
    docx = dialog.get_by_role("checkbox", name=f"{DOCX} bestätigen")
    expect(txt).to_be_enabled()
    expect(docx).to_be_enabled()
    expect(txt).not_to_be_checked()
    expect(docx).not_to_be_checked()
    submit = dialog.get_by_role("button", name="Bestätigte Ziele ausgeben")
    expect(submit).to_be_disabled()
    txt.check()
    docx.check()
    return submit


def _export(page: Page, path: str, dialog: Locator, submit: Locator, revision: str) -> None:
    def for_format(response: Response, fmt: str) -> bool:
        return (response.url == path + "/exports" and response.request.method == "POST"
                and (response.request.post_data_json or {}).get("format") == fmt)

    with (page.expect_response(lambda r: for_format(r, "txt")) as txt,
          page.expect_response(lambda r: for_format(r, "docx")) as docx):
        submit.click()
    for response, status in ((txt.value, "ready"), (docx.value, "failed")):
        assert response.status == 201, response.text()
        assert (response.request.post_data_json or {}).get("revision_id") == revision
        assert response.json()["status"] == status
    assert docx.value.json()["reason"] == "render_failed"
    expect(dialog.get_by_role("status").filter(has_text=f"{TXT}: {READY}")).to_be_visible()
    expect(dialog.get_by_role("status").filter(has_text=f"{DOCX}: {FAILED}")).to_be_visible()


def _close_finished(page: Page, dialog: Locator, mount: str) -> None:
    dialog.get_by_role("button", name="Schliessen", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_test_id(f"clinic-{mount}-view")).not_to_be_visible()
    expect(page).to_have_url(re.compile(r"/#/aufnahmen$"))
    expect(_recording(page).locator(".status")).to_have_text("Arbeitsstand: Fertiggestellt")


@pytest.mark.parametrize(("mount", "output"), [("review", False), ("correction", True)])
def test_finish_dialog(browser: Browser, staging: str, mount: str, output: bool) -> None:
    with browser.new_context(storage_state=_author_state(browser, staging)) as ctx:
        page = ctx.new_page()
        path = _open(page, staging, mount)
        dialog = _open_dialog(page, mount, path)
        before = ctx.request.get(path + "/detail").json()
        before_url = page.url
        writes = []
        page.on("request", lambda req: writes.append(req.url) if req.method == "POST" else None)
        dialog.get_by_role("checkbox", name="Inhalt geprüft").check()
        if mount == "review":
            page.keyboard.press("Escape")
        else:
            dialog.get_by_role("button", name="Abbrechen", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page).to_have_url(before_url)
        editor = page.get_by_test_id(f"clinic-{mount}-view")
        expect(editor).to_be_visible()
        if mount == "review":
            expect(editor.get_by_test_id("review-text")).to_contain_text(TEXT)
        else:
            expect(editor.get_by_test_id("text")).to_have_value(TEXT)
        after = ctx.request.get(path + "/detail").json()
        for key in ("status_axes", "etag", "binding"):
            assert after[key] == before[key]
        assert path + "/releases" not in writes and path + "/exports" not in writes

        page.get_by_role("button", name=TITLE, exact=True).press("F5")
        expect(dialog.get_by_role("checkbox", name="Inhalt geprüft")).not_to_be_checked()
        expect(dialog.get_by_role("checkbox", name="Fallkontext geprüft")).not_to_be_checked()
        revision = _confirm_release(page, path, dialog, output=output)
        assert revision == before["binding"]["release"]["transcript_revision_id"]
        if output:
            _export(page, path, dialog, _select_targets(dialog), revision)
        _close_finished(page, dialog, mount)
        assert writes.count(path + "/exports") == (2 if output else 0)

        # Open a fresh output mount for the same recording: results must come from the server.
        assert _open(page, staging, "output") == path
        view = page.get_by_test_id("output-view")
        expect(view.get_by_test_id("revision")).to_contain_text(revision)
        txt = view.locator('[data-target="txt"]')
        docx = view.locator('[data-target="docx"]')
        expect(txt).to_contain_text(TXT)
        expect(docx).to_contain_text(DOCX)
        if output:
            expect(txt.get_by_role("status")).to_have_text(READY)
            expect(txt.get_by_role("link", name="Herunterladen")).to_be_visible()
            expect(docx.get_by_role("status")).to_have_text(FAILED)
            expect(docx.get_by_role("link", name="Herunterladen")).to_have_count(0)
        else:
            expect(txt.get_by_role("status")).to_have_count(0)
            expect(docx.get_by_role("status")).to_have_count(0)
        assert ctx.request.get(path + "/detail").json()["status_axes"]["workflow"] == "fertiggestellt"


def _capture(page: Page, name: str) -> None:
    out = support.ROOT / ".wp" / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    page.evaluate("document.fonts.ready")
    page.screenshot(path=out / f"{name}.png", animations="disabled")
    html = page.evaluate("""root => {
        const copy = document.documentElement.cloneNode(true);
        copy.querySelectorAll('script, meta[name=csrf-token], link[rel=manifest]').forEach(el => el.remove());
        for (const attr of ['href', 'src', 'poster']) {
            for (const el of copy.querySelectorAll(`[${attr}]`)) {
                const url = new URL(el.getAttribute(attr), location.href);
                if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
                    el.setAttribute(attr, root + url.pathname);
                } else if (url.origin === location.origin && attr !== 'href') {
                    el.removeAttribute(attr);
                }
            }
        }
        copy.querySelectorAll('input, textarea').forEach((el, i) => {
            const live = document.querySelectorAll('input, textarea')[i];
            if (el.tagName === 'TEXTAREA') el.textContent = live.value;
            else if (el.type !== 'password' && el.type !== 'hidden') {
                el.setAttribute('value', live.value);
                el.toggleAttribute('checked', live.checked);
            } else el.removeAttribute('value');
        });
        const modal = copy.querySelector('dialog[open]');
        if (modal) {
            modal.removeAttribute('open');
            modal.setAttribute('data-captured-modal', '');
            const script = document.createElement('script');
            script.textContent = "document.querySelector('[data-captured-modal]').showModal()";
            copy.querySelector('body').append(script);
        }
        return '<!doctype html>\\n' + copy.outerHTML;
    }""", support.ROOT.as_uri())
    (out / f"{name}.html").write_text(html, encoding="utf-8")


@pytest.mark.skipif(not CAPTURE, reason="set P107_VISUAL=1 to capture evidence")
@pytest.mark.parametrize("viewport", support.VIEWPORTS)
@pytest.mark.parametrize("theme", support.THEMES)
@pytest.mark.parametrize("mount", ["review", "correction"])
@pytest.mark.parametrize("staging", ["populated", "empty", "no-output"], indirect=True)
def test_visual_finish_dialog(browser: Browser, staging: str, viewport: str, theme: str,
                              mount: str, request: pytest.FixtureRequest) -> None:
    width, height = support.VIEWPORTS[viewport]
    with browser.new_context(storage_state=_author_state(browser, staging),
                             viewport={"width": width, "height": height},
                             **support.theme_context_options(theme)) as ctx:
        ctx.add_init_script(support.theme_init_script(theme))
        page = ctx.new_page()
        path = _open(page, staging, mount)
        expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b") if theme == "dark" else
                                                  re.compile(r"^(?!.*\bdark\b)"))
        state = request.node.callspec.params["staging"]
        stem = f"p107-{mount}-{viewport}-{theme}"
        view = page.get_by_test_id(f"clinic-{mount}-view")
        expect(view).to_have_attribute("aria-busy", "false")
        if mount == "correction":
            expect(view.get_by_test_id("text")).to_have_value("" if state == "empty" else TEXT)
        elif state == "empty":
            expect(view.get_by_test_id("no-review")).to_be_visible()
        else:
            expect(view.get_by_test_id("review-text")).to_contain_text(TEXT)
        if state == "empty":
            expect(view.get_by_role("button", name=TITLE, exact=True)).to_have_count(0)
        if state != "no-output":
            _capture(page, f"{stem}-{state}")
        if state == "empty":
            return

        dialog = _open_dialog(page, mount, path)
        if state == "no-output":
            _confirm_release(page, path, dialog, output=False)
            _capture(page, f"{stem}-finished-without-output")
            _close_finished(page, dialog, mount)
            return

        _capture(page, f"{stem}-confirm-release")
        revision = _confirm_release(page, path, dialog, output=True)
        expect(dialog.get_by_role("checkbox", name=f"{DOCX} bestätigen")).to_be_enabled()
        _capture(page, f"{stem}-choose-output")
        _export(page, path, dialog, _select_targets(dialog), revision)
        _capture(page, f"{stem}-output-results")
        _close_finished(page, dialog, mount)
