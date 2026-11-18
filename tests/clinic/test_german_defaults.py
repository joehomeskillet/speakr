"""German is the default language in clinic mode only.

With CLINIC_MODE off the upstream defaults (English) stay in force; explicitly
chosen languages (including NULL = auto-detection) are never overwritten.
"""

import re
import uuid

from src.app import app, db
from src.models import User
from src.utils.localization import api_error, default_language, request_language, translate


def _saved_user(**kwargs):
    suffix = uuid.uuid4().hex[:12]
    user = User(username=f"de_{suffix}", email=f"de_{suffix}@example.invalid", **kwargs)
    db.session.add(user)
    db.session.flush()
    user_id = user.id
    db.session.expire_all()
    return db.session.get(User, user_id)


def test_default_language_follows_clinic_mode(clinic_on):
    assert default_language() == 'de'
    with app.test_request_context('/'):
        assert request_language() == 'de'
    assert translate('nav.adminDashboard') == translate('nav.adminDashboard', 'de')


def test_default_language_is_english_without_clinic_mode(clinic_off):
    assert default_language() == 'en'
    with app.test_request_context('/'):
        assert request_language() == 'en'


def test_new_user_languages_are_german_in_clinic_mode(clinic_on):
    with app.app_context():
        try:
            saved = _saved_user()
            assert saved.transcription_language == 'de'
            assert saved.output_language == 'German'
            assert saved.ui_language == 'de'
        finally:
            db.session.rollback()


def test_new_user_languages_stay_upstream_without_clinic_mode(clinic_off):
    with app.app_context():
        try:
            saved = _saved_user()
            assert saved.transcription_language is None
            assert saved.output_language is None
            assert saved.ui_language == 'en'
        finally:
            db.session.rollback()


def test_explicit_auto_detection_and_other_languages_persist(clinic_on):
    with app.app_context():
        try:
            saved = _saved_user(transcription_language=None, output_language=None, ui_language='en')
            assert saved.transcription_language is None
            assert saved.output_language is None
            assert saved.ui_language == 'en'
        finally:
            db.session.rollback()


def test_login_page_is_german_before_login(clinic_on):
    response = app.test_client().get('/login')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert re.search(r'<html[^>]*\blang="de"', html)
    assert 'Anmelden' in html or 'Benutzername' in html or 'Passwort' in html


def test_login_page_honours_browser_language_in_clinic_mode(clinic_on):
    response = app.test_client().get('/login', headers={'Accept-Language': 'en'})
    assert re.search(r'<html[^>]*\blang="en"', response.get_data(as_text=True))


def test_login_page_is_english_without_clinic_mode(clinic_off):
    response = app.test_client().get('/login')
    assert re.search(r'<html[^>]*\blang="en"', response.get_data(as_text=True))


def test_error_messages_are_german_in_clinic_mode(clinic_on):
    with app.test_request_context('/'):
        german = api_error('recording_not_found', 'de')
        assert api_error('recording_not_found') == german
        assert api_error('recording_not_found', 'en') != german


def test_error_messages_are_english_without_clinic_mode(clinic_off):
    with app.test_request_context('/'):
        assert api_error('recording_not_found') == api_error('recording_not_found', 'en')
