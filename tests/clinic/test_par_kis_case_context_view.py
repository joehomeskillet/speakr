"""Read-only synthetic KIS handoffs through the real Flask and V8 browser paths."""

from itertools import product
import sqlite3
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from playwright.sync_api import expect, sync_playwright
import pytest

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server  # noqa: F401

FALL = 'SYN-0001'
MARKER = 'data-clinic-case-view'
HANDOFF = '/?fall_id=' + FALL
ROWS = '/api/clinic/v1/worklist/rows'
SIGNING_KEY = 'local-synthetic-case-view-test-key'
SIZES = ((1440, 900), (1280, 720), (390, 844))

SEED = r'''
from datetime import datetime, timedelta, timezone
from src.clinic.context_grants import VerifiedContextGrant, bind_context_grant
from src.clinic.models import ClinicContextRevision
from src.clinic.routes_output import configured_adapter

def bind_case(target, case, trusted=True):
    adapter = configured_adapter(app)
    if not trusted:
        db.session.add(ClinicContextRevision(dictation_id=target.id, source_system=adapter.name,
                                            tenant_scope="staging", case_ref=case, patient_ref="opaque"))
        return
    now = datetime.now(timezone.utc)
    evidence = VerifiedContextGrant(adapter.name, "speakr", target.author_user_id, target.id, adapter.name,
                                    "synthetic", "staging", case, "opaque", "bind_existing",
                                    uuid.uuid4().hex, now + timedelta(seconds=30))
    bind_context_grant(evidence.nonce, dictation_id=target.id, actor_user_id=target.author_user_id,
                       tenant="staging", expected_version=target.version_counter, issuer=adapter.name,
                       audience="speakr", now=now, resolve=lambda *_: evidence, adapter=adapter,
                       authorize_binding=lambda *_: True)

def add_case(owner, title, case, trusted=True, status=workflow.DRAFT):
    rec = Recording(user_id=owner.id, title=title, original_filename="sample.wav")
    db.session.add(rec)
    db.session.flush()
    target = ClinicDictation(recording_id=rec.id, author_user_id=owner.id, pool_id=dic.pool_id)
    db.session.add(target)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(id=target.id, author_user_id=owner.id, pool_id=dic.pool_id,
                                                    status=status))
    bind_case(target, case, trusted)

with app.app_context():
    dic = db.session.get(ClinicDictation, dictation_id)
    author, admin, stranger = (User.query.filter_by(username=name).one() for name in ("u213", "a213", "s213"))
    bind_case(dic, "SYN-0001")
    add_case(author, "Zweites Falldiktat", "SYN-0001", status=workflow.TO_ARCHIVE)
    add_case(author, "Anderer Fall", "SYN-0002")
    add_case(author, "Ungeprüfter Verweis", "SYN-0001", trusted=False)
    add_case(admin, "Fremdes Falldiktat", "SYN-0001")
    add_case(stranger, "Eigener anderer Fall", "SYN-0002")
    db.session.commit()
'''


@pytest.fixture(scope='module')
def case_scene(request):
    paths = {}
    original_env = mount._env

    def environment(tmp, clinic):
        paths.update(db=tmp / 'w213.db', log=tmp / 'server.log')
        return {**original_env(tmp, clinic), 'CLINIC_PROFILE': 'staging',
                'SPEAKR_SKIP_BACKGROUND_TASKS': 'true', 'SECRET_KEY': SIGNING_KEY,
                'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1'}

    source = mount.SERVER.replace('    dictation_id = dic.id\n', '    dictation_id = dic.id\n' + SEED)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mount, 'SERVER', source)
        patch.setattr(mount, '_env', environment)
        base, did = request.getfixturevalue('live_server')
        yield base, did, paths


def _scoped_rows(page, base, marker=None, query=''):
    marker = marker if marker is not None else page.locator('#app').get_attribute(MARKER)
    response = page.request.get(base + ROWS + query, headers={'X-Clinic-Case-View': marker})
    assert response.ok, response.status
    return response.json()


def _handoff(page, base, path=HANDOFF):
    response = page.request.get(base + path, max_redirects=0)
    assert response.status == 303
    assert response.headers['location'] == '/fall'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['cache-control'] == 'private, no-store'
    assert FALL not in response.text()
    page.goto(base + response.headers['location'])


def _submitted(page, email):
    page.fill('input[name=email]', email)
    page.fill('input[name=password]', mount.PASSWORD)
    page.locator('form:has(input[name=password]) [type=submit]:visible').first.click()
    page.wait_for_url('**/fall*')


def _audit_rows(paths):
    with sqlite3.connect(paths['db']) as connection:
        return connection.execute(
            "SELECT actor_kind, outcome, details FROM audit_event "
            "WHERE event = 'clinic.kis.case_handoff' ORDER BY id"
        ).fetchall()


def test_case_handoff_access_login_privacy_and_audit(case_scene):
    base, did, paths = case_scene
    malformed = 'invalid-case-canary'
    before = len(_audit_rows(paths))
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            owner = mount._login(browser, base, 'author213@local.test')
            _handoff(owner, base)
            case = _scoped_rows(owner, base)
            assert case['total'] == 2
            assert {row['title'] for row in case['items']} == {'Synthetisch', 'Zweites Falldiktat'}
            assert did in {row['dictation_id'] for row in case['items']}
            assert _scoped_rows(owner, base, query='?page_size=1')['total'] == 2
            assert _scoped_rows(owner, base, query='?search=Zweites')['total'] == 1
            owner_old_marker = owner.locator('#app').get_attribute(MARKER)
            regular = owner.request.get(base + ROWS).json()
            assert {row['title'] for row in regular['items']} >= {'Anderer Fall', 'Ungeprüfter Verweis'}
            owner.reload()
            assert _scoped_rows(owner, base)['total'] == 2

            outsider = mount._login(browser, base, mount.STRANGER)
            _handoff(outsider, base)
            assert _scoped_rows(outsider, base)['items'] == []
            assert _scoped_rows(outsider, base, owner_old_marker)['items'] == []
            assert outsider.request.get(base + ROWS).json()['total'] == 1

            _handoff(owner, base, '/?fall_id=SYN-9876')
            assert _scoped_rows(owner, base)['items'] == []
            assert _scoped_rows(owner, base, owner_old_marker)['items'] == []
            _handoff(owner, base, '/?%66all_id=' + malformed)
            assert _scoped_rows(owner, base)['items'] == []
            _handoff(owner, base, HANDOFF + '&fall_id=' + FALL)
            assert _scoped_rows(owner, base)['items'] == []

            anonymous = browser.new_page()
            requests = []
            anonymous.on('request', lambda req: requests.append(req.all_headers().get('referer', '')))
            redirection = anonymous.request.get(base + HANDOFF, max_redirects=0)
            assert redirection.status == 303
            assert redirection.headers['location'] == '/login?next=/fall'
            cookies = anonymous.context.cookies(base + '/fall')
            pending = next(cookie for cookie in cookies if cookie['name'] == 'clinic_case_pending')
            assert pending['httpOnly'] and pending['path'] == '/fall'
            # Use an actual browser navigation to verify the redirect referrers too.
            anonymous.goto(base + HANDOFF)
            assert urlsplit(anonymous.url).path == '/login' and FALL not in anonymous.url
            _submitted(anonymous, 'author213@local.test')
            expect(anonymous.locator('[data-row]')).to_have_count(2)
            history = anonymous.context.new_cdp_session(anonymous).send('Page.getNavigationHistory')
            assert all(FALL not in entry['url'] for entry in history['entries'])
            assert all(FALL not in referer for referer in requests)
            assert not anonymous.locator(f'a[href*="{FALL}"]').count()
            assert FALL not in anonymous.evaluate('JSON.stringify([localStorage, sessionStorage])')
            assert all(FALL not in cookie['value'] for cookie in anonymous.context.cookies())
            assert not any(cookie['name'] == 'clinic_case_pending' for cookie in anonymous.context.cookies())
            anonymous.reload()
            expect(anonymous.locator('[data-row]')).to_have_count(2)
            anonymous.get_by_role('link', name='Alle Diktate').click()
            expect(anonymous.locator('[data-row]')).to_have_count(3)
        finally:
            browser.close()
    recorded = _audit_rows(paths)[before:]
    assert [(actor, outcome) for actor, outcome, _ in recorded] == [
        ('session', 'success'), ('session', 'success'), ('session', 'success'),
        ('session', 'denied'), ('session', 'denied'), ('anonymous', 'success'), ('anonymous', 'success'),
    ]
    assert all(FALL not in str(details) and malformed not in str(details) for _, _, details in recorded)
    access_log = paths['log'].read_text()
    assert access_log.count('KIS handoff request (query redacted)') >= len(recorded)
    assert all(value not in access_log for value in (FALL, 'SYN-9876', malformed))


def test_signed_mock_handoff_is_bound_to_case_and_single_use(case_scene):
    base, _, paths = case_scene
    signer = URLSafeTimedSerializer(SIGNING_KEY, salt='speakr-mock-kis-case-view-v1')
    signature = signer.dumps([FALL, uuid4().hex])
    path = '/?' + urlencode({'fall_id': FALL, 'case_signature': signature})

    class ExpiredSigner(TimestampSigner):
        def get_timestamp(self):
            return super().get_timestamp() - 61

    expired = URLSafeTimedSerializer(SIGNING_KEY, salt='speakr-mock-kis-case-view-v1', signer=ExpiredSigner)
    expired_path = '/?' + urlencode({'fall_id': FALL, 'case_signature': expired.dumps([FALL, uuid4().hex])})
    before = len(_audit_rows(paths))
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            owner = mount._login(browser, base, 'author213@local.test')
            _handoff(owner, base, path.replace(FALL, 'SYN-0002', 1))
            assert _scoped_rows(owner, base)['total'] == 0
            _handoff(owner, base, path + 'tampered')
            assert _scoped_rows(owner, base)['total'] == 0
            _handoff(owner, base, expired_path)
            assert _scoped_rows(owner, base)['total'] == 0
            _handoff(owner, base, path)
            assert _scoped_rows(owner, base)['total'] == 2
            _handoff(owner, base, path)
            assert _scoped_rows(owner, base)['total'] == 0
        finally:
            browser.close()
    assert [outcome for _, outcome, _ in _audit_rows(paths)[before:]] == [
        'denied', 'denied', 'denied', 'success', 'denied',
    ]
    assert signature not in paths['log'].read_text()


@pytest.mark.parametrize('profile', ['production', 'staging'])
def test_disabled_adapter_refuses_case_input(profile, monkeypatch):
    from src.app import app
    from src.clinic.case_handoff import _verified_case
    from src.clinic.kis import DisabledAdapter

    monkeypatch.setenv('CLINIC_PROFILE', profile)
    monkeypatch.setitem(app.extensions, 'clinic_kis_adapter', DisabledAdapter())
    with app.app_context():
        assert _verified_case([FALL], []) == (None, None)


def test_case_worklist_visual_matrix(case_scene, tmp_path):
    base, _, _ = case_scene
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            for (email, state), (width, height), theme in product(
                [('author213@local.test', 'rows'), (mount.STRANGER, 'empty')], SIZES, ('light', 'dark')
            ):
                page = mount._login(browser, base, email) if state == 'rows' else browser.new_page()
                try:
                    js_errors = []
                    page.on('pageerror', lambda error: js_errors.append(str(error)))
                    page.set_viewport_size({'width': width, 'height': height})
                    page.emulate_media(color_scheme=theme, reduced_motion='reduce')
                    page.add_init_script(f"localStorage.setItem('darkMode', '{str(theme == 'dark').lower()}')")
                    if state == 'rows':
                        _handoff(page, base)
                    else:
                        page.goto(base + HANDOFF)
                        assert urlsplit(page.url).query == 'next=/fall'
                        expect(page.locator('input[name=password]')).to_be_visible()
                        page.locator('.app-loading-overlay').first.wait_for(state='detached')
                        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                        page.screenshot(path=tmp_path / f'kis-login-{width}x{height}-{theme}.png',
                                        animations='disabled')
                        _submitted(page, email)
                    page.locator('.app-loading-overlay').first.wait_for(state='detached')
                    expect(page.get_by_role('heading', name='Diktate zum Fall')).to_be_visible()
                    if state == 'rows':
                        expect(page.locator('[data-row]')).to_have_count(2)
                        result = page.locator('[data-row]').last
                    else:
                        result = page.get_by_test_id('empty-state')
                        expect(result).to_have_text('Keine Diktate vorhanden.')
                    if height <= 844:
                        result.scroll_into_view_if_needed()
                    if width == 390:
                        # Keep the preceding row or action clear of the sticky mobile header.
                        result.evaluate("""element => {
                            let scroller = element.parentElement;
                            while (scroller && !scroller.scrollTop) scroller = scroller.parentElement;
                            if (scroller) scroller.scrollTop -= 28;
                        }""")
                    expect(result).to_be_in_viewport()
                    assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    assert not js_errors, js_errors
                    page.screenshot(path=tmp_path / f'kis-case-{state}-{width}x{height}-{theme}.png',
                                    animations='disabled')
                finally:
                    page.context.close()
        finally:
            browser.close()
