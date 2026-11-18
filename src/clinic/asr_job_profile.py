"""Resolve the author's server ASR profile onto one local job.

The stored ``ClinicAsrProfile`` is the only source of model, language, source
and save rule (PAR-ASR-002/006/007). A workstation claim is not applied, so a
new workstation gets the server profile back. Live and batch share that one
body only when the author did not ask for a separate offline profile.

Prompts stay off. Hotwords are a decoder capability, never a prompt. An
unproven capability fails visibly. This module does not record training.

shortcut: dev.defaults leaves model_revision null and has no engine_revision,
so both stay "default" until a runtime probe publishes real revisions.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.asr_profiles import (
    SOURCES,
    ClinicAsrProfile,
    ClinicHotwordSet,
    effective_hotwords,
)
from src.clinic.audio_format_choice import FormatChoice, FormatError, choose_upload_format
from src.clinic.config import client_profile, dev_defaults
from src.database import db

CLINIC_ASR_PARAM = "clinic_asr"
PROMPTS_ENABLED = False
SPEAKER_PROFILES_SUPPORTED = False
_MODES = ("live", "batch")


class AsrJobProfileError(Exception):
    """The job profile could not be resolved or stored."""

    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = {} if detail is None else detail


@dataclass(frozen=True)
class AsrJobProfile:
    """Exact server-side configuration for one ASR run."""

    user_id: int
    profile_id: str
    profile_version: int
    mode: Literal["live", "batch"]
    shared: bool
    source: Literal["headset", "recorder"]
    language: str
    model: str
    model_revision: str
    decoder: str
    engine_revision: str
    audio_format: FormatChoice
    parameters: dict[str, Any]
    parameter_version: int
    hotwords: tuple[str, ...]
    hotword_set_revisions: tuple[tuple[str, int], ...]
    capability_test_set: str
    client_profile: str

    def as_run(self) -> dict[str, Any]:
        """JSON-ready record. ``prompt`` is null and prompts stay disabled."""
        return {
            "user_id": self.user_id,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "mode": self.mode,
            "shared": self.shared,
            "source": self.source,
            "language": self.language,
            "model": self.model,
            "model_revision": self.model_revision,
            "decoder": self.decoder,
            "engine_revision": self.engine_revision,
            "audio_format": {
                "mime": self.audio_format.mime,
                "container": self.audio_format.container,
                "codec": self.audio_format.codec,
                "extension": self.audio_format.extension,
            },
            "parameters": dict(self.parameters),
            "parameter_version": self.parameter_version,
            "hotwords": list(self.hotwords),
            "hotword_set_revisions": [
                {"id": set_id, "revision": revision}
                for set_id, revision in self.hotword_set_revisions
            ],
            "prompt": None,
            "prompts_enabled": PROMPTS_ENABLED,
            "speaker_profiles_supported": SPEAKER_PROFILES_SUPPORTED,
            "capability_test_set": self.capability_test_set,
            "client_profile": self.client_profile,
            "origin": "server",
        }


def _asr_base_url() -> str:
    return os.environ.get("ASR_BASE_URL", "").split("#")[0].strip()


def _asr_setting(name: str) -> str:
    value = (dev_defaults().get("asr") or {}).get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "default"


def asr_capability_target(language: str, model: str) -> AsrTarget:
    """Target a probe must have proven. The prompt parameter is always null."""
    parameters = json.dumps(
        {"language": language, "prompt": None}, sort_keys=True, separators=(",", ":"),
    )
    return AsrTarget(
        base_url=_asr_base_url(),
        engine=_asr_setting("integration_provider"),
        engine_revision=_asr_setting("engine_revision"),
        model=model,
        model_revision=_asr_setting("model_revision"),
        parameters_json=parameters,
    )


def _require_evidence(
    registry: AsrCapabilityRegistry, capability: AsrCapability, target: AsrTarget,
) -> CapabilityEvidence:
    evidence = registry.check(capability.value, target)
    if not isinstance(evidence, CapabilityEvidence) or evidence == CAPABILITY_UNAVAILABLE:
        raise AsrJobProfileError(
            "capability_unavailable",
            {"capability": capability.value, "prompts_enabled": False},
        )
    return evidence


def _bound_revisions(profile: ClinicAsrProfile) -> tuple[tuple[str, int], ...]:
    found: list[tuple[str, int]] = []
    for set_id in profile.hotword_set_ids:
        row = db.session.get(ClinicHotwordSet, set_id)
        if row is not None:
            found.append((row.id, int(row.revision)))
    return tuple(found)


def resolve_asr_job_profile(
    user_id: int,
    mode: Literal["live", "batch"],
    supported_formats: Sequence[str],
    registry: AsrCapabilityRegistry,
    *,
    pool_id: str | None = None,
    lossless_preferred: bool = False,
    expected_profile_version: int | None = None,
    workstation: Mapping[str, Any] | None = None,
) -> AsrJobProfile:
    """Resolve the versioned server profile for one live or batch job.

    ``workstation`` is the local claim from another workplace. Its model,
    language, source and prompt are ignored. The stored profile is the run.

    Raises:
        AsrJobProfileError: missing, stale, incomplete or unproven configuration.
            ``FormatError`` codes from the format chooser are wrapped.
    """
    if mode not in _MODES:
        raise AsrJobProfileError("mode_invalid", {"mode": mode})
    if workstation is not None and not isinstance(workstation, Mapping):
        raise AsrJobProfileError("workstation_invalid")

    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        raise AsrJobProfileError("profile_missing", {"user_id": user_id})
    if expected_profile_version is not None and profile.version != expected_profile_version:
        raise AsrJobProfileError(
            "profile_stale",
            {"expected": expected_profile_version, "actual": profile.version},
        )
    if not profile.language or not profile.model:
        raise AsrJobProfileError(
            "profile_incomplete",
            {"language": profile.language, "model": profile.model},
        )

    source = profile.options.get("source")
    if source not in SOURCES:
        code = "source_missing" if source is None else "source_invalid"
        raise AsrJobProfileError(code, {"source": source})
    if profile.options.get("separate_offline_profile") is True:
        # One stored body cannot honestly be two profiles.
        raise AsrJobProfileError(
            "separate_profile_incomplete",
            {"profile_id": profile.id, "profile_version": profile.version},
        )

    target = asr_capability_target(profile.language, profile.model)
    capability = AsrCapability.BATCH if mode == "batch" else AsrCapability.LIVE
    evidence = _require_evidence(registry, capability, target)

    try:
        audio_format = choose_upload_format(
            supported_formats, lossless_preferred=lossless_preferred,
        )
    except FormatError as exc:
        raise AsrJobProfileError(exc.code, {"prompts_enabled": False}) from exc

    hotwords = tuple(effective_hotwords(user_id, pool_id=pool_id))
    if hotwords:
        _require_evidence(registry, AsrCapability.HOTWORD, target)

    return AsrJobProfile(
        user_id=user_id,
        profile_id=profile.id,
        profile_version=profile.version,
        mode=mode,
        shared=True,
        source=source,
        language=profile.language,
        model=profile.model,
        model_revision=target.model_revision,
        decoder=target.engine,
        engine_revision=target.engine_revision,
        audio_format=audio_format,
        parameters=dict(profile.options),
        parameter_version=profile.version,
        hotwords=hotwords,
        hotword_set_revisions=_bound_revisions(profile),
        capability_test_set=evidence.test_set_id,
        client_profile=client_profile(),
    )


def persist_job_profile(job: Any, resolved: AsrJobProfile) -> dict[str, Any]:
    """Write the run record onto ``job.params`` under ``clinic_asr``.

    Other parameter keys stay. Does not commit, start the job, or enable prompts.
    """
    raw = getattr(job, "params", None)
    if raw in (None, ""):
        current: Any = {}
    else:
        try:
            current = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AsrJobProfileError("job_params_invalid") from exc
    if not isinstance(current, dict):
        raise AsrJobProfileError("job_params_invalid")
    record = resolved.as_run()
    current[CLINIC_ASR_PARAM] = record
    job.params = json.dumps(current, sort_keys=True, ensure_ascii=False)
    return record
