"""Audio format selection and validation for clinic dictation uploads.

Pure functions for choosing the browser upload format, verifying declared MIME
against magic bytes, clamping sample rates, and gating dictation sources.
No Flask, no database access, no clock. All validation is strict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, Sequence


SOURCES = ("browser_recording", "device_import", "file_upload", "live_session")


class FormatError(ValueError):
    """The format choice or validation was rejected."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FormatChoice:
    """A chosen upload format with its properties."""

    mime: str
    container: str
    codec: str | None
    extension: str


_MIME_RE = re.compile(r"^\s*([a-zA-Z0-9][a-zA-Z0-9!#$&-\^_]*\/[a-zA-Z0-9][a-zA-Z0-9!#$&-\^_.+]*?)\s*(?:;\s*(.*))?\s*$", re.IGNORECASE)
_PARAM_RE = re.compile(r'([a-zA-Z0-9!#$&-\^_]+)\s*=\s*(?:"([^"]*)"|([^;,\s]+))')

_KNOWN_FORMATS: dict[str, tuple[str, str | None, str]] = {
    "audio/webm": ("webm", "opus", ".webm"),
    "audio/ogg": ("ogg", "opus", ".ogg"),
    "audio/mp4": ("mp4", "mp4a.40.2", ".m4a"),
    "audio/wav": ("wav", None, ".wav"),
}

_PREFERRED_ORDER = (
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mp4;codecs=mp4a.40.2",
    "audio/wav",
)

_PREFERRED_ORDER_LOSSLESS_FIRST = (
    "audio/wav",
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/mp4;codecs=mp4a.40.2",
)

_MAGIC_BYTES: dict[str, tuple[bytes, int]] = {
    "audio/webm": (b"\x1a\x45\xdf\xa3", 0),  # EBML
    "audio/ogg": (b"OggS", 0),
    "audio/wav": (b"RIFF", 0),  # checked with WAVE at offset 8
    "audio/mp4": (b"ftyp", 4),
    "audio/m4a": (b"ftyp", 4),
    "audio/ogg;codecs=opus": (b"OggS", 0),
    "audio/webm;codecs=opus": (b"\x1a\x45\xdf\xa3", 0),
    "audio/mp4;codecs=mp4a.40.2": (b"ftyp", 4),
}


def parse_mime(value: str) -> tuple[str, dict[str, str]]:
    """Parse a MIME type string into (media_type, parameters).

    Whitespace and quotes are handled. The media type is lowercased.
    Parameters keep their original case for keys and unquoted values.

    Raises:
        FormatError('bad_mime'): when value is empty, not a string,
            not an audio MIME type, or malformed.
    """
    if not isinstance(value, str):
        raise FormatError("MIME type must be a string", "bad_mime")
    value = value.strip()
    if not value:
        raise FormatError("MIME type must not be empty", "bad_mime")

    m = _MIME_RE.match(value)
    if not m:
        raise FormatError("Malformed MIME type", "bad_mime")

    media_type = m.group(1).lower()
    if not media_type.startswith("audio/"):
        raise FormatError("Not an audio MIME type", "bad_mime")

    params: dict[str, str] = {}
    param_str = m.group(2) or ""
    for pm in _PARAM_RE.finditer(param_str):
        key = pm.group(1).lower()
        val = pm.group(2) if pm.group(2) is not None else pm.group(3)
        params[key] = val

    return media_type, params


def _normalize_mime_for_lookup(mime: str, params: dict[str, str]) -> str:
    """Build a normalized lookup key from mime and codecs param."""
    codecs = params.get("codecs", "").lower()
    if codecs:
        return f"{mime};codecs={codecs}"
    return mime


def choose_upload_format(supported: Sequence[str], *, lossless_preferred: bool = False) -> FormatChoice:
    """Choose the best upload format from the browser's supported list.

    Preference order (normal): webm/opus, ogg/opus, mp4/aac, wav.
    With lossless_preferred=True: wav first, then normal order.
    Unknown MIME types in supported are skipped.
    Empty or all-unknown list raises FormatError('no_supported_format').

    Args:
        supported: Sequence of MIME type strings the browser claims to support.
        lossless_preferred: Prefer uncompressed WAV when available.

    Returns:
        FormatChoice with mime, container, codec, extension.

    Raises:
        FormatError('no_supported_format'): when no known format is supported.
        FormatError('bad_mime'): when an element is not a valid audio MIME string.
    """
    if not isinstance(supported, Sequence) or isinstance(supported, (str, bytes)):
        raise FormatError("supported must be a sequence of strings", "bad_mime")

    order = _PREFERRED_ORDER_LOSSLESS_FIRST if lossless_preferred else _PREFERRED_ORDER

    supported_keys: set[str] = set()
    for raw in supported:
        if not isinstance(raw, str):
            raise FormatError("Each supported entry must be a string", "bad_mime")
        mime, params = parse_mime(raw)
        key = _normalize_mime_for_lookup(mime, params)
        if key in _KNOWN_FORMATS or key.split(";")[0] in _KNOWN_FORMATS:
            supported_keys.add(key)

    if not supported_keys:
        raise FormatError("No supported format available", "no_supported_format")

    for pref in order:
        if pref in supported_keys:
            mime = pref.split(";")[0]
            container, codec, extension = _KNOWN_FORMATS[mime]
            codecs_param = pref.split(";codecs=")[1] if ";codecs=" in pref else codec
            return FormatChoice(mime=mime, container=container, codec=codecs_param, extension=extension)

    # Fallback: first supported key that matches a known base mime
    for key in supported_keys:
        base = key.split(";")[0]
        if base in _KNOWN_FORMATS:
            container, codec, extension = _KNOWN_FORMATS[base]
            codecs_param = key.split(";codecs=")[1] if ";codecs=" in key else codec
            mime = base
            return FormatChoice(mime=mime, container=container, codec=codecs_param, extension=extension)

    raise FormatError("No supported format available", "no_supported_format")


def declared_matches(mime: str, head: bytes) -> bool:
    """Check whether the first bytes match the declared MIME type.

    webm needs EBML (0x1A45DFA3) at offset 0.
    ogg needs 'OggS' at offset 0.
    wav needs 'RIFF' at offset 0 and 'WAVE' at offset 8.
    mp4/m4a needs 'ftyp' at offset 4.
    Head shorter than 12 bytes returns False.
    Unknown MIME returns False.

    Args:
        mime: The declared MIME type (e.g. 'audio/webm;codecs=opus').
        head: First bytes of the file (at least 12 bytes recommended).

    Returns:
        True when the magic bytes match, False otherwise.
    """
    if not isinstance(mime, str) or not isinstance(head, (bytes, bytearray)):
        return False
    if len(head) < 12:
        return False

    mime_lower = mime.lower()
    params_start = mime_lower.find(";")
    base_mime = mime_lower[:params_start] if params_start != -1 else mime_lower
    base_mime = base_mime.strip()

    # Direct lookup with codecs
    key = _normalize_mime_for_lookup(base_mime, {})
    if ";codecs=" in mime_lower:
        codecs = mime_lower.split(";codecs=")[1].split(";")[0].split(",")[0].strip().strip('"')
        key = f"{base_mime};codecs={codecs}"

    magic_info = _MAGIC_BYTES.get(key)
    if magic_info is None:
        magic_info = _MAGIC_BYTES.get(base_mime)
    if magic_info is None:
        return False

    magic, offset = magic_info
    if offset + len(magic) > len(head):
        return False
    if head[offset:offset + len(magic)] != magic:
        return False

    # Extra check for WAV: WAVE at offset 8
    if base_mime == "audio/wav":
        if len(head) < 12:
            return False
        return head[8:12] == b"WAVE"

    return True


def sample_rate_for(choice: FormatChoice, source_rate: int) -> int:
    """Clamp the source sample rate to the target range for the chosen format.

    Opus is always 48000 or 16000: map 8000-16000 to 16000, above to 48000.
    Other codecs clamp to 16000..48000.

    Args:
        choice: The chosen FormatChoice.
        source_rate: The source audio sample rate in Hz.

    Returns:
        The target sample rate.

    Raises:
        FormatError('bad_rate'): when source_rate is not a positive int
            or is a bool.
    """
    if isinstance(source_rate, bool) or not isinstance(source_rate, int) or source_rate <= 0:
        raise FormatError("Sample rate must be a positive integer", "bad_rate")

    is_opus = choice.codec is not None and "opus" in choice.codec.lower()

    if is_opus:
        if source_rate <= 16000:
            return 16000
        return 48000

    # Clamp to 16000..48000 for other codecs
    if source_rate < 16000:
        return 16000
    if source_rate > 48000:
        return 48000
    return source_rate


def source_allowed(allowed_sources: Collection[str], source: str) -> bool:
    """Check whether a dictation source is allowed.

    Returns True only for an exact member of SOURCES that is in allowed_sources.
    An unknown source name (not in SOURCES) raises FormatError('unknown_source').

    Args:
        allowed_sources: Collection of allowed source identifiers.
        source: The source to check.

    Returns:
        True when source is in allowed_sources and is a known source.

    Raises:
        FormatError('unknown_source'): when source is not in SOURCES.
    """
    if not isinstance(source, str):
        raise FormatError("Source must be a string", "unknown_source")
    if source not in SOURCES:
        raise FormatError(f"Unknown source: {source}", "unknown_source")
    return source in allowed_sources