"""Policy-bound output of the exact original audio and a bounded .eml artifact (PAR-EXP-009).

The caller claims the frozen attempt with ``begin_output`` first. This module
then re-checks authorisation, the revision binding and the client profile's
document-export capability, and returns only bytes whose hash equals the stored
original receipt. Opening a local mail draft with the attachment is NOT done
here (WG-EXP-009 stays open); the .eml is a downloadable artifact only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

from .config import client_capabilities
from .models import ClinicOriginal
from .output_attempts import UNKNOWN, OutputAttempt, OutputAttemptError, OutputSource

CHANNEL_AUDIO = "AUDIO"
FORMAT_ORIGINAL = "original"
FORMAT_EML = "eml"
MAX_EML_AUDIO_BYTES = 10 * 1024 * 1024
EXPORT_PERMITTED = "permission_controlled"
_EXTENSION = re.compile(r"[A-Za-z0-9]{1,8}")
_MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4", "ds2": "application/octet-stream",
         "dss": "application/octet-stream", "ogg": "audio/ogg", "webm": "audio/webm"}


class AudioOutputRefused(OutputAttemptError):
    """The original-audio output was refused; nothing was released."""


@dataclass(frozen=True)
class AudioArtifact:
    idempotency_key: str
    filename: str
    mimetype: str
    data: bytes
    sha256: str
    size: int


def export_allowed(profile: str, capabilities: Mapping[str, Any] | None = None) -> bool:
    """True only for a profile whose contract expressly grants permission-controlled export."""
    profiles = (capabilities if capabilities is not None else client_capabilities()).get("profiles") or {}
    entry = profiles.get(profile)
    return isinstance(entry, Mapping) and entry.get("document_export") == EXPORT_PERMITTED


def _checked(
    attempt: OutputAttempt, current: OutputSource, original: ClinicOriginal, *,
    allowed: bool, profile: str, capabilities: Mapping[str, Any] | None, fmt: str,
) -> None:
    if allowed is not True:
        raise AudioOutputRefused("output not authorised")
    if not export_allowed(profile, capabilities):
        raise AudioOutputRefused("client profile may not download audio")
    if attempt.source != current:
        raise AudioOutputRefused("stale output revision")
    if attempt.channel != CHANNEL_AUDIO or dict(attempt.format_settings).get("format") != fmt:
        raise AudioOutputRefused("attempt is not a frozen original-audio target")
    if attempt.status != UNKNOWN:
        raise AudioOutputRefused("claim the attempt before delivery")
    if original.dictation_id != attempt.source.dictation_id:
        raise AudioOutputRefused("original belongs to another dictation")


def _read_verified(original: ClinicOriginal, read_bytes: Callable[[str], bytes]) -> tuple[bytes, str]:
    data = read_bytes(original.storage_key)
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, original.content_hash) or len(data) != original.size_bytes:
        raise AudioOutputRefused("stored original does not match its receipt")
    return data, digest


def _extension(extension: str) -> str:
    ext = extension.lower().lstrip(".")
    if not _EXTENSION.fullmatch(ext):
        raise AudioOutputRefused("invalid audio extension")
    return ext


def export_original_audio(
    attempt: OutputAttempt, current: OutputSource, original: ClinicOriginal,
    read_bytes: Callable[[str], bytes], *, allowed: bool, profile: str, extension: str,
    capabilities: Mapping[str, Any] | None = None,
) -> AudioArtifact:
    """Return the exact original bytes and hash for a claimed ``original`` audio attempt."""
    _checked(attempt, current, original, allowed=allowed, profile=profile,
             capabilities=capabilities, fmt=FORMAT_ORIGINAL)
    ext = _extension(extension)
    data, digest = _read_verified(original, read_bytes)
    return AudioArtifact(attempt.idempotency_key, f"diktat-{original.id[:8]}.{ext}",
                         _MIME.get(ext, "application/octet-stream"), data, digest, len(data))


def build_eml_artifact(
    attempt: OutputAttempt, current: OutputSource, original: ClinicOriginal,
    read_bytes: Callable[[str], bytes], *, allowed: bool, profile: str, extension: str,
    eml_policy_allows: bool, capabilities: Mapping[str, Any] | None = None,
) -> AudioArtifact:
    """Build an unsent .eml binding the selected audio. No recipient header is ever set."""
    if eml_policy_allows is not True:
        raise AudioOutputRefused(".eml output is not allowed by policy")
    _checked(attempt, current, original, allowed=allowed, profile=profile,
             capabilities=capabilities, fmt=FORMAT_EML)
    if original.size_bytes > MAX_EML_AUDIO_BYTES:
        raise AudioOutputRefused("audio exceeds the .eml size bound")
    ext = _extension(extension)
    data, digest = _read_verified(original, read_bytes)
    name = f"diktat-{original.id[:8]}.{ext}"
    mime = _MIME.get(ext, "application/octet-stream")
    message = EmailMessage(policy=SMTP)
    message["Subject"] = "Diktat (Audio)"
    message["X-Unsent"] = "1"
    message["X-Audio-SHA256"] = digest
    message.set_content("Das ausgewählte Diktat liegt als Audiodatei bei.")
    message.add_attachment(data, maintype=mime.split("/")[0], subtype=mime.split("/")[1], filename=name)
    raw = message.as_bytes()
    return AudioArtifact(attempt.idempotency_key, f"diktat-{original.id[:8]}.eml", "message/rfc822",
                         raw, hashlib.sha256(raw).hexdigest(), len(raw))
