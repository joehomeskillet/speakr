"""Tests for playback tempo, seek and rewind arithmetic.

Covers PAR-COR-004. Pure arithmetic, no database, no mocks.
"""

import math
import pytest

from src.clinic.playback_tempo import (
    TEMPO_MIN,
    TEMPO_MAX,
    TEMPO_DEFAULT,
    TEMPO_STEP,
    TempoError,
    NoAlignment,
    clamp_tempo,
    step_tempo,
    seek,
    jump_back,
    wall_to_media_ms,
    media_to_wall_ms,
    resume_position,
    rewind_words,
)


# =============================================================================
# Constants
# =============================================================================


def test_tempo_constants():
    assert TEMPO_MIN == 0.5
    assert TEMPO_MAX == 2.0
    assert TEMPO_DEFAULT == 1.0
    assert TEMPO_STEP == 0.1


# =============================================================================
# 1. clamp_tempo tests
# =============================================================================


def test_clamp_tempo_below_min():
    assert clamp_tempo(0.1) == 0.5
    assert clamp_tempo(0.0) == 0.5
    assert clamp_tempo(-1.0) == 0.5


def test_clamp_tempo_above_max():
    assert clamp_tempo(9.0) == 2.0
    assert clamp_tempo(3.0) == 2.0
    assert clamp_tempo(100.0) == 2.0


def test_clamp_tempo_within_range():
    assert clamp_tempo(1.0) == 1.0
    assert clamp_tempo(1.5) == 1.5
    assert clamp_tempo(2.0) == 2.0


def test_clamp_tempo_rounds_to_two_decimals():
    assert clamp_tempo(1.234) == 1.23
    assert clamp_tempo(1.235) == 1.24
    assert clamp_tempo(1.236) == 1.24


def test_clamp_tempo_rejects_bool():
    with pytest.raises(TempoError) as exc:
        clamp_tempo(True)
    assert exc.value.code == "invalid_type"
    with pytest.raises(TempoError) as exc:
        clamp_tempo(False)
    assert exc.value.code == "invalid_type"


def test_clamp_tempo_rejects_nan():
    with pytest.raises(TempoError) as exc:
        clamp_tempo(float("nan"))
    assert exc.value.code == "not_finite"


def test_clamp_tempo_rejects_infinity():
    with pytest.raises(TempoError) as exc:
        clamp_tempo(float("inf"))
    assert exc.value.code == "not_finite"
    with pytest.raises(TempoError) as exc:
        clamp_tempo(float("-inf"))
    assert exc.value.code == "not_finite"


def test_clamp_tempo_rejects_str():
    with pytest.raises(TempoError) as exc:
        clamp_tempo("1.0")
    assert exc.value.code == "invalid_type"


def test_clamp_tempo_rejects_none():
    with pytest.raises(TempoError) as exc:
        clamp_tempo(None)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 2. step_tempo tests
# =============================================================================


def test_step_tempo_up_one():
    assert step_tempo(1.0, +1) == 1.1


def test_step_tempo_down_one():
    assert step_tempo(1.1, -1) == 1.0


def test_step_tempo_ten_steps_up_from_1_0_reaches_2_0_exactly():
    current = 1.0
    for _ in range(10):
        current = step_tempo(current, +1)
    assert current == 2.0


def test_step_tempo_ten_steps_down_from_1_0_reaches_0_5_exactly():
    current = 1.0
    for _ in range(10):
        current = step_tempo(current, -1)
    assert current == 0.5


def test_step_tempo_clamped_at_max():
    assert step_tempo(2.0, +1) == 2.0
    assert step_tempo(1.95, +1) == 2.0


def test_step_tempo_clamped_at_min():
    assert step_tempo(0.5, -1) == 0.5
    assert step_tempo(0.55, -1) == 0.5


def test_step_tempo_rejects_invalid_direction():
    with pytest.raises(TempoError) as exc:
        step_tempo(1.0, 0)
    assert exc.value.code == "invalid_direction"
    with pytest.raises(TempoError) as exc:
        step_tempo(1.0, 2)
    assert exc.value.code == "invalid_direction"
    with pytest.raises(TempoError) as exc:
        step_tempo(1.0, -2)
    assert exc.value.code == "invalid_direction"


def test_step_tempo_rejects_bool_for_current():
    with pytest.raises(TempoError) as exc:
        step_tempo(True, +1)
    assert exc.value.code == "invalid_type"


def test_step_tempo_rejects_nan():
    with pytest.raises(TempoError) as exc:
        step_tempo(float("nan"), +1)
    assert exc.value.code == "not_finite"


def test_step_tempo_rejects_infinity():
    with pytest.raises(TempoError) as exc:
        step_tempo(float("inf"), +1)
    assert exc.value.code == "not_finite"


def test_step_tempo_rejects_str():
    with pytest.raises(TempoError) as exc:
        step_tempo("1.0", +1)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 3. seek tests
# =============================================================================


def test_seek_forward():
    assert seek(1000, 500, 10000) == 1500


def test_seek_backward():
    assert seek(1000, -500, 10000) == 500


def test_seek_clamped_at_zero():
    assert seek(100, -500, 10000) == 0
    assert seek(0, -1000, 10000) == 0


def test_seek_clamped_at_duration():
    assert seek(9500, 1000, 10000) == 10000
    assert seek(10000, 500, 10000) == 10000


def test_seek_rejects_negative_duration():
    with pytest.raises(TempoError) as exc:
        seek(1000, 500, -1)
    assert exc.value.code == "negative_duration"


def test_seek_rejects_bool_for_position():
    with pytest.raises(TempoError) as exc:
        seek(True, 500, 10000)
    assert exc.value.code == "invalid_type"


def test_seek_rejects_bool_for_delta():
    with pytest.raises(TempoError) as exc:
        seek(1000, True, 10000)
    assert exc.value.code == "invalid_type"


def test_seek_rejects_bool_for_duration():
    with pytest.raises(TempoError) as exc:
        seek(1000, 500, True)
    assert exc.value.code == "invalid_type"


def test_seek_rejects_str():
    with pytest.raises(TempoError) as exc:
        seek("1000", 500, 10000)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 4. jump_back tests
# =============================================================================


def test_jump_back_basic():
    assert jump_back(1500, 2.0) == 0  # 1500 - 2000 = -500 -> clamped to 0
    assert jump_back(10000, 2.5) == 7500  # 10000 - 2500 = 7500


def test_jump_back_clamped_at_zero():
    assert jump_back(500, 1.0) == 0
    assert jump_back(0, 5.0) == 0


def test_jump_back_zero_seconds():
    assert jump_back(5000, 0.0) == 5000


def test_jump_back_rejects_negative_seconds():
    with pytest.raises(TempoError) as exc:
        jump_back(1000, -1.0)
    assert exc.value.code == "negative_seconds"


def test_jump_back_rejects_bool_for_position():
    with pytest.raises(TempoError) as exc:
        jump_back(True, 1.0)
    assert exc.value.code == "invalid_type"


def test_jump_back_rejects_bool_for_seconds():
    with pytest.raises(TempoError) as exc:
        jump_back(1000, True)
    assert exc.value.code == "invalid_type"


def test_jump_back_rejects_str():
    with pytest.raises(TempoError) as exc:
        jump_back("1000", 1.0)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 5. wall_to_media_ms and media_to_wall_ms tests
# =============================================================================


def test_wall_to_media_ms_basic():
    assert wall_to_media_ms(1000.0, 2.0) == 2000.0
    assert wall_to_media_ms(500.0, 0.5) == 250.0
    assert wall_to_media_ms(1000.0, 1.0) == 1000.0
    assert wall_to_media_ms(1000.0, 1.3) == 1300.0


def test_wall_to_media_ms_rejects_non_positive_tempo():
    with pytest.raises(TempoError) as exc:
        wall_to_media_ms(1000.0, 0.0)
    assert exc.value.code == "non_positive_tempo"
    with pytest.raises(TempoError) as exc:
        wall_to_media_ms(1000.0, -1.0)
    assert exc.value.code == "non_positive_tempo"


def test_media_to_wall_ms_basic():
    assert media_to_wall_ms(2000.0, 2.0) == 1000.0
    assert media_to_wall_ms(250.0, 0.5) == 500.0
    assert media_to_wall_ms(1000.0, 1.0) == 1000.0
    assert media_to_wall_ms(1300.0, 1.3) == 1000.0


def test_media_to_wall_ms_rejects_non_positive_tempo():
    with pytest.raises(TempoError) as exc:
        media_to_wall_ms(1000.0, 0.0)
    assert exc.value.code == "non_positive_tempo"
    with pytest.raises(TempoError) as exc:
        media_to_wall_ms(1000.0, -1.0)
    assert exc.value.code == "non_positive_tempo"


def test_roundtrip_wall_media_wall():
    for tempo in (0.5, 1.0, 1.3, 2.0):
        for wall in (0.0, 1.0, 100.0, 1000.0, 12345.6):
            media = wall_to_media_ms(wall, tempo)
            back = media_to_wall_ms(media, tempo)
            assert abs(back - wall) < 1e-9, f"roundtrip failed for wall={wall}, tempo={tempo}: got {back}"


def test_roundtrip_media_wall_media():
    for tempo in (0.5, 1.0, 1.3, 2.0):
        for media in (0.0, 1.0, 100.0, 1000.0, 12345.6):
            wall = media_to_wall_ms(media, tempo)
            back = wall_to_media_ms(wall, tempo)
            assert abs(back - media) < 1e-9, f"roundtrip failed for media={media}, tempo={tempo}: got {back}"


def test_wall_to_media_rejects_bool():
    with pytest.raises(TempoError) as exc:
        wall_to_media_ms(True, 1.0)
    assert exc.value.code == "invalid_type"
    with pytest.raises(TempoError) as exc:
        wall_to_media_ms(1000.0, True)
    assert exc.value.code == "invalid_type"


def test_media_to_wall_rejects_bool():
    with pytest.raises(TempoError) as exc:
        media_to_wall_ms(True, 1.0)
    assert exc.value.code == "invalid_type"
    with pytest.raises(TempoError) as exc:
        media_to_wall_ms(1000.0, True)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 6. resume_position tests
# =============================================================================


def test_resume_position_none_returns_zero():
    assert resume_position(None, 10000) == 0
    assert resume_position(None, 0) == 0


def test_resume_position_clamped_to_duration():
    assert resume_position(15000, 10000) == 10000
    assert resume_position(10000, 10000) == 10000
    assert resume_position(5000, 10000) == 5000


def test_resume_position_subtracts_back_ms():
    assert resume_position(5000, 10000, 1000) == 4000
    assert resume_position(10000, 10000, 5000) == 5000


def test_resume_position_back_ms_clamped_at_zero():
    assert resume_position(500, 10000, 1000) == 0
    assert resume_position(0, 10000, 5000) == 0


def test_resume_position_rejects_negative_duration():
    with pytest.raises(TempoError) as exc:
        resume_position(0, -1)
    assert exc.value.code == "negative_duration"


def test_resume_position_rejects_negative_back_ms():
    with pytest.raises(TempoError) as exc:
        resume_position(0, 10000, -1)
    assert exc.value.code == "negative_back_ms"


def test_resume_position_rejects_bool_for_saved():
    with pytest.raises(TempoError) as exc:
        resume_position(True, 10000)
    assert exc.value.code == "invalid_type"


def test_resume_position_rejects_bool_for_duration():
    with pytest.raises(TempoError) as exc:
        resume_position(None, True)
    assert exc.value.code == "invalid_type"


def test_resume_position_rejects_bool_for_back():
    with pytest.raises(TempoError) as exc:
        resume_position(0, 10000, True)
    assert exc.value.code == "invalid_type"


# =============================================================================
# 7. rewind_words tests
# =============================================================================


def test_rewind_words_basic():
    starts = [0, 1000, 2000, 3000, 4000]
    # Position in the 3rd word (2000-3000), count=1 -> start of that word (2000)
    assert rewind_words(2500, 1, starts) == 2000
    # Position in the 3rd word, count=2 -> start of previous word (1000)
    assert rewind_words(2500, 2, starts) == 1000
    # Position in the 3rd word, count=3 -> start of word before that (0)
    assert rewind_words(2500, 3, starts) == 0


def test_rewind_words_fewer_words_than_count_returns_first():
    starts = [0, 1000, 2000]
    assert rewind_words(2500, 5, starts) == 0


def test_rewind_words_at_exact_word_boundary():
    starts = [0, 1000, 2000, 3000]
    # Position exactly at word start counts as that word
    assert rewind_words(2000, 1, starts) == 2000
    assert rewind_words(2000, 2, starts) == 1000


def test_rewind_words_before_first_word_returns_first():
    starts = [1000, 2000, 3000]
    assert rewind_words(500, 1, starts) == 1000


def test_rewind_words_rejects_none():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, None)


def test_rewind_words_rejects_empty_list():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [])


def test_rewind_words_rejects_unsorted_list():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [0, 2000, 1000])


def test_rewind_words_rejects_duplicate_entries():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [0, 1000, 1000, 2000])


def test_rewind_words_rejects_negative_entry():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [-100, 0, 1000])


def test_rewind_words_rejects_non_int_entry():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [0, 1000.5, 2000])


def test_rewind_words_rejects_bool_entry():
    with pytest.raises(NoAlignment):
        rewind_words(1000, 1, [0, True, 2000])


def test_rewind_words_rejects_non_positive_count():
    with pytest.raises(TempoError) as exc:
        rewind_words(1000, 0, [0, 1000])
    assert exc.value.code == "non_positive_count"
    with pytest.raises(TempoError) as exc:
        rewind_words(1000, -1, [0, 1000])
    assert exc.value.code == "non_positive_count"


def test_rewind_words_rejects_bool_for_position():
    with pytest.raises(TempoError) as exc:
        rewind_words(True, 1, [0, 1000])
    assert exc.value.code == "invalid_type"


def test_rewind_words_rejects_bool_for_count():
    with pytest.raises(TempoError) as exc:
        rewind_words(1000, True, [0, 1000])
    assert exc.value.code == "invalid_type"


def test_rewind_words_rejects_str_for_position():
    with pytest.raises(TempoError) as exc:
        rewind_words("1000", 1, [0, 1000])
    assert exc.value.code == "invalid_type"