"""UX-31 with the real clinic list, editor, and a synthetic V8 database."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


@pytest.fixture(scope="module")
def app_base(tmp_path_factory):
    seed = '''
    for title, kind, status, resume in (
        ("Persönliche Testnotiz", "notiz", workflow.DRAFT, None),
        ("Text wird geschrieben", "bericht", workflow.IN_PROGRESS, workflow.TO_WRITE),
        ("Text wird geprüft", "bericht", workflow.IN_PROGRESS, workflow.TO_CORRECT),
        ("Text wartet auf Prüfung", "bericht", workflow.TO_CORRECT, None),
    ):
        rec = Recording(user_id=author.id, title=title, status="COMPLETED", original_filename="s.wav")
        db.session.add(rec)
        db.session.flush()
        item = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool.id,
                               configured_recording_type=kind)
        db.session.add(item)
        db.session.flush()
        if status == workflow.TO_CORRECT:
            original = ClinicOriginal(dictation_id=item.id, content_hash="p" * 64, size_bytes=1000,
                                      storage_key="orig/" + uuid.uuid4().hex, receipt="stored",
                                      origin_ref="device:ux31-synthetic")
            db.session.add(original)
            db.session.flush()
            add_asr_revision(item, original, "Synthetischer Text zur Prüfung.", "model:synthetic")
        db.session.add(workflow.ClinicWorkflowDictation(
            id=item.id, author_user_id=author.id, pool_id=pool.id, status=status, resume_status=resume))
    db.session.commit()
'''
    source = support.SERVER.replace('server = make_server', seed + 'server = make_server')
    with support.serve_app(tmp_path_factory.mktemp('ux31'), source) as base:
        yield base


def test_text_pruefen_keyboard_and_details(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states['author'], viewport={'width': 390, 'height': 844})
    try:
        page = context.new_page()
        support._show_recordings(page, app_base)
        records = page.get_by_test_id('clinic-recordings-view')
        buttons = records.get_by_role('button', name='Text prüfen', exact=True)
        expect(buttons).to_have_count(2)
        for title in ('Text wird geschrieben', 'Persönliche Testnotiz'):
            other = records.locator('.cl-reference-row').filter(has_text=title)
            expect(other.get_by_role('button', name='Text prüfen', exact=True)).to_have_count(0)
        card = records.locator('.cl-reference-row').filter(has_text='Text wartet auf Prüfung')
        row = card.get_by_test_id('open-link')
        dictation = row.get_attribute('data-id')
        row.focus()
        page.keyboard.press('Tab')
        action = card.get_by_role('button', name='Text prüfen', exact=True)
        expect(action).to_be_focused()
        assert action.evaluate(
            "button => button.matches(':focus-visible') && getComputedStyle(button).outlineStyle !== 'none'"
        )
        assert action.evaluate('(button) => button.getBoundingClientRect().height') >= 48
        page.keyboard.press('Enter')
        expect(page).to_have_url(app_base + '/#/diktat')
        expect(page.get_by_test_id('clinic-correction-view')).to_have_attribute('data-open-mode', 'correction')
        assert page.evaluate('history.state.dictationId') == dictation
        correction = page.request.get(app_base + f'/api/clinic/v1/dictations/{dictation}/correction')
        assert correction.ok and correction.json()['text'] == 'Synthetischer Text zur Prüfung.'
        page.get_by_test_id('clinic-correction-back').click()
        card.get_by_role('button', name='Details öffnen', exact=True).click()
        expect(page).to_have_url(app_base + '/#/diktat/details')
        expect(page.get_by_test_id('clinic-detail-view')).to_be_visible()
        assert page.evaluate('history.state.dictationId') == dictation
    finally:
        context.close()


def test_view_only_user_has_no_correction_button(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states['admin'])
    try:
        page = context.new_page()
        support._show_recordings(page, app_base)
        response = page.request.get(app_base + '/api/clinic/v1/recordings?status=zu_korrigieren')
        assert response.ok
        [item] = response.json()['items']
        row = page.locator(f'[data-id="{item["dictation_id"]}"]')
        expect(row).to_be_visible()
        card = page.locator('.cl-reference-row').filter(has=row)
        expect(card.get_by_role('button', name='Text prüfen', exact=True)).to_have_count(0)
        expect(card.get_by_role('button', name='Details öffnen', exact=True)).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize('size', support.VIEWPORTS)
@pytest.mark.parametrize('theme', support.THEMES)
def test_render_recordings_rows_and_empty(browser, app_base, role_states, size, theme):
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(
        viewport={'width': width, 'height': height}, storage_state=role_states['author'],
        **support.theme_context_options(theme),
    )
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        support._show_recordings(page, app_base)
        records = page.get_by_test_id('clinic-recordings-view')
        expect(records.get_by_test_id('open-link')).to_have_count(5)
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
        if width < 700:
            records.get_by_test_id('filters-toggle').click()
        action = records.get_by_role('button', name='Text prüfen', exact=True).first
        expect(action).to_be_visible()
        action.evaluate("button => button.parentElement.scrollIntoView({ block: 'nearest' })")
        expect(action).to_be_in_viewport(ratio=1)
        expect(action).to_have_css('transition-duration', '0s')
        _capture(page, f'aufnahmen-{size}-{theme}-rows')
        records.get_by_test_id('filter-status').select_option('abgenommen')
        empty = records.get_by_test_id('empty-state')
        expect(empty).to_be_visible()
        empty.get_by_role('button').scroll_into_view_if_needed()
        _capture(page, f'aufnahmen-{size}-{theme}-empty')
    finally:
        context.close()


def _capture(page, stem):
    if not (directory := os.getenv('UX31_ARTIFACTS')):
        return
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out / f'{stem}.png', animations='disabled', caret='hide')
    html = page.evaluate('''(project) => {
        const root = document.documentElement.cloneNode(true);
        for (const [selector, attribute] of [['link[href]', 'href'], ['img[src]', 'src']]) {
            for (const element of root.querySelectorAll(selector)) {
                const url = new URL(element.getAttribute(attribute), location.href);
                if (url.origin === location.origin && url.pathname.startsWith('/static/')) {
                    element.setAttribute(attribute, project + url.pathname);
                } else if (url.protocol !== 'data:') {
                    element.remove();
                }
            }
        }
        root.querySelectorAll('script, meta[name="csrf-token"], input[name="csrf_token"]').forEach((node) => node.remove());
        return '<!doctype html>\\n' + root.outerHTML;
    }''', os.path.relpath(support.ROOT, out.resolve()))
    (out / f'{stem}.html').write_text(html, encoding='utf-8')
