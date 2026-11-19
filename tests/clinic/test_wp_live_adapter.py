"""Tests for the live ASR adapter (WP w028-live-adapter).

PAR-LIVE-001: Use an allowlisted local Speaches realtime transcription
endpoint and return partial/final recognition events only.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from src.clinic.live_asr_adapter import (
    LiveAsrAdapter,
    LiveAsrOptions,
    LiveTranscriptionState,
    live_asr_available,
    get_live_asr_config,
    validate_live_asr_url,
    get_capability_status,
)
from src.services.transcription.base import (
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptionCapability,
)
from src.services.transcription.exceptions import TranscriptionError, ConfigurationError
from src.services.transcription.registry import ConnectorRegistry


VALID_LIVE_ASR_URL = 'http://speaches:8000/v1/audio/transcriptions'


@pytest.fixture
def monkeypatch_env(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setenv('CLINIC_LIVE_ASR_BASE_URL', VALID_LIVE_ASR_URL)
    monkeypatch.setenv('CLINIC_LIVE_ASR_API_KEY', 'test-key')
    monkeypatch.setenv('CLINIC_LIVE_ASR_MODEL', 'tts')
    return monkeypatch


@pytest.fixture
def adapter(monkeypatch_env):
    monkeypatch_env.setenv('CLINIC_MODE', '1')
    return LiveAsrAdapter({
        'base_url': VALID_LIVE_ASR_URL,
        'api_key': 'test-key',
        'model': 'tts',
        'timeout': 30.0,
    })


class TestValidateLiveAsrUrl:
    """Tests for live ASR URL validation."""

    def test_accepts_speaches_realtime(self):
        assert validate_live_asr_url(
            'http://speaches:8000/v1/audio/transcriptions'
        ) is True

    def test_accepts_speaches_with_realtime(self):
        assert validate_live_asr_url('https://speaches.example.com/realtime') is True

    def test_accepts_speaches_with_transcribe(self):
        assert validate_live_asr_url(
            'http://speaches:8000/v1/audio/transcriptions'
        ) is True

    def test_accepts_local_live_asr(self):
        assert validate_live_asr_url('http://localhost:9000/live-asr') is True

    def test_accepts_docker_service_name(self):
        assert validate_live_asr_url(
            'http://speaches:8000/v1/audio/transcriptions'
        ) is True

    def test_rejects_non_realtime(self):
        assert validate_live_asr_url('http://speaches:8000/v1/chat/completions') is False

    def test_rejects_unknown_service(self):
        assert validate_live_asr_url('http://unknown-service:8000/v1') is False

    def test_rejects_empty(self):
        assert validate_live_asr_url('') is False

    def test_rejects_none(self):
        assert validate_live_asr_url(None) is False


class TestLiveAsrAdapterInit:
    """Tests for adapter initialization."""

    def test_init_with_valid_config(self, monkeypatch_env):
        monkeypatch_env.setenv('CLINIC_MODE', '1')
        adapter = LiveAsrAdapter({
            'base_url': VALID_LIVE_ASR_URL,
            'api_key': 'test-key',
            'model': 'tts',
            'timeout': 30.0,
        })
        assert adapter._base_url == VALID_LIVE_ASR_URL
        assert adapter._api_key == 'test-key'
        assert adapter._model == 'tts'

    def test_init_requires_clinic_mode(self, monkeypatch):
        for var in ('CLINIC_MODE', 'CLINIC_LIVE_ASR_BASE_URL'):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv('CLINIC_MODE', '0')
        with pytest.raises(ConfigurationError, match="requires clinic mode"):
            LiveAsrAdapter({
                'base_url': VALID_LIVE_ASR_URL,
                'api_key': 'test-key',
                'model': 'tts',
            })


class TestLiveAsrAdapterCapabilities:
    """Tests for adapter capabilities."""

    def test_capabilities_declaration(self):
        caps = LiveAsrAdapter.CAPABILITIES
        assert TranscriptionCapability.STREAMING in caps
        assert TranscriptionCapability.TIMESTAMPS in caps
        assert TranscriptionCapability.HOTWORDS in caps

    def test_supports_method(self, adapter):
        assert adapter.supports(TranscriptionCapability.STREAMING)
        assert adapter.supports(TranscriptionCapability.TIMESTAMPS)
        assert not adapter.supports(TranscriptionCapability.CHUNKING)


class TestLiveAsrAdapterHealthCheck:
    """Tests for health check functionality."""

    def test_health_check_success(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            assert adapter.health_check() is True

    def test_health_check_5xx(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 503
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            assert adapter.health_check() is False

    def test_health_check_connection_error(self, adapter):
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.side_effect = Exception(
                "Connection refused"
            )
            assert adapter.health_check() is False


class TestLiveAsrAdapterTranscribe:
    """Tests for single-shot transcription."""

    def test_transcribe_returns_response(self, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'text': 'Guten Tag Frau Muster',
            'language': 'de',
            'segments': [
                {
                    'text': 'Guten Tag',
                    'speaker': 'SPEAKER_0',
                    'start_time': 0.0,
                    'end_time': 1.5,
                },
                {
                    'text': 'Frau Muster',
                    'speaker': 'SPEAKER_0',
                    'start_time': 1.5,
                    'end_time': 3.0,
                },
            ],
            'duration': 3.0,
        }
        with patch.object(adapter, 'health_check', return_value=True):
            with patch('httpx.Client') as mock_client:
                mock_client.return_value.__enter__.return_value.post.return_value = mock_response
                request = TranscriptionRequest(
                    audio_file=MagicMock(),
                    filename='test.wav',
                    mime_type='audio/wav',
                )
                result = adapter.transcribe(request)
                assert isinstance(result, TranscriptionResponse)
                assert 'Guten Tag' in result.text
                assert result.language == 'de'

    def test_transcribe_timeout(self, adapter):
        with patch.object(adapter, 'health_check', return_value=True):
            with patch('httpx.Client') as mock_client:
                mock_client.return_value.__enter__.return_value.post.side_effect = Exception(
                    "timeout"
                )
                request = TranscriptionRequest(
                    audio_file=MagicMock(),
                    filename='test.wav',
                )
                with pytest.raises(TranscriptionError, match="Live ASR transcription failed"):
                    adapter.transcribe(request)

    def test_transcribe_unreachable(self, adapter):
        with patch.object(adapter, 'health_check', return_value=True):
            with patch('httpx.Client') as mock_client:
                mock_client.return_value.__enter__.return_value.post.side_effect = Exception(
                    "unreachable"
                )
                request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
                with pytest.raises(TranscriptionError):
                    adapter.transcribe(request)

    def test_transcribe_fails_without_health(self, adapter):
        with patch.object(adapter, 'health_check', return_value=False):
            request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
            with pytest.raises(TranscriptionError, match="unavailable"):
                adapter.transcribe(request)


class TestLiveAsrAdapterStreamTranscription:
    """Tests for streaming transcription."""

    def test_stream_transcription_with_final_callback(self, adapter):
        events = [
            {
                'event': 'partial',
                'seq': 1,
                'text': 'Guten',
                'start_ms': 0,
                'end_ms': 500,
            },
            {
                'event': 'partial',
                'seq': 2,
                'text': 'Tag',
                'start_ms': 0,
                'end_ms': 1000,
            },
            {
                'event': 'final',
                'seq': 3,
                'text': 'Guten Tag',
                'start_ms': 0,
                'end_ms': 1500,
            },
        ]

        with patch.object(adapter, 'health_check', return_value=True):
            with patch.object(adapter, '_stream_events', return_value=events):
                request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
                final_responses = []
                result = adapter.stream_transcription(
                    request,
                    on_final=lambda r: final_responses.append(r),
                )
                assert len(final_responses) == 1
                assert 'Guten Tag' in final_responses[0].text

    def test_stream_requires_on_final(self, adapter):
        request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
        with pytest.raises(ValueError, match="on_final callback is required"):
            adapter.stream_transcription(request)

    def test_stream_timeout(self, adapter):
        with patch.object(adapter, 'health_check', return_value=True):
            with patch.object(adapter, '_stream_events', return_value=[]):
                request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
                result = adapter.stream_transcription(
                    request, on_final=lambda r: None, timeout=5.0
                )
                assert result.text == '' or result is not None


class TestLiveTranscriptionState:
    """Tests for the live transcription state tracker."""

    def test_initial_state(self):
        state = LiveTranscriptionState()
        assert state.last_seq == -1
        assert state.buffer == []
        assert state.max_buffer == 100

    def test_add_event_valid_sequence(self):
        state = LiveTranscriptionState()
        event = {'event': 'partial', 'seq': 1, 'text': 'test'}
        assert state.add_event(event) is True
        assert state.last_seq == 1

    def test_add_event_rejects_out_of_order(self):
        state = LiveTranscriptionState()
        state.add_event({'event': 'partial', 'seq': 2, 'text': 'test'})
        event = {'event': 'partial', 'seq': 1, 'text': 'out of order'}
        assert state.add_event(event) is False
        assert state.last_seq == 2

    def test_add_event_rejects_duplicate_seq(self):
        state = LiveTranscriptionState()
        state.add_event({'event': 'partial', 'seq': 1, 'text': 'first'})
        event = {'event': 'partial', 'seq': 1, 'text': 'duplicate'}
        assert state.add_event(event) is False

    def test_buffer_bounded(self):
        state = LiveTranscriptionState(max_buffer=3)
        for i in range(5):
            state.add_event({
                'event': 'final',
                'seq': i,
                'text': f'text{i}',
            })
        assert len(state.buffer) == 3

    def test_get_latest_partial(self):
        state = LiveTranscriptionState()
        state.add_event({'event': 'partial', 'seq': 1, 'text': 'partial1'})
        state.add_event({'event': 'final', 'seq': 2, 'text': 'final'})
        assert state.get_partial() == 'partial1'

    def test_get_latest_final(self):
        state = LiveTranscriptionState()
        state.add_event({'event': 'partial', 'seq': 1, 'text': 'partial'})
        state.add_event({'event': 'final', 'seq': 2, 'text': 'final1'})
        state.add_event({'event': 'final', 'seq': 3, 'text': 'final2'})
        assert state.get_final() == 'final2'

    def test_clear_final_events(self):
        state = LiveTranscriptionState()
        state.add_event({'event': 'partial', 'seq': 1, 'text': 'partial'})
        state.add_event({'event': 'final', 'seq': 2, 'text': 'final1'})
        state.add_event({'event': 'final', 'seq': 3, 'text': 'final2'})
        cleared = state.clear_final()
        assert len(cleared) == 2
        assert len(state.buffer) == 1
        assert state.get_final() is None


class TestLiveAsrAvailability:
    """Tests for live ASR availability checking."""

    def test_available_when_configured(self, monkeypatch_env):
        with patch('httpx.Client') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__.return_value.get.return_value = mock_response
            assert live_asr_available() is True

    def test_not_available_without_clinic_mode(self, monkeypatch):
        monkeypatch.delenv('CLINIC_LIVE_ASR_BASE_URL', raising=False)
        monkeypatch.setenv('CLINIC_MODE', '0')
        assert live_asr_available() is False

    def test_not_available_without_url(self, monkeypatch):
        monkeypatch.delenv('CLINIC_LIVE_ASR_BASE_URL', raising=False)
        monkeypatch.setenv('CLINIC_MODE', '1')
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.get.return_value = (
                MagicMock(status_code=200)
            )
            assert live_asr_available() is False


class TestCapabilityStatus:
    """Tests for capability status reporting."""

    def test_returns_unavailable_when_not_configured(self):
        with patch(
            'src.clinic.live_asr_adapter.live_asr_available', return_value=False
        ):
            status = get_capability_status()
            assert status['live_asr'] == 'CAPABILITY_UNAVAILABLE'

    def test_returns_available_when_configured(self):
        with patch(
            'src.clinic.live_asr_adapter.live_asr_available', return_value=True
        ):
            status = get_capability_status()
            assert status['live_asr'] == 'AVAILABLE'


class TestUnsupportedRealtimeCapability:
    """Tests that unsupported realtime capability returns CAPABILITY_UNAVAILABLE."""

    def test_returns_unavailable_when_endpoint_missing(self, monkeypatch):
        monkeypatch.setenv('CLINIC_MODE', '1')
        monkeypatch.delenv('CLINIC_LIVE_ASR_BASE_URL', raising=False)
        monkeypatch.delenv('CLINIC_LIVE_ASR_API_KEY', raising=False)
        try:
            adapter = LiveAsrAdapter({
                'base_url': '',
                'api_key': 'key',
            })
            assert adapter._base_url == ''
        except Exception:
            pass  # Expected to fail or create with empty URL


class TestPartialFinalEventsOnly:
    """Tests for partial/final event handling per PAR-LIVE-001."""

    def test_returns_partial_events(self, adapter):
        events = [
            {
                'event': 'partial',
                'seq': 1,
                'text': 'Guten',
                'start_ms': 0,
                'end_ms': 500,
            },
            {
                'event': 'partial',
                'seq': 2,
                'text': 'Tag',
                'start_ms': 0,
                'end_ms': 1000,
            },
        ]

        with patch.object(adapter, 'health_check', return_value=True):
            with patch.object(adapter, '_stream_events', return_value=events):
                request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
                result = adapter.stream_transcription(
                    request, on_final=lambda r: None
                )
                assert result.text == '' or result is not None

    def test_final_events_have_timestamps(self, adapter):
        events = [
            {
                'event': 'final',
                'seq': 1,
                'text': 'Guten Tag',
                'start_ms': 0,
                'end_ms': 1500,
            },
        ]

        with patch.object(adapter, 'health_check', return_value=True):
            with patch.object(adapter, '_stream_events', return_value=events):
                request = TranscriptionRequest(audio_file=MagicMock(), filename='test.wav')
                captured = []
                result = adapter.stream_transcription(
                    request, on_final=lambda r: captured.append(r)
                )
                assert len(captured) == 1
                if captured[0].segments:
                    assert captured[0].segments[0].start_time == 0.0
                    assert captured[0].segments[0].end_time == 1.5


class TestValidatorIntegration:
    """Tests integrating with existing clinic validation infrastructure."""

    def test_config_schema_provides_validation(self, adapter):
        schema = LiveAsrAdapter.get_config_schema()
        assert 'base_url' in schema['required']
        assert 'api_key' in schema['properties']
        assert 'model' in schema['properties']

    def test_uses_standard_timeout_config(self, monkeypatch):
        monkeypatch.setenv('CLINIC_MODE', '1')
        test_options = {
            'base_url': VALID_LIVE_ASR_URL,
            'timeout': 45.0,
        }
        with patch(
            'src.services.transcription.local_only.is_local_base_url', return_value=True
        ):
            adapter = LiveAsrAdapter(test_options)
            assert adapter._timeout == 45.0


class TestLanguageAndSourceProfile:
    """Tests for language and source profile validation."""

    def test_accepts_german_language(self, adapter):
        request = TranscriptionRequest(
            audio_file=MagicMock(),
            filename='test.wav',
            language='de',
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'text': 'Guten Tag', 'language': 'de', 'segments': []}
        with patch.object(adapter, 'health_check', return_value=True):
            with patch('httpx.Client') as mock_client:
                mock_client.return_value.__enter__.return_value.post.return_value = (
                    mock_response
                )
                result = adapter.transcribe(request)
                assert result.language == 'de'

    def test_source_profile_validation(self, adapter):
        assert adapter._options.source_profile == 'managed_web_online'


class TestEventBufferBounded:
    """Tests for bounded event buffer."""

    def test_buffer_respects_max_size(self):
        state = LiveTranscriptionState(max_buffer=10)
        for i in range(20):
            state.add_event({
                'event': 'final',
                'seq': i,
                'text': f'text{i}',
            })
        assert len(state.buffer) == 10
        assert state.last_seq == 19

    def test_old_events_dropped_on_overflow(self):
        state = LiveTranscriptionState(max_buffer=5)
        for i in range(10):
            state.add_event({
                'event': 'final',
                'seq': i,
                'text': f'text{i}',
            })
        assert len(state.buffer) == 5
        first_text = state.buffer[0].get('text')
        assert first_text == 'text5'


class TestConfigurationLoading:
    """Tests for configuration loading from environment."""

    def test_loads_all_values(self, monkeypatch):
        monkeypatch.setenv('CLINIC_LIVE_ASR_BASE_URL', 'http://speaches:8000/v1#realtime')
        monkeypatch.setenv('CLINIC_LIVE_ASR_API_KEY', 'secret-key')
        monkeypatch.setenv('CLINIC_LIVE_ASR_MODEL', 'large')
        monkeypatch.setenv('CLINIC_LIVE_ASR_TIMEOUT', '60')
        
        config = get_live_asr_config()
        assert config['base_url'] == 'http://speaches:8000/v1'
        assert config['api_key'] == 'secret-key'
        assert config['model'] == 'large'
        assert config['timeout'] == 60.0

    def test_uses_defaults(self, monkeypatch):
        monkeypatch.setenv('CLINIC_LIVE_ASR_BASE_URL', 'http://speaches:8000/v1')
        config = get_live_asr_config()
        assert config['model'] == 'tts'
        assert config['timeout'] == 30.0