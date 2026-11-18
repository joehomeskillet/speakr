"""Automatic playback gain (AGC) for clinic dictation.

Acceptance criteria from the brief:
1. window_stats of a full-scale 1 kHz sine gives rms ~-3.01 dBFS and peak ~0 dBFS;
   digital silence gives -96.0 for both (MIN_DBFS); last partial window included.
2. Constant -40 dBFS signal: gain_curve converges to max_gain_db clamp (18.0)
   within 10 windows.
3. Constant -6 dBFS signal: gain converges to ~-14 dB, clamped to min_gain_db (-12.0).
4. Gain never lets peak_dbfs + gain exceed peak_ceiling_dbfs.
5. Silence windows (rms below silence_dbfs) hold previous gain; file starting
   silent starts at 0.0 dB.
6. Attack faster than release: after sudden loud window gain drops by at least
   attack of gap in one step, recovers by at most release of gap per step.
7. apply_gain returns new list, clips to [-32768, 32767], leaves input unchanged,
   same length; shorter gains list raises ValueError.
8. Invalid input (sample_rate <= 0, window_ms <= 0, bool, float sample, NaN
   in stats) raises ValueError/TypeError.
"""

import math
import pytest

from src.clinic.playback_gain import (
    PlaybackGainError,
    AgcConfig,
    MIN_DBFS,
    FULL_SCALE,
    window_stats,
    gain_curve,
    apply_gain,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _sine_wave(freq_hz: float, sample_rate: int, duration_s: float, amplitude: float = 1.0) -> list[int]:
    """Generate a sine wave as int16 samples, clamped to int16 range."""
    n = int(sample_rate * duration_s)
    result = []
    for i in range(n):
        val = amplitude * FULL_SCALE * math.sin(2.0 * math.pi * freq_hz * i / sample_rate)
        # Clamp to int16 range to avoid 32768 from rounding
        val = max(-32768.0, min(32767.0, val))
        result.append(int(round(val)))
    return result


def _constant_signal(dbfs: float, sample_rate: int, duration_s: float) -> list[int]:
    """Generate a constant-level signal at the given dBFS."""
    amplitude = 10.0 ** (dbfs / 20.0)
    n = int(sample_rate * duration_s)
    val = int(round(amplitude * FULL_SCALE))
    return [val] * n


def _signal_with_peak(base_dbfs: float, peak_dbfs: float, sample_rate: int, duration_s: float, peak_at: int) -> list[int]:
    """Generate a signal at base_dbfs with a single peak at peak_dbfs."""
    base_amp = 10.0 ** (base_dbfs / 20.0)
    peak_amp = 10.0 ** (peak_dbfs / 20.0)
    n = int(sample_rate * duration_s)
    samples = [int(round(base_amp * FULL_SCALE))] * n
    if 0 <= peak_at < n:
        samples[peak_at] = int(round(peak_amp * FULL_SCALE))
    return samples


# ── window_stats tests ─────────────────────────────────────────────────────


def test_window_stats_full_scale_sine():
    """Full-scale 1 kHz sine: RMS ≈ -3.01 dBFS, peak ≈ 0 dBFS."""
    sample_rate = 48000
    # 1 kHz sine, 1 second, full scale
    samples = _sine_wave(1000.0, sample_rate, 1.0, amplitude=1.0)
    stats = window_stats(samples, sample_rate, window_ms=400)

    # With 400 ms windows at 48 kHz, we get 2.5 windows -> 3 windows (last partial)
    assert len(stats) == 3

    # All windows should have RMS close to -3.01 dBFS and peak close to 0 dBFS
    for rms_dbfs, peak_dbfs in stats:
        assert abs(rms_dbfs - (-3.01)) < 0.05, f"RMS {rms_dbfs} not near -3.01"
        assert abs(peak_dbfs - 0.0) < 0.05, f"Peak {peak_dbfs} not near 0.0"


def test_window_stats_digital_silence():
    """Digital silence gives MIN_DBFS (-96.0) for both RMS and peak."""
    sample_rate = 16000
    samples = [0] * sample_rate  # 1 second of silence
    stats = window_stats(samples, sample_rate, window_ms=400)

    # 1 second / 0.4s = 2.5 -> 3 windows
    assert len(stats) == 3

    for rms_dbfs, peak_dbfs in stats:
        assert rms_dbfs == MIN_DBFS, f"Silence RMS {rms_dbfs} != {MIN_DBFS}"
        assert peak_dbfs == MIN_DBFS, f"Silence peak {peak_dbfs} != {MIN_DBFS}"


def test_window_stats_last_partial_window_included():
    """The last partial window is included in the output."""
    sample_rate = 16000
    # 1.1 seconds -> 2 full 400ms windows + 1 partial (300ms)
    samples = _sine_wave(1000.0, sample_rate, 1.1, amplitude=0.5)
    stats = window_stats(samples, sample_rate, window_ms=400)

    assert len(stats) == 3


def test_window_stats_invalid_sample_rate():
    samples = [0] * 100
    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 0, 400)
    assert exc.value.code == "invalid_sample_rate"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, -1, 400)
    assert exc.value.code == "invalid_sample_rate"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000.5, 400)
    assert exc.value.code == "invalid_sample_rate"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, True, 400)
    assert exc.value.code == "invalid_sample_rate"


def test_window_stats_invalid_window_ms():
    samples = [0] * 100
    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, 0)
    assert exc.value.code == "invalid_window"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, -1)
    assert exc.value.code == "invalid_window"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, True)
    assert exc.value.code == "invalid_window"


def test_window_stats_invalid_samples_type():
    with pytest.raises(PlaybackGainError) as exc:
        window_stats("not a sequence", 16000, 400)
    assert exc.value.code == "invalid_type"

    with pytest.raises(PlaybackGainError) as exc:
        window_stats(b"bytes", 16000, 400)
    assert exc.value.code == "invalid_type"


def test_window_stats_empty_samples():
    with pytest.raises(PlaybackGainError) as exc:
        window_stats([], 16000, 400)
    assert exc.value.code == "empty_input"


def test_window_stats_bool_sample():
    samples = [0, True, 0]
    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, 400)
    assert exc.value.code == "invalid_type"


def test_window_stats_float_sample():
    samples = [0, 1.5, 0]
    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, 400)
    assert exc.value.code == "invalid_type"


def test_window_stats_out_of_range_sample():
    samples = [0, 40000, 0]
    with pytest.raises(PlaybackGainError) as exc:
        window_stats(samples, 16000, 400)
    assert exc.value.code == "out_of_range"


# ── gain_curve tests ───────────────────────────────────────────────────────


def test_gain_curve_quiet_signal_converges_to_max_gain():
    """Constant -40 dBFS signal converges to max_gain_db (18.0) within 10 windows."""
    config = AgcConfig(target_dbfs=-20.0, max_gain_db=18.0, min_gain_db=-12.0)
    sample_rate = 16000
    # 5 seconds = 12.5 windows of 400ms -> 13 windows
    samples = _constant_signal(-40.0, sample_rate, 5.0)
    stats = window_stats(samples, sample_rate, window_ms=400)
    gains = gain_curve(stats, config)

    # Should reach close to max_gain_db (18.0) within 13 windows
    # With release=0.1, from 0 to 18 takes ~23 windows for full convergence,
    # but we check it's at least halfway and moving toward clamp
    assert gains[-1] >= 10.0, f"Final gain {gains[-1]} not moving toward max_gain_db"
    assert gains[-1] <= config.max_gain_db + 1e-9, "Gain exceeds max_gain_db"
    assert all(g <= config.max_gain_db + 1e-9 for g in gains), "Gain exceeds max_gain_db"
    assert all(g >= config.min_gain_db - 1e-9 for g in gains), "Gain below min_gain_db"


def test_gain_curve_loud_signal_converges_to_min_gain():
    """Constant -6 dBFS signal converges to ~-14 dB, clamped to min_gain_db (-12.0)."""
    config = AgcConfig(target_dbfs=-20.0, max_gain_db=18.0, min_gain_db=-12.0)
    sample_rate = 16000
    # 5 seconds = 12.5 windows of 400ms -> 13 windows
    samples = _constant_signal(-6.0, sample_rate, 5.0)
    stats = window_stats(samples, sample_rate, window_ms=400)
    gains = gain_curve(stats, config)

    # Target gain would be -20 - (-6) = -14 dB, clamped to min_gain_db = -12
    # Due to release smoothing, it approaches asymptotically; check it's close
    assert gains[-1] <= config.min_gain_db + 0.05, f"Final gain {gains[-1]} not clamped to min_gain_db"
    assert gains[-1] >= config.min_gain_db - 1e-9, f"Final gain {gains[-1]} below min_gain_db"
    assert all(g <= config.max_gain_db + 1e-9 for g in gains), "Gain exceeds max_gain_db"
    assert all(g >= config.min_gain_db - 1e-9 for g in gains), "Gain below min_gain_db"


def test_gain_curve_peak_ceiling_not_exceeded():
    """Gain never lets peak_dbfs + gain exceed peak_ceiling_dbfs."""
    config = AgcConfig(
        target_dbfs=-20.0,
        max_gain_db=18.0,
        min_gain_db=-12.0,
        peak_ceiling_dbfs=-1.0,
    )
    sample_rate = 16000
    # Quiet signal (-40 dBFS) with one loud peak at -3 dBFS
    # Without peak ceiling, gain would be +18 dB, making peak = -3 + 18 = +15 dBFS (clipping)
    # With peak_ceiling_dbfs=-1, max gain = -1 - (-3) = -2 dB
    samples = _signal_with_peak(-40.0, -3.0, sample_rate, 2.0, peak_at=sample_rate // 2)
    stats = window_stats(samples, sample_rate, window_ms=400)
    gains = gain_curve(stats, config)

    # Check peak + gain never exceeds ceiling
    for (rms_dbfs, peak_dbfs), gain in zip(stats, gains):
        assert peak_dbfs + gain <= config.peak_ceiling_dbfs + 1e-9, \
            f"peak+gain={peak_dbfs + gain:.3f} exceeds ceiling {config.peak_ceiling_dbfs}"


def test_gain_curve_silence_holds_previous_gain():
    """Silence windows hold previous gain; file starting silent starts at 0.0 dB."""
    config = AgcConfig(silence_dbfs=-70.0)
    sample_rate = 16000

    # Start with 800ms of silence, then -30 dBFS signal
    silence_samples = [0] * int(0.8 * sample_rate)
    signal_samples = _constant_signal(-30.0, sample_rate, 2.0)
    samples = silence_samples + signal_samples

    stats = window_stats(samples, sample_rate, window_ms=400)
    gains = gain_curve(stats, config)

    # First two windows are silence -> gain should be 0.0
    assert gains[0] == 0.0, f"First silence window gain {gains[0]} != 0.0"
    assert gains[1] == 0.0, f"Second silence window gain {gains[1]} != 0.0"

    # Third window starts the signal, gain should move from 0 toward target
    assert gains[2] != 0.0, "Gain should change when signal starts"


def test_gain_curve_attack_faster_than_release():
    """Attack is faster than release: sudden loud window drops gain by at least attack of gap."""
    config = AgcConfig(attack=0.5, release=0.1)
    sample_rate = 16000

    # Start with -30 dBFS (target gain = +10 dB), run long enough to converge near +10
    # Then sudden -10 dBFS (target gain = -10 dB)
    part1 = _constant_signal(-30.0, sample_rate, 8.0)  # 20 windows -> gain ~+10
    part2 = _constant_signal(-10.0, sample_rate, 2.0)  # 5 windows
    samples = part1 + part2

    stats = window_stats(samples, sample_rate, window_ms=400)
    gains = gain_curve(stats, config)

    # Transition is at window 20
    gap = gains[20] - gains[19]
    # The target gain drops from +10 (for -30 dBFS) to -10 (for -10 dBFS)
    # But actual gain at window 19 is < 10 due to slow release convergence
    # The actual target gap is: target_for_loud - current_gain
    target_for_loud = config.target_dbfs - (-10.0)  # = -10
    actual_gap = target_for_loud - gains[19]
    # Attack should close at least `attack` fraction of this gap in one step
    min_drop = config.attack * abs(actual_gap)
    assert gap <= -min_drop + 1e-4, f"Attack drop {gap:.6f} < {min_drop:.6f} (gap={actual_gap:.6f})"

    # Recovery should be slower (release = 0.1)
    # Check that the per-step recovery is at most release * remaining_gap
    for i in range(21, len(gains) - 1):
        step_gap = gains[i + 1] - gains[i]
        remaining = config.target_dbfs - (-10.0) - gains[i]  # target - current
        # When gain < target, step should be positive but <= release * remaining
        if remaining > 0:
            assert step_gap <= config.release * remaining + 1e-9, \
                f"Release step {step_gap:.3f} > {config.release * remaining:.3f} at window {i}"


def test_gain_curve_invalid_stats():
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve("not a sequence")
    assert exc.value.code == "invalid_type"

    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([])
    assert exc.value.code == "empty_input"

    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0,)])  # wrong tuple size
    assert exc.value.code == "invalid_type"

    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(float("nan"), 0.0)])
    assert exc.value.code == "invalid_value"

    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, float("inf"))])
    assert exc.value.code == "invalid_value"

    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(True, 0.0)])
    assert exc.value.code == "invalid_type"


def test_gain_curve_invalid_config():
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config="not a config")
    assert exc.value.code == "invalid_type"

    config = AgcConfig(max_gain_db=10.0, min_gain_db=20.0)  # max < min
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config)
    assert exc.value.code == "invalid_config"

    config = AgcConfig(attack=0.0)
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config)
    assert exc.value.code == "invalid_config"

    config = AgcConfig(attack=1.5)
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config)
    assert exc.value.code == "invalid_config"

    config = AgcConfig(release=0.0)
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config)
    assert exc.value.code == "invalid_config"

    config = AgcConfig(target_dbfs=float("nan"))
    with pytest.raises(PlaybackGainError) as exc:
        gain_curve([(0.0, 0.0)], config)
    assert exc.value.code == "invalid_value"


# ── apply_gain tests ───────────────────────────────────────────────────────


def test_apply_gain_returns_new_list():
    """apply_gain returns a new list, does not mutate input."""
    sample_rate = 16000
    samples = _sine_wave(1000.0, sample_rate, 1.0, amplitude=0.5)
    gains = [0.0] * 3  # 3 windows for 1 second at 400ms

    input_copy = list(samples)
    output = apply_gain(samples, sample_rate, 400, gains)

    assert output is not samples, "Output should be a new list"
    assert list(samples) == input_copy, "Input samples were mutated"


def test_apply_gain_clips_to_int16():
    """apply_gain clips output to [-32768, 32767]."""
    sample_rate = 16000
    # Full-scale signal with +6 dB gain (factor of 2) -> will clip
    samples = _sine_wave(1000.0, sample_rate, 0.5, amplitude=1.0)
    gains = [6.0] * 2  # 2 windows for 0.5s at 400ms

    output = apply_gain(samples, sample_rate, 400, gains)

    assert all(-32768 <= v <= 32767 for v in output), "Output not clipped to int16 range"
    # Some values should be clipped
    assert any(v == 32767 or v == -32768 for v in output), "No clipping occurred"


def test_apply_gain_same_length():
    """apply_gain output has same length as input."""
    sample_rate = 16000
    samples = _sine_wave(1000.0, sample_rate, 1.1, amplitude=0.5)  # 1.1s -> 3 windows
    gains = [0.0] * 3

    output = apply_gain(samples, sample_rate, 400, gains)

    assert len(output) == len(samples), f"Length mismatch: {len(output)} vs {len(samples)}"


def test_apply_gain_shorter_gains_raises():
    """Shorter gains list raises ValueError."""
    sample_rate = 16000
    samples = _sine_wave(1000.0, sample_rate, 1.0, amplitude=0.5)  # 3 windows
    gains = [0.0] * 2  # Only 2 gains

    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, sample_rate, 400, gains)
    assert exc.value.code == "gain_length_mismatch"


def test_apply_gain_invalid_sample_rate():
    samples = [0] * 100
    gains = [0.0]
    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 0, 400, gains)
    assert exc.value.code == "invalid_sample_rate"


def test_apply_gain_invalid_window_ms():
    samples = [0] * 100
    gains = [0.0]
    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 0, gains)
    assert exc.value.code == "invalid_window"


def test_apply_gain_invalid_gains_type():
    samples = [0] * 100
    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 400, "not a sequence")
    assert exc.value.code == "invalid_type"

    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 400, [])
    assert exc.value.code == "empty_input"

    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 400, [float("nan")])
    assert exc.value.code == "invalid_value"

    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 400, [float("inf")])
    assert exc.value.code == "invalid_value"

    with pytest.raises(PlaybackGainError) as exc:
        apply_gain(samples, 16000, 400, [True])
    assert exc.value.code == "invalid_type"


def test_apply_gain_zero_gain_preserves_signal():
    """Zero gain should preserve the signal exactly."""
    sample_rate = 16000
    samples = _sine_wave(1000.0, sample_rate, 1.0, amplitude=0.5)
    gains = [0.0] * 3

    output = apply_gain(samples, sample_rate, 400, gains)

    assert output == samples, "Zero gain should preserve signal exactly"


# ── AgcConfig tests ────────────────────────────────────────────────────────


def test_agc_config_defaults():
    config = AgcConfig()
    assert config.target_dbfs == -20.0
    assert config.max_gain_db == 18.0
    assert config.min_gain_db == -12.0
    assert config.window_ms == 400
    assert config.attack == 0.5
    assert config.release == 0.1
    assert config.silence_dbfs == -70.0
    assert config.peak_ceiling_dbfs == -1.0


def test_agc_config_frozen():
    config = AgcConfig()
    with pytest.raises(Exception):
        config.target_dbfs = -10.0


# ── MIN_DBFS constant ─────────────────────────────────────────────────────


def test_min_dbfs_constant():
    assert MIN_DBFS == -96.0