"""Tests for src.clinic.audio_format_choice."""

import pytest

from src.clinic.audio_format_choice import (
    SOURCES,
    FormatChoice,
    FormatError,
    choose_upload_format,
    declared_matches,
    parse_mime,
    sample_rate_for,
    source_allowed,
)


class TestParseMime:
    def test_valid_webm_opus(self):
        mime, params = parse_mime('Audio/WebM; codecs="opus"')
        assert mime == "audio/webm"
        assert params == {"codecs": "opus"}

    def test_valid_ogg_opus_no_quotes(self):
        mime, params = parse_mime("audio/ogg;codecs=opus")
        assert mime == "audio/ogg"
        assert params == {"codecs": "opus"}

    def test_valid_mp4_aac(self):
        mime, params = parse_mime('audio/mp4; codecs="mp4a.40.2"')
        assert mime == "audio/mp4"
        assert params == {"codecs": "mp4a.40.2"}

    def test_valid_wav_no_params(self):
        mime, params = parse_mime("audio/wav")
        assert mime == "audio/wav"
        assert params == {}

    def test_whitespace_handling(self):
        mime, params = parse_mime("  audio/webm  ;  codecs = opus  ")
        assert mime == "audio/webm"
        assert params == {"codecs": "opus"}

    def test_empty_raises(self):
        with pytest.raises(FormatError) as exc:
            parse_mime("")
        assert exc.value.code == "bad_mime"

    def test_non_audio_raises(self):
        with pytest.raises(FormatError) as exc:
            parse_mime("video/mp4")
        assert exc.value.code == "bad_mime"

    def test_non_string_raises(self):
        with pytest.raises(FormatError) as exc:
            parse_mime(123)  # type: ignore[arg-type]
        assert exc.value.code == "bad_mime"

    def test_malformed_raises(self):
        with pytest.raises(FormatError) as exc:
            parse_mime("audio/")
        assert exc.value.code == "bad_mime"

    def test_multiple_params(self):
        mime, params = parse_mime('audio/webm; codecs="opus"; rate=48000')
        assert mime == "audio/webm"
        assert params == {"codecs": "opus", "rate": "48000"}


class TestChooseUploadFormat:
    def test_normal_preference_order(self):
        choice = choose_upload_format(["audio/wav", "audio/webm;codecs=opus", "audio/ogg;codecs=opus"])
        assert choice.mime == "audio/webm"
        assert choice.container == "webm"
        assert choice.codec == "opus"
        assert choice.extension == ".webm"

    def test_lossless_preferred_wav_first(self):
        choice = choose_upload_format(["audio/wav", "audio/webm;codecs=opus"], lossless_preferred=True)
        assert choice.mime == "audio/wav"
        assert choice.container == "wav"
        assert choice.codec is None
        assert choice.extension == ".wav"

    def test_lossless_preferred_fallback_when_no_wav(self):
        choice = choose_upload_format(["audio/webm;codecs=opus", "audio/ogg;codecs=opus"], lossless_preferred=True)
        assert choice.mime == "audio/webm"
        assert choice.codec == "opus"

    def test_unknown_mime_skipped(self):
        choice = choose_upload_format(["audio/unknown", "audio/webm;codecs=opus"])
        assert choice.mime == "audio/webm"

    def test_all_unknown_raises(self):
        with pytest.raises(FormatError) as exc:
            choose_upload_format(["audio/unknown", "audio/x-custom"])
        assert exc.value.code == "no_supported_format"

    def test_empty_list_raises(self):
        with pytest.raises(FormatError) as exc:
            choose_upload_format([])
        assert exc.value.code == "no_supported_format"

    def test_non_sequence_raises(self):
        with pytest.raises(FormatError) as exc:
            choose_upload_format("audio/webm")  # type: ignore[arg-type]
        assert exc.value.code == "bad_mime"

    def test_non_string_element_raises(self):
        with pytest.raises(FormatError) as exc:
            choose_upload_format([123])  # type: ignore[list-item]
        assert exc.value.code == "bad_mime"

    def test_mp4_aac_chosen(self):
        choice = choose_upload_format(["audio/mp4;codecs=mp4a.40.2"])
        assert choice.mime == "audio/mp4"
        assert choice.container == "mp4"
        assert choice.codec == "mp4a.40.2"
        assert choice.extension == ".m4a"

    def test_case_insensitive_mime(self):
        choice = choose_upload_format(["AUDIO/WEBM;CODECS=OPUS"])
        assert choice.mime == "audio/webm"

    def test_duplicate_entries_ignored(self):
        choice = choose_upload_format(["audio/webm;codecs=opus", "audio/webm;codecs=opus"])
        assert choice.mime == "audio/webm"


class TestDeclaredMatches:
    def test_webm_ebml_match(self):
        head = b"\x1a\x45\xdf\xa3" + b"\x00" * 8
        assert declared_matches("audio/webm;codecs=opus", head) is True

    def test_webm_wrong_bytes(self):
        head = b"\x00\x00\x00\x00" + b"\x00" * 8
        assert declared_matches("audio/webm", head) is False

    def test_ogg_match(self):
        head = b"OggS" + b"\x00" * 8
        assert declared_matches("audio/ogg;codecs=opus", head) is True

    def test_wav_match(self):
        head = b"RIFF\x00\x00\x00\x00WAVE"
        assert declared_matches("audio/wav", head) is True

    def test_wav_missing_wave_at_offset_8(self):
        head = b"RIFF\x00\x00\x00\x00XXXX"
        assert declared_matches("audio/wav", head) is False

    def test_mp4_match(self):
        head = b"\x00\x00\x00\x00ftyp" + b"\x00" * 7
        assert declared_matches("audio/mp4;codecs=mp4a.40.2", head) is True

    def test_m4a_match(self):
        head = b"\x00\x00\x00\x00ftyp" + b"\x00" * 7
        assert declared_matches("audio/m4a", head) is True

    def test_head_too_short(self):
        head = b"\x1a\x45\xdf\xa3"
        assert declared_matches("audio/webm", head) is False

    def test_unknown_mime_false(self):
        head = b"\x1a\x45\xdf\xa3" + b"\x00" * 8
        assert declared_matches("audio/unknown", head) is False

    def test_non_string_mime_false(self):
        head = b"\x1a\x45\xdf\xa3" + b"\x00" * 8
        assert declared_matches(123, head) is False  # type: ignore[arg-type]

    def test_non_bytes_head_false(self):
        assert declared_matches("audio/webm", "not bytes") is False  # type: ignore[arg-type]


class TestSampleRateFor:
    def test_opus_16000_or_below(self):
        choice = FormatChoice("audio/webm", "webm", "opus", ".webm")
        assert sample_rate_for(choice, 8000) == 16000
        assert sample_rate_for(choice, 16000) == 16000

    def test_opus_above_16000(self):
        choice = FormatChoice("audio/webm", "webm", "opus", ".webm")
        assert sample_rate_for(choice, 24000) == 48000
        assert sample_rate_for(choice, 44100) == 48000
        assert sample_rate_for(choice, 48000) == 48000
        assert sample_rate_for(choice, 96000) == 48000

    def test_non_opus_clamp_low(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        assert sample_rate_for(choice, 8000) == 16000
        assert sample_rate_for(choice, 16000) == 16000

    def test_non_opus_clamp_high(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        assert sample_rate_for(choice, 48000) == 48000
        assert sample_rate_for(choice, 96000) == 48000

    def test_non_opus_passthrough(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        assert sample_rate_for(choice, 22050) == 22050
        assert sample_rate_for(choice, 44100) == 44100

    def test_bool_raises(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        with pytest.raises(FormatError) as exc:
            sample_rate_for(choice, True)  # type: ignore[arg-type]
        assert exc.value.code == "bad_rate"

    def test_zero_raises(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        with pytest.raises(FormatError) as exc:
            sample_rate_for(choice, 0)
        assert exc.value.code == "bad_rate"

    def test_negative_raises(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        with pytest.raises(FormatError) as exc:
            sample_rate_for(choice, -1)
        assert exc.value.code == "bad_rate"

    def test_float_raises(self):
        choice = FormatChoice("audio/wav", "wav", None, ".wav")
        with pytest.raises(FormatError) as exc:
            sample_rate_for(choice, 16000.0)  # type: ignore[arg-type]
        assert exc.value.code == "bad_rate"


class TestSourceAllowed:
    def test_allowed_exact_match(self):
        assert source_allowed({"browser_recording", "file_upload"}, "browser_recording") is True

    def test_not_allowed_returns_false(self):
        assert source_allowed({"browser_recording"}, "file_upload") is False

    def test_unknown_source_raises(self):
        with pytest.raises(FormatError) as exc:
            source_allowed({"browser_recording"}, "unknown_source")
        assert exc.value.code == "unknown_source"

    def test_non_string_raises(self):
        with pytest.raises(FormatError) as exc:
            source_allowed({"browser_recording"}, 123)  # type: ignore[arg-type]
        assert exc.value.code == "unknown_source"

    def test_all_sources_constant(self):
        assert SOURCES == ("browser_recording", "device_import", "file_upload", "live_session")

    def test_case_sensitive_raises(self):
        with pytest.raises(FormatError) as exc:
            source_allowed({"browser_recording"}, "Browser_Recording")
        assert exc.value.code == "unknown_source"


class TestFormatChoiceDataclass:
    def test_frozen(self):
        choice = FormatChoice("audio/webm", "webm", "opus", ".webm")
        with pytest.raises(Exception):
            choice.mime = "audio/ogg"  # type: ignore[misc]

    def test_equality(self):
        c1 = FormatChoice("audio/webm", "webm", "opus", ".webm")
        c2 = FormatChoice("audio/webm", "webm", "opus", ".webm")
        assert c1 == c2