"""Voice activated recording (Vox) detector for clinic dictation.

Decides when a voice activated recording starts and stops so that no word
onset is lost. Mode, threshold and run-on are visible settings.

Public API:
- VoxConfig: frozen dataclass with validated parameters
- VoxError: module-specific exception with stable error code
- plan_segments: offline analysis of a full level sequence
- PreRollBuffer: ring buffer keeping the last pre_roll_ms of audio frames
- VoxDetector: streaming detector with IDLE/RECORDING state

Non-scope: audio I/O, Flask routes, database access, persistence, ASR coupling.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Sequence


class VoxError(ValueError):
    """The Vox configuration or input was rejected before processing."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class VoxConfig:
    """Configuration for voice activated recording.

    All values are validated on construction.

    Attributes:
        threshold_dbfs: Speech starts when level >= threshold_dbfs.
            Must be <= 0.0 (dBFS). Typical -40.0.
        hysteresis_db: Once recording, level must drop below
            threshold_dbfs - hysteresis_db to consider stopping.
            Must be > 0. Typical 6.0.
        pre_roll_ms: Milliseconds of audio to include before the
            detected speech onset. Must be >= 0. Typical 500.
        post_roll_ms: Milliseconds to keep recording after level
            drops below the continue threshold. Must be >= 0.
            Typical 1500.
        frame_ms: Duration of one analysis frame in milliseconds.
            Must be > 0. Typical 20.
        min_speech_ms: Minimum duration of speech above threshold
            to trigger a start. Must be >= frame_ms. Typical 100.
    """

    threshold_dbfs: float = -40.0
    hysteresis_db: float = 6.0
    pre_roll_ms: int = 500
    post_roll_ms: int = 1500
    frame_ms: int = 20
    min_speech_ms: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.threshold_dbfs, (int, float)) or isinstance(self.threshold_dbfs, bool):
            raise VoxError("invalid_type", "threshold_dbfs must be a number")
        if self.threshold_dbfs != self.threshold_dbfs or abs(self.threshold_dbfs) == float("inf"):  # NaN or inf
            raise VoxError("invalid_value", "threshold_dbfs must be finite")
        if self.threshold_dbfs > 0.0:
            raise VoxError("threshold_positive", "threshold_dbfs must be <= 0.0 dBFS")
        if not isinstance(self.hysteresis_db, (int, float)) or isinstance(self.hysteresis_db, bool):
            raise VoxError("invalid_type", "hysteresis_db must be a number")
        if self.hysteresis_db <= 0.0:
            raise VoxError("hysteresis_non_positive", "hysteresis_db must be > 0")
        if not isinstance(self.pre_roll_ms, int) or isinstance(self.pre_roll_ms, bool):
            raise VoxError("invalid_type", "pre_roll_ms must be an int")
        if self.pre_roll_ms < 0:
            raise VoxError("pre_roll_negative", "pre_roll_ms must be >= 0")
        if not isinstance(self.post_roll_ms, int) or isinstance(self.post_roll_ms, bool):
            raise VoxError("invalid_type", "post_roll_ms must be an int")
        if self.post_roll_ms < 0:
            raise VoxError("post_roll_negative", "post_roll_ms must be >= 0")
        if not isinstance(self.frame_ms, int) or isinstance(self.frame_ms, bool):
            raise VoxError("invalid_type", "frame_ms must be an int")
        if self.frame_ms <= 0:
            raise VoxError("frame_non_positive", "frame_ms must be > 0")
        if not isinstance(self.min_speech_ms, int) or isinstance(self.min_speech_ms, bool):
            raise VoxError("invalid_type", "min_speech_ms must be an int")
        if self.min_speech_ms < self.frame_ms:
            raise VoxError("min_speech_too_small", "min_speech_ms must be >= frame_ms")


class PreRollBuffer:
    """Ring buffer keeping the last ``capacity_ms`` of audio frames.

    Frames are stored as ``bytes``. The buffer holds exactly the newest
    ``capacity_ms / frame_ms`` frames in chronological order.
    """

    def __init__(self, capacity_ms: int, frame_ms: int) -> None:
        if not isinstance(capacity_ms, int) or isinstance(capacity_ms, bool):
            raise VoxError("invalid_type", "capacity_ms must be an int")
        if capacity_ms < 0:
            raise VoxError("capacity_negative", "capacity_ms must be >= 0")
        if not isinstance(frame_ms, int) or isinstance(frame_ms, bool):
            raise VoxError("invalid_type", "frame_ms must be an int")
        if frame_ms <= 0:
            raise VoxError("frame_non_positive", "frame_ms must be > 0")

        self._capacity_frames = capacity_ms // frame_ms
        self._buffer: deque[bytes] = deque(maxlen=self._capacity_frames)

    def push(self, frame: bytes) -> None:
        """Append a frame. Oldest frame is dropped when full."""
        if not isinstance(frame, bytes):
            raise VoxError("invalid_type", "frame must be bytes")
        self._buffer.append(frame)

    def drain(self) -> list[bytes]:
        """Return all buffered frames in chronological order and clear."""
        frames = list(self._buffer)
        self._buffer.clear()
        return frames


class VoxDetector:
    """Streaming voice activity detector.

    State machine with two states: "IDLE" and "RECORDING".
    """

    def __init__(self, config: VoxConfig = VoxConfig()) -> None:
        self.config = config
        self.state: str = "IDLE"
        self._above_threshold_count = 0
        self._run_start_frame_idx: int | None = None
        self._below_continue_count = 0
        self._last_above_continue_frame_idx: int | None = None
        self._segment_start_ms: int | None = None
        self._last_frame_ms: int = -1

    def feed(self, frame_level_dbfs: float, now_ms: int) -> str:
        """Process one frame and return the detector action.

        Args:
            frame_level_dbfs: RMS level of the frame in dBFS.
            now_ms: Monotonic timestamp of this frame in milliseconds.
                Must not go backwards.

        Returns:
            "start" when a new recording segment begins,
            "stop" when the current segment ends,
            "none" otherwise.
        """
        if not isinstance(frame_level_dbfs, (int, float)) or isinstance(frame_level_dbfs, bool):
            raise VoxError("invalid_type", "frame_level_dbfs must be a number")
        if frame_level_dbfs != frame_level_dbfs or abs(frame_level_dbfs) == float("inf"):  # NaN or inf
            raise VoxError("invalid_level", "frame_level_dbfs must be finite")
        if not isinstance(now_ms, int) or isinstance(now_ms, bool):
            raise VoxError("invalid_type", "now_ms must be an int")
        if now_ms < self._last_frame_ms:
            raise VoxError("time_backwards", "now_ms must not decrease")
        self._last_frame_ms = now_ms

        threshold = self.config.threshold_dbfs
        continue_threshold = threshold - self.config.hysteresis_db
        frame_ms = self.config.frame_ms
        min_speech_frames = self.config.min_speech_ms // frame_ms
        post_roll_frames = self.config.post_roll_ms // frame_ms

        current_frame_idx = now_ms // frame_ms

        if self.state == "IDLE":
            if frame_level_dbfs >= threshold:
                if self._above_threshold_count == 0:
                    self._run_start_frame_idx = current_frame_idx
                self._above_threshold_count += 1
                if self._above_threshold_count >= min_speech_frames:
                    # Speech detected: segment starts at first frame of the qualifying run,
                    # moved back by pre_roll_ms (clamped to 0)
                    assert self._run_start_frame_idx is not None
                    first_frame_start_ms = self._run_start_frame_idx * frame_ms
                    start_ms = max(0, first_frame_start_ms - self.config.pre_roll_ms)
                    self._segment_start_ms = start_ms
                    self.state = "RECORDING"
                    self._below_continue_count = 0
                    self._last_above_continue_frame_idx = current_frame_idx
                    return "start"
            else:
                self._above_threshold_count = 0
                self._run_start_frame_idx = None
            return "none"

        # state == "RECORDING"
        if frame_level_dbfs >= continue_threshold:
            self._below_continue_count = 0
            self._last_above_continue_frame_idx = current_frame_idx
        else:
            self._below_continue_count += 1
            if self._below_continue_count >= post_roll_frames:
                # End segment: last frame above continue_threshold + post_roll_ms
                assert self._last_above_continue_frame_idx is not None
                last_above_frame_end_ms = (self._last_above_continue_frame_idx + 1) * frame_ms
                end_ms = last_above_frame_end_ms + self.config.post_roll_ms
                self.state = "IDLE"
                self._above_threshold_count = 0
                self._run_start_frame_idx = None
                self._last_above_continue_frame_idx = None
                self._segment_start_ms = None
                # Return "stop" with the end_ms encoded in the detector for plan_segments to read
                self._pending_stop_end_ms = end_ms
                return "stop"
        return "none"

    def describe(self) -> str:
        """German status text for the current state."""
        if self.state == "IDLE":
            return "Stimmgesteuert: wartet auf Sprache"
        return "Stimmgesteuert: Aufnahme läuft"


def plan_segments(
    frame_levels_dbfs: Sequence[float],
    config: VoxConfig = VoxConfig(),
) -> list[tuple[int, int]]:
    """Analyse a full level sequence and return recording segments.

    Args:
        frame_levels_dbfs: Sequence of frame levels in dBFS.
        config: VoxConfig with thresholds and timing parameters.

    Returns:
        List of (start_ms, end_ms) tuples, half-open intervals [start, end).
        Segments are merged when their gap is shorter than post_roll_ms.
        A recording still running at the end of input is closed at input length.
    """
    if not isinstance(frame_levels_dbfs, Sequence) or isinstance(frame_levels_dbfs, (str, bytes)):
        raise VoxError("invalid_type", "frame_levels_dbfs must be a sequence")
    if len(frame_levels_dbfs) == 0:
        return []

    detector = VoxDetector(config)
    frame_ms = config.frame_ms
    segments: list[tuple[int, int]] = []
    current_start: int | None = None

    for i, level in enumerate(frame_levels_dbfs):
        now_ms = i * frame_ms
        action = detector.feed(level, now_ms)
        if action == "start":
            current_start = detector._segment_start_ms
        elif action == "stop":
            if current_start is not None:
                end_ms = detector._pending_stop_end_ms
                segments.append((current_start, end_ms))
                current_start = None

    # Close open segment at input length
    if current_start is not None:
        end_ms = len(frame_levels_dbfs) * frame_ms
        segments.append((current_start, end_ms))

    # Merge segments with gap < post_roll_ms
    if len(segments) > 1:
        merged: list[tuple[int, int]] = []
        cur_start, cur_end = segments[0]
        for nxt_start, nxt_end in segments[1:]:
            if nxt_start - cur_end < config.post_roll_ms:
                cur_end = nxt_end
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = nxt_start, nxt_end
        merged.append((cur_start, cur_end))
        return merged

    return segments