"""
Tests for the clinic_stub transcription connector (WP08).

The stub is not an ASR quality proof. These tests only check registration,
deterministic synthetic output, production refusal and the absence of network use.
"""

import io
import socket
import wave
from pathlib import Path

import pytest

from src.services.transcription import registry as registry_module
from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.connectors.clinic_stub import (
    ClinicStubTranscriptionConnector,
    SYNTHETIC_SENTENCES,
)
from src.services.transcription.exceptions import ConfigurationError, TranscriptionError

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "clinic-spec"
    / "sources"
    / "dns-functional"
    / "fixtures"
    / "generated"
    / "audio"
    / "tone_16k_mono.wav"
)


def _request(data: bytes, filename: str = "tone_16k_mono.wav") -> TranscriptionRequest:
    return TranscriptionRequest(audio_file=io.BytesIO(data), filename=filename, mime_type="audio/wav")


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _fixture_duration() -> float:
    with wave.open(str(FIXTURE), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


@pytest.fixture(autouse=True)
def _clean_profile(monkeypatch):
    monkeypatch.delenv("CLINIC_PROFILE", raising=False)


def test_connector_is_registered_under_clinic_stub():
    reg = registry_module.get_registry()
    assert reg.get_connector_class("clinic_stub") is ClinicStubTranscriptionConnector
    names = [entry["name"] for entry in reg.list_connectors()]
    assert "clinic_stub" in names


def test_connector_selected_only_by_explicit_configuration(monkeypatch):
    reg = registry_module.get_registry()
    monkeypatch.setattr(reg, "_active_connector", None)
    monkeypatch.setattr(reg, "_connector_name", "")
    monkeypatch.setenv("TRANSCRIPTION_CONNECTOR", "clinic_stub")
    connector = reg.initialize_from_env()
    assert isinstance(connector, ClinicStubTranscriptionConnector)


def test_connector_is_not_default_without_configuration(monkeypatch):
    reg = registry_module.get_registry()
    monkeypatch.setattr(reg, "_active_connector", None)
    monkeypatch.setattr(reg, "_connector_name", "")
    for var in ("TRANSCRIPTION_CONNECTOR", "ASR_BASE_URL", "USE_ASR_ENDPOINT", "TRANSCRIPTION_MODEL", "WHISPER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    connector = reg.initialize_from_env()
    assert not isinstance(connector, ClinicStubTranscriptionConnector)


def test_output_is_deterministic_and_marked_synthetic():
    first = ClinicStubTranscriptionConnector({}).transcribe(_request(_fixture_bytes()))
    second = ClinicStubTranscriptionConnector({}).transcribe(_request(_fixture_bytes()))

    assert first.text == second.text
    assert [s.text for s in first.segments] == SYNTHETIC_SENTENCES
    assert [(s.start_time, s.end_time) for s in first.segments] == [
        (s.start_time, s.end_time) for s in second.segments
    ]
    assert first.raw_response["synthetic"] is True
    assert first.raw_response["not_asr_quality_proof"] is True
    assert first.provider == "clinic_stub"
    assert first.language == "de"


def test_timestamps_follow_audio_duration():
    duration = _fixture_duration()
    result = ClinicStubTranscriptionConnector({}).transcribe(_request(_fixture_bytes()))

    assert result.segments[0].start_time == 0.0
    assert result.segments[-1].end_time == round(duration, 3)
    for previous, current in zip(result.segments, result.segments[1:]):
        assert previous.end_time == current.start_time
    assert result.duration == round(duration, 3)


def test_non_wav_input_is_rejected():
    with pytest.raises(TranscriptionError):
        ClinicStubTranscriptionConnector({}).transcribe(_request(b"not a wav file"))


def test_refuses_to_construct_in_production_profile(monkeypatch):
    monkeypatch.setenv("CLINIC_PROFILE", "production")
    with pytest.raises(ConfigurationError):
        ClinicStubTranscriptionConnector({})


def test_refuses_to_transcribe_when_production_profile_set_after_construction(monkeypatch):
    connector = ClinicStubTranscriptionConnector({})
    monkeypatch.setenv("CLINIC_PROFILE", "production")
    with pytest.raises(ConfigurationError):
        connector.transcribe(_request(_fixture_bytes()))


def test_no_socket_is_opened(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("clinic_stub must not open a socket")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    result = ClinicStubTranscriptionConnector({}).transcribe(_request(_fixture_bytes()))
    assert len(result.segments) == len(SYNTHETIC_SENTENCES)
