"""
OpenAI Whisper API connector (whisper-1 model).

This is the legacy Whisper API connector that supports the whisper-1 model.
It returns plain text transcriptions without speaker diarization.
"""

import logging
import os
import httpx
from openai import OpenAI
from typing import Dict, Any, Set

from ..base import (
    BaseTranscriptionConnector,
    TranscriptionCapability,
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptionSegment,
    ConnectorSpecifications,
)
from ..exceptions import TranscriptionError, ConfigurationError

from src.utils.error_text import describe_body, log_failure
logger = logging.getLogger(__name__)


class OpenAIWhisperConnector(BaseTranscriptionConnector):
    """Connector for OpenAI Whisper API (whisper-1 model)."""

    CAPABILITIES: Set[TranscriptionCapability] = {
        TranscriptionCapability.CHUNKING,
        TranscriptionCapability.TIMESTAMPS,
        TranscriptionCapability.LANGUAGE_DETECTION,
        TranscriptionCapability.HOTWORDS,
        TranscriptionCapability.INITIAL_PROMPT,
    }
    PROVIDER_NAME = "openai_whisper"

    # OpenAI Whisper has a 25MB file limit and doesn't handle chunking internally
    # Supported formats: mp3, mp4, mpeg, mpga, m4a, wav, webm, flac, ogg, oga
    # NOT supported: opus (used by WhatsApp voice notes, Discord)
    SPECIFICATIONS = ConnectorSpecifications(
        max_file_size_bytes=25 * 1024 * 1024,  # 25MB
        handles_chunking_internally=False,
        recommended_chunk_seconds=600,  # 10 minutes
        unsupported_codecs=frozenset({'opus'}),  # OpenAI API doesn't support opus
    )

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Whisper connector.

        Args:
            config: Configuration dict with keys:
                - api_key: OpenAI API key (required)
                - base_url: API base URL (optional)
                - model: Model name (default: whisper-1)
                - http_client: Optional httpx.Client instance
        """
        super().__init__(config)

        # Set up HTTP client with custom headers
        http_client = config.get('http_client')
        if not http_client:
            app_headers = {
                "HTTP-Referer": "https://github.com/murtaza-nasir/speakr",
                "X-Title": "Speakr - AI Audio Transcription",
                "User-Agent": "Speakr/1.0 (https://github.com/murtaza-nasir/speakr)"
            }
            http_client = httpx.Client(verify=True, headers=app_headers)

        self.client = OpenAI(
            api_key=config['api_key'],
            base_url=config.get('base_url') or None,
            http_client=http_client
        )
        self.model = config.get('model', 'whisper-1')

    def _validate_config(self) -> None:
        """Validate required configuration."""
        from ..local_only import validate_clinic_base_url
        validate_clinic_base_url(self.config.get('base_url'))

        if not self.config.get('api_key'):
            raise ConfigurationError("api_key is required for OpenAI Whisper connector")

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        """
        Transcribe audio using OpenAI Whisper API.

        Args:
            request: Standardized transcription request

        Returns:
            TranscriptionResponse with segments and plain text (no diarization)
        """
        try:
            effective_model = self._effective_model(request)
            params = {
                "model": effective_model,
                "file": request.audio_file,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }

            if request.language:
                params["language"] = request.language
                logger.info(f"Using transcription language: {request.language}")

            # Combine initial prompt and hotwords into a single prompt
            # OpenAI Whisper uses prompt for both steering and vocabulary hints
            prompt_parts = []
            if request.prompt:
                prompt_parts.append(request.prompt)
            if request.hotwords:
                prompt_parts.append(request.hotwords)
            if prompt_parts:
                params["prompt"] = ". ".join(prompt_parts)

            if request.temperature is not None:
                params["temperature"] = request.temperature

            logger.info(f"Sending request to Whisper API with model: {effective_model}")
            transcript = self.client.audio.transcriptions.create(**params)

            # Robust parsing of response: handle TranscriptionVerbose, Transcription, dict, or str
            text = ""
            raw_segments = None
            detected_language = None
            duration = None

            if isinstance(transcript, str):
                text = transcript
            elif isinstance(transcript, dict):
                text = str(transcript.get("text") or "")
                raw_segments = transcript.get("segments")
                detected_language = transcript.get("language")
                duration = transcript.get("duration")
            elif hasattr(transcript, "text"):
                text = str(getattr(transcript, "text", "") or "")
                raw_segments = getattr(transcript, "segments", None)
                detected_language = getattr(transcript, "language", None)
                duration = getattr(transcript, "duration", None)
            else:
                text = ""
                raw_segments = None
                detected_language = None
                duration = None

            parsed_segments = []
            if raw_segments and isinstance(raw_segments, list):
                for seg in raw_segments:
                    if isinstance(seg, dict):
                        seg_text = seg.get("text")
                        if seg_text is None:
                            seg_text = seg.get("sentence", "")
                        start = seg.get("start")
                        if start is None:
                            start = seg.get("start_time")
                        end = seg.get("end")
                        if end is None:
                            end = seg.get("end_time")
                        words = seg.get("words")
                    else:
                        seg_text = getattr(seg, "text", getattr(seg, "sentence", ""))
                        start = getattr(seg, "start", getattr(seg, "start_time", None))
                        end = getattr(seg, "end", getattr(seg, "end_time", None))
                        words = getattr(seg, "words", None)

                    try:
                        start_time = float(start) if start is not None else None
                    except (ValueError, TypeError):
                        start_time = None
                    try:
                        end_time = float(end) if end is not None else None
                    except (ValueError, TypeError):
                        end_time = None

                    parsed_words = None
                    if isinstance(words, list):
                        parsed_words = []
                        for w in words:
                            if isinstance(w, dict):
                                parsed_words.append(w)
                            elif hasattr(w, "model_dump"):
                                try:
                                    parsed_words.append(w.model_dump())
                                except Exception:
                                    parsed_words.append(vars(w))
                            else:
                                parsed_words.append({
                                    "word": getattr(w, "word", ""),
                                    "start": getattr(w, "start", None),
                                    "end": getattr(w, "end", None),
                                })

                    parsed_segments.append(
                        TranscriptionSegment(
                            text=str(seg_text or ""),
                            start_time=start_time,
                            end_time=end_time,
                            words=parsed_words,
                        )
                    )

            if not text and parsed_segments:
                text = " ".join(s.text for s in parsed_segments).strip()

            try:
                duration_sec = float(duration) if duration is not None else None
            except (ValueError, TypeError):
                duration_sec = None

            raw_response = None
            if hasattr(transcript, "model_dump"):
                try:
                    raw_response = transcript.model_dump()
                except Exception:
                    raw_response = None
            elif isinstance(transcript, dict):
                raw_response = transcript

            return TranscriptionResponse(
                text=text,
                segments=parsed_segments if parsed_segments else None,
                language=str(detected_language) if detected_language else None,
                duration=duration_sec,
                provider=self.PROVIDER_NAME,
                model=effective_model,
                raw_response=raw_response,
            )

        except Exception as e:
            error_msg = str(e)
            log_failure(logger, logging.ERROR, "Whisper transcription failed", e)
            raise TranscriptionError(f"Whisper transcription failed: {error_msg}") from e

    def list_models(self):
        """Return audio-relevant models from OpenAI's /v1/models.

        Filters the full catalog to entries whose id contains 'whisper'.
        """
        try:
            resp = self.client.models.list()
            audio = []
            for m in resp.data:
                mid = getattr(m, 'id', '')
                if mid and 'whisper' in mid.lower():
                    audio.append({
                        'id': mid,
                        'label': mid,
                        'owned_by': getattr(m, 'owned_by', 'openai'),
                    })
            return audio
        except Exception as e:
            logger.warning(f"openai_whisper /v1/models probe failed: {e}")
            return []

    def health_check(self) -> bool:
        """Check if the connector is properly configured."""
        return bool(self.config.get('api_key'))

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """Return JSON schema for configuration."""
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": {
                    "type": "string",
                    "description": "OpenAI API key"
                },
                "base_url": {
                    "type": "string",
                    "description": "API base URL (optional, for OpenAI-compatible endpoints)"
                },
                "model": {
                    "type": "string",
                    "default": "whisper-1",
                    "description": "Whisper model to use"
                }
            }
        }
