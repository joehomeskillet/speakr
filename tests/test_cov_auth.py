#!/usr/bin/env python3
"""Coverage tests for src/api/auth.py — the auth/login/account security boundary.

These exercise the HTTP-facing auth blueprint: login (success/failure/logout),
the /account handler's two distinct persistence branches (personal-info vs
preferences — the branch where a past bug routed ``ui_language`` to the wrong
column), password change (success + wrong-current-password rejection),
registration gating (ALLOW_REGISTRATION + REGISTRATION_ALLOWED_DOMAINS), the
SSO callback paths (OIDC client mocked), and auth-required routes redirecting
when unauthenticated.

Hermetic & offline: conftest.py points the app at a throwaway SQLite DB and
sets safe env defaults; SSO/OIDC and SMTP effects are patched. CSRF is disabled
per test_client.

Run (pytest, isolated temp DB from conftest.py):
    python -m pytest tests/test_cov_auth.py -q
"""

import os
import sys
import uuid
import contextlib
from unittest import mock
from flask import redirect

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db  # noqa: E402
from src.models import User, SystemSetting  # noqa: E402
from src.api import auth as auth_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_csrf():
    prev = app.config.get('WTF_CSRF_ENABLED')
    app.config['WTF_CSRF_ENABLED'] = False
    yield
    app.config['WTF_CSRF_ENABLED'] = prev


@pytest.fixture
def app_ctx():
    with app.app_context():
        yield


@pytest.fixture
def cleanup_users(app_ctx):
    created = {"usernames": set(), "emails": set(), "subjects": set()}
    yield created
    q = User.query.filter(
        db.or_(
            User.username.in_(created["usernames"] or [""]),
            User.email.in_(created["emails"] or [""]),
            User.sso_subject.in_(created["subjects"] or [""]),
        )
    )
    for u in q.all():
        db.session.delete(u)
    db.session.commit()


# Plaintext password used for login tests; hashed via the same bcrypt the app
# verifies with so check_password_hash succeeds.
GOOD_PW = "Sup3rSecret!"


def _track(cleanup, user):
    cleanup["usernames"].add(user.username)
    cleanup["emails"].add(user.email)
    if user.sso_subject:
        cleanup["subjects"].add(user.sso_subject)


def _mk_user(cleanup, password=GOOD_PW, sso_subject=None, is_admin=False,
             email_verified=True, **extra):
    username = "u_" + uuid.uuid4().hex[:14]
    email = uuid.uuid4().hex[:10] + "@example.com"
    hashed = auth_mod.bcrypt.generate_password_hash(password).decode('utf-8') if password else None
    u = User(username=username, email=email, password=hashed, sso_subject=sso_subject,
             is_admin=is_admin, email_verified=email_verified, **extra)
    db.session.add(u)
    db.session.commit()
    _track(cleanup, u)
    return u


@contextlib.contextmanager
def _client_logged_in_as(user):
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(user.id)
        s['_fresh'] = True
    yield client


@contextlib.contextmanager
def _auto_translation_admin(allow):
    """Set the global auto-translation switch the account page reads, then
    restore it so other tests keep the shipped default (disabled)."""
    from src.models import SystemSetting
    row = SystemSetting.query.filter_by(key='disable_auto_translation').first()
    old_value = row.value if row else None
    SystemSetting.set_setting('disable_auto_translation', 'false' if allow else 'true',
                              setting_type='boolean')
    try:
        yield
    finally:
        if old_value is None:
            SystemSetting.query.filter_by(key='disable_auto_translation').delete()
        else:
            SystemSetting.set_setting('disable_auto_translation', old_value, setting_type='boolean')
        db.session.commit()


@contextlib.contextmanager
def _envset(**overrides):
    saved = {k: os.environ.get(k) for k in overrides}
    setting_saved = {}
    _ENV_TO_SETTING = {
        'ALLOW_REGISTRATION': 'allow_registration',
        'REGISTRATION_ALLOWED_DOMAINS': 'registration_allowed_domains',
    }
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with app.app_context():
            for k, v in overrides.items():
                if k in _ENV_TO_SETTING:
                    key = _ENV_TO_SETTING[k]
                    row = SystemSetting.query.filter_by(key=key).first()
                    setting_saved[k] = row.value if row else None
                    if v is None:
                        SystemSetting.query.filter_by(key=key).delete()
                    else:
                        SystemSetting.set_setting(
                            key, v,
                            setting_type='string' if key == 'registration_allowed_domains' else 'boolean')
            db.session.commit()
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
        with app.app_context():
            for k, old in setting_saved.items():
                key = _ENV_TO_SETTING[k]
                if old is None:
                    SystemSetting.query.filter_by(key=key).delete()
                else:
                    SystemSetting.set_setting(key, old,
                                             setting_type='string' if key == 'registration_allowed_domains' else 'boolean')
            db.session.commit()



# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def test_login_success_sets_session_and_redirects(cleanup_users):
    user = _mk_user(cleanup_users)
    client = app.test_client()
    resp = client.post('/login', data={'email': user.email, 'password': GOOD_PW})
    assert resp.status_code == 302
    assert '/login' not in resp.headers['Location']
    with client.session_transaction() as s:
        assert s.get('_user_id') == str(user.id)


def test_login_wrong_password_no_session(cleanup_users):
    user = _mk_user(cleanup_users)
    client = app.test_client()
    resp = client.post('/login', data={'email': user.email, 'password': 'wrong-password'})
    assert resp.status_code == 200  # re-renders login form
    with client.session_transaction() as s:
        assert '_user_id' not in s


def test_login_unknown_user_no_session(cleanup_users):
    client = app.test_client()
    resp = client.post('/login', data={
        'email': uuid.uuid4().hex[:8] + '@nobody.com', 'password': GOOD_PW})
    assert resp.status_code == 200
    with client.session_transaction() as s:
        assert '_user_id' not in s


def test_login_respects_safe_next(cleanup_users):
    user = _mk_user(cleanup_users)
    client = app.test_client()
    resp = client.post('/login?next=/recordings', data={'email': user.email, 'password': GOOD_PW})
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/recordings')


def test_login_rejects_unsafe_next(cleanup_users):
    user = _mk_user(cleanup_users)
    client = app.test_client()
    resp = client.post('/login?next=//evil.com', data={'email': user.email, 'password': GOOD_PW})
    assert resp.status_code == 302
    assert 'evil.com' not in resp.headers['Location']


def test_login_when_already_authenticated_redirects(cleanup_users):
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        resp = client.get('/login')
        assert resp.status_code == 302


def test_logout_clears_session(cleanup_users):
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        # Logout is POST-only (CSRF-protected) so a cross-site GET can't force it.
        resp = client.post('/logout')
        assert resp.status_code == 302
        with client.session_transaction() as s:
            assert '_user_id' not in s


def test_logout_rejects_get(cleanup_users):
    # A cross-site <img src="/logout"> style GET must not log the user out.
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        resp = client.get('/logout')
        assert resp.status_code == 405
        with client.session_transaction() as s:
            assert s.get('_user_id') == str(user.id)  # still logged in


LOGIN_FAILED = "Wrong email or password."
PASSWORD_LOGIN_DISABLED = "Sign in with SSO. Password sign-in is turned off."


def _post_login(email, password):
    """POST /login, counting bcrypt checks (#534)."""
    real = auth_mod.bcrypt.check_password_hash
    with mock.patch.object(auth_mod.bcrypt, 'check_password_hash', side_effect=real) as check:
        client = app.test_client()
        resp = client.post('/login', data={'email': email, 'password': password})
        with client.session_transaction() as s:
            signed_in = '_user_id' in s
    return resp, check, signed_in


def test_login_sso_only_user_gets_the_generic_failure(cleanup_users):
    # User with no local password: password login refused with the same
    # message and the same bcrypt cost as an unknown address (#534).
    user = _mk_user(cleanup_users, password=None, sso_subject="sub-" + uuid.uuid4().hex)
    resp, check, signed_in = _post_login(user.email, GOOD_PW)
    assert resp.status_code == 200
    assert not signed_in
    assert LOGIN_FAILED in resp.get_data(as_text=True)
    assert check.call_count == 1


def test_login_unknown_email_runs_one_password_check(cleanup_users):
    resp, check, signed_in = _post_login(uuid.uuid4().hex[:8] + '@nobody.com', GOOD_PW)
    assert not signed_in
    assert LOGIN_FAILED in resp.get_data(as_text=True)
    assert check.call_count == 1


def test_login_wrong_password_checks_the_stored_hash(cleanup_users):
    user = _mk_user(cleanup_users)
    resp, check, signed_in = _post_login(user.email, 'wrong-password')
    assert not signed_in
    assert LOGIN_FAILED in resp.get_data(as_text=True)
    assert check.call_count == 1
    assert check.call_args.args[0] == user.password


@contextlib.contextmanager
def _password_login_disabled():
    with mock.patch.object(auth_mod, 'is_sso_enabled', return_value=True), \
         mock.patch.object(auth_mod, 'get_sso_config',
                           return_value={'disable_password_login': True, 'provider_name': 'SSO'}), \
         mock.patch.object(auth_mod, 'init_sso_client', return_value=None):
        yield


@pytest.mark.parametrize("who", ["unknown", "sso_only", "user_right_pw", "user_wrong_pw", "admin_wrong_pw"])
def test_password_login_disabled_answers_everyone_alike(cleanup_users, who):
    # With password login disabled, only an admin with the correct password
    # signs in; every other attempt gets the same message (#534).
    if who == "unknown":
        email, pw = uuid.uuid4().hex[:8] + '@nobody.com', GOOD_PW
    elif who == "sso_only":
        email, pw = _mk_user(cleanup_users, password=None, sso_subject="sub-" + uuid.uuid4().hex).email, GOOD_PW
    elif who == "admin_wrong_pw":
        email, pw = _mk_user(cleanup_users, is_admin=True).email, 'wrong-password'
    else:
        email = _mk_user(cleanup_users).email
        pw = GOOD_PW if who == "user_right_pw" else 'wrong-password'
    with _password_login_disabled():
        resp, check, signed_in = _post_login(email, pw)
    assert not signed_in
    assert PASSWORD_LOGIN_DISABLED in resp.get_data(as_text=True)
    assert check.call_count == 1


def test_password_login_disabled_still_admits_an_admin(cleanup_users):
    admin = _mk_user(cleanup_users, is_admin=True)
    with _password_login_disabled():
        resp, check, signed_in = _post_login(admin.email, GOOD_PW)
    assert resp.status_code == 302
    assert signed_in


# ---------------------------------------------------------------------------
# /account — personal-info branch vs preferences branch
# ---------------------------------------------------------------------------

def test_account_requires_login():
    client = app.test_client()
    resp = client.get('/account')
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert '/login' in resp.headers['Location']


def test_account_get_renders_for_logged_in_user(cleanup_users):
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        resp = client.get('/account')
        assert resp.status_code == 200


def test_account_personal_info_branch_persists_name_fields(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/account', data={
            'user_name': 'Jane Doe',
            'user_job_title': 'Engineer',
            'user_company': 'Acme',
        })
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    assert refreshed.name == 'Jane Doe'
    assert refreshed.job_title == 'Engineer'
    assert refreshed.company == 'Acme'


def test_account_personal_info_branch_does_not_touch_language(cleanup_users):
    # The personal-info branch must NOT write ui_language (past-bug guard).
    user = _mk_user(cleanup_users, ui_language='fr')
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/account', data={'user_name': 'Bob'})
    refreshed = db.session.get(User, uid)
    assert refreshed.ui_language == 'fr'  # unchanged
    assert refreshed.name == 'Bob'


def test_account_preferences_branch_persists_languages(cleanup_users):
    user = _mk_user(cleanup_users, ui_language='en')
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/account', data={
            'preferences_form': '1',
            'ui_language': 'es',
            'transcription_language': 'fr',
            'output_language': 'Spanish',
            'show_timestamps_simple_view': 'on',
            'editor_autosave': 'on',
            'audio_player_position': 'top',
        })
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    # ui_language goes to ui_language (NOT transcription_language) — the bug.
    assert refreshed.ui_language == 'es'
    assert refreshed.transcription_language == 'fr'
    assert refreshed.output_language == 'Spanish'
    assert refreshed.show_timestamps_simple_view is True
    assert refreshed.editor_autosave is True
    assert refreshed.audio_player_position == 'top'


def test_account_preferences_keyboard_shortcuts_setting(cleanup_users):
    # On for a new account; saved only by a form that rendered the checkbox.
    user = _mk_user(cleanup_users)
    uid = user.id
    assert user.keyboard_shortcuts is True
    with _client_logged_in_as(user) as client:
        # A form without the checkbox's marker leaves the setting alone
        client.post('/account', data={'preferences_form': '1', 'ui_language': 'en'})
        assert db.session.get(User, uid).keyboard_shortcuts is True
        # The marker without the checkbox: turned off
        client.post('/account', data={'preferences_form': '1', 'ui_language': 'en', 'keyboard_shortcuts_shown': '1'})
        assert db.session.get(User, uid).keyboard_shortcuts is False
        page = client.get('/account').get_data(as_text=True)
        assert '"enabled": false' in page
        assert 'id="prefKeyboardShortcuts" name="keyboard_shortcuts"  class' in page
        assert 'data-shortcuts-help' not in page
        client.post('/account', data={'preferences_form': '1', 'ui_language': 'en', 'keyboard_shortcuts_shown': '1',
                                      'keyboard_shortcuts': 'on'})
        assert db.session.get(User, uid).keyboard_shortcuts is True
        page = client.get('/account').get_data(as_text=True)
        assert '"enabled": true' in page
        assert 'name="keyboard_shortcuts" checked' in page
        assert 'data-shortcuts-help' in page


def test_account_preferences_keyboard_shortcuts_null_means_on(cleanup_users):
    # An account from before the column has NULL: shortcuts are on.
    user = _mk_user(cleanup_users)
    uid = user.id
    User.query.filter_by(id=uid).update({'keyboard_shortcuts': None})
    db.session.commit()
    with _client_logged_in_as(user) as client:
        page = client.get('/account').get_data(as_text=True)
    assert '"enabled": true' in page
    assert 'name="keyboard_shortcuts" checked' in page


def test_account_preferences_branch_normalizes_legacy_language_name(cleanup_users):
    # Legacy display-name transcription language must be normalized to a code.
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/account', data={
            'preferences_form': '1',
            'ui_language': 'en',
            'transcription_language': 'Français',
        })
    refreshed = db.session.get(User, uid)
    assert refreshed.transcription_language == 'fr'


def test_account_preferences_invalid_audio_position_falls_back(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/account', data={
            'preferences_form': '1',
            'ui_language': 'en',
            'audio_player_position': 'sideways',
        })
    refreshed = db.session.get(User, uid)
    assert refreshed.audio_player_position == 'bottom'


def test_account_preferences_persist_default_recording_sort(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    assert user.default_recording_sort is None  # existing accounts keep the date-added default
    with _client_logged_in_as(user) as client:
        client.post('/account', data={
            'preferences_form': '1',
            'ui_language': 'en',
            'default_recording_sort': 'meeting_date',
        })
    assert db.session.get(User, uid).default_recording_sort == 'meeting_date'


def test_account_preferences_unknown_default_recording_sort_resets_it(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    user.default_recording_sort = 'meeting_date'
    db.session.commit()
    with _client_logged_in_as(user) as client:
        client.post('/account', data={
            'preferences_form': '1',
            'ui_language': 'en',
            'default_recording_sort': 'title',
        })
    assert db.session.get(User, uid).default_recording_sort is None


def test_account_preferences_without_the_field_keep_default_recording_sort(cleanup_users):
    # Other tabs submit the same form without this field and must not clear it.
    user = _mk_user(cleanup_users)
    uid = user.id
    user.default_recording_sort = 'meeting_date'
    db.session.commit()
    with _client_logged_in_as(user) as client:
        client.post('/account', data={'preferences_form': '1', 'ui_language': 'en'})
    assert db.session.get(User, uid).default_recording_sort == 'meeting_date'


def test_account_preferences_persist_auto_translation_when_admin_allows(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    assert user.auto_translation is False
    with _auto_translation_admin(allow=True):
        with _client_logged_in_as(user) as client:
            client.post('/account', data={
                'preferences_form': '1',
                'ui_language': 'en',
                'auto_translation_shown': '1',
                'auto_translation': 'on',
            })
    assert db.session.get(User, uid).auto_translation is True


def test_account_preferences_refuse_auto_translation_when_admin_disables(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _auto_translation_admin(allow=False):
        with _client_logged_in_as(user) as client:
            client.post('/account', data={
                'preferences_form': '1',
                'ui_language': 'en',
                'auto_translation_shown': '1',
                'auto_translation': 'on',
            })
    assert db.session.get(User, uid).auto_translation is False


def test_account_preferences_without_auto_translation_section_keeps_value(cleanup_users):
    # The section disappears when the admin disables the feature; other form
    # submissions must not clear a preference saved while it was available.
    user = _mk_user(cleanup_users, auto_translation=True)
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/account', data={'preferences_form': '1', 'ui_language': 'en'})
    assert db.session.get(User, uid).auto_translation is True


def test_account_page_shows_auto_translation_only_when_admin_allows(cleanup_users):
    user = _mk_user(cleanup_users)
    with _auto_translation_admin(allow=False):
        with _client_logged_in_as(user) as client:
            hidden = client.get('/account').get_data(as_text=True)
    assert 'name="auto_translation"' not in hidden
    assert 'Turned off by your administrator.' in hidden

    with _auto_translation_admin(allow=True):
        with _client_logged_in_as(user) as client:
            shown = client.get('/account').get_data(as_text=True)
    assert 'name="auto_translation"' in shown
    assert 'Auto-translate new recordings' in shown


def test_default_recording_sort_reaches_the_page_and_the_account_form(cleanup_users):
    import re
    user = _mk_user(cleanup_users)
    user.default_recording_sort = 'meeting_date'
    db.session.commit()
    with _client_logged_in_as(user) as client:
        index = client.get('/').get_data(as_text=True)
        account = client.get('/account').get_data(as_text=True)
    assert 'data-default-recording-sort="meeting_date"' in index
    assert re.search(r'value="meeting_date"\s+checked', account)
    assert not re.search(r'value="created_at"\s+checked', account)


def test_default_recording_sort_defaults_to_date_added_for_existing_accounts(cleanup_users):
    import re
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        index = client.get('/').get_data(as_text=True)
        account = client.get('/account').get_data(as_text=True)
    assert 'data-default-recording-sort="created_at"' in index
    assert re.search(r'value="created_at"\s+checked', account)


def test_default_translation_language_follows_output_language(cleanup_users):
    user = _mk_user(cleanup_users, ui_language='en', output_language='Spanish')
    with _client_logged_in_as(user) as client:
        index = client.get('/').get_data(as_text=True)
    assert 'data-default-translation-language="Spanish"' in index


def test_default_translation_language_falls_back_to_interface_language(cleanup_users):
    user = _mk_user(cleanup_users, ui_language='es', output_language=None)
    with _client_logged_in_as(user) as client:
        index = client.get('/').get_data(as_text=True)
    assert 'data-default-translation-language="Spanish"' in index


def test_default_translation_language_is_english_for_english_interface(cleanup_users):
    user = _mk_user(cleanup_users, ui_language='en', output_language=None)
    with _client_logged_in_as(user) as client:
        index = client.get('/').get_data(as_text=True)
    assert 'data-default-translation-language="English"' in index


def test_account_prompts_branch_persists_summary_prompt(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/account', data={
            'summary_prompt': 'Summarize tightly.',
            'extract_events': 'on',
            'transcription_hotwords': 'Speakr',
        })
        assert resp.status_code == 302
        assert '#prompts' in resp.headers['Location']
    refreshed = db.session.get(User, uid)
    assert refreshed.summary_prompt == 'Summarize tightly.'
    assert refreshed.extract_events is True
    assert refreshed.transcription_hotwords == 'Speakr'


def test_account_ajax_returns_json(cleanup_users):
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        resp = client.post('/account', data={'user_name': 'Ajax'},
                           headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

def test_change_password_success(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    new_pw = "Brand-New-Pw9!"
    with _client_logged_in_as(user) as client:
        resp = client.post('/change_password', data={
            'current_password': GOOD_PW,
            'new_password': new_pw,
            'confirm_password': new_pw,
        })
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    assert auth_mod.bcrypt.check_password_hash(refreshed.password, new_pw)
    assert not auth_mod.bcrypt.check_password_hash(refreshed.password, GOOD_PW)


def test_change_password_wrong_current_rejected(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/change_password', data={
            'current_password': 'not-the-real-one',
            'new_password': "Another-Pw9!",
            'confirm_password': "Another-Pw9!",
        })
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    # Password unchanged.
    assert auth_mod.bcrypt.check_password_hash(refreshed.password, GOOD_PW)


def test_change_password_mismatch_confirmation_rejected(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/change_password', data={
            'current_password': GOOD_PW,
            'new_password': "Another-Pw9!",
            'confirm_password': "Different-Pw9!",
        })
    refreshed = db.session.get(User, uid)
    assert auth_mod.bcrypt.check_password_hash(refreshed.password, GOOD_PW)


def test_change_password_weak_new_rejected(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        client.post('/change_password', data={
            'current_password': GOOD_PW,
            'new_password': "weak",
            'confirm_password': "weak",
        })
    refreshed = db.session.get(User, uid)
    assert auth_mod.bcrypt.check_password_hash(refreshed.password, GOOD_PW)


def test_change_password_sso_only_user_redirected_to_reset(cleanup_users):
    # GHSA-x4q4-3ww4-h329: SSO-only (no local password) cannot set password here.
    user = _mk_user(cleanup_users, password=None, sso_subject="sub-" + uuid.uuid4().hex)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/change_password', data={
            'new_password': "Brand-New-Pw9!",
            'confirm_password': "Brand-New-Pw9!",
        })
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    assert refreshed.password is None  # no password was set


def test_change_password_requires_login():
    client = app.test_client()
    resp = client.post('/change_password', data={})
    assert resp.status_code in (302, 401)


# ---------------------------------------------------------------------------
# SSO callback paths (OIDC client mocked)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _sso_on():
    with mock.patch.object(auth_mod, 'is_sso_enabled', return_value=True), \
            mock.patch.object(auth_mod, 'init_sso_client', return_value=mock.MagicMock()), \
            mock.patch.object(auth_mod, 'get_sso_config',
                              return_value={'provider_name': 'Keycloak', 'redirect_uri': 'https://app/cb'}):
        yield


def test_sso_callback_disabled_redirects_to_login():
    with mock.patch.object(auth_mod, 'is_sso_enabled', return_value=False):
        client = app.test_client()
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_sso_callback_success_logs_in_user(cleanup_users):
    sub = "sub-" + uuid.uuid4().hex
    user = _mk_user(cleanup_users, password=None, sso_subject=sub)
    oauth = mock.MagicMock()
    oauth.sso.authorize_access_token.return_value = {'userinfo': {'sub': sub}}
    with _sso_on(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth), \
            mock.patch.object(auth_mod, 'create_or_update_sso_user', return_value=user):
        client = app.test_client()
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/login' not in resp.headers['Location']
        with client.session_transaction() as s:
            assert s.get('_user_id') == str(user.id)


def test_sso_callback_token_error_redirects_to_login():
    oauth = mock.MagicMock()
    oauth.sso.authorize_access_token.side_effect = Exception("boom")
    with _sso_on(), mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth):
        client = app.test_client()
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_sso_callback_missing_subject_redirects_to_login():
    oauth = mock.MagicMock()
    oauth.sso.authorize_access_token.return_value = {'userinfo': {}}  # no 'sub'
    with _sso_on(), mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth):
        client = app.test_client()
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_sso_callback_permission_error_redirects_to_login():
    oauth = mock.MagicMock()
    oauth.sso.authorize_access_token.return_value = {'userinfo': {'sub': 'sub-' + uuid.uuid4().hex}}
    with _sso_on(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth), \
            mock.patch.object(auth_mod, 'create_or_update_sso_user',
                              side_effect=PermissionError("domain not allowed")):
        client = app.test_client()
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_sso_callback_links_to_target_user(cleanup_users):
    target = _mk_user(cleanup_users)
    uid = target.id
    sub = "sub-" + uuid.uuid4().hex
    oauth = mock.MagicMock()
    oauth.sso.authorize_access_token.return_value = {'userinfo': {'sub': sub, 'name': 'Linked'}}
    with _sso_on(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth), \
            mock.patch.object(auth_mod, 'update_user_profile_from_claims'):
        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
            s['sso_link_user_id'] = uid
        resp = client.get('/auth/sso/callback')
        assert resp.status_code == 302
        assert '/account' in resp.headers['Location']
    refreshed = db.session.get(User, uid)
    cleanup_users["subjects"].add(sub)
    assert refreshed.sso_subject == sub
    assert refreshed.sso_provider == 'Keycloak'


def test_sso_login_disabled_redirects():
    with mock.patch.object(auth_mod, 'is_sso_enabled', return_value=False):
        client = app.test_client()
        resp = client.get('/auth/sso/login')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_sso_login_does_not_store_unsafe_next(cleanup_users):
    # auth.py:249 open-redirect guard — a dangerous ?next must NOT be
    # persisted to session['sso_next'] (it would be honoured after the
    # IdP round-trip). Both an absolute and a scheme-relative target.
    oauth = mock.MagicMock()
    oauth.sso.authorize_redirect.return_value = redirect("https://idp.example/authorize")
    with _sso_on(), mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth):
        for evil in ('http://evil.com', '//evil.com'):
            client = app.test_client()
            resp = client.get('/auth/sso/login', query_string={'next': evil})
            assert resp.status_code == 200
            with client.session_transaction() as s:
                assert s.get('sso_next') != evil
                assert 'sso_next' not in s


def test_sso_login_stores_safe_next(cleanup_users):
    # The safe-path companion: a local relative target IS stored.
    oauth = mock.MagicMock()
    oauth.sso.authorize_redirect.return_value = redirect("https://idp.example/authorize")
    with _sso_on(), mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth):
        client = app.test_client()
        resp = client.get('/auth/sso/login', query_string={'next': '/recordings'})
        assert resp.status_code == 200
        with client.session_transaction() as s:
            assert s.get('sso_next') == '/recordings'


def test_sso_unlink_with_password(cleanup_users):
    user = _mk_user(cleanup_users, sso_subject="sub-" + uuid.uuid4().hex, sso_provider='Keycloak')
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/auth/sso/unlink')
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    assert refreshed.sso_subject is None


def test_sso_unlink_without_password_blocked(cleanup_users):
    sub = "sub-" + uuid.uuid4().hex
    user = _mk_user(cleanup_users, password=None, sso_subject=sub)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/auth/sso/unlink')
        assert resp.status_code == 302
    refreshed = db.session.get(User, uid)
    assert refreshed.sso_subject == sub  # still linked


# ---------------------------------------------------------------------------
# JSON preference endpoints (login-required)
# ---------------------------------------------------------------------------

def test_update_auto_summarization_requires_login():
    client = app.test_client()
    resp = client.post('/api/user/auto-summarization', json={'enabled': False})
    assert resp.status_code in (302, 401)


def test_update_auto_summarization_persists(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/api/user/auto-summarization', json={'enabled': False})
        assert resp.status_code == 200
        assert resp.get_json()['auto_summarization'] is False
    assert db.session.get(User, uid).auto_summarization is False


def test_update_auto_speaker_labelling_invalid_threshold(cleanup_users):
    user = _mk_user(cleanup_users)
    with _client_logged_in_as(user) as client:
        resp = client.post('/api/user/auto-speaker-labelling',
                           json={'enabled': True, 'threshold': 'bogus'})
        assert resp.status_code == 400


def test_update_auto_speaker_labelling_persists(cleanup_users):
    user = _mk_user(cleanup_users)
    uid = user.id
    with _client_logged_in_as(user) as client:
        resp = client.post('/api/user/auto-speaker-labelling',
                           json={'enabled': True, 'threshold': 'high'})
        assert resp.status_code == 200
    refreshed = db.session.get(User, uid)
    assert refreshed.auto_speaker_labelling is True
    assert refreshed.auto_speaker_labelling_threshold == 'high'


# ---------------------------------------------------------------------------
# SSO client initialization on the login route (auth.py:243-244)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _sso_enabled_with_config():
    """SSO enabled + a usable config, WITHOUT patching the client helpers so
    each test controls get_sso_client / init_sso_client itself."""
    with mock.patch.object(auth_mod, 'is_sso_enabled', return_value=True), \
            mock.patch.object(auth_mod, 'get_sso_config',
                              return_value={'provider_name': 'Keycloak',
                                            'redirect_uri': 'https://app/cb'}):
        yield


def test_sso_login_uses_cached_client_without_reinit():
    # auth.py:243 — when get_sso_client() returns a client, the `or` must
    # short-circuit and init_sso_client must NOT be called. The `or`->`and`
    # mutation would call init_sso_client (and use ITS return value instead).
    oauth = mock.MagicMock()
    oauth.sso.authorize_redirect.return_value = redirect("https://idp.example/authorize")
    with _sso_enabled_with_config(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=oauth), \
            mock.patch.object(auth_mod, 'init_sso_client') as init_mock:
        client = app.test_client()
        resp = client.get('/auth/sso/login')
        assert resp.status_code == 200
        init_mock.assert_not_called()
        oauth.sso.authorize_redirect.assert_called_once()


def test_sso_login_inits_client_when_none_cached():
    # auth.py:243 — get_sso_client() None ⇒ init_sso_client IS invoked and its
    # client is used. Under `or`->`and`, `None and init_sso_client(...)`
    # short-circuits, init is never called, and the route bails to /login.
    oauth = mock.MagicMock()
    oauth.sso.authorize_redirect.return_value = redirect("https://idp.example/authorize")
    with _sso_enabled_with_config(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=None), \
            mock.patch.object(auth_mod, 'init_sso_client', return_value=oauth) as init_mock:
        client = app.test_client()
        resp = client.get('/auth/sso/login')
        assert resp.status_code == 200
        init_mock.assert_called_once()
        oauth.sso.authorize_redirect.assert_called_once()


def test_sso_login_bails_to_login_when_client_unavailable():
    # auth.py:244 — when neither a cached nor a freshly-initialized client is
    # available (both None), `if not oauth` must redirect to login. The
    # `if not oauth`->`if oauth` mutation would instead fall through and
    # dereference None (no 302-to-login).
    with _sso_enabled_with_config(), \
            mock.patch.object(auth_mod, 'get_sso_client', return_value=None), \
            mock.patch.object(auth_mod, 'init_sso_client', return_value=None):
        client = app.test_client()
        resp = client.get('/auth/sso/login')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
