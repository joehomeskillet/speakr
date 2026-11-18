"""w044: pronunciation training bound to proven local capability (PAR-VOC-007)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.pronunciation_training import (
    MAX_SAMPLES,
    PronunciationProvider,
    PronunciationTrainingError,
    hint_version,
    train_pronunciation,
    training_proven,
    validate_hint,
)
from src.clinic.vocabulary import ClinicPronunciationHint

TARGET = AsrTarget(
    base_url="http://speaches/v1/audio/transcriptions", engine="speaches",
    engine_revision="v0.8.3", model="synthetic-model", model_revision="r1",
    parameters_json="{}",
)
PROVIDER = PronunciationProvider(TARGET, frozenset({"a", "b", "k"}), "synthetic-ipa")
EVIDENCE = CapabilityEvidence(
    AsrCapability.TRAINING, TARGET, "set-1", "ref-1", "runtime_passed")
SAMPLES = [b"synthetic-audio-1", b"synthetic-audio-2"]


def make_hint(phonetic="a b k"):
    return ClinicPronunciationHint(
        id="h1", scope="user", user_id=7, written_form="Wort", phonetic_hint=phonetic,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


class Engine:
    def __init__(self, scores=(0.4, 0.8), fail_times=0):
        self.scores, self.fail_times = list(scores), fail_times
        self.trained = 0
        self.attempts = 0

    def probe(self, target, hint, samples):
        return self.scores.pop(0)

    def train(self, target, hint, samples):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("engine down")
        self.trained += 1


def run(hint=None, engine=None, registry=None, **kw):
    hint = hint or make_hint()
    engine = engine or Engine()
    args = dict(
        actor_id=7, expected_version=hint_version(hint), source_profile="synthetic-ipa",
        samples=SAMPLES, probe=engine.probe, trainer=engine.train)
    args.update(kw)
    reg = registry if registry is not None else AsrCapabilityRegistry([EVIDENCE])
    return train_pronunciation(hint, PROVIDER, reg, **args)


def test_validate_symbols_and_source_profile():
    assert validate_hint(make_hint(), PROVIDER, "synthetic-ipa") == []
    assert validate_hint(make_hint("a z"), PROVIDER, "synthetic-ipa") == ["unknown_symbols:z"]
    assert "source_profile_mismatch" in validate_hint(make_hint(), PROVIDER, "other")


def test_positive_training_records_provenance_and_probe():
    engine = Engine()
    rec = run(engine=engine)
    assert engine.trained == 1
    assert (rec.score_before, rec.score_after) == (0.4, 0.8)
    assert rec.improved and rec.evidence_ref == "ref-1" and rec.test_set_id == "set-1"
    assert rec.model == "synthetic-model" and len(rec.sample_sha256) == 2
    assert training_proven(make_hint(), [rec])


def test_metadata_edit_alone_is_not_proven():
    hint = make_hint()
    assert not training_proven(hint, [])
    rec = run(hint=hint)
    hint.phonetic_hint = "k a"
    hint.updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert not training_proven(hint, [rec])


def test_no_improvement_not_proven():
    rec = run(engine=Engine(scores=(0.5, 0.5)))
    assert not rec.improved and not training_proven(make_hint(), [rec])


def test_unavailable_provider_capability_error_and_no_training():
    engine = Engine()
    with pytest.raises(PronunciationTrainingError) as err:
        run(engine=engine, registry=AsrCapabilityRegistry())
    assert err.value.code == CAPABILITY_UNAVAILABLE
    assert engine.attempts == 0 and engine.scores == [0.4, 0.8]


def test_source_review_only_evidence_is_unavailable():
    weak = CapabilityEvidence(AsrCapability.TRAINING, TARGET, "s", "r", "source_review_only")
    with pytest.raises(PronunciationTrainingError) as err:
        run(registry=AsrCapabilityRegistry([weak]))
    assert err.value.code == CAPABILITY_UNAVAILABLE


def test_unauthorized_actor_and_pool_scope():
    engine = Engine()
    with pytest.raises(PronunciationTrainingError) as err:
        run(engine=engine, actor_id=8)
    assert err.value.code == "forbidden" and engine.attempts == 0
    pool = make_hint()
    pool.scope, pool.user_id, pool.pool_id = "pool", None, "p1"
    with pytest.raises(PronunciationTrainingError):
        run(hint=pool)
    assert run(hint=pool, pool_lead=True).hint_id == "h1"


def test_stale_version():
    with pytest.raises(PronunciationTrainingError) as err:
        run(expected_version="old")
    assert err.value.code == "stale_version"


def test_invalid_metadata_blocks_training():
    with pytest.raises(PronunciationTrainingError) as err:
        run(hint=make_hint("a z"))
    assert err.value.code == "invalid_metadata"


@pytest.mark.parametrize("samples", [[], [b""], [b"x"] * (MAX_SAMPLES + 1)])
def test_samples_bounded(samples):
    with pytest.raises(PronunciationTrainingError) as err:
        run(samples=samples)
    assert err.value.code == "samples_out_of_bounds"


def test_retry_then_success_and_final_failure():
    engine = Engine(fail_times=1)
    assert run(engine=engine).improved and engine.attempts == 2
    engine = Engine(fail_times=5)
    with pytest.raises(PronunciationTrainingError) as err:
        run(engine=engine)
    assert err.value.code == "training_failed" and engine.trained == 0
