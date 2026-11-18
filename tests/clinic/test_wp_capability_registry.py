"""Synthetic contract tests; these fixtures do not prove any ASR engine feature."""

from dataclasses import replace

import pytest

from src.clinic.asr_capabilities import (
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)

TARGET = AsrTarget("http://127.0.0.1:9000", "test-engine", "e1", "test-model", "m1",
                   '{"language":"de","prompt":null}')
CAPABILITIES = (
    "batch", "live", "hotword", "negative_lexicon", "pronunciation", "training",
    "adaptation", "checkpoint", "alignment", "local_tts",
)


def _evidence(capability: str) -> CapabilityEvidence:
    # Simulate a producer's runtime pass to exercise the positive lookup path.
    return CapabilityEvidence(AsrCapability(capability), TARGET, "test-set-v1",
                              "fixture:report-1", "runtime_passed")


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_capabilities_require_independent_evidence(capability: str) -> None:
    row = _evidence(capability)
    registry = AsrCapabilityRegistry([row])

    assert registry.check(capability, TARGET) == row
    for other in (*CAPABILITIES, "unknown"):
        if other != capability:
            assert registry.check(other, TARGET) == "CAPABILITY_UNAVAILABLE"
    assert registry.training_proven(TARGET) is (capability == "training")


@pytest.mark.parametrize("stale", [
    replace(TARGET, engine_revision="e2"),
    replace(TARGET, model_revision="m2"),
    replace(TARGET, parameters_json='{"language":"de","prompt":"test hint"}'),
    replace(TARGET, base_url="http://127.0.0.1:9001"),
])
def test_stale_or_different_target_cannot_inherit_proof(stale: AsrTarget) -> None:
    assert AsrCapabilityRegistry([_evidence("batch")]).check("batch", stale) == "CAPABILITY_UNAVAILABLE"


def test_unauthorized_remote_target_cannot_be_enabled() -> None:
    remote = replace(TARGET, base_url="https://asr.example.invalid")
    row = replace(_evidence("batch"), target=remote)
    registry = AsrCapabilityRegistry([row, _evidence("batch")])

    assert registry.check("batch", remote) == "CAPABILITY_UNAVAILABLE"


@pytest.mark.parametrize("incomplete", [
    replace(TARGET, model_revision=" "),
    replace(TARGET, parameters_json="unrecorded"),
    replace(TARGET, parameters_json="null"),
])
def test_runtime_pass_requires_complete_target_metadata(incomplete: AsrTarget) -> None:
    row = replace(_evidence("batch"), target=incomplete)
    assert AsrCapabilityRegistry([row]).check("batch", incomplete) == "CAPABILITY_UNAVAILABLE"


def test_unsupported_request_retry_remains_side_effect_free() -> None:
    row = _evidence("training")
    registry = AsrCapabilityRegistry([row, replace(row, outcome="runtime_failed")])
    for _ in range(2):
        assert registry.check("training", TARGET) == "CAPABILITY_UNAVAILABLE"
        assert not registry.training_proven(TARGET)


def test_configuration_and_non_runtime_records_cannot_prove_training() -> None:
    row = _evidence("training")
    configured = replace(TARGET, parameters_json='{"training_proven":true}')
    assert not AsrCapabilityRegistry().training_proven(configured)
    for invalid in (
        replace(row, outcome="source_review_only"),
        replace(row, outcome="synthetic"),
        replace(row, outcome="runtime_failed"),
        replace(row, evidence_ref=" "),
        replace(row, test_set_id=""),
    ):
        assert AsrCapabilityRegistry([invalid]).check("training", TARGET) == "CAPABILITY_UNAVAILABLE"
