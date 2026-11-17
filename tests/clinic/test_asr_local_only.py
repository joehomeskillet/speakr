"""
Tests for local-only ASR base URL validation and clinic mode enforcement (WP RM-cloud-asr).
"""

import os
import pytest

from src.services.transcription.exceptions import ConfigurationError
from src.services.transcription.local_only import is_local_base_url, validate_clinic_base_url
from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector


class TestIsLocalBaseUrl:
    """Test pure function is_local_base_url."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000",
            "http://localhost:8000/v1",
            "https://localhost/v1",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.2:9000",
            "http://127.128.0.1:8000",
            "http://[::1]:8000",
            "http://[::1]:8000/v1",
            "localhost:8000",
            "127.0.0.1:8000",
        ],
    )
    def test_loopback_urls_accepted(self, url):
        assert is_local_base_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.1:8000",
            "http://10.254.1.100:9000/v1",
            "http://172.16.0.1:8000",
            "http://172.20.0.5:8000/v1",
            "http://172.31.255.254:8000",
            "http://192.168.1.1:8000",
            "http://192.168.100.50:8000/v1",
            "http://169.254.1.1:8000",
            "http://[fd00::1]:8000",
            "http://[fd12:3456:789a:1::1]:8000/v1",
            "http://[fc00::1]:8000",
            "http://[fe80::1]:8000",
        ],
    )
    def test_rfc1918_and_ula_private_ips_accepted(self, url):
        assert is_local_base_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://speaches:8000",
            "http://speaches:8000/v1",
            "http://whisper:9000",
            "http://asr-service:8000",
            "http://local_whisper:8000/v1",
            "http://speaches.local:8000",
            "http://speaches.internal:8000",
            "http://speaches.lan:8000",
            "http://speaches.localhost:8000",
        ],
    )
    def test_single_label_and_local_hostnames_accepted(self, url):
        assert is_local_base_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com",
            "https://api.openai.com/v1",
            "https://api.mistral.ai",
            "https://api.assemblyai.com",
            "https://example.com/v1",
            "http://speaches.example.com:8000",
            "http://asr.company.org:9000",
            "http://1.1.1.1:8000",
            "http://8.8.8.8:8000",
            "http://93.184.216.34:8000",
            "http://[2001:4860:4860::8888]:8000",
            "",
            "   ",
            None,
        ],
    )
    def test_public_urls_and_invalids_rejected(self, url):
        assert is_local_base_url(url) is False


class TestValidateClinicBaseUrl:
    """Test clinic mode enforcement with validate_clinic_base_url."""

    def test_clinic_mode_off_allows_public_and_none(self, monkeypatch):
        monkeypatch.delenv("CLINIC_MODE", raising=False)
        validate_clinic_base_url("https://api.openai.com/v1")
        validate_clinic_base_url(None)
        validate_clinic_base_url("")

    def test_clinic_mode_on_allows_local_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        validate_clinic_base_url("http://127.0.0.1:8000/v1")
        validate_clinic_base_url("http://speaches:8000/v1")
        validate_clinic_base_url("http://10.0.0.5:8000")

    def test_clinic_mode_on_fails_closed_on_public_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        with pytest.raises(ConfigurationError, match="CLINIC_MODE is active"):
            validate_clinic_base_url("https://api.openai.com/v1")

    def test_clinic_mode_on_fails_closed_on_none_or_empty(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "true")
        with pytest.raises(ConfigurationError, match="CLINIC_MODE is active"):
            validate_clinic_base_url(None)
        with pytest.raises(ConfigurationError, match="CLINIC_MODE is active"):
            validate_clinic_base_url("")


class TestOpenAIWhisperConnectorClinicGuard:
    """Test OpenAIWhisperConnector initialization under CLINIC_MODE."""

    def test_initialization_with_clinic_mode_on_and_local_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        connector = OpenAIWhisperConnector({
            "api_key": "dummy-key",
            "base_url": "http://127.0.0.1:8000/v1",
        })
        assert connector is not None
        assert connector.model == "whisper-1"

    def test_initialization_with_clinic_mode_on_and_single_label_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        connector = OpenAIWhisperConnector({
            "api_key": "dummy-key",
            "base_url": "http://speaches:8000/v1",
        })
        assert connector is not None

    def test_initialization_fails_closed_when_clinic_mode_on_and_public_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        with pytest.raises(ConfigurationError, match="CLINIC_MODE is active"):
            OpenAIWhisperConnector({
                "api_key": "dummy-key",
                "base_url": "https://api.openai.com/v1",
            })

    def test_initialization_fails_closed_when_clinic_mode_on_and_no_base_url(self, monkeypatch):
        monkeypatch.setenv("CLINIC_MODE", "1")
        with pytest.raises(ConfigurationError, match="CLINIC_MODE is active"):
            OpenAIWhisperConnector({
                "api_key": "dummy-key",
            })

    def test_initialization_unchanged_when_clinic_mode_off(self, monkeypatch):
        monkeypatch.delenv("CLINIC_MODE", raising=False)
        connector = OpenAIWhisperConnector({
            "api_key": "dummy-key",
            "base_url": "https://api.openai.com/v1",
        })
        assert connector is not None

        connector_default = OpenAIWhisperConnector({
            "api_key": "dummy-key",
        })
        assert connector_default is not None
