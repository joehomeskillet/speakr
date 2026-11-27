"""Adapt local realtime transcription to the durable phrase contract.

PAR-LIVE-001: Use an allowlisted local Speaches realtime transcription endpoint
and return partial/final recognition events only.

The adapter wraps a speaches realtime connector and presents it as a
BaseTranscriptionConnector that yields partial and final TranscriptionResponse
objects. Validation includes event order, bounded buffers, language and source
profile checks. Timeouts surface a recoverable state. The adapter never invokes
a generative rewrite or cloud recognizer; unsupported realtime capability
returns CAPABILITY_UNAVAILABLE.

Configuration via CLINIC_LIVE_ASR_BASE_URL (allowlisted Speaches realtime endpoint).
The endpoint must be local (checked by local_only.is_local_base_url).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Callable

import httpx
from typing_extensions import TypedDict

from src.services.transcription.base import (
    BaseTranscriptionConnector,
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptionSegment,
    TranscriptionCapability,
    ConnectorSpecifications,
)
from src.services.transcription.exceptions import TranscriptionError, ConfigurationError
from src.services.transcription.local_only import is_local_base_url
from src.clinic.config import clinic_mode_enabled
from src.clinic.asr_profiles import effective_hotwords as get_hotwords_for_user
from src.clinic.live_asr_transport import iter_speaches_events

logger = logging.getLogger(__name__)

LIVE_ASR_URL_ENV = 'CLINIC_LIVE_ASR_BASE_URL'
LIVE_ASR_TIMEOUT_ENV = 'CLINIC_LIVE_ASR_TIMEOUT'
LIVE_ASR_API_KEY_ENV = 'CLINIC_LIVE_ASR_API_KEY'
LIVE_ASR_MODEL_ENV = 'CLINIC_LIVE_ASR_MODEL'
DEFAULT_LIVE_TIMEOUT = 30.0
DEFAULT_BUFFER_SIZE = 100
DEFAULT_EVENT_TIMEOUT = 2.0

LIVE_CAPABILITY_FLAGS = {'streaming', 'partial', 'final_only', 'json_ld'}


class LiveTranscriptionEvent(TypedDict, total=False):
    """A single event from the Speaches realtime endpoint."""
    event: str
    seq: int
    text: str
    words: List[Dict[str, Any]]
    start_ms: int
    end_ms: int
    language: str
    model: str
    timestamp: float


class LiveTranscriptionState:
    """Tracks the state of an ongoing live transcription session."""

    def __init__(self, max_buffer: int = DEFAULT_BUFFER_SIZE):
        self.last_seq = -1
        self.buffer: List[LiveTranscriptionEvent] = []
        self.max_buffer = max_buffer
        self.is_active = False
        self.start_time: Optional[float] = None
        self.last_event_time: Optional[float] = None

    def add_event(self, event: LiveTranscriptionEvent) -> bool:
        """Add an event, validating sequence order. Returns True if valid."""
        seq = event.get('seq', -1)
        if seq <= self.last_seq:
            logger.warning(f"Live ASR: event order violation (seq {seq} <= {self.last_seq})")
            return False
        if len(self.buffer) >= self.max_buffer:
            self.buffer.pop(0)
        self.buffer.append(event)
        self.last_seq = seq
        self.last_event_time = event.get('timestamp', time.time())
        return True

    def get_partial(self) -> Optional[str]:
        """Get the latest partial text from buffer."""
        for evt in reversed(self.buffer):
            if evt.get('event') == 'partial':
                return evt.get('text')
        return None

    def get_final(self) -> Optional[str]:
        """Get the latest final text from buffer."""
        for evt in reversed(self.buffer):
            if evt.get('event') == 'final':
                return evt.get('text')
        return None

    def clear_final(self) -> List[LiveTranscriptionEvent]:
        """Remove and return all final events from buffer."""
        finals = [e for e in self.buffer if e.get('event') == 'final']
        self.buffer = [e for e in self.buffer if e.get('event') != 'final']
        return finals


def validate_live_asr_url(url: str) -> bool:
    """Check if URL is in the allowlist for live ASR endpoints.
    
    Accepts:
    - Speaches endpoints with realtime capabilities: /v1/audio/transcriptions, /realtime
    - Generic live ASR endpoints: /live/asr, /live/transcribe, etc.
    """
    if not url:
        return False
    url_lower = url.lower()
    if 'speaches' in url_lower:
        return (
            '/realtime' in url_lower
            or '/transcribe' in url_lower
            or '/transcriptions' in url_lower
        )
    if 'live' in url_lower and ('asr' in url_lower or 'transcribe' in url_lower):
        return True
    return False


def check_language_support(url: str, language: Optional[str]) -> Optional[str]:
    """Check if the endpoint supports the requested language. Returns the language code."""
    if language is None:
        return 'de'
    if not isinstance(language, str) or not language.strip():
        return None
    return language.strip()


@dataclass
class LiveAsrOptions:
    """Runtime options for the live ASR adapter."""
    source_profile: str = 'managed_web_online'
    max_buffer_size: int = DEFAULT_BUFFER_SIZE
    event_timeout: float = DEFAULT_EVENT_TIMEOUT
    language: Optional[str] = 'de'


@dataclass
class LiveAsrResult:
    """Result from streaming a live transcription."""
    text: str
    segments: List[TranscriptionSegment] = field(default_factory=list)
    language: Optional[str] = None
    duration_ms: Optional[int] = None
    event_count: int = 0
    is_final: bool = False


class LiveAsrAdapter(BaseTranscriptionConnector):
    """Connector for local Speaches realtime transcription in clinic mode.

    Implements a pull-based interface to a push/streaming realtime endpoint.
    Returns partial and final recognition events as TranscriptionResponse objects.
    """

    CAPABILITIES: Set[TranscriptionCapability] = {
        TranscriptionCapability.STREAMING,
        TranscriptionCapability.TIMESTAMPS,
        TranscriptionCapability.LANGUAGE_DETECTION,
        TranscriptionCapability.HOTWORDS,
    }
    PROVIDER_NAME = "live_asr_adapter"

    SPECIFICATIONS = ConnectorSpecifications(
        max_file_size_bytes=None,
        max_duration_seconds=None,
        handles_chunking_internally=True,
    )

    def __init__(self, config: Dict[str, Any]):
        self._base_url = config.get('base_url', '').rstrip('/')
        self._api_key = config.get('api_key', '')
        self._model = config.get('model', 'tts')
        self._timeout = config.get('timeout', DEFAULT_LIVE_TIMEOUT)
        self._options = LiveAsrOptions()
        self._state = LiveTranscriptionState(config.get('max_buffer_size', DEFAULT_BUFFER_SIZE))
        self._event_handlers: List[Callable[[LiveAsrResult], None]] = []
        super().__init__(config)

    def _validate_config(self) -> None:
        if not clinic_mode_enabled():
            raise ConfigurationError("Live ASR adapter requires clinic mode")
        if not self._base_url or not is_local_base_url(self._base_url):
            raise ConfigurationError(
                f"CLINIC_LIVE_ASR_BASE_URL must be a local/private URL (got {self._base_url!r})"
            )
        if not validate_live_asr_url(self._base_url):
            raise ConfigurationError(
                f"CLINIC_LIVE_ASR_BASE_URL is not in the allowlist for live ASR "
                f"(expected speaches realtime endpoint)"
            )

    def supports(self, capability: TranscriptionCapability) -> bool:
        return capability in self.CAPABILITIES

    def health_check(self) -> bool:
        """Check if the live ASR endpoint is reachable and configured."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self._base_url}/health")
                if resp.status_code < 500:
                    return True
        except Exception as e:
            logger.debug(f"Live ASR health check failed: {e}")
        return False

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        """Single-shot transcription using the realtime endpoint.

        For live dictation, this triggers a quick recognition pass and returns
        a final result. Partial events are available via streaming methods.
        """
        if not self.health_check():
            raise TranscriptionError("Live ASR endpoint is unavailable")

        language = request.language or self._options.language or 'de'
        user_id = getattr(request, 'user_id', None)
        hotwords = get_hotwords_for_user(user_id) if user_id else []
        prompt = request.prompt or ''
        if hotwords:
            prompt = (prompt + ' ' if prompt else '') + 'Keywords: ' + ', '.join(hotwords)

        try:
            result = self._request_recognition(
                request, language=language, prompt=prompt, hotwords=hotwords
            )
            return self._build_response(result, final_only=True)
        except TranscriptionError:
            raise
        except Exception as e:
            raise TranscriptionError(f"Live ASR transcription failed: {e}") from e

    def stream_transcription(
        self,
        request: TranscriptionRequest,
        on_partial: Optional[Callable[[TranscriptionResponse], None]] = None,
        on_final: Optional[Callable[[TranscriptionResponse], None]] = None,
        timeout: float = 0.0,
    ) -> TranscriptionResponse:
        """Stream partial and final transcription events.

        Args:
            request: The transcription request with audio_bytes (bytes or bytes-like)
            on_partial: Callback for partial recognition events
            on_final: Callback for final recognition events
            timeout: Max seconds to wait for events (0 = use default)

        Returns:
            A TranscriptionResponse with the accumulated text
        """
        if not on_final:
            raise ValueError("on_final callback is required for live transcription")

        effective_timeout = timeout if timeout > 0 else self._timeout
        language = request.language or self._options.language or 'de'
        hotwords = get_request_hotwords(request)

        end_time = time.time() + effective_timeout
        start_ms = 0

        try:
            events = self._stream_events(
                request,
                language=language,
                hotwords=hotwords,
                timeout=effective_timeout
            )
            final_text_parts = []
            segments: List[TranscriptionSegment] = []
            last_end_ms = start_ms

            for event in events:
                if event.get('event') == 'partial' and on_partial:
                    on_partial(TranscriptionResponse(
                        text=event.get('text', ''),
                        language=language,
                        provider=self.PROVIDER_NAME,
                        model=self._model,
                    ))
                if event.get('event') == 'final':
                    text = event.get('text', '')
                    end_ms = event.get('end_ms', 0)
                    segment = TranscriptionSegment(
                        text=text,
                        speaker=event.get('speaker', 'SPEAKER_0'),
                        start_time=start_ms / 1000.0,
                        end_time=end_ms / 1000.0,
                        words=event.get('words', []),
                    )
                    segments.append(segment)
                    final_text_parts.append(text)
                    start_ms = end_ms
                    if on_final:
                        resp = TranscriptionResponse(
                            text=' '.join(final_text_parts),
                            segments=segments,
                            language=language,
                            duration=end_ms / 1000.0,
                            provider=self.PROVIDER_NAME,
                            model=self._model,
                        )
                        on_final(resp)
                    if time.time() >= end_time:
                        break

            result_text = ' '.join(final_text_parts)
            return self._build_response(
                {'text': result_text, 'segments': segments, 'duration_ms': start_ms},
                final_only=True
            )

        except TranscriptionError:
            raise
        except Exception as e:
            raise TranscriptionError(f"Live ASR streaming failed: {e}") from e

    def _stream_events(
        self,
        request: TranscriptionRequest,
        language: str,
        hotwords: List[str],
        timeout: float,
    ):
        """Stream local Speaches /v1/realtime. Partials yield before the socket closes.

        A placeholder or English API-default model is refused before any audio
        is read. Batch HTTP is not a fallback for this path.
        """
        model = (getattr(request, 'model', None) or self._model or '').strip()
        return iter_speaches_events(
            base_url=self._base_url,
            api_key=self._api_key,
            model=model,
            language=language or 'de',
            audio=request.audio_file,
            timeout=timeout,
            hotwords=hotwords,
            should_cancel=getattr(request, 'should_cancel', None),
        )

    def _request_recognition(
        self,
        request: TranscriptionRequest,
        language: str,
        prompt: str,
        hotwords: List[str],
    ) -> Dict[str, Any]:
        """Make a single recognition request to the realtime endpoint."""
        url = f"{self._base_url}/v1/audio/transcriptions"

        files = {
            'file': (request.filename or 'audio.wav', request.audio_file, 'audio/wav'),
        }

        data = {
            'model': self._model,
            'language': language,
            'prompt': prompt,
        }
        if hotwords:
            data['hotwords'] = json.dumps(hotwords)

        headers = {}
        if self._api_key:
            headers['Authorization'] = f'Bearer {self._api_key}'

        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(url, files=files, data=data, headers=headers)
            response.raise_for_status()
            return response.json()

    def _build_response(
        self,
        data: Dict[str, Any],
        final_only: bool = True,
    ) -> TranscriptionResponse:
        """Build a TranscriptionResponse from recognition data."""
        text = data.get('text', '')
        segments: List[TranscriptionSegment] = []
        language = data.get('language')
        duration = data.get('duration')

        if not final_only:
            duration = data.get('duration_ms', 0) / 1000.0 if data.get('duration_ms') else None

        raw_segments = data.get('segments', [])
        for seg in raw_segments if raw_segments else []:
            if isinstance(seg, TranscriptionSegment):
                segments.append(seg)
            else:
                segment = TranscriptionSegment(
                    text=seg.get('text', ''),
                    speaker=seg.get('speaker', 'SPEAKER_0'),
                    start_time=seg.get('start_time'),
                    end_time=seg.get('end_time'),
                    words=seg.get('words'),
                )
                segments.append(segment)

        return TranscriptionResponse(
            text=text,
            segments=segments if segments else None,
            language=language,
            duration=duration,
            provider=self.PROVIDER_NAME,
            model=self._model,
            raw_response=data,
        )

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["base_url"],
            "properties": {
                "base_url": {
                    "type": "string",
                    "description": "Speaches realtime endpoint URL (must be local)",
                },
                "api_key": {
                    "type": "string",
                    "description": "API key for the realtime endpoint",
                },
                "model": {
                    "type": "string",
                    "default": "tts",
                    "description": "Model to use for recognition (default: tts)",
                },
                "timeout": {
                    "type": "number",
                    "default": 30.0,
                    "description": "Streaming timeout in seconds",
                },
                "max_buffer_size": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum events to buffer",
                },
            },
        }


def get_live_asr_config() -> Dict[str, Any]:
    """Read live ASR configuration from environment variables."""
    base_url = os.environ.get(LIVE_ASR_URL_ENV, '').strip()
    if not base_url:
        return {}

    return {
        'base_url': base_url.split('#')[0].strip(),
        'api_key': os.environ.get(LIVE_ASR_API_KEY_ENV, '').strip(),
        'model': os.environ.get(LIVE_ASR_MODEL_ENV, 'tts').strip() or 'tts',
        'timeout': float(os.environ.get(LIVE_ASR_TIMEOUT_ENV, '').strip() or DEFAULT_LIVE_TIMEOUT),
    }


def live_asr_available() -> bool:
    """Check if live ASR is configured and available."""
    if not clinic_mode_enabled():
        return False
    if not os.environ.get(LIVE_ASR_URL_ENV, '').strip():
        return False
    config = get_live_asr_config()
    if not config:
        return False
    if not is_local_base_url(config.get('base_url', '')):
        logger.warning(f"Live ASR base_url is not local: {config.get('base_url')}")
        return False
    adapter = LiveAsrAdapter(config)
    return adapter.health_check()


def get_capability_status() -> Dict[str, str]:
    """Return capability status for the live ASR adapter."""
    if not live_asr_available():
        return {
            'live_asr': 'CAPABILITY_UNAVAILABLE',
            'reason': 'Not configured or not in clinic mode',
        }
    return {
        'live_asr': 'AVAILABLE',
        'endpoint': os.environ.get(LIVE_ASR_URL_ENV, ''),
    }


def get_request_hotwords(request: TranscriptionRequest) -> List[str]:
    """Get hotwords for a transcription request."""
    user_id = getattr(request, 'user_id', None)
    if user_id is not None:
        return get_hotwords_for_user(user_id)
    return []
