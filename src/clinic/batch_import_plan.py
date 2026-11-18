"""Plan a manual batch import from file metadata and header bytes.

This is a signature check only; it does not decode, verify full file contents,
store files, or look for duplicates outside the current batch.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from src.clinic.decoder import detect_container

KIND_AUDIO, KIND_DOCUMENT = "audio", "document"
AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".dss", ".ds2")
DOCUMENT_EXTENSIONS = (".txt", ".rtf", ".docx", ".odt")

_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_DECODER_EXTENSIONS = (".wav", ".mp3", ".dss", ".ds2")
_PREFIXES = {
    ".ogg": (b"OggS",),
    ".opus": (b"OggS", b"\x1a\x45\xdf\xa3"),  # Opus in Ogg or an EBML container.
    ".flac": (b"fLaC",),
    ".rtf": (b"{\\rtf",),
    ".docx": (b"PK\x03\x04",),
    ".odt": (b"PK\x03\x04",),
}


class BatchPlanError(ValueError):
    """Invalid API input, with a stable code for callers."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _require_nonnegative_int(value: int, code: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BatchPlanError(code, "Eine nichtnegative Ganzzahl ist erforderlich.")


def _require_head(head: bytes) -> None:
    if not isinstance(head, bytes):
        raise BatchPlanError("invalid_head", "Der Dateikopf muss aus Bytes bestehen.")


@dataclass(frozen=True)
class BatchItem:
    name: str
    size: int
    head: bytes
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise BatchPlanError("invalid_name", "Der Dateiname muss eine Zeichenkette sein.")
        _require_nonnegative_int(self.size, "invalid_size")
        _require_head(self.head)
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise BatchPlanError("invalid_sha256", "SHA-256 muss aus 64 Hexadezimalzeichen bestehen.")


@dataclass(frozen=True)
class ItemVerdict:
    index: int
    name: str
    kind: str | None
    status: str
    reason: str


@dataclass(frozen=True)
class BatchLimits:
    max_files: int = 50
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024**3

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.max_files, "invalid_max_files")
        _require_nonnegative_int(self.max_file_bytes, "invalid_max_file_bytes")
        _require_nonnegative_int(self.max_total_bytes, "invalid_max_total_bytes")


def _display_name(name: str) -> str:
    basename = ntpath.basename(name)
    return "".join(char for char in basename if unicodedata.category(char) not in {"Cc", "Cs"})


def _valid_name(name: str, display_name: str) -> bool:
    return (
        name == display_name
        and bool(name.strip())
        and name not in {".", ".."}
        and len(name) <= 255
    )


def _matches(extension: str, head: bytes) -> bool:
    if extension in _DECODER_EXTENSIONS:
        return detect_container(head) == extension[1:]
    if extension == ".m4a":
        return head[4:8] == b"ftyp"
    if extension != ".txt":
        return head.startswith(_PREFIXES[extension])
    if not head or b"\x00" in head:
        return False
    for encoding in ("utf-8", "cp1252"):
        try:
            head.decode(encoding)
        except UnicodeDecodeError:
            continue
        return True
    return False


def _classify(name: str, head: bytes) -> tuple[str | None, str, str]:
    extension = posixpath.splitext(name)[1].lower()
    if extension in AUDIO_EXTENSIONS:
        kind = KIND_AUDIO
    elif extension in DOCUMENT_EXTENSIONS:
        kind = KIND_DOCUMENT
    else:
        return None, "unsupported", f"unsupported_extension:{extension}"
    if not _matches(extension, head):
        return None, "type_mismatch", "type_mismatch"
    return kind, "accepted", ""


def classify(name: str, head: bytes) -> tuple[str | None, str]:
    """Return the matching kind and an empty reason, or a rejection reason."""
    if not isinstance(name, str):
        raise BatchPlanError("invalid_name", "Der Dateiname muss eine Zeichenkette sein.")
    _require_head(head)
    if not _valid_name(name, _display_name(name)):
        return None, "bad_name"
    kind, _, reason = _classify(name, head)
    return kind, reason


def plan_batch(items: Sequence[BatchItem], limits: BatchLimits = BatchLimits()) -> list[ItemVerdict]:
    """Return ordered verdicts with zero-based indexes.

    Only accepted items reserve bytes and register digests. A file that would
    exceed the total limit is skipped, leaving room for later smaller files.
    The count limit applies to input positions regardless of earlier verdicts.
    """
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        raise BatchPlanError("invalid_items", "Eine Folge von Dateien ist erforderlich.")
    if not isinstance(limits, BatchLimits):
        raise BatchPlanError("invalid_limits", "BatchLimits ist erforderlich.")

    verdicts: list[ItemVerdict] = []
    accepted: dict[str, int] = {}
    total = 0
    for index, item in enumerate(items):
        name = _display_name(item.name) if isinstance(item, BatchItem) else ""
        kind = None
        if index >= limits.max_files:
            status, reason = "batch_limit", "file_count"
        elif not isinstance(item, BatchItem):
            status, reason = "unsupported", "invalid_item"
        elif not _valid_name(item.name, name):
            status, reason = "bad_name", "bad_name"
        elif item.size == 0:
            status, reason = "unsupported", "empty"
        elif item.size > limits.max_file_bytes:
            status, reason = "too_large", "file_size"
        else:
            kind, status, reason = _classify(name, item.head)
            if kind is not None:
                digest = item.sha256.lower()
                if digest in accepted:
                    status, reason = "duplicate", f"same_content_as_{accepted[digest]}"
                elif total + item.size > limits.max_total_bytes:
                    status, reason = "batch_limit", "total_size"
                else:
                    accepted[digest] = index
                    total += item.size
        verdicts.append(ItemVerdict(index, name, kind, status, reason))
    return verdicts
