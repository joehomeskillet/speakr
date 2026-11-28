"""Deliver workflow notices through the real queue after later commits."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.clinic import notification_preferences, policy, pools, workflow
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.job_handlers import can_see
from src.clinic.models import ClinicDictation
from src.clinic.notify_enqueue import record_notice
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key
from src.database import db
from src.models import Notification, ProcessingJob
from src.services.job_queue import job_queue
from tests.clinic.test_wp_v8_lease_and_acceptance_defaults import Browser, ROOT, TEXT, _seed


@pytest.mark.parametrize("scenario", ["all", "current_access_and_preferences", "rollback"])
def test_delayed_workflow_notices(tmp_path, scenario):
    env = {**os.environ, "CLINIC_MODE": "1", "CLINIC_PROFILE": "staging", "PYTHONPATH": str(ROOT),
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
           "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'notices.db'}"}
    result = subprocess.run([sys.executable, __file__, scenario], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-6000:]


def _assert_rollback_is_not_delivered(dictation, actor):
    transition_id = str(uuid4())
    did, version = dictation.id, dictation.version_counter
    scope = (actor, "release", did, str(uuid4()))
    jobs_before = ProcessingJob.query.filter_by(job_type="clinic_notify").count()

    def fail_after_notice():
        record_notice("completed", dictation, actor, version, transition_id)
        raise RuntimeError("rollback after recording the intent")

    with pytest.raises(RuntimeError, match="rollback after recording the intent"):
        run_once(scope, "synthetic", fail_after_notice)
    assert db.session.get(ClinicParityReceipt, scope_key(scope)) is None
    assert ProcessingJob.query.filter_by(job_type="clinic_notify").count() == jobs_before

    # Even with a matching live status/version, this job has no committed receipt.
    job_id = job_queue.enqueue(dictation.author_user_id, dictation.recording_id, "clinic_notify",
                               dict(event="completed", dictation_id=did, actor_user_id=actor,
                                    expected_version=version, transition_id=transition_id,
                                    clinic_operation_key=transition_id))
    job = db.session.get(ProcessingJob, job_id)
    job_queue._process_job(job)
    db.session.refresh(job)
    assert job.status == "failed", job.error_message
    assert "transition_not_durable" in job.error_message
    assert Notification.query.filter_by(kind="clinic.workflow").count() == 0


def _exercise(app, scenario):
    users, target = _seed(app)
    did = target["id"]
    author, actor, staff, admin = (users[key] for key in ("author", "typist", "staff_only", "admin"))
    policy.register_grant_provider(lambda uid: [policy.Grant("all", frozenset({"view", "edit", "release"}))]
                                   if uid == actor else [])
    with app.app_context():
        item = db.session.get(workflow.ClinicWorkflowDictation, did)
        item.assignee_user_id = author
        db.session.add(ClinicContextGrantUse(
            revision_id=target["context"], issuer="kis", audience="speakr", actor_user_id=author,
            subject_reference="synthetic", purpose="bind_existing", verified_at=datetime.now(timezone.utc),
            nonce_digest=uuid4().hex.ljust(64, "0")))
        db.session.commit()
        pools.allow(staff, "pool", actor_id=admin, pool_id=item.pool_id, actions=("view",))
        assert can_see(staff, item)

    published = Browser(app, admin, did).publish(True, [actor, author, staff], 0)
    assert published.status_code == 200, published.json
    browser = Browser(app, actor, did)
    held = browser.acquire()

    def command(name, **payload):
        with app.app_context():
            version = db.session.get(ClinicDictation, did).version_counter
        response = browser.client.post("/api/clinic/v1/parity/commands",
            json=dict(command=name, expected_revision=version + 1, request_id=str(uuid4()),
                      payload=dict(dictation_id=did, **payload)),
            headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": browser.csrf})
        assert response.status_code == 200, response.json
        return response.json["command_id"]

    def submit():
        return command("request_acceptance", text_revision_id=target["head"],
                       context_revision_id=target["context"],
                       acceptance_rule_id=browser.get("/review")["binding"]["acceptance_rule_id"])

    requested = submit()
    rejected = command("decide_acceptance", text_revision_id=target["head"], decision="return", reason="Prüfen")
    requested_again = submit()
    accepted = command("decide_acceptance", text_revision_id=target["head"], decision="accept")
    released = browser.release(target, held, target["head"], TEXT)
    assert released.status_code == 201, released.json
    completed = released.json["release_id"]

    with app.app_context():
        dictation = db.session.get(ClinicDictation, did)
        item = db.session.get(workflow.ClinicWorkflowDictation, did)
        assert item.status == workflow.DONE
        if scenario == "rollback":
            _assert_rollback_is_not_delivered(dictation, actor)
            return
        filtered = scenario == "current_access_and_preferences"
        if filtered:
            pools.deny(staff, "pool", actor_id=admin, pool_id=item.pool_id, actions=("view",))
            assert not can_see(staff, item)
            notification_preferences.set_event(author, "accepted", False, expected_version=0)

        events = [("acceptance_requested", requested), ("acceptance_rejected", rejected),
                  ("acceptance_requested", requested_again), ("accepted", accepted), ("completed", completed)]
        waiting = ProcessingJob.query.filter_by(job_type="clinic_notify", status="queued").order_by(ProcessingJob.id).all()
        assert [(json.loads(job.params)["event"], job.notify_transition_id) for job in waiting] == events
        assert json.loads(waiting[0].params)["expected_version"] < dictation.version_counter
        assert Notification.query.filter_by(kind="clinic.workflow").count() == 0
        for job in [*waiting, waiting[0]]:
            job_queue._process_job(job)
            db.session.refresh(job)
            assert job.status == "completed", job.error_message
        expected = [(author, f"clinic.wf:{event}:{did}:{transition}") for event, transition in events
                    if not filtered or event != "accepted"]
        if not filtered:
            expected.extend((staff, f"clinic.wf:acceptance_requested:{did}:{transition}")
                            for transition in (requested, requested_again))
        notices = Notification.query.filter_by(kind="clinic.workflow").all()
        assert sorted((notice.user_id, notice.dedupe_key) for notice in notices) == sorted(expected)


if __name__ == "__main__":
    from src.app import app
    from src.init_db import initialize_database

    with app.app_context():
        initialize_database(app)
    _exercise(app, sys.argv[1])
