"""Browser contract of the shared summary card; only its asynchronous backend is controlled."""
import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server


def _login(browser, base):
    page = mount._login(browser, base, mount.TYPIST)
    # The real clinic service worker can claim a tab just after login. Keep all requests
    # for the controlled summary backend observable by Playwright throughout this test.
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setBypassServiceWorker", {"bypass": True})
    return page


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_summary_from_recording_row_and_detail(live_server, width, height):
    base, did = live_server
    saved = "Synthetische Übersicht.\n<em>Prüftext als Klartext</em>"
    state = {"text": None, "status": "empty", "may_generate": True}
    calls = []
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)
            page.set_viewport_size({"width": width, "height": height})

            def respond(route):
                method = route.request.method
                calls.append(method)
                if method == "POST":
                    assert route.request.post_data_json == {}
                    state.update(status="queued")
                    route.fulfill(status=202, json=state)
                else:
                    route.fulfill(json=state)

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            editor = page.get_by_test_id("clinic-correction-view")
            control = editor.get_by_text("KI-Zusammenfassung", exact=True)
            expect(control).to_be_visible()
            assert not calls
            control.focus()
            control.press("Enter")
            expect(editor.get_by_text("Für dieses Diktat liegt noch keine KI-Zusammenfassung vor.")).to_be_visible()
            assert calls == ["GET"]
            original = editor.get_by_test_id("text").input_value()
            editor.get_by_role("button", name="Zusammenfassung erstellen").click()
            expect(editor.get_by_text("Die KI-Zusammenfassung wartet auf die Verarbeitung.")).to_be_visible()
            state.update(text=saved, status="ready")
            expect(editor.locator(".clinic-summary-text")).to_have_text(saved, timeout=8000)
            assert editor.locator(".clinic-summary-text em").count() == 0
            expect(editor.get_by_test_id("text")).to_have_value(original)
            assert calls.count("POST") == 1
            state.update(may_generate=False)
            page.get_by_test_id("clinic-dictation-link-detail").click()
            detail = page.get_by_test_id("clinic-detail-view")
            expect(detail.locator(".clinic-summary-text")).to_have_text(saved)
            expect(detail.locator(".clinic-dictation-summary button")).to_have_count(0)
            expect(detail.locator(".clinic-summary-text em")).to_have_count(0)
            assert calls.count("POST") == 1
            assert detail.locator(".clinic-dictation-summary").evaluate(
                "e=>e.scrollWidth<=e.clientWidth && e.getBoundingClientRect().right<=innerWidth")
            assert not page.js_errors
        finally:
            browser.close()


def test_summary_error_reconciles_without_reposting(live_server):
    base, did = live_server
    calls = []
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)

            def respond(route):
                calls.append(route.request.method)
                if route.request.method == "POST":
                    route.fulfill(status=503, json={"code": "DEPENDENCY_UNAVAILABLE"})
                else:
                    route.fulfill(json={"text": "Ältere Übersicht", "status": "stale", "may_generate": True})

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            page.get_by_test_id("clinic-dictation-link-detail").click()
            summary = page.get_by_test_id("clinic-detail-view").locator(".clinic-dictation-summary")
            expect(summary.get_by_role("status")).to_have_text("Der Bezug zur aktuellen Textfassung ist nicht bestätigt.")
            summary.get_by_role("button", name="Zusammenfassung neu erstellen").click()
            expect(summary.get_by_role("alert")).to_have_text("Der lokale KI-Dienst ist derzeit nicht verfügbar.")
            expect(summary.locator(".clinic-summary-text")).to_have_text("Ältere Übersicht")
            summary.get_by_role("button", name="Stand erneut laden").click()
            expect(summary.locator(".clinic-summary-text")).to_have_text("Ältere Übersicht")
            assert calls == ["GET", "POST", "GET"]
        finally:
            browser.close()


def test_direct_switch_discards_pending_summary_and_renders_safe_markdown(live_server):
    base, _ = live_server
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)
            external = []
            page.route("https://summary-test.invalid/**", lambda route: (external.append(True), route.abort()))
            # Exercise a direct prop change while the old read is still pending.
            # Use the actual shipped Vue, Marked and summary component.
            page.evaluate("""async () => {
                const { DictationSummary } = await import('/static/js/clinic/dictation-summary-ui.js');
                const { createApp, defineComponent, ref } = window.Vue;
                const ids = ref('first');
                const traces = [];
                let resolveOld;
                const snapshot = (text) => ({outcome:'ok', data:{text, status:'ready', may_generate:false}});
                const client = {request: async path => {
                    traces.push(path);
                    if (path.endsWith('/first/summary')) {
                        return new Promise(done => { resolveOld = () => done(snapshot('Erstes Diktat')); });
                    }
                    return snapshot('### Anlass\\n\\n- **Probe** und [Dokument](https://summary-test.invalid/a)\\n- ![Bild](https://summary-test.invalid/b)\\n\\n<em>Nur Text</em>');
                }};
                const host = document.createElement('main');
                host.id = 'summary-switch-test'; document.body.append(host);
                createApp(defineComponent({components:{DictationSummary},setup:()=>({ids, client}),
                    template:'<DictationSummary :dictation-id="ids" :client="client" expanded />'})).mount(host);
                window.summaryFixture = { selectNext:()=>{ids.value='second'},finishOld:()=>resolveOld(),traces };
            }""")
            page.wait_for_function("window.summaryFixture.traces.length === 1")
            page.evaluate("window.summaryFixture.selectNext()")
            host = page.locator("#summary-switch-test")
            expect(host.get_by_role("heading", name="Anlass", level=3)).to_be_visible()
            expect(host.get_by_role("listitem")).to_have_count(2)
            expect(host.locator("strong")).to_have_text("Probe")
            expect(host.get_by_text("<em>Nur Text</em>", exact=True)).to_be_visible()
            expect(host.locator("a, img, em")).to_have_count(0)
            page.evaluate("window.summaryFixture.finishOld()")
            expect(host.get_by_role("heading", name="Anlass")).to_be_visible()
            expect(host).not_to_contain_text("Erstes Diktat")
            assert page.evaluate("window.summaryFixture.traces") == [
                "/dictations/first/summary", "/dictations/second/summary"]
            assert not external
        finally:
            browser.close()


@pytest.mark.parametrize("pending_method", ["GET", "POST"])
def test_open_summary_tracks_saved_revision_with_pending_request(live_server, pending_method):
    base, did = live_server
    calls = []
    held = []
    subsequent = {"status": 403, "data": {}}
    ready = {"text": "Alte Planfassung", "status": "ready", "may_generate": True}
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)

            def respond(route):
                calls.append(route.request.method)
                if len(calls) == 1:
                    route.fulfill(json=ready)
                elif len(calls) == 2:
                    held.append(route)
                else:
                    route.fulfill(status=subsequent["status"], json=subsequent["data"])

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            editor = page.get_by_test_id("clinic-correction-view")
            card = editor.locator(".clinic-dictation-summary")
            toggle = card.locator("summary")
            field = editor.get_by_test_id("text")
            if not field.is_editable():
                editor.locator("[data-action=acquire]").click()
            expect(field).to_be_editable()
            toggle.click()
            expect(card.locator(".clinic-summary-text")).to_have_text(ready["text"])
            field.fill(field.input_value() + " Neue Abfahrtszeit.")
            expect(card.get_by_text("Die Zusammenfassung verwendet die zuletzt gespeicherte Textfassung.")).to_be_visible()
            with page.expect_request(lambda r: r.url.endswith("/summary") and r.method == pending_method):
                if pending_method == "POST":
                    card.get_by_role("button", name="Zusammenfassung neu erstellen").click()
                else:
                    toggle.click()
                    toggle.click()
            with page.expect_response(lambda r: r.url.endswith("/correction/save") and r.request.method == "POST") as saved:
                editor.get_by_test_id("save").click()
            assert saved.value.status == 200
            expect(editor.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
            expect(card.locator(".clinic-summary-text")).to_have_count(0)
            assert len(held) == 1
            if pending_method == "GET":
                expect(card.get_by_role("alert")).to_contain_text("Berechtigung")
            held[0].fulfill(status=202 if pending_method == "POST" else 200, json=ready)
            expect(card.get_by_role("alert")).to_contain_text("Berechtigung")
            expect(card.locator(".clinic-summary-text")).to_have_count(0)
            assert calls == ["GET", pending_method, "GET"]

            toggle.click()
            previous = field.input_value()
            field.fill(previous + " Neues Ziel.")
            with page.expect_response(lambda r: r.url.endswith("/correction/save") and r.request.method == "POST") as saved:
                editor.get_by_test_id("save").click()
            assert saved.value.status == 200
            expect(editor.get_by_test_id("save-state")).to_have_attribute("data-state", "saved")
            assert calls == ["GET", pending_method, "GET"]
            subsequent.update(status=200, data={**ready, "status": "stale"})
            toggle.click()
            expect(card.get_by_role("status")).to_contain_text("aktuellen Textfassung")
            expect(card.locator(".clinic-summary-text")).to_have_text(ready["text"])
            assert calls == ["GET", pending_method, "GET", "GET"]
            assert not page.js_errors
        finally:
            browser.close()


def test_automatic_summary_waits_for_transcription_only_while_open(live_server):
    base, did = live_server
    calls = []
    state = {"text": None, "status": "waiting_for_transcription", "may_generate": True}
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)

            def respond(route):
                calls.append(route.request.method)
                route.fulfill(json=state)

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            card = page.get_by_test_id("clinic-correction-view").locator(".clinic-dictation-summary")
            toggle = card.locator("summary")
            expect(toggle).to_be_visible()
            page.clock.install(time=1_700_000_000_000)
            page.clock.pause_at(1_700_000_001_000)
            toggle.click()
            waiting = "Die Zusammenfassung wird nach der Transkription automatisch erstellt."
            expect(card.get_by_role("status")).to_have_text(waiting)
            expect(card.get_by_role("button")).to_have_count(0)
            # The native toggle event follows the details element's immediate DOM change.
            card.evaluate("el => { window.summaryClosed = new Promise(done => el.addEventListener('toggle', done, {once:true})); }")
            toggle.click()
            page.evaluate("window.summaryClosed.then(() => true)")
            expect(card.get_by_role("status")).to_be_hidden()
            page.clock.run_for(5100)
            assert calls == ["GET"]
            with page.expect_response(lambda r: r.url.endswith("/summary")):
                toggle.click()
            expect(card.get_by_role("status")).to_have_text(waiting)
            state.update(text="Synthetischer Plan", status="ready")
            page.clock.run_for(2600)
            expect(card.locator(".clinic-summary-text")).to_have_text("Synthetischer Plan")
            assert calls == ["GET", "GET", "GET"]
        finally:
            browser.close()
