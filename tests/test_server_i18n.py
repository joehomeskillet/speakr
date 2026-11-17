"""Server-generated text in the user's interface language (#477).

translate() resolves the same static/locales/*.json catalogs the browser uses.
Flash messages, the verification and password-reset emails, budget messages
and API error and info messages go through it. API errors also carry a code
that is the same in every language.
"""

import os
import uuid
from unittest import mock

import pytest

from src.app import app, db
from src.models import User
from src.utils.localization import (
    api_error, format_file_size, format_number, format_percent, match_language, request_language, translate,
)


# ---------------------------------------------------------------------------
# translate()
# ---------------------------------------------------------------------------

def test_translate_returns_the_requested_language():
    assert translate('server.flash.loginFailed', 'de') == 'Falsche E-Mail-Adresse oder falsches Passwort.'
    assert translate('server.flash.loginFailed', 'en') == 'Wrong email or password.'


def test_translate_interpolates_double_brace_placeholders():
    assert translate('server.budget.tokenWarning', 'en', percent='42%') == (
        '42% of your monthly token budget used.')
    assert '42%' in translate('server.budget.tokenWarning', 'zh', percent='42%')


def test_translate_leaves_unfilled_placeholders_as_is():
    assert '{{percent}}' in translate('server.budget.tokenWarning', 'en')


def test_translate_missing_key_returns_the_key():
    assert translate('server.flash.noSuchKey', 'de') == 'server.flash.noSuchKey'


def test_translate_falls_back_to_english_for_a_key_missing_in_the_language():
    catalogs = {'de': {'server': {}}, 'en': {'server': {'only': 'English only'}}}
    # Patched through translate's own globals: other tests re-import src
    # modules, so the module path may no longer name this function's module.
    with mock.patch.dict(translate.__globals__, load_locale=lambda lang: catalogs[lang]):
        assert translate('server.only', 'de') == 'English only'


def test_translate_unknown_language_falls_back_to_english():
    assert translate('server.flash.loginFailed', 'xx') == 'Wrong email or password.'
    assert translate('server.flash.loginFailed', '../../etc/passwd') == 'Wrong email or password.'


@pytest.mark.parametrize('code,expected', [
    ('de', 'de'), ('DE', 'de'), ('de-AT', 'de'), ('de_CH', 'de'),
    ('pt-BR', 'pt-BR'), ('pt-br', 'pt-BR'), ('pt_BR', 'pt-BR'), ('pt', 'pt-BR'), ('pt-PT', 'pt-BR'),
    ('zh', 'zh'), ('zh-CN', 'zh'), ('zh-Hans', 'zh'),
    ('xx', None), ('', None), (None, None),
])
def test_match_language_maps_region_codes(code, expected):
    assert match_language(code) == expected


# ---------------------------------------------------------------------------
# format_number() / format_file_size(): the browser's i18n.formatNumber and
# i18n.formatFileSize on the server (#481), same cases as static/js/i18n.test.js
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('lang,expected', [
    ('en', '1.4 MB'), ('zh', '1.4 MB'), ('de', '1,4 MB'), ('es', '1,4 MB'),
    ('fr', '1,4 MB'), ('pt-BR', '1,4 MB'), ('ru', '1,4 МБ'),
])
def test_file_size_uses_the_languages_decimal_separator(lang, expected):
    assert format_file_size(1464812, lang) == expected


@pytest.mark.parametrize('size,expected', [
    (0, '0 B'), (None, '0 B'), (-5, '0 B'), (1023, '1023 B'), (1024, '1 KB'),
    (1536000, '1,5 MB'), (9 * 1024 ** 2, '9 MB'), (1024 ** 2 - 1, '1 MB'),
    (1024 ** 3 - 1024, '1 GB'), (1024 ** 3, '1 GB'), (int(2.5 * 1024 ** 4), '2,5 TB'),
    (1024 ** 5, '1024 TB'),
])
def test_file_size_units_and_rounding(size, expected):
    assert format_file_size(size, 'de') == expected


def test_format_number_groups_and_rounds_in_the_language():
    assert format_number(1234567, 'en') == '1,234,567'
    assert format_number(1234567, 'de') == '1.234.567'
    assert format_number(1234.25, 'de', max_fraction_digits=1) == '1.234,3'  # half up
    assert format_number(9.0, 'en', max_fraction_digits=1) == '9'
    assert format_number(1234, 'xx') == '1,234'  # unknown language: English


def test_format_number_uses_the_request_language_by_default():
    with app.test_request_context('/', headers={'Accept-Language': 'de'}):
        assert format_number(1234.5, max_fraction_digits=1) == '1.234,5'
        assert format_file_size(1536000) == '1,5 MB'


def test_outside_a_request_the_language_is_english():
    assert request_language() == 'en'
    assert translate('server.flash.loginFailed') == 'Wrong email or password.'


@pytest.mark.parametrize('header,expected', [
    ('de-DE,de;q=0.9,en;q=0.8', 'de'),
    ('fr-CA', 'fr'),
    ('pt', 'pt-BR'),
    ('zh-CN,zh;q=0.9', 'zh'),
    ('xx-YY', 'en'),
])
def test_anonymous_requests_use_accept_language(header, expected):
    with app.test_request_context('/', headers={'Accept-Language': header}):
        assert request_language() == expected


def test_anonymous_request_without_accept_language_is_english():
    with app.test_request_context('/'):
        assert translate('server.flash.loginFailed') == 'Wrong email or password.'


# ---------------------------------------------------------------------------
# Flash messages and emails
# ---------------------------------------------------------------------------

@pytest.fixture
def make_user():
    created = []

    def make(ui_language=None):
        with app.app_context():
            u = User(username=f'i18n_{uuid.uuid4().hex[:12]}', email=f'{uuid.uuid4().hex[:10]}@example.com',
                     password='x', ui_language=ui_language)
            db.session.add(u)
            db.session.commit()
            created.append(u.id)
            return u.id

    yield make
    with app.app_context():
        for uid in created:
            u = db.session.get(User, uid)
            if u:
                db.session.delete(u)
        db.session.commit()


def _flashes(client):
    with client.session_transaction() as s:
        return [message for _, message in s.get('_flashes', [])]


def test_flash_follows_the_users_interface_language(make_user):
    uid = make_user('de')
    prev = app.config.get('WTF_CSRF_ENABLED')
    app.config['WTF_CSRF_ENABLED'] = False
    try:
        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True
        # Accept-Language disagrees on purpose: the saved setting wins.
        resp = client.post('/change_password', data={'new_password': 'x', 'confirm_password': 'x'},
                           headers={'Accept-Language': 'fr'})
        assert resp.status_code == 302
        assert _flashes(client) == ['Das aktuelle Passwort ist erforderlich.']
    finally:
        app.config['WTF_CSRF_ENABLED'] = prev


def test_flash_for_anonymous_visitor_uses_accept_language():
    client = app.test_client()
    client.get('/account', headers={'Accept-Language': 'es-MX,es;q=0.9'})
    assert _flashes(client) == ['Inicie sesión para ver esta página.']


# ---------------------------------------------------------------------------
# Budget messages (#477): shown as the error of a blocked request or a failed
# recording, so they follow the budget owner's interface language.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('lang,percent', [
    ('en', '85.3%'), ('zh', '85.3%'), ('pt-BR', '85,3%'),
    ('de', '85,3\xa0%'), ('fr', '85,3\xa0%'), ('ru', '85,3\xa0%'),
])
def test_format_percent_follows_the_language(lang, percent):
    assert format_percent(85.25, lang) == percent
    assert format_percent(100, lang) == percent.replace('85,3', '100').replace('85.3', '100')


def _budget(tracker, uid, used, budget_field, budget):
    with app.app_context():
        u = db.session.get(User, uid)
        setattr(u, budget_field, budget)
        db.session.commit()
        with mock.patch.object(tracker, 'get_monthly_usage', return_value=used):
            return tracker.check_budget(uid)


def test_token_budget_messages_use_the_users_language(make_user):
    from src.services.token_tracking import token_tracker
    de, en = make_user('de'), make_user(None)
    assert _budget(token_tracker, de, 1200, 'monthly_token_budget', 1000) == (
        False, 120.0, 'Monatliches Token-Budget aufgebraucht (120\xa0%). '
        'Bitten Sie Ihren Administrator um mehr.')
    assert _budget(token_tracker, en, 853, 'monthly_token_budget', 1000) == (
        True, 85.3, '85.3% of your monthly token budget used.')
    assert _budget(token_tracker, en, 100, 'monthly_token_budget', 1000) == (True, 10.0, None)


def test_transcription_budget_messages_use_the_users_language(make_user):
    from src.services.transcription_tracking import transcription_tracker
    ru, en = make_user('ru'), make_user('en')
    can, _, msg = _budget(transcription_tracker, ru, 90 * 60, 'monthly_transcription_budget', 60 * 60)
    assert not can and msg.startswith('Месячный бюджет транскрипции исчерпан (90/60 мин).')
    assert _budget(transcription_tracker, en, 54 * 60, 'monthly_transcription_budget', 60 * 60)[2] == (
        '90% of your monthly transcription budget used.')


# ---------------------------------------------------------------------------
# API errors (#477): `error` in the interface language, `code` the same in
# every language. A response that already had a code keeps it.
# ---------------------------------------------------------------------------

def _session_client(uid):
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return client


@pytest.mark.parametrize('lang,text', [
    ('de', 'Aufnahme nicht gefunden'), ('fr', 'Enregistrement introuvable'),
    (None, 'Recording not found'),
    # An unknown saved language falls back to Accept-Language (ru here).
    ('xx', 'Запись не найдена'),
])
def test_api_error_follows_the_users_language_with_a_stable_code(make_user, lang, text):
    uid = make_user(lang)
    prev = app.config.get('WTF_CSRF_ENABLED')
    app.config['WTF_CSRF_ENABLED'] = False
    try:
        resp = _session_client(uid).post('/recording/999999999/update_speakers', json={},
                                         headers={'Accept-Language': 'ru'} if lang else {})
    finally:
        app.config['WTF_CSRF_ENABLED'] = prev
    assert resp.status_code == 404
    assert resp.get_json() == {'error': text, 'code': 'recording_not_found'}


def test_api_v1_token_error_keeps_its_existing_code(make_user):
    import secrets
    from src.models import APIToken
    from src.utils.token_auth import hash_token
    uid = make_user('de')
    plain = f'tok-{secrets.token_urlsafe(16)}'
    with app.app_context():
        db.session.add(APIToken(user_id=uid, token_hash=hash_token(plain), name='i18n'))
        db.session.commit()
    resp = app.test_client().get('/api/v1/recordings/999999999/external-refs',
                                 headers={'Authorization': f'Bearer {plain}'})
    assert resp.status_code == 404
    assert resp.get_json() == {'error': 'Aufnahme nicht gefunden', 'code': 'not_found'}
    with app.app_context():
        APIToken.query.filter_by(user_id=uid).delete()
        db.session.commit()


def test_api_error_for_anonymous_request_uses_accept_language():
    with app.test_request_context('/', headers={'Accept-Language': 'es-MX,es;q=0.9'}):
        assert api_error('recording_not_found') == {
            'error': translate('server.apiError.recording_not_found', 'es'), 'code': 'recording_not_found'}
        assert api_error('recording_not_found')['error'] != 'Recording not found'


def test_every_api_error_code_is_a_snake_case_key():
    import json
    import re
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'locales', 'en.json'), encoding='utf-8') as f:
        codes = json.load(f)['server']['apiError']
    assert codes and all(re.fullmatch(r'[a-z][a-z0-9_]*', c) for c in codes)


# ---------------------------------------------------------------------------
# Form validation messages (#477): fixed when the form class is defined,
# shown in the language of the request that renders them.
# ---------------------------------------------------------------------------

def test_lazy_translation_resolves_when_used():
    from src.utils.localization import LazyTranslation
    text = LazyTranslation('server.form.required')
    with app.test_request_context('/', headers={'Accept-Language': 'fr'}):
        assert str(text) == "Ce champ est obligatoire."
    with app.test_request_context('/'):
        assert str(text) == 'This field is required.'
