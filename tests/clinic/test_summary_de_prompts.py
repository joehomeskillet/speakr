"""Deterministic prompt-build tests for the German clinic summary, title and chat."""

import pytest

from src import transcription_prompts as prompts
from src.clinic import prompts as clinic_prompts
from src.clinic.prompts import CLINIC_DEFAULT_SUMMARY_PROMPT, CLINIC_DEFAULT_TITLE_PROMPT
from src.config.prompts import DEFAULT_SUMMARY_PROMPT as UPSTREAM_SUMMARY, DEFAULT_TITLE_PROMPT as UPSTREAM_TITLE

DEFAULT_SUMMARY_PROMPT = CLINIC_DEFAULT_SUMMARY_PROMPT
DEFAULT_TITLE_PROMPT = CLINIC_DEFAULT_TITLE_PROMPT

# Synthetic dictation, fictitious patient.
TRANSCRIPT = (
    "Patient Test, Hans, 45 Jahre, Aufnahme zum qualifizierten Alkoholentzug. "
    "Letzter Konsum gestern Abend, etwa 2 Promille bei Eintritt. CIWA-Ar 14. "
    "Diazepam 10 mg dreimal täglich, Ausschleichen über 5 Tage."
)

HEADINGS = (
    "Anlass / Fragestellung", "Suchtanamnese", "Aktueller Befund / Psychischer Status",
    "Diagnosen", "Medikation / Entzugsbehandlung", "Procedere / Nächste Schritte",
    "Offene Punkte / Unklarheiten",
)


def test_default_prompt_has_german_headings_and_rules():
    for heading in HEADINGS:
        assert f"### {heading}" in DEFAULT_SUMMARY_PROMPT
    for needle in ("CIWA-Ar", "COWS", "ICD-10", "[bitte prüfen]", "ausschließlich Inhalte aus dem Transkript",
                   "keine zusätzlichen Diagnosen, Dosierungen oder Empfehlungen", "exakt", "weg"):
        assert needle in DEFAULT_SUMMARY_PROMPT


def test_default_title_prompt_is_german():
    assert "Deutsch" in DEFAULT_TITLE_PROMPT


@pytest.mark.parametrize("cache", [True, False])
def test_clinic_summary_prompt_demands_german(monkeypatch, cache):
    monkeypatch.setenv('CLINIC_MODE', '1')
    system, user = prompts.build_summary_prompt(TRANSCRIPT, "", DEFAULT_SUMMARY_PROMPT, None, cache)
    built = system + "\n" + user
    assert "in German" in built
    assert "Hans" in user
    for heading in HEADINGS:
        assert heading in user


@pytest.mark.parametrize("cache", [True, False])
def test_clinic_title_prompt_demands_german(monkeypatch, cache):
    monkeypatch.setenv('CLINIC_MODE', '1')
    system, user = prompts.build_title_prompt(TRANSCRIPT, DEFAULT_TITLE_PROMPT, None, cache)
    assert "German" in system + user


@pytest.mark.parametrize("cache", [True, False])
def test_explicit_output_language_wins(monkeypatch, cache):
    monkeypatch.setenv('CLINIC_MODE', '1')
    system, user = prompts.build_summary_prompt(TRANSCRIPT, "", DEFAULT_SUMMARY_PROMPT, "French", cache)
    assert "in French" in system + user
    assert "German" not in system + user
    t_system, t_user = prompts.build_title_prompt(TRANSCRIPT, DEFAULT_TITLE_PROMPT, "French", cache)
    assert "French" in t_system + t_user and "German" not in t_system + t_user


def test_without_clinic_mode_no_language_directive(monkeypatch):
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    system, user = prompts.build_summary_prompt(TRANSCRIPT, "", "x", None, False)
    assert "German" not in system + user


def test_chat_default_language_in_clinic_mode(monkeypatch):
    from src.clinic.output_language import effective_output_language
    monkeypatch.setenv('CLINIC_MODE', '1')
    assert effective_output_language(None) == 'German'
    assert effective_output_language('') == 'German'
    assert effective_output_language('Spanish') == 'Spanish'
    monkeypatch.delenv('CLINIC_MODE')
    assert effective_output_language(None) is None


def test_clinic_mode_selects_the_german_defaults(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    assert clinic_prompts.shipped_summary_prompt() == CLINIC_DEFAULT_SUMMARY_PROMPT
    assert clinic_prompts.shipped_title_prompt() == CLINIC_DEFAULT_TITLE_PROMPT
    # unset or still-upstream admin values become the clinic default
    assert clinic_prompts.admin_summary_prompt(None) == CLINIC_DEFAULT_SUMMARY_PROMPT
    assert clinic_prompts.admin_summary_prompt(UPSTREAM_SUMMARY) == CLINIC_DEFAULT_SUMMARY_PROMPT
    assert clinic_prompts.admin_title_prompt(UPSTREAM_TITLE) == CLINIC_DEFAULT_TITLE_PROMPT


def test_without_clinic_mode_the_upstream_defaults_stay(monkeypatch):
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    assert clinic_prompts.shipped_summary_prompt() == UPSTREAM_SUMMARY
    assert clinic_prompts.shipped_title_prompt() == UPSTREAM_TITLE
    assert clinic_prompts.admin_summary_prompt(UPSTREAM_SUMMARY) == UPSTREAM_SUMMARY
    assert clinic_prompts.admin_summary_prompt(None) is None
    assert "Minutes" in UPSTREAM_SUMMARY and "Maximum 8 words" in UPSTREAM_TITLE


def test_custom_admin_prompt_wins_in_clinic_mode(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    assert clinic_prompts.admin_summary_prompt("Eigener Prompt") == "Eigener Prompt"
    assert clinic_prompts.admin_title_prompt("- Eigener Titel") == "- Eigener Titel"


def test_title_resolution_in_clinic_mode(monkeypatch):
    from src.app import app
    from src.models import SystemSetting
    from src.services.titling import user_title_instructions

    monkeypatch.setenv('CLINIC_MODE', '1')
    with app.app_context():
        previous = SystemSetting.get_setting('admin_default_title_prompt', None)
        try:
            SystemSetting.set_setting('admin_default_title_prompt', UPSTREAM_TITLE, setting_type='string')
            assert user_title_instructions(None)[0] == CLINIC_DEFAULT_TITLE_PROMPT
            SystemSetting.set_setting('admin_default_title_prompt', '- Eigener Titel', setting_type='string')
            assert user_title_instructions(None) == ('- Eigener Titel', 'admin')
            monkeypatch.delenv('CLINIC_MODE')
            SystemSetting.set_setting('admin_default_title_prompt', UPSTREAM_TITLE, setting_type='string')
            assert user_title_instructions(None)[0] == UPSTREAM_TITLE
        finally:
            SystemSetting.set_setting('admin_default_title_prompt',
                                      previous if previous is not None else UPSTREAM_TITLE,
                                      setting_type='string')
