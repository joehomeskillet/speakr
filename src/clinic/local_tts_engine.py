"""Local Speaches engine for the pinned TTS contract (PAR-LIVE-008).

Availability is only the approved model ``PINNED_MODEL_ID`` and the proven
German voice ``de_DE``, and only when both are already on a local endpoint.
``preview(document, fixture)`` speaks ``document.text`` through ``local_tts``
and does not write the document. A fixture without that model or voice raises
``TtsError``. This module never calls the Speaches download route and never
sends the speech request to a second host.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.clinic.config import is_local_model_url
from src.clinic.local_tts import (
    CAPABILITY_UNAVAILABLE,
    LANGUAGE_DE,
    MAX_CHARS,
    PINNED_MODEL_ID,
    CanonicalText,
    LocalTtsEngine,
    TtsEngineFailed,
    TtsError,
    declare_capability,
    synthesize,
)
from src.clinic.policy import RecordingFacts, Subject

APPROVED_VOICE_ID = "de_DE"
MODELS_PATH = "/v1/models"
VOICES_PATH = "/v1/audio/voices"
SPEECH_PATH = "/v1/audio/speech"
SYNTHETIC_GERMAN = "Synthetischer Satz ohne Patientendaten."
_WAV = b"RIFF"
_TIMEOUT_TEXT = "Die Sprachausgabe hat das Zeitlimit überschritten."


@dataclass(frozen=True)
class InstalledVoice:
    """One voice already present on the local server."""

    voice_id: str
    language: str


@dataclass(frozen=True)
class LocalInventory:
    """Installed models and voices. This is an observation, not a download request."""

    endpoint: str
    models: tuple[str, ...] = ()
    voices: tuple[InstalledVoice, ...] = ()
    timeout: bool = False


@dataclass(frozen=True)
class Availability:
    """The only model and voice this engine will report. Both are None when unavailable."""

    available: bool
    model_id: str | None
    voice_id: str | None
    message: str
    code: str


def approved_local_fixture() -> LocalInventory:
    """The pre-approved local model and the proven German test voice."""
    return LocalInventory(
        "http://speaches/v1/audio/speech",
        (PINNED_MODEL_ID,),
        (InstalledVoice(APPROVED_VOICE_ID, LANGUAGE_DE),),
    )


def unavailable_fixture() -> LocalInventory:
    """Approved model present, German voice absent."""
    return LocalInventory("http://speaches/v1/audio/speech", (PINNED_MODEL_ID,), ())


def report_availability(fixture: LocalInventory) -> Availability:
    """Report the approved pair, or neither. Other installed ids are not reported."""
    if not is_local_model_url(fixture.endpoint) or PINNED_MODEL_ID not in _model_ids(fixture):
        return _no("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
    if _approved_voice(fixture) is not None:
        return Availability(True, PINNED_MODEL_ID, APPROVED_VOICE_ID, "", "")
    if _voice_in_other_language(fixture):
        return _no("Fehlende deutsche Stimme.", "voice_missing")
    return _no("Fehlende Stimme.", "voice_missing")


def preview(
    document: CanonicalText, fixture: LocalInventory, *, engine: LocalTtsEngine | None = None
) -> bytes:
    """Speak ``document.text`` with the approved voice. Does not write ``document``."""
    if not isinstance(document, CanonicalText) or not isinstance(document.text, str):
        raise TtsError("Die Textfassung ist ungültig.", "invalid_text")
    snapshot = (document.text, document.version)
    try:
        return _preview(document, fixture, engine)
    finally:
        if (document.text, document.version) != snapshot:
            raise TtsError("Der Diktattext wurde verändert.", "text_mutated")


def inventory_from_bodies(endpoint: str, models_body: Any, voices_body: Any) -> LocalInventory:
    """Build an inventory from GET /v1/models and GET /v1/audio/voices JSON."""
    models = _body_ids(models_body)
    voices = tuple(
        InstalledVoice(item_id, language)
        for item_id, language in _voice_rows(voices_body)
    )
    return LocalInventory(endpoint, tuple(models), voices)


class SpeachesLocalTtsEngine:
    """``local_tts.LocalTtsEngine`` against one local Speaches origin.

    ``client`` is injectable for tests. The owned client ignores proxy
    variables and does not follow redirects.
    """

    def __init__(self, endpoint: str, client: httpx.Client | None = None):
        self.endpoint = endpoint
        self._client = client
        self.calls: list[tuple[str, str]] = []

    def availability(self) -> Availability:
        if not is_local_model_url(self.endpoint):
            return report_availability(LocalInventory(self.endpoint))
        try:
            with self._session(5.0) as client:
                return report_availability(self._load(client, 5.0))
        except TtsError as exc:
            return Availability(False, None, None, str(exc), exc.code)
        except TimeoutError as exc:
            return Availability(False, None, None, str(exc), "timeout")
        except TtsEngineFailed as exc:
            return Availability(False, None, None, str(exc), "synthesis_failed")

    def synthesize(self, text: str, *, voice_id: str, model_id: str, timeout_s: float) -> bytes:
        """Return audio for ``text``. Does not accept a document, so it cannot write one."""
        if not isinstance(text, str) or text.strip() == "":
            raise TtsError("Nichts zum Vorlesen.", "empty_selection")
        if len(text) > MAX_CHARS:
            raise TtsError("Der Text ist zu lang.", "text_too_long")
        if not is_local_model_url(self.endpoint):
            raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
        with self._session(timeout_s) as client:
            inventory = self._load(client, timeout_s)
            report = report_availability(inventory)
            if not report.available:
                raise TtsError(report.message, report.code)
            if voice_id != APPROVED_VOICE_ID or model_id != PINNED_MODEL_ID:
                raise TtsError("Fehlende Stimme.", "voice_missing")
            response = self._send(
                client,
                "POST",
                SPEECH_PATH,
                {"model": model_id, "input": text, "voice": voice_id, "response_format": "wav"},
                timeout_s,
            )
        return _audio(response)

    @contextmanager
    def _session(self, timeout_s: float) -> Iterator[httpx.Client]:
        if self._client is not None:
            yield self._client
            return
        client = httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout_s)
        try:
            yield client
        finally:
            client.close()

    def _load(self, client: httpx.Client, timeout_s: float) -> LocalInventory:
        models_body = self._json(self._send(client, "GET", MODELS_PATH, None, timeout_s))
        if PINNED_MODEL_ID not in _body_ids(models_body):
            return LocalInventory(self.endpoint)
        voices_body = self._json(self._send(client, "GET", VOICES_PATH, None, timeout_s))
        return inventory_from_bodies(self.endpoint, models_body, voices_body)

    def _send(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        payload: Mapping[str, str] | None,
        timeout_s: float,
    ) -> httpx.Response:
        # POST /v1/models/{id} is the Speaches download route. Speech is the only POST.
        if method == "POST" and path != SPEECH_PATH:
            raise TtsEngineFailed("Modelldownload ist nicht erlaubt.")
        url = _url(self.endpoint, path)
        if not is_local_model_url(url):
            raise TtsError("Lokale Sprachausgabe ist nicht verfügbar.", CAPABILITY_UNAVAILABLE)
        # Keep test call tracing off long-lived production engines.
        if self._client is not None:
            self.calls.append((method, path))
        try:
            if payload is None:
                response = client.request(method, url, timeout=timeout_s)
            else:
                response = client.request(method, url, json=dict(payload), timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise TimeoutError(_TIMEOUT_TEXT) from exc
        except httpx.HTTPError as exc:
            raise TtsEngineFailed("Die Sprachausgabe ist fehlgeschlagen.") from exc
        if response.status_code >= 300:
            raise TtsEngineFailed("Die Sprachausgabe ist fehlgeschlagen.")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None


class _ReadOnlySpeaker:
    """In-memory speech for an injected fixture. Holds no document."""

    def __init__(self, fixture: LocalInventory):
        self.fixture = fixture
        self.calls: list[dict[str, str]] = []

    def synthesize(self, text: str, *, voice_id: str, model_id: str, timeout_s: float) -> bytes:
        self.calls.append({"text": text, "voice_id": voice_id, "model_id": model_id})
        if self.fixture.timeout:
            raise TimeoutError(_TIMEOUT_TEXT)
        if voice_id != APPROVED_VOICE_ID or model_id != PINNED_MODEL_ID:
            raise TtsError("Fehlende Stimme.", "voice_missing")
        return _WAV


def _preview(
    document: CanonicalText, fixture: LocalInventory, engine: LocalTtsEngine | None
) -> bytes:
    report = report_availability(fixture)
    if not report.available:
        raise TtsError(report.message, report.code)
    speaker = engine if engine is not None else _ReadOnlySpeaker(fixture)
    result = synthesize(
        document,
        current_version=document.version,
        subject=Subject(user_id=1),
        recording=RecordingFacts(recording_id=1, owner_id=1),
        capability=declare_capability(endpoint=fixture.endpoint, voice_id=APPROVED_VOICE_ID),
        engine=speaker,
    )
    return result.audio


def _no(message: str, code: str) -> Availability:
    return Availability(False, None, None, message, code)


def _model_ids(fixture: LocalInventory) -> tuple[str, ...]:
    models = fixture.models
    if isinstance(models, tuple) and all(isinstance(item, str) for item in models):
        return models
    return ()


def _approved_voice(fixture: LocalInventory) -> InstalledVoice | None:
    for voice in _voices(fixture):
        if voice.voice_id == APPROVED_VOICE_ID and voice.language == LANGUAGE_DE:
            return voice
    return None


def _voice_in_other_language(fixture: LocalInventory) -> bool:
    return any(
        voice.voice_id == APPROVED_VOICE_ID and voice.language != LANGUAGE_DE
        for voice in _voices(fixture)
    )


def _voices(fixture: LocalInventory) -> tuple[InstalledVoice, ...]:
    voices = fixture.voices
    if isinstance(voices, tuple) and all(isinstance(voice, InstalledVoice) for voice in voices):
        return voices
    return ()


def _body_ids(body: Any) -> tuple[str, ...]:
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return ()
    ids = []
    for item in rows:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return tuple(ids)


def _voice_rows(body: Any) -> tuple[tuple[str, str], ...]:
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return ()
    found = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        language = item.get("language")
        if isinstance(language, str):
            found.append((item["id"], language))
    return tuple(found)


def _url(endpoint: str, path: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}{path}"


def _audio(response: httpx.Response) -> bytes:
    body = response.content
    if not body or body.lstrip().startswith((b"{", b"[")):
        raise TtsEngineFailed("Die Sprachausgabe ist fehlgeschlagen.")
    return bytes(body)
