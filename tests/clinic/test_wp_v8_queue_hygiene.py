"""Exercise training queue wiring and the container entrypoint with synthetic data."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _exercise_training_queue():
    from clinic_support import login
    from src.app import app, db
    from src.clinic.asr_profiles import ClinicAsrTrainingRun, set_profile
    from src.clinic.identity import ClinicAccountPolicy
    from src.clinic.routes_jobs import PREFIX as JOBS_URL
    from src.clinic.routes_training import PREFIX as TRAINING_URL
    from src.models import ProcessingJob, Recording
    from src.services.job_queue import job_queue
    from test_wp_training_manifest import _approved, _user

    assert not job_queue._is_owner
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin = _user(admin=True)
        db.session.add(ClinicAccountPolicy(user_id=admin.id, status="active"))
        set_profile(admin.id, actor_id=admin.id, language="de", model="synthetic")
        scene = _approved(admin, "Alpha Beta", None)
        recording = db.session.get(Recording, scene["dictation"].recording_id)
        recording.status = "QUEUED"  # A training claim must not advance transcription.
        legacy_recording = Recording(user_id=admin.id, title="CAPABILITY_UNAVAILABLE", status="COMPLETED")
        db.session.add(legacy_recording)
        db.session.flush()
        legacy = ProcessingJob(user_id=admin.id, recording_id=legacy_recording.id,
                               job_type="asr_training", status="queued")
        db.session.add(legacy)
        db.session.commit()

        client = login(admin.id)
        response = client.post(TRAINING_URL + "/runs", json={"expected_profile_version": 1})
        assert response.status_code == 201, response.get_json()
        scheduled_id = response.get_json()["job_id"]

        summary_jobs = job_queue.jobs_for_queue("summary")
        with patch.dict(os.environ, {"CLINIC_MODE": "false"}):
            assert job_queue._claim_next_job(summary_jobs, "summary") is None
        for expected_id in (legacy.id, scheduled_id):
            claimed = job_queue._claim_next_job(summary_jobs, "summary")
            assert claimed is not None, "summary worker did not claim the training intent"
            assert claimed.id == expected_id
            job_queue._process_job(claimed)
            db.session.expire_all()
            job = db.session.get(ProcessingJob, expected_id)
            assert (job.status, job.retry_count) == ("failed", 1)
        assert job_queue._claim_next_job(summary_jobs, "summary") is None
        assert (recording.status, legacy_recording.status) == ("QUEUED", "COMPLETED")
        assert ClinicAsrTrainingRun.query.count() == 0

        status = client.get(f"{TRAINING_URL}/runs/{scheduled_id}")
        assert status.status_code == 200
        assert status.get_json()["state"] == "failed"
        health = client.get(JOBS_URL + "/health")
        assert health.status_code == 200
        failures = health.get_json()["recent_failures"]
        assert {row["id"] for row in failures} == {legacy.id, scheduled_id}
        assert all(row["error_class"] == "CAPABILITY_UNAVAILABLE" for row in failures)
        assert all(row["message"] == "Training nicht verfügbar" for row in failures)
        assert health.get_json()["oldest_queued_age_s"] is None


def test_real_app_claims_training_intents_and_reports_refusal(tmp_path):
    env = {
        **os.environ,
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(ROOT),
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'queue.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "CLINIC_ORIGINALS_ROOT": str(tmp_path / "uploads"),
        "CLINIC_MODE": "1",
        "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
    }
    run = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=60)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("existing_db", [False, True])
def test_entrypoint_limits_background_skip_to_one_shot_python(tmp_path, existing_db):
    data = tmp_path / "data"
    if existing_db:
        (data / "instance").mkdir(parents=True)
        (data / "instance" / "transcriptions.db").touch()
    python = tmp_path / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print('CALL ' + json.dumps([os.getenv('SPEAKR_SKIP_BACKGROUND_TASKS'), sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    entrypoint = (ROOT / "scripts/docker-entrypoint.sh").read_text(encoding="utf-8")
    entrypoint = entrypoint.replace("/data/", f"{data}/").replace("cd /app", f"cd {shlex.quote(str(ROOT))}")
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "ADMIN_USERNAME": "synthetic", "ADMIN_EMAIL": "admin@example.invalid", "ADMIN_PASSWORD": "synthetic"}
    env.pop("SPEAKR_SKIP_BACKGROUND_TASKS", None)
    run = subprocess.run(
        ["bash", "-c", entrypoint, "docker-entrypoint.sh", "python", "server"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=15, check=True,
    )
    calls = [json.loads(line[5:]) for line in run.stdout.splitlines() if line.startswith("CALL ")]
    schema, admin, server = calls
    assert schema[0] == "true"
    assert schema[1][0] == "-c"
    assert ("db.create_all()" in schema[1][1]) is not existing_db
    assert admin == ["true", ["scripts/docker_create_admin.py"]]
    assert server == [None, ["server"]]


if __name__ == "__main__":
    _exercise_training_queue()
