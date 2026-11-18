"""Deterministic prompt-build tests for the German clinic summary, title and chat."""

import pytest

from src import transcription_prompts as prompts
from src.config.prompts import DEFAULT_SUMMARY_PROMPT, DEFAULT_TITLE_PROMPT

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


def test_clinic_incognito_summary_demands_german(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    system, user = prompts.build_incognito_summary_prompt(TRANSCRIPT, DEFAULT_SUMMARY_PROMPT, None)
    assert "in German" in system and "in German" in user


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
