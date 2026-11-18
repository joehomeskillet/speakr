"""Automatic playback gain (AGC) for clinic dictation.

Computes a gain curve for playback only. The gain is never applied to an
original and the input samples are never mutated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

FULL_SCALE = 32768.0
MIN_DBFS = -96.0


class PlaybackGainError(ValueError):
    """Gain computation was rejected before it produced a result."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AgcConfig:
    """Configuration for automatic gain control during playback.

    Attributes:
        target_dbfs: Target RMS level in dBFS for the output.
        max_gain_db: Maximum gain that can be applied (clamp).
        min_gain_db: Minimum gain that can be applied (clamp).
        window_ms: Analysis window size in milliseconds.
        attack: Fraction of the gain gap to close per step when gain must decrease.
        release: Fraction of the gain gap to close per step when gain must increase.
        silence_dbfs: RMS threshold below which a window is considered silent.
        peak_ceiling_dbfs: Peak level that must not be exceeded after gain.
    """

    target_dbfs: float = -20.0
    max_gain_db: float = 18.0
    min_gain_db: float = -12.0
    window_ms: int = 400
    attack: float = 0.5
    release: float = 0.1
    silence_dbfs: float = -70.0
    peak_ceiling_dbfs: float = -1.0


def _validate_samples(samples: Sequence[int], label: str = "samples") -> None:
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
        raise PlaybackGainError("invalid_type", f"{label} must be a sequence")
    if len(samples) == 0:
        raise PlaybackGainError("empty_input", f"{label} must not be empty")
    for i, s in enumerate(samples):
        if isinstance(s, bool) or not isinstance(s, int):
            raise PlaybackGainError("invalid_type", f"{label}[{i}] must be an int")
        if s < -32768 or s > 32767:
            # Allow 32768 as it can occur from rounding full-scale sine
            if s == 32768:
                continue
            raise PlaybackGainError("out_of_range", f"{label}[{i}] out of int16 range")


def _validate_sample_rate(sample_rate: int) -> None:
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
        raise PlaybackGainError("invalid_sample_rate", "sample_rate must be a positive int")


def _validate_window_ms(window_ms: int) -> None:
    if isinstance(window_ms, bool) or not isinstance(window_ms, int) or window_ms <= 0:
        raise PlaybackGainError("invalid_window", "window_ms must be a positive int")


def _validate_gains(gains_db: Sequence[float], label: str = "gains_db") -> None:
    if not isinstance(gains_db, Sequence) or isinstance(gains_db, (str, bytes)):
        raise PlaybackGainError("invalid_type", f"{label} must be a sequence")
    if len(gains_db) == 0:
        raise PlaybackGainError("empty_input", f"{label} must not be empty")
    for i, g in enumerate(gains_db):
        if isinstance(g, bool) or not isinstance(g, (int, float)):
            raise PlaybackGainError("invalid_type", f"{label}[{i}] must be a number")
        if math.isnan(g) or math.isinf(g):
            raise PlaybackGainError("invalid_value", f"{label}[{i}] must be finite")


def _validate_stats(stats: Sequence[tuple[float, float]]) -> None:
    if not isinstance(stats, Sequence) or isinstance(stats, (str, bytes)):
        raise PlaybackGainError("invalid_type", "stats must be a sequence")
    if len(stats) == 0:
        raise PlaybackGainError("empty_input", "stats must not be empty")
    for i, w in enumerate(stats):
        if not isinstance(w, tuple) or len(w) != 2:
            raise PlaybackGainError("invalid_type", f"stats[{i}] must be a (rms_dbfs, peak_dbfs) tuple")
        rms, peak = w
        for val, name in ((rms, "rms_dbfs"), (peak, "peak_dbfs")):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise PlaybackGainError("invalid_type", f"stats[{i}].{name} must be a number")
            if math.isnan(val) or math.isinf(val):
                raise PlaybackGainError("invalid_value", f"stats[{i}].{name} must be finite")


def _validate_config(config: AgcConfig) -> None:
    if not isinstance(config, AgcConfig):
        raise PlaybackGainError("invalid_type", "config must be an AgcConfig")
    for name in (
        "target_dbfs",
        "max_gain_db",
        "min_gain_db",
        "window_ms",
        "attack",
        "release",
        "silence_dbfs",
        "peak_ceiling_dbfs",
    ):
        val = getattr(config, name)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise PlaybackGainError("invalid_type", f"config.{name} must be a number")
        if math.isnan(val) or math.isinf(val):
            raise PlaybackGainError("invalid_value", f"config.{name} must be finite")
    if config.max_gain_db < config.min_gain_db:
        raise PlaybackGainError("invalid_config", "max_gain_db must be >= min_gain_db")
    if not (0.0 < config.attack <= 1.0):
        raise PlaybackGainError("invalid_config", "attack must be in (0, 1]")
    if not (0.0 < config.release <= 1.0):
        raise PlaybackGainError("invalid_config", "release must be in (0, 1]")


def _dbfs_from_rms(rms: float) -> float:
    if rms <= 0.0:
        return MIN_DBFS
    return 20.0 * math.log10(rms / FULL_SCALE)


def window_stats(
    samples: Sequence[int], sample_rate: int, window_ms: int
) -> list[tuple[float, float]]:
    """Compute per-window RMS and peak in dBFS.

    Args:
        samples: Audio samples as int16 values ([-32768, 32767]).
        sample_rate: Sample rate in Hz (e.g., 16000, 44100, 48000).
        window_ms: Window size in milliseconds.

    Returns:
        List of (rms_dbfs, peak_dbfs) tuples, one per window. The last
        partial window is included.
    """
    _validate_samples(samples)
    _validate_sample_rate(sample_rate)
    _validate_window_ms(window_ms)

    window_samples = max(1, int(round(sample_rate * window_ms / 1000.0)))
    results: list[tuple[float, float]] = []

    for start in range(0, len(samples), window_samples):
        window = samples[start : start + window_samples]
        if not window:
            continue
        # RMS
        sum_sq = 0.0
        peak_abs = 0
        for s in window:
            sum_sq += s * s
            if abs(s) > peak_abs:
                peak_abs = abs(s)
        rms = math.sqrt(sum_sq / len(window))
        rms_dbfs = _dbfs_from_rms(rms)
        peak_dbfs = _dbfs_from_rms(float(peak_abs))
        results.append((rms_dbfs, peak_dbfs))

    return results


def gain_curve(
    stats: Sequence[tuple[float, float]], config: AgcConfig = AgcConfig()
) -> list[float]:
    """Compute the gain in dB for each window.

    The gain drives the RMS toward target_dbfs while respecting:
    - max_gain_db / min_gain_db clamps
    - peak_ceiling_dbfs (peak_dbfs + gain must not exceed it)
    - silence_dbfs (silent windows hold previous gain; start at 0.0 dB)
    - attack (faster) vs release (slower) smoothing

    Args:
        stats: Sequence of (rms_dbfs, peak_dbfs) from window_stats.
        config: AgcConfig with target, clamps, and smoothing parameters.

    Returns:
        List of gain values in dB, one per window.
    """
    _validate_stats(stats)
    _validate_config(config)

    gains: list[float] = []
    prev_gain = 0.0

    for rms_dbfs, peak_dbfs in stats:
        # Target gain to reach target_dbfs
        target_gain = config.target_dbfs - rms_dbfs

        # Clamp to [min_gain_db, max_gain_db]
        clamped_gain = max(config.min_gain_db, min(config.max_gain_db, target_gain))

        # Peak ceiling: gain must not let peak exceed ceiling
        max_gain_for_peak = config.peak_ceiling_dbfs - peak_dbfs
        if clamped_gain > max_gain_for_peak:
            clamped_gain = max_gain_for_peak

        # Silence handling: hold previous gain
        if rms_dbfs <= config.silence_dbfs:
            gain = prev_gain
        else:
            # Attack/release smoothing
            gap = clamped_gain - prev_gain
            if gap < 0:
                # Gain must decrease (attack: faster)
                gain = prev_gain + gap * config.attack
            else:
                # Gain must increase (release: slower)
                gain = prev_gain + gap * config.release

        gains.append(gain)
        prev_gain = gain

    return gains


def apply_gain(
    samples: Sequence[int],
    sample_rate: int,
    window_ms: int,
    gains_db: Sequence[float],
) -> list[int]:
    """Apply per-window gain to samples.

    Args:
        samples: Input int16 samples (not mutated).
        sample_rate: Sample rate in Hz.
        window_ms: Window size in milliseconds (must match window_stats).
        gains_db: Gain in dB per window (from gain_curve).

    Returns:
        New list of int16 samples with gain applied, clipped to [-32768, 32767].

    Raises:
        ValueError: If gains_db is shorter than the number of windows.
    """
    _validate_samples(samples)
    _validate_sample_rate(sample_rate)
    _validate_window_ms(window_ms)
    _validate_gains(gains_db)

    window_samples = max(1, int(round(sample_rate * window_ms / 1000.0)))
    num_windows = (len(samples) + window_samples - 1) // window_samples

    if len(gains_db) < num_windows:
        raise PlaybackGainError(
            "gain_length_mismatch",
            f"gains_db has {len(gains_db)} entries but {num_windows} windows are needed",
        )

    # Copy input to verify non-mutation
    input_copy = list(samples)
    output: list[int] = []

    for w in range(num_windows):
        start = w * window_samples
        end = min(start + window_samples, len(samples))
        gain_db = gains_db[w]
        gain_linear = 10.0 ** (gain_db / 20.0)

        for i in range(start, end):
            val = int(round(samples[i] * gain_linear))
            # Clip to int16 range
            if val > 32767:
                val = 32767
            elif val < -32768:
                val = -32768
            output.append(val)

    # Verify input was not mutated
    if list(samples) != input_copy:
        raise RuntimeError("input samples were mutated")

    return output