"""TRANSCRIPTION_TIMEOUT / TRANSCRIPTION_MAX_RETRIES for the OpenAI-compatible ASR connector."""

import pytest

from src.services.transcription.connectors import openai_whisper
from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
from src.services.transcription.exceptions import ConfigurationError

CONFIG = {'api_key': 'local', 'base_url': 'http://127.0.0.1:9/v1', 'model': 'm'}


@pytest.fixture
def ctor(monkeypatch):
    for name in ('CLINIC_MODE', 'TRANSCRIPTION_TIMEOUT', 'TRANSCRIPTION_MAX_RETRIES'):
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(openai_whisper, 'OpenAI', lambda **kw: calls.append(kw) or object())
    return calls


def test_unset_and_clinic_off_passes_no_timeout_or_retries(ctor):
    OpenAIWhisperConnector(CONFIG)
    assert 'timeout' not in ctor[0] and 'max_retries' not in ctor[0]


def test_clinic_mode_defaults(ctor, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    OpenAIWhisperConnector(CONFIG)
    assert ctor[0]['timeout'] == 3600.0 and ctor[0]['max_retries'] == 0


def test_explicit_env_wins_in_both_modes(ctor, monkeypatch):
    monkeypatch.setenv('TRANSCRIPTION_TIMEOUT', '7200')
    monkeypatch.setenv('TRANSCRIPTION_MAX_RETRIES', '1')
    OpenAIWhisperConnector(CONFIG)
    monkeypatch.setenv('CLINIC_MODE', '1')
    OpenAIWhisperConnector(CONFIG)
    for kw in ctor:
        assert kw['timeout'] == 7200.0 and kw['max_retries'] == 1


def test_clinic_mode_partial_override_keeps_other_default(ctor, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setenv('TRANSCRIPTION_TIMEOUT', '90')
    OpenAIWhisperConnector(CONFIG)
    assert ctor[0]['timeout'] == 90.0 and ctor[0]['max_retries'] == 0


@pytest.mark.parametrize('name,value', [
    ('TRANSCRIPTION_TIMEOUT', 'abc'),
    ('TRANSCRIPTION_TIMEOUT', '0'),
    ('TRANSCRIPTION_TIMEOUT', '-5'),
    ('TRANSCRIPTION_TIMEOUT', 'inf'),
    ('TRANSCRIPTION_TIMEOUT', 'nan'),
    ('TRANSCRIPTION_MAX_RETRIES', '-1'),
    ('TRANSCRIPTION_MAX_RETRIES', '1.5'),
    ('TRANSCRIPTION_MAX_RETRIES', 'x'),
])
def test_invalid_values_raise_clear_error(ctor, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        OpenAIWhisperConnector(CONFIG)
    assert ctor == []
