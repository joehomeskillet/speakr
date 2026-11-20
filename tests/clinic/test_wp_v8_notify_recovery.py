"""Restart genuine app processes on either side of the durable queue INSERT."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.clinic.test_wp_v8_lease_and_acceptance_defaults import Browser, ROOT, _seed


def test_pending_notice_recovers_across_abrupt_process_exits(tmp_path):
    env = {**os.environ, "CLINIC_MODE": "1", "CLINIC_PROFILE": "staging", "PYTHONPATH": str(ROOT),
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
           "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'crash.db'}"}
    for phase, expected in (("command", 73), ("owner", 74), ("restart", 0)):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), phase], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == expected, result.stderr[-4000:]


def run(phase):
    from datetime import datetime, timedelta

    from src.app import app
    from src.clinic import policy
    from src.clinic.models import ClinicDictation
    from src.clinic.parity_receipts import ClinicNoticeDispatch
    from src.database import db
    from src.init_db import initialize_database
    from src.models import ProcessingJob
    from src.services.job_queue import job_queue

    with app.app_context():
        initialize_database(app)
    if phase == "command":
        users, target = _seed(app)
        actor, did = users["typist"], target["id"]
        policy.register_grant_provider(lambda uid: [policy.Grant("all", frozenset({"view", "edit", "release"}))]
                                       if uid == actor else [])
        published = Browser(app, users['admin'], did).publish(True, [actor], 0)
        assert published.status_code == 200, published.json
        browser = Browser(app, actor, did)
        browser.acquire()
        with app.app_context():
            version = db.session.get(ClinicDictation, did).version_counter
        payload = dict(dictation_id=did, text_revision_id=target["head"],
                       context_revision_id=target["context"],
                       acceptance_rule_id=browser.get("/review")["binding"]["acceptance_rule_id"])
        with patch.object(job_queue, "enqueue", side_effect=lambda **kw: os._exit(73)):
            response = browser.client.post("/api/clinic/v1/parity/commands",
                json=dict(command="request_acceptance", expected_revision=version + 1,
                          request_id=str(uuid4()), payload=payload),
                headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": browser.csrf})
        raise AssertionError(response.json)
    job_queue.mark_as_owner()
    with patch.object(job_queue, "start"):
        with app.app_context():
            assert ClinicNoticeDispatch.query.filter_by(handed_off=False).count() == 1
            jobs = ProcessingJob.query.filter_by(job_type="clinic_notify")
            assert jobs.count() == (0 if phase == "owner" else 1)
        if phase == "owner":
            enqueue = job_queue.enqueue

            def crash_after_enqueue(**kwargs):
                enqueue(**kwargs)
                os._exit(74)

            with patch.object(job_queue, "enqueue", side_effect=crash_after_enqueue):
                job_queue.recover_orphaned_jobs()
            raise AssertionError("missing recovery")

        job_queue.recover_orphaned_jobs()
        job_queue.recover_orphaned_jobs()
        with app.app_context():
            assert ClinicNoticeDispatch.query.one().handed_off
            job = ProcessingJob.query.filter_by(job_type="clinic_notify").one()
            job.status = "completed"
            job.completed_at = datetime.utcnow() - timedelta(days=2)
            db.session.commit()
        job_queue.cleanup_old_jobs()
        job_queue.recover_orphaned_jobs()
        with app.app_context():
            assert ProcessingJob.query.filter_by(job_type="clinic_notify").count() == 0


if __name__ == "__main__":
    run(sys.argv[1])
