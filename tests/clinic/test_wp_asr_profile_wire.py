"""Server profile, source and format on one local ASR job.

Registry rows are synthetic. They do not prove an engine, a device or a prompt.
"""

from __future__ import annotations

import json
import secrets

import pytest

from src.app import app, db
from src.clinic.asr_capabilities import (
    AsrCapability,
    AsrCapabilityRegistry,
    CapabilityEvidence,
)
from src.clinic.asr_job_profile import (
    AsrJobProfileError,
    asr_capability_target,
    persist_job_profile,
    resolve_asr_job_profile,
)
from src.clinic.asr_profiles import asr_status, create_hotword_set, set_profile
from src.clinic.audio_format_choice import FormatChoice
from src.models import ProcessingJob, Recording, User

SUPPORTED = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/wav"]
LOCAL_URL = "http://127.0.0.1:9000"


def _evidence(capability: str, model: str = "large-v3", outcome: str = "runtime_passed"):
    return CapabilityEvidence(
        AsrCapability(capability),
        asr_capability_target("de", model),
        "test-set-v1",
        "fixture:report-1",
        outcome,  # type: ignore[arg-type]
    )


def _registry(*rows: CapabilityEvidence) -> AsrCapabilityRegistry:
    return AsrCapabilityRegistry(rows)


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("ASR_BASE_URL", LOCAL_URL)
    with app.app_context():
        db.create_all()
        tag = secrets.token_hex(4)
        users = {}
        for name in ("author", "admin"):
            user = User(
                username=f"jobprof_{name}_{tag}",
                email=f"jobprof_{name}_{tag}@local.test",
                password="x",
                is_admin=name == "admin",
            )
            db.session.add(user)
            users[name] = user
        db.session.commit()
        yield {k: v.id for k, v in users.items()}
        db.session.rollback()


def _make_profile(author_id: int, actor_id: int, **options):
    defaults = {"language": "de", "model": "large-v3", "options": {"source": "headset"}}
    defaults.update(options)
    return set_profile(author_id, actor_id=actor_id, **defaults)


def test_capability_target_keeps_prompt_null():
    assert asr_capability_target("de", "large-v3").parameters_json == (
        '{"language":"de","prompt":null}'
    )


def test_resolves_batch_profile_and_persists_on_the_job(ctx):
    profile = _make_profile(ctx["author"], ctx["admin"], options={
        "source": "headset", "save_rule": "always",
    })
    resolved = resolve_asr_job_profile(
        ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")),
    )

    assert resolved.user_id == ctx["author"]
    assert resolved.profile_id == profile.id
    assert resolved.profile_version == profile.version == 1
    assert resolved.parameter_version == 1
    assert resolved.mode == "batch"
    assert resolved.shared is True
    assert resolved.source == "headset"
    assert resolved.language == "de"
    assert resolved.model == "large-v3"
    assert resolved.decoder == "speaches"
    assert resolved.engine_revision == "default"
    assert resolved.model_revision == "default"
    assert resolved.audio_format == FormatChoice("audio/webm", "webm", "opus", ".webm")
    assert resolved.parameters == {"save_rule": "always", "source": "headset"}
    assert resolved.capability_test_set == "test-set-v1"
    assert resolved.client_profile == "managed_web_online"
    assert resolved.hotwords == ()

    run = resolved.as_run()
    assert run["prompts_enabled"] is False
    assert run["prompt"] is None
    assert run["speaker_profiles_supported"] is False
    assert run["origin"] == "server"

    recording = Recording(
        user_id=ctx["author"], title="Job", status="COMPLETED", original_filename="a.wav",
    )
    db.session.add(recording)
    db.session.commit()
    job = ProcessingJob(
        user_id=ctx["author"], recording_id=recording.id, job_type="transcribe",
        status="queued", params=json.dumps({"language": "fr"}),
    )
    db.session.add(job)
    db.session.commit()

    persist_job_profile(job, resolved)
    db.session.commit()
    db.session.refresh(job)
    stored = json.loads(job.params)
    assert stored["language"] == "fr"
    assert stored["clinic_asr"]["profile_id"] == profile.id
    assert stored["clinic_asr"]["profile_version"] == 1
    assert stored["clinic_asr"]["parameter_version"] == 1
    assert stored["clinic_asr"]["model"] == "large-v3"
    assert stored["clinic_asr"]["decoder"] == "speaches"
    assert stored["clinic_asr"]["language"] == "de"
    assert stored["clinic_asr"]["source"] == "headset"
    assert stored["clinic_asr"]["audio_format"]["codec"] == "opus"
    assert stored["clinic_asr"]["prompts_enabled"] is False
    assert job.status == "queued"
    assert asr_status(ctx["author"])["training_proven"] is False


def test_shared_mode_uses_the_same_configuration(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    registry = _registry(_evidence("live"), _evidence("batch"))
    live = resolve_asr_job_profile(ctx["author"], "live", SUPPORTED, registry)
    batch = resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, registry)

    assert live.shared is True and batch.shared is True
    assert (live.model, live.language, live.source, live.profile_version) == (
        batch.model, batch.language, batch.source, batch.profile_version,
    )
    assert live.mode == "live" and batch.mode == "batch"
    assert live.as_run()["prompts_enabled"] is False


def test_recorder_source_is_the_server_selection(ctx):
    _make_profile(ctx["author"], ctx["admin"], options={"source": "recorder", "save_rule": "always"})
    resolved = resolve_asr_job_profile(
        ctx["author"], "live", SUPPORTED, _registry(_evidence("live")),
    )
    assert resolved.source == "recorder"
    assert resolved.mode == "live"


def test_lossless_format_when_preferred(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    resolved = resolve_asr_job_profile(
        ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")),
        lossless_preferred=True,
    )
    assert resolved.audio_format == FormatChoice("audio/wav", "wav", None, ".wav")


def test_hotwords_stay_hotwords_when_proven(ctx):
    hot = create_hotword_set(
        "Eigene", ["Apixaban", "Pneumonie"], actor_id=ctx["author"], owner_user_id=ctx["author"],
    )
    _make_profile(ctx["author"], ctx["admin"], hotword_set_ids=[hot.id])
    resolved = resolve_asr_job_profile(
        ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch"), _evidence("hotword")),
    )
    assert resolved.hotwords == ("Apixaban", "Pneumonie")
    assert resolved.hotword_set_revisions == ((hot.id, 1),)
    assert resolved.as_run()["prompts_enabled"] is False
    assert resolved.as_run()["prompt"] is None


def test_missing_profile_fails_visibly(ctx):
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.code == "profile_missing"


def test_incomplete_profile_fails_visibly(ctx):
    _make_profile(ctx["author"], ctx["admin"], language=None, model="large-v3")
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.code == "profile_incomplete"


def test_missing_source_is_not_invented(ctx):
    _make_profile(ctx["author"], ctx["admin"], options={"save_rule": "never"})
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.code == "source_missing"


def test_separate_offline_flag_is_not_silently_shared(ctx):
    _make_profile(ctx["author"], ctx["admin"], options={
        "source": "headset", "separate_offline_profile": True,
    })
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.code == "separate_profile_incomplete"
    assert asr_status(ctx["author"])["training_proven"] is False


def test_stale_expected_version_fails_and_a_new_save_is_used(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    registry = _registry(_evidence("batch"), _evidence("batch", model="small"))
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(
            ctx["author"], "batch", SUPPORTED, registry, expected_profile_version=99,
        )
    assert exc.value.code == "profile_stale"
    assert exc.value.detail == {"expected": 99, "actual": 1}

    set_profile(
        ctx["author"], actor_id=ctx["admin"], model="small",
        options={"source": "headset"},
    )
    with pytest.raises(AsrJobProfileError) as stale:
        resolve_asr_job_profile(
            ctx["author"], "batch", SUPPORTED, registry, expected_profile_version=1,
        )
    assert stale.value.detail == {"expected": 1, "actual": 2}
    resolved = resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, registry)
    assert resolved.model == "small"
    assert resolved.profile_version == 2
    assert resolved.parameter_version == 2


@pytest.mark.parametrize("mode,proven", [("batch", "live"), ("live", "batch")])
def test_unproven_mode_fails_visibly(ctx, mode, proven):
    _make_profile(ctx["author"], ctx["admin"])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], mode, SUPPORTED, _registry(_evidence(proven)))
    assert exc.value.code == "capability_unavailable"
    assert exc.value.detail == {"capability": mode, "prompts_enabled": False}


def test_unavailable_hotword_does_not_enable_a_prompt(ctx):
    hot = create_hotword_set(
        "Eigene", ["Apixaban"], actor_id=ctx["author"], owner_user_id=ctx["author"],
    )
    _make_profile(ctx["author"], ctx["admin"], hotword_set_ids=[hot.id])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.detail == {"capability": "hotword", "prompts_enabled": False}


def test_unauthorized_remote_target_cannot_be_enabled(ctx, monkeypatch):
    _make_profile(ctx["author"], ctx["admin"])
    monkeypatch.setenv("ASR_BASE_URL", "https://asr.example.invalid")
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")))
    assert exc.value.code == "capability_unavailable"
    assert exc.value.detail["prompts_enabled"] is False


@pytest.mark.parametrize("outcome", ["runtime_failed", "source_review_only", "synthetic"])
def test_non_runtime_evidence_does_not_enable(ctx, outcome):
    _make_profile(ctx["author"], ctx["admin"])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(
            ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch", outcome=outcome)),
        )
    assert exc.value.code == "capability_unavailable"


def test_retry_needs_a_single_passing_snapshot(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    failed = _evidence("batch", outcome="runtime_failed")
    passed = _evidence("batch")
    with pytest.raises(AsrJobProfileError):
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(failed))
    with pytest.raises(AsrJobProfileError):
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(failed, passed))
    resolved = resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(passed))
    assert resolved.mode == "batch"
    assert resolved.as_run()["prompts_enabled"] is False


def test_stale_target_revision_cannot_inherit_proof(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    stale = _evidence("batch")
    from dataclasses import replace
    stale = replace(stale, target=replace(stale.target, model_revision="m2"))
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(ctx["author"], "batch", SUPPORTED, _registry(stale))
    assert exc.value.code == "capability_unavailable"


def test_workstation_claim_does_not_replace_the_server_profile(ctx):
    _make_profile(ctx["author"], ctx["admin"], options={"source": "headset", "save_rule": "ask"})
    claim = {
        "model": "tiny",
        "language": "en",
        "source": "recorder",
        "prompts_enabled": True,
        "initial_prompt": "Ergaenze den Befund",
    }
    resolved = resolve_asr_job_profile(
        ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")), workstation=claim,
    )
    assert resolved.model == "large-v3"
    assert resolved.language == "de"
    assert resolved.source == "headset"
    assert resolved.parameters["save_rule"] == "ask"
    run = resolved.as_run()
    assert run["origin"] == "server"
    assert run["prompts_enabled"] is False
    assert run["prompt"] is None
    assert "initial_prompt" not in run


def test_workstation_claim_must_be_a_mapping(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(
            ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")), workstation="local",  # type: ignore[arg-type]
        )
    assert exc.value.code == "workstation_invalid"


def test_unsupported_format_is_rejected(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(
            ctx["author"], "batch", ["audio/flac"], _registry(_evidence("batch")),
        )
    assert exc.value.code == "no_supported_format"
    assert exc.value.detail["prompts_enabled"] is False


def test_invalid_mode_fails(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    with pytest.raises(AsrJobProfileError) as exc:
        resolve_asr_job_profile(
            ctx["author"], "stream", SUPPORTED, _registry(_evidence("batch")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "mode_invalid"


def test_corrupt_job_params_are_not_overwritten(ctx):
    _make_profile(ctx["author"], ctx["admin"])
    resolved = resolve_asr_job_profile(
        ctx["author"], "batch", SUPPORTED, _registry(_evidence("batch")),
    )

    class _Job:
        params = "not-json"

    with pytest.raises(AsrJobProfileError) as exc:
        persist_job_profile(_Job(), resolved)
    assert exc.value.code == "job_params_invalid"
    assert _Job.params == "not-json"
