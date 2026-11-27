"""Clinic job handlers registered in the real app (v8-90). Synthetic data only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from src.clinic import kis, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.job_handlers import register_clinic_job_handlers
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicRelease
from src.database import db
from src.models import Notification, ProcessingJob, Recording, User
from src.services import job_queue as jq

ROOT = Path(__file__).resolve().parents[2]

CHILD = r'''
import json
from src.app import app
from src.services import job_queue as jq
print("RESULT " + json.dumps({"handlers": sorted(jq._CLINIC_HANDLERS), "jobs": sorted(jq.CLINIC_JOBS)}))
'''


def _build(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'reg.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=300)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-3000:]
    return json.loads(line[len("RESULT "):])


def test_real_app_registers_every_clinic_job_type_in_clinic_mode(tmp_path):
    result = _build(tmp_path, True)
    assert result["jobs"] == ["clinic_alignment", "clinic_finalize", "clinic_notify", "clinic_output"]
    assert result["handlers"] == result["jobs"]


def test_real_app_registers_nothing_with_clinic_mode_off(tmp_path):
    assert _build(tmp_path, False)["handlers"] == []


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.delenv("CLINIC_PROFILE", raising=False)
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'q.db'}")
    db.init_app(app)
    monkeypatch.setattr(jq.job_queue, "_app", app)
    monkeypatch.setattr(jq.job_queue, "_is_owner", False)
    monkeypatch.setattr(jq, "_CLINIC_HANDLERS", {})
    register_clinic_job_handlers(app)
    with app.app_context():
        db.create_all()
        users = {}
        for name in ("author", "other", "admin", "idle_admin"):
            users[name] = User(username=name, email=f"{name}@example.invalid", password="x",
                               is_admin=name.endswith("admin"))
            db.session.add(users[name])
        db.session.flush()
        db.session.add(ClinicAccountPolicy(user_id=users["admin"].id, status="active"))
        db.session.add(ClinicAccountPolicy(user_id=users["idle_admin"].id, status="disabled"))
        ids = {name: user.id for name, user in users.items()}
        recording = Recording(user_id=ids["author"], title="Synthetic")
        db.session.add(recording)
        db.session.flush()
        item = workflow.start(db.session(), "create", author_user_id=ids["author"], pool_id="pool",
                              assignee_user_id=ids["author"], acceptance_required=False)
        db.session.add(ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=ids["author"]))
        for trigger in ("send_offline", "skip_recognition", "complete"):
            workflow.transition(db.session(), item.id, trigger)
        context = ClinicContextRevision(dictation_id=item.id, source_system="test", tenant_scope="test",
                                        case_ref="case-1", patient_ref="opaque")
        db.session.add(context)
        db.session.flush()
        rel = ClinicRelease(dictation_id=item.id, audio_revision_id="a", transcript_revision_id="t",
                            context_revision_id=context.id, text_hash="h", actor_user_id=ids["author"],
                            document_type="arztbrief", release_type="clinical_final",
                            authorization_decision="{}")
        db.session.add(rel)
        db.session.commit()
        yield SimpleNamespace(app=app, ids=ids, recording=recording.id, dictation=item.id, release=rel.id)


def _run(env, job_type, **params):
    job_id = jq.job_queue.enqueue(user_id=env.ids["author"], recording_id=env.recording,
                                  job_type=job_type, params=params)
    jq.job_queue._process_job(db.session.get(ProcessingJob, job_id))
    db.session.expire_all()
    return db.session.get(ProcessingJob, job_id)


def test_notify_job_is_processed_end_to_end(env):
    job = _run(env, "clinic_notify", clinic_operation_key="wf-1:completed:1", event="completed",
               dictation_id=env.dictation, actor_user_id=env.ids["other"], expected_version=0)
    assert job.status == "completed", job.error_message
    (row,) = db.session.query(Notification).all()
    assert row.user_id == env.ids["author"]


def test_notify_does_not_reach_a_user_without_view_access(env):
    # The assignee is routed but has no pool or grant for this dictation: dropped, nothing stored.
    item = db.session.get(workflow.ClinicWorkflowDictation, env.dictation)
    item.assignee_user_id = env.ids["other"]
    db.session.commit()
    job = _run(env, "clinic_notify", clinic_operation_key="wf-1:accepted:2", event="accepted",
               dictation_id=env.dictation, actor_user_id=env.ids["author"], expected_version=0)
    assert job.status == "completed", job.error_message
    assert db.session.query(Notification).count() == 0


def _notify_assignee(env, name):
    item = db.session.get(workflow.ClinicWorkflowDictation, env.dictation)
    item.assignee_user_id = env.ids[name]
    db.session.commit()
    return _run(env, "clinic_notify", clinic_operation_key=f"wf-1:accepted:{name}", event="accepted",
                dictation_id=env.dictation, actor_user_id=env.ids["author"], expected_version=0)


def test_notify_reaches_an_active_admin_without_a_grant(env):
    job = _notify_assignee(env, "admin")
    assert job.status == "completed", job.error_message
    (row,) = db.session.query(Notification).all()
    assert row.user_id == env.ids["admin"]


def test_notify_does_not_reach_an_inactive_admin_without_a_grant(env):
    job = _notify_assignee(env, "idle_admin")
    assert job.status == "completed", job.error_message
    assert db.session.query(Notification).count() == 0


def test_output_without_target_is_a_permanent_refusal_and_sends_nothing(env, legacy_kis_delivery, monkeypatch):
    sent = []
    monkeypatch.setattr(kis, "deliver", lambda *a, **k: sent.append(a))
    job = _run(env, "clinic_output", clinic_release_id=env.release)
    assert (job.status, sent) == ("failed", [])
    assert "no configured output target" in job.error_message
    assert job.retry_count == 1  # the one failed run; the job stays failed, no retry loop


def test_output_with_disabled_adapter_is_refused_and_sends_nothing(env, legacy_kis_delivery, monkeypatch):
    env.app.extensions['clinic_kis_adapter'] = kis.DisabledAdapter()
    sent = []
    monkeypatch.setattr(kis, "deliver", lambda *a, **k: sent.append(a))
    job = _run(env, "clinic_output", clinic_release_id=env.release, target="kis")
    assert (job.status, sent) == ("failed", [])
    assert "KIS adapter not configured" in job.error_message


def test_output_with_mock_adapter_but_untrusted_context_is_refused(env, legacy_kis_delivery, monkeypatch):
    sent = []
    monkeypatch.setattr(kis, "deliver", lambda *a, **k: sent.append(a))
    job = _run(env, "clinic_output", clinic_release_id=env.release, target="kis")
    assert (job.status, sent) == ("failed", [])
    assert "context is not trusted" in job.error_message


def test_output_delivers_once_through_the_adapter_when_trusted(env, legacy_kis_delivery, monkeypatch):
    monkeypatch.setattr("src.clinic.finalize_gates.trusted_context", lambda *_: True)
    adapter = env.app.extensions["clinic_kis_adapter"] = kis.MockKisAdapter()
    calls = []
    real = kis.deliver
    monkeypatch.setattr(kis, "deliver", lambda *a, **k: calls.append(a[3]) or real(*a, **k))
    job = _run(env, "clinic_output", clinic_release_id=env.release, target="kis")
    assert calls == [f"export:{env.release}"]
    assert job.status == "completed", job.error_message
    assert list(adapter.documents) == [f"export:{env.release}"]
    again = _run(env, "clinic_output", clinic_release_id=env.release, target="kis")
    assert again.status == "failed"  # the queue's delivery marker blocks a second send
    assert len(adapter.documents) == 1


def test_finalize_job_checks_the_release_binding(env):
    ok = _run(env, "clinic_finalize", clinic_release_id=env.release, text_hash="h")
    assert ok.status == "completed", ok.error_message
    other = db.session.get(ClinicRelease, env.release)
    second = ClinicRelease(dictation_id=other.dictation_id, audio_revision_id="a", transcript_revision_id="t",
                           context_revision_id=other.context_revision_id, text_hash="h2",
                           actor_user_id=other.actor_user_id, document_type="arztbrief",
                           release_type="clinical_final", authorization_decision="{}")
    db.session.add(second)
    db.session.commit()
    stale = _run(env, "clinic_finalize", clinic_release_id=second.id, text_hash="h")
    assert stale.status == "failed" and "binding differs" in stale.error_message
    gone = _run(env, "clinic_finalize", clinic_release_id="missing")
    assert gone.status == "failed" and "release not found" in gone.error_message
