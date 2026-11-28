"""PAR-WF-014: render clinic notices and open their exact, currently readable dictation."""

from pathlib import Path

from playwright.sync_api import expect

import v8_browser_support as support


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / ".wp" / "artifacts"
LEAD_MEMBERSHIP = '    db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))\n'
SEED = '''
with app.app_context():
    from src.clinic import notification_rules
    from src.services import notifications

    first = ClinicDictation.query.one()
    author = db.session.get(User, first.author_user_id)
    lead = User.query.filter_by(email=emails["lead"]).one()
    admin = User.query.filter_by(is_admin=True).one()
    second_recording = Recording(user_id=author.id, title="Zweites Diktat", status="COMPLETED",
                                 original_filename="second.wav")
    db.session.add(second_recording)
    db.session.flush()
    second = ClinicDictation(recording_id=second_recording.id, author_user_id=author.id,
                             pool_id=first.pool_id, version_counter=0)
    db.session.add(second)
    db.session.flush()
    original = ClinicOriginal(dictation_id=second.id, content_hash="p" * 64, size_bytes=1000,
                              storage_key="orig/" + uuid.uuid4().hex, receipt="stored",
                              origin_ref="device:synthetic")
    db.session.add(original)
    db.session.flush()
    add_asr_revision(second, original, "Zweiter synthetischer Befund.", "model:synthetic")
    init_audio_revision(second, original, 17000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=second.id, author_user_id=author.id, pool_id=second.pool_id,
        assignee_user_id=None, status=workflow.DONE, audio_seconds=17))
    db.session.commit()
    pools.allow(lead.id, "pool", actor_id=admin.id, pool_id=first.pool_id, actions=("view", "play"))
    for event, target in (("accepted", first), ("completed", second)):
        note = notification_rules.build_notification(event, dictation_ref=target.id, recipient_ids=(lead.id,))
        notifications.notify(
            "clinic.workflow", "clinic.workflow." + event, user_ids=[lead.id],
            params={"title": note.title, "body": note.body}, link=note.url_path,
            dedupe_key="test:" + event)
'''


def _server_source():
    source = support.SERVER.replace(LEAD_MEMBERSHIP, "")
    assert source != support.SERVER
    return source.replace("server = make_server", SEED + "\nserver = make_server")


def _open_notices(page, base, path="/"):
    page.goto(base + path)
    root = page.locator("#app" if path == "/" else "#global-header")
    expect(root).to_have_attribute("data-v-app", "")
    if path == "/":
        support._wait_loading_overlay_gone(page)
    root.locator("[data-user-menu-toggle]").click()
    root.locator("[data-notifications-toggle]").click()
    panel = root.locator("[data-notifications-panel]")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Diktat abgenommen")
    expect(panel).to_contain_text("Das Diktat wurde abgenommen.")
    expect(panel).to_contain_text("Diktat fertiggestellt")
    expect(panel).to_contain_text("Das Diktat ist fertiggestellt.")
    expect(panel).not_to_contain_text("clinic.workflow.")
    return panel


def _target_id(context, base):
    notices = context.request.get(base + "/api/notifications").json()["notifications"]
    completed = next(row for row in notices if row["message_key"] == "clinic.workflow.completed")
    return completed["link"].rsplit("/", 1)[-1]


def _open_target(page, base, target_id, expected_status):
    link = page.locator(f'[data-notification-open][href*="dictation={target_id}"]')
    expect(link).to_be_visible()
    endpoint = f"/api/clinic/v1/dictations/{target_id}/detail"
    with page.expect_response(lambda response: response.url.endswith(endpoint)) as pending:
        link.click()
    response = pending.value
    assert response.status == expected_status, response.text()
    expect(page).to_have_url(base + "/#/diktat/details")
    return response


def test_notice_navigation_from_main_account_and_after_revoke(browser, tmp_path):
    with support.serve_app(tmp_path, _server_source()) as base:
        admin_state = support.login(browser, base, support.ROLES["admin"])
        lead_state = support.login(browser, base, support.ROLES["lead"])
        admin = browser.new_context(storage_state=admin_state)
        lead = browser.new_context(storage_state=lead_state)
        try:
            target_id = _target_id(lead, base)
            page = lead.new_page()
            for path in ("/", "/account"):
                _open_notices(page, base, path)
                response = _open_target(page, base, target_id, 200)
                assert response.json()["dictation_id"] == target_id
                expect(page.get_by_test_id("detail-header")).to_be_visible()
                expect(page.get_by_test_id("metadata")).to_contain_text("00:17")
                stored = page.evaluate("""() => [localStorage, sessionStorage].flatMap(store =>
                    Array.from({length: store.length}, (_, index) => store.getItem(store.key(index)) || ''))""")
                assert not any(target_id in value or "Das Diktat ist fertiggestellt." in value for value in stored)

            admin_page = admin.new_page()
            admin_page.goto(base + "/admin#/benutzer")
            users = admin_page.get_by_test_id("clinic-users-view")
            users.get_by_role("button", name="Fachrechte", exact=True).click()
            grants = users.get_by_role("region", name="Fachrechte", exact=True)
            user_id = grants.get_by_label("Benutzer", exact=True).select_option(label="lead-v860")[0]
            entry = grants.get_by_role("listitem").filter(has_text="Autoren V860")
            with admin_page.expect_response(lambda response: response.request.method == "DELETE") as removed:
                entry.get_by_role("button", name="Entziehen").click()
            assert removed.value.status == 200
            assert f"/identity/users/{user_id}/grants/" in removed.value.url

            _open_notices(page, base, "/account")
            _open_target(page, base, target_id, 404)
            expect(page.get_by_test_id("denied-state")).to_contain_text("Dieses Diktat ist nicht verfügbar")
            expect(page.get_by_test_id("detail-header")).to_have_count(0)
            expect(page.get_by_test_id("clinic-dictation-mount")).not_to_contain_text("00:17")
            expect(page.get_by_test_id("clinic-dictation-mount")).not_to_contain_text("author-v860")
        finally:
            lead.close()
            admin.close()


def test_notice_visual_matrix_and_empty_state(browser, tmp_path):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with support.serve_app(tmp_path, _server_source()) as base:
        state = support.login(browser, base, support.ROLES["lead"])
        for size, (width, height) in support.VIEWPORTS.items():
            for theme in support.THEMES:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, storage_state=state,
                    **support.theme_context_options(theme))
                try:
                    context.add_init_script(support.theme_init_script(theme))
                    context.route("**/api/notifications/*", lambda route: route.fulfill(
                        status=200, content_type="application/json", body='{"unread_count": 0}'
                    ) if route.request.method == "DELETE" else route.continue_())
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    panel = _open_notices(page, base)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(ARTIFACTS / f"notices-{size}-{theme}-rows.png"),
                                    full_page=True, animations="disabled")
                    for remaining in (1, 0):
                        panel.locator('[title="Ausblenden"]').first.click()
                        expect(panel.locator('[title="Ausblenden"]')).to_have_count(remaining)
                    expect(panel.locator("[data-notification-open]")).to_have_count(0)
                    expect(panel).to_contain_text("Nichts erfordert Ihre Aufmerksamkeit.")
                    page.screenshot(path=str(ARTIFACTS / f"notices-{size}-{theme}-empty.png"),
                                    full_page=True, animations="disabled")
                    assert not errors, errors
                finally:
                    context.close()
