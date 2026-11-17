"""
Local stub ASR connector for the clinic fork (WP08).

This stub is NOT an ASR quality proof. It opens no network connection and
recognises no speech. It returns fixed, synthetic German dictation sentences
whose timestamps are split across the audio duration read from the WAV header.
Every result is marked as synthetic in `raw_response`. It exists to exercise the
transcription pipeline end to end offline. It must never serve production
transcripts, and it refuses to run when CLINIC_PROFILE=production.

Select it only explicitly: TRANSCRIPTION_CONNECTOR=clinic_stub.
"""

import io
import logging
import os
import wave
from typing import Any, Dict, List, Set

from ..base import (
    BaseTranscriptionConnector,
    TranscriptionCapability,
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptionSegment,
    ConnectorSpecifications,
)
from ..exceptions import TranscriptionError, ConfigurationError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "clinic_stub"
STUB_MODEL = "clinic-stub-v1"
PRODUCTION_PROFILE = "production"

# Fixed synthetic dictation sentences. Output depends only on their order and the audio duration.
SYNTHETIC_SENTENCES: List[str] = [
    "Synthetischer Diktattext eins.",
    "Synthetischer Diktattext zwei.",
    "Synthetischer Diktattext drei.",
    "Synthetischer Diktattext vier.",
]


def _refuse_in_production() -> None:
    """Raise ConfigurationError when the clinic production profile is active."""
    if os.environ.get("CLINIC_PROFILE", "").strip().lower() == PRODUCTION_PROFILE:
        raise ConfigurationError(
            "clinic_stub is a synthetic test connector and refuses to run with CLINIC_PROFILE=production."
        )


def _wav_duration_seconds(audio: bytes) -> float:
    """Return the duration of a PCM WAV file from its header, without decoding samples."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
    except (wave.Error, EOFError) as e:
        raise TranscriptionError(f"clinic_stub accepts only PCM WAV audio: {e}") from e
    if rate <= 0:
        raise TranscriptionError("clinic_stub: WAV header has a non-positive sample rate")
    return frames / rate


class ClinicStubTranscriptionConnector(BaseTranscriptionConnector):
    """Synthetic, offline stub connector. Not an ASR quality proof."""

    CAPABILITIES: Set[TranscriptionCapability] = {
        TranscriptionCapability.TIMESTAMPS,
    }
    PROVIDER_NAME = PROVIDER_NAME

    SPECIFICATIONS = ConnectorSpecifications(
        max_file_size_bytes=None,
        max_duration_seconds=None,
        handles_chunking_internally=True,
    )

    def __init__(self, config: Dict[str, Any]):
        self.model = STUB_MODEL
        super().__init__(config)

    def _validate_config(self) -> None:
        _refuse_in_production()

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        _refuse_in_production()
        audio = request.audio_file.read()
        duration = _wav_duration_seconds(audio)

        count = len(SYNTHETIC_SENTENCES)
        segments: List[TranscriptionSegment] = []
        for index, sentence in enumerate(SYNTHETIC_SENTENCES):
            start = round(duration * index / count, 3)
            end = round(duration * (index + 1) / count, 3)
            segments.append(TranscriptionSegment(text=sentence, start_time=start, end_time=end))

        text = " ".join(segment.text for segment in segments)
        logger.info("clinic_stub: returned %d synthetic segments for %.3f s audio", count, duration)

        return TranscriptionResponse(
            text=text,
            segments=segments,
            language="de",
            duration=round(duration, 3),
            provider=self.PROVIDER_NAME,
            model=self.model,
            raw_response={
                "synthetic": True,
                "connector": PROVIDER_NAME,
                "not_asr_quality_proof": True,
                "duration_seconds": round(duration, 3),
            },
        )

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}
