"""Tests for src/clinic/rtf_text: RTF and plain-text conversion."""

import time

import pytest

from src.clinic.rtf_text import (
    RtfError,
    load_text_file,
    rtf_to_text,
    save_text_file,
    text_to_rtf,
)

ROUND_TRIP_TEXTS = [
    "Grüezi, Müller & Söhne {x} \\ end",
    "Bericht \U0001F600 mit Emoji \U0001F4C4 und 💬",
    "Winkel Ω und Summe Σ und 泣",
    "",
    "L1\n\nL3\n\n\nL6\n\nL8\n\nL10",
    "Tab\there\t\tand there",
    "a\n b\t9-x end",
]


# --- criterion 1: header, font table, escaping, \par, \tab ------------------


def test_output_starts_with_rtf_header_and_font_table():
    rtf = text_to_rtf("x")
    assert rtf.startswith("{\\rtf1\\ansi\\ansicpg1252")
    assert "{\\fonttbl" in rtf


def test_special_characters_are_escaped():
    body = text_to_rtf("a\\b{c}d")
    assert body.startswith("{\\rtf1")
    assert body.endswith("a\\\\b\\{c\\}d}")


def test_newline_becomes_par_and_tab_becomes_tab():
    assert text_to_rtf("a\nb\tc").endswith("a\\par{}b\\tab{}c}")


def test_crlf_and_cr_are_normalised_to_lf_before_conversion():
    assert text_to_rtf("a\r\nb\rc") == text_to_rtf("a\nb\nc")


def test_blank_lines_survive_conversion():
    rtf = text_to_rtf("a\n\nb")
    assert rtf.endswith("a\\par\\par{}b}")


def test_control_characters_raise():
    for bad in ("a\x00b", "a\x01b", "a\x1fb", "a\x7fb", "a\x0bb"):
        with pytest.raises(RtfError) as excinfo:
            text_to_rtf(bad)
        assert excinfo.value.code == "invalid_text"


def test_non_string_text_raises():
    with pytest.raises(RtfError) as excinfo:
        text_to_rtf(5)
    assert excinfo.value.code == "invalid_text"


# --- criterion 2: cp1252 and unicode escapes --------------------------------


@pytest.mark.parametrize(
    "ch,hh",
    [
        ("ä", "e4"),
        ("ö", "f6"),
        ("ü", "fc"),
        ("Ä", "c4"),
        ("Ö", "d6"),
        ("Ü", "dc"),
        ("ß", "df"),
        ("é", "e9"),
        ("€", "80"),
    ],
)
def test_cp1252_characters_are_written_as_hex_escape(ch, hh):
    assert text_to_rtf(ch).endswith("\\'" + hh + "}")


def test_out_of_cp1252_character_is_signed_16_bit_escape():
    rtf = text_to_rtf("Ω")
    assert "\\u937?" in rtf


def test_character_at_signed_16_bit_boundary_is_negative():
    rtf = text_to_rtf("\u8000")
    assert "\\u-32768?" in rtf


def test_character_above_ffff_becomes_surrogate_pair():
    rtf = text_to_rtf("\U0001F600")
    assert "\\u-10179?\\u-8704?" in rtf


# --- criterion 3: round trips -----------------------------------------------


@pytest.mark.parametrize("text", ROUND_TRIP_TEXTS)
def test_round_trip(text):
    assert rtf_to_text(text_to_rtf(text)) == text


def test_round_trip_preserves_space_after_control_word():
    text = "Zeile eins\n eingerückt\t9 weitere"
    assert rtf_to_text(text_to_rtf(text)) == text


# --- criterion 4: reader behaviour ------------------------------------------


@pytest.mark.parametrize(
    "rtf,expected",
    [
        ("{\\rtf1{\\fonttbl{\\f0\\fnil Arial;}}Hi}", "Hi"),
        ("{\\rtf1{\\colortbl;\\red0\\green0\\blue0;}Hi}", "Hi"),
        ("{\\rtf1{\\stylesheet{\\s1 Normal;}}Hi}", "Hi"),
        ("{\\rtf1{\\info{\\title Geheim}{\\author X}}Hi}", "Hi"),
        ("{\\rtf1{\\*\\generator Test}{\\*\\ds2dictation}Hi}", "Hi"),
        ("{\\rtf1\\'e4\\'80}", "ä€"),
        ("{\\rtf1\\u937?}", "Ω"),
        ("{\\rtf1\\uc0\\u937}", "Ω"),
        ("{\\rtf1\\uc2\\u937??}", "Ω"),
        ("{\\rtf1\\u-10179?\\u-8704?}", "\U0001F600"),
        ("{\\rtf1\\u55357?\\u56832?}", "\U0001F600"),
        ("{\\rtf1a\\par b\\line c\\tab d}", "a\nb\nc\td"),
        ("{\\rtf1\\foo bar}", "bar"),
        ("{\\rtf1\\b1 x}", "x"),
        ("{\\rtf1\\{x\\}}", "{x}"),
        ("{\\rtf1\\\\}", "\\"),
        ("{\\rtf1\\uc0\\u937\\uc1 x}", "Ωx"),
    ],
)
def test_reader_behaviour(rtf, expected):
    assert rtf_to_text(rtf) == expected


def test_reader_ignores_unknown_control_word_with_parameter():
    assert rtf_to_text("{\\rtf1\\pard\\sa200 Title}") == "Title"


def test_reader_rejects_text_with_destination_group_only():
    assert rtf_to_text(text_to_rtf("")) == ""


# --- criterion 5: error codes ------------------------------------------------


@pytest.mark.parametrize("rtf", ["{\\rtf1 open", "{\\rtf1 a}b}", "{\\rtf1{\\fonttbl x"])
def test_unbalanced_braces_raise_malformed(rtf):
    with pytest.raises(RtfError) as excinfo:
        rtf_to_text(rtf)
    assert excinfo.value.code == "malformed"


def test_input_longer_than_max_chars_raises_too_large():
    rtf = text_to_rtf("x" * 100)
    with pytest.raises(RtfError) as excinfo:
        rtf_to_text(rtf, max_chars=50)
    assert excinfo.value.code == "too_large"


def test_bad_max_chars_type_raises_invalid_data():
    with pytest.raises(RtfError) as excinfo:
        rtf_to_text("{\\rtf1}", max_chars=True)
    assert excinfo.value.code == "invalid_data"


@pytest.mark.parametrize("rtf", ["plain text", "{foo}", "}"])
def test_input_without_rtf_header_raises_not_rtf(rtf):
    with pytest.raises(RtfError) as excinfo:
        rtf_to_text(rtf)
    assert excinfo.value.code == "not_rtf"


def test_non_string_rtf_raises_malformed():
    with pytest.raises(RtfError) as excinfo:
        rtf_to_text(42)
    assert excinfo.value.code == "malformed"


# --- criterion 6: performance guard -----------------------------------------


def test_200k_characters_convert_fast_in_both_directions():
    unit = "Grüezi, Müller & Söhne äöü € \n\t"
    text = (unit * 6452)[:200_000]
    start = time.perf_counter()
    rtf = text_to_rtf(text)
    to_rtf = time.perf_counter() - start
    start = time.perf_counter()
    back = rtf_to_text(rtf)
    to_text = time.perf_counter() - start
    assert back == text
    assert to_rtf < 2.0
    assert to_text < 2.0


def test_50k_emoji_convert_fast_in_both_directions():
    text = "\U0001F600 Ω \U0001F4C4\n" * 10_000
    start = time.perf_counter()
    rtf = text_to_rtf(text)
    to_rtf = time.perf_counter() - start
    start = time.perf_counter()
    back = rtf_to_text(rtf)
    to_text = time.perf_counter() - start
    assert back == text
    assert to_rtf < 2.0
    assert to_text < 2.0


# --- criterion 7: plain text files ------------------------------------------


def test_save_never_writes_bom_and_uses_lf():
    assert save_text_file("ä") == b"\xc3\xa4"
    assert save_text_file("") == b""
    assert save_text_file("a\r\nb\rc") == b"a\nb\nc"


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\xc3\xa4", "ä"),
        (b"\xef\xbb\xbf\xc3\xa4", "ä"),
        (b"\xe4", "ä"),
        (b"ascii", "ascii"),
        (b"", ""),
    ],
)
def test_load_picks_utf8_sig_then_cp1252(data, expected):
    assert load_text_file(data) == expected


def test_load_accepts_utf16_with_bom():
    assert load_text_file("ä".encode("utf-16")) == "ä"
    be = b"\xfe\xff" + "ä".encode("utf-16-be")
    assert load_text_file(be) == "ä"
    assert load_text_file(b"\xff\xfea\x00b\x00") == "ab"


def test_load_rejects_nul_outside_utf16():
    with pytest.raises(RtfError) as excinfo:
        load_text_file(b"a\x00b")
    assert excinfo.value.code == "binary"


def test_load_rejects_undecodable_bytes():
    with pytest.raises(RtfError) as excinfo:
        load_text_file(b"\x81")
    assert excinfo.value.code == "binary"


def test_load_rejects_truncated_utf16():
    with pytest.raises(RtfError) as excinfo:
        load_text_file(b"\xff\xfea")
    assert excinfo.value.code == "binary"


def test_load_accepts_bytes_like():
    assert load_text_file(bytearray(b"\xe4")) == "ä"


def test_save_load_round_trip():
    for text in ROUND_TRIP_TEXTS:
        assert load_text_file(save_text_file(text)) == text


def test_save_rejects_non_string():
    with pytest.raises(RtfError) as excinfo:
        save_text_file(7)
    assert excinfo.value.code == "invalid_data"


def test_load_rejects_non_bytes():
    with pytest.raises(RtfError) as excinfo:
        load_text_file("nope")
    assert excinfo.value.code == "invalid_data"


# --- criterion 8: loss report ------------------------------------------------


def test_lone_surrogate_raises_invalid_text():
    with pytest.raises(RtfError) as excinfo:
        text_to_rtf("a\ud800b")
    assert excinfo.value.code == "invalid_text"
    with pytest.raises(RtfError) as excinfo:
        text_to_rtf("a\udcffb")
    assert excinfo.value.code == "invalid_text"


def test_lone_surrogate_raises_for_save_text_file():
    with pytest.raises(RtfError) as excinfo:
        save_text_file("\ud800")
    assert excinfo.value.code == "invalid_text"
