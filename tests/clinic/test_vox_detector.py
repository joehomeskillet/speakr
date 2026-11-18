"""Tests for the Vox detector module."""

import pytest

from src.clinic.vox_detector import (
    VoxConfig,
    VoxError,
    PreRollBuffer,
    VoxDetector,
    plan_segments,
)


class TestVoxConfig:
    """Config validation tests."""

    def test_valid_defaults(self):
        cfg = VoxConfig()
        assert cfg.threshold_dbfs == -40.0
        assert cfg.hysteresis_db == 6.0
        assert cfg.pre_roll_ms == 500
        assert cfg.post_roll_ms == 1500
        assert cfg.frame_ms == 20
        assert cfg.min_speech_ms == 100

    def test_threshold_above_zero_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(threshold_dbfs=1.0)
        assert exc.value.code == "threshold_positive"

    def test_negative_pre_roll_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(pre_roll_ms=-1)
        assert exc.value.code == "pre_roll_negative"

    def test_negative_post_roll_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(post_roll_ms=-1)
        assert exc.value.code == "post_roll_negative"

    def test_frame_ms_zero_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(frame_ms=0)
        assert exc.value.code == "frame_non_positive"

    def test_frame_ms_negative_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(frame_ms=-10)
        assert exc.value.code == "frame_non_positive"

    def test_min_speech_lt_frame_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(frame_ms=20, min_speech_ms=10)
        assert exc.value.code == "min_speech_too_small"

    def test_min_speech_equal_frame_ok(self):
        cfg = VoxConfig(frame_ms=20, min_speech_ms=20)
        assert cfg.min_speech_ms == 20

    def test_bool_rejected_for_int_fields(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(pre_roll_ms=True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_nan_threshold_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(threshold_dbfs=float("nan"))
        assert exc.value.code == "invalid_value"

    def test_inf_threshold_raises(self):
        with pytest.raises(VoxError) as exc:
            VoxConfig(threshold_dbfs=float("inf"))
        assert exc.value.code == "invalid_value"


class TestPreRollBuffer:
    """Pre-roll ring buffer tests."""

    def test_keeps_newest_frames(self):
        buf = PreRollBuffer(capacity_ms=100, frame_ms=20)  # 5 frames
        for i in range(10):
            buf.push(f"frame{i}".encode())
        drained = buf.drain()
        assert len(drained) == 5
        assert [f.decode() for f in drained] == ["frame5", "frame6", "frame7", "frame8", "frame9"]

    def test_drain_clears_buffer(self):
        buf = PreRollBuffer(capacity_ms=100, frame_ms=20)
        buf.push(b"a")
        buf.push(b"b")
        assert buf.drain() == [b"a", "b".encode()]
        assert buf.drain() == []

    def test_zero_capacity(self):
        buf = PreRollBuffer(capacity_ms=0, frame_ms=20)
        buf.push(b"x")
        assert buf.drain() == []

    def test_invalid_frame_type(self):
        buf = PreRollBuffer(capacity_ms=100, frame_ms=20)
        with pytest.raises(VoxError) as exc:
            buf.push("not bytes")  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_negative_capacity_raises(self):
        with pytest.raises(VoxError) as exc:
            PreRollBuffer(capacity_ms=-1, frame_ms=20)
        assert exc.value.code == "capacity_negative"

    def test_zero_frame_ms_raises(self):
        with pytest.raises(VoxError) as exc:
            PreRollBuffer(capacity_ms=100, frame_ms=0)
        assert exc.value.code == "frame_non_positive"


class TestVoxDetector:
    """Streaming detector tests."""

    def test_describe_idle(self):
        det = VoxDetector()
        assert det.describe() == "Stimmgesteuert: wartet auf Sprache"
        assert det.state == "IDLE"

    def test_describe_recording(self):
        det = VoxDetector(VoxConfig(threshold_dbfs=-40.0, hysteresis_db=6.0, min_speech_ms=20, frame_ms=20))
        # Feed enough frames above threshold to trigger
        for i in range(2):
            det.feed(-30.0, i * 20)
        assert det.state == "RECORDING"
        assert det.describe() == "Stimmgesteuert: Aufnahme läuft"

    def test_time_backwards_raises(self):
        det = VoxDetector()
        det.feed(-50.0, 100)
        with pytest.raises(VoxError) as exc:
            det.feed(-50.0, 50)
        assert exc.value.code == "time_backwards"

    def test_nan_level_raises(self):
        det = VoxDetector()
        with pytest.raises(VoxError) as exc:
            det.feed(float("nan"), 0)
        assert exc.value.code == "invalid_level"

    def test_inf_level_raises(self):
        det = VoxDetector()
        with pytest.raises(VoxError) as exc:
            det.feed(float("inf"), 0)
        assert exc.value.code == "invalid_level"

    def test_bool_level_rejected(self):
        det = VoxDetector()
        with pytest.raises(VoxError) as exc:
            det.feed(True, 0)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_bool_now_ms_rejected(self):
        det = VoxDetector()
        with pytest.raises(VoxError) as exc:
            det.feed(-50.0, True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"


class TestPlanSegments:
    """Offline segment planning tests."""

    def test_empty_input_returns_empty(self):
        segs = plan_segments([], VoxConfig())
        assert segs == []

    def test_no_speech_returns_empty(self):
        levels = [-60.0] * 100
        segs = plan_segments(levels, VoxConfig(threshold_dbfs=-40.0))
        assert segs == []

    def test_single_loud_frame_below_min_speech_starts_nothing(self):
        # 1 frame at -30 dBFS, min_speech_ms=100, frame_ms=20 -> need 5 frames
        levels = [-60.0] * 10 + [-30.0] + [-60.0] * 10
        segs = plan_segments(levels, VoxConfig(threshold_dbfs=-40.0, min_speech_ms=100, frame_ms=20))
        assert segs == []

    def test_speech_trigger_after_min_speech_ms(self):
        # 5 frames at -30 dBFS (100ms at 20ms/frame) -> triggers at frame 14 (0-indexed)
        # Input: 10 silence + 5 speech + 10 silence = 25 frames = 500ms
        # post_roll = 1500ms (75 frames) > 10 silence frames, so segment closes at input end
        levels = [-60.0] * 10 + [-30.0] * 5 + [-60.0] * 10
        segs = plan_segments(levels, VoxConfig(threshold_dbfs=-40.0, min_speech_ms=100, frame_ms=20))
        assert len(segs) == 1
        start, end = segs[0]
        # Speech run starts at frame 10 (200ms), pre_roll=500 -> start at max(0, 200-500)=0
        assert start == 0
        # Segment still running at input end (500ms), closes at input length
        assert end == 500

    def test_pre_roll_moves_start_back(self):
        # Speech at frame 50 (1000ms), pre_roll=500 -> start at 500ms
        # Need 5 frames (100ms) above threshold to trigger
        levels = [-60.0] * 50 + [-30.0] * 10 + [-60.0] * 10
        segs = plan_segments(levels, VoxConfig(threshold_dbfs=-40.0, pre_roll_ms=500, frame_ms=20, min_speech_ms=100))
        assert len(segs) == 1
        start, _ = segs[0]
        # First speech frame at frame 50 = 1000ms, pre_roll 500ms -> 500ms
        assert start == 500

    def test_pre_roll_clamped_at_zero(self):
        # Speech at frame 10 (200ms), pre_roll=500 -> start at 0
        levels = [-60.0] * 10 + [-30.0] * 10 + [-60.0] * 10
        segs = plan_segments(levels, VoxConfig(threshold_dbfs=-40.0, pre_roll_ms=500, frame_ms=20, min_speech_ms=100))
        assert len(segs) == 1
        start, _ = segs[0]
        assert start == 0

    def test_hysteresis_continue_threshold(self):
        # threshold=-40, hysteresis=6 -> continue_threshold=-46
        # Dip to -43 should NOT end segment, dip to -47 for post_roll should
        # Frames:
        # 0-9: silence (-60)
        # 10-19: speech (-30) 10 frames = 200ms
        # 20-24: dip (-43) 5 frames = 100ms, above -46 so continue
        # 25-29: speech (-30) 5 frames
        # 30-109: below continue (-47) 80 frames = 1600ms > post_roll 1500ms (75 frames)
        # 110-119: silence
        levels = (
            [-60.0] * 10 +           # silence
            [-30.0] * 10 +           # speech (10 frames = 200ms > 100ms min)
            [-43.0] * 5 +            # dip but above -46 (continue)
            [-30.0] * 5 +            # speech resumes
            [-47.0] * 80 +           # below -46 for 1600ms > post_roll 1500ms
            [-60.0] * 10
        )
        segs = plan_segments(
            levels,
            VoxConfig(
                threshold_dbfs=-40.0,
                hysteresis_db=6.0,
                post_roll_ms=1500,
                frame_ms=20,
                min_speech_ms=100,
            ),
        )
        assert len(segs) == 1
        start, end = segs[0]
        # Speech starts at frame 10, pre_roll 500 -> max(0, 200-500)=0
        assert start == 0
        # Last frame above -46 is frame 29 (index 29), ends at 30*20=600ms
        # end = 600 + 1500 = 2100ms
        assert end == 2100

    def test_hysteresis_dip_ends_segment(self):
        # Dip below continue_threshold for full post_roll
        # 0-9: silence
        # 10-19: speech (10 frames)
        # 20-99: below continue (-47) 80 frames = 1600ms > post_roll 1500ms (75 frames)
        levels = (
            [-60.0] * 10 +
            [-30.0] * 10 +
            [-47.0] * 80 +  # 1600ms below -46
            [-60.0] * 10
        )
        segs = plan_segments(
            levels,
            VoxConfig(threshold_dbfs=-40.0, hysteresis_db=6.0, post_roll_ms=1500, frame_ms=20, min_speech_ms=100),
        )
        assert len(segs) == 1
        start, end = segs[0]
        assert start == 0
        # Last above -46 at frame 19, ends at 20*20=400ms
        # end = 400 + 1500 = 1900ms
        assert end == 1900

    def test_segments_merge_when_gap_lt_post_roll(self):
        # Two speech bursts with 1000ms gap (< post_roll 1500ms)
        # Burst 1: frames 10-19 (200ms)
        # Gap: frames 20-69 (50 frames = 1000ms)
        # Burst 2: frames 70-79 (200ms)
        # post_roll = 1500ms (75 frames)
        # Need 75 frames silence after burst 2 to trigger stop
        # Burst 1 end: frame 19 ends at 400ms + 1500 = 1900ms
        # Burst 2 start: frame 70 starts at 1400ms - 500 pre_roll = 900ms
        # Gap between segments: 900 - 1900 = -1000 (overlap) -> merge
        # Merged end: burst 2 last frame 79 ends at 1600ms + 1500 = 3100ms
        levels = (
            [-60.0] * 10 +
            [-30.0] * 10 +     # speech 1 (frames 10-19)
            [-60.0] * 50 +     # 1000ms gap (frames 20-69)
            [-30.0] * 10 +     # speech 2 (frames 70-79)
            [-60.0] * 80       # 1600ms silence (frames 80-159) > post_roll
        )
        segs = plan_segments(
            levels,
            VoxConfig(threshold_dbfs=-40.0, post_roll_ms=1500, frame_ms=20, min_speech_ms=100),
        )
        assert len(segs) == 1
        start, end = segs[0]
        assert start == 0
        # Burst 2 last frame 79 ends at 1600ms, +1500 = 3100ms
        assert end == 3100

    def test_segments_separate_when_gap_ge_post_roll(self):
        # Two speech bursts with gap >= post_roll (1500ms) between segment end and next start
        # Burst 1: frames 10-19 -> end at frame 19 ends 400ms + 1500 = 1900ms
        # Need segment 2 start >= 1900 + 1500 = 3400ms
        # Segment 2 start = frame_X * 20 - 500 (pre_roll) >= 3400 -> frame_X >= 195
        # Burst 2: frames 195-204 (10 frames)
        # post_roll = 1500ms (75 frames), need 75 frames silence after each burst
        levels = (
            [-60.0] * 10 +
            [-30.0] * 10 +       # speech 1 (frames 10-19)
            [-60.0] * 175 +      # gap: frames 20-194 (175 frames = 3500ms)
            [-30.0] * 10 +       # speech 2 (frames 195-204)
            [-60.0] * 80         # 1600ms silence (frames 205-284) > post_roll
        )
        segs = plan_segments(
            levels,
            VoxConfig(threshold_dbfs=-40.0, post_roll_ms=1500, frame_ms=20, min_speech_ms=100),
        )
        assert len(segs) == 2
        # First: start 0, end 1900
        # Second: frame 195 starts at 3900ms, pre_roll 500 -> 3400ms
        # End: frame 204 ends at 4100ms + 1500 = 5600ms
        assert segs[0] == (0, 1900)
        assert segs[1] == (3400, 5600)

    def test_open_segment_closes_at_input_end(self):
        # Speech starts but never drops below threshold
        # 10 silence + 50 speech = 60 frames = 1200ms
        levels = [-60.0] * 10 + [-30.0] * 50
        segs = plan_segments(
            levels,
            VoxConfig(threshold_dbfs=-40.0, post_roll_ms=1500, frame_ms=20, min_speech_ms=100),
        )
        assert len(segs) == 1
        start, end = segs[0]
        assert start == 0
        # Input length = 60 frames * 20 = 1200ms
        assert end == 1200

    def test_no_lost_word_onset(self):
        """
        Acceptance criterion 3:
        Levels [-60, -55, -45, -39, -30, -30...] with threshold -40
        and 500ms pre-roll give segment start at or before first audible rise (frame 1).
        """
        # Frame 0: -60, Frame 1: -55 (first audible rise), Frame 2: -45, Frame 3: -39 (crosses -40)
        # With min_speech_ms=100 (5 frames at 20ms), threshold crossed at frame 3,
        # need 5 frames >= -40 -> frames 3,4,5,6,7
        levels = [-60.0, -55.0, -45.0] + [-39.0] * 10 + [-30.0] * 20
        segs = plan_segments(
            levels,
            VoxConfig(threshold_dbfs=-40.0, pre_roll_ms=500, frame_ms=20, min_speech_ms=100),
        )
        assert len(segs) == 1
        start, _ = segs[0]
        # First audible rise at frame 1 (20ms). Segment start should be <= 20ms.
        # Qualifying run starts at frame 3 (60ms). pre_roll 500 -> max(0, 60-500)=0
        assert start == 0
        assert start <= 20  # At or before first audible rise (frame 1 = 20ms)

    def test_invalid_input_type_raises(self):
        with pytest.raises(VoxError) as exc:
            plan_segments("not a sequence", VoxConfig())  # type: ignore[arg-type]
        assert exc.value.code == "invalid_type"

    def test_feed_matches_plan_segments(self):
        """VoxDetector.feed agrees with plan_segments on a 30 second synthetic sequence."""
        import random
        random.seed(42)
        frame_ms = 20
        num_frames = 30000 // frame_ms  # 30 seconds
        config = VoxConfig(threshold_dbfs=-40.0, hysteresis_db=6.0, pre_roll_ms=500, post_roll_ms=1500, frame_ms=frame_ms, min_speech_ms=100)

        # Generate synthetic levels: mostly silence with some speech bursts
        levels = []
        i = 0
        while i < num_frames:
            if random.random() < 0.02:  # 2% chance of speech burst
                burst_len = random.randint(5, 50)  # 100ms to 1s
                for _ in range(burst_len):
                    levels.append(random.uniform(-35.0, -20.0))
                i += burst_len
            else:
                levels.append(random.uniform(-60.0, -50.0))
                i += 1
        levels = levels[:num_frames]

        # Run plan_segments
        planned = plan_segments(levels, config)

        # Run VoxDetector streaming
        det = VoxDetector(config)
        streaming_segments = []
        current_start = None
        for idx, level in enumerate(levels):
            now_ms = idx * frame_ms
            action = det.feed(level, now_ms)
            if action == "start":
                current_start = det._segment_start_ms
            elif action == "stop":
                if current_start is not None:
                    end_ms = det._pending_stop_end_ms
                    streaming_segments.append((current_start, end_ms))
                    current_start = None
        if current_start is not None:
            streaming_segments.append((current_start, num_frames * frame_ms))

        # Merge streaming segments same as plan_segments
        if len(streaming_segments) > 1:
            merged = []
            cur_start, cur_end = streaming_segments[0]
            for nxt_start, nxt_end in streaming_segments[1:]:
                if nxt_start - cur_end < config.post_roll_ms:
                    cur_end = nxt_end
                else:
                    merged.append((cur_start, cur_end))
                    cur_start, cur_end = nxt_start, nxt_end
            merged.append((cur_start, cur_end))
            streaming_segments = merged

        # Compare segment boundaries
        assert planned == streaming_segments, f"Planned: {planned}, Streaming: {streaming_segments}"


class TestVoxError:
    """Error code stability tests."""

    def test_error_has_code_attribute(self):
        try:
            raise VoxError("test_code", "test message")
        except VoxError as e:
            assert e.code == "test_code"
            assert str(e) == "test message"

    def test_error_is_valueerror(self):
        assert issubclass(VoxError, ValueError)