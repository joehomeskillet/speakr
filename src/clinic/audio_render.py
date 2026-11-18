"""Render clinic edit lists into derived PCM WAV artifacts without writing sources."""
from __future__ import annotations

import hashlib
import io
import wave
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass

from src.clinic.audio_revisions import (
    SOURCE_TYPES, AudioRevisionError, EditDecisionList, compute_edl_hash,
    current_audio_revision, get_revision_edl,
)
from src.clinic.decoder import DEFAULT_MAX_OUTPUT_BYTES
from src.clinic.models import ClinicDictation


@dataclass(frozen=True)
class AudioSource:
    wav: bytes
    # For decoded media, use the decoder's input_sha256; otherwise hash the WAV.
    original_sha256: str | None = None


@dataclass(frozen=True)
class RenderSpan:
    source_type: str
    source_id: str
    source_sha256: str
    source_rate: int
    source_start_sample: int
    source_end_sample: int
    output_start_sample: int
    output_end_sample: int


@dataclass(frozen=True)
class RenderedAudio:
    edl: EditDecisionList
    wav: bytes
    spans: tuple[RenderSpan, ...]


def ms_to_sample(ms: int, sample_rate: int) -> int:
    """Floor each absolute boundary; intervals are half-open at every rate."""
    if type(ms) is not int or ms < 0 or type(sample_rate) is not int or sample_rate <= 0:
        raise AudioRevisionError("invalid sample rate or millisecond boundary")
    return ms * sample_rate // 1000


def render_audio(edl: EditDecisionList, sources: Mapping[tuple[str, str], AudioSource], *,
                 sample_rate: int = 16000) -> RenderedAudio:
    """Render mono PCM16 using sources already authorized for this dictation.

    Map keys are (source_type, source_id). Each slice is converted independently
    to its exact output span using left-edge sample selection (no interpolation
    across edits). No partial artifact is returned on failure.
    """
    total = ms_to_sample(edl.total_duration_ms, sample_rate)
    if sample_rate > 0x7fffffff or 44 + 2 * total > DEFAULT_MAX_OUTPUT_BYTES:
        raise AudioRevisionError("render exceeds WAV limits")
    if edl.content_hash != compute_edl_hash(edl.entries):
        raise AudioRevisionError("edit list hash mismatch")

    spans: list[RenderSpan] = []
    readers: dict[tuple[str, str], tuple[wave.Wave_read, str]] = {}
    cursor = 0
    dest = io.BytesIO()
    try:
        with ExitStack() as stack:
            for entry in edl.entries:
                key = (entry.source_type, entry.source_id)
                if entry.source_type not in SOURCE_TYPES or key not in sources:
                    raise AudioRevisionError("audio source unavailable in this dictation")
                if key not in readers:
                    source = sources[key]
                    reader = stack.enter_context(wave.open(io.BytesIO(source.wav), "rb"))
                    if (reader.getnchannels(), reader.getsampwidth()) != (1, 2):
                        raise AudioRevisionError("audio source must be mono PCM16 WAV")
                    digest = source.original_sha256 or hashlib.sha256(source.wav).hexdigest()
                    readers[key] = reader, digest
                reader, digest = readers[key]
                rate = reader.getframerate()
                start = ms_to_sample(entry.source_start_ms, rate)
                end = ms_to_sample(entry.source_end_ms, rate)
                out_start = ms_to_sample(entry.output_start_ms, sample_rate)
                out_end = ms_to_sample(entry.output_end_ms, sample_rate)
                if not start < end <= reader.getnframes():
                    raise AudioRevisionError("invalid or out-of-bounds source interval")
                if (entry.output_start_ms != cursor or not out_start < out_end <= total
                        or entry.duration_ms != entry.source_end_ms - entry.source_start_ms):
                    raise AudioRevisionError("invalid output interval")
                cursor = entry.output_end_ms
                spans.append(RenderSpan(*key, digest, rate, start, end, out_start, out_end))
            if cursor != edl.total_duration_ms:
                raise AudioRevisionError("edit list does not cover its declared duration")

            last_end: dict[tuple[str, str], int] = {}
            for span in sorted(spans, key=lambda s: s.source_start_sample):
                key = (span.source_type, span.source_id)
                if span.source_start_sample < last_end.get(key, 0):
                    raise AudioRevisionError("overlapping source intervals")
                last_end[key] = span.source_end_sample

            with wave.open(dest, "wb") as output:
                output.setparams((1, 2, sample_rate, total, "NONE", "not compressed"))
                for span in spans:
                    reader, _ = readers[span.source_type, span.source_id]
                    reader.setpos(span.source_start_sample)
                    count = span.source_end_sample - span.source_start_sample
                    pcm = reader.readframes(count)
                    if len(pcm) != 2 * count:
                        raise AudioRevisionError("truncated audio source")
                    width = span.output_end_sample - span.output_start_sample
                    if count != width:
                        converted = bytearray(2 * width)
                        for n in range(width):
                            offset = 2 * (n * count // width)
                            converted[2 * n:2 * n + 2] = pcm[offset:offset + 2]
                        pcm = bytes(converted)
                    output.writeframesraw(pcm)
    except (wave.Error, EOFError) as exc:
        raise AudioRevisionError("invalid PCM WAV") from exc
    return RenderedAudio(edl, dest.getvalue(), tuple(spans))


def render_current_revision(dictation: ClinicDictation, expected_revision_id: str,
                            sources: Mapping[tuple[str, str], AudioSource], *,
                            sample_rate: int = 16000) -> RenderedAudio:
    """Render the expected snapshot for an already authorized dictation."""
    revision = current_audio_revision(dictation)
    if revision is None or revision.id != expected_revision_id:
        raise AudioRevisionError("stale audio revision")
    edl = get_revision_edl(dictation, revision)
    if edl.dictation_id != dictation.id or edl.content_hash != revision.content_hash:
        raise AudioRevisionError("audio revision snapshot mismatch")
    return render_audio(edl, sources, sample_rate=sample_rate)
