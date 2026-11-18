"""Tests for src.clinic.recorder_chunks.

They cover the three recorder phases and their transitions, the required
metadata fields, and the strict chunk sequence rules: gapless order from
sequence 0, idempotent repeats, generation guard, container signature of
the first chunk, explicit byte and duration limits without silent
truncation, and the idempotent finalize with count, byte and digest
checks. They do not cover persistence, routes, the live editor or ASR.
"""

import hashlib

import pytest

from src.clinic.recorder_chunks import (
    PHASES,
    SENT,
    ChunkError,
    ChunkResult,
    ChunkSession,
    missing_required,
    next_phase,
)

WEBM = b"\x1a\x45\xdf\xa3"
OGG = b"OggS"


def webm_chunk(payload: bytes = b"audio-payload") -> bytes:
    return WEBM + payload


def session(
    mime: str = "audio/webm",
    *,
    max_bytes: int = 1024,
    max_duration_ms: int | None = None,
    generation: int = 1,
) -> ChunkSession:
    return ChunkSession(
        "sess-1", generation, mime,
        max_bytes=max_bytes, max_duration_ms=max_duration_ms,
    )


class TestNextPhase:
    def test_title_done_moves_to_recording(self):
        assert next_phase("TITLE", "title_done") == "RECORDING"

    def test_stop_moves_to_sending(self):
        assert next_phase("RECORDING", "stop") == "SENDING"

    def test_back_to_recording_moves_from_sending(self):
        assert next_phase("SENDING", "back_to_recording") == "RECORDING"

    def test_sent_is_terminal(self):
        assert next_phase("SENDING", "sent") == SENT
        assert SENT not in PHASES

    @pytest.mark.parametrize(
        ("current", "event"),
        [
            ("TITLE", "stop"),
            ("TITLE", "sent"),
            ("TITLE", "back_to_recording"),
            ("RECORDING", "title_done"),
            ("RECORDING", "back_to_recording"),
            ("RECORDING", "sent"),
            ("SENDING", "title_done"),
            ("SENDING", "stop"),
            ("SENT", "sent"),
            ("SENT", "title_done"),
            ("DRAFT", "title_done"),
            ("RECORDING", "pause"),
        ],
    )
    def test_other_pairs_raise_illegal_transition(self, current, event):
        with pytest.raises(ChunkError) as exc:
            next_phase(current, event)
        assert exc.value.code == "illegal_transition"

    @pytest.mark.parametrize("current", [None, 1, b"TITLE"])
    def test_non_string_phase_raises(self, current):
        with pytest.raises(ChunkError) as exc:
            next_phase(current, "title_done")
        assert exc.value.code == "bad_phase"

    @pytest.mark.parametrize("event", [None, 7, b"stop"])
    def test_non_string_event_raises(self, event):
        with pytest.raises(ChunkError) as exc:
            next_phase("TITLE", event)
        assert exc.value.code == "bad_event"

    def test_chunk_error_is_a_value_error(self):
        assert issubclass(ChunkError, ValueError)
        with pytest.raises(ValueError):
            next_phase("TITLE", "stop")


class TestMissingRequired:
    def test_sending_rules_report_only_absent_fields(self):
        missing = missing_required(
            "SENDING", {"priority": "1"}, {"SENDING": ["priority", "author"]}
        )
        assert missing == ["author"]

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_values_count_as_missing(self, value):
        missing = missing_required(
            "SENDING",
            {"priority": value, "author": value},
            {"SENDING": ["priority", "author"]},
        )
        assert missing == ["priority", "author"]

    def test_present_values_are_not_missing(self):
        missing = missing_required(
            "SENDING",
            {"priority": "128", "author": "müller"},
            {"SENDING": ["priority", "author"]},
        )
        assert missing == []

    def test_missing_order_follows_rule_order(self):
        missing = missing_required("SENDING", {}, {"SENDING": ["z_last", "a_first"]})
        assert missing == ["z_last", "a_first"]

    def test_phase_without_rules_returns_empty(self):
        assert missing_required("TITLE", {}, {}) == []
        assert missing_required("TITLE", {"title": "x"}, {"RECORDING": ["author"]}) == []

    def test_unknown_phase_raises(self):
        with pytest.raises(ChunkError) as exc:
            missing_required("ARCHIVED", {}, {})
        assert exc.value.code == "unknown_phase"

    def test_non_mapping_metadata_raises(self):
        with pytest.raises(ChunkError) as exc:
            missing_required("SENDING", [("priority", "1")], {})
        assert exc.value.code == "bad_metadata"

    def test_non_string_metadata_value_raises(self):
        with pytest.raises(ChunkError) as exc:
            missing_required("SENDING", {"priority": 1}, {})
        assert exc.value.code == "bad_metadata"

    def test_non_mapping_rules_raises(self):
        with pytest.raises(ChunkError) as exc:
            missing_required("SENDING", {}, ["priority"])
        assert exc.value.code == "bad_rules"


class TestSequenceOrder:
    def test_phases_are_the_three_recorder_phases(self):
        assert PHASES == ("TITLE", "RECORDING", "SENDING")

    def test_sequence_starts_at_zero(self):
        result = session().accept(1, 0, webm_chunk())
        assert result.accepted is True
        assert result.duplicate is False
        assert result.next_sequence == 1

    def test_first_chunk_other_than_zero_is_a_gap(self):
        with pytest.raises(ChunkError) as exc:
            session().accept(1, 1, webm_chunk())
        assert exc.value.code == "gap"

    def test_gap_leaves_state_unchanged(self):
        s = session()
        first = s.accept(1, 0, webm_chunk())
        with pytest.raises(ChunkError):
            s.accept(1, 2, webm_chunk())
        result = s.accept(1, 1, webm_chunk())
        assert result.next_sequence == 2
        assert result.remaining_bytes == first.remaining_bytes - len(webm_chunk())

    def test_gap_in_the_middle_leaves_state_unchanged(self):
        s = session()
        s.accept(1, 0, webm_chunk())
        s.accept(1, 1, webm_chunk())
        with pytest.raises(ChunkError):
            s.accept(1, 3, webm_chunk())
        result = s.accept(1, 2, webm_chunk())
        assert result.next_sequence == 3

    def test_negative_sequence_is_rejected(self):
        with pytest.raises(ChunkError) as exc:
            session().accept(1, -1, webm_chunk())
        assert exc.value.code == "bad_sequence"

    def test_bool_sequence_is_rejected(self):
        with pytest.raises(ChunkError) as exc:
            session().accept(1, True, webm_chunk())
        assert exc.value.code == "bad_sequence"


class TestRepeats:
    def test_identical_repeat_is_an_accepted_duplicate(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        result = s.accept(1, 0, data)
        assert result.accepted is True
        assert result.duplicate is True
        assert result.next_sequence == 1

    def test_duplicate_stores_nothing_twice(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        s.accept(1, 0, data)
        concat = s.finalize(
            total_chunks=1, total_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        assert concat == data

    def test_same_sequence_with_different_bytes_conflicts(self):
        s = session()
        s.accept(1, 0, webm_chunk(b"one"))
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 0, webm_chunk(b"two"))
        assert exc.value.code == "conflict"

    def test_conflict_leaves_state_unchanged(self):
        s = session()
        s.accept(1, 0, webm_chunk(b"one"))
        with pytest.raises(ChunkError):
            s.accept(1, 0, webm_chunk(b"two"))
        result = s.accept(1, 1, webm_chunk(b"three"))
        assert result.next_sequence == 2

    def test_conflict_between_later_chunks(self):
        s = session()
        s.accept(1, 0, webm_chunk(b"a"))
        s.accept(1, 1, webm_chunk(b"b"))
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 1, webm_chunk(b"c"))
        assert exc.value.code == "conflict"


class TestGenerations:
    def test_older_generation_is_stale(self):
        s = session(generation=3)
        with pytest.raises(ChunkError) as exc:
            s.accept(2, 0, webm_chunk())
        assert exc.value.code == "stale_generation"

    def test_newer_generation_mismatches(self):
        s = session(generation=3)
        with pytest.raises(ChunkError) as exc:
            s.accept(4, 0, webm_chunk())
        assert exc.value.code == "generation_mismatch"

    def test_matching_generation_is_accepted(self):
        s = session(generation=3)
        assert s.accept(3, 0, webm_chunk()).accepted is True

    def test_stale_generation_beats_sequence_errors(self):
        s = session(generation=2)
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 5, b"not-even-a-header")
        assert exc.value.code == "stale_generation"

    def test_bool_generation_is_rejected(self):
        with pytest.raises(ChunkError) as exc:
            session(generation=True)
        assert exc.value.code == "bad_generation"
        s = session()
        with pytest.raises(ChunkError) as exc:
            s.accept(True, 0, webm_chunk())
        assert exc.value.code == "bad_generation"


class TestFirstChunkHeader:
    @pytest.mark.parametrize(
        ("mime", "head"),
        [
            ("audio/webm", WEBM + b"\x02\x00\x00\x00"),
            ("audio/webm;codecs=opus", WEBM + b"\x02\x00\x00\x00"),
            ("Audio/WebM", WEBM + b"\x02\x00\x00\x00"),
            ("audio/ogg", OGG + b"\x02\x00\x00\x00"),
            ("audio/ogg; codecs=opus", OGG + b"\x02\x00\x00\x00"),
            ("audio/wav", b"RIFF\x24\x00\x00\x00WAVE"),
            ("audio/x-wav", b"RIFF\x24\x00\x00\x00WAVE"),
            ("audio/mp4", b"\x00\x00\x00\x20ftypisom"),
        ],
    )
    def test_matching_signature_is_accepted(self, mime, head):
        s = session(mime=mime)
        assert s.accept(1, 0, head).accepted is True

    @pytest.mark.parametrize(
        ("mime", "head"),
        [
            ("audio/webm", OGG + b"nope"),
            ("audio/webm", b"\x1a\x45\xdf\xa2"),
            ("audio/webm", WEBM[:3]),
            ("audio/ogg", WEBM + b"nope"),
            ("audio/wav", b"RIFF\x24\x00\x00\x00WAVX"),
            ("audio/wav", b"RIFF\x24\x00\x00"),
            ("audio/mp4", b"\x00\x00\x00\x20Xtypisom"),
            ("audio/mp4", b"\x00\x00ftyp"),
        ],
    )
    def test_mismatched_signature_raises_bad_header(self, mime, head):
        s = session(mime=mime)
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 0, head)
        assert exc.value.code == "bad_header"

    def test_later_chunks_need_no_header(self):
        s = session()
        s.accept(1, 0, WEBM + b"init segment")
        result = s.accept(1, 1, b"media fragment bytes without any header")
        assert result.accepted is True
        assert result.next_sequence == 2

    @pytest.mark.parametrize(
        ("mime", "code"),
        [
            ("audio/mpeg", "unsupported_mime"),
            ("audio/flac", "unsupported_mime"),
            ("video/webm", "unsupported_mime"),
            ("text/plain", "unsupported_mime"),
            ("", "bad_mime"),
            ("   ", "bad_mime"),
            ("audio/webm\x00", "bad_mime"),
            (None, "bad_mime"),
        ],
    )
    def test_rejected_mime_at_construction(self, mime, code):
        with pytest.raises(ChunkError) as exc:
            session(mime=mime)
        assert exc.value.code == code


class TestLimits:
    def test_byte_limit_rejects_without_storing(self):
        s = session(max_bytes=10)
        first = s.accept(1, 0, WEBM + b"1234")
        assert first.remaining_bytes == 2
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 1, b"12345")
        assert exc.value.code == "limit_reached"
        second = s.accept(1, 1, b"12")
        assert second.accepted is True
        assert second.remaining_bytes == 0
        assert second.limit_reached is True

    def test_exact_byte_limit_is_accepted(self):
        s = session(max_bytes=8)
        result = s.accept(1, 0, WEBM + b"1234")
        assert result.accepted is True
        assert result.remaining_bytes == 0
        assert result.limit_reached is True

    def test_previous_remaining_bytes_stays_truthful(self):
        s = session(max_bytes=20)
        first = s.accept(1, 0, WEBM + b"1234")
        with pytest.raises(ChunkError):
            s.accept(1, 1, b"0123456789ABCDEFGHIJKLMNOP")
        again = s.accept(1, 1, b"0123456789AB")
        assert again.remaining_bytes == 0
        assert again.limit_reached is True
        assert first.remaining_bytes == 20 - len(WEBM + b"1234")

    def test_duration_limit_rejects_without_storing(self):
        s = session(max_duration_ms=1000)
        first = s.accept(1, 0, webm_chunk(), duration_ms=600)
        assert first.limit_reached is False
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 1, b"fragment", duration_ms=500)
        assert exc.value.code == "limit_reached"
        second = s.accept(1, 1, b"fragment", duration_ms=400)
        assert second.accepted is True
        assert second.limit_reached is True

    def test_duration_limit_keeps_state_after_rejection(self):
        s = session(max_duration_ms=1000)
        s.accept(1, 0, webm_chunk(), duration_ms=700)
        with pytest.raises(ChunkError):
            s.accept(1, 1, b"fragment", duration_ms=700)
        result = s.accept(1, 1, b"fragment", duration_ms=300)
        assert result.next_sequence == 2

    def test_no_partial_chunk_is_stored(self):
        s = session(max_bytes=10)
        data = WEBM + b"1234"
        s.accept(1, 0, data)
        with pytest.raises(ChunkError):
            s.accept(1, 1, b"0123456789ABCDEF")
        concat = s.finalize(
            total_chunks=1, total_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        assert concat == data

    def test_zero_byte_chunk_is_rejected(self):
        with pytest.raises(ChunkError) as exc:
            session().accept(1, 0, b"")
        assert exc.value.code == "bad_data"

    def test_non_bytes_data_is_rejected(self):
        s = session()
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 0, "audio")
        assert exc.value.code == "bad_data"
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 0, memoryview(b"audio"))
        assert exc.value.code == "bad_data"

    def test_bad_duration_values_are_rejected(self):
        s = session()
        for duration_ms in (-5, True, 1.5, float("nan"), float("inf")):
            with pytest.raises(ChunkError) as exc:
                s.accept(1, 0, webm_chunk(), duration_ms=duration_ms)
            assert exc.value.code == "bad_duration"


class TestFinalize:
    def test_finalize_returns_the_concatenation(self):
        s = session()
        first = WEBM + b"one-"
        second = b"two"
        s.accept(1, 0, first)
        s.accept(1, 1, second)
        concat = s.finalize(
            total_chunks=2, total_bytes=len(first) + len(second),
            sha256=hashlib.sha256(first + second).hexdigest(),
        )
        assert concat == first + second

    def test_finalize_rejects_wrong_chunk_count(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        with pytest.raises(ChunkError) as exc:
            s.finalize(
                total_chunks=2, total_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        assert exc.value.code == "incomplete"

    def test_finalize_rejects_wrong_byte_total(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        with pytest.raises(ChunkError) as exc:
            s.finalize(
                total_chunks=1, total_bytes=len(data) + 1,
                sha256=hashlib.sha256(data).hexdigest(),
            )
        assert exc.value.code == "incomplete"

    def test_finalize_rejects_wrong_digest(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        with pytest.raises(ChunkError) as exc:
            s.finalize(
                total_chunks=1, total_bytes=len(data),
                sha256=hashlib.sha256(b"different").hexdigest(),
            )
        assert exc.value.code == "digest_mismatch"

    def test_finalize_is_idempotent(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        digest = hashlib.sha256(data).hexdigest()
        first = s.finalize(total_chunks=1, total_bytes=len(data), sha256=digest)
        second = s.finalize(total_chunks=1, total_bytes=len(data), sha256=digest)
        assert first == second == data

    def test_finalize_twice_with_other_arguments_raises(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        digest = hashlib.sha256(data).hexdigest()
        s.finalize(total_chunks=1, total_bytes=len(data), sha256=digest)
        with pytest.raises(ChunkError) as exc:
            s.finalize(total_chunks=2, total_bytes=len(data), sha256=digest)
        assert exc.value.code == "incomplete"
        with pytest.raises(ChunkError) as exc:
            s.finalize(
                total_chunks=1, total_bytes=len(data),
                sha256=hashlib.sha256(b"x").hexdigest(),
            )
        assert exc.value.code == "digest_mismatch"

    @pytest.mark.parametrize(
        "digest",
        ["", "abcd", "z" * 64, "A" * 63, "g" * 64, None, 123],
    )
    def test_finalize_rejects_malformed_digest(self, digest):
        s = session()
        with pytest.raises(ChunkError) as exc:
            s.finalize(total_chunks=0, total_bytes=0, sha256=digest)
        assert exc.value.code == "bad_digest"

    def test_finalize_accepts_uppercase_digest(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        concat = s.finalize(
            total_chunks=1, total_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest().upper(),
        )
        assert concat == data

    def test_accept_after_finalize_is_rejected(self):
        s = session()
        data = webm_chunk()
        s.accept(1, 0, data)
        s.finalize(
            total_chunks=1, total_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        with pytest.raises(ChunkError) as exc:
            s.accept(1, 1, b"late fragment")
        assert exc.value.code == "already_finalized"


class TestSessionConstruction:
    @pytest.mark.parametrize("session_id", ["", "   ", "sess\x00id", None, 7])
    def test_bad_session_id_is_rejected(self, session_id):
        with pytest.raises(ChunkError) as exc:
            ChunkSession(session_id, 1, "audio/webm", max_bytes=1024)
        assert exc.value.code == "bad_session_id"

    @pytest.mark.parametrize(
        "max_bytes",
        [0, -1, True, 10.5, float("nan"), float("inf"), None],
    )
    def test_bad_max_bytes_is_rejected(self, max_bytes):
        with pytest.raises(ChunkError) as exc:
            ChunkSession("sess-1", 1, "audio/webm", max_bytes=max_bytes)
        assert exc.value.code == "bad_max_bytes"

    @pytest.mark.parametrize("max_duration_ms", [-1, True, 1.5, float("nan")])
    def test_bad_max_duration_is_rejected(self, max_duration_ms):
        with pytest.raises(ChunkError) as exc:
            ChunkSession("sess-1", 1, "audio/webm", max_bytes=1024, max_duration_ms=max_duration_ms)
        assert exc.value.code == "bad_max_duration"

    def test_chunk_result_shape_is_frozen(self):
        result = ChunkResult(True, False, 1, 0, False)
        assert result.accepted is True
        assert result.duplicate is False
        assert result.next_sequence == 1
        assert result.remaining_bytes == 0
        assert result.limit_reached is False
        with pytest.raises(AttributeError):
            result.accepted = False
