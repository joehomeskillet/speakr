"""Mounted profile view and job receipts against local HTTP fixtures."""

import json
from datetime import datetime
from hashlib import sha256

import pytest
from flask import Flask
from flask_login import FlaskLoginClient, LoginManager

from src.app import app, db, register_clinic_resources
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_job_profile import asr_capability_target
from src.clinic.asr_profiles import set_profile
from src.clinic.routes_asr_profiles import PREFIX
from src.models import Recording, User
from src.services.job_queue import job_queue
from src.services.transcription.registry import ConnectorRegistry
from tests.clinic.test_asr_fallback import stubs  # noqa: F401 -- pytest fixture
from tests.clinic.test_stub_asr import FIXTURE
from tests.clinic.test_wp_asr_engine_wire import PLAN_CPU, PLAN_GPU, _job, ctx  # noqa: F401


@pytest.fixture
def mounted(ctx, monkeypatch):
    monkeypatch.setenv("CLINIC_GPU_ASR_BASE_URL", "http://127.0.0.1:9100/v1")
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    flask.test_client_class = FlaskLoginClient
    register_clinic_resources(flask)
    return flask, flask.test_client(user=ctx)


def _transcribe(user, connector, plan, monkeypatch, tmp_path):
    audio = tmp_path / FIXTURE.name
    audio.write_bytes(FIXTURE.read_bytes())
    rec = Recording(user_id=user.id, title="Synthetic tone", status="QUEUED", mime_type="audio/wav",
                    original_filename=audio.name, audio_path=str(audio))
    db.session.add(rec)
    db.session.commit()
    job = _job(rec, {"clinic_asr": {"engine": plan}})
    monkeypatch.setattr("src.services.transcription.get_connector", lambda: connector)
    monkeypatch.setattr("src.clinic.follow_up.enqueue_follow_up", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_queue, "_app", app)
    monkeypatch.setattr(job_queue, "_emit_completion_email", lambda *args: None)
    job_queue._process_job(job)
    db.session.refresh(job)
    assert job.status == "completed"
    return job


@pytest.mark.parametrize(("gpu_status", "actual", "posts"), [(200, "gpu", (1, 0)), (503, "cpu", (1, 1))])
def test_local_http_request_job_and_mounted_view_agree(ctx, mounted, stubs, monkeypatch, tmp_path,
                                                     gpu_status, actual, posts):
    cpu, gpu = stubs("cpu"), stubs("gpu", status=gpu_status)
    monkeypatch.setenv("TRANSCRIPTION_CONNECTOR", "openai_whisper")
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", cpu.url)
    monkeypatch.setenv("CLINIC_GPU_ASR_BASE_URL", gpu.url)
    job = _transcribe(ctx, ConnectorRegistry().initialize_from_env(), PLAN_GPU, monkeypatch, tmp_path)

    response = mounted[1].get(PREFIX, query_string={"job_id": job.id})
    engine = response.json["engine"]
    stored = json.loads(job.params)["clinic_asr"]["engine"]
    assert (gpu.posts, cpu.posts) == posts
    assert engine["actual"] == stored["actual"] == actual
    assert (engine["status"], engine["fallback"]) == ("runtime", actual == "cpu")
    assert engine["job_id"] == job.id
    assert datetime.fromisoformat(engine["observed_at"]).replace(tzinfo=None) == job.completed_at
    assert response.json["capabilities"]["proven"] == []
    print("P250_FIXTURE_RECEIPT " + json.dumps(dict(
        fixture=FIXTURE.name, audio_sha256=sha256(FIXTURE.read_bytes()).hexdigest(),
        gpu_http_status=gpu_status, gpu_posts=gpu.posts, cpu_posts=cpu.posts,
        job_engine=stored, profile_engine=engine,
    ), sort_keys=True))


def test_capability_registry_is_live_and_missing_observation_is_unavailable(ctx, mounted, monkeypatch):
    monkeypatch.setenv("ASR_BASE_URL", "http://127.0.0.1:9000/v1")
    set_profile(ctx.id, actor_id=ctx.id, language="de", model="large-v3", options={"source": "headset"})
    flask, client = mounted
    target = asr_capability_target("de", "large-v3")
    flask.extensions["clinic_capability_registry"] = AsrCapabilityRegistry([
        CapabilityEvidence(AsrCapability.BATCH, target, "set-1", "receipt:1", "runtime_passed"),
    ])
    view = client.get(PREFIX).json
    assert view["capabilities"]["proven"] == ["batch"]
    assert (view["engine"]["status"], view["engine"]["actual"], view["engine"]["fallback"]) == (
        "unavailable", None, False,
    )


def test_synthetic_receipt_is_visible_only_to_the_job_owner(ctx, mounted, monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    monkeypatch.setenv("TRANSCRIPTION_CONNECTOR", "clinic_stub")
    job = _transcribe(ctx, ConnectorRegistry().initialize_from_env(), PLAN_CPU, monkeypatch, tmp_path)
    _, client = mounted
    view = client.get(PREFIX).json
    assert (view["engine"]["status"], view["engine"]["actual"], view["engine"]["job_id"]) == (
        "synthetic", None, job.id,
    )
    newer = _job(job.recording, {"clinic_asr": {"engine": PLAN_CPU}})
    latest = client.get(PREFIX).json["engine"]
    assert (latest["status"], latest["job_id"]) == ("unavailable", newer.id)
    assert client.get(PREFIX, query_string={"job_id": job.id}).json["engine"]["status"] == "synthetic"
    assert client.get(PREFIX, query_string={"job_id": 2**31}).status_code == 400
    foreign = User(username="p250_foreign", email="p250_foreign@local.test", password="x")
    db.session.add(foreign)
    db.session.commit()
    foreign_client = mounted[0].test_client(user=foreign)
    view = foreign_client.get(PREFIX, query_string={"job_id": job.id}).json
    assert (view["engine"]["status"], view["engine"]["job_id"]) == ("unavailable", None)
