"""Training runner over a pinned local provider (PAR-ASR-004, PAR-ASR-005).

BT-ASR-004 and BT-ASR-005 stay the unchanged oracles and are not marked passed.
The provider here is a synthetic stub: it proves the runner contract only and is
no evidence for a genuine acoustic training capability (G-ASR-PARITY stays blocked).
"""

import threading

import pytest

from src.app import app, db
from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.asr_profiles import ClinicAsrTrainingRun, asr_status, set_profile
from src.clinic.training_manifest import build_manifest
from src.clinic.training_runner import (
    ALREADY_RECORDED,
    CANCELLED,
    COMPLETED,
    FAILED,
    GATE,
    LIMIT_EXCEEDED,
    TIMED_OUT,
    run_training,
)
from src.clinic.training_schedule import TrainingScheduleError
from test_wp_training_manifest import _approved, _candidate, _user, clinic_db  # noqa: F401

TARGET = AsrTarget(
    base_url="http://127.0.0.1:9000", engine="stub-engine", engine_revision="1",
    model="stub-model", model_revision="1", parameters_json="{}",
)
CHECKPOINTS = ["a" * 64, "b" * 64]


class Provider:
    def __init__(self, target=TARGET, result=None, error=None, block=None):
        self.target = target
        self.calls = 0
        self._result = result if result is not None else {
            "model_ref": "local/stub-adapted", "checkpoints": CHECKPOINTS, "audio_seconds": 12.5,
        }
        self._error = error
        self._block = block

    def train(self, manifest, cancel):
        self.calls += 1
        if self._block is not None:
            self._block.wait(5)
            cancel.wait(5)
        if self._error:
            raise self._error
        return self._result


def _registry(kind="language_model"):
    name = AsrCapability.ADAPTATION if kind == "acoustic" else AsrCapability.TRAINING
    return AsrCapabilityRegistry([CapabilityEvidence(
        capability=name, target=TARGET, test_set_id="synthetic-1",
        evidence_ref="evidence/stub", outcome="runtime_passed",
    )])


def _scene():
    author = _user()
    set_profile(author.id, actor_id=author.id, language="de", model="stub")
    scene = _approved(author, "Alpha Beta", None)
    manifest = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="language_model",
        candidates=[_candidate(scene)],
    )
    db.session.commit()  # the provider thread writes on its own connection
    return author, manifest


def _run(author, manifest, provider, **kw):
    kw.setdefault("registry", _registry())
    kw.setdefault("target", TARGET)
    kw.setdefault("expected_profile_version", 1)
    return run_training(author.id, manifest, provider=provider, **kw)


def _runs(author):
    return ClinicAsrTrainingRun.query.filter_by(user_id=author.id).all()


def test_completed_run_persists_provenance_and_keeps_model(clinic_db):
    author, manifest = _scene()
    provider = Provider()

    result = _run(author, manifest, provider)

    assert result["state"] == COMPLETED
    (run,) = _runs(author)
    assert run.engine == "stub-engine" and run.dictation_count == 1
    assert run.audio_seconds == 12.5 and run.text_chars == len("Alpha Beta")
    assert manifest["manifest_hash"] in run.result_ref
    assert all(c in run.result_ref for c in CHECKPOINTS)
    assert asr_status(author.id)["training_proven"] is True
    from src.clinic.asr_profiles import ClinicAsrProfile
    profile = ClinicAsrProfile.query.filter_by(user_id=author.id).one()
    assert (profile.model, profile.version) == ("stub", 1)


def test_retry_after_success_is_idempotent(clinic_db):
    author, manifest = _scene()
    first = _run(author, manifest, Provider())
    provider = Provider()
    again = _run(author, manifest, provider)

    assert again["state"] == ALREADY_RECORDED and again["run_id"] == first["run_id"]
    assert provider.calls == 0 and len(_runs(author)) == 1


def test_capability_unavailable_blocks_gate_and_never_calls_provider(clinic_db):
    author, manifest = _scene()
    provider = Provider()
    other = AsrTarget(**{**TARGET.__dict__, "model_revision": "2"})

    for kw in ({"registry": AsrCapabilityRegistry()}, {"target": other},
               {"registry": _registry("acoustic")}):
        result = _run(author, manifest, provider, **kw)
        assert result == {"state": CAPABILITY_UNAVAILABLE, "gate": GATE, "gate_status": "blocked"}
    assert _run(author, manifest, Provider(target=other))["state"] == CAPABILITY_UNAVAILABLE
    assert provider.calls == 0 and _runs(author) == []


def test_provider_failure_and_bad_result_record_nothing_and_retry_works(clinic_db):
    author, manifest = _scene()

    failed = _run(author, manifest, Provider(error=RuntimeError("oom")))
    bad = _run(author, manifest, Provider(result={"model_ref": "x", "checkpoints": ["nothex"],
                                                  "audio_seconds": 1}))
    assert (failed["state"], failed["reason"]) == (FAILED, "RuntimeError")
    assert (bad["state"], bad["reason"]) == (FAILED, "result_invalid")
    assert failed["active_model_preserved"] and _runs(author) == []

    assert _run(author, manifest, Provider())["state"] == COMPLETED


def test_timeout_and_cancel_are_bounded_and_record_nothing(clinic_db):
    author, manifest = _scene()
    timed_out = _run(author, manifest, Provider(block=threading.Event()), timeout_seconds=0.05)
    assert timed_out["state"] == TIMED_OUT

    cancel = threading.Event()
    cancel.set()
    cancelled = _run(author, manifest, Provider(), cancel=cancel)
    assert cancelled["state"] == CANCELLED
    assert _runs(author) == []


def test_item_cap(clinic_db):
    author, manifest = _scene()
    provider = Provider()
    result = _run(author, manifest, provider, max_items=0)
    assert result["state"] == LIMIT_EXCEEDED and provider.calls == 0


def test_unauthorized_stale_and_tampered_are_refused(clinic_db):
    author, manifest = _scene()
    other = _user()
    provider = Provider()

    for actor in (other.id, True, None):
        with pytest.raises(TrainingScheduleError) as denied:
            run_training(actor, manifest, registry=_registry(), target=TARGET,
                         provider=provider, expected_profile_version=1)
        assert denied.value.code == "unauthorized"
    with pytest.raises(TrainingScheduleError) as tampered:
        _run(author, dict(manifest, scope="pool"), provider)
    with pytest.raises(TrainingScheduleError) as stale:
        _run(author, manifest, provider, expected_profile_version=2)
    assert (tampered.value.code, stale.value.code) == ("manifest_invalid", "stale_version")
    assert provider.calls == 0 and _runs(author) == []


def test_profile_change_during_run_records_nothing(clinic_db):
    author, manifest = _scene()

    class Moving(Provider):
        def train(self, manifest, cancel):
            with app.app_context():  # the provider thread has its own context
                set_profile(author.id, actor_id=author.id, model="stub-2")
            return super().train(manifest, cancel)

    with pytest.raises(TrainingScheduleError) as moved:
        _run(author, manifest, Moving())
    assert moved.value.code == "stale_version" and _runs(author) == []


def test_clinic_mode_off_refuses(clinic_db, monkeypatch):
    author, manifest = _scene()
    monkeypatch.delenv("CLINIC_MODE")
    with pytest.raises(TrainingScheduleError) as off:
        _run(author, manifest, Provider())
    assert off.value.code == "clinic_mode_required"
