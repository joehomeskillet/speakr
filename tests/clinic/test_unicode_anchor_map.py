"""Tests for src.clinic.unicode_anchor_map."""

import pytest

from src.clinic.revisions import normalise_text
from src.clinic.unicode_anchor_map import (
    OffsetError,
    map_through_normalisation,
    to_codepoint_offset,
    to_utf16_offset,
    utf16_length,
)


class TestToUtf16Offset:
    def test_basic_ascii(self):
        assert to_utf16_offset("hello", 0) == 0
        assert to_utf16_offset("hello", 1) == 1
        assert to_utf16_offset("hello", 5) == 5

    def test_emoji_surrogate_pair(self):
        # 'a😀b' - emoji is U+1F600, takes 2 UTF-16 code units
        assert to_utf16_offset("a😀b", 0) == 0  # before 'a'
        assert to_utf16_offset("a😀b", 1) == 1  # after 'a'
        assert to_utf16_offset("a😀b", 2) == 3  # after 😀 (1 + 2)
        assert to_utf16_offset("a😀b", 3) == 4  # after 'b' (end)

    def test_umlauts(self):
        # 'Grüezi' - ü is U+00FC, 1 UTF-16 unit
        assert to_utf16_offset("Grüezi", 0) == 0
        assert to_utf16_offset("Grüezi", 2) == 2  # after 'Gr'
        assert to_utf16_offset("Grüezi", 3) == 3  # after 'ü'
        assert to_utf16_offset("Grüezi", 6) == 6  # end

    def test_combining_characters(self):
        # 'e\u0301x' - e + combining acute = 2 code points, 2 UTF-16 units
        assert to_utf16_offset("e\u0301x", 0) == 0
        assert to_utf16_offset("e\u0301x", 1) == 1
        assert to_utf16_offset("e\u0301x", 2) == 2
        assert to_utf16_offset("e\u0301x", 3) == 3

    def test_empty_string(self):
        assert to_utf16_offset("", 0) == 0

    def test_invalid_text_type(self):
        with pytest.raises(TypeError):
            to_utf16_offset(123, 0)
        with pytest.raises(TypeError):
            to_utf16_offset(None, 0)

    def test_invalid_offset_type(self):
        with pytest.raises(OffsetError) as exc:
            to_utf16_offset("hello", "2")
        assert exc.value.code == "invalid_type"
        with pytest.raises(OffsetError) as exc:
            to_utf16_offset("hello", True)
        assert exc.value.code == "invalid_type"
        with pytest.raises(OffsetError) as exc:
            to_utf16_offset("hello", 2.5)
        assert exc.value.code == "invalid_type"

    def test_negative_offset(self):
        with pytest.raises(OffsetError) as exc:
            to_utf16_offset("hello", -1)
        assert exc.value.code == "out_of_range"

    def test_offset_exceeds_length(self):
        with pytest.raises(OffsetError) as exc:
            to_utf16_offset("hello", 6)
        assert exc.value.code == "out_of_range"


class TestToCodepointOffset:
    def test_basic_ascii(self):
        assert to_codepoint_offset("hello", 0) == 0
        assert to_codepoint_offset("hello", 1) == 1
        assert to_codepoint_offset("hello", 5) == 5

    def test_emoji_surrogate_pair(self):
        # 'a😀b' - code points: a, 😀, b (3 code points)
        # UTF-16: a=1, 😀=2 (surrogate pair), b=1 → total 4 UTF-16 units
        # UTF-16 offsets: 0, 1, 2 (1st surrogate), 3 (2nd surrogate), 4
        # Per acceptance criteria: to_codepoint_offset('a😀b', 2) raises (inside pair)
        # 1 -> 1, 3 -> 2
        assert to_codepoint_offset("a😀b", 0) == 0
        assert to_codepoint_offset("a😀b", 1) == 1  # after 'a'
        assert to_codepoint_offset("a😀b", 3) == 2  # after 😀 (end of surrogate pair)
        assert to_codepoint_offset("a😀b", 4) == 3  # end

    def test_inside_surrogate_pair_raises(self):
        # UTF-16 offset 2 is inside the surrogate pair for 😀 (1st surrogate)
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("a😀b", 2)
        assert exc.value.code == "inside_surrogate_pair"

    def test_umlauts(self):
        assert to_codepoint_offset("Grüezi", 0) == 0
        assert to_codepoint_offset("Grüezi", 2) == 2
        assert to_codepoint_offset("Grüezi", 3) == 3
        assert to_codepoint_offset("Grüezi", 6) == 6

    def test_combining_characters(self):
        assert to_codepoint_offset("e\u0301x", 0) == 0
        assert to_codepoint_offset("e\u0301x", 1) == 1
        assert to_codepoint_offset("e\u0301x", 2) == 2
        assert to_codepoint_offset("e\u0301x", 3) == 3

    def test_empty_string(self):
        assert to_codepoint_offset("", 0) == 0

    def test_invalid_text_type(self):
        with pytest.raises(TypeError):
            to_codepoint_offset(123, 0)

    def test_invalid_offset_type(self):
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("hello", "2")
        assert exc.value.code == "invalid_type"
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("hello", True)
        assert exc.value.code == "invalid_type"

    def test_negative_offset(self):
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("hello", -1)
        assert exc.value.code == "out_of_range"

    def test_offset_exceeds_utf16_length(self):
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("hello", 6)
        assert exc.value.code == "out_of_range"
        # For emoji, UTF-16 length is longer
        with pytest.raises(OffsetError) as exc:
            to_codepoint_offset("😀", 3)
        assert exc.value.code == "out_of_range"


class TestMapThroughNormalisation:
    def test_crlf_becomes_lf(self):
        # 'a\r\nb' → 'a\nb', offset 3 (after \r\n) maps to 2 (after \n)
        assert map_through_normalisation("a\r\nb", 3) == 2

    def test_lone_cr_becomes_lf(self):
        # 'a\rb' → 'a\nb'
        assert map_through_normalisation("a\rb", 2) == 2

    def test_nfc_composition(self):
        # 'e\u0301x' → 'éx' (NFC composes e + combining acute into é)
        # offset 2 in raw (after e + combining) maps to 1 in normalised (after é)
        assert map_through_normalisation("e\u0301x", 2) == 1

    def test_offsets_inside_composed_sequence_map_to_start(self):
        # offset 1 (after 'e' but before combining acute) maps to 0 (start of composed char)
        assert map_through_normalisation("e\u0301x", 1) == 0
        # offset 0 maps to 0
        assert map_through_normalisation("e\u0301x", 0) == 0
        # offset 3 (after 'x') maps to 2 (after 'x' in normalised)
        assert map_through_normalisation("e\u0301x", 3) == 2

    def test_monotone_non_decreasing(self):
        test_strings = [
            "Grüezi",
            "Größe",
            "e\u0301x",
            "👨‍👩‍👧x",
            "a\r\nb",
            "日本語😀",
            "",
            "a\rb\nc\r\nd",
            "hello\u0301\u0302world",  # multiple combining marks
        ]
        for s in test_strings:
            raw_len = len(s)
            norm_len = len(normalise_text(s))
            prev = -1
            for i in range(raw_len + 1):
                mapped = map_through_normalisation(s, i)
                assert mapped >= prev, f"not monotone for {s!r} at offset {i}: {mapped} < {prev}"
                prev = mapped
            assert map_through_normalisation(s, raw_len) == norm_len

    def test_end_offset_maps_to_normalised_length(self):
        from src.clinic.revisions import normalise_text
        for s in ["Grüezi", "Größe", "e\u0301x", "👨‍👩‍👧x", "a\r\nb", "日本語😀", ""]:
            raw_len = len(s)
            norm_len = len(normalise_text(s))
            assert map_through_normalisation(s, raw_len) == norm_len

    def test_round_trip_through_utf16(self):
        test_strings = [
            "Grüezi",
            "Größe",
            "e\u0301x",
            "👨‍👩‍👧x",
            "a\r\nb",
            "日本語😀",
            "",
        ]
        for s in test_strings:
            for cp in range(len(s) + 1):
                utf16 = to_utf16_offset(s, cp)
                back = to_codepoint_offset(s, utf16)
                assert back == cp, f"round trip failed for {s!r} at cp {cp}: got {back}"

    def test_invalid_text_type(self):
        with pytest.raises(TypeError):
            map_through_normalisation(123, 0)

    def test_invalid_offset_type(self):
        with pytest.raises(OffsetError) as exc:
            map_through_normalisation("hello", "2")
        assert exc.value.code == "invalid_type"
        with pytest.raises(OffsetError) as exc:
            map_through_normalisation("hello", True)
        assert exc.value.code == "invalid_type"

    def test_negative_offset(self):
        with pytest.raises(OffsetError) as exc:
            map_through_normalisation("hello", -1)
        assert exc.value.code == "out_of_range"

    def test_offset_exceeds_length(self):
        with pytest.raises(OffsetError) as exc:
            map_through_normalisation("hello", 6)
        assert exc.value.code == "out_of_range"


class TestUtf16Length:
    def test_emoji(self):
        assert utf16_length("😀") == 2

    def test_latin1(self):
        assert utf16_length("é") == 1
        assert utf16_length("ü") == 1
        assert utf16_length("ß") == 1

    def test_ascii(self):
        assert utf16_length("hello") == 5

    def test_mixed(self):
        assert utf16_length("a😀b") == 4
        assert utf16_length("Grüezi") == 6
        assert utf16_length("日本語") == 3

    def test_empty(self):
        assert utf16_length("") == 0

    def test_invalid_type(self):
        with pytest.raises(TypeError):
            utf16_length(123)