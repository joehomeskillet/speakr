"""PAR-LIVE-005 through the real P05 shell, correction routes and graphic store."""

from base64 import b64encode
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from test_audit_richtext_graphics import png


ART = Path(__file__).resolve().parents[2] / ".wp" / "artifacts"
ROWS = "22400000-0000-4000-8000-000000000001"
EMPTY = "22400000-0000-4000-8000-000000000002"
GRAPHIC = png(80, 40, b"".join(
    b"\x00" + b"\x4f\x87\x84\xff" * 80 if row % 10 < 5 else b"\x00" + b"\xe9\xf1\xed\xff" * 80
    for row in range(40)))
SEED = f'''
from datetime import datetime, timezone
from src.clinic import lease
from src.clinic.correction import link_asr_segments
from src.clinic.revisions import current_revision
from src.clinic.richtext import CanonicalDocument, Node, Segment, save_document

with app.app_context():
    dic = ClinicDictation.query.one()
    author = db.session.get(User, dic.author_user_id)
    head = current_revision(dic)
    parts = ("Anamnese", "Husten seit drei Tagen.")
    links = link_asr_segments(dic, head, [
        dict(text=part, start=i, end=i + 1) for i, part in enumerate(parts)])
    now = datetime.now(timezone.utc)
    held = lease.acquire(dic.id, author.id, now)
    document = CanonicalDocument(tuple(
        Node(f"absatz-{{i}}", "paragraph",
             (Segment(id=f"text-{{i}}", text=part, audio_anchor_id=links[i].id),))
        for i, part in enumerate(parts)))
    save_document(dic, head, document, expected_version=0, actor_user_id=author.id,
                  lease_generation=held.generation, now=now)
    lease.release(held, held.generation, now)
    rec = Recording(user_id=author.id, title="Leeres Diktat", status="COMPLETED")
    db.session.add(rec)
    db.session.flush()
    empty = ClinicDictation(id="{EMPTY}", recording_id=rec.id, author_user_id=author.id,
                            pool_id=dic.pool_id, version_counter=0)
    db.session.add(empty)
    db.session.commit()
'''


def _capture(page, editor, stem, has_graphic):
    sizes = editor.locator("button, input, select, summary, textarea").evaluate_all(
        """elements => elements.filter(el => el.getClientRects().length).map(el => {
            const {width, height} = el.getBoundingClientRect();
            return [width, height];
        })""")
    assert sizes and all(w >= 47.5 and h >= 47.5 for w, h in sizes), sizes
    page.screenshot(path=str(stem.with_suffix(".png")), full_page=True, animations="disabled", caret="hide")
    # Render the saved DOM from disk using the worktree's real stylesheets.
    dom = page.evaluate(r"""() => {
        const root = document.documentElement.cloneNode(true);
        root.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
        const selector = '.clinic-richtext textarea, .clinic-richtext select';
        const controls = document.querySelectorAll(selector);
        root.querySelectorAll(selector).forEach((el, i) => {
            if (el.tagName === 'TEXTAREA') el.textContent = controls[i].value;
            else [...el.options].forEach((option, j) => option.toggleAttribute('selected',
                j === controls[i].selectedIndex));
        });
        return '<!doctype html>\n' + root.outerHTML;
    }""")
    dom = dom.replace('href="/static/', 'href="../../static/').replace('src="/static/', 'src="../../static/')
    if has_graphic:
        src = editor.get_by_role("img", name="Synthetische Grafik").get_attribute("src")
        dom = dom.replace(src, "data:image/png;base64," + b64encode(GRAPHIC).decode("ascii"))
    stem.with_suffix(".html").write_text(dom, encoding="utf-8")


def _shots(page, support, label):
    ART.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        page.set_viewport_size(dict(width=width, height=height))
        for theme in support.THEMES:
            page.evaluate(support.theme_init_script(theme))
            page.reload()
            editor = page.get_by_role("region", name="Textformatierung")
            expect(editor).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
            page.get_by_role("button", name="Bearbeiten", exact=True).press("Enter")
            expect(editor.get_by_role("textbox").first).to_be_editable()
            expect(editor.get_by_role("textbox").first).to_be_in_viewport()
            assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
            overflow = support.horizontal_overflow(page)
            assert not (overflow["document"] or overflow["body"]), (label, size, theme, overflow)
            _capture(page, editor, ART / f"p224-live-{label}-{size}-{theme}", label == "rows")
            if label == "rows" and width == 390:
                editor.get_by_role("button", name="Formatieren").press("Enter")
                editor.get_by_role("button", name="Unterstrichen").scroll_into_view_if_needed()
                _capture(page, editor, ART / f"p224-live-format-{size}-{theme}", True)


def test_live_document_round_trip_and_foreign_holder(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent / "browser"))
    monkeypatch.setenv("SPEAKR_SKIP_BACKGROUND_TASKS", "true")
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", "http://127.0.0.1:9/v1")
    import v8_browser_support as support

    source = support.SERVER.replace("dic = ClinicDictation(", f'dic = ClinicDictation(id="{ROWS}", ', 1)
    source = source.replace("Anamnese Husten seit drei Tagen.", r"Anamnese\nHusten seit drei Tagen.")
    source = source.replace("server = make_server", SEED + "\nserver = make_server")
    with support.serve_app(tmp_path, source) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        author = support.login(browser, base, support.ROLES["author"])
        page = browser.new_context(storage_state=author, viewport=dict(width=390, height=844),
                                   locale="de-CH", reduced_motion="reduce").new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        target = base + "/?dictation=" + ROWS + "#/live"
        endpoint = base + "/api/clinic/v1/dictations/" + ROWS
        page.goto(base + "/?dictation=" + EMPTY + "#/live")
        editor = page.get_by_role("region", name="Textformatierung")
        expect(editor).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
        expect(editor.get_by_role("textbox").first).to_have_value("")
        _shots(page, support, "empty")

        page.goto(target)
        first = editor.locator('[data-run-id="text-0"]')
        expect(first).to_have_value("Anamnese")
        initial = page.request.get(endpoint + "/correction").json()
        page.get_by_role("button", name="Bearbeiten", exact=True).press("Enter")
        expect(first).to_be_editable()
        first.focus()
        first.press("Control+b")
        editor.get_by_role("button", name="Formatieren").press("Enter")
        editor.get_by_label("Schriftart").press("s")
        editor.get_by_label("Schriftgrösse").fill("18")
        editor.get_by_label("Schriftgrösse").press("Tab")
        editor.get_by_label("Ausrichtung").press("ArrowDown")
        editor.get_by_label("Ausrichtung").press("ArrowDown")
        editor.get_by_label("Zeilenabstand").press("ArrowDown")
        editor.get_by_label("Zeilenabstand").press("ArrowDown")
        editor.get_by_label("Zeilenabstand").press("ArrowDown")
        editor.get_by_role("button", name="Überschrift").press("Space")
        first_attempt = []

        def lose_receipt(route):
            first_attempt.append((route.request.headers["idempotency-key"], route.request.post_data_json))
            assert route.fetch().status == 200
            route.abort()

        page.route(endpoint + "/correction/save", lose_receipt, times=1)
        editor.get_by_role("button", name="Speichern", exact=True).press("Enter")
        retry = editor.get_by_role("button", name="Erneut versuchen")
        expect(retry).to_be_visible()
        first.fill("Verlauf")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.get_by_test_id("nav-recordings").press("Enter")
        expect(first).to_have_value("Verlauf")
        with page.expect_request(endpoint + "/correction/save") as replay:
            retry.press("Enter")
        assert first_attempt == [(replay.value.headers["idempotency-key"], replay.value.post_data_json)]
        expect(editor.get_by_role("status")).to_have_text("Weitere Änderungen sind noch nicht gespeichert.")
        expect(first).to_have_value("Verlauf")
        formatted = page.request.get(endpoint + "/correction").json()
        assert formatted["text_revision_id"] == initial["text_revision_id"]
        assert formatted["document_version"] == initial["document_version"] + 1
        block = formatted["document"]["nodes"][0]
        assert (block["kind"], block["text_align"], block["line_spacing"]) == ("heading", "center", 1.5)
        assert block["runs"][0]["bold"] is True
        assert block["runs"][0]["font_family"] == "serif"
        assert block["runs"][0]["font_size_pt"] == 18

        editor.get_by_text("Grafik einfügen", exact=True).press("Enter")
        editor.get_by_label("Bildbeschreibung").fill("Synthetische Grafik")
        with page.expect_file_chooser() as chooser:
            editor.get_by_label("PNG-Datei").press("Enter")
        chooser.value.set_files(dict(name="grafik.png", mimeType="image/png", buffer=GRAPHIC))
        editor.get_by_role("button", name="Einfügen", exact=True).press("Enter")
        icon = editor.get_by_role("img", name="Synthetische Grafik")
        expect(icon).to_be_visible()
        editor.get_by_role("button", name="Speichern", exact=True).press("Enter")
        expect(editor.get_by_role("status")).to_have_text("Gespeichert.")
        saved = page.request.get(endpoint + "/correction").json()
        text, graphic, neighbor = saved["document"]["nodes"]
        assert text["id"] == block["id"]
        assert text["runs"][0]["id"] == "text-0"
        assert text["runs"][0]["audio_anchor_id"] is None
        assert neighbor == formatted["document"]["nodes"][1]
        assert saved["text"] == "Verlauf\n[graphic: Synthetische Grafik]\nHusten seit drei Tagen."
        assert graphic["kind"] == "graphic"
        assert page.request.get(endpoint + "/graphics/" + graphic["object_id"]).body() == GRAPHIC
        other_scope = base + "/api/clinic/v1/dictations/" + EMPTY
        assert page.request.get(other_scope + "/graphics/" + graphic["object_id"]).status == 404
        _shots(page, support, "rows")
        expect(first).to_have_value("Verlauf")
        expect(first).to_have_css("font-weight", "700")
        expect(first).to_have_css("text-align", "center")
        expect(first).to_have_css("font-size", "24px")
        expect(first).to_have_css("line-height", "36px")
        assert icon.evaluate("element => element.complete && element.naturalWidth > 0")

        first.fill("Lokaler Entwurf")
        assert page.evaluate("""async scope => {
            const {createClinicApiClient} = await import('/static/js/clinic/api-client.js');
            const client = createClinicApiClient();
            const {data} = await client.request(scope + '/correction');
            const lock = (await client.request(scope + '/leases')).data;
            data.document.nodes[0].text_align = 'right';
            return (await client.request(scope + '/correction/save', {
                method: 'POST', ifMatch: data.etag, idempotencyKey: crypto.randomUUID(),
                body: { text: data.text, document: data.document,
                    expected_document_version: data.document_version, expected_text_revision_id: data.text_revision_id,
                    lease_generation: lock.generation, audio_position_ms: data.state.audio_position_ms,
                    expected_state_version: data.state.version },
            })).status;
        }""", endpoint.removeprefix(base)) == 200
        editor.get_by_role("button", name="Speichern", exact=True).press("Enter")
        expect(editor.get_by_role("status")).to_contain_text("Die Änderungen sind noch lokal vorhanden.")
        reload_current = editor.get_by_role("button", name="Aktuellen Stand laden")
        page.once("dialog", lambda dialog: dialog.dismiss())
        reload_current.press("Enter")
        expect(first).to_have_value("Lokaler Entwurf")
        page.once("dialog", lambda dialog: dialog.accept())
        reload_current.press("Enter")
        expect(first).to_have_value("Verlauf")
        expect(first).to_have_css("text-align", "right")
        page.get_by_role("button", name="Bearbeitung beenden").press("Enter")
        expect(first).not_to_be_editable()

        typist = support.login(browser, base, support.ROLES["typist"])
        other = browser.new_context(storage_state=typist).new_page()
        other.goto(base + "/")
        other.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)
        assert other.evaluate("""async path => {
            const {createClinicApiClient} = await import('/static/js/clinic/api-client.js');
            return (await createClinicApiClient().request(path, {
                method: 'POST', idempotencyKey: crypto.randomUUID()
            })).status;
        }""", endpoint.removeprefix(base) + "/leases") == 200
        page.reload()
        expect(page.get_by_text("Dieses Diktat wird von einer anderen Person bearbeitet.")).to_be_visible()
        expect(first).not_to_be_editable()
        expect(editor.get_by_role("button", name="Speichern", exact=True)).to_be_disabled()
        expect(editor.get_by_role("button", name="Formatieren")).to_be_disabled()
        expect(editor.get_by_role("button", name="Grafik einfügen")).to_be_disabled()
        assert not errors, errors
        browser.close()
