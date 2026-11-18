"""Bounded local text-to-speech (PAR-LIVE-008, PAR-VOC-007).

Speaks only through one pinned, declared local capability. The engine is
injected and this module opens no socket. It reads a canonical selection or
a pronunciation string and does not write the transcript, store a voice
profile, or treat reading aloud as training.

A missing engine is ``CAPABILITY_UNAVAILABLE``. A browser voice is used only
when the caller names it and its backend is verified local; it is never
chosen on its own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.clinic.config import is_local_model_url
from src.clinic.policy import RecordingFacts, Subject, decide

CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"

# Spike pin: Speaches Kokoro ONNX. Voice id and endpoint are declared per
# deployment; the model id and revision are not interchangeable.
PINNED_MODEL_ID = "speaches-ai/Kokoro-82M-v1.0-ONNX"
PINNED_REVISION = "kokoro-82m-v1.0-onnx"
LANGUAGE_DE = "de"
LANG_ID_DE = 1031
MAX_CHARS = 2000
DEFAULT_TIMEOUT_S = 15.0
MAX_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2
LOCAL_BACKEND = "local"
PLAY = "play"


class TtsError(ValueError):
    """The read-aloud was rejected. ``code`` is stable; the text is German."""

    def __init__(self, message: str, code: str):
        self.code = code
        super().__init__(message)


class TtsTransient(Exception):
    """The local engine failed in a way one retry may clear."""


class TtsEngineFailed(Exception):
    """The local engine failed and a retry will not help."""


class LocalTtsEngine(Protocol):
    def synthesize(
        self, text: str, *, voice_id: str, model_id: str, timeout_s: float
    ) -> bytes:
        """Return audio bytes for ``text``. Raise TtsTransient, TtsEngineFailed or TimeoutError."""


@dataclass
class CanonicalText:
    """Canonical document. ``synthesize`` only reads ``text`` and ``version``."""

    text: str
    version: int


@dataclass(frozen=True)
class LocalTtsCapability:
    """One declared local TTS capability. ``verified`` means a prior local check, not this call."""

    model_id: str
    revision: str
    voice_id: str
    language: str
    endpoint: str
    backend: str
    verified: bool
    enabled: bool = True
    timeout_s: float = DEFAULT_TIMEOUT_S
    lang_id: int = LANG_ID_DE


@dataclass(frozen=True)
class BrowserVoice:
    """A browser-reported voice. ``backend`` is where the audio would be made."""

    voice_id: str
    backend: str
    verified: bool


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    spoken_text: str
    voice_id: str
    model_id: str
    revision: str
    attempts: int
    training_proven: bool
    voice_profile_stored: bool


def declare_capability(
    *,
    model_id: str = PINNED_MODEL_ID,
    revision: str = PINNED_REVISION,
    voice_id: str = "de_DE",
    language: str = LANGUAGE_DE,
    endpoint: str = "http://speaches/v1/audio/speech",
    backend: str = LOCAL_BACKEND,
    verified: bool = True,
    enabled: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    lang_id: int = LANG_ID_DE,
) -> LocalTtsCapability:
    """Build a capability declaration. Defaults are the pinned local German voice."""
    return LocalTtsCapability(
        model_id=model_id,
        revision=revision,
        voice_id=voice_id,
        language=language,
        endpoint=endpoint,
        backend=backend,
        verified=verified,
        enabled=enabled,
        timeout_s=timeout_s,
        lang_id=lang_id,
    )


def synthesize(
    document: CanonicalText,
    *,
    current_version: int,
    subject: Subject,
    recording: RecordingFacts,
    capability: LocalTtsCapability | None,
    engine: LocalTtsEngine | None,
    start: int | None = None,
    end: int | None = None,
    cursor: int = 0,
    pronunciation: str | None = None,
    requested_voice: str | None = None,
    browser_voices: Sequence[BrowserVoice] = (),
) -> SynthesisResult:
    """Speak the selection or the pronunciation. Does not write ``document``."""
    _require_play(subject, recording)
    _require_current(document, current_version)
    cap = _require_capability(capability)
    if engine is None or not callable(getattr(engine, "synthesize", None)):
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    voice_id = resolve_voice(cap, requested_voice, browser_voices)
    spoken = _spoken(document.text, start, end, cursor, pronunciation)
    if len(spoken) > MAX_CHARS:
        raise TtsError("Der Text ist zu lang.", "text_too_long")
    audio, attempts = _speak(engine, spoken, voice_id, cap)
    return SynthesisResult(
        audio=audio,
        spoken_text=spoken,
        voice_id=voice_id,
        model_id=cap.model_id,
        revision=cap.revision,
        attempts=attempts,
        training_proven=False,
        voice_profile_stored=False,
    )


def resolve_voice(
    capability: LocalTtsCapability,
    requested_voice: str | None,
    browser_voices: Sequence[BrowserVoice] = (),
) -> str:
    """Pick the voice. Never returns a browser voice unless the caller named a verified local one."""
    if not capability.enabled or not isinstance(capability.voice_id, str) or capability.voice_id.strip() == "":
        raise TtsError("Fehlende Stimme.", "voice_missing")
    if requested_voice is None:
        return capability.voice_id
    if not isinstance(requested_voice, str) or requested_voice.strip() == "":
        raise TtsError("Fehlende Stimme.", "voice_missing")
    if requested_voice == capability.voice_id:
        return capability.voice_id
    match = _browser_match(browser_voices, requested_voice)
    if match is not None and match.verified and match.backend == LOCAL_BACKEND:
        return match.voice_id
    if match is not None:
        raise TtsError("Browserstimme ohne geprüften lokalen Betrieb.", "voice_unverified")
    raise TtsError("Fehlende Stimme.", "voice_missing")


def _require_play(subject: Subject, recording: RecordingFacts) -> None:
    if not isinstance(subject, Subject) or not isinstance(recording, RecordingFacts):
        raise TtsError("Vorlesen ist nicht erlaubt.", "unauthorized")
    if not decide(subject, PLAY, recording).allowed:
        raise TtsError("Vorlesen ist nicht erlaubt.", "unauthorized")


def _require_current(document: CanonicalText, current_version: int) -> None:
    if not isinstance(document, CanonicalText) or not isinstance(document.text, str):
        raise TtsError("Die Textfassung ist ungültig.", "invalid_text")
    for value in (document.version, current_version):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TtsError("Die Textfassung ist ungültig.", "invalid_version")
    if document.version != current_version:
        raise TtsError("Die Textfassung ist veraltet.", "stale_version")


def _require_capability(capability: LocalTtsCapability | None) -> LocalTtsCapability:
    if not isinstance(capability, LocalTtsCapability) or not capability.verified:
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    if capability.model_id != PINNED_MODEL_ID or capability.revision != PINNED_REVISION:
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    if capability.backend != LOCAL_BACKEND or not is_local_model_url(capability.endpoint):
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    timeout = capability.timeout_s
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    if timeout <= 0 or timeout > MAX_TIMEOUT_S:
        raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    if capability.language != LANGUAGE_DE or capability.lang_id != LANG_ID_DE:
        raise TtsError("Fehlende deutsche Stimme.", "voice_missing")
    return capability


def _spoken(
    text: str,
    start: int | None,
    end: int | None,
    cursor: int,
    pronunciation: str | None,
) -> str:
    if pronunciation is not None:
        if not isinstance(pronunciation, str) or pronunciation.strip() == "":
            raise TtsError("Nichts zum Vorlesen.", "empty_selection")
        return pronunciation
    length = len(text)
    if start is None and end is None:
        spoken = text[_index(cursor, length):]
    elif start is None or end is None:
        raise TtsError("Die Auswahl ist ungültig.", "invalid_selection")
    else:
        lo, hi = _index(start, length), _index(end, length)
        if hi < lo:
            raise TtsError("Die Auswahl ist ungültig.", "invalid_selection")
        spoken = text[lo:hi]
    if spoken.strip() == "":
        raise TtsError("Nichts zum Vorlesen.", "empty_selection")
    return spoken


def _index(value: int, length: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > length:
        raise TtsError("Die Auswahl ist ungültig.", "invalid_selection")
    return value


def _browser_match(voices: Sequence[BrowserVoice], requested: str) -> BrowserVoice | None:
    found = None
    for voice in voices:
        if not isinstance(voice, BrowserVoice):
            raise TtsError("Fehlende Stimme.", "voice_missing")
        if voice.voice_id == requested and found is None:
            found = voice
    return found


def _speak(engine: LocalTtsEngine, text: str, voice_id: str, cap: LocalTtsCapability) -> tuple[bytes, int]:
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            audio = engine.synthesize(
                text, voice_id=voice_id, model_id=cap.model_id, timeout_s=float(cap.timeout_s)
            )
        except TtsTransient as exc:
            last = exc
            continue
        except TimeoutError as exc:
            raise TtsError("Die Sprachausgabe hat das Zeitlimit überschritten.", "timeout") from exc
        except TtsEngineFailed as exc:
            raise TtsError("Die Sprachausgabe ist fehlgeschlagen.", "synthesis_failed") from exc
        if not isinstance(audio, (bytes, bytearray)) or len(audio) == 0:
            raise TtsError("Die Sprachausgabe ist fehlgeschlagen.", "synthesis_failed")
        return bytes(audio), attempt
    raise TtsError("Die Sprachausgabe ist fehlgeschlagen.", "synthesis_failed") from last
