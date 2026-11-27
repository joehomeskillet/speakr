"""Processing overview routes (V8 P17 backend): job health and controlled retry.

Synthetic users, recordings and jobs on a throwaway SQLite database; the real
receipt (`parity_receipts.run_once`), the real policy and the real `JobQueue.enqueue`.
The queue is not the owner here, so no worker thread starts and no job runs.
"""

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import parity_receipts, pools  # noqa: F401 - tables for create_all
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.routes_jobs import PREFIX, create_blueprint
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services import job_queue as jq

SECRET_TEXT = "Befund Muster Vertraulich"


def _user(name, admin=False):
    user = User(username=name, email=f"{name}@example.invalid", password="x", is_admin=admin)
    db.session.add(user)
    db.session.flush()
    db.session.add(ClinicAccountPolicy(user_id=user.id, status="active"))
    return user


def _job(user, recording, status="failed", job_type="transcribe", **fields):
    job = ProcessingJob(user_id=user.id, recording_id=recording.id, job_type=job_type, status=status, **fields)
    db.session.add(job)
    db.session.commit()
    return job


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_MODE", "1")
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'jobs.db'}")
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    monkeypatch.setattr(jq.job_queue, "_app", flask)
    monkeypatch.setattr(jq.job_queue, "_is_owner", False)
    flask.register_blueprint(create_blueprint(run_once=parity_receipts.run_once))
    with flask.app_context():
        db.create_all()
        admin, alice, bob = _user("ops", True), _user("alice"), _user("bob")
        rec_a = Recording(user_id=alice.id, title="A")
        rec_b = Recording(user_id=bob.id, title="B")
        rec_plain = Recording(user_id=alice.id, title="Plain")
        db.session.add_all([rec_a, rec_b, rec_plain])
        db.session.flush()
        db.session.add_all([ClinicDictation(recording_id=rec_a.id, author_user_id=alice.id),
                            ClinicDictation(recording_id=rec_b.id, author_user_id=bob.id)])
        db.session.commit()

        def client(user):
            c = flask.test_client()
            return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

        yield SimpleNamespace(client=client, admin=admin, alice=alice, bob=bob,
                              rec_a=rec_a, rec_b=rec_b, rec_plain=rec_plain, flask=flask)
        db.session.rollback()


def _retry(c, h, job, key=None, version=None, expected=None):
    key = key or str(uuid.uuid4())
    version = job.retry_count if version is None else version
    headers = {**h, "Idempotency-Key": key, "If-Match": f'"{version if expected is None else expected}"'}
    return c.post(f"{PREFIX}/{job.id}/retry", headers=headers,
                  json={"request_id": key, "expected_version": version})


def _new_jobs(job):
    return db.session.query(ProcessingJob).filter(ProcessingJob.id != job.id,
                                                  ProcessingJob.recording_id == job.recording_id).all()


# Contract of static/js/clinic/jobs-ui.js (normalizeFailure, summarizeProgress, load).
def test_health_shape_matches_what_jobs_ui_reads(world):
    failed = _job(world.alice, world.rec_a, error_message=f"ValueError: {SECRET_TEXT}",
                  params=json.dumps({"text": SECRET_TEXT}), completed_at=datetime.utcnow())
    _job(world.alice, world.rec_a, "queued", "summarize", created_at=datetime.utcnow() - timedelta(seconds=90))
    _job(world.alice, world.rec_plain, "completed")
    stuck = _job(world.alice, world.rec_plain, "processing", "reprocess_summary",
                 started_at=datetime.utcnow() - timedelta(hours=2))
    c, _ = world.client(world.admin)
    res = c.get(f"{PREFIX}/health")
    assert res.status_code == 200 and res.headers["Cache-Control"] == "no-store"
    data = res.json
    assert {"counts", "stuck_ids", "recent_failures"} <= data.keys()
    assert all(isinstance(data["counts"][k], int) for k in ("queued", "processing", "completed", "failed"))
    assert data["counts"] == {"queued": 1, "processing": 1, "completed": 1, "failed": 1}
    assert data["stuck_ids"] == [stuck.id] and all(isinstance(i, int) for i in data["stuck_ids"])
    assert data["oldest_queued_age_s"] >= 90
    assert len(data["recent_failures"]) == 1
    row = data["recent_failures"][0]
    assert type(row["id"]) is int and row["id"] == failed.id
    assert row["job_type"] == "transcribe" and row["error_class"] == "ValueError"
    assert isinstance(row["message"], str) and isinstance(row["state"], str) and row["state"] == "failed"
    assert type(row["version"]) is int and row["version"] == 0
    assert isinstance(row["finished_at"], str)
    assert data["can_retry"] is True
    # No free text, parameters or recording reference leave the server.
    assert SECRET_TEXT not in res.get_data(as_text=True)
    assert "text" not in row and "params" not in row and "recording_id" not in row


def test_health_is_capped_at_ten_failures(world):
    for _ in range(12):
        _job(world.alice, world.rec_a, error_message="RuntimeError: x", completed_at=datetime.utcnow())
    c, _ = world.client(world.admin)
    assert len(c.get(f"{PREFIX}/health").json["recent_failures"]) == 10


@pytest.mark.parametrize(("code", "label"), [
    ("storage_unavailable", "Ablage nicht verfügbar"),
    ("engine_unavailable", "Erkennungsengine nicht verfügbar"),
    ("missing_author_pool", "Autor oder Autorenpool fehlt"),
    ("corrupt_audio", "Audiodatei beschädigt"),
])
@pytest.mark.parametrize("with_details", [False, True])
def test_health_exposes_safe_cause_labels_without_details(world, code, label, with_details):
    details = f": {SECRET_TEXT}, '/private/audio.wav', person@example.invalid 123456789"
    _job(world.alice, world.rec_a, error_message=code + (details if with_details else ""),
         completed_at=datetime.utcnow())
    c, _ = world.client(world.admin)
    response = c.get(f"{PREFIX}/health")
    assert response.status_code == 200
    (failure,) = response.json["recent_failures"]
    assert failure["error_class"] == code
    assert failure["message"] == label
    for private in (SECRET_TEXT, "/private/audio.wav", "person@example.invalid", "123456789"):
        assert private not in response.get_data(as_text=True)


def test_ordinary_user_sees_only_allowed_jobs(world):
    mine = _job(world.alice, world.rec_a, error_message="ValueError: a", completed_at=datetime.utcnow())
    plain = _job(world.alice, world.rec_plain, error_message="ValueError: p", completed_at=datetime.utcnow())
    theirs = _job(world.bob, world.rec_b, error_message="ValueError: b", completed_at=datetime.utcnow())
    c, _ = world.client(world.alice)
    data = c.get(f"{PREFIX}/health").json
    assert {r["id"] for r in data["recent_failures"]} == {mine.id, plain.id}
    assert data["counts"]["failed"] == 2 and data["can_retry"] is False
    c, _ = world.client(world.bob)
    assert {r["id"] for r in c.get(f"{PREFIX}/health").json["recent_failures"]} == {theirs.id}
    c, _ = world.client(world.admin)
    assert {r["id"] for r in c.get(f"{PREFIX}/health").json["recent_failures"]} == {mine.id, plain.id, theirs.id}


def test_anonymous_and_clinic_mode_off_are_refused(world, monkeypatch):
    assert world.flask.test_client().get(f"{PREFIX}/health").status_code == 401
    c, _ = world.client(world.admin)
    monkeypatch.setenv("CLINIC_MODE", "0")
    assert c.get(f"{PREFIX}/health").status_code == 404


def test_retry_is_refused_for_a_non_operations_user(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a")
    for user in (world.alice, world.bob):
        c, h = world.client(user)
        res = _retry(c, h, job)
        assert res.status_code == 403 and res.json["code"] == "FORBIDDEN"
    assert _new_jobs(job) == []
    # A missing job gives the same answer: no existence leak.
    c, h = world.client(world.bob)
    assert c.post(f"{PREFIX}/99999/retry", headers=h, json={}).status_code == 403


def test_retry_without_csrf_is_refused(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a")
    c, _ = world.client(world.admin)
    assert _retry(c, {}, job).status_code == 403
    assert _new_jobs(job) == []


def test_retry_twice_with_the_same_key_enqueues_one_job(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a", completed_at=datetime.utcnow(),
               params=json.dumps({"language": "de"}))
    c, h = world.client(world.admin)
    key = str(uuid.uuid4())
    first, second = _retry(c, h, job, key), _retry(c, h, job, key)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json == second.json and first.json["status"] == "queued"
    created = _new_jobs(job)
    assert len(created) == 1 and created[0].id == first.json["new_job_id"]
    assert created[0].status == "queued" and created[0].job_type == "transcribe"
    assert json.loads(created[0].params)["language"] == "de"
    # The old failed row is no longer offered; the retried attempt is not shown twice.
    assert c.get(f"{PREFIX}/health").json["recent_failures"] == []
    # A second key for the same failed attempt still gives one job.
    third = _retry(c, h, job)
    assert third.status_code == 200 and third.json["new_job_id"] == created[0].id
    assert len(_new_jobs(job)) == 1


def test_same_key_with_another_version_is_an_idempotency_conflict(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a")
    c, h = world.client(world.admin)
    key = str(uuid.uuid4())
    assert _retry(c, h, job, key).status_code == 200
    res = _retry(c, h, job, key, version=3)
    assert res.status_code == 409 and res.json["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(_new_jobs(job)) == 1


def test_stale_version_and_wrong_state_are_conflicts(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a", retry_count=2)
    c, h = world.client(world.admin)
    res = _retry(c, h, job, version=1)
    assert res.status_code == 409 and res.json["code"] == "REVISION_CONFLICT"
    running = _job(world.alice, world.rec_plain, "processing")
    assert _retry(c, h, running, version=0).status_code == 409
    assert _new_jobs(job) == [] and _new_jobs(running) == []
    assert c.get(f"{PREFIX}/health").json["recent_failures"][0]["version"] == 2


def test_unknown_outcome_and_keyed_jobs_are_not_retried_blindly(world):
    unknown = _job(world.alice, world.rec_a, job_type="clinic_output", error_message="ClinicDeliveryUnknown: x",
                   params=json.dumps({"delivery_started": True}), completed_at=datetime.utcnow())
    keyed = _job(world.alice, world.rec_plain, job_type="clinic_notify", error_message="ClinicJobError: x",
                 params=json.dumps({"clinic_release_id": "rel-1"}), completed_at=datetime.utcnow())
    c, h = world.client(world.admin)
    rows = {r["id"]: r for r in c.get(f"{PREFIX}/health").json["recent_failures"]}
    assert rows[unknown.id]["state"] == "UNKNOWN"
    res = _retry(c, h, unknown)
    assert res.status_code == 422 and res.json["code"] == "RECONCILE_REQUIRED"
    res = _retry(c, h, keyed)
    assert res.status_code == 422 and res.json["code"] == "NOT_RETRYABLE"
    assert _new_jobs(unknown) == [] and _new_jobs(keyed) == []


def test_malformed_retry_requests_are_rejected(world):
    job = _job(world.alice, world.rec_a, error_message="ValueError: a")
    c, h = world.client(world.admin)
    key = str(uuid.uuid4())
    url = f"{PREFIX}/{job.id}/retry"
    base = {**h, "Idempotency-Key": key, "If-Match": '"0"'}
    ok = {"request_id": key, "expected_version": 0}
    assert c.post(url, headers=base, json={**ok, "user_id": 1}).status_code == 400
    assert c.post(url, headers=base, json={**ok, "request_id": str(uuid.uuid4())}).status_code == 400
    assert c.post(url, headers=base, json={**ok, "expected_version": "0"}).status_code == 400
    assert c.post(url, headers={**base, "If-Match": '"1"'}, json=ok).status_code == 400
    assert c.post(url, headers={k: v for k, v in base.items() if k != "If-Match"}, json=ok).status_code == 428
    assert c.post(url, headers={**base, "Idempotency-Key": "x"}, json=ok).status_code == 400
    assert c.post(f"{PREFIX}/99999/retry", headers=base, json=ok).status_code == 404
    assert _new_jobs(job) == []
