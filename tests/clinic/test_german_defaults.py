"""German product defaults, without changing explicitly selected languages."""

import uuid

import pytest

from src.app import app, db
from src.config.prompts import DEFAULT_SUMMARY_PROMPT, DEFAULT_TITLE_PROMPT
from src.models import User
from src.transcription_prompts import (
    build_incognito_summary_prompt,
    build_summary_prompt,
    build_title_prompt,
)


def test_new_user_languages_are_german_and_persist():
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        user = User(username=f"de_{suffix}", email=f"de_{suffix}@example.invalid")
        db.session.add(user)
        try:
            db.session.flush()
            user_id = user.id
            db.session.expire_all()
            saved = db.session.get(User, user_id)
            assert saved.transcription_language == 'de'
            assert saved.output_language == 'German'
            assert saved.ui_language == 'de'
        finally:
            db.session.rollback()


def test_explicit_auto_detection_and_other_languages_persist():
    suffix = uuid.uuid4().hex[:12]
    with app.app_context():
        user = User(
            username=f"auto_{suffix}", email=f"auto_{suffix}@example.invalid",
            transcription_language=None, output_language=None, ui_language='en',
        )
        db.session.add(user)
        try:
            db.session.flush()
            user_id = user.id
            db.session.expire_all()
            saved = db.session.get(User, user_id)
            assert saved.transcription_language is None
            assert saved.output_language is None
            assert saved.ui_language == 'en'
        finally:
            db.session.rollback()


def test_shipped_summary_and_title_are_in_german():
    assert 'Sitzungsprotokoll' in DEFAULT_SUMMARY_PROMPT
    assert 'Besprochene Kernthemen' in DEFAULT_SUMMARY_PROMPT
    assert 'Nächste Schritte und Verantwortliche' in DEFAULT_SUMMARY_PROMPT
    assert 'Formuliere einen aussagekräftigen Titel' in DEFAULT_TITLE_PROMPT


@pytest.mark.parametrize('prefix_cache_optimized', [False, True])
def test_explicit_output_language_overrides_german_default_prompts(prefix_cache_optimized):
    title_system, title_body = build_title_prompt(
        'Hello world', DEFAULT_TITLE_PROMPT, 'English', prefix_cache_optimized,
    )
    summary_system, summary_body = build_summary_prompt(
        'Hello world', '', DEFAULT_SUMMARY_PROMPT, 'English', prefix_cache_optimized,
    )
    assert 'English' in title_system + title_body
    assert 'English' in summary_system + summary_body
    assert 'Schreibe den Titel auf Deutsch' not in title_body
    assert 'Gib alle Überschriften und Erläuterungen auf Deutsch aus' not in summary_body


def test_incognito_summary_honors_explicit_output_language():
    system, body = build_incognito_summary_prompt('Bonjour', DEFAULT_SUMMARY_PROMPT, 'French')
    assert 'French' in system
    assert 'French' in body
