"""Resolve the author of an imported dictation from a device id and/or an
embedded author name.

The resolution is strict: no guessing, no fuzzy matching, no reordering of
name parts. An alias maps to user ids after NFC normalisation, stripping,
collapsing inner whitespace to a single space, and casefolding. Device ids are
matched case-insensitively after stripping. Contradictory or ambiguous inputs
are reported as conflicts; missing inputs are reported as unresolved.

This module has no database access, no Flask, and no clock. It is pure Python
and operates only on the plain mappings passed by the caller.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass


RESOLVED = "resolved"
UNRESOLVED = "unresolved"
CONFLICT = "conflict"


class AuthorResolutionError(ValueError):
    """The resolution was rejected before it produced a result."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Resolution:
    """Outcome of an author resolution attempt."""

    status: str
    author_user_id: int | None
    source: str | None
    reason: str


def normalise_alias(value: str) -> str:
    """Normalise an author alias for stable lookup.

    Applies NFC normalisation, strips leading/trailing whitespace, collapses
    internal runs of whitespace to a single space, and casefolds. Does not
    reorder name parts and does not remove diacritics.

    Raises:
        AuthorResolutionError: if value is not a string, is empty after
            stripping, or contains control characters.
    """
    if not isinstance(value, str):
        raise AuthorResolutionError("invalid_type", "alias must be a string")
    stripped = value.strip()
    if stripped == "":
        raise AuthorResolutionError("empty", "alias must not be empty or whitespace-only")
    # Allow whitespace (tab, newline, etc.) to be collapsed later; reject other control chars
    if any((ord(ch) < 32 and ch not in "\t\n\r\v\f") or ord(ch) == 127 for ch in stripped):
        raise AuthorResolutionError("control_character", "alias must not contain control characters")
    normalised = unicodedata.normalize("NFC", stripped)
    collapsed = " ".join(normalised.split())
    return collapsed.casefold()


def _validate_device_map(device_map: Mapping[str, int]) -> None:
    """Validate that device_map contains only str->int with positive ints."""
    for key, value in device_map.items():
        if not isinstance(key, str):
            raise AuthorResolutionError("invalid_type", "device_map keys must be strings")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AuthorResolutionError("invalid_user_id", "device_map values must be positive integers")


def _validate_alias_map(alias_map: Mapping[str, Collection[int]]) -> None:
    """Validate that alias_map contains only str->Collection[int] with positive ints."""
    for key, value in alias_map.items():
        if not isinstance(key, str):
            raise AuthorResolutionError("invalid_type", "alias_map keys must be strings")
        if not isinstance(value, Collection):
            raise AuthorResolutionError("invalid_type", "alias_map values must be collections")
        for uid in value:
            if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
                raise AuthorResolutionError("invalid_user_id", "alias_map user ids must be positive integers")


def resolve_author(
    *,
    device_id: str | None,
    embedded_author: str | None,
    device_map: Mapping[str, int],
    alias_map: Mapping[str, Collection[int]],
) -> Resolution:
    """Resolve the author from a device id and/or an embedded author name.

    Args:
        device_id: The device identifier from the import source. Matched case-
            insensitively after stripping. None or whitespace-only counts as
            absent.
        embedded_author: The author name found in the recording metadata.
            Normalised via ``normalise_alias``. None or whitespace-only counts
            as absent.
        device_map: Mapping from uppercased, stripped device id to a single
            user id. Must contain only positive integers.
        alias_map: Mapping from normalised alias to a collection of user ids.
            Must contain only positive integers. The keys are expected to be
            already normalised (output of ``normalise_alias``).

    Returns:
        A Resolution with status RESOLVED, UNRESOLVED, or CONFLICT.

    Raises:
        AuthorResolutionError: if the maps contain invalid types or values.
    """
    _validate_device_map(device_map)
    _validate_alias_map(alias_map)

    # Normalise device_id: strip and upper case
    device_user_id: int | None = None
    if device_id is not None:
        stripped_device = device_id.strip()
        if stripped_device != "":
            upper_device = stripped_device.upper()
            device_user_id = device_map.get(upper_device)

    # Normalise embedded_author
    alias_user_ids: Collection[int] | None = None
    if embedded_author is not None:
        stripped_author = embedded_author.strip()
        if stripped_author != "":
            norm_alias = normalise_alias(stripped_author)
            alias_user_ids = alias_map.get(norm_alias)

    # Both absent
    if device_user_id is None and alias_user_ids is None:
        return Resolution(
            status=UNRESOLVED,
            author_user_id=None,
            source=None,
            reason="no_input",
        )

    # Only device resolves
    if device_user_id is not None and alias_user_ids is None:
        return Resolution(
            status=RESOLVED,
            author_user_id=device_user_id,
            source="device",
            reason="device_only",
        )

    # Only alias resolves
    if device_user_id is None and alias_user_ids is not None:
        if len(alias_user_ids) == 1:
            return Resolution(
                status=RESOLVED,
                author_user_id=next(iter(alias_user_ids)),
                source="embedded",
                reason="alias_only",
            )
        # Ambiguous alias (2+ users)
        return Resolution(
            status=CONFLICT,
            author_user_id=None,
            source=None,
            reason="ambiguous_alias",
        )

    # Both resolve
    if device_user_id in alias_user_ids:
        # Device wins as the stronger fact
        return Resolution(
            status=RESOLVED,
            author_user_id=device_user_id,
            source="device",
            reason="device_alias_agree",
        )
    # Different users
    return Resolution(
        status=CONFLICT,
        author_user_id=None,
        source=None,
        reason="device_alias_conflict",
    )