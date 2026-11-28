"""PAR-COR-009 on the mounted correction view with synthetic dictation text."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_v8_130_release_browser import _headers


SEED = 'Anamnese Husten seit drei Tagen.'
SECOND_PARAGRAPH = 'Unveränderter Absatz.'


@pytest.fixture
def live_server(tmp_path_factory, monkeypatch, request):
    mode, state = getattr(request, 'param', ('correction', 'populated'))
    server = mount.SERVER
    if mode == 'correction':
        assert 'status=workflow.TO_WRITE' in server
        server = server.replace('status=workflow.TO_WRITE', 'status=workflow.TO_CORRECT')
    assert f'"{SEED}"' in server
    text = '' if state == 'empty' else f'{SEED}\n{SECOND_PARAGRAPH}'
    server = server.replace(f'"{SEED}"', repr(text))
    monkeypatch.setattr(mount, 'SERVER', server)
    for base, did in mount.live_server.__wrapped__(tmp_path_factory):
        yield base, did, mode, state


@pytest.fixture
def correction(live_server):
    base, did, mode, state = live_server
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, mount.TYPIST)
            page.locator(f'[data-testid=open-link][data-id="{did}"]').click()
            expect(page.get_by_test_id('clinic-correction-view')).to_have_attribute('data-open-mode', mode)
            editor = page.get_by_test_id('correction-document')
            field = page.get_by_test_id('text')
            if mode == 'correction':
                expect(editor.locator('[data-node-id]')).to_have_count(1 if state == 'empty' else 2)
                expect(editor.get_by_test_id('text')).to_be_editable()
            else:
                expect(editor).to_have_count(0)
                page.locator('[data-action=acquire]:enabled').click()
            expect(field).to_be_editable()
            yield browser, page, field
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def _selected(field):
    return field.evaluate('el => el.value.slice(el.selectionStart, el.selectionEnd)')


def test_case_preserves_unicode_selection_and_history(live_server, correction):
    base, did, _, _ = live_server
    _, page, field = correction
    other = page.get_by_test_id('correction-document').locator('textarea').nth(1)
    assert other.input_value() == SECOND_PARAGRAPH
    run_ids = page.get_by_test_id('correction-document').locator('textarea').evaluate_all(
        'fields => fields.map(field => field.dataset.runId)'
    )
    field.focus()
    page.locator('[data-tool=bold]').click()
    expect(field).to_have_css('font-weight', '700')
    original = '🩺 vor äÖ straße danach\n\nzweiter Absatz'
    upper = '🩺 vor ÄÖ STRASSE danach\n\nzweiter Absatz'
    lower = '🩺 vor äö strasse danach\n\nzweiter Absatz'
    field.fill(original)
    field.evaluate("el => el.setSelectionRange(7, 16, 'backward')")

    page.get_by_role('button', name='Auswahl in Grossbuchstaben').click(timeout=3000)
    expect(field).to_have_value(upper)
    expect(field).to_be_focused()
    assert _selected(field) == 'ÄÖ STRASSE'
    assert field.evaluate('el => el.selectionDirection') == 'backward'

    lower_button = page.get_by_role('button', name='Auswahl in Kleinbuchstaben')
    lower_button.focus()
    lower_button.press('Enter')
    expect(field).to_have_value(lower)
    expect(field).to_be_focused()
    assert _selected(field) == 'äö strasse'

    for key, value, selection in [('Control+z', upper, 'ÄÖ STRASSE'),
                                  ('Control+z', original, 'äÖ straße'),
                                  ('Control+Shift+z', upper, 'ÄÖ STRASSE'),
                                  ('Control+Shift+z', lower, 'äö strasse')]:
        field.press(key)
        expect(field).to_have_value(value)
        assert _selected(field) == selection
        assert field.evaluate('el => el.selectionDirection') == 'backward'

    field.press('ArrowRight')
    assert _selected(field) == ''
    lower_button.click()
    expect(field).to_have_value(lower)
    field.press('Control+z')
    expect(field).to_have_value(upper)
    expect(other).to_have_value(SECOND_PARAGRAPH)
    expect(field).to_have_css('font-weight', '700')

    page.get_by_test_id('save').click()
    expect(page.get_by_test_id('save-state')).to_have_attribute('data-state', 'saved')
    saved = page.request.get(f'{base}/api/clinic/v1/dictations/{did}/correction').json()
    first, second = [node['runs'][0] for node in saved['document']['nodes']]
    assert [first['id'], second['id']] == run_ids
    assert first['text'] == upper and first['bold'] is True
    assert second['text'] == SECOND_PARAGRAPH and second['bold'] is False

    other.press('Home')
    other.press('Control+Shift+ArrowRight')
    assert _selected(other) == 'Unveränderter'
    page.locator('[data-tool=upper-case]').click()
    expect(other).to_have_value('UNVERÄNDERTER Absatz.')
    expect(field).to_have_value(upper)
    field.focus()
    page.locator('[data-tool=undo]').click()
    expect(other).to_be_focused()
    assert _selected(other) == 'Unveränderter'
    field.focus()
    page.locator('[data-tool=redo]').click()
    expect(other).to_be_focused()
    assert _selected(other) == 'UNVERÄNDERTER'
    expect(field).to_have_value(upper)


def test_native_word_paragraph_and_clipboard_survive_lease_loss(live_server, correction):
    base, did, _, _ = live_server
    browser, page, field = correction
    admin = mount._login(browser, base, 'admin213@local.test')
    page.bring_to_front()
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    field.fill('Ödem\n\nZweiter Absatz')
    field.press('Control+Home')
    field.press('Control+Shift+ArrowRight')
    assert _selected(field) == 'Ödem'
    field.press('Control+c')
    assert page.evaluate('navigator.clipboard.readText()') == 'Ödem'
    field.press('Control+End')
    field.press('Space')
    field.press('Control+v')
    expect(field).to_have_value('Ödem\n\nZweiter Absatz Ödem')
    field.press('Control+Shift+ArrowUp')
    assert _selected(field) == '\nZweiter Absatz Ödem'
    field.press('Control+x')
    expect(field).to_have_value('Ödem\n')
    assert page.evaluate('navigator.clipboard.readText()') == '\nZweiter Absatz Ödem'
    field.press('Control+v')
    expect(field).to_have_value('Ödem\n\nZweiter Absatz Ödem')

    other = page.get_by_test_id('correction-document').locator('textarea').nth(1)
    other.press('Control+Home')
    other.press('Control+Shift+End')
    assert _selected(other) == SECOND_PARAGRAPH
    other.press('Control+c')
    assert page.evaluate('navigator.clipboard.readText()') == SECOND_PARAGRAPH
    field.press('Control+End')
    field.press('Enter')
    field.press('Control+v')
    expect(field).to_have_value(f'Ödem\n\nZweiter Absatz Ödem\n{SECOND_PARAGRAPH}')
    field.press('Control+z')
    field.press('Control+z')
    expect(field).to_have_value('Ödem\n\nZweiter Absatz Ödem')
    expect(other).to_have_value(SECOND_PARAGRAPH)

    takeover = admin.request.post(f'{base}/api/clinic/v1/dictations/{did}/leases/takeover',
                                  headers=_headers(admin, base), data={'confirm': True})
    assert takeover.ok, takeover.text()
    field.press('Control+End')
    page.keyboard.insert_text('.')
    text_at_loss = 'Ödem\n\nZweiter Absatz Ödem.'
    expect(field).to_have_value(text_at_loss)
    page.get_by_test_id('save').click()
    expect(field).not_to_be_editable()
    expect(page.get_by_test_id('lease-bar')).to_contain_text('entzogen')
    for name in ('Auswahl in Grossbuchstaben', 'Auswahl in Kleinbuchstaben'):
        expect(page.get_by_role('button', name=name)).to_be_disabled()
    field.evaluate('el => { el.focus(); el.setSelectionRange(6, el.value.length); }')
    field.press('Control+c')
    assert page.evaluate('navigator.clipboard.readText()') == 'Zweiter Absatz Ödem.'
    field.press('Control+x')
    expect(field).to_have_value(text_at_loss)
    page.evaluate("navigator.clipboard.writeText('Anderer Text')")
    for key in ('Control+v', 'Control+z', 'Control+Shift+z'):
        field.press(key)
        expect(field).to_have_value(text_at_loss)


@pytest.mark.parametrize('live_server', [
    pytest.param(('correction', 'populated'), id='correction-populated'),
    pytest.param(('correction', 'empty'), id='correction-empty'),
    pytest.param(('player', 'populated'), id='player-populated'),
    pytest.param(('player', 'empty'), id='player-empty'),
], indirect=True)
def test_selection_toolbar_visuals(correction, live_server, tmp_path):
    _, page, field = correction
    _, _, mode, state = live_server
    dest = Path(os.environ.get('COR009_ARTIFACTS', tmp_path)) / mode
    dest.mkdir(parents=True, exist_ok=True)
    toolbar = page.get_by_test_id('toolbar')
    buttons = toolbar.get_by_role('button', name='Auswahl in ', exact=False)
    expect(buttons).to_have_count(2)
    if state == 'empty':
        expect(field).to_have_value('')
        if mode == 'correction':
            expect(field).to_have_attribute('placeholder', 'Hier mit dem Schreiben beginnen')
    else:
        value = 'Ödem und äussere Schwellung.'
        if mode == 'correction':
            field.fill(value)
            expect(page.get_by_test_id('correction-document').locator('textarea').nth(1)).to_have_value(SECOND_PARAGRAPH)
        else:
            field.fill(f'{value}\n\n{SECOND_PARAGRAPH}')
    page.mouse.move(0, 0)
    for theme in ('light', 'dark'):
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        for width, height in ((1440, 900), (1280, 720), (390, 844)):
            page.set_viewport_size({'width': width, 'height': height})
            toolbar.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
            assert toolbar.evaluate('el => el.scrollWidth <= el.clientWidth'), (mode, state, theme, width, height)
            assert toolbar.evaluate('''el => {
                const bounds = el.getBoundingClientRect();
                return Array.from(el.querySelectorAll('button')).every(button => {
                    const rect = button.getBoundingClientRect();
                    return rect.left >= bounds.left && rect.right <= bounds.right;
                });
            }'''), (mode, state, theme, width, height)
            for button in buttons.all():
                expect(button).to_be_in_viewport(ratio=1)
            page.screenshot(path=dest / f'{state}-{width}x{height}-{theme}.png', animations='disabled')
