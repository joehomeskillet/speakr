"""P-253: real clinic shell, opt-in binding and in-page recorder pinning."""

import pytest
from playwright.sync_api import Page, Response, Route, expect, sync_playwright

import test_audit_live_draft_picker_browser as drafts
from test_audit_live_draft_picker_browser import draft_app  # noqa: F401
from test_wp_mount_correction import _launch, _login


def _release(response: Response) -> bool:
    return response.url.endswith('/pause') and response.request.post_data_json.get('release') is True


def _open(page: Page, title: str) -> None:
    page.get_by_test_id('live-draft-open').and_(page.get_by_role('button', name=title, exact=True)).click()
    expect(page.get_by_role('heading', name=title, exact=True)).to_be_focused()


def _layout(page: Page, actions: list[str]) -> None:
    pin = page.get_by_test_id('live-pin-controls')
    recorder = page.get_by_test_id('live-recorder')
    text = page.get_by_test_id('live-draft-text')
    scroller = page.get_by_test_id('clinic-menu-view')
    before = actions.copy()
    text.fill('Noch nicht gespeicherter Entwurfstext.')
    text.evaluate('node => { node.dataset.sameEditor = "yes"; node.setSelectionRange(5, 14); }')
    pin.focus()
    page.keyboard.press('Space')
    expect(pin).to_be_focused()
    expect(pin).to_have_attribute('aria-pressed', 'true')
    expect(recorder).to_have_css('position', 'sticky')
    page.set_viewport_size({'width': 390, 'height': 844})
    pinned_top = scroller.evaluate('''node => {
        const top = node.getBoundingClientRect().top;
        const recorder = node.querySelector('[data-testid="live-recorder"]');
        const text = node.querySelector('[data-testid="live-draft-text"]');
        text.style.height = '760px';
        node.scrollTop += recorder.getBoundingClientRect().top - top + 60;
        return top + parseFloat(getComputedStyle(node).paddingTop);
    }''')
    assert abs(recorder.bounding_box()['y'] - pinned_top) <= 1
    scroller.evaluate('node => node.scrollTop += 40')
    assert abs(recorder.bounding_box()['y'] - pinned_top) <= 1
    page.keyboard.press('Tab')
    expect(text).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(pin).to_be_focused()
    page.keyboard.press('Space')
    expect(pin).to_be_focused()
    expect(pin).to_have_attribute('aria-pressed', 'false')
    expect(recorder).to_have_css('position', 'static')
    expect(text).to_have_attribute('data-same-editor', 'yes')
    expect(text).to_have_value('Noch nicht gespeicherter Entwurfstext.')
    assert text.evaluate('node => [node.selectionStart, node.selectionEnd]') == [5, 14]
    assert actions == before
    text.evaluate('node => node.style.removeProperty("height")')
    page.get_by_test_id('live-draft-save').click()
    expect(page.get_by_role('status').filter(has_text='gespeichert.')).to_be_visible()


def test_live_layout_rule_is_local_and_switches_off(draft_app: tuple[str, dict[str, list[str]]]) -> None:
    base, originals = draft_app
    a, b = originals
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, drafts.AUTHOR)
            disable_mic = '''() => {
                navigator.mediaDevices.getUserMedia = () => {
                    sessionStorage.setItem('unexpectedMicrophone', 'yes');
                    throw Error('Unexpected microphone');
                };
            }'''
            page.evaluate(disable_mic)
            page.add_init_script(f'({disable_mic})()')
            drafts._picker(page, base)
            drafts._capture(page, 'picker-empty', prefix='p253-live-layout')
            drafts._create(page, a, drafts.A)
            page.get_by_test_id('live-draft-close').click()
            drafts._create(page, b, drafts.B)
            automatic = page.get_by_test_id('live-automatic-binding')
            status = page.get_by_test_id('live-binding-status')
            view = page.get_by_test_id('clinic-live-view')
            expect(automatic).to_have_attribute('aria-pressed', 'false')
            expect(status).to_contain_text('Gebunden: Entwurf B')
            actions = []
            page.on('request', lambda req: actions.append(req.url) if req.method == 'POST' and '/live/' in req.url else None)
            automatic.click()
            expect(automatic).to_have_attribute('aria-pressed', 'true')
            assert not actions
            _open(page, drafts.A)
            expect(status).to_contain_text('Kein Entwurf gebunden')
            page.get_by_test_id('live-draft-text').focus()
            expect(status).to_contain_text('Kein Entwurf gebunden')
            assert not any(url.endswith('/switch') for url in actions)

            expect(automatic).to_have_attribute('aria-pressed', 'true')
            drafts._capture(page, 'rule-active', prefix='p253-live-layout', scroll_to=automatic)
            with page.expect_response(f'{base}{drafts.API}/{b}/switch') as claimed:
                _open(page, drafts.B)
            assert claimed.value.ok, claimed.value.text()
            assert 'acknowledged_holder' not in claimed.value.request.post_data_json
            expect(view).to_have_attribute('data-phase', 'paused')
            expect(status).to_contain_text('Niemand spricht')
            before_focus = actions.copy()
            page.get_by_test_id('live-draft-text').focus()
            expect(page.get_by_test_id('live-draft-text')).to_be_focused()
            assert actions == before_focus
            _layout(page, actions)
            page.get_by_test_id('live-pin-controls').click()
            drafts._capture(page, 'bound-rows', prefix='p253-live-layout', scroll_to=view)
            with page.expect_response(_release) as freed:
                automatic.click()
            assert freed.value.ok, freed.value.text()
            expect(status).to_contain_text('Kein Entwurf gebunden')
            expect(automatic).to_have_attribute('aria-pressed', 'false')
            previous = actions.copy()
            page.reload()
            expect(status).to_contain_text('Kein Entwurf gebunden')
            expect(automatic).to_have_attribute('aria-pressed', 'false')
            expect(page.get_by_test_id('live-pin-controls')).to_have_attribute('aria-pressed', 'false')
            expect(page.get_by_test_id('live-draft-text')).to_have_value('Noch nicht gespeicherter Entwurfstext.')
            assert actions == previous
            assert not page.evaluate('sessionStorage.getItem("unexpectedMicrophone")')
            assert not any(url.endswith('/continue') for url in actions)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize('leave', ['disable', 'cache'])
def test_leaving_rule_releases_late_claim(draft_app: tuple[str, dict[str, list[str]]], leave: str) -> None:
    base, originals = draft_app
    a = next(iter(originals))
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, drafts.AUTHOR)
            drafts._picker(page, base)
            drafts._create(page, a, drafts.A)
            automatic = page.get_by_test_id('live-automatic-binding')
            status = page.get_by_test_id('live-binding-status')
            page.get_by_role('button', name='Bindung lösen').click()
            expect(status).to_contain_text('Kein Entwurf gebunden')
            pending: list[Route] = []

            def hold_claim(route: Route) -> None:
                pending.append(route)
                page.evaluate('document.documentElement.dataset.claimWaiting = "yes"')

            page.route(f'{base}{drafts.API}/{a}/switch', hold_claim, times=1)
            automatic.click()
            expect(page.locator('html')).to_have_attribute('data-claim-waiting', 'yes')
            expect(automatic).to_have_attribute('aria-pressed', 'true')
            if leave == 'disable':
                automatic.click()
                expect(page.get_by_role('status').filter(has_text='Wird ausgeschaltet')).to_be_visible()
            else:
                page.evaluate('''() => {
                    dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
                    dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
                }''')
                expect(automatic).to_have_attribute('aria-pressed', 'false')
            assert len(pending) == 1
            with page.expect_response(_release, timeout=5000) as freed:
                pending[0].continue_()
            assert freed.value.ok, freed.value.text()
            expect(automatic).to_have_attribute('aria-disabled', 'false')
            expect(automatic).to_have_attribute('aria-pressed', 'false')
            expect(status).to_contain_text('Kein Entwurf gebunden')
            expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'idle')
        finally:
            browser.close()
