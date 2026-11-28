"""Upload and transcription support for Olympus/Philips dictation files (.ds2, .dss).

Two steps, both thin wrappers around :mod:`src.clinic.decoder`:

* At upload, :func:`check_upload` accepts the extension only together with a
  matching magic header (``\\x03ds2`` / ``\\x02dss``). The original is then
  stored unchanged like any other audio; it is never transcoded.
* Before transcription, :func:`decode_for_asr` turns the stored original into a
  16 kHz mono WAV next to it; that WAV is what the normal ASR path sends. A
  decoder failure raises :class:`DictationDecodeError` carrying a German
  message for the recording's ``error_message``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.clinic import decoder
from src.clinic.audio_format_choice import FormatError, declared_matches, parse_mime

DICTATION_EXTENSIONS = ('.ds2', '.dss')
DICTATION_MIME = {'ds2': 'audio/x-ds2', 'dss': 'audio/x-dss'}

MSG_BAD_HEADER = (
    'Die Datei ist keine gültige Diktatdatei (DS2/DSS): Der Dateikopf passt nicht zur Endung.')
MSG_ENCRYPTED = (
    'Die Diktatdatei ist passwortgeschützt (verschlüsselt) und kann nicht transkribiert werden. '
    'Das Original bleibt unverändert gespeichert.')
MSG_UNSUPPORTED = (
    'Das Aufnahmeformat dieser Diktatdatei wird vom Decoder nicht unterstützt. '
    'Das Original bleibt unverändert gespeichert.')
MSG_DECODER_MISSING = (
    'Der Decoder für Diktatdateien (DS2/DSS) ist auf dem Server nicht eingerichtet. '
    'Das Original bleibt unverändert gespeichert.')
MSG_TIMEOUT = (
    'Das Dekodieren der Diktatdatei hat zu lange gedauert und wurde abgebrochen. '
    'Das Original bleibt unverändert gespeichert.')
MSG_EMPTY = (
    'Die Diktatdatei enthält keine Audiodaten. Das Original bleibt unverändert gespeichert.')
MSG_CORRUPT = (
    'Die Diktatdatei konnte nicht dekodiert werden (beschädigt oder nicht unterstütztes '
    'Aufnahmeformat). Das Original bleibt unverändert gespeichert.')
MSG_FORMAT_MISMATCH = (
    'Die Datei entspricht nicht dem angegebenen Audioformat. '
    'Das Original wird nicht umetikettiert übernommen.')
FORMAT_MISMATCH = 'audio_format_mismatch'

# Declared upload formats whose container the existing matcher can prove.
# Opus is the negotiated compressed choice; WAV is the standard one.
_INTAKE_MIMES = {
    'audio/wav': 'audio/wav',
    'audio/wave': 'audio/wav',
    'audio/x-wav': 'audio/wav',
    'audio/opus': 'audio/ogg;codecs=opus',
    'audio/ogg': 'audio/ogg;codecs=opus',
    'audio/webm': 'audio/webm;codecs=opus',
}


class DictationDecodeError(Exception):
    """Decoding failed; ``str(exc)`` is the German user-facing message."""

    def __init__(self, message: str, status: str = decoder.CORRUPT):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class UploadCheck:
    is_dictation: bool
    container: Optional[str] = None
    error: Optional[str] = None


def is_dictation_name(filename: Optional[str]) -> bool:
    return os.path.splitext(filename or '')[1].lower() in DICTATION_EXTENSIONS


def check_upload(filepath, original_filename) -> UploadCheck:
    """Classify a staged upload; an error means the upload must be refused (400).

    A file that is not named .ds2/.dss is not a dictation upload. A file with
    such a name must also start with the matching magic bytes.
    """
    if not is_dictation_name(original_filename):
        return UploadCheck(False)
    with open(filepath, 'rb') as handle:
        container = decoder.detect_container(handle.read(16))
    expected = os.path.splitext(original_filename)[1].lower().lstrip('.')
    if container != expected:
        return UploadCheck(True, container, MSG_BAD_HEADER)
    return UploadCheck(True, container)


def mime_for(container: str) -> str:
    return DICTATION_MIME[container]


def negotiated_intake_mime(declared: Optional[str]) -> Optional[str]:
    """Map a declared upload MIME to one the container matcher understands.

    Returns None when nothing was declared, or when the value is not one of
    the negotiated clinic formats (WAV, Opus in Ogg, Opus in WebM).
    """
    if not declared or not str(declared).strip():
        return None
    try:
        base, params = parse_mime(declared)
    except FormatError:
        return None
    codec = (params.get('codecs') or '').lower()
    if base == 'audio/ogg' and codec and 'opus' not in codec:
        return None
    if base == 'audio/webm' and codec and 'opus' not in codec:
        return None
    return _INTAKE_MIMES.get(base)


def codec_matches_declared(declared: Optional[str], audio_codec: Optional[str]) -> bool:
    """True when a probed codec agrees with a negotiated Opus or WAV declaration.

    A missing probe does not contradict the header check. A probe that names
    another codec does: an Ogg container is not enough to call a file Opus.
    """
    expected = negotiated_intake_mime(declared)
    if expected is None or not audio_codec:
        return True
    codec = audio_codec.lower()
    if 'opus' in expected:
        return codec == 'opus'
    if expected == 'audio/wav':
        return codec.startswith('pcm_')
    return True


def container_matches_declared(filepath, declared: Optional[str]) -> tuple[bool, Optional[str]]:
    """Whether a negotiated MIME was declared, and a refusal message if any.

    The first value is True when ``declared`` is a negotiated clinic format
    (WAV or Opus). The message is set when the header does not match or the
    file cannot be read. The original is only read. An absent or unrelated
    declaration is not this check's concern.
    """
    expected = negotiated_intake_mime(declared)
    if expected is None:
        return False, None
    try:
        with open(filepath, 'rb') as handle:
            head = handle.read(64)
    except OSError:
        return True, MSG_FORMAT_MISMATCH
    if not declared_matches(expected, head):
        return True, MSG_FORMAT_MISMATCH
    return True, None


def sniff_container(filepath) -> Optional[str]:
    """'ds2' or 'dss' when the file starts with that magic, else None."""
    try:
        with open(filepath, 'rb') as handle:
            container = decoder.detect_container(handle.read(16))
    except OSError:
        return None
    return container if container in ('ds2', 'dss') else None


def _german_message(result: decoder.DecodeResult) -> str:
    detail = (result.detail or '').lower()
    if result.status == decoder.DECODER_MISSING:
        return MSG_DECODER_MISSING
    if result.status == decoder.TIMEOUT:
        return MSG_TIMEOUT
    if result.status == decoder.EMPTY:
        return MSG_EMPTY
    if result.status == decoder.UNSUPPORTED:
        return MSG_UNSUPPORTED
    if any(word in detail for word in ('encrypt', 'password', 'passwort')):
        return MSG_ENCRYPTED
    if 'unsupported' in detail or 'not supported' in detail:
        return MSG_UNSUPPORTED
    return MSG_CORRUPT


def wav_seconds(path) -> float:
    """Duration of a decoded WAV in seconds."""
    return decoder._wav_duration(Path(path))


def decode_for_asr(src, out_dir=None) -> str:
    """Decode the stored original to a 16 kHz mono WAV and return its path.

    The WAV lands in ``out_dir`` (default: the original's directory). The
    original is only read. Raises :class:`DictationDecodeError` on any decoder
    status other than ``ok``.
    """
    src = Path(src)
    result = decoder.decode_to_wav(src, out_dir or src.parent)
    if result.status != decoder.OK or not result.output_path:
        raise DictationDecodeError(_german_message(result), result.status)
    return result.output_path
