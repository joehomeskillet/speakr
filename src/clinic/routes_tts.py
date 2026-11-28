"""Local read-aloud routes for canonical selections and pronunciation entries (PAR-LIVE-008, PAR-VOC-007).

A thin HTTP layer over ``src/clinic/local_tts.py``. The actor always comes from
the session. The per-object decision, the stored text and its current version
come from the injected ``load`` adapter of the later consumer packages; the
play decision itself is made by ``local_tts.synthesize`` through
``src/clinic/policy.py``. The client only names the object, the version it saw
and a selection. Without a verified local capability the routes answer
CAPABILITY_UNAVAILABLE and the engine is never called.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic import local_tts
from src.clinic.config import clinic_mode_enabled, is_local_model_url
from src.clinic.local_tts_engine import SpeachesLocalTtsEngine
from src.clinic.parity_dispatch import _problem
from src.clinic.policy import RecordingFacts, Subject

MAX_BODY_BYTES = 16 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
SELECTION_KEYS = frozenset({"start", "end", "cursor"})
SELECTION_KIND = "selection"
PRONUNCIATION_KIND = "pronunciation"

# Public problem code per local_tts error code; anything else is a bad request.
_PROBLEM_FOR = {
    "unauthorized": "POLICY_DENIED",
    local_tts.CAPABILITY_UNAVAILABLE: "CAPABILITY_UNAVAILABLE",
    "voice_missing": "CAPABILITY_UNAVAILABLE",
    "stale_version": "REVISION_CONFLICT",
    "timeout": "DEPENDENCY_UNAVAILABLE",
    "synthesis_failed": "DEPENDENCY_UNAVAILABLE",
}


@dataclass(frozen=True)
class Loaded:
    """Stored object as the adapter sees it. For a pronunciation, ``document.text`` is the entry."""

    document: local_tts.CanonicalText
    subject: Subject
    recording: RecordingFacts
    text_revision_id: str | None = None


# load(actor_id, kind, object_id) -> Loaded, or None when absent or not authorized.
Load = Callable[[int, str, str], Loaded | None]
Capability = Callable[[], local_tts.LocalTtsCapability | None]


def _json_body() -> dict[str, Any]:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        return _bad()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _bad()
    return body


def _bad() -> NoReturn:
    raise _Refusal("INVALID_PAYLOAD")


class _Refusal(Exception):
    pass


def _parse(body: dict[str, Any]) -> tuple[str, str, int | str, dict[str, Any], str | None]:
    """Return kind, object id, revision selector, selection kwargs and requested voice."""
    if body.keys() - ({"dictation_id", "pronunciation_id", "version", "text_revision_id", "voice"} | SELECTION_KEYS):
        _bad()
    ids = [k for k in ("dictation_id", "pronunciation_id") if k in body]
    if len(ids) != 1 or not isinstance(body[ids[0]], str) or not body[ids[0]]:
        _bad()
    revision: int | str
    if "text_revision_id" in body:
        raw_revision = body["text_revision_id"]
        if ids[0] != "dictation_id" or "version" in body or not isinstance(raw_revision, str):
            _bad()
        try:
            revision = str(UUID(raw_revision))
        except ValueError:
            _bad()
        if revision != raw_revision.lower():
            _bad()
    else:
        revision = body.get("version")
        if type(revision) is not int or revision < 1:
            _bad()
    voice = body.get("voice")
    if voice is not None and not isinstance(voice, str):
        _bad()
    selection = {k: body[k] for k in SELECTION_KEYS if k in body}
    if any(type(v) is not int for v in selection.values()):
        _bad()
    if ids[0] == "pronunciation_id":
        if selection:
            _bad()
        return PRONUNCIATION_KIND, body[ids[0]], revision, {}, voice
    return SELECTION_KIND, body[ids[0]], revision, selection, voice


class TtsApi:
    """Mount ``blueprint`` once, after supplying the adapters.

    ``capability()`` returns the verified local capability or None; it is read
    per request so an absent capability is visible immediately. ``engine`` is
    the injected local engine, or None.
    """

    def __init__(
        self,
        load: Load,
        capability: Capability,
        engine: local_tts.LocalTtsEngine | None,
        browser_voices: Sequence[local_tts.BrowserVoice] = (),
    ):
        self._load, self._capability, self._engine = load, capability, engine
        self._browser_voices = tuple(browser_voices)
        self.blueprint = Blueprint("clinic_tts", __name__)
        self.blueprint.add_url_rule("/api/clinic/v1/tts/capability", "capability", self._status, methods=["GET"])
        self.blueprint.add_url_rule("/api/clinic/v1/tts/speak", "speak", self._speak, methods=["POST"])
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))

    @classmethod
    def for_local_speaches(cls, load: Load, endpoint: str) -> TtsApi:
        """A blank endpoint disables TTS. Verify the local inventory before each use."""
        endpoint = endpoint.strip()
        if not endpoint.lower().startswith(("http://", "https://")) or not is_local_model_url(endpoint):
            return cls(load, lambda: None, None)
        engine = SpeachesLocalTtsEngine(endpoint)

        def capability() -> local_tts.LocalTtsCapability | None:
            report = engine.availability()
            if not report.available or report.model_id is None or report.voice_id is None:
                return None
            return local_tts.declare_capability(
                endpoint=endpoint, model_id=report.model_id, voice_id=report.voice_id,
            )

        return cls(load, capability, engine)

    def _authenticated(self) -> bool:
        return bool(
            current_user.is_authenticated
            and current_user.is_active
            and session.get("_user_id") == current_user.get_id()
        )

    def _available_capability(self) -> local_tts.LocalTtsCapability | None:
        if not callable(getattr(self._engine, "synthesize", None)):
            return None
        try:
            capability = local_tts._require_capability(self._capability())
            local_tts.resolve_voice(capability, None)
        except local_tts.TtsError:
            return None
        return capability

    def _status(self):
        if not clinic_mode_enabled():
            abort(404)
        if not self._authenticated():
            return _problem("UNAUTHORIZED")
        capability = self._available_capability()
        if request.args.get("details") == "1":
            return jsonify(available=capability is not None, voice=capability.voice_id if capability else None)
        return jsonify(available=capability is not None)

    def _speak(self):
        if not clinic_mode_enabled():
            abort(404)
        if not self._authenticated():
            return _problem("UNAUTHORIZED")
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except CsrfValidationError:
            return _problem("FORBIDDEN")
        try:
            kind, object_id, revision, selection, voice = _parse(_json_body())
        except _Refusal as error:
            return _problem(str(error))
        # No document load or synthesis while the capability is absent.
        capability = self._available_capability()
        if capability is None:
            return _problem("CAPABILITY_UNAVAILABLE")
        try:
            loaded = self._load(current_user.id, kind, object_id)
        except Exception:
            return _problem("DEPENDENCY_UNAVAILABLE")
        if not isinstance(loaded, Loaded) or loaded.subject.user_id != current_user.id:
            return _problem("POLICY_DENIED")
        extra = {"pronunciation": loaded.document.text} if kind == PRONUNCIATION_KIND else selection
        try:
            if isinstance(revision, str):
                local_tts._require_play(loaded.subject, loaded.recording)
                if revision != loaded.text_revision_id:
                    return _problem("REVISION_CONFLICT")
                version = loaded.document.version
            else:
                version = revision
            result = local_tts.synthesize(
                loaded.document,
                current_version=version,
                subject=loaded.subject,
                recording=loaded.recording,
                capability=capability,
                engine=self._engine,
                requested_voice=voice,
                browser_voices=self._browser_voices,
                **extra,
            )
        except local_tts.TtsError as error:
            return _problem(_PROBLEM_FOR.get(error.code, "INVALID_PAYLOAD"))
        if len(result.audio) > MAX_AUDIO_BYTES:
            return _problem("DEPENDENCY_UNAVAILABLE")
        response = Response(result.audio, mimetype="audio/wav")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-TTS-Model"] = result.model_id
        response.headers["X-TTS-Voice"] = result.voice_id
        return response
