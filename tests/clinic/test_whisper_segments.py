"""
Tests for OpenAI Whisper connector segment extraction and processing.py segment persistence
without diarization (WP segments).
"""

import io
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from src.app import app
from src.database import db
from src.models import Recording, User
from src.services.transcript_segments import canonical_segments, segments_of
from src.services.transcription.base import (
    TranscriptionRequest,
    TranscriptionResponse,
    TranscriptionSegment,
)
from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
from src.tasks import processing as proc


# -----------------------------------------------------------------------------
# 1. OpenAI Whisper Connector Tests
# -----------------------------------------------------------------------------

def _request(text=b"fake-audio-bytes", filename="audio.wav"):
    return TranscriptionRequest(
        audio_file=io.BytesIO(text),
        filename=filename,
        mime_type="audio/wav",
    )


def test_openai_whisper_requests_verbose_json_and_segment_granularity():
    """Connector requests response_format=verbose_json and timestamp_granularities=['segment']."""
    connector = OpenAIWhisperConnector({"api_key": "test-key", "base_url": "http://127.0.0.1:8000/v1"})
    mock_create = MagicMock()
    mock_create.return_value = {"text": "Simple test", "segments": []}
    connector.client.audio.transcriptions.create = mock_create

    req = _request()
    connector.transcribe(req)

    assert mock_create.called
    _, kwargs = mock_create.call_args
    assert kwargs.get("response_format") == "verbose_json"
    assert kwargs.get("timestamp_granularities") == ["segment"]


def test_openai_whisper_parses_verbose_json_segments_dict():
    """Connector parses segments and words from a verbose_json dictionary response."""
    connector = OpenAIWhisperConnector({"api_key": "test-key", "base_url": "http://127.0.0.1:8000/v1"})
    mock_response = {
        "text": "Hello world. Second sentence.",
        "duration": 5.4,
        "language": "en",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world.",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 1.0},
                    {"word": "world.", "start": 1.1, "end": 2.5},
                ],
            },
            {
                "id": 1,
                "start": 2.6,
                "end": 5.4,
                "text": "Second sentence.",
                "words": [
                    {"word": "Second", "start": 2.6, "end": 3.8},
                    {"word": "sentence.", "start": 3.9, "end": 5.4},
                ],
            },
        ],
    }
    connector.client.audio.transcriptions.create = MagicMock(return_value=mock_response)

    res = connector.transcribe(_request())

    assert res.text == "Hello world. Second sentence."
    assert res.duration == 5.4
    assert res.language == "en"
    assert res.segments is not None
    assert len(res.segments) == 2

    seg1, seg2 = res.segments
    assert seg1.text == "Hello world."
    assert seg1.start_time == 0.0
    assert seg1.end_time == 2.5
    assert seg1.speaker is None
    assert seg1.words == [
        {"word": "Hello", "start": 0.0, "end": 1.0},
        {"word": "world.", "start": 1.1, "end": 2.5},
    ]

    assert seg2.text == "Second sentence."
    assert seg2.start_time == 2.6
    assert seg2.end_time == 5.4
    assert seg2.speaker is None

    # Crucial: has_diarization must be False because no speakers were assigned
    assert res.has_diarization() is False


def test_openai_whisper_parses_verbose_json_segments_objects():
    """Connector parses segments from an SDK object response (like TranscriptionVerbose)."""
    connector = OpenAIWhisperConnector({"api_key": "test-key", "base_url": "http://127.0.0.1:8000/v1"})

    class MockWord:
        def __init__(self, word, start, end):
            self.word = word
            self.start = start
            self.end = end

    class MockSegment:
        def __init__(self, text, start, end, words=None):
            self.text = text
            self.start = start
            self.end = end
            self.words = words

    class MockVerboseResponse:
        def __init__(self):
            self.text = "Guten Tag."
            self.duration = 2.0
            self.language = "german"
            self.segments = [
                MockSegment("Guten Tag.", 0.2, 1.8, [MockWord("Guten", 0.2, 0.9), MockWord("Tag.", 1.0, 1.8)])
            ]

    connector.client.audio.transcriptions.create = MagicMock(return_value=MockVerboseResponse())

    res = connector.transcribe(_request())
    assert res.text == "Guten Tag."
    assert res.duration == 2.0
    assert res.language == "german"
    assert len(res.segments) == 1
    assert res.segments[0].text == "Guten Tag."
    assert res.segments[0].start_time == 0.2
    assert res.segments[0].end_time == 1.8
    assert res.segments[0].words == [
        {"word": "Guten", "start": 0.2, "end": 0.9},
        {"word": "Tag.", "start": 1.0, "end": 1.8},
    ]
    assert res.has_diarization() is False


def test_openai_whisper_fallback_to_text_only():
    """Servers returning only text (no segments field) do NOT fail and fall back to plain text."""
    connector = OpenAIWhisperConnector({"api_key": "test-key", "base_url": "http://127.0.0.1:8000/v1"})

    # Case 1: dict with only text
    connector.client.audio.transcriptions.create = MagicMock(return_value={"text": "Just plain text back."})
    res = connector.transcribe(_request())
    assert res.text == "Just plain text back."
    assert res.segments is None
    assert res.has_diarization() is False

    # Case 2: Object with text attribute and None segments
    class MockTextResponse:
        def __init__(self):
            self.text = "Another plain text."
            self.segments = None

    connector.client.audio.transcriptions.create = MagicMock(return_value=MockTextResponse())
    res2 = connector.transcribe(_request())
    assert res2.text == "Another plain text."
    assert res2.segments is None
    assert res2.has_diarization() is False

    # Case 3: Plain string return
    connector.client.audio.transcriptions.create = MagicMock(return_value="Plain string return.")
    res3 = connector.transcribe(_request())
    assert res3.text == "Plain string return."
    assert res3.segments is None
    assert res3.has_diarization() is False


# -----------------------------------------------------------------------------
# 2. processing.py Persistence Tests
# -----------------------------------------------------------------------------

def _make_user(name):
    pid = os.getpid()
    # user.username is VARCHAR(20): PostgreSQL enforces it, SQLite does not
    user = User(
        username=f"{name}_{os.urandom(3).hex()}",
        email=f"{name}_{pid}_{time.time()}@example.com",
        password="test-password-1234",
    )
    db.session.add(user)
    db.session.commit()
    return user


def _make_recording(user_id, transcription=None, status="PENDING"):
    rec = Recording(
        user_id=user_id,
        title="Test Recording",
        transcription=transcription,
        status=status,
        audio_path="/fake/path.mp3",
    )
    db.session.add(rec)
    db.session.commit()
    return rec


def _mock_pipeline(connector, rid, tmp_path):
    conv_result = MagicMock()
    conv_result.was_converted = False

    with patch("src.services.transcription.get_connector", return_value=connector), \
         patch.object(proc, "is_video_file", return_value=False), \
         patch.object(proc, "convert_if_needed", return_value=conv_result), \
         patch.object(proc, "client", MagicMock()), \
         patch.object(proc, "ENABLE_INQUIRE_MODE", False), \
         patch.object(proc, "generate_title_task"), \
         patch.object(proc, "generate_summary_only_task"), \
         patch.object(proc, "chunking_service") as mock_chunk_svc:
        mock_chunk_svc.needs_chunking.return_value = False
        mock_chunk_svc.get_audio_duration.return_value = 60.0
        proc.transcribe_with_connector(
            app.app_context(), rid, tmp_path, "test.mp3", time.time(),
            mime_type="audio/mpeg",
        )


def test_processing_persists_segments_without_diarization(tmp_path):
    """processing.py persists segments as JSON even when has_diarization() is False."""
    with app.app_context():
        user = _make_user("seg_no_diar")
        rec = _make_recording(user.id)
        rid = rec.id

        audio_file = tmp_path / f"test_{rid}.mp3"
        audio_file.write_bytes(b"\x00" * 512)

        segments = [
            TranscriptionSegment(text="First sentence.", start_time=0.0, end_time=2.0),
            TranscriptionSegment(text="Second sentence.", start_time=2.1, end_time=4.5),
        ]
        response = TranscriptionResponse(
            text="First sentence. Second sentence.",
            segments=segments,
            provider="openai_whisper",
            model="whisper-1",
        )
        assert response.has_diarization() is False

        connector = MagicMock()
        connector.PROVIDER_NAME = "openai_whisper"
        connector.supports_diarization = False
        connector.default_diarize = False
        connector.specifications = MagicMock(
            max_duration_seconds=1800,
            handles_chunking_internally=False,
            recommended_chunk_seconds=600,
            unsupported_codecs=[],
        )
        connector.transcribe.return_value = response

        _mock_pipeline(connector, rid, str(audio_file))

        db.session.expire_all()
        stored = db.session.get(Recording, rid)

        # Must be stored as JSON array of segments!
        parsed = json.loads(stored.transcription)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["sentence"] == "First sentence."
        assert parsed[0]["start_time"] == 0.0
        assert parsed[0]["end_time"] == 2.0
        assert parsed[1]["sentence"] == "Second sentence."
        assert parsed[1]["start_time"] == 2.1
        assert parsed[1]["end_time"] == 4.5

        # Frontend editor reader segments_of parses it into segments
        frontend_segments = segments_of(stored.transcription)
        assert len(frontend_segments) == 2
        assert frontend_segments[0]["start_time"] == 0.0
        assert frontend_segments[0]["end_time"] == 2.0
        assert frontend_segments[0]["text"] == "First sentence."


def test_processing_persists_segments_with_diarization_unchanged(tmp_path):
    """Existing diarization behaviour (speakers + segments) remains unchanged."""
    with app.app_context():
        user = _make_user("seg_diar")
        rec = _make_recording(user.id)
        rid = rec.id

        audio_file = tmp_path / f"test_{rid}.mp3"
        audio_file.write_bytes(b"\x00" * 512)

        segments = [
            TranscriptionSegment(text="Hello from Alice.", speaker="SPEAKER_00", start_time=0.0, end_time=1.5),
            TranscriptionSegment(text="Hello from Bob.", speaker="SPEAKER_01", start_time=1.6, end_time=3.0),
        ]
        response = TranscriptionResponse(
            text="[SPEAKER_00]: Hello from Alice.\n[SPEAKER_01]: Hello from Bob.",
            segments=segments,
            speakers=["SPEAKER_00", "SPEAKER_01"],
            provider="asr_endpoint",
            model="whisperx",
        )
        assert response.has_diarization() is True

        connector = MagicMock()
        connector.PROVIDER_NAME = "asr_endpoint"
        connector.supports_diarization = True
        connector.default_diarize = True
        connector.specifications = MagicMock(
            max_duration_seconds=1800,
            handles_chunking_internally=True,
            recommended_chunk_seconds=600,
            unsupported_codecs=[],
        )
        connector.transcribe.return_value = response

        _mock_pipeline(connector, rid, str(audio_file))

        db.session.expire_all()
        stored = db.session.get(Recording, rid)

        parsed = json.loads(stored.transcription)
        assert len(parsed) == 2
        assert parsed[0]["speaker"] == "SPEAKER_00"
        assert parsed[0]["sentence"] == "Hello from Alice."
        assert parsed[1]["speaker"] == "SPEAKER_01"
        assert parsed[1]["sentence"] == "Hello from Bob."


def test_processing_persists_plain_text_when_no_segments(tmp_path):
    """When connector returns no segments, processing.py stores plain text."""
    with app.app_context():
        user = _make_user("no_seg")
        rec = _make_recording(user.id)
        rid = rec.id

        audio_file = tmp_path / f"test_{rid}.mp3"
        audio_file.write_bytes(b"\x00" * 512)

        response = TranscriptionResponse(
            text="Plain text with no segments.",
            segments=None,
            provider="openai_whisper",
            model="whisper-1",
        )

        connector = MagicMock()
        connector.PROVIDER_NAME = "openai_whisper"
        connector.supports_diarization = False
        connector.default_diarize = False
        connector.specifications = MagicMock(
            max_duration_seconds=1800,
            handles_chunking_internally=False,
            recommended_chunk_seconds=600,
            unsupported_codecs=[],
        )
        connector.transcribe.return_value = response

        _mock_pipeline(connector, rid, str(audio_file))

        db.session.expire_all()
        stored = db.session.get(Recording, rid)
        assert stored.transcription == "Plain text with no segments."
        assert segments_of(stored.transcription) is None
