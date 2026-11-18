"""Server-side recorder phases and chunk sequence validation.

Scope: the three recorder phases (``TITLE``, ``RECORDING``, ``SENDING``)
with their allowed transitions and required metadata fields, and strict
in-memory validation of an uploaded chunk sequence: gapless order starting
at sequence 0, the container signature of the first chunk, idempotent
repeats, the session generation guard, and explicit byte and duration
limits without silent truncation. ``finalize`` verifies chunk count, byte
total and the sha256 of the concatenation and is idempotent.

Non-scope: persistence, Flask routes, the live editor, ASR and approval.
The session holds chunks in memory only; a later wiring package persists
and routes them. Approval (Abnahme) and speech recognition are not
touched by any code path here.

``ChunkError.code`` is a stable identifier. Codes: illegal_transition,
unknown_phase, bad_phase, bad_event, bad_metadata, bad_rules,
bad_session_id, bad_generation, bad_mime, unsupported_mime, bad_max_bytes,
bad_max_duration, bad_sequence, bad_data, bad_duration, gap, conflict,
stale_generation, generation_mismatch, bad_header, limit_reached,
already_finalized, bad_count, bad_bytes, bad_digest, incomplete,
digest_mismatch.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

PHASES = ("TITLE", "RECORDING", "SENDING")
SENT = "SENT"

_TRANSITIONS = {
    ("TITLE", "title_done"): "RECORDING",
    ("RECORDING", "stop"): "SENDING",
    ("SENDING", "back_to_recording"): "RECORDING",
    ("SENDING", "sent"): SENT,
}


def _is_webm(data: bytes) -> bool:
    return data[:4] == b"\x1a\x45\xdf\xa3"


def _is_ogg(data: bytes) -> bool:
    return data[:4] == b"OggS"


def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _is_mp4(data: bytes) -> bool:
    return len(data) >= 8 and data[4:8] == b"ftyp"


_HEADER_CHECKS = {
    "audio/webm": _is_webm,
    "audio/ogg": _is_ogg,
    "audio/wav": _is_wav,
    "audio/wave": _is_wav,
    "audio/x-wav": _is_wav,
    "audio/mp4": _is_mp4,
}


class ChunkError(ValueError):
    """A recorder rule rejected the input. ``code`` is a stable identifier."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class ChunkResult:
    accepted: bool
    duplicate: bool
    next_sequence: int
    remaining_bytes: int
    limit_reached: bool


def next_phase(current: str, event: str) -> str:
    """Return the phase reached from ``current`` on ``event``; ``SENT`` is terminal."""
    if not isinstance(current, str):
        raise ChunkError("bad_phase", "current must be a str")
    if not isinstance(event, str):
        raise ChunkError("bad_event", "event must be a str")
    target = _TRANSITIONS.get((current, event))
    if target is None:
        raise ChunkError("illegal_transition", f"{current} + {event}")
    return target


def missing_required(
    phase: str,
    metadata: Mapping[str, str],
    rules: Mapping[str, Sequence[str]],
) -> list[str]:
    """Required field names of ``phase`` that are absent or blank, in rule order."""
    if not isinstance(phase, str) or phase not in PHASES:
        raise ChunkError("unknown_phase", "phase must be one of " + ", ".join(PHASES))
    if not isinstance(metadata, Mapping):
        raise ChunkError("bad_metadata", "metadata must be a Mapping")
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ChunkError("bad_metadata", "metadata keys and values must be str")
    if not isinstance(rules, Mapping):
        raise ChunkError("bad_rules", "rules must be a Mapping")
    required = rules.get(phase)
    if required is None:
        return []
    for name in required:
        if not isinstance(name, str) or not name:
            raise ChunkError("bad_rules", "rule field names must be non-empty str")
    return [name for name in required if not metadata.get(name, "").strip()]


class ChunkSession:
    """In-memory chunk accumulator for one recording session generation.

    Sequences are gapless and start at 0. The first chunk must carry the
    container signature of ``mime``; later MediaRecorder fragments need no
    header. A chunk that would exceed ``max_bytes`` or ``max_duration_ms``
    is rejected without being stored. ``finalize`` is idempotent.
    """

    def __init__(
        self,
        session_id: str,
        generation: int,
        mime: str,
        *,
        max_bytes: int,
        max_duration_ms: int | None = None,
    ):
        self.session_id = _require_identifier(session_id, "bad_session_id")
        self.generation = _require_int(generation, "bad_generation", minimum=0)
        self.mime = _normalise_mime(mime)
        self.max_bytes = _require_int(max_bytes, "bad_max_bytes", minimum=1)
        if max_duration_ms is None:
            self.max_duration_ms = None
        else:
            self.max_duration_ms = _require_int(max_duration_ms, "bad_max_duration", minimum=0)
        self._chunks: list[bytes] = []
        self._total_bytes = 0
        self._total_duration_ms = 0
        self._final_bytes: bytes | None = None
        self._final_args: tuple[int, int, str] | None = None

    def accept(
        self,
        generation: int,
        sequence: int,
        data: bytes,
        duration_ms: int = 0,
    ) -> ChunkResult:
        """Validate one uploaded chunk against the sequence state.

        Checked in order: finalize state, generation, argument types,
        repeat or gap, first-chunk header, byte and duration limits. A
        rejected chunk is never stored.
        """
        if self._final_bytes is not None:
            raise ChunkError("already_finalized", "no chunks after finalize")
        generation = _require_int(generation, "bad_generation", minimum=0)
        if generation < self.generation:
            raise ChunkError("stale_generation", f"session generation is {self.generation}")
        if generation > self.generation:
            raise ChunkError("generation_mismatch", f"session generation is {self.generation}")
        sequence = _require_int(sequence, "bad_sequence", minimum=0)
        if not isinstance(data, bytes) or not data:
            raise ChunkError("bad_data", "data must be non-empty bytes")
        duration_ms = _require_int(duration_ms, "bad_duration", minimum=0)
        if sequence < len(self._chunks):
            return self._repeat(sequence, data)
        if sequence > len(self._chunks):
            raise ChunkError("gap", f"expected sequence {len(self._chunks)}, got {sequence}")
        return self._append(data, duration_ms)

    def finalize(self, *, total_chunks: int, total_bytes: int, sha256: str) -> bytes:
        """Verify count, byte total and digest; return the concatenated chunks.

        A second call with the same arguments returns the same bytes. A
        second call with different arguments raises the code the first
        verification would have used for that difference.
        """
        total_chunks = _require_int(total_chunks, "bad_count", minimum=0)
        total_bytes = _require_int(total_bytes, "bad_bytes", minimum=0)
        digest = _require_digest(sha256)
        if self._final_args is not None:
            if (total_chunks, total_bytes, digest) == self._final_args:
                return self._final_bytes
            if (total_chunks, total_bytes) != self._final_args[:2]:
                raise ChunkError("incomplete", "finalize arguments differ from the first call")
            raise ChunkError("digest_mismatch", "finalize digest differs from the first call")
        if len(self._chunks) != total_chunks or self._total_bytes != total_bytes:
            raise ChunkError(
                "incomplete",
                f"stored {len(self._chunks)} chunks / {self._total_bytes} bytes",
            )
        concat = b"".join(self._chunks)
        if hashlib.sha256(concat).hexdigest() != digest:
            raise ChunkError("digest_mismatch", "sha256 of the concatenation differs")
        self._final_bytes = concat
        self._final_args = (total_chunks, total_bytes, digest)
        return concat

    def _repeat(self, sequence: int, data: bytes) -> ChunkResult:
        if self._chunks[sequence] != data:
            raise ChunkError("conflict", f"chunk {sequence} bytes differ from the stored chunk")
        return self._result(duplicate=True)

    def _append(self, data: bytes, duration_ms: int) -> ChunkResult:
        if not self._chunks and not _HEADER_CHECKS[self.mime](data):
            raise ChunkError("bad_header", f"first chunk does not carry the {self.mime} signature")
        new_total = self._total_bytes + len(data)
        if new_total > self.max_bytes:
            raise ChunkError(
                "limit_reached",
                f"{new_total} bytes would exceed max_bytes {self.max_bytes}",
            )
        new_duration = self._total_duration_ms + duration_ms
        if self.max_duration_ms is not None and new_duration > self.max_duration_ms:
            raise ChunkError(
                "limit_reached",
                f"{new_duration} ms would exceed max_duration_ms {self.max_duration_ms}",
            )
        self._chunks.append(data)
        self._total_bytes = new_total
        self._total_duration_ms = new_duration
        return self._result(duplicate=False)

    def _result(self, *, duplicate: bool) -> ChunkResult:
        limit_reached = self._total_bytes >= self.max_bytes or (
            self.max_duration_ms is not None
            and self._total_duration_ms >= self.max_duration_ms
        )
        return ChunkResult(
            accepted=True,
            duplicate=duplicate,
            next_sequence=len(self._chunks),
            remaining_bytes=self.max_bytes - self._total_bytes,
            limit_reached=limit_reached,
        )


def _require_int(value: int, code: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ChunkError(code, "must be an int >= " + str(minimum))
    return value


def _require_identifier(value: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or _has_control_chars(value):
        raise ChunkError(code, "must be a non-empty str without control characters")
    return value


def _require_digest(sha256: str) -> str:
    if not isinstance(sha256, str):
        raise ChunkError("bad_digest", "sha256 must be a str")
    digest = sha256.lower()
    if len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise ChunkError("bad_digest", "sha256 must be 64 hex characters")
    return digest


def _normalise_mime(mime: str) -> str:
    if not isinstance(mime, str):
        raise ChunkError("bad_mime", "mime must be a str")
    base = mime.split(";", 1)[0].strip().lower()
    if not base or _has_control_chars(base):
        raise ChunkError("bad_mime", "mime must be a non-empty type/subtype")
    if base not in _HEADER_CHECKS:
        raise ChunkError("unsupported_mime", f"no container signature known for {base}")
    return base


def _has_control_chars(value: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in value)
