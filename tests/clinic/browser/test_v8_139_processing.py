"""Real local app/API visual proof; synthetic jobs, no external recognition service."""
import pytest
from playwright.sync_api import expect

import v8_browser_support as support


@pytest.fixture(scope='session', autouse=True)
def processing_seed():
    previous = support.SERVER
    seed = '''
with app.app_context():
    from src.models import ProcessingJob
    author = User.query.filter_by(email=emails['author']).one()
    pool = ClinicAuthorPool.query.filter_by(name='Autoren V860').one()
    for state in ('queued', 'processing', 'failed'):
        anchor = Recording(user_id=author.id, title='Synthetic ' + state, status='PENDING')
        source = Recording(user_id=author.id, title='Synthetic source', status='PENDING')
        db.session.add_all([anchor, source])
        db.session.flush()
        d = ClinicDictation(recording_id=anchor.id, author_user_id=author.id, pool_id=pool.id)
        db.session.add(d)
        db.session.flush()
        db.session.add(ClinicOriginal(dictation_id=d.id, content_hash='a'*64, size_bytes=1,
            storage_key='synthetic/' + state, receipt='{}',
            origin_ref=f'upload:recording:{source.id}:time=upload'))
        db.session.add(workflow.ClinicWorkflowDictation(id=d.id, author_user_id=author.id,
            pool_id=pool.id, status=workflow.DRAFT))
        db.session.add(ProcessingJob(recording_id=source.id, user_id=author.id,
            job_type='transcribe', status=state, error_message='ValueError: synthetic' if state == 'failed' else None))
    db.session.commit()
'''
    support.SERVER = previous.replace('from src.app import app, db',
        "import os\nos.environ['SPEAKR_SKIP_BACKGROUND_TASKS'] = 'true'\nfrom src.app import app, db"
    ).replace('server = make_server', seed + '\nserver = make_server')
    yield
    support.SERVER = previous


@pytest.mark.parametrize('size', support.VIEWPORTS)
@pytest.mark.parametrize('theme', support.THEMES)
def test_processing_labels_and_dictation_jobs(browser, app_base, role_states, size, theme):
    width, height = support.VIEWPORTS[size]
    context = browser.new_context(storage_state=role_states['author'],
        viewport={'width': width, 'height': height}, **support.theme_context_options(theme))
    context.add_init_script(support.theme_init_script(theme))
    page = context.new_page()
    out = support.OUT_DIR / 'v8-139'
    out.mkdir(parents=True, exist_ok=True)
    try:
        for fragment, testid in (('aufnahmen', 'clinic-recordings-view'), ('arbeitsliste', 'clinic-worklist-view')):
            page.goto(app_base + '/#/' + fragment)
            support._wait_loading_overlay_gone(page)
            view = page.get_by_test_id(testid)
            for label in ('Transkription wartet', 'Transkription läuft', 'Transkription fehlgeschlagen'):
                expect(view.get_by_text(label, exact=False).first).to_be_visible()
            if fragment == 'arbeitsliste':
                assert view.locator('[data-layout="pill-status"]').evaluate_all(
                    '(items) => items.every(el => el.getBoundingClientRect().right <= el.closest("td").getBoundingClientRect().right)')
            if width < 768:
                view.get_by_text('Transkription fehlgeschlagen', exact=False).first.scroll_into_view_if_needed()
            page.screenshot(path=str(out / f'{fragment}-{size}-{theme}.png'), full_page=True)
        page.goto(app_base + '/#/aufnahmen')
        support._wait_loading_overlay_gone(page)
        page.get_by_test_id('clinic-recordings-view').locator('button.recording-row').filter(
            has_text='Transkription läuft').click()
        page.get_by_role('button', name='Verarbeitung', exact=True).click()
        jobs = page.get_by_test_id('clinic-jobs-view')
        expect(jobs.get_by_test_id('job-list')).to_be_visible()
        expect(jobs.get_by_test_id('job-count')).to_have_text('1')
        page.screenshot(path=str(out / f'jobs-{size}-{theme}.png'))
    finally:
        context.close()
