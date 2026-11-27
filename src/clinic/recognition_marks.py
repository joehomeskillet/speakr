"""Playback phrases and uncertainty derived from measured ASR values."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite


class RecognitionMarksError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Mark:
    start_ms: int
    end_ms: int
    kind: str
    source: str
    value: float


@dataclass(frozen=True)
class MarkReport:
    marks: tuple[Mark, ...]
    unknown_count: int
    confidence_available: bool


def _number(value):
    return type(value) is int or (type(value) is float and isfinite(value))


def _span(item):
    if not isinstance(item, Mapping):
        return None
    if "start_ms" in item or "end_ms" in item:
        start, end = item.get("start_ms"), item.get("end_ms")
        if type(start) is int and type(end) is int and 0 <= start < end:
            return start, end
        return None
    start = item.get("start_time", item.get("start"))
    end = item.get("end_time", item.get("end"))
    if _number(start) and _number(end) and 0 <= start < end:
        start, end = start * 1000, end * 1000
        if _number(start) and _number(end) and round(start) < round(end):
            return round(start), round(end)
    return None


def phrase_at(segments, position_ms):
    """Return the earliest segment covering the position in [start, end)."""
    if type(position_ms) is not int or position_ms < 0:
        raise RecognitionMarksError("invalid_position_ms")
    match = None
    match_start = position_ms + 1
    for segment in segments:
        span = _span(segment)
        if span and span[0] <= position_ms < span[1] and span[0] < match_start:
            match, match_start = segment, span[0]
    return match


def recognition_marks(segments, *, logprob_floor=-1.0, word_prob_floor=0.5, no_speech_ceiling=0.6):
    """Report measured threshold crossings and count missing or unusable metrics."""
    if not all(_number(x) for x in (logprob_floor, word_prob_floor, no_speech_ceiling)):
        raise RecognitionMarksError("invalid_threshold")
    if not (0 <= word_prob_floor <= 1 and 0 <= no_speech_ceiling <= 1):
        raise RecognitionMarksError("invalid_threshold_range")

    marks = {}
    unknown_count = known_count = 0

    def consider(item, metric, threshold, kind, source):
        nonlocal unknown_count, known_count
        span = _span(item)
        value = item.get(metric) if isinstance(item, Mapping) else None
        if not span or not _number(value) or (metric != "avg_logprob" and not 0 <= value <= 1):
            unknown_count += 1
            return
        known_count += 1
        crosses = value > threshold if metric == "no_speech_prob" else value < threshold
        if crosses:
            marks[Mark(*span, kind, source, value)] = None

    for segment in segments:
        consider(segment, "avg_logprob", logprob_floor, "low_confidence", "segment")
        consider(segment, "no_speech_prob", no_speech_ceiling, "no_speech", "segment")
        words = segment.get("words") if isinstance(segment, Mapping) else None
        if isinstance(words, list):
            for word in words:
                consider(word, "probability", word_prob_floor, "low_confidence", "word")

    return MarkReport(tuple(sorted(marks, key=lambda mark: mark.start_ms)), unknown_count, bool(known_count))
