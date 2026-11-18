"""Tests for correction_modes.py."""

import pytest

from src.clinic.correction_modes import (
    MODE_STOP_ON_TYPING,
    MODE_TYPE_WHILE_PLAYING,
    Action,
    CorrectionModeEngine,
    ModeError,
)


class TestConstructorValidation:
    def test_valid_defaults(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        assert engine._rewind_ms == 2000
        assert engine._resume_idle_ms == 1500

    def test_valid_custom_values(self):
        engine = CorrectionModeEngine(
            MODE_STOP_ON_TYPING, rewind_ms=1000, resume_idle_ms=500
        )
        assert engine._rewind_ms == 1000
        assert engine._resume_idle_ms == 500

    def test_rejects_negative_rewind(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine(MODE_STOP_ON_TYPING, rewind_ms=-1)
        assert exc.value.code == "invalid_rewind"

    def test_rejects_zero_resume_idle(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=0)
        assert exc.value.code == "invalid_resume_idle"

    def test_rejects_negative_resume_idle(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=-100)
        assert exc.value.code == "invalid_resume_idle"

    def test_rejects_unknown_mode(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine("unknown_mode")
        assert exc.value.code == "invalid_mode"

    def test_rejects_bool_rewind(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine(MODE_STOP_ON_TYPING, rewind_ms=True)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_rewind"

    def test_rejects_bool_resume_idle(self):
        with pytest.raises(ModeError) as exc:
            CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=False)  # type: ignore[arg-type]
        assert exc.value.code == "invalid_resume_idle"


class TestSetMode:
    def test_switch_mode_clears_pending(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        engine.on_key(1000, 5000)  # pause
        engine.set_mode(MODE_TYPE_WHILE_PLAYING)
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_rejects_unknown_mode(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        with pytest.raises(ModeError) as exc:
            engine.set_mode("invalid")
        assert exc.value.code == "invalid_mode"


class TestSetPlaying:
    def test_pause_cancels_pending_resume(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        engine.on_key(1000, 5000)  # pause, pending resume
        engine.set_playing(False)
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_rejects_non_bool(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        with pytest.raises(ModeError) as exc:
            engine.set_playing(1)  # type: ignore[arg-type]
        assert exc.value.code == "bool_argument"


class TestModeTypeWhilePlaying:
    def test_keys_always_none(self):
        engine = CorrectionModeEngine(MODE_TYPE_WHILE_PLAYING)
        engine.set_playing(True)
        assert engine.on_key(1000, 5000) == Action("none")
        assert engine.on_key(2000, 5000) == Action("none")

    def test_ticks_always_none(self):
        engine = CorrectionModeEngine(MODE_TYPE_WHILE_PLAYING)
        engine.set_playing(True)
        assert engine.on_tick(1000, 5000) == Action("none")
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_playing_false_still_none(self):
        engine = CorrectionModeEngine(MODE_TYPE_WHILE_PLAYING)
        engine.set_playing(False)
        assert engine.on_key(1000, 5000) == Action("none")
        assert engine.on_tick(2000, 5000) == Action("none")


class TestModeStopOnTyping:
    def test_first_key_pauses_when_playing(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        assert engine.on_key(1000, 5000) == Action("pause")

    def test_subsequent_keys_within_idle_return_none(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)  # pause
        assert engine.on_key(1500, 5000) == Action("none")
        assert engine.on_key(2000, 5000) == Action("none")

    def test_resume_after_idle_window(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, rewind_ms=2000, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)  # pause at 1000
        # before idle window expires
        assert engine.on_tick(2499, 5000) == Action("none")
        # exactly at idle window boundary
        assert engine.on_tick(2500, 5000) == Action("resume", seek_to_ms=3000)
        # after resume, further ticks return none
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_resume_not_one_ms_early(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        assert engine.on_tick(2499, 5000) == Action("none")

    def test_seek_target_clamped_at_zero(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, rewind_ms=2000, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 500)  # position < rewind
        assert engine.on_tick(2500, 500) == Action("resume", seek_to_ms=0)

    def test_no_pause_when_not_playing(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(False)
        assert engine.on_key(1000, 5000) == Action("none")
        assert engine.on_tick(2000, 5000) == Action("none")

    def test_user_pause_during_idle_cancels_resume(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        engine.set_playing(False)  # user pauses
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_switch_mode_clears_pending_resume(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        engine.set_mode(MODE_TYPE_WHILE_PLAYING)
        assert engine.on_tick(3000, 5000) == Action("none")

    def test_new_key_after_resume_starts_new_cycle(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING, rewind_ms=2000, resume_idle_ms=1500)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        engine.on_tick(2500, 5000)  # resume to 3000
        # new key should pause again
        assert engine.on_key(3000, 3000) == Action("pause")
        assert engine.on_tick(4500, 3000) == Action("resume", seek_to_ms=1000)


class TestInputValidation:
    def test_on_key_rejects_negative_now(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        with pytest.raises(ModeError) as exc:
            engine.on_key(-1, 5000)
        assert exc.value.code == "negative_time"

    def test_on_key_rejects_negative_position(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        with pytest.raises(ModeError) as exc:
            engine.on_key(1000, -1)
        assert exc.value.code == "negative_time"

    def test_on_key_rejects_time_travel(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        with pytest.raises(ModeError) as exc:
            engine.on_key(999, 5000)
        assert exc.value.code == "time_travel"

    def test_on_key_rejects_bool_now(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        with pytest.raises(ModeError) as exc:
            engine.on_key(True, 5000)  # type: ignore[arg-type]
        assert exc.value.code == "bool_argument"

    def test_on_tick_rejects_negative_now(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        with pytest.raises(ModeError) as exc:
            engine.on_tick(-1, 5000)
        assert exc.value.code == "negative_time"

    def test_on_tick_rejects_negative_position(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        with pytest.raises(ModeError) as exc:
            engine.on_tick(1000, -1)
        assert exc.value.code == "negative_time"

    def test_on_tick_rejects_time_travel(self):
        engine = CorrectionModeEngine(MODE_STOP_ON_TYPING)
        engine.set_playing(True)
        engine.on_key(1000, 5000)
        with pytest.raises(ModeError) as exc:
            engine.on_tick(999, 5000)
        assert exc.value.code == "time_travel"