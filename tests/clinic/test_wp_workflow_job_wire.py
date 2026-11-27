"""Synthetic evidence for BT-WF-011, BT-WF-014 and BT-OPS-008 (queue wiring slice)."""

import json
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from src.clinic import workflow
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services import job_queue as jq

KEY = {"clinic_release_id": "rel-1"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'q.db'}")
    db.init_app(app)
    monkeypatch.setattr(jq.job_queue, "_app", app)
    monkeypatch.setattr(jq.job_queue, "_is_owner", False)
    monkeypatch.setattr(jq, "_CLINIC_HANDLERS", {})
    with app.app_context():
        db.create_all()
        user = User(username="author", email="author@example.invalid", password="synthetic")
        db.session.add(user)
        db.session.flush()
        recording = Recording(user_id=user.id, title="Synthetic")
        db.session.add(recording)
        db.session.commit()
        yield SimpleNamespace(app=app, uid=user.id, rid=recording.id)


def _enqueue(env, job_type="clinic_output", **params):
    return jq.job_queue.enqueue(user_id=env.uid, recording_id=env.rid, job_type=job_type,
                                params=params or KEY)


def _run(job_id):
    jq.job_queue._process_job(db.session.get(ProcessingJob, job_id))
    db.session.expire_all()
    return db.session.get(ProcessingJob, job_id)


def test_same_operation_key_is_one_job_also_after_it_ended(env):
    first = _enqueue(env, "clinic_notify")
    db.session.get(ProcessingJob, first).status = "completed"
    db.session.commit()
    assert _enqueue(env, "clinic_notify") == first
    assert db.session.query(ProcessingJob).count() == 1
    # A different type with the same key is its own job.
    assert _enqueue(env, "clinic_output") != first


def test_concurrent_retries_enqueue_one_job(env):
    ids, errors = [], []

    def worker():
        try:
            with env.app.app_context():
                ids.append(_enqueue(env, "clinic_finalize"))
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(ids)) == 1
    assert db.session.query(ProcessingJob).count() == 1


def test_active_job_of_another_operation_is_not_returned_as_this_one(env):
    _enqueue(env, "clinic_notify", clinic_release_id="rel-1")
    with pytest.raises(jq.ClinicJobError):
        _enqueue(env, "clinic_notify", clinic_release_id="rel-2")


def test_upstream_dedupe_unchanged_with_clinic_mode_off(env, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE")
    first = _enqueue(env, "clinic_notify")
    db.session.get(ProcessingJob, first).status = "completed"
    db.session.commit()
    assert _enqueue(env, "clinic_notify") != first


def test_output_failure_is_visible_with_correlation_and_never_retried(env, legacy_kis_delivery):
    calls = []

    def boom(params, correlation_id):
        calls.append(correlation_id)
        raise RuntimeError("adapter connection lost")

    jq.register_clinic_handler("clinic_output", boom)
    job_id = _enqueue(env)
    job = _run(job_id)
    assert (job.status, len(calls)) == ("failed", 1)
    assert f"rel-1:{job_id}" in job.error_message
    # A crash recovery or manual requeue must not deliver a second time.
    job.status = "queued"
    db.session.commit()
    job = _run(job_id)
    assert (job.status, len(calls)) == ("failed", 1)
    assert "UNKNOWN" in job.error_message


def test_output_outcome_unknown_is_not_an_automatic_resend(env, legacy_kis_delivery):
    from src.clinic.output_attempts import UNKNOWN

    calls = []
    jq.register_clinic_handler("clinic_output", lambda p, c: calls.append(c) or UNKNOWN)
    job = _run(_enqueue(env))
    assert (job.status, len(calls), job.retry_count) == ("failed", 1, 1)
    assert json.loads(job.params)[jq.CLINIC_DELIVERY_STARTED] is True


def test_notify_passes_real_visibility_and_retries_only_on_storage_failure(env, monkeypatch):
    seen = []

    def emit(event, dictation_id, **kwargs):
        seen.append((event, dictation_id, kwargs["can_see"](7, None), kwargs["expected_version"]))
        return SimpleNamespace(failed=(7,) if len(seen) == 1 else ())

    monkeypatch.setattr("src.clinic.workflow_notifications.emit", emit)
    jq.register_clinic_handler("clinic_notify", jq.clinic_notify_handler(lambda uid, item: uid == 7))
    job_id = _enqueue(env, "clinic_notify", clinic_operation_key="wf-1:accepted:3", event="accepted",
                      dictation_id="d1", actor_user_id=2, expected_version=3, acceptor_ids=[7])
    assert _run(job_id).status == "queued"
    assert _run(job_id).status == "completed"
    assert seen == [("accepted", "d1", True, 3)] * 2


def test_notify_workflow_refusal_is_permanent(env, monkeypatch):
    def emit(*_a, **_k):
        raise workflow.WorkflowError("stale_version")

    monkeypatch.setattr("src.clinic.workflow_notifications.emit", emit)
    jq.register_clinic_handler("clinic_notify", jq.clinic_notify_handler(lambda *_: True))
    job_id = _enqueue(env, "clinic_notify", event="accepted", dictation_id="d1", actor_user_id=2,
                      expected_version=3, transition_id="t-1", **KEY)
    job = _run(job_id)
    assert (job.status, job.retry_count) == ("failed", 1)
    assert f"rel-1:{job_id}" in job.error_message and "stale_version" in job.error_message


def _notify_params(**extra):
    return dict(event="accepted", dictation_id="d1", actor_user_id=2, expected_version=3,
                acceptor_ids=[7], **extra)


def test_notify_without_transition_id_or_operation_key_is_refused(env, monkeypatch):
    calls = []
    monkeypatch.setattr("src.clinic.workflow_notifications.emit", lambda *a, **k: calls.append(k))
    jq.register_clinic_handler("clinic_notify", jq.clinic_notify_handler(lambda *_: True))
    job = _run(_enqueue(env, "clinic_notify", **_notify_params(**KEY)))
    assert (job.status, job.retry_count, calls) == ("failed", 1, [])
    assert "transition_id" in job.error_message


def test_two_transitions_at_same_version_both_notify_and_retry_does_not_duplicate(env, monkeypatch):
    stored, ids = set(), []

    def emit(event, dictation_id, *, transition_id, **kwargs):
        ids.append(transition_id)
        key = (event, dictation_id, transition_id)
        fresh = key not in stored
        stored.add(key)
        return SimpleNamespace(failed=(7,) if fresh and len(ids) == 1 else ())

    monkeypatch.setattr("src.clinic.workflow_notifications.emit", emit)
    jq.register_clinic_handler("clinic_notify", jq.clinic_notify_handler(lambda *_: True))
    first = _enqueue(env, "clinic_notify", **_notify_params(clinic_operation_key="wf-1:accepted:3:a",
                                                            transition_id="tr-a"))
    # Same version_counter, different transition: its own job (no operation clash).
    db.session.get(ProcessingJob, first).status = "completed"
    db.session.commit()
    second = _enqueue(env, "clinic_notify", **_notify_params(clinic_operation_key="wf-1:accepted:3:b",
                                                             transition_id="tr-b"))
    assert second != first
    assert _run(second).status == "queued"       # storage failure -> retried
    assert _run(second).status == "completed"
    assert ids == ["tr-b", "tr-b"]               # retry reuses the same transition id
    # Re-enqueueing the first transition returns the existing job, no new notice.
    assert _enqueue(env, "clinic_notify", **_notify_params(clinic_operation_key="wf-1:accepted:3:a",
                                                           transition_id="tr-a")) == first
    assert len(ids) == 2 and stored == {("accepted", "d1", "tr-b")}


@pytest.mark.parametrize("job_type", jq.CLINIC_JOBS)
def test_job_without_handler_stays_pending_and_runs_once_after_registration(env, job_type, legacy_kis_delivery):
    job_id = _enqueue(env, job_type)
    claim = lambda: jq.job_queue._claim_next_job(jq.SUMMARY_JOBS, "summary")  # noqa: E731
    # Workers skip it: not claimed, not failed, nothing consumed.
    assert claim() is None and claim() is None
    job = db.session.get(ProcessingJob, job_id)
    assert (job.status, job.retry_count, job.error_message) == ("queued", 0, None)

    calls = []
    jq.register_clinic_handler(job_type, lambda params, correlation_id: calls.append(correlation_id))
    claimed = claim()
    assert claimed is not None and claimed.id == job_id
    assert claim() is None
    job = _run(job_id)
    assert (job.status, len(calls)) == ("completed", 1)
    assert claim() is None and len(calls) == 1


def test_missing_handler_and_clinic_mode_off_fail_without_retry(env, monkeypatch):
    job = _run(_enqueue(env, "clinic_finalize"))
    assert (job.status, job.retry_count) == ("failed", 1)
    jq.register_clinic_handler("clinic_finalize", lambda *_: None)
    monkeypatch.delenv("CLINIC_MODE")
    job.status, job.retry_count = "queued", 0
    db.session.commit()
    assert _run(job.id).status == "failed"
    with pytest.raises(ValueError):
        jq.register_clinic_handler("summarize", lambda *_: None)
