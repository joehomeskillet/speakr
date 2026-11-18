"""Read-only capability decisions for clinic adapters before they handle content."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .config import is_local_model_url

CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


class AsrCapability(str, Enum):
    BATCH = "batch"
    LIVE = "live"
    HOTWORD = "hotword"
    NEGATIVE_LEXICON = "negative_lexicon"
    PRONUNCIATION = "pronunciation"
    TRAINING = "training"
    ADAPTATION = "adaptation"
    CHECKPOINT = "checkpoint"
    ALIGNMENT = "alignment"
    LOCAL_TTS = "local_tts"


@dataclass(frozen=True)
class AsrTarget:
    base_url: str
    engine: str
    engine_revision: str
    model: str
    model_revision: str
    parameters_json: str  # Canonical JSON snapshot of effective parameters; {} is explicit.


@dataclass(frozen=True)
class CapabilityEvidence:
    """A trusted probe's result; never construct this from profile or request data."""

    capability: AsrCapability
    target: AsrTarget
    test_set_id: str
    evidence_ref: str
    outcome: Literal["runtime_passed", "runtime_failed", "source_review_only", "synthetic"]


class AsrCapabilityRegistry:
    """A snapshot of current evidence supplied by the trusted runtime probe owner.

    A runtime pass refers to a recorded evaluation (including the evaluated
    model artifact for training). This registry does not run or verify probes.
    """

    def __init__(self, evidence: Iterable[CapabilityEvidence] = ()) -> None:
        self._evidence = tuple(evidence)

    def check(
        self, capability: str, target: AsrTarget
    ) -> CapabilityEvidence | Literal["CAPABILITY_UNAVAILABLE"]:
        """Resolve only this target; callers must stop on an unavailable result."""
        if not is_local_model_url(target.base_url) or not all(
            value.strip() for value in (
                target.engine, target.engine_revision, target.model,
                target.model_revision,
            )
        ):
            return CAPABILITY_UNAVAILABLE

        try:
            requested = AsrCapability(capability)
            parameters = json.loads(target.parameters_json)
        except ValueError:
            return CAPABILITY_UNAVAILABLE
        if not isinstance(parameters, dict):
            return CAPABILITY_UNAVAILABLE

        matches = [
            row for row in self._evidence if row.capability == requested and row.target == target
        ]
        if len(matches) == 1:
            row = matches[0]
            if (
                row.outcome == "runtime_passed" and row.test_set_id.strip()
                and row.evidence_ref.strip()
            ):
                return row
        return CAPABILITY_UNAVAILABLE

    def training_proven(self, target: AsrTarget) -> bool:
        """Engine-level proof, independent of an author's recorded training history."""
        return isinstance(self.check(AsrCapability.TRAINING, target), CapabilityEvidence)
