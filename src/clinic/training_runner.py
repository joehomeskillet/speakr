"""Run a scheduled local adaptation through the pinned provider (CLINIC_MODE only).

Input is a sealed training manifest (``training_manifest.build_manifest``) that
``training_schedule`` accepted. The runner invokes exactly one provider whose
``target`` equals the pinned ``AsrTarget`` the capability registry proved for
this training kind. Without runtime evidence it returns ``CAPABILITY_UNAVAILABLE``
and G-ASR-PARITY stays blocked; it never fakes a run.

Resources are bounded (item cap, wall-clock timeout) and cancellable: on a
timeout or cancel the provider's ``cancel`` event is set and nothing is recorded.
A finished run is persisted once through ``record_training_run`` with the
manifest hash, profile version and checkpoint hashes in ``result_ref``.
The runner never edits the profile: the previously active model stays active on
every non-success path (promotion belongs to checkpoint evaluation).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
)
from src.clinic.asr_profiles import ClinicAsrTrainingRun, record_training_run
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicTranscriptRevision
from src.clinic.training_schedule import (
    TrainingScheduleError,
    _check_fresh,
    _profile_version,
    _verified_items,
)
from src.database import db

COMPLETED = "completed"
ALREADY_RECORDED = "already_recorded"
FAILED = "failed"
CANCELLED = "cancelled"
TIMED_OUT = "timed_out"
LIMIT_EXCEEDED = "limit_exceeded"
INSUFFICIENT_DATA = "insufficient_data"
GATE = "G-ASR-PARITY"

MAX_ITEMS = 5000
TIMEOUT_SECONDS = 4 * 3600
_RESULT_REF_MAX = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def run_training(
    actor_id: int,
    manifest: dict,
    *,
    registry: AsrCapabilityRegistry,
    target: AsrTarget,
    provider,
    expected_profile_version: int,
    cancel: threading.Event | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_items: int = MAX_ITEMS,
) -> dict:
    """Execute one intent. ``provider.train(manifest, cancel)`` returns
    ``{"model_ref": str, "checkpoints": [sha256 hex, ...], "audio_seconds": float}``.
    """
    if not clinic_mode_enabled():
        raise TrainingScheduleError("clinic_mode_required")
    items = _verified_items(actor_id, manifest)
    user_id = manifest["subject_user_id"]
    profile_version = _profile_version(user_id, expected_profile_version)
    _check_fresh(items)

    name = AsrCapability.ADAPTATION if manifest["kind"] == "acoustic" else AsrCapability.TRAINING
    proof = registry.check(name.value, target)
    if proof == CAPABILITY_UNAVAILABLE or getattr(provider, "target", None) != target:
        return {"state": CAPABILITY_UNAVAILABLE, "gate": GATE, "gate_status": "blocked"}

    base = {"manifest_hash": manifest["manifest_hash"], "profile_version": profile_version}
    existing = _recorded(user_id, base)
    if existing is not None:
        return {**base, "state": ALREADY_RECORDED, "run_id": existing.id}
    if not items:
        return {**base, "state": INSUFFICIENT_DATA}
    if len(items) > max_items:
        return {**base, "state": LIMIT_EXCEEDED, "max_items": max_items}

    cancel = cancel or threading.Event()
    started = datetime.now(timezone.utc)
    outcome = _invoke(provider, manifest, cancel, timeout_seconds)
    finished = datetime.now(timezone.utc)
    if outcome["state"] != COMPLETED:
        return {**base, **outcome, "active_model_preserved": True}
    result = outcome["result"]

    # The profile may have moved while the provider ran: record nothing then.
    _profile_version(user_id, expected_profile_version)
    checkpoints = result["checkpoints"]
    root = hashlib.sha256("\n".join(checkpoints).encode("ascii")).hexdigest()
    ref = {**base, "model_ref": result["model_ref"], "checkpoints_root": root,
           "checkpoints": checkpoints}
    result_ref = json.dumps(ref, sort_keys=True, separators=(",", ":"))
    if len(result_ref) > _RESULT_REF_MAX:
        del ref["checkpoints"]
        result_ref = json.dumps(ref, sort_keys=True, separators=(",", ":"))
    run = record_training_run(
        user_id, kind=manifest["kind"], engine=target.engine, started_at=started,
        finished_at=finished, audio_seconds=result["audio_seconds"],
        text_chars=_text_chars(items), dictation_count=len(items),
        result_ref=result_ref, actor_id=actor_id,
    )
    return {**base, "state": COMPLETED, "run_id": run.id, "checkpoints_root": root,
            "active_model_preserved": True}


def _invoke(provider, manifest, cancel, timeout_seconds) -> dict:
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = provider.train(manifest, cancel)
        except Exception as exc:  # provider failure is a state, never a half-recorded run
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        cancel.set()
        return {"state": TIMED_OUT}
    if cancel.is_set():
        return {"state": CANCELLED}
    if "error" in box:
        return {"state": FAILED, "reason": box["error"]}
    result = box.get("result")
    if not _valid(result):
        return {"state": FAILED, "reason": "result_invalid"}
    return {"state": COMPLETED, "result": result}


def _valid(result) -> bool:
    if not isinstance(result, dict):
        return False
    checkpoints = result.get("checkpoints")
    seconds = result.get("audio_seconds")
    return (
        isinstance(result.get("model_ref"), str)
        and bool(result["model_ref"].strip())
        and isinstance(checkpoints, list)
        and bool(checkpoints)
        and all(isinstance(c, str) and _SHA256.match(c) for c in checkpoints)
        and isinstance(seconds, (int, float))
        and not isinstance(seconds, bool)
        and seconds >= 0
    )


def _recorded(user_id, base):
    rows = ClinicAsrTrainingRun.query.filter(
        ClinicAsrTrainingRun.user_id == user_id,
        ClinicAsrTrainingRun.result_ref.contains(base["manifest_hash"]),
    ).all()
    for row in rows:
        ref = json.loads(row.result_ref)
        if ref.get("manifest_hash") == base["manifest_hash"] and (
            ref.get("profile_version") == base["profile_version"]
        ):
            return row
    return None


def _text_chars(items) -> int:
    total = 0
    for item in items:
        revision = db.session.get(ClinicTranscriptRevision, item["transcript_revision_id"])
        total += len(revision.body) if revision is not None else 0
    return total
