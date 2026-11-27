"""HTTP surface for the ASR profile view (PAR-ASR-002/003/006/007), read and select.

A thin layer over ``asr_profiles``, ``audio_format_choice``, ``asr_wait_policy``,
``asr_capabilities`` and ``asr_fallback``; no rule is re-implemented. The actor is
always ``current_user`` and only the caller's own profile is ever read or changed:
a body never names an actor, user or role. Writes need the CSRF token.
Mounting the blueprint is left to the consumer package (x22).

Contract (fixed by static/js/clinic/asr-profiles-ui.js):

* ``GET  /api/clinic/v1/asr-profiles``
* ``PUT  /api/clinic/v1/asr-profiles/selection`` with ``expected_version``
  Optional ``skip_wait_minutes`` (positive int, minutes) and ``wait_disabled``
  (bool) are stored on the caller's own profile. GET ``wait`` then reports
  ``offer_manual_after_s`` (minutes × 60) or, when disabled, a running view
  with no manual offer.

The stored profile is one body, so it is offered as one entry with mode ``both``.
Separate live/batch choices cannot be persisted by ``asr_profiles._clean_options``;
a PUT may name the profile id for either side or null, nothing else. Only the
source is stored (through ``set_profile``). Prompts are always reported off.

shortcut: the version check and ``set_profile`` are not one atomic compare-and-swap.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import asr_fallback, asr_profiles
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_job_profile import PROMPTS_ENABLED, asr_capability_target
from src.clinic.asr_wait_policy import profile_wait_view
from src.clinic.audio_format_choice import FormatError, choose_upload_format, sample_rate_for
from src.clinic.config import clinic_mode_enabled
from src.database import db

PREFIX = "/api/clinic/v1/asr-profiles"
MAX_BODY_BYTES = 4 * 1024
DEFAULT_SOURCE = "headset"
DEFAULT_FORMATS = ("audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/wav")
UNDETERMINED = "nicht ermittelt"
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_SELECTION_KEYS = {
    "source", "live_profile_id", "batch_profile_id", "expected_version",
    "skip_wait_minutes", "wait_disabled", "save_rule", "profile_mode", "batch_source",
}
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "profile_not_found": 404,
    "stale_version": 409, "invalid_payload": 400,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Versionskonflikt"}

RegistryProvider = Callable[[], AsrCapabilityRegistry]


class ApiError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.extra = code, extra


def _problem(code: str, **extra: Any):
    status = _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


def _guard():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
        return _problem("unauthenticated")
    if request.method != "GET":
        try:
            validate_csrf(request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken"))
        except ValidationError:
            return _problem("unauthorized")
    return None


def _body() -> dict[str, Any]:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        raise ApiError("invalid_payload")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys() or body.keys() - _SELECTION_KEYS:
        raise ApiError("invalid_payload")
    return body


def _wait(profile: asr_profiles.ClinicAsrProfile | None) -> dict[str, Any]:
    """Effective deadline, or the shutdown when this profile disables the wait."""
    return profile_wait_view(profile.options if profile is not None else None)


def _wait_changes(body: dict[str, Any]) -> dict[str, Any]:
    """Validated wait fields from a selection body. Absent fields are left alone."""
    changes: dict[str, Any] = {}
    if "skip_wait_minutes" in body:
        minutes = body["skip_wait_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise ApiError("invalid_payload")
        changes["skip_wait_minutes"] = minutes
    if "wait_disabled" in body:
        flag = body["wait_disabled"]
        if not isinstance(flag, bool):
            raise ApiError("invalid_payload")
        changes["wait_disabled"] = flag
    return changes


def _proven(registry: AsrCapabilityRegistry | None, target: Any, names: list[str]) -> list[str]:
    if registry is None:
        return []
    return [name for name in names if isinstance(registry.check(name, target), CapabilityEvidence)]


def _engine_and_capabilities(profile: asr_profiles.ClinicAsrProfile | None,
                             registry: AsrCapabilityRegistry | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    gpu_url = asr_fallback.gpu_base_url()
    configured_engine = "gpu" if gpu_url else "cpu"
    engine = {"configured": configured_engine, "actual": configured_engine, "reason": UNDETERMINED}
    caps: dict[str, list[str]] = {"configured": [], "proven": []}
    if profile is None or not profile.language or not profile.model:
        return engine, caps
    names = [AsrCapability.BATCH.value, AsrCapability.LIVE.value]
    if profile.hotword_set_ids:
        names.append(AsrCapability.HOTWORD.value)
    caps["configured"] = names
    cpu_target = asr_capability_target(profile.language, profile.model)
    targets = {"cpu": cpu_target}
    if gpu_url:
        targets = {"gpu": dataclasses.replace(cpu_target, base_url=gpu_url), "cpu": cpu_target}
    for name in (configured_engine, "cpu"):  # configured engine first, then the fallback
        proven = _proven(registry, targets[name], names)
        if {"batch", "live"} & set(proven):
            caps["proven"] = proven
            engine["actual"] = name
            engine["reason"] = ("nachgewiesen" if name == configured_engine
                                else "Ausweichlösung: GPU nicht nachgewiesen, CPU nachgewiesen")
            break
    return engine, caps


def _output_format(formats: list[str]) -> dict[str, Any]:
    try:
        choice = choose_upload_format(formats or DEFAULT_FORMATS)
        return {"container": choice.container, "codec": choice.codec, "sample_rate": sample_rate_for(choice, 48000)}
    except FormatError:
        raise ApiError("invalid_payload")


def _view(registry: AsrCapabilityRegistry | None, formats: list[str]) -> dict[str, Any]:
    profile = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=current_user.id).first()
    engine, caps = _engine_and_capabilities(profile, registry)
    entry = ({"id": profile.id, "name": profile.model or "Serverprofil", "version": profile.version}
             if profile is not None else None)
    options = profile.options if profile is not None else {}
    source = options.get("source", DEFAULT_SOURCE)
    separate = options.get("separate_offline_profile") is True
    batch_source = options.get("batch_source") if separate else source
    available_id = entry["id"] if entry else None
    mapped_id = available_id if not separate or batch_source in asr_profiles.SOURCES else None
    return {
        "version": profile.version if profile is not None else 0,
        "profile": entry,
        "source": source,
        "sources": list(asr_profiles.SOURCES),
        "profile_mode": "separate" if separate else "shared",
        "batch_source": batch_source,
        "save_rule": options.get("save_rule", "never"),
        "live_profile_id": mapped_id,
        "batch_profile_id": mapped_id,
        "profiles": [{"id": entry["id"], "name": entry["name"], "mode": "both"}] if entry else [],
        "output_format": _output_format(formats),
        "engine": engine,
        "prompts_enabled": PROMPTS_ENABLED,
        "wait": _wait(profile),
        "capabilities": caps,
    }


def create_asr_profiles_blueprint(registry: RegistryProvider | None = None) -> Blueprint:
    """Blueprint ``clinic_asr_profiles``. ``registry()`` returns the current capability evidence snapshot."""
    bp = Blueprint("clinic_asr_profiles", __name__)
    bp.before_request(_guard)
    bp.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))

    def snapshot() -> AsrCapabilityRegistry | None:
        return registry() if registry is not None else None

    def wrap(view: Callable[[], Any]) -> Callable[[], Any]:
        def run():
            try:
                return view()
            except ApiError as error:
                db.session.rollback()
                return _problem(error.code, **error.extra)
            except asr_profiles.AsrProfileError:
                db.session.rollback()
                return _problem("invalid_payload")
        run.__name__ = view.__name__
        return run

    def show():
        return jsonify(_view(snapshot(), request.args.getlist("supported")))

    def select():
        body = _body()
        version = body.get("expected_version")
        source = body.get("source")
        if isinstance(version, bool) or not isinstance(version, int) or source not in asr_profiles.SOURCES:
            raise ApiError("invalid_payload")
        updates = _wait_changes(body)
        profile = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=current_user.id).first()
        current = profile.version if profile is not None else 0
        if version != current:
            raise ApiError("stale_version", current_version=current)
        offered = profile.id if profile is not None else None
        for key in ("live_profile_id", "batch_profile_id"):
            wanted = body.get(key)
            if wanted is not None and (not isinstance(wanted, str) or wanted != offered):
                raise ApiError("profile_not_found")
        options = dict(profile.options) if profile is not None else {}
        if "save_rule" in body:
            if body["save_rule"] not in asr_profiles.SAVE_RULES:
                raise ApiError("invalid_payload")
            options["save_rule"] = body["save_rule"]
        if "profile_mode" in body:
            mode = body["profile_mode"]
            if mode == "separate" and body.get("batch_source") in asr_profiles.SOURCES:
                options.update(separate_offline_profile=True, batch_source=body["batch_source"])
            elif mode == "shared" and "batch_source" not in body:
                options["separate_offline_profile"] = False
                options.pop("batch_source", None)
            else:
                raise ApiError("invalid_payload")
        elif "batch_source" in body:
            raise ApiError("invalid_payload")
        options["source"] = source
        options.update(updates)
        asr_profiles.set_profile(current_user.id, actor_id=current_user.id, options=options)
        return jsonify(_view(snapshot(), []))

    bp.add_url_rule(PREFIX, "show", wrap(show), methods=["GET"])
    bp.add_url_rule(PREFIX + "/selection", "select", wrap(select), methods=["PUT"])
    return bp


# Endpoint names and methods for the registering package (x22).
ENDPOINTS = (("clinic_asr_profiles.show", "GET", PREFIX), ("clinic_asr_profiles.select", "PUT", PREFIX + "/selection"))
