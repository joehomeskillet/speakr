"""Lease lifecycle and takeover capability through the real clinic app."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Clinic routes are registered at import time. Start the app in a fresh process
# with its own database so the suite's existing app can keep its startup mode.
SCRIPT = r'''
import sys
from uuid import uuid4

from src.app import app, db
from src.clinic import pools, workflow, worklist_entries
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.init_db import initialize_database
from src.models import GroupMembership, Recording, User

app.testing = True
queue_trigger, initial_status = sys.argv[1:]
with app.app_context():
    initialize_database(app)
    users = {}
    for name in ("admin", "lead", "typist", "outsider", "other_admin"):
        user = User(username=name, email=f"{name}@example.invalid", password="synthetic",
                    is_admin=name in ("admin", "other_admin"))
        db.session.add(user)
        db.session.flush()
        users[name] = user.id
        db.session.add(ClinicAccountPolicy(user_id=user.id, status="active"))
    db.session.commit()

    admin_id, typist_id = users["admin"], users["typist"]
    author_pool = pools.create_author_pool("Authors", actor_id=admin_id)
    staff_pool = pools.create_staff_pool("Typists", actor_id=admin_id)
    pools.link_staff_pool(author_pool.id, staff_pool.id, actor_id=admin_id)
    pools.add_staff_member(staff_pool.id, typist_id, actor_id=admin_id)
    db.session.add(GroupMembership(group_id=staff_pool.group_id, user_id=users["lead"], role="admin"))
    pools.allow(typist_id, "pool", actor_id=admin_id, pool_id=author_pool.id,
                actions=["play", "edit", "release"])
    pools.allow(admin_id, "own", actor_id=admin_id, actions=["release"])

    recording = Recording(user_id=admin_id, title="Synthetic", status="COMPLETED")
    db.session.add(recording)
    flow = workflow.start(db.session(), "create", author_user_id=admin_id, pool_id=author_pool.id,
                          assignee_user_id=typist_id)
    did = flow.id
    db.session.add(ClinicDictation(id=did, recording_id=recording.id, author_user_id=admin_id,
                                  pool_id=author_pool.id))
    if queue_trigger != "draft":
        workflow.transition(db.session(), did, "send_offline")
        workflow.transition(db.session(), did, queue_trigger)
    db.session.commit()

clients, tokens = {}, {}
for name, uid in users.items():
    client = clients[name] = app.test_client()
    with client.session_transaction() as browser:
        browser["_user_id"] = str(uid)
        browser["_fresh"] = True
    tokens[name] = client.get("/api/csrf-token").json["csrf_token"]

base = f"/api/clinic/v1/dictations/{did}/leases"

def post(name, operation="", body=None, *, key=None, status=200):
    headers = {"X-CSRF-Token": tokens[name], "Idempotency-Key": key or str(uuid4())}
    args = {} if body is None else {"json": body}
    response = clients[name].post(base + operation, headers=headers, **args)
    assert response.status_code == status, response.get_data(as_text=True)
    return response.json

def proof(held):
    return {key: held[key] for key in ("lease_id", "generation")}

def observe(expected, *, after=None):
    headers = {} if after is None else {"If-None-Match": after}
    response = clients["admin"].get("/api/clinic/v1/worklist/rows", headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    item = next(item for item in response.json["items"] if item["dictation_id"] == did)
    assert item["status"] == expected, item
    return response.headers["ETag"]

before = observe(initial_status)
held = post("typist")
assert held["generation"] == 1
assert clients["typist"].get(base).json["lease_id"] == held["lease_id"]
assert 'lease_id' not in clients["admin"].get(base).json
assert 'lease_id' not in clients["lead"].get(base).json
editing = initial_status if initial_status == workflow.DRAFT else workflow.IN_PROGRESS
opened = observe(editing, after=before)
if initial_status == workflow.TO_WRITE:
    with app.app_context():
        for actor in (typist_id, users["other_admin"]):
            row = next(entry.row for entry in worklist_entries.load_entries(actor)
                       if entry.row["dictation_id"] == did)
            assert row["open_mode"] == "player", row
        snapshot = worklist_entries.dictation_snapshot(db.session.get(ClinicDictation, did))
        assert snapshot["asr_state"] == "SKIPPED", snapshot
        assert snapshot["review_state"] == "IN_REVIEW", snapshot
assert proof(post("typist")) == proof(held)  # same-holder acquire with a new key
key = str(uuid4())
renewed = post("typist", "/renew", proof(held), key=key)
assert proof(renewed) == proof(held)
assert post("typist", "/renew", proof(held), key=key) == renewed
observe(editing)

for name, allowed in (("admin", True), ("lead", True), ("typist", False)):
    response = clients[name].get(base)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["may_take_over"] is allowed, response.json
assert clients["outsider"].get(base).status_code == 404

lead = post("lead", "/takeover", {"confirm": True})
assert lead["generation"] == 2 and lead["previous_holder_user_id"] == typist_id
post("typist", "/release", proof(held), status=409)
observe(editing)
taken = post("admin", "/takeover", {"confirm": True})
assert taken["generation"] == 3 and taken["previous_holder_user_id"] == users["lead"]
key = str(uuid4())
closed = post("admin", "/release", proof(taken), key=key)
assert closed["review_state"] == "QUEUED", closed
assert post("admin", "/release", proof(taken), key=key) == closed
observe(initial_status, after=opened)
assert clients["admin"].get(base).json["held"] is False
'''


@pytest.mark.parametrize(
    ("trigger", "status"),
    [("skip_recognition", "zu_schreiben"), ("recognition_done", "zu_korrigieren"), ("draft", "neu_angelegt")],
)
def test_edit_lease_updates_worklist_and_preserves_takeover_policy(tmp_path, trigger, status):
    env = {
        **os.environ,
        "CLINIC_MODE": "1",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'lease.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "ENABLE_AUTO_PROCESSING": "false",
        "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
    }
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, trigger, status], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr
