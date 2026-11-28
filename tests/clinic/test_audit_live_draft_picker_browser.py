"""BT-LIVE-004: drive the real clinic shell and verify durable A/B draft isolation."""

import json
import socket
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from test_audit_live_session_browser import ART, VIEWPORTS, _theme
from test_wp_mount_correction import PASSWORD, ROOT, SERVER, _env, _launch, _login

AUTHOR = "author215@local.test"
API = "/api/clinic/v1/live/drafts"
A, B = "Entwurf A", "Entwurf B"
TEXT_A, TEXT_B = "Gespeicherter Text A.", "Gespeicherter Text B."
SEED = SERVER.split("with app.app_context():", 1)[0] + r'''
import io, os, wave
from hashlib import sha256
from pathlib import Path

with app.app_context():
    initialize_database(app)
    db.create_all()
    author = user("Autor P215", sys.argv[3])
    pool = ClinicAuthorPool(name="Autoren P215", created_by=author.id)
    db.session.add(pool)
    db.session.flush()
    originals = {}
    for index, title in enumerate(("Aufnahme A", "Aufnahme B"), 1):
        recording = Recording(user_id=author.id, title=title, status="PENDING")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id, pool_id=pool.id)
        db.session.add(dictation)
        db.session.flush()
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dictation.id, author_user_id=author.id, pool_id=pool.id, status=workflow.DRAFT))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(bytes([index, 0]) * 16000)
        audio = buffer.getvalue()
        key = f"synthetic/{dictation.id}.wav"
        path = Path(os.environ["CLINIC_ORIGINALS_ROOT"]) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        digest = sha256(audio).hexdigest()
        db.session.add(ClinicOriginal(dictation_id=dictation.id, content_hash=digest, size_bytes=len(audio),
                                       storage_key=key, receipt="stored", origin_ref="synthetic"))
        originals[dictation.id] = [str(path), digest]
    db.session.commit()
server = make_server("127.0.0.1", port, app, threaded=True)
print("READY " + json.dumps(originals), flush=True)
server.serve_forever()
'''


@pytest.fixture
def draft_app(tmp_path: Path) -> Iterator[tuple[str, dict[str, list[str]]]]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**_env(tmp_path, True), "CLINIC_ORIGINALS_ROOT": str(tmp_path / "originals"),
           "SPEAKR_SKIP_BACKGROUND_TASKS": "true", "CLINIC_WEB_ONLY": "0",
           "TRANSCRIPTION_BASE_URL": "http://127.0.0.1:9/v1"}
    log = tmp_path / "server.log"
    with log.open("w") as out, subprocess.Popen(
        [sys.executable, "-c", SEED, str(port), PASSWORD, AUTHOR], cwd=ROOT, env=env,
        stdout=out, stderr=subprocess.STDOUT, text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 60
            while True:
                output = log.read_text()
                ready = next((line for line in output.splitlines() if line.startswith("READY ")), None)
                if ready:
                    break
                assert process.poll() is None and time.monotonic() < deadline, output[-4000:]
                time.sleep(0.2)
            yield f"http://127.0.0.1:{port}", json.loads(ready.removeprefix("READY "))
        finally:
            process.terminate()
            process.wait(timeout=20)


def _picker(page: Page, base: str) -> None:
    page.get_by_test_id("clinic-recordings-view").wait_for(state="visible", timeout=30000)
    with page.expect_response(base + API) as listed:
        page.get_by_test_id("menu-live").first.click()
        page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)
    assert listed.value.ok
    expect(page.get_by_test_id("clinic-live-picker")).to_be_visible()


def _capture(page: Page, state: str) -> None:
    previous = page.viewport_size
    ART.mkdir(parents=True, exist_ok=True)
    for width, height in VIEWPORTS:
        page.set_viewport_size({"width": width, "height": height})
        for theme in ("light", "dark"):
            _theme(page, theme)
            page.evaluate("scrollTo(0, 0)")
            stem = ART / f"p215-live-drafts-{state}-{width}x{height}-{theme}"
            page.screenshot(path=stem.with_suffix(".png"), full_page=True, animations="disabled", caret="hide")
            # Clone the mounted DOM, including form values, without executable scripts or CSRF tokens.
            html = page.evaluate(r'''(local) => {
                const root = document.documentElement.cloneNode(true);
                root.querySelectorAll('script, meta[name="csrf-token"], input[name="csrf_token"]')
                    .forEach(node => node.remove());
                const sheets = document.querySelectorAll('link[rel="stylesheet"]');
                const offline = (url, base) => new URL(url, base || location.href).href.replace(location.origin + '/static/', local + '/static/');
                const css = sheet => [...sheet.cssRules].map(rule => rule.styleSheet ? css(rule.styleSheet)
                    : rule.cssText.replace(/url\("([^"]+)"\)/g, (_, url) => 'url("' + offline(url, sheet.href) + '")')).join('\n');
                root.querySelectorAll('link[rel="stylesheet"]').forEach((link, i) => {
                    const style = document.createElement('style');
                    style.textContent = css(sheets[i].sheet);
                    link.replaceWith(style);
                });
                for (const node of root.querySelectorAll('[src^="/static/"], link[href^="/static/"]')) {
                    const attr = node.hasAttribute('src') ? 'src' : 'href';
                    node.setAttribute(attr, offline(node.getAttribute(attr)));
                }
                const text = root.querySelector('[data-testid="live-draft-text"]');
                if (text) text.textContent = document.querySelector('[data-testid="live-draft-text"]').value;
                return '<!doctype html>\n' + root.outerHTML;
            }''', ROOT.as_uri())
            stem.with_suffix(".html").write_text(html, encoding="utf-8")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (state, width, theme)
            assert page.get_by_test_id("clinic-live-picker").evaluate('''root =>
                [...root.querySelectorAll('button, input')].every(control => !control.getClientRects().length
                    || control.getBoundingClientRect().height >= 48)'''), (state, width, theme)
    assert previous is not None
    page.set_viewport_size(previous)
    _theme(page, "light")


def _create(page: Page, dictation: str, title: str) -> None:
    picker = page.get_by_test_id("clinic-live-picker")
    picker.get_by_test_id("clinic-live-id").fill(dictation)
    picker.get_by_test_id("clinic-live-title").fill(title)
    picker.get_by_role("button", name="Entwurf anlegen", exact=True).click()
    row = picker.get_by_test_id("live-draft-open").and_(picker.get_by_role("button", name=title, exact=True))
    expect(row).to_be_visible()
    expect(page.get_by_test_id("live-draft-text")).to_have_value("")


def _draft(page: Page, base: str, draft_id: str) -> dict:
    response = page.request.get(f"{base}{API}/{draft_id}")
    assert response.ok, response.text()
    return response.json()


def _run_staged_flow(page: Page, base: str, a: str, b: str) -> None:
    _picker(page, base)
    picker = page.get_by_test_id("clinic-live-picker")
    rows = picker.get_by_test_id("live-draft-open")
    open_a = picker.get_by_role("button", name=A, exact=True)
    open_b = picker.get_by_role("button", name=B, exact=True)
    view = page.get_by_test_id("clinic-live-view")
    text = view.get_by_test_id("live-draft-text")
    close = view.get_by_test_id("live-draft-close")
    save = view.get_by_test_id("live-draft-save")
    discard = view.get_by_test_id("live-draft-discard")
    dirty = page.get_by_role("dialog", name="Ungesicherte Änderungen", exact=True)
    confirm = page.get_by_role("dialog").filter(has=page.get_by_role("button", name="Entwurf verwerfen", exact=True))

    expect(rows).to_have_count(0)
    expect(view).to_have_count(0)
    expect(picker.get_by_test_id("clinic-live-open")).to_be_disabled()
    _capture(page, "empty")
    _create(page, a, A)
    close.click()
    expect(view).to_have_count(0)
    _create(page, b, B)
    expect(rows).to_have_count(2)
    text.fill(TEXT_B)
    with page.expect_response(lambda response: response.url == base + API + f"/{b}"
                              and response.request.method == "PUT") as saved:
        save.click()
    assert saved.value.ok, saved.value.text()
    open_a.click()
    expect(text).to_have_value("")
    text.fill(TEXT_A)
    _capture(page, "populated-open")

    before = _draft(page, base, a)
    open_b.click()
    expect(dirty).to_be_visible()
    _capture(page, "dirty-dialog")
    page.keyboard.press("Escape")
    expect(dirty).to_be_hidden()
    expect(open_b).to_be_focused()
    expect(text).to_have_value(TEXT_A)

    text.focus()
    page.go_back()
    expect(dirty).to_be_visible()
    assert page.url.endswith("#/live")
    page.keyboard.press("Escape")
    expect(text).to_be_focused()
    expect(text).to_have_value(TEXT_A)

    close.click()
    expect(dirty).to_be_visible()
    dirty.get_by_role("button", name="Abbrechen", exact=True).click()
    expect(dirty).to_be_hidden()
    expect(close).to_be_focused()
    expect(text).to_have_value(TEXT_A)
    assert _draft(page, base, a) == before

    close.click()
    dirty.get_by_role("button", name="Speichern", exact=True).click()
    expect(view).to_have_count(0)
    assert _draft(page, base, a)["text"] == TEXT_A
    # Reload with the draft still selected and a valid holder in this tab.
    open_a.click()
    expect(text).to_have_value(TEXT_A)
    with page.expect_response(lambda response: response.url == base + API + f"/{a}/switch",
                              timeout=30000) as reopened:
        page.reload()
    assert reopened.value.ok, reopened.value.text()
    expect(open_a).to_be_visible()
    expect(open_b).to_be_visible()
    expect(text).to_have_value(TEXT_A)
    open_b.click()
    expect(text).to_have_value(TEXT_B)
    text.fill("Diese Änderung an B wird nicht gespeichert.")

    # Both the dedicated action and the dirty-switch choice must confirm the named draft.
    discard.click()
    expect(confirm).to_contain_text(B)
    page.keyboard.press("Escape")
    expect(confirm).to_be_hidden()
    expect(discard).to_be_focused()
    open_a.click()
    dirty.get_by_role("button", name="Verwerfen", exact=True).click()
    expect(confirm).to_contain_text(B)
    _capture(page, "discard-dialog")
    assert _draft(page, base, b)["discarded_at"] is None
    confirm.get_by_role("button", name="Entwurf verwerfen", exact=True).click()
    expect(open_b).to_have_count(0)
    expect(text).to_have_value(TEXT_A)
    archived = _draft(page, base, b)
    assert archived["discarded_at"] and archived["text"] == TEXT_B
    close.click()
    expect(view).to_have_count(0)
    assert not page.js_errors, page.js_errors


def _fresh_login(browser: Browser, base: str) -> None:
    page = _login(browser, base, AUTHOR)
    try:
        _picker(page, base)
        picker = page.get_by_test_id("clinic-live-picker")
        expect(picker.get_by_test_id("live-draft-open")).to_have_text([A])
        picker.get_by_role("button", name=A, exact=True).click()
        expect(page.get_by_test_id("live-draft-text")).to_have_value(TEXT_A)
        assert not page.js_errors, page.js_errors
    finally:
        page.context.close()


def test_named_live_drafts_survive_reload_and_keep_originals(draft_app: tuple[str, dict[str, list[str]]]) -> None:
    base, originals = draft_app
    a, b = originals
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, AUTHOR)
            _run_staged_flow(page, base, a, b)
            page.context.close()
            _fresh_login(browser, base)
        finally:
            browser.close()
    for path, digest in originals.values():
        assert sha256(Path(path).read_bytes()).hexdigest() == digest
