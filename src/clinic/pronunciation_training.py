"""Connect phonetic entries to proven local pronunciation training (PAR-VOC-007).

Thin layer over ``vocabulary.ClinicPronunciationHint`` (metadata) and
``asr_capabilities`` (engine proof). Three separate facts are kept apart:

* Metadata: a hint's phonetic text. Editing it never proves training.
* Capability: the registry must hold runtime-passed TRAINING evidence for the
  provider's exact target; otherwise ``CAPABILITY_UNAVAILABLE`` and nothing runs.
* Evidence: a ``TrainingRecord`` exists only after an explicit, authorised
  training action with a measurable before/after probe. It is bound to the
  hint version it trained, so a later edit makes it stale (not proven).

No database writes and no profiles are created here; the caller persists the
returned record. Training input is bounded synthetic-or-consented sample audio
plus the hint text only, never patient profiles.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.vocabulary import ClinicPronunciationHint

MAX_SAMPLES = 20
MAX_SAMPLE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ATTEMPTS = 2


class PronunciationTrainingError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class PronunciationProvider:
    """Declared by the trusted runtime owner, never built from request data."""

    target: AsrTarget
    symbol_set: frozenset[str]
    source_profile: str  # e.g. the notation family the symbols belong to


@dataclass(frozen=True)
class TrainingRecord:
    hint_id: str
    hint_version: str
    actor_id: int
    score_before: float
    score_after: float
    evidence_ref: str
    test_set_id: str
    engine: str
    engine_revision: str
    model: str
    model_revision: str
    sample_sha256: tuple[str, ...]
    trained_at: datetime

    @property
    def improved(self) -> bool:
        return self.score_after > self.score_before


def hint_version(hint: ClinicPronunciationHint) -> str:
    """Optimistic-lock token; changes whenever the hint is edited."""
    stamp = hint.updated_at.isoformat() if hint.updated_at else ""
    return f"{hint.phonetic_hint}|{hint.sounds_like or ''}|{stamp}"


def validate_hint(
    hint: ClinicPronunciationHint, provider: PronunciationProvider, source_profile: str,
) -> list[str]:
    """Return issue codes; empty means the metadata fits the provider."""
    issues: list[str] = []
    if source_profile != provider.source_profile:
        issues.append("source_profile_mismatch")
    unknown = [s for s in hint.phonetic_hint.split() if s not in provider.symbol_set]
    if not hint.phonetic_hint.strip():
        issues.append("empty_phonetic")
    if unknown:
        issues.append("unknown_symbols:" + ",".join(unknown))
    return issues


def _authorised(hint: ClinicPronunciationHint, actor_id: int, pool_lead: bool) -> bool:
    if hint.scope == "user":
        return hint.user_id == actor_id
    return hint.scope == "pool" and pool_lead


def _check_samples(samples: Sequence[bytes]) -> None:
    if not samples or len(samples) > MAX_SAMPLES:
        raise PronunciationTrainingError("samples_out_of_bounds")
    if any(not s or len(s) > MAX_SAMPLE_BYTES for s in samples):
        raise PronunciationTrainingError("samples_out_of_bounds")
    if sum(len(s) for s in samples) > MAX_TOTAL_BYTES:
        raise PronunciationTrainingError("samples_out_of_bounds")


def train_pronunciation(
    hint: ClinicPronunciationHint,
    provider: PronunciationProvider,
    registry: AsrCapabilityRegistry,
    *,
    actor_id: int,
    expected_version: str,
    source_profile: str,
    samples: Sequence[bytes],
    probe: Callable[[AsrTarget, ClinicPronunciationHint, Sequence[bytes]], float],
    trainer: Callable[[AsrTarget, ClinicPronunciationHint, Sequence[bytes]], Any],
    pool_lead: bool = False,
) -> TrainingRecord:
    """Run one explicit local training action and return its evidence record.

    Order: authorisation, stale version, metadata validity, capability,
    sample bounds, probe before, train (bounded retry), probe after.
    """
    if not _authorised(hint, actor_id, pool_lead):
        raise PronunciationTrainingError("forbidden")
    if expected_version != hint_version(hint):
        raise PronunciationTrainingError("stale_version")
    issues = validate_hint(hint, provider, source_profile)
    if issues:
        raise PronunciationTrainingError("invalid_metadata", ";".join(issues))
    evidence = registry.check(AsrCapability.TRAINING, provider.target)
    if evidence == CAPABILITY_UNAVAILABLE or not isinstance(evidence, CapabilityEvidence):
        raise PronunciationTrainingError(CAPABILITY_UNAVAILABLE)
    _check_samples(samples)

    before = float(probe(provider.target, hint, samples))
    last: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            trainer(provider.target, hint, samples)
            break
        except Exception as exc:  # noqa: BLE001 - engine failure, reported below
            last = exc
    else:
        raise PronunciationTrainingError("training_failed", type(last).__name__)
    after = float(probe(provider.target, hint, samples))

    t = provider.target
    return TrainingRecord(
        hint_id=hint.id, hint_version=hint_version(hint), actor_id=actor_id,
        score_before=before, score_after=after, evidence_ref=evidence.evidence_ref,
        test_set_id=evidence.test_set_id, engine=t.engine,
        engine_revision=t.engine_revision, model=t.model, model_revision=t.model_revision,
        sample_sha256=tuple(hashlib.sha256(s).hexdigest() for s in samples),
        trained_at=datetime.now(timezone.utc),
    )


def training_proven(
    hint: ClinicPronunciationHint, records: Sequence[TrainingRecord],
) -> bool:
    """True only with an improving record for the hint's current version."""
    current = hint_version(hint)
    return any(
        r.hint_id == hint.id and r.hint_version == current and r.improved for r in records
    )
