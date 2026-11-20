"""Direct app mounts must resolve the recording choices used by activity and the archive."""

from playwright.sync_api import expect


def test_admin_boots_when_activity_and_archive_modules_are_imported(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states["admin"])
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Our admin has no statistics grant. The page must still mount and explain the denial.
        page.goto(app_base + "/admin#/aktivitaet")
        expect(page.get_by_test_id("clinic-admin-denied")).to_contain_text("Aktivität")
        assert not errors, errors
    finally:
        context.close()
