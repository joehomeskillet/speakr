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

``evaluate_provider`` is the lab check of that provider on reviewed synthetic
pairs (PAR-ASR-004, owner Q-17). Text-only and acoustic stay separate. Acoustic
pairs count only with confirmed alignment of that text to that audio. The only
adaptation evidence is an independently evaluated model artifact; hotword lists
and summaries never qualify. With no approved provider the capability stays
open and the job is refused — the check never invents a pass.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
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
NOT_RUN = "not_run"
REFUSED = "refused"
OPEN = "open"

# Hotword lists and summaries are decoder bias / post-processing, never a model.
NON_ADAPTATION = frozenset({"hotword", "hotwords", "summary", "summaries"})
KINDS = frozenset({"language_model", "acoustic"})

MAX_ITEMS = 5000
TIMEOUT_SECONDS = 4 * 3600
_RESULT_REF_MAX = 500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReviewedPair:
    """One reviewed synthetic pair. ``alignment`` is confirmed only when True.

    ``matched`` is whether the reviewed text belongs to this audio. Acoustic
    training requires both; text-only training ignores audio.
    """

    pair_id: str
    kind: str  # language_model (text-only) or acoustic (text+audio)
    text: str
    reviewed: bool
    audio_ref: str | None = None
    matched: bool = False
    alignment: bool | None = None  # True = confirmed; anything else is not
    artifact_kind: str = "model"  # model | hotword | summary
    holdout: bool = False  # held out of training; the independent evaluation set


@dataclass(frozen=True)
class AdaptationArtifact:
    """A local model artifact. Its version is what the lab inspects.

    It counts as adaptation only when ``evaluate_provider`` scores it on
    held-out reviewed pairs (``holdout=True``). The provider does not grade
    itself, and hotword or summary stand-ins never count.
    """

    version: str
    kind: str  # language_model | acoustic


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


def _approved(provider, registry: AsrCapabilityRegistry, target: AsrTarget) -> bool:
    """The registry proved this exact local target and the provider is that target."""
    if getattr(provider, "target", None) != target:
        return False
    training = registry.check(AsrCapability.TRAINING.value, target)
    adaptation = registry.check(AsrCapability.ADAPTATION.value, target)
    return training != CAPABILITY_UNAVAILABLE and adaptation != CAPABILITY_UNAVAILABLE


def _pair_decision(pair: ReviewedPair) -> str | None:
    """None when the pair may train; otherwise the refusal reason."""
    if pair.kind not in KINDS:
        return "kind_invalid"
    if pair.artifact_kind in NON_ADAPTATION:
        return "not_adaptation"
    if pair.artifact_kind != "model":
        return "not_model_artifact"
    if not pair.reviewed or not isinstance(pair.text, str) or not pair.text.strip():
        return "not_reviewed"
    if pair.kind == "acoustic":
        if not pair.audio_ref or not pair.matched or pair.alignment is not True:
            return "alignment_unconfirmed"
    return None


def _score(provider, pair: ReviewedPair, artifact: AdaptationArtifact):
    """The provider's own score of one held-out pair. None when it cannot score."""
    score = getattr(provider, "score", None)
    if not callable(score):
        return None
    try:
        value = score(pair, artifact)
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return None
    return float(value)


def _evaluation(provider, pairs, decisions, artifact: AdaptationArtifact | None) -> dict | None:
    """Inspect the artifact version. It counts only when held-out pairs were scored."""
    if artifact is None:
        return None
    base = {
        "version": artifact.version, "kind": artifact.kind,
        "evaluation_ref": None, "counts_as_adaptation": False,
    }
    if (
        artifact.kind not in KINDS
        or not isinstance(artifact.version, str)
        or not artifact.version.strip()
    ):
        return {**base, "reason": "not_independently_evaluated"}
    holdout = [
        pair for pair, reason in zip(pairs, decisions)
        if reason is None and pair.holdout and pair.kind == artifact.kind
    ]
    scores = [(pair.pair_id, _score(provider, pair, artifact)) for pair in holdout]
    measured = [item for item in scores if item[1] is not None]
    if not measured:
        return {**base, "reason": "not_independently_evaluated"}
    joined = "\n".join(f"{pair_id}:{score:.6f}" for pair_id, score in measured)
    return {
        **base,
        "evaluation_ref": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        "evaluated_pairs": len(measured),
        "counts_as_adaptation": True,
    }


def evaluate_provider(
    provider,
    pairs: list[ReviewedPair],
    *,
    registry: AsrCapabilityRegistry,
    target: AsrTarget,
    artifact: AdaptationArtifact | None = None,
) -> dict:
    """Lab evaluation of one local provider on reviewed synthetic pairs.

    No approved provider (registry proof for this exact target, both kinds)
    leaves the capability ``open``: every case is ``not_run`` and the job is
    ``refused``. An artifact counts only after this check scores it on held-out
    reviewed pairs. Nothing here records a training run or promotes a model.
    """
    if not clinic_mode_enabled():
        raise TrainingScheduleError("clinic_mode_required")
    if not _approved(provider, registry, target):
        return {
            "capability": OPEN,
            "job": REFUSED,
            "gate": GATE,
            "gate_status": "blocked",
            "cases": [
                {"pair_id": pair.pair_id, "kind": pair.kind, "status": NOT_RUN}
                for pair in pairs
            ],
            "artifact": None,
        }

    decisions = [_pair_decision(pair) for pair in pairs]
    cases = []
    for pair, reason in zip(pairs, decisions):
        if reason is None and not pair.holdout:
            cases.append({
                "pair_id": pair.pair_id, "kind": pair.kind, "status": "accepted",
            })
        elif reason is None:
            cases.append({
                "pair_id": pair.pair_id, "kind": pair.kind, "status": "evaluated",
            })
        else:
            cases.append({
                "pair_id": pair.pair_id, "kind": pair.kind,
                "status": REFUSED, "reason": reason,
            })

    shown = _evaluation(provider, pairs, decisions, artifact)
    trainable = any(case["status"] == "accepted" for case in cases)
    counts = shown is not None and shown["counts_as_adaptation"]
    return {
        "capability": "available",
        "job": "eligible" if trainable and counts else REFUSED,
        "gate": GATE,
        "gate_status": "blocked",  # a lab check is not the parity oracle
        "cases": cases,
        "artifact": shown,
    }


def _text_chars(items) -> int:
    total = 0
    for item in items:
        revision = db.session.get(ClinicTranscriptRevision, item["transcript_revision_id"])
        total += len(revision.body) if revision is not None else 0
    return total
