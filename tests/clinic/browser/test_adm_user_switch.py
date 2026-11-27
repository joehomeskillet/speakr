"""P-234: exercise the switch dialog in the real clinic shell and session."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import v8_browser_support as support


ARTIFACTS = Path(__file__).resolve().parents[3] / '.wp' / 'artifacts'
DRAFT = 'Synthetischer Text für den Benutzerwechsel.'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,
            args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
        )
        try:
            yield instance
        finally:
            instance.close()


def open_switch(page):
    page.locator('[data-user-menu-toggle]').click()
    page.get_by_role('menuitem', name='Benutzer wechseln').click()
    dialog = page.get_by_role('dialog', name='Benutzer wechseln')
    expect(dialog.get_by_role('button', name='Abbrechen')).to_be_focused()
    return dialog


def open_editor(page, base):
    page.goto(base + '/#/aufnahmen')
    support._wait_loading_overlay_gone(page)
    row = page.locator('[data-row]:visible, [data-card]:visible').first
    expect(row).to_be_visible()
    dictation = row.get_attribute('data-id')
    row.locator('.record-title').click()
    editor = page.get_by_test_id('text')
    expect(editor).to_have_attribute('aria-readonly', 'false')
    return dictation, editor


def sign_in(page, user):
    page.locator('input[name=email]').fill(support.ROLES[user])
    page.locator('input[name=password]').fill(support.PASSWORD)
    page.locator('input[name=submit]').click()
    expect(page.locator('#app')).to_have_attribute('data-v-app', '')


@pytest.mark.parametrize('size', support.VIEWPORTS)
@pytest.mark.parametrize('theme', support.THEMES)
@pytest.mark.parametrize('dirty', (False, True))
def test_switch_dialog_matrix(browser, app_base, role_states, size, theme, dirty):
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(
        storage_state=role_states['author'], viewport={'width': width, 'height': height},
        has_touch=width == 390, **support.theme_context_options(theme),
    )
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        if dirty:
            _, editor = open_editor(page, app_base)
            page.route('**/correction/save', lambda route: route.fulfill(status=503))
            editor.fill(DRAFT)
        else:
            page.goto(app_base + '/#/aufnahmen')
            support._wait_loading_overlay_gone(page)
            page.get_by_test_id('filter-search').fill('Keine Aufnahme mit diesem Namen')
            expect(page.get_by_test_id('empty-state')).to_be_visible()
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
        dialog = open_switch(page)
        save = dialog.get_by_role('button', name='Speichern und abmelden')
        if dirty:
            expect(save).to_be_visible()
        else:
            expect(save).to_be_hidden()
        assert dialog.evaluate('(el) => el.scrollWidth <= el.clientWidth')
        for button in dialog.get_by_role('button').all():
            assert button.bounding_box()['height'] >= 48
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        state = 'draft' if dirty else 'clean'
        stem = ARTIFACTS / f'user-switch-{size}-{theme}-{state}'
        page.screenshot(path=stem.with_suffix('.png'), animations='disabled')
        if size == '390x844' and theme == 'light':
            stem.with_suffix('.html').write_text(page.content(), encoding='utf-8')
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        if dirty:
            expect(editor).to_have_value(DRAFT)
    finally:
        context.close()


def test_cancel_and_discard_text_then_switch_account(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states['author'])
    try:
        page = context.new_page()
        dictation, editor = open_editor(page, app_base)
        original = editor.input_value()
        lease_url = f'{app_base}/api/clinic/v1/dictations/{dictation}/leases'
        before = context.request.get(lease_url).json()
        release_url = lease_url + '/release'
        writes = []
        page.on('request', lambda req: writes.append(req.url) if req.method == 'POST' else None)
        page.route('**/correction/save', lambda route: route.fulfill(status=503))
        editor.fill(DRAFT)
        dialog = open_switch(page)
        dialog.get_by_role('button', name='Abbrechen').click()
        expect(editor).to_have_value(DRAFT)
        after = context.request.get(lease_url).json()
        assert (after['held'], after['generation']) == (True, before['generation'])
        assert app_base + '/logout' not in writes and release_url not in writes
        dialog = open_switch(page)
        dialog.get_by_role('button', name='Verwerfen und abmelden').click()
        expect(dialog).to_contain_text('gehen verloren')
        assert app_base + '/logout' not in writes
        with page.expect_response(app_base + '/logout') as logout:
            with page.expect_response(lambda response: response.url == release_url and response.request.method == 'POST') as released:
                dialog.get_by_role('button', name='Verwerfen und abmelden').click()
            assert released.value.ok
        assert logout.value.ok or logout.value.status == 302
        expect(page.locator('input[name=email]')).to_be_visible(timeout=5000)
        assert writes.index(release_url) < writes.index(app_base + '/logout')
        assert writes.count(app_base + '/logout') == 1
        sign_in(page, 'typist')
        expect(page.locator('#app')).to_have_attribute('data-current-user-name', 'typist-v860')
        expect(page.get_by_test_id('text')).to_have_count(0)
        assert DRAFT not in page.content()
        author = browser.new_context(storage_state=role_states['author'])
        try:
            _, saved = open_editor(author.new_page(), app_base)
            expect(saved).to_have_value(original)
        finally:
            author.close()
    finally:
        context.close()


def test_text_save_precedes_lease_release_and_logout(browser, tmp_path):
    with support.serve_app(tmp_path, support.SERVER) as app_base:
        exercise_text_save(browser, app_base)


def exercise_text_save(browser, app_base):
    context = browser.new_context(storage_state=support.login(browser, app_base, support.ROLES['author']))
    try:
        page = context.new_page()
        dictation, editor = open_editor(page, app_base)
        base = f'{app_base}/api/clinic/v1/dictations/{dictation}'
        responses = []
        page.on('response', lambda response: responses.append((response.url, response.status))
                if response.request.method == 'POST' else None)
        editor.fill(DRAFT)
        dialog = open_switch(page)
        dialog.get_by_role('button', name='Speichern und abmelden').click()
        expect(page.locator('#clinic-switch-saved')).to_be_visible()
        assert responses.index((base + '/correction/save', 200)) < responses.index((base + '/leases/release', 200))
        logout_index = next(i for i, (url, _) in enumerate(responses) if url == app_base + '/logout')
        assert responses.index((base + '/leases/release', 200)) < logout_index
        sign_in(page, 'author')
        _, saved = open_editor(page, app_base)
        expect(saved).to_have_value(DRAFT)
    finally:
        context.close()


def test_recording_save_requires_receipt_before_logout(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states['author'], permissions=['microphone'])
    page = context.new_page()
    page.goto(app_base + '/#/neu')
    view = page.get_by_test_id('clinic-recording-view')
    view.get_by_test_id('rec-start').click()
    page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
    dialog = open_switch(page)
    dialog.get_by_role('button', name='Abbrechen').click()
    expect(view.get_by_test_id('rec-stop')).to_be_visible()
    events = []
    page.on('request', lambda req: events.append(req.url) if req.method == 'POST' else None)
    page.on('response', lambda res: events.append('receipt') if res.url == app_base + '/upload' and res.status == 202 else None)
    page.route('**/upload', lambda route: route.fulfill(status=503), times=1)
    open_switch(page).get_by_role('button', name='Speichern und abmelden').click()
    expect(dialog.get_by_role('alert')).to_contain_text('konnte nicht abgeschlossen werden')
    assert app_base + '/logout' not in events
    dialog.get_by_role('button', name='Abbrechen').click()
    expect(view.get_by_test_id('rec-send')).to_be_visible()
    with page.expect_response(lambda res: res.url == app_base + '/upload' and res.status == 202) as upload:
        open_switch(page).get_by_role('button', name='Speichern und abmelden').click()
    saved = upload.value.json()['dictation_id']
    expect(page.locator('#clinic-switch-saved')).to_be_visible()
    assert events.index('receipt') < events.index(app_base + '/logout')
    assert events.count(app_base + '/logout') == 1
    assert not any(url.endswith('/send') for url in events)
    sign_in(page, 'typist')
    listing = context.request.get(app_base + '/api/clinic/v1/recordings').json()['items']
    assert saved not in {row['dictation_id'] for row in listing}
    assert not page.locator('audio[src^="blob:"]').count()
    context.close()
