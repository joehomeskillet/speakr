"""Local adaptation provider on reviewed synthetic pairs (PAR-ASR-004, Q-17).

The isolated lab check: text-only, matched audio and unmatched audio, then the
artifact version and its evaluation. BT-ASR-004 stays the unchanged oracle and
is not marked passed. No approved local provider is wired, so the capability
stays open, every case is not_run and the job stays refused.
"""

import pytest

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.training_runner import (
    GATE,
    NOT_RUN,
    OPEN,
    REFUSED,
    AdaptationArtifact,
    ReviewedPair,
    evaluate_provider,
)
from src.clinic.training_schedule import TrainingScheduleError

TARGET = AsrTarget(
    base_url="http://127.0.0.1:9000", engine="local-lab", engine_revision="1",
    model="local-lab", model_revision="1", parameters_json="{}",
)
OTHER = AsrTarget(**{**TARGET.__dict__, "model_revision": "2"})

TEXT = ReviewedPair("text-only", "language_model", "Alpha Beta", reviewed=True)
MATCHED = ReviewedPair(
    "audio-matched", "acoustic", "Alpha Beta", reviewed=True,
    audio_ref="synthetic://matched", matched=True, alignment=True,
)
UNMATCHED = ReviewedPair(
    "audio-unmatched", "acoustic", "Alpha Beta", reviewed=True,
    audio_ref="synthetic://other", matched=False, alignment=False,
)
PAIRS = [TEXT, MATCHED, UNMATCHED]


class _Provider:
    """Stand-in for a selected local engine. It does not prove the engine exists."""

    def __init__(self, target=TARGET, scores=None):
        self.target = target
        self.calls = []
        self._scores = {} if scores is None else scores

    def score(self, pair, artifact):
        self.calls.append((pair.pair_id, artifact.version))
        return self._scores.get(pair.pair_id)


def _registry(*names):
    return AsrCapabilityRegistry([
        CapabilityEvidence(
            capability=name, target=TARGET, test_set_id="synthetic-lab",
            evidence_ref="evidence/lab", outcome="runtime_passed",
        )
        for name in names
    ])


def _evaluate(provider, registry, artifact=None, pairs=PAIRS):
    return evaluate_provider(
        provider, pairs, registry=registry, target=TARGET, artifact=artifact,
    )


def test_absent_capability_is_not_run_and_refused(clinic_on):
    """No approved provider: the open capability is recorded, jobs stay refused."""
    provider = _Provider()
    artifact = AdaptationArtifact("lab-0", "language_model")

    for registry in (
        AsrCapabilityRegistry(),
        _registry(AsrCapability.TRAINING),
        _registry(AsrCapability.ADAPTATION),
    ):
        result = _evaluate(provider, registry, artifact)
        assert result["capability"] == OPEN
        assert result["job"] == REFUSED
        assert result["gate"] == GATE and result["gate_status"] == "blocked"
        assert result["artifact"] is None
        assert [case["status"] for case in result["cases"]] == [NOT_RUN, NOT_RUN, NOT_RUN]
    assert provider.calls == []

    mismatched = _evaluate(_Provider(target=OTHER), _registry(
        AsrCapability.TRAINING, AsrCapability.ADAPTATION,
    ))
    assert mismatched["capability"] == OPEN and mismatched["job"] == REFUSED


def test_text_only_and_audio_cases_stay_separate(clinic_on):
    provider = _Provider()
    registry = _registry(AsrCapability.TRAINING, AsrCapability.ADAPTATION)

    result = _evaluate(provider, registry)

    by_id = {case["pair_id"]: case for case in result["cases"]}
    assert by_id["text-only"] == {
        "pair_id": "text-only", "kind": "language_model", "status": "accepted",
    }
    assert by_id["audio-matched"]["status"] == "accepted"
    assert by_id["audio-matched"]["kind"] == "acoustic"
    assert by_id["audio-unmatched"] == {
        "pair_id": "audio-unmatched", "kind": "acoustic",
        "status": REFUSED, "reason": "alignment_unconfirmed",
    }
    assert result["artifact"] is None and result["job"] == REFUSED
    assert provider.calls == []


def test_unconfirmed_alignment_never_trains_audio(clinic_on):
    registry = _registry(AsrCapability.TRAINING, AsrCapability.ADAPTATION)
    gaps = [
        MATCHED.__class__(**{**MATCHED.__dict__, "pair_id": "no-audio", "audio_ref": None}),
        MATCHED.__class__(**{**MATCHED.__dict__, "pair_id": "unmatched", "matched": False}),
        MATCHED.__class__(**{**MATCHED.__dict__, "pair_id": "unaligned", "alignment": None}),
        MATCHED.__class__(**{**MATCHED.__dict__, "pair_id": "denied", "alignment": False}),
    ]
    result = _evaluate(_Provider(), registry, pairs=gaps)
    assert {case["reason"] for case in result["cases"]} == {"alignment_unconfirmed"}
    assert result["job"] == REFUSED


def test_hotwords_and_summaries_never_count(clinic_on):
    registry = _registry(AsrCapability.TRAINING, AsrCapability.ADAPTATION)
    pairs = [
        ReviewedPair("hotwords", "language_model", "Alpha", reviewed=True, artifact_kind="hotwords"),
        ReviewedPair("summary", "language_model", "Alpha", reviewed=True, artifact_kind="summary"),
        ReviewedPair("other", "language_model", "Alpha", reviewed=True, artifact_kind="lexicon"),
    ]
    result = _evaluate(_Provider(), registry, pairs=pairs)
    reasons = [case["reason"] for case in result["cases"]]
    assert reasons == ["not_adaptation", "not_adaptation", "not_model_artifact"]
    assert result["job"] == REFUSED


def test_artifact_counts_only_after_independent_holdout_evaluation(clinic_on):
    registry = _registry(AsrCapability.TRAINING, AsrCapability.ADAPTATION)
    holdout = ReviewedPair(
        "held-out", "language_model", "Gamma Delta", reviewed=True, holdout=True,
    )
    artifact = AdaptationArtifact("lab-3", "language_model")
    provider = _Provider(scores={"held-out": 0.91})

    unevaluated = _evaluate(provider, registry, artifact)
    assert unevaluated["artifact"]["version"] == "lab-3"
    assert unevaluated["artifact"]["counts_as_adaptation"] is False
    assert unevaluated["artifact"]["reason"] == "not_independently_evaluated"
    assert unevaluated["job"] == REFUSED
    assert provider.calls == []

    evaluated = _evaluate(provider, registry, artifact, pairs=[*PAIRS, holdout])
    shown = evaluated["artifact"]
    assert shown["version"] == "lab-3" and shown["kind"] == "language_model"
    assert shown["counts_as_adaptation"] is True
    assert shown["evaluated_pairs"] == 1 and len(shown["evaluation_ref"]) == 64
    assert evaluated["job"] == "eligible"
    assert provider.calls == [("held-out", "lab-3")]
    # The parity oracle is unchanged: this lab check does not close the gate.
    assert evaluated["gate_status"] == "blocked"


def test_unreviewed_pair_is_refused_and_clinic_mode_off_blocks(clinic_on, monkeypatch):
    registry = _registry(AsrCapability.TRAINING, AsrCapability.ADAPTATION)
    raw = ReviewedPair("raw", "language_model", "Alpha", reviewed=False)
    result = _evaluate(_Provider(), registry, pairs=[raw])
    assert result["cases"][0]["reason"] == "not_reviewed"

    monkeypatch.delenv("CLINIC_MODE", raising=False)
    with pytest.raises(TrainingScheduleError) as off:
        _evaluate(_Provider(), registry)
    assert off.value.code == "clinic_mode_required"


def test_capability_token_stays_the_refusal():
    assert CAPABILITY_UNAVAILABLE == "CAPABILITY_UNAVAILABLE"
