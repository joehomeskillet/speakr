"""Clinic ASR engine wiring in transcribe_with_connector (x20).

Synthetic recordings and a mocked connector only. A run here is not GPU
evidence: it proves what the task passes on and records, nothing about an
engine, a device or recognition quality.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

import src.tasks.processing as proc
from src.app import app, db
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision, add_human_revision, current_revision, history
from src.models import ProcessingJob, Recording, User
from src.services import job_control

PLAN_CPU = {"selected": "cpu", "fallback": None, "actual": None}
PLAN_GPU = {"selected": "gpu", "fallback": "cpu", "actual": None}
ADMIN = {"admin_default_hotwords": "AdminWort", "admin_default_initial_prompt": "Admin Prompt"}


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with app.app_context():
        db.create_all()
        suffix = uuid.uuid4().hex[:8]
        user = User(username=f"x20_{suffix}", email=f"x20_{suffix}@local.test", password="x")
        db.session.add(user)
        db.session.commit()
        yield user
        db.session.rollback()


def _recording(user, transcription=None):
    rec = Recording(user_id=user.id, title="x20", status="PENDING", original_filename="x.mp3",
                    audio_path="local://x20.mp3", transcription=transcription)
    db.session.add(rec)
    db.session.commit()
    return rec


def _job(rec, params):
    job = ProcessingJob(user_id=rec.user_id, recording_id=rec.id, job_type="transcribe",
                        status="processing", params=json.dumps(params))
    db.session.add(job)
    db.session.commit()
    return job


def _connector(text="Synthetischer Entwurf.", **attrs):
    connector = MagicMock()
    connector.PROVIDER_NAME = "test-connector"
    connector.supports_diarization = False
    connector.default_diarize = False
    specs = MagicMock()
    specs.max_duration_seconds = 1800
    specs.handles_chunking_internally = False
    specs.recommended_chunk_seconds = 600
    specs.unsupported_codecs = []
    connector.specifications = specs
    response = MagicMock()
    response.text = text
    response.segments = None
    response.speakers = None
    response.speaker_embeddings = None
    response.language = "de"
    connector.transcribe.return_value = response
    for key, value in attrs.items():
        setattr(connector, key, value)
    return connector


def _run(rec, connector, job=None, **kwargs):
    """Run transcribe_with_connector on a synthetic file, optionally bound to ``job``."""
    path = os.path.join(app.config["UPLOAD_FOLDER"], f"x20_{rec.id}.mp3")
    with open(path, "wb") as handle:
        handle.write(b"\x00" * 1024)
    converted = MagicMock()
    converted.was_converted = False

    def setting(key, default=None):
        return ADMIN.get(key, default)

    control = job_control.JobControl(job.id) if job is not None else job_control.JobControl(None)
    with patch("src.services.transcription.get_connector", return_value=connector), \
         patch.object(proc, "is_video_file", return_value=False), \
         patch.object(proc, "convert_if_needed", return_value=converted), \
         patch.object(proc, "client", MagicMock()), \
         patch.object(proc, "ENABLE_INQUIRE_MODE", False), \
         patch.object(proc, "generate_title_task"), \
         patch.object(proc, "generate_summary_only_task"), \
         patch("src.clinic.follow_up.enqueue_follow_up"), \
         patch.object(proc, "chunking_service") as chunking, \
         patch.object(proc.SystemSetting, "get_setting", side_effect=setting), \
         job_control.running(control):
        chunking.needs_chunking.return_value = False
        chunking.get_audio_duration.return_value = 60.0
        return proc.transcribe_with_connector(
            app.app_context(), rec.id, path, "x.mp3", time.time(), mime_type="audio/mpeg", **kwargs)


def _request(connector):
    return connector.transcribe.call_args.args[0]


def _actual(job):
    db.session.expire_all()
    stored = json.loads(db.session.get(ProcessingJob, job.id).params)
    return stored["clinic_asr"]["engine"]["actual"]


def test_gap1_resolver_values_are_not_topped_up_with_admin_defaults(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_CPU}})
    connector = _connector()
    _run(rec, connector, job)
    request = _request(connector)
    assert request.prompt is None and request.hotwords is None
    db.session.expire_all()
    out = db.session.get(Recording, rec.id)
    assert out.resolved_initial_prompt is None and out.resolved_hotwords is None


def test_gap1_upstream_admin_defaults_still_apply_with_clinic_mode_off(ctx, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE")
    rec = _recording(ctx)
    connector = _connector()
    _run(rec, connector)
    request = _request(connector)
    assert request.prompt == "Admin Prompt" and request.hotwords == "AdminWort"


def test_gap2_enabled_profile_hotwords_reach_the_connector(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_CPU, "prompts_enabled": True}})
    connector = _connector()
    _run(rec, connector, job, hotwords="Apixaban Marcumar", initial_prompt="Kardiologie")
    request = _request(connector)
    assert request.hotwords == "Apixaban Marcumar"
    assert request.prompt == "Kardiologie"


def test_gap2_disabled_prompts_send_neither(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_CPU, "prompts_enabled": False}})
    connector = _connector()
    _run(rec, connector, job, hotwords=None, initial_prompt=None)
    request = _request(connector)
    assert request.hotwords is None and request.prompt is None


def test_gap3_engine_reported_by_the_fallback_connector_is_recorded(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_GPU}, "other": 1})
    _run(rec, _connector(last_engine="cpu"), job)
    assert _actual(job) == "cpu"
    assert json.loads(db.session.get(ProcessingJob, job.id).params)["other"] == 1


def test_gap3_plain_cpu_connector_records_cpu_but_unreported_gpu_plan_stays_empty(ctx):
    rec = _recording(ctx)
    cpu_job = _job(rec, {"clinic_asr": {"engine": PLAN_CPU}})
    _run(rec, _connector(), cpu_job)
    assert _actual(cpu_job) == "cpu"
    rec2 = _recording(ctx)
    gpu_job = _job(rec2, {"clinic_asr": {"engine": PLAN_GPU}})
    _run(rec2, _connector(), gpu_job)
    assert _actual(gpu_job) is None


def test_gap3_failed_run_records_no_engine(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_GPU}})
    connector = _connector(last_engine="gpu")
    connector.transcribe.side_effect = RuntimeError("asr down")
    with pytest.raises(RuntimeError):
        _run(rec, connector, job)
    assert _actual(job) is None


def _dictation_with_head(user, human):
    rec = _recording(user, transcription="Menschliche Fassung.")
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
                              storage_key="originals/" + uuid.uuid4().hex, receipt="stored",
                              origin_ref="device:test")
    db.session.add(original)
    db.session.flush()
    asr = add_asr_revision(dictation, original, "Erster Entwurf", "stub:1")
    head = add_human_revision(dictation, asr, "Menschliche Fassung.", user.id) if human else asr
    db.session.commit()
    return rec, dictation, asr, head


def test_gap4_late_asr_behind_a_human_revision_is_a_side_revision(ctx):
    rec, dictation, asr, human = _dictation_with_head(ctx, human=True)
    _run(rec, _connector(text="Spaeter Entwurf."))
    db.session.expire_all()
    assert db.session.get(Recording, rec.id).transcription == "Menschliche Fassung."
    assert current_revision(dictation).id == human.id
    rows = history(dictation)
    assert [row.body for row in rows] == ["Erster Entwurf", "Menschliche Fassung.", "Spaeter Entwurf."]
    assert rows[-1].kind == "ASR" and rows[-1].parent_revision_id == human.id


def test_gap4_without_a_human_revision_the_existing_path_stays(ctx):
    rec, dictation, asr, head = _dictation_with_head(ctx, human=False)
    _run(rec, _connector(text="Neuer Entwurf."))
    db.session.expire_all()
    assert db.session.get(Recording, rec.id).transcription == "Neuer Entwurf."
    assert [row.body for row in history(dictation)] == ["Erster Entwurf"]


@pytest.mark.parametrize("unhealthy, expected", [(None, "gpu"), ("unhealthy: test", "cpu")])
def test_gap3_real_fallback_connector_reports_the_engine_that_ran(ctx, unhealthy, expected):
    from src.clinic.asr_fallback import FallbackTranscriptionConnector
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_GPU}})
    gpu, cpu = _connector(text="GPU Entwurf."), _connector(text="CPU Entwurf.")
    cpu.config = {"base_url": "http://cpu.test"}
    fallback = FallbackTranscriptionConnector(cpu, gpu, "http://gpu.test")
    with patch.object(FallbackTranscriptionConnector, "_gpu_healthy", return_value=unhealthy):
        _run(rec, fallback, job)
    assert fallback.last_engine == expected
    assert _actual(job) == expected
    assert (gpu if expected == "gpu" else cpu).transcribe.call_count == 1
    assert (cpu if expected == "gpu" else gpu).transcribe.call_count == 0
