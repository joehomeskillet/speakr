r"""RTF and plain-text conversion for the text part of a clinic dictation.

Scope: a small, safe, dependency-free converter between dictation text and
RTF (``\ansicpg1252`` document charset with ``\'hh`` for cp1252 characters
and ``\uN?`` signed 16-bit escapes for everything else), plus byte-level
load/save helpers for plain text files (UTF-8 without BOM, UTF-16 with BOM,
cp1252 fallback).

Non-scope: no Flask routes, no database access, no clock, no file system
access. Persistence and routes are a later wiring package. Nothing here
couples to approval (Abnahme) or to speech recognition. Nothing deletes
data.

Error codes (stable strings on ``RtfError.code``):

- ``invalid_text``: the text argument is unusable (wrong type, lone
  surrogate, or a control character other than newline and tab).
- ``invalid_data``: an argument has the wrong type (bytes expected, etc.).
- ``malformed``: RTF braces are unbalanced or the argument is not a str.
- ``too_large``: RTF input is longer than ``max_chars``.
- ``not_rtf``: RTF input does not start with ``{\rtf``.
- ``binary``: file bytes are not decodable text (NUL outside UTF-16, or
  an undefined cp1252 byte after UTF-8 failed).
"""

from __future__ import annotations

import codecs
import unicodedata
from functools import lru_cache

_HEADER = "{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fnil\\fcharset0 Calibri;}}"

_DESTINATIONS = frozenset({"fonttbl", "colortbl", "stylesheet", "info"})

_MAX_TEXT_CHARS = 5_000_000


class RtfError(ValueError):
    """The conversion rejected the input. ``code`` is a stable string."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def text_to_rtf(text: str) -> str:
    r"""Convert dictation text to an RTF document string.

    CRLF and CR are normalised to LF first. ``\\``, ``{`` and ``}`` are
    escaped, ``\\n`` becomes ``\\par``, a tab becomes ``\\tab``. cp1252
    characters are written as ``\'hh``, everything else as ``\uN?`` with a
    signed 16-bit value; characters above U+FFFF as a surrogate pair.
    The conversion is lossless for text without lone surrogates.
    """
    if not isinstance(text, str):
        raise RtfError("invalid_text", "text must be a str")
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    i = 0
    n = len(normalised)
    while i < n:
        ch = normalised[i]
        if ch == "\n":
            parts.append("\\par")
            i += 1
            if i < n and _needs_group_guard(normalised[i]):
                parts.append("{}")
            continue
        if ch == "\t":
            parts.append("\\tab")
            i += 1
            if i < n and _needs_group_guard(normalised[i]):
                parts.append("{}")
            continue
        parts.append(_escape_char(ch))
        i += 1
    return _HEADER + "".join(parts) + "}"


def rtf_to_text(rtf: str, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    r"""Extract the visible text from an RTF document string.

    Destination groups (``{\fonttbl ...}``, ``{\colortbl ...}``,
    ``{\stylesheet ...}``, ``{\info ...}``, any ``{\*\...}``) are ignored.
    ``\'hh`` is decoded as cp1252, ``\uN?`` as Unicode with the fallback
    characters skipped (``\ucN`` respected), ``\par``/``\line`` become
    newlines and ``\tab`` a tab. Unknown control words are ignored.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise RtfError("invalid_data", "max_chars must be an int")
    if not isinstance(rtf, str):
        raise RtfError("malformed", "rtf must be a str")
    if len(rtf) > max_chars:
        raise RtfError("too_large", "rtf input is longer than max_chars")
    if not rtf.startswith("{\\rtf"):
        raise RtfError("not_rtf", "rtf must start with {\\rtf")

    out: list[str] = []
    stack: list[int] = []
    pending_high: int | None = None
    uc_skip = 1
    i = 0
    n = len(rtf)

    def flush_pending() -> None:
        nonlocal pending_high
        if pending_high is not None:
            out.append("\ufffd")
            pending_high = None

    while i < n:
        ch = rtf[i]
        if ch == "{":
            flush_pending()
            j = i + 1
            skip = False
            if j < n and rtf[j] == "\\":
                if j + 1 < n and rtf[j + 1] == "*":
                    skip = True
                else:
                    k = j + 1
                    while k < n and rtf[k].isascii() and rtf[k].isalpha():
                        k += 1
                    if rtf[j + 1 : k] in _DESTINATIONS:
                        skip = True
            if skip:
                i = _skip_group(rtf, i)
                continue
            stack.append(uc_skip)
            i += 1
            continue
        if ch == "}":
            flush_pending()
            if not stack:
                raise RtfError("malformed", "unbalanced closing brace")
            uc_skip = stack.pop()
            i += 1
            continue
        if ch == "\\":
            j = i + 1
            if j >= n:
                break
            token = rtf[j]
            if token == "'":
                hex_digits = rtf[j + 1 : j + 3]
                if len(hex_digits) == 2 and all(c in "0123456789abcdefABCDEF" for c in hex_digits):
                    flush_pending()
                    out.append(_cp1252_byte(int(hex_digits, 16)))
                    i = j + 3
                else:
                    i = j + 1
                continue
            if token == "*":
                i = j + 1
                continue
            if token in "\\{}":
                flush_pending()
                out.append(token)
                i = j + 1
                continue
            if token.isascii() and token.isalpha():
                k = j + 1
                while k < n and rtf[k].isascii() and rtf[k].isalpha():
                    k += 1
                name = rtf[j:k]
                param: int | None = None
                if k < n and rtf[k] == "-" and k + 1 < n and rtf[k + 1].isdigit():
                    k += 1
                    start = k
                    while k < n and rtf[k].isdigit():
                        k += 1
                    param = -int(rtf[start:k])
                elif k < n and rtf[k].isdigit():
                    start = k
                    while k < n and rtf[k].isdigit():
                        k += 1
                    param = int(rtf[start:k])
                if k < n and rtf[k] == " ":
                    k += 1
                if name == "u":
                    if param is not None:
                        value = param % 65536
                        if pending_high is not None and 0xDC00 <= value <= 0xDFFF:
                            out.append(
                                chr(0x10000 + ((pending_high - 0xD800) << 10) + (value - 0xDC00))
                            )
                            pending_high = None
                        else:
                            if pending_high is not None:
                                out.append("\ufffd")
                                pending_high = None
                            if 0xD800 <= value <= 0xDBFF:
                                pending_high = value
                            elif 0xDC00 <= value <= 0xDFFF:
                                out.append("\ufffd")
                            else:
                                out.append(chr(value))
                        i = _skip_units(rtf, k, uc_skip)
                        continue
                elif name in ("par", "line"):
                    flush_pending()
                    out.append("\n")
                elif name == "tab":
                    flush_pending()
                    out.append("\t")
                elif name == "uc":
                    uc_skip = param if param is not None else 0
                # unknown control words are ignored
                i = k
                continue
            # unknown control symbol: ignore
            i = j + 1
            continue
        if ch in "\r\n":
            # literal line noise inside the RTF stream
            i += 1
            continue
        flush_pending()
        out.append(ch)
        i += 1
    flush_pending()
    if stack:
        raise RtfError("malformed", "unclosed group")
    return "".join(out)


def save_text_file(text: str) -> bytes:
    """Encode text as UTF-8 bytes without BOM and with LF line ends."""
    if not isinstance(text, str):
        raise RtfError("invalid_data", "text must be a str")
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    _validate_text(normalised)
    return normalised.encode("utf-8")


def load_text_file(data: bytes) -> str:
    """Decode file bytes to text: UTF-8(-sig), UTF-16 with BOM, then cp1252.

    A file with NUL bytes that is not UTF-16, or bytes that no codec
    accepts, raises ``RtfError('binary')``.
    """
    if isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise RtfError("invalid_data", "data must be bytes")
    if not data:
        return ""
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise RtfError("binary", "truncated UTF-16 data") from exc
    if b"\x00" in data:
        raise RtfError("binary", "NUL byte in non-UTF-16 data")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise RtfError("binary", "data is not UTF-8, UTF-16 or cp1252 text") from exc


def _validate_text(normalised: str) -> None:
    for ch in normalised:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise RtfError("invalid_text", "lone surrogate in text")
        if unicodedata.category(ch) == "Cc" and ch not in "\n\t":
            raise RtfError("invalid_text", f"control character U+{code:04X} in text")


def _needs_group_guard(ch: str) -> bool:
    """True when a char right after a control word would be misread as part of it."""
    return ch.isascii() and (ch.isalnum() or ch in " -")


@lru_cache(maxsize=4096)
def _escape_char(ch: str) -> str:
    """RTF representation of one character; raises for unusable characters."""
    code = ord(ch)
    if 0xD800 <= code <= 0xDFFF:
        raise RtfError("invalid_text", "lone surrogate in text")
    if unicodedata.category(ch) == "Cc":
        raise RtfError("invalid_text", f"control character U+{code:04X} in text")
    if code == 0x5C:
        return "\\\\"
    if code == 0x7B:
        return "\\{"
    if code == 0x7D:
        return "\\}"
    if 0x20 <= code <= 0x7E:
        return ch
    try:
        byte = ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        return _unicode_escape(code)
    return "\\'%02x" % byte


def _unicode_escape(code: int) -> str:
    r"""Signed 16-bit ``\uN?`` words for one code point above the cp1252 set."""
    if code > 0xFFFF:
        code -= 0x10000
        high = 0xD800 + (code >> 10)
        low = 0xDC00 + (code & 0x3FF)
        return "\\u%d?\\u%d?" % (_signed16(high), _signed16(low))
    return "\\u%d?" % _signed16(code)


def _signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


@lru_cache(maxsize=256)
def _cp1252_byte(byte: int) -> str:
    return bytes([byte]).decode("cp1252", errors="replace")


def _skip_group(rtf: str, i: int) -> int:
    """``rtf[i]`` is ``{``. Return the index just past the matching ``}``."""
    depth = 1
    i += 1
    n = len(rtf)
    while i < n and depth:
        ch = rtf[i]
        if ch == "\\":
            i = _skip_escape(rtf, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return i


def _skip_escape(rtf: str, i: int) -> int:
    """Consume one escaped token starting at the backslash at ``rtf[i]``."""
    n = len(rtf)
    j = i + 1
    if j >= n:
        return j
    ch = rtf[j]
    if ch.isascii() and ch.isalpha():
        j += 1
        while j < n and rtf[j].isascii() and rtf[j].isalpha():
            j += 1
        if j < n and rtf[j] == "-":
            j += 1
        while j < n and rtf[j].isdigit():
            j += 1
        if j < n and rtf[j] == " ":
            j += 1
        return j
    if ch == "'":
        return min(j + 3, n)
    return j + 1


def _skip_units(rtf: str, i: int, count: int) -> int:
    r"""Skip ``count`` fallback units after ``\uN``; escapes count as one unit."""
    n = len(rtf)
    for _ in range(max(0, count)):
        if i >= n:
            break
        if rtf[i] == "\\":
            i = _skip_escape(rtf, i)
        else:
            i += 1
    return i
