"""Clinic profile on normal transcription jobs (resolve_transcription_params).

Registry rows are synthetic. They do not prove an engine, a device or a prompt.
"""

from __future__ import annotations

import secrets

import pytest

from src.app import app, db
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_job_profile import asr_capability_target
from src.clinic.asr_profiles import set_profile
from src.models import Recording, User
from src.services.transcription_defaults import resolve_transcription_params

LOCAL_URL = "http://127.0.0.1:9000"


def _evidence(capability: str, model: str = "large-v3"):
    return CapabilityEvidence(
        AsrCapability(capability), asr_capability_target("de", model),
        "test-set-v1", "fixture:report-1", "runtime_passed",
    )


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("ASR_BASE_URL", LOCAL_URL)
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.delenv("CLINIC_ASR_PROMPTS_ENABLED", raising=False)
    monkeypatch.delenv("CLINIC_GPU_ASR_BASE_URL", raising=False)
    with app.app_context():
        db.create_all()
        tag = secrets.token_hex(4)
        users = {}
        for name in ("author", "admin"):
            user = User(username=f"w118_{name}_{tag}", email=f"w118_{name}_{tag}@local.test",
                        password="x", is_admin=name == "admin")
            db.session.add(user)
            users[name] = user
        db.session.commit()
        yield users
        db.session.rollback()


def _recording(user):
    rec = Recording(user_id=user.id, title="r", status="COMPLETED",
                    audio_path="local://x.mp3", original_filename="x.mp3")
    db.session.add(rec)
    db.session.commit()
    return rec


def _profile(ctx, **kw):
    return set_profile(ctx["author"].id, actor_id=ctx["admin"].id, language="de",
                       model="large-v3", options={"source": "headset"}, **kw)


def _registry(*rows):
    app.extensions["clinic_capability_registry"] = AsrCapabilityRegistry(rows)


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    app.extensions.pop("clinic_capability_registry", None)


def test_german_default_and_lang_id_without_profile(ctx):
    p = resolve_transcription_params(_recording(ctx["author"]))
    assert p["language"] == "de"
    assert p["clinic_asr"]["lang_id"] == 1031
    assert p["clinic_asr"]["status"] == "profile_missing"
    assert p["clinic_asr"]["engine"] == {"selected": "cpu", "fallback": None, "actual": None}


def test_gpu_default_with_cpu_fallback_recorded(ctx, monkeypatch):
    monkeypatch.setenv("CLINIC_GPU_ASR_BASE_URL", "http://127.0.0.1:9100")
    p = resolve_transcription_params(_recording(ctx["author"]))
    assert p["clinic_asr"]["engine"] == {"selected": "gpu", "fallback": "cpu", "actual": None}


def test_profile_applied_with_revision_and_core_job_survives_missing_capabilities(ctx):
    profile = _profile(ctx)
    _registry(_evidence("batch"))
    p = resolve_transcription_params(_recording(ctx["author"]), {"language": ""})
    rec = p["clinic_asr"]
    assert rec["status"] == "profile"
    assert rec["profile"] == profile.id and rec["profile_version"] == 1
    assert rec["model_revision"] == "default" and rec["engine_revision"] == "default"
    assert p["transcription_model"] == "large-v3"
    assert rec["negative_lexicon"] == {"status": "CAPABILITY_UNAVAILABLE"}
    assert rec["checkpoint"] == {"status": "CAPABILITY_UNAVAILABLE"}


def test_negative_lexicon_and_checkpoint_consumed_when_proven(ctx):
    _profile(ctx)
    _registry(_evidence("batch"), _evidence("negative_lexicon"), _evidence("checkpoint"))
    rec = resolve_transcription_params(_recording(ctx["author"]))["clinic_asr"]
    assert rec["negative_lexicon"]["evidence_ref"] == "fixture:report-1"
    assert rec["negative_lexicon"]["terms"] == []
    assert rec["checkpoint"] == {"evidence_ref": "fixture:report-1"}


def test_unproven_batch_capability_keeps_job_on_defaults(ctx):
    _profile(ctx)
    p = resolve_transcription_params(_recording(ctx["author"]))
    assert p["clinic_asr"]["status"] == "capability_unavailable"
    assert p["language"] == "de"


def test_explicit_request_values_beat_the_profile(ctx):
    _profile(ctx)
    _registry(_evidence("batch"))
    p = resolve_transcription_params(
        _recording(ctx["author"]), {"language": "en", "transcription_model": "small"})
    assert p["language"] == "en" and p["clinic_asr"]["lang_id"] is None
    assert p["transcription_model"] == "small"


def test_prompts_and_hotwords_off_by_default(ctx):
    p = resolve_transcription_params(
        _recording(ctx["author"]), {"initial_prompt": "Befund", "hotwords": "Apixaban"})
    assert p["initial_prompt"] is None and p["hotwords"] is None
    assert p["clinic_asr"]["prompts_enabled"] is False


def test_enabled_prompts_share_one_55_word_cap(ctx, monkeypatch):
    monkeypatch.setenv("CLINIC_ASR_PROMPTS_ENABLED", "1")
    p = resolve_transcription_params(_recording(ctx["author"]), {
        "initial_prompt": " ".join(["p"] * 40), "hotwords": " ".join(["h"] * 40)})
    assert len(p["initial_prompt"].split()) == 40
    assert len(p["hotwords"].split()) == 15
    long = resolve_transcription_params(
        _recording(ctx["author"]), {"initial_prompt": " ".join(["w"] * 200)})
    assert len(long["initial_prompt"].split()) == 55
    assert long["hotwords"] is None


def test_stale_profile_version_is_recorded_not_applied(ctx):
    _profile(ctx)
    _registry(_evidence("batch"))
    first = resolve_transcription_params(_recording(ctx["author"]))["clinic_asr"]["profile_version"]
    set_profile(ctx["author"].id, actor_id=ctx["admin"].id, model="large-v3",
                options={"source": "headset", "save_rule": "ask"})
    second = resolve_transcription_params(_recording(ctx["author"]))["clinic_asr"]["profile_version"]
    assert (first, second) == (1, 2)


def test_upstream_unchanged_with_clinic_mode_off(ctx, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE")
    p = resolve_transcription_params(
        _recording(ctx["author"]), {"initial_prompt": "Befund", "hotwords": "x"})
    assert "clinic_asr" not in p
    assert p["language"] == (ctx["author"].transcription_language or None) and p["initial_prompt"] == "Befund" and p["hotwords"] == "x"
