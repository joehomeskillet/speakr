"""Playback tempo, seek and rewind arithmetic for clinic dictation.

Pure arithmetic. No Flask, no database, no datetime. All functions are
deterministic and stateless. Input validation is strict: wrong type,
bool where int is meant, NaN or infinity, empty strings, and control
characters raise the module error with a stable code.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

TEMPO_MIN = 0.5
TEMPO_MAX = 2.0
TEMPO_DEFAULT = 1.0
TEMPO_STEP = 0.1


class TempoError(ValueError):
    """The operation was rejected before it changed playback state."""

    def __init__(self, message: str, code: str):
        self.code = code
        super().__init__(message)


class NoAlignment(TempoError):
    """Word-based rewind requires a valid alignment."""

    def __init__(self, message: str = "word alignment required"):
        super().__init__(message, "no_alignment")


def _validate_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TempoError(f"{name} must be a number", "invalid_type")
    if math.isnan(value) or math.isinf(value):
        raise TempoError(f"{name} must be finite", "not_finite")
    return float(value)


def _validate_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TempoError(f"{name} must be an int", "invalid_type")
    return value


def _validate_positive_int(value: int, name: str) -> int:
    value = _validate_int(value, name)
    if value < 0:
        raise TempoError(f"{name} must be non-negative", "negative_value")
    return value


def clamp_tempo(value: float) -> float:
    """Clamp tempo to [0.5, 2.0] and round to 2 decimal places.

    Raises TempoError for bool, NaN, infinity, or non-numeric types.
    """
    value = _validate_float(value, "tempo")
    clamped = max(TEMPO_MIN, min(TEMPO_MAX, value))
    # Round to 2 decimal places to avoid float drift
    return round(clamped + 1e-12, 2)


def step_tempo(current: float, direction: int) -> float:
    """Step tempo by +1 or -1 step of 0.1, clamped to range.

    Ten steps up from 1.0 reach exactly 2.0; ten steps down reach 0.5.
    """
    current = _validate_float(current, "current")
    if direction not in (1, -1):
        raise TempoError("direction must be +1 or -1", "invalid_direction")
    # Use integer arithmetic to avoid float drift: steps of 0.1 = 1/10
    # Convert to tenths, step, then convert back
    tenths = round(current * 10)
    tenths += direction
    tenths = max(round(TEMPO_MIN * 10), min(round(TEMPO_MAX * 10), tenths))
    return round(tenths / 10.0, 2)


def seek(position_ms: int, delta_ms: int, duration_ms: int) -> int:
    """Seek by delta_ms from position_ms, clamped to [0, duration_ms].

    Raises TempoError for negative duration or invalid types.
    """
    position_ms = _validate_int(position_ms, "position_ms")
    delta_ms = _validate_int(delta_ms, "delta_ms")
    duration_ms = _validate_int(duration_ms, "duration_ms")
    if duration_ms < 0:
        raise TempoError("duration_ms must be non-negative", "negative_duration")
    target = position_ms + delta_ms
    if target < 0:
        return 0
    if target > duration_ms:
        return duration_ms
    return target


def jump_back(position_ms: int, seconds: float) -> int:
    """Jump back by seconds from position_ms, clamped at 0."""
    position_ms = _validate_int(position_ms, "position_ms")
    seconds = _validate_float(seconds, "seconds")
    if seconds < 0:
        raise TempoError("seconds must be non-negative", "negative_seconds")
    back_ms = round(seconds * 1000)
    result = position_ms - back_ms
    return max(0, result)


def wall_to_media_ms(wall_ms: float, tempo: float) -> float:
    """Convert wall-clock milliseconds to media milliseconds at given tempo."""
    wall_ms = _validate_float(wall_ms, "wall_ms")
    tempo = _validate_float(tempo, "tempo")
    if tempo <= 0:
        raise TempoError("tempo must be positive", "non_positive_tempo")
    return wall_ms * tempo


def media_to_wall_ms(media_ms: float, tempo: float) -> float:
    """Convert media milliseconds to wall-clock milliseconds at given tempo."""
    media_ms = _validate_float(media_ms, "media_ms")
    tempo = _validate_float(tempo, "tempo")
    if tempo <= 0:
        raise TempoError("tempo must be positive", "non_positive_tempo")
    return media_ms / tempo


def resume_position(saved_ms: int | None, duration_ms: int, back_ms: int = 0) -> int:
    """Resume position: None -> 0, clamped to duration, then subtract back_ms (min 0)."""
    duration_ms = _validate_int(duration_ms, "duration_ms")
    if duration_ms < 0:
        raise TempoError("duration_ms must be non-negative", "negative_duration")
    back_ms = _validate_int(back_ms, "back_ms")
    if back_ms < 0:
        raise TempoError("back_ms must be non-negative", "negative_back_ms")

    if saved_ms is None:
        position = 0
    else:
        saved_ms = _validate_int(saved_ms, "saved_ms")
        position = min(saved_ms, duration_ms)

    result = position - back_ms
    return max(0, result)


def rewind_words(
    position_ms: int, count: int, word_starts_ms: Sequence[int] | None
) -> int:
    """Return the start of the n-th word that starts before position_ms.

    The word containing position_ms counts as the first. When fewer than
    count words exist, returns the first word start. Raises NoAlignment
    when word_starts_ms is None, empty, unsorted, or contains negative values.
    """
    position_ms = _validate_int(position_ms, "position_ms")
    count = _validate_int(count, "count")
    if count <= 0:
        raise TempoError("count must be positive", "non_positive_count")

    if word_starts_ms is None:
        raise NoAlignment()

    starts = list(word_starts_ms)
    if not starts:
        raise NoAlignment()

    # Validate: all non-negative, strictly increasing (sorted, no duplicates)
    prev = -1
    for i, start in enumerate(starts):
        if not isinstance(start, int) or isinstance(start, bool):
            raise NoAlignment()
        if start < 0:
            raise NoAlignment()
        if start <= prev:
            raise NoAlignment()
        prev = start

    # Find words that start at or before position_ms
    # The word containing position_ms counts as the first
    candidates = [s for s in starts if s <= position_ms]
    if not candidates:
        # No word starts at or before position, return first word start
        return starts[0]

    # We want the n-th word from the end of candidates (counting back)
    # count=1 -> last candidate (word containing position)
    # count=2 -> second to last, etc.
    index = len(candidates) - count
    if index < 0:
        return starts[0]
    return candidates[index]