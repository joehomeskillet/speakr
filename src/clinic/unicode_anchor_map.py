"""Unicode offset mapping for text-audio anchors.

Converts between Python code point offsets, UTF-16 code unit offsets (what the
browser editor counts), and offsets into NFC/LF-normalised stored text so that
anchors do not drift on umlauts, emoji or line endings.

Non-scope: no Flask, no routes, no database access, no clock. Pure functions
only. Persistence and wiring are handled by later packages.
"""

from __future__ import annotations

import unicodedata

from src.clinic.revisions import normalise_text


class OffsetError(ValueError):
    """Offset conversion failed before it could return a result."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _validate_text(text: str, param: str = "text") -> None:
    if not isinstance(text, str):
        raise TypeError(f"{param} must be a str")


def _validate_int_offset(offset: int, param: str = "offset") -> None:
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise OffsetError("invalid_type", f"{param} must be an int, not {type(offset).__name__}")


def _validate_range(offset: int, length: int, param: str = "offset") -> None:
    if offset < 0 or offset > length:
        raise OffsetError("out_of_range", f"{param} {offset} not in [0, {length}]")


def to_utf16_offset(text: str, cp_offset: int) -> int:
    """Convert a code point offset to a UTF-16 code unit offset.

    Args:
        text: The source string.
        cp_offset: Offset in Unicode code points (Python's native indexing).

    Returns:
        Offset in UTF-16 code units (what JavaScript/browser APIs use).

    Raises:
        TypeError: If ``text`` is not a str.
        OffsetError: If ``cp_offset`` is not an int, is negative, or exceeds
            the code point length of ``text``.
    """
    _validate_text(text)
    _validate_int_offset(cp_offset)
    cp_len = len(text)
    _validate_range(cp_offset, cp_len)

    utf16 = 0
    for ch in text[:cp_offset]:
        cp = ord(ch)
        if 0x10000 <= cp <= 0x10FFFF:
            utf16 += 2
        else:
            utf16 += 1
    return utf16


def to_codepoint_offset(text: str, utf16_offset: int) -> int:
    """Convert a UTF-16 code unit offset to a code point offset.

    Args:
        text: The source string.
        utf16_offset: Offset in UTF-16 code units.

    Returns:
        Offset in Unicode code points.

    Raises:
        TypeError: If ``text`` is not a str.
        OffsetError: If ``utf16_offset`` is not an int, is negative, exceeds
            the UTF-16 length of ``text``, or lands inside a surrogate pair.
    """
    _validate_text(text)
    _validate_int_offset(utf16_offset)

    utf16_len = utf16_length(text)
    _validate_range(utf16_offset, utf16_len)

    cp = 0
    utf16 = 0
    for ch in text:
        ch_cp = ord(ch)
        ch_utf16 = 2 if 0x10000 <= ch_cp <= 0x10FFFF else 1
        if utf16 + ch_utf16 > utf16_offset:
            if ch_utf16 == 2 and utf16 + 1 == utf16_offset:
                raise OffsetError("inside_surrogate_pair", "offset lands inside a surrogate pair")
            return cp
        utf16 += ch_utf16
        cp += 1
    return cp


def map_through_normalisation(raw: str, raw_cp_offset: int) -> int:
    """Map a code point offset in raw text to the corresponding offset in normalised text.

    Normalisation applies NFC followed by CRLF→LF and CR→LF (see
    ``src.clinic.revisions.normalise_text``). The mapping is monotone
    non-decreasing and clamps offsets that fall inside a composed sequence to
    the start of that composed character.

    Args:
        raw: The original unnormalised string.
        raw_cp_offset: Offset in code points within ``raw``.

    Returns:
        Offset in code points within ``normalise_text(raw)``.

    Raises:
        TypeError: If ``raw`` is not a str.
        OffsetError: If ``raw_cp_offset`` is not an int, is negative, or exceeds
            the code point length of ``raw``.
    """
    _validate_text(raw)
    _validate_int_offset(raw_cp_offset)
    _validate_range(raw_cp_offset, len(raw))

    # Build mapping by checking for each raw character whether it starts a new
    # normalised character or modifies the previous one (e.g., combining marks,
    # CRLF second half). This is O(n^2) but correct and fast enough for
    # clinical text lengths.
    mapping = [0] * (len(raw) + 1)
    norm_idx = 0

    for i in range(len(raw)):
        prefix = raw[:i]
        prefix_plus_ch = raw[:i + 1]
        if len(normalise_text(prefix_plus_ch)) > len(normalise_text(prefix)):
            # This character starts a new normalised character
            mapping[i] = norm_idx
            norm_idx += 1
        else:
            # This character modifies the previous normalised character
            # (combining mark, CRLF \n, etc.) - clamp to start of composed char
            mapping[i] = norm_idx - 1 if norm_idx > 0 else 0

    # Final offset maps to full normalised length
    mapping[len(raw)] = norm_idx

    return mapping[raw_cp_offset]


def utf16_length(text: str) -> int:
    """Return the length of ``text`` in UTF-16 code units.

    Args:
        text: The string to measure.

    Returns:
        Number of UTF-16 code units (surrogate pairs count as 2).

    Raises:
        TypeError: If ``text`` is not a str.
    """
    _validate_text(text)
    length = 0
    for ch in text:
        cp = ord(ch)
        if 0x10000 <= cp <= 0x10FFFF:
            length += 2
        else:
            length += 1
    return length