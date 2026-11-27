"""PAR-ADM-007 through the real recording mount, recipient resolver and send receipt."""

import io
import os
import re
import wave
from pathlib import Path

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


SEED = r'''
    from src.clinic import recipient_defaults as rd
    typist.name = "Mira Schreib"
    lead.name = "Noah Schreib"
    second = pools.create_staff_pool("Schreibteam Nord", actor_id=admin.id)
    pools.link_staff_pool(pool.id, second.id, actor_id=admin.id)
    db.session.add(GroupMembership(group_id=second.group_id, user_id=typist.id, role="member"))
    own_pool = pools.create_author_pool("Aufnahmeteam ohne Schreibdienst", actor_id=admin.id)
    for mode, settings in {
        "person": ("person", [str(typist.id)], rd.CHOICE_DEFAULT_ONLY),
        "team": ("staff_pool", [staff.id], rd.CHOICE_STAFF_POOLS),
        "all": ("all_eligible", [], rd.CHOICE_OPEN),
        "own": ("self_correction", [], rd.CHOICE_OPEN),
        "retry": ("staff_pool", [staff.id], rd.CHOICE_OPEN),
    }.items():
        actor = user("author-" + mode, mode + "@local.test")
        actor.name = "Eva Diktat"
        db.session.commit()
        pools.add_author_member(own_pool.id if mode == "own" else pool.id, actor.id, actor_id=admin.id)
        target_mode, target_ids, policy = settings
        rd.set_default(actor.id, target_mode, target_ids=target_ids, actor_id=actor.id, expected_version=0)
        db.session.add(rd.ClinicRecipientChoiceGrant(user_id=actor.id, choice=policy))
        db.session.commit()
'''


@pytest.fixture(scope="module")
def recipient_server(tmp_path_factory):
    source = support.SERVER.replace('server = make_server', SEED + '\nserver = make_server')
    source = ("import os\nos.environ['SPEAKR_SKIP_BACKGROUND_TASKS'] = 'true'\n"
              "os.environ['TRANSCRIPTION_BASE_URL'] = 'http://127.0.0.1:9/v1'\n" + source)
    with support.serve_app(tmp_path_factory.mktemp("adm-submit"), source) as base:
        yield base


@pytest.fixture
def audio(size, theme):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        # The real upload deduplicates identical audio for an author.
        frames = 8000 + support.VIEWPORTS[size][0] + (1 if theme == "dark" else 0)
        wav.writeframes(bytes(frames * 2))
    return {"name": "synthetisch.wav", "mimeType": "audio/wav", "buffer": buffer.getvalue()}


def capture(page, view, stem, state):
    assert not support.horizontal_overflow(page)["document"]
    for control in view.locator("button:visible, a:visible, select:visible, input:visible").all():
        box = control.bounding_box()
        assert box and box["height"] >= 48 and box["width"] >= 48
        assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    out = os.environ.get("P123_BROWSER_OUT")
    if out:
        path = Path(out) / f"p123-{stem}-{state}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if state.startswith("detail-"):
            view.scroll_into_view_if_needed()
            expect(view).to_be_in_viewport()
        page.screenshot(path=path.with_suffix(".png"), full_page=True, animations="disabled")
        html = page.evaluate(r"""() => {
            const snapshot = document.documentElement.cloneNode(true);
            snapshot.querySelectorAll('script, meta[name="csrf-token"], link[rel="preload"], link[rel="preconnect"]')
                .forEach(node => node.remove());
            snapshot.querySelectorAll('link[href^="/static/"]').forEach(link => {
                link.setAttribute('href', '../..' + link.getAttribute('href').split('?')[0]);
            });
            const selects = document.querySelectorAll('select');
            snapshot.querySelectorAll('select').forEach((select, index) => {
                for (const [i, option] of Array.from(select.options).entries()) {
                    option.toggleAttribute('selected', selects[index].options[i].selected);
                }
            });
            return '<!DOCTYPE html>\n' + snapshot.outerHTML;
        }""")
        path.with_suffix(".html").write_text(html, encoding="utf-8")


def open_details(page, did):
    page.locator(f'[data-row][data-id="{did}"]').click()
    page.get_by_test_id("clinic-dictation-link-detail").click()
    return page.get_by_test_id("routing-target")


@pytest.mark.parametrize(("size", "theme", "scenario"), [
    ("1440x900", "light", "person"),
    ("1440x900", "dark", "team"),
    ("1280x720", "light", "all"),
    ("1280x720", "dark", "retry"),
    ("390x844", "light", "own"),
    ("390x844", "dark", "team"),
])
def test_send_and_reload_routing_target(browser, recipient_server, audio, size, theme, scenario):
    base = recipient_server
    width, height = support.VIEWPORTS[size]
    state = support.login(browser, base, scenario + "@local.test")
    context = browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                  **support.theme_context_options(theme))
    context.add_init_script(support.theme_init_script(theme))
    stem = f"{size}-{theme}"
    try:
        page = context.new_page()
        page.goto(base + "/#/aufnahme")
        view = page.get_by_test_id("clinic-recording-view")
        expect(view.get_by_test_id("rec-start")).to_be_enabled()
        if theme == "dark":
            expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
        else:
            expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))
        capture(page, view, stem, "empty")
        view.get_by_test_id("mode-file").click()
        view.get_by_label("Audiodatei").set_input_files(audio)
        with page.expect_response(base + "/upload") as upload:
            view.get_by_test_id("file-upload").click()
        assert upload.value.ok, upload.value.text()
        did = upload.value.json()["dictation_id"]
        path = f"{base}/api/clinic/v1/dictations/{did}"
        if os.environ.get("P123_BROWSER_OUT"):
            draft_page = context.new_page()
            draft_page.goto(base + "/#/aufnahmen")
            target = open_details(draft_page, did)
            expect(target).to_have_text("Kein Versandziel")
            capture(draft_page, target, stem, "detail-empty")
            draft_page.close()
        stage = view.get_by_test_id("submit-draft")
        stage.click()
        preview = view.get_by_test_id("send-preview")
        default_label, label, mode = {
            "person": ("Mira Schreib", "Mira Schreib", "person"),
            "team": ("Schreibdienst V860", "Schreibteam Nord", "staff_pool"),
            "all": ("Alle berechtigten Schreibkräfte (Mira Schreib, Noah Schreib)",
                    "Alle berechtigten Schreibkräfte (Mira Schreib, Noah Schreib)", "all_eligible"),
            "own": ("Selbst korrigieren (Eva Diktat)", "Selbst korrigieren (Eva Diktat)", "self_correction"),
            "retry": ("Schreibdienst V860", "Noah Schreib", "person"),
        }[scenario]
        expect(preview.get_by_test_id("send-default")).to_have_text(default_label)
        expect(preview.get_by_role("heading", name="Diktat einreichen")).to_be_focused()
        choice = preview.get_by_label("Anderes Ziel wählen")
        if scenario == "person":
            expect(choice).to_have_count(0)
        elif scenario == "team":
            expect(choice.locator("option")).to_have_text(["Vorgabe verwenden", "Schreibteam Nord"])
            choice.select_option(label=label)
        elif scenario == "own":
            expect(choice).to_have_count(0)
        elif scenario == "retry":
            choice.select_option(label=label)
        capture(page, view, stem, "preview")

        confirm = preview.get_by_role("button", name="Jetzt einreichen")
        if scenario == "retry":
            sent = []

            def lost_receipt(route):
                request = route.request
                sent.append((request.post_data_json, request.header_value("if-match"),
                             request.header_value("idempotency-key")))
                response = route.fetch()
                assert response.ok
                if len(sent) == 1:
                    route.fulfill(status=503)
                else:
                    route.fulfill(response=response)

            page.route(path + "/send", lost_receipt)
            confirm.click()
            expect(view.get_by_test_id("status")).to_contain_text("nicht bestätigt")
            expect(choice).to_be_disabled()
            confirm = preview.get_by_role("button", name="Erneut einreichen")

        with page.expect_response(path + "/send") as command:
            confirm.click()
        assert command.value.ok, command.value.text()
        receipt = command.value.json()["routing_target"]
        assert receipt["mode"] == mode and receipt["label"] == label
        posted = command.value.request.post_data_json
        assert posted == {
            "expected_version": 1,
            **({"override": {"mode": mode, "target_ids": receipt["target_ids"]}}
               if scenario in ("team", "retry") else {}),
        }
        if scenario == "retry":
            assert len(sent) == 2 and sent[0] == sent[1]
        expect(view.get_by_test_id("send-receipt")).to_have_text("Ziel: " + label)
        capture(page, view, stem, "receipt")
        view.get_by_test_id("open-created").click()
        page.get_by_test_id("clinic-dictation-link-detail").click()
        expect(page.get_by_test_id("routing-target")).to_have_text(label)
        page.reload()
        target = open_details(page, did)
        expect(target).to_have_text(label)
        capture(page, target, stem, "detail-filled")
        assert page.request.get(path + "/detail").json()["routing_target"] == receipt
    finally:
        context.close()
