"""Drop folder scan planning: classify directory entries for auto-send.

Scope:
- Decide which entries of a directory listing are ready to be sent, which
  are still waiting for stability, and which are ignored.
- Track file stability across consecutive scans: a file must be unchanged
  (same size and mtime_ns) for ``required_scans`` consecutive scans before
  it is considered ready.
- Resolve the author prefix from a file name against a known-author map.

Non-scope:
- No filesystem access. This module works purely on ``Entry`` objects.
- No persistence, no routes, no clock. State lives only in ``StabilityTracker``.
- No automatic acceptance of unknown authors; there is no switch for it.
- No deletion or archiving (archive decisions happen in later wiring).
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

ALLOWED_EXTENSIONS = (
    ".wav",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".flac",
    ".dss",
    ".ds2",
)

_ALLOWED_LOWER = tuple(ext.lower() for ext in ALLOWED_EXTENSIONS)
_MAX_NAME_LENGTH = 255
_MAX_AUTHOR_LENGTH = 64
_TEMPORARY_PREFIXES = ("~$", ".")
_TEMPORARY_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload")


class DropFolderRulesError(ValueError):
    """A drop-folder input was invalid.

    ``code`` is a stable string that callers can match on; ``message``
    is a human-readable explanation.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Entry:
    """One item from a directory listing, without filesystem access."""

    name: str
    size: int
    mtime_ns: int
    is_dir: bool = False
    is_symlink: bool = False


@dataclass(frozen=True)
class ScanPlan:
    """Result of planning one directory scan."""

    ready: tuple[str, ...]
    waiting: tuple[str, ...]
    ignored: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _ObservedFile:
    """Internal tracker state for one file name."""

    size: int
    mtime_ns: int
    count: int


class StabilityTracker:
    """Remember directory entries across scans and decide when they are stable."""

    def __init__(self, required_scans: int = 2):
        if isinstance(required_scans, bool) or not isinstance(required_scans, int):
            raise DropFolderRulesError(
                "invalid_required_scans_type",
                "required_scans must be an int",
            )
        if required_scans < 1:
            raise ValueError("required_scans must be at least 1")
        self._required_scans = required_scans
        self._state: dict[str, _ObservedFile] = {}

    def plan(self, listing: Sequence[Entry]) -> ScanPlan:
        """Classify a directory listing and update stability state.

        Returns a ``ScanPlan`` with ``ready``, ``waiting`` and ``ignored``
        entries. Ignored entries do not affect tracker state.
        """
        if isinstance(listing, str) or not isinstance(listing, Sequence):
            raise DropFolderRulesError(
                "invalid_listing_type",
                "listing must be a sequence",
            )

        _validate_unique_names(listing)

        ready: list[str] = []
        waiting: list[str] = []
        ignored: list[tuple[str, str]] = []
        seen_names: set[str] = set()

        for entry in listing:
            _validate_entry(entry)
            seen_names.add(entry.name)
            reason = _ignored_reason(entry)
            if reason is not None:
                ignored.append((entry.name, reason))
                continue

            observed = self._state.get(entry.name)
            if observed is None:
                current = _ObservedFile(
                    size=entry.size,
                    mtime_ns=entry.mtime_ns,
                    count=1,
                )
                self._state[entry.name] = current
                if current.count >= self._required_scans:
                    ready.append(entry.name)
                else:
                    waiting.append(entry.name)
            elif observed.size == entry.size and observed.mtime_ns == entry.mtime_ns:
                current = _ObservedFile(
                    size=entry.size,
                    mtime_ns=entry.mtime_ns,
                    count=observed.count + 1,
                )
                self._state[entry.name] = current
                if current.count >= self._required_scans:
                    ready.append(entry.name)
                else:
                    waiting.append(entry.name)
            else:
                current = _ObservedFile(
                    size=entry.size,
                    mtime_ns=entry.mtime_ns,
                    count=1,
                )
                self._state[entry.name] = current
                if current.count >= self._required_scans:
                    ready.append(entry.name)
                else:
                    waiting.append(entry.name)

        # Files that disappeared between scans are forgotten.
        for name in list(self._state):
            if name not in seen_names:
                del self._state[name]

        return ScanPlan(
            ready=tuple(sorted(ready)),
            waiting=tuple(sorted(waiting)),
            ignored=tuple(sorted(ignored, key=lambda item: item[0])),
        )


def author_for(name: str, known_authors: Mapping[str, int]) -> int | None:
    """Resolve the author prefix of ``name`` to a known user id.

    The author is the text before the first underscore of the base name.
    It must be between 1 and 64 characters after NFC normalization.
    The match in ``known_authors`` is case-sensitive after NFC.
    Unknown authors are never invented; ``None`` is returned instead.
    """
    if not isinstance(name, str):
        raise DropFolderRulesError("invalid_name_type", "name must be a str")
    if not isinstance(known_authors, Mapping):
        raise DropFolderRulesError(
            "invalid_known_authors_type",
            "known_authors must be a mapping",
        )

    base = os.path.basename(name)
    normalised = unicodedata.normalize("NFC", base)
    if "_" not in normalised:
        return None

    prefix = normalised.split("_", 1)[0]
    if not prefix or len(prefix) > _MAX_AUTHOR_LENGTH:
        return None

    for author_name, user_id in known_authors.items():
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise DropFolderRulesError(
                "invalid_author_id_type",
                "known author user ids must be ints",
            )

    nfc_author_name = unicodedata.normalize("NFC", prefix)
    for author_name, user_id in known_authors.items():
        if unicodedata.normalize("NFC", author_name) == nfc_author_name:
            return user_id
    return None


def _validate_unique_names(listing: Sequence[Entry]) -> None:
    seen: set[str] = set()
    for entry in listing:
        if entry.name in seen:
            raise ValueError("duplicate_name")
        seen.add(entry.name)


def _validate_entry(entry: object) -> None:
    if not isinstance(entry, Entry):
        raise DropFolderRulesError(
            "invalid_entry_type",
            "listing items must be Entry instances",
        )

    if not isinstance(entry.name, str):
        raise DropFolderRulesError("invalid_name_type", "name must be a str")
    if entry.name == "":
        raise DropFolderRulesError("invalid_name_empty", "name must not be empty")
    if len(entry.name) > _MAX_NAME_LENGTH:
        return  # will be classified as unsafe_name; do not raise here

    if isinstance(entry.size, bool) or not isinstance(entry.size, int):
        raise DropFolderRulesError("invalid_size_type", "size must be an int")
    if entry.size < 0:
        raise DropFolderRulesError("invalid_size_value", "size must not be negative")

    if isinstance(entry.mtime_ns, bool) or not isinstance(entry.mtime_ns, int):
        raise DropFolderRulesError(
            "invalid_mtime_type",
            "mtime_ns must be an int",
        )


def _ignored_reason(entry: Entry) -> str | None:
    if entry.is_dir:
        return "directory"
    if entry.is_symlink:
        return "symlink"
    if _is_unsafe_name(entry.name):
        return "unsafe_name"
    if _is_temporary(entry.name):
        return "temporary"
    if entry.size == 0:
        return "empty"
    if not _has_allowed_extension(entry.name):
        return "unsupported_extension"
    return None


def _is_unsafe_name(name: str) -> bool:
    if len(name) > _MAX_NAME_LENGTH:
        return True
    if "/" in name or "\\" in name or os.sep in name:
        return True
    if "\x00" in name:
        return True
    return any(_is_control_character(char) for char in name)


def _is_control_character(char: str) -> bool:
    code = ord(char)
    return code <= 0x1F or code == 0x7F


def _is_temporary(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(_TEMPORARY_PREFIXES):
        return True
    return lowered.endswith(_TEMPORARY_SUFFIXES)


def _has_allowed_extension(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in _ALLOWED_LOWER
