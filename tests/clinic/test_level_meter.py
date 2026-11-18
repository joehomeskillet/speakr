"""Tests for src.clinic.level_meter."""

import math

import pytest

from src.clinic.level_meter import (
    MIN_DBFS,
    LevelMeter,
    LevelMeterError,
    LevelReading,
    clipping_runs,
    dbfs,
    gain_db_to_percent,
    peak_rms_dbfs,
    percent_to_gain_db,
)


class TestDbfs:
    def test_zero_returns_min_dbfs(self) -> None:
        assert dbfs(0) == MIN_DBFS

    def test_full_scale_negative_returns_zero(self) -> None:
        assert dbfs(-32768) == 0.0

    def test_full_scale_positive_returns_zero(self) -> None:
        assert dbfs(32767) == 0.0

    def test_half_scale_approx_minus_6(self) -> None:
        # 16384 is half of 32768 -> 20*log10(0.5) ≈ -6.02
        val = dbfs(16384)
        assert math.isclose(val, -6.02, abs_tol=0.05)

    def test_never_below_min_dbfs(self) -> None:
        assert dbfs(1) >= MIN_DBFS
        assert dbfs(-1) >= MIN_DBFS

    def test_custom_full_scale(self) -> None:
        assert dbfs(199, full_scale=200) == 0.0
        assert dbfs(0, full_scale=200) == MIN_DBFS

    def test_invalid_sample_type_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            dbfs(1.5)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_bool_sample_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            dbfs(True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_invalid_full_scale_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            dbfs(100, full_scale=0)
        assert exc.value.code == "invalid_value"

    def test_negative_full_scale_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            dbfs(100, full_scale=-100)
        assert exc.value.code == "invalid_value"


class TestPeakRmsDbfs:
    def test_empty_returns_min_dbfs(self) -> None:
        peak, rms = peak_rms_dbfs([])
        assert peak == MIN_DBFS
        assert rms == MIN_DBFS

    def test_full_scale_square_wave(self) -> None:
        # Alternating +32767, -32767 -> peak 0, rms 0
        samples = [32767, -32767] * 100
        peak, rms = peak_rms_dbfs(samples)
        assert peak == 0.0
        assert rms == 0.0

    def test_full_scale_sine_rms_approx_minus_3(self) -> None:
        # Full-scale sine: RMS = peak / sqrt(2) -> 20*log10(1/sqrt(2)) ≈ -3.01
        samples = [int(32767 * math.sin(2 * math.pi * i / 100)) for i in range(1000)]
        peak, rms = peak_rms_dbfs(samples)
        assert peak == 0.0
        assert math.isclose(rms, -3.01, abs_tol=0.1)

    def test_constant_signal(self) -> None:
        samples = [1000] * 100
        peak, rms = peak_rms_dbfs(samples)
        expected = dbfs(1000)
        assert peak == expected
        assert rms == expected

    def test_invalid_input_type_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            peak_rms_dbfs("not a sequence")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_invalid_sample_in_sequence_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            peak_rms_dbfs([1, 2, 3.5])  # type: ignore[list-item]
        assert exc.value.code == "invalid_type"


class TestClippingRuns:
    def test_no_clipping_returns_empty(self) -> None:
        samples = [0, 1000, -1000, 2000]
        assert clipping_runs(samples) == []

    def test_single_run_at_threshold(self) -> None:
        samples = [0, 32767, 32767, 32767, 0]
        runs = clipping_runs(samples)
        assert runs == [(1, 4)]

    def test_run_at_negative_threshold(self) -> None:
        samples = [0, -32767, -32767, -32767, 0]
        runs = clipping_runs(samples)
        assert runs == [(1, 4)]

    def test_run_exactly_min_run(self) -> None:
        samples = [32767, 32767, 32767]
        runs = clipping_runs(samples, min_run=3)
        assert runs == [(0, 3)]

    def test_run_shorter_than_min_run_ignored(self) -> None:
        samples = [32767, 32767]
        runs = clipping_runs(samples, min_run=3)
        assert runs == []

    def test_multiple_runs(self) -> None:
        samples = [32767] * 3 + [0] * 2 + [32767] * 4
        runs = clipping_runs(samples, min_run=3)
        assert runs == [(0, 3), (5, 9)]

    def test_run_to_end(self) -> None:
        samples = [0, 32767, 32767, 32767]
        runs = clipping_runs(samples, min_run=3)
        assert runs == [(1, 4)]

    def test_custom_threshold(self) -> None:
        samples = [1000, 1000, 1000]
        runs = clipping_runs(samples, threshold=1000, min_run=3)
        assert runs == [(0, 3)]

    def test_invalid_input_type_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            clipping_runs("not a sequence")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_invalid_threshold_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            clipping_runs([1, 2, 3], threshold=0)
        assert exc.value.code == "invalid_value"

    def test_invalid_min_run_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            clipping_runs([1, 2, 3], min_run=0)
        assert exc.value.code == "invalid_value"

    def test_bool_sample_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            clipping_runs([True, False, True])  # type: ignore[list-item]
        assert exc.value.code == "invalid_type"


class TestPercentToGainDb:
    def test_zero_returns_minus_60(self) -> None:
        assert percent_to_gain_db(0.0) == -60.0

    def test_hundred_returns_plus_12(self) -> None:
        assert percent_to_gain_db(100.0) == 12.0

    def test_strictly_increasing(self) -> None:
        prev = -61.0
        for p in [i * 0.5 for i in range(201)]:  # 0 to 100 in 0.5 steps
            val = percent_to_gain_db(p)
            assert val > prev, f"Not increasing at {p}: {prev} -> {val}"
            prev = val

    def test_out_of_range_negative_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db(-0.1)
        assert exc.value.code == "out_of_range"

    def test_out_of_range_over_100_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db(100.1)
        assert exc.value.code == "out_of_range"

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db("50")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_nan_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db(float("nan"))
        assert exc.value.code == "invalid_value"

    def test_inf_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db(float("inf"))
        assert exc.value.code == "invalid_value"

    def test_bool_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            percent_to_gain_db(True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"


class TestGainDbToPercent:
    def test_inverse_at_key_points(self) -> None:
        for p in (0.0, 10.0, 25.0, 50.0, 75.0, 100.0):
            gain = percent_to_gain_db(p)
            back = gain_db_to_percent(gain)
            assert math.isclose(back, p, abs_tol=0.01), f"Round-trip failed at {p}: {back}"

    def test_clamped_below_min(self) -> None:
        assert gain_db_to_percent(-100.0) == 0.0
        assert gain_db_to_percent(-60.0) == 0.0

    def test_clamped_above_max(self) -> None:
        assert gain_db_to_percent(12.0) == 100.0
        assert gain_db_to_percent(100.0) == 100.0

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            gain_db_to_percent("0")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_nan_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            gain_db_to_percent(float("nan"))
        assert exc.value.code == "invalid_value"


class TestLevelMeter:
    def test_default_construction(self) -> None:
        m = LevelMeter()
        reading = m.update([0], 0)
        assert isinstance(reading, LevelReading)
        assert reading.peak_dbfs == MIN_DBFS
        assert reading.rms_dbfs == MIN_DBFS
        assert reading.held_peak_dbfs == MIN_DBFS
        assert reading.clipped is False

    def test_custom_hold_and_decay(self) -> None:
        m = LevelMeter(hold_ms=500, decay_db_per_s=10.0)
        reading = m.update([1000], 0)
        assert reading.held_peak_dbfs == dbfs(1000)

    def test_invalid_hold_ms_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            LevelMeter(hold_ms=0)
        assert exc.value.code == "invalid_value"

    def test_invalid_decay_raises(self) -> None:
        with pytest.raises(LevelMeterError) as exc:
            LevelMeter(decay_db_per_s=0)
        assert exc.value.code == "invalid_value"

    def test_peak_hold_duration(self) -> None:
        m = LevelMeter(hold_ms=1000, decay_db_per_s=20.0)
        # Loud peak
        r1 = m.update([32767], 0)
        assert r1.held_peak_dbfs == 0.0
        # Within hold: quieter signal, held peak stays
        r2 = m.update([1000], 500)
        assert r2.held_peak_dbfs == 0.0
        # After hold: decay starts
        r3 = m.update([1000], 1500)
        # 500ms elapsed since hold expired -> 0.5s * 20dB/s = 10dB decay
        assert math.isclose(r3.held_peak_dbfs, -10.0, abs_tol=0.5)

    def test_louder_peak_resets_hold_immediately(self) -> None:
        m = LevelMeter(hold_ms=1000, decay_db_per_s=20.0)
        m.update([1000], 0)  # held_peak = -30dB approx
        m.update([1000], 500)  # still in hold
        r = m.update([32767], 800)  # louder peak
        assert r.held_peak_dbfs == 0.0
        # Hold should be reset to now + hold_ms
        r2 = m.update([1000], 1500)  # 700ms later, still in hold
        assert r2.held_peak_dbfs == 0.0

    def test_clip_flag_set_and_held(self) -> None:
        m = LevelMeter(hold_ms=1000, decay_db_per_s=20.0)
        # Clipping block
        r1 = m.update([32767] * 5, 0)
        assert r1.clipped is True
        # Next block no clipping, within hold
        r2 = m.update([0], 500)
        assert r2.clipped is True
        # After hold expires
        r3 = m.update([0], 1500)
        assert r3.clipped is False

    def test_time_regression_raises(self) -> None:
        m = LevelMeter()
        m.update([0], 100)
        with pytest.raises(LevelMeterError) as exc:
            m.update([0], 50)
        assert exc.value.code == "time_regression"

    def test_two_meters_independent(self) -> None:
        m1 = LevelMeter(hold_ms=1000)
        m2 = LevelMeter(hold_ms=1000)
        m1.update([32767], 0)
        m2.update([0], 0)
        r1 = m1.update([0], 500)
        r2 = m2.update([0], 500)
        assert r1.held_peak_dbfs == 0.0
        assert r2.held_peak_dbfs == MIN_DBFS

    def test_update_invalid_now_ms_raises(self) -> None:
        m = LevelMeter()
        with pytest.raises(LevelMeterError) as exc:
            m.update([0], -1)
        assert exc.value.code == "invalid_value"

    def test_update_invalid_samples_raises(self) -> None:
        m = LevelMeter()
        with pytest.raises(LevelMeterError) as exc:
            m.update("not a sequence", 0)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_rms_and_peak_in_reading(self) -> None:
        m = LevelMeter()
        samples = [32767, -32767] * 50
        r = m.update(samples, 0)
        assert r.peak_dbfs == 0.0
        assert r.rms_dbfs == 0.0

    def test_held_peak_never_below_min_dbfs(self) -> None:
        m = LevelMeter(hold_ms=100, decay_db_per_s=100.0)
        m.update([32767], 0)
        # Wait long enough for decay to go way below MIN_DBFS
        r = m.update([0], 10000)
        assert r.held_peak_dbfs >= MIN_DBFS

    def test_level_reading_is_frozen_dataclass(self) -> None:
        r = LevelReading(peak_dbfs=0.0, rms_dbfs=-3.0, held_peak_dbfs=0.0, clipped=False)
        with pytest.raises(Exception):
            r.peak_dbfs = -10.0  # type: ignore[misc]


class TestLevelMeterError:
    def test_error_has_code(self) -> None:
        err = LevelMeterError("test_code", "message")
        assert err.code == "test_code"
        assert str(err) == "message"

    def test_error_code_only(self) -> None:
        err = LevelMeterError("test_code")
        assert str(err) == "test_code"