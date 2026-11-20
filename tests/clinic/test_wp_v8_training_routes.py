"""Admin routes for ASR optimisation runs (V8 P15).

Synthetic rows only. The routes are mounted here with their route-policy rows (the
consumer package mounts them in the app). Nothing in these checks may open a socket.
"""

from __future__ import annotations

import socket
import uuid

import pytest

from clinic_support import login
from src.app import app, db
from src.clinic import routes_training
from src.clinic.asr_profiles import set_profile
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.route_policy import ADMIN, ROUTE_POLICY, RouteRule
from src.clinic.training_schedule import JOB_TYPE
from src.models import ProcessingJob, User
from src.services.job_queue import job_queue
from test_wp_training_manifest import _approved, clinic_db  # noqa: F401

P = routes_training.PREFIX


def _mount():
    if routes_training.blueprint.name not in app.blueprints:
        app.register_blueprint(routes_training.blueprint)
    for endpoint in routes_training.ENDPOINTS:
        ROUTE_POLICY.setdefault(endpoint, RouteRule(ADMIN))


_mount()


@pytest.fixture(autouse=True)
def _quiet(clinic_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(job_queue, "start", lambda: None)

    def no_network(*args, **kwargs):
        raise AssertionError("network access during a training route")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    with app.app_context():
        _create_identity_tables()
    yield
    db.session.rollback()
    ProcessingJob.query.filter_by(job_type=JOB_TYPE).delete()
    db.session.commit()


def _user(*, admin):
    tag = uuid.uuid4().hex[:10]
    user = User(username="v831" + tag, email=tag + "@example.invalid", password="x", is_admin=admin)
    db.session.add(user)
    db.session.flush()
    db.session.add(ClinicAccountPolicy(user_id=user.id, status="active", is_identity_admin=False))
    db.session.flush()
    return user


def _scene():
    admin = _user(admin=True)
    set_profile(admin.id, actor_id=admin.id, language="de", model="stub")
    _approved(admin, "Alpha Beta", None)
    db.session.commit()  # the queue writes on its own connection
    return admin


def _start(client, **extra):
    body = {"kind": "language_model", "scope": "author", "expected_profile_version": 1}
    body.update(extra)
    return client.post(P + "/runs", json=body)


def test_non_admin_and_anonymous_are_refused():
    author = _user(admin=False)
    db.session.commit()
    client = login(author.id)
    calls = (
        client.get(P + "/manifest"), client.get(P + "/schedule"), _start(client),
        client.get(P + "/runs/1"), client.post(P + "/runs/1/cancel"),
    )
    assert [r.status_code for r in calls] == [403] * 5
    assert app.test_client().get(P + "/schedule").status_code in (401, 403)
    assert ProcessingJob.query.filter_by(job_type=JOB_TYPE).count() == 0


def test_revoked_admin_is_refused():
    admin = _scene()
    db.session.get(ClinicAccountPolicy, admin.id).status = "disabled"
    db.session.commit()
    assert login(admin.id).get(P + "/schedule").status_code == 403


def test_manifest_read_is_sealed_and_starts_nothing():
    admin = _scene()
    body = login(admin.id).get(P + "/manifest").get_json()
    assert body["learning_claimed"] is False
    assert len(body["items"]) == 1
    assert len(body["manifest_hash"]) == 64
    assert ProcessingJob.query.filter_by(job_type=JOB_TYPE).count() == 0


def test_start_twice_creates_one_job_and_schedule_lists_it():
    admin = _scene()
    client = login(admin.id)
    first = _start(client)
    again = _start(client)
    assert (first.status_code, first.get_json()["state"]) == (201, "scheduled")
    assert (again.status_code, again.get_json()["state"]) == (200, "already_scheduled")
    assert again.get_json()["job_id"] == first.get_json()["job_id"]
    assert ProcessingJob.query.filter_by(user_id=admin.id, job_type=JOB_TYPE).count() == 1

    listed = client.get(P + "/schedule").get_json()["runs"]
    assert [row["state"] for row in listed] == ["queued"]
    assert listed[0]["manifest_hash"] == first.get_json()["manifest_hash"]
    one = client.get(P + "/runs/%d" % first.get_json()["job_id"]).get_json()
    assert one["state"] == "queued"


def test_cancel_removes_a_waiting_run_and_a_second_cancel_is_gone():
    admin = _scene()
    client = login(admin.id)
    job_id = _start(client).get_json()["job_id"]
    done = client.post(P + "/runs/%d/cancel" % job_id)
    assert (done.status_code, done.get_json()["outcome"]) == (200, "removed")
    assert client.post(P + "/runs/%d/cancel" % job_id).status_code == 404
    assert client.get(P + "/runs/%d" % job_id).status_code == 404


def test_cancel_marks_a_running_run():
    admin = _scene()
    client = login(admin.id)
    job_id = _start(client).get_json()["job_id"]
    db.session.get(ProcessingJob, job_id).status = "processing"
    db.session.commit()
    assert client.post(P + "/runs/%d/cancel" % job_id).get_json()["outcome"] == "cancel_requested"
    assert client.get(P + "/runs/%d" % job_id).get_json()["state"] == "cancelled"


def test_cancel_refuses_foreign_job_types():
    admin = _scene()
    from src.models import Recording
    rec = Recording(user_id=admin.id, title="x", status="COMPLETED")
    db.session.add(rec)
    db.session.flush()
    other = ProcessingJob(user_id=admin.id, recording_id=rec.id, job_type="transcribe", status="queued")
    db.session.add(other)
    db.session.commit()
    assert login(admin.id).post(P + "/runs/%d/cancel" % other.id).status_code == 404
    assert db.session.get(ProcessingJob, other.id) is not None
    db.session.delete(other)
    db.session.commit()


def test_start_refusals_are_separate_codes():
    admin = _scene()
    client = login(admin.id)
    assert _start(client, expected_profile_version=9).status_code == 409
    assert _start(client, kind="nope").status_code == 400
    assert _start(client, extra_key=1).status_code == 400
    assert client.post(P + "/runs", data="x").status_code == 400
    assert ProcessingJob.query.filter_by(job_type=JOB_TYPE).count() == 0


def test_clinic_mode_off_hides_the_routes(monkeypatch):
    admin = _scene()
    monkeypatch.delenv("CLINIC_MODE")
    assert login(admin.id).get(P + "/schedule").status_code == 404


def test_module_makes_no_network_import():
    source = open(routes_training.__file__, encoding="utf-8").read()
    for name in ("requests", "httpx", "urllib", "socket", "aiohttp"):
        assert "import " + name not in source and "from " + name not in source
