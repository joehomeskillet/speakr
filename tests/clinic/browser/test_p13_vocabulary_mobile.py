"""P13 density and live feedback against real isolated vocabulary API data."""

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


@pytest.fixture(scope="module")
def vocabulary_base(tmp_path_factory):
    seed = '''
    from src.clinic import vocabulary
    pools.add_author_member(pool.id, admin.id, actor_id=admin.id)
    vocabulary.add_vocabulary_entry('pool', 'Frühdienstübergabe', pool_id=pool.id)
    vocabulary.add_vocabulary_entry('pool', 'Teamnotiz', pool_id=pool.id)
    vocabulary.add_vocabulary_entry('pool', 'Beispielausdruck', pool_id=pool.id, status='suppressed')
    vocabulary.add_vocabulary_entry('user', 'Privatwort', user_id=admin.id)
    vocabulary.add_vocabulary_entry('user', 'Fremdwort', user_id=author.id)
    db.session.commit()
'''
    source = support.SERVER.replace('server = make_server', seed + '\nserver = make_server')
    with support.serve_app(tmp_path_factory.mktemp('p13-vocabulary'), source) as base:
        yield base


@pytest.mark.parametrize('width,height', [(390, 844), (1440, 900)])
def test_p13_compact_filters_and_accessible_scope_feedback(browser, vocabulary_base, width, height):
    state = support.login(browser, vocabulary_base, support.ROLES['admin'])
    context = browser.new_context(storage_state=state, viewport={'width': width, 'height': height})
    try:
        page = context.new_page()
        page.goto(vocabulary_base + '/admin#/wortschatz')
        view = page.locator('.vocabulary-ui')
        rows = view.locator('tbody tr')
        picker = page.get_by_label('Wortliste', exact=True)
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('Privatwort')
        picker.select_option(label='Autoren V860')
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text('Frühdienstübergabe')
        page.evaluate('window.scrollTo(0, 0)')
        box = rows.first.bounding_box()
        assert box['y'] + box['height'] < height
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        feedback = view.locator('.voc-feedback')
        expect(feedback).to_have_attribute('aria-live', 'polite')
        expect(feedback).to_have_attribute('aria-atomic', 'true')
        expect(feedback).to_contain_text('Autoren V860')
        assert feedback.bounding_box()['height'] <= 1
        expect(view.locator('.voc-result-count')).to_have_text('2 Einträge')
        page.get_by_label('Status', exact=True).select_option('all')
        page.get_by_role('button', name='Suchen', exact=True).click()
        expect(rows).to_have_count(3)
        page.get_by_label('Suchbegriff', exact=True).fill('Teamnotiz')
        page.get_by_label('Suchbegriff', exact=True).press('Enter')
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('Teamnotiz')
        page.get_by_label('Suchbegriff', exact=True).fill('')
        picker.select_option('')
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('Privatwort')
        expect(view).not_to_contain_text('Fremdwort')
        page.route('**/api/clinic/v1/vocabulary/search?**', lambda route: route.abort('failed'))
        picker.select_option(label='Autoren V860')
        expect(rows).to_have_count(0)
        expect(feedback).not_to_have_class('note voc-feedback voc-quiet')
        assert feedback.bounding_box()['height'] > 1
        assert feedback.inner_text().strip()
        page.unroute('**/api/clinic/v1/vocabulary/search?**')
        page.get_by_role('button', name='Aktuellen Stand laden', exact=True).click()
        expect(rows).to_have_count(3)
    finally:
        context.close()
