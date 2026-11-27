"""Q-18 availability reporting. Registry fixtures do not prove decoder behavior."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.app import app, db
from src.clinic import asr_job_profile
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_profiles import set_profile
from src.clinic.vocabulary import add_vocabulary_entry, suppress_vocabulary_entry
from src.models import User
from src.services.transcription_defaults import resolve_transcription_params


@pytest.fixture
def author(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("ASR_BASE_URL", "http://127.0.0.1:9000")
    monkeypatch.setattr(asr_job_profile, "dev_defaults", lambda: {"asr": {
        "integration_provider": "speaches", "engine_revision": "engine-fixture-v1",
        "model_revision": "model-fixture-v2",
    }})
    target = asr_job_profile.asr_capability_target("de", "large-v3")
    registry = AsrCapabilityRegistry([
        CapabilityEvidence(capability, target, "generic-probe", "fixture:probe", "runtime_passed")
        for capability in (AsrCapability.BATCH, AsrCapability.LIVE, AsrCapability.NEGATIVE_LEXICON)
    ])
    monkeypatch.setitem(app.extensions, "clinic_capability_registry", registry)
    with app.app_context():
        db.create_all()
        name = f"p185_{uuid4().hex[:15]}"
        user = User(username=name, email=f"{name}@local.test", password="x")
        db.session.add(user)
        db.session.flush()
        set_profile(user.id, actor_id=user.id, language="de", model="large-v3",
                    options={"source": "headset"})
        yield user
        db.session.rollback()


@pytest.mark.parametrize("mode", ["live", "batch"])
def test_profile_persists_unavailable_with_selected_decoder_version(author, mode):
    registry = app.extensions["clinic_capability_registry"]
    resolved = asr_job_profile.resolve_asr_job_profile(author.id, mode, ["audio/wav"], registry)
    job = SimpleNamespace(params='{"language":"de"}')
    asr_job_profile.persist_job_profile(job, resolved)
    run = json.loads(job.params)["clinic_asr"]

    assert run["negative_lexicon"] == {"status": "CAPABILITY_UNAVAILABLE"}
    assert run["negative_lexicon_audio_pair"] is None
    assert (run["decoder"], run["engine_revision"]) == ("speaches", "engine-fixture-v1")
    assert (run["model"], run["model_revision"]) == ("large-v3", "model-fixture-v2")


def test_suppressed_word_does_not_change_transcription_job_params(author):
    entry = add_vocabulary_entry("user", "Apixaban", user_id=author.id)
    baseline = resolve_transcription_params(owner=author)
    suppress_vocabulary_entry(entry.id)
    with_suppression = resolve_transcription_params(owner=author)

    record = with_suppression["clinic_asr"]
    assert record["status"] == "profile"
    assert record["negative_lexicon"]["status"] == "CAPABILITY_UNAVAILABLE"
    assert record["negative_lexicon_audio_pair"] is None
    assert with_suppression == baseline


def test_default_job_reports_unavailable_without_a_versioned_profile(author):
    record = resolve_transcription_params()["clinic_asr"]
    assert record["status"] == "defaults"
    assert record["negative_lexicon"] == {"status": "CAPABILITY_UNAVAILABLE"}
    assert record["negative_lexicon_audio_pair"] is None
