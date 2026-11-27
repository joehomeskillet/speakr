"""Reviewer discovery on the worklist (PAR-WF-010).

Beta is neither author nor assignee. With view, release and the current
acceptance rule, the waiting case is on Beta's list. Gamma's deny and an
unassigned typist stay out. W13 is unchanged. Beta returns the case through
the review command the UI posts; a later correction needs a new acceptance.
Synthetic rows only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app import app, db
from src.clinic import pools, revisions, workflow, worklist_entries
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.models import Group, GroupMembership, Recording, User

COMMANDS = "/api/clinic/v1/parity/commands"
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clinic_mode(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setitem(app.config, "TESTING", True)
    with app.app_context():
        _create_identity_tables()


def _user(name, *, is_admin=False):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"p028_{name}_{tag}"[:20], email=f"p028_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin)
    db.session.add(user)
    db.session.flush()
    return user


def _ids(actor_id):
    return {entry.row["dictation_id"] for entry in worklist_entries.load_entries(actor_id)}


def _scene():
    admin = _user("admin", is_admin=True)
    alpha, schreib, beta = _user("alpha"), _user("schreib"), _user("beta")
    gamma, typist, lead, viewer = _user("gamma"), _user("typist"), _user("lead"), _user("viewer")
    author_pool = ClinicAuthorPool(name=f"p028 authors {uuid.uuid4().hex[:6]}", created_by=admin.id)
    db.session.add(author_pool)
    group = Group(name=f"p028 typists {uuid.uuid4().hex[:6]}", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add(staff)
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff.id, created_by=admin.id))
    db.session.add_all([
        GroupMembership(group_id=group.id, user_id=lead.id, role="admin"),
        GroupMembership(group_id=group.id, user_id=typist.id, role="member"),
        GroupMembership(group_id=group.id, user_id=schreib.id, role="member"),
    ])
    db.session.commit()
    for user_id, actions in (
        (beta.id, ("view", "release")),
        (gamma.id, ("view", "release")),
        (viewer.id, ("view", "release")),
        (schreib.id, ("view", "edit", "release")),
    ):
        pools.allow(user_id, "all", actor_id=admin.id, actions=actions)
    return dict(admin=admin.id, alpha=alpha.id, schreib=schreib.id, beta=beta.id, gamma=gamma.id,
                typist=typist.id, lead=lead.id, viewer=viewer.id, pool=author_pool.id)


def _case(scene):
    alpha, schreib, pool = scene["alpha"], scene["schreib"], scene["pool"]
    item = workflow.start(db.session(), "create", author_user_id=alpha, pool_id=pool,
                          assignee_user_id=schreib, acceptance_required=True)
    rec = Recording(user_id=alpha, title="Waiting acceptance", status="COMPLETED", original_filename="x.wav")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(id=item.id, recording_id=rec.id, author_user_id=alpha, pool_id=pool)
    db.session.add(dictation)
    db.session.flush()
    for trigger in ("send_offline", "recognition_done"):
        workflow.transition(db.session(), item.id, trigger)
    original = ClinicOriginal(dictation_id=item.id, content_hash="a" * 64, size_bytes=1,
                              storage_key="k", receipt="r", origin_ref="o")
    db.session.add(original)
    db.session.flush()
    head = revisions.add_human_revision(dictation, None, "Korrektur eins.", schreib, original_version=original)
    db.session.add(ClinicAudioRevision(dictation_id=item.id, content_hash="b" * 64))
    context = ClinicContextRevision(dictation_id=item.id, source_system="test", tenant_scope="test",
                                    case_ref="C-1", patient_ref="P-1")
    db.session.add(context)
    db.session.flush()
    rule = AcceptanceLedger().publish(
        SCOPE_DICTATION, item.id, required=True, approver_user_ids=[scene["beta"], scene["gamma"]],
        deny_user_ids=[scene["gamma"]], now=NOW)
    klaer = Recording(user_id=alpha, title="Klaerliste", status="COMPLETED", original_filename="y.wav")
    db.session.add(klaer)
    db.session.flush()
    extra = ClinicDictation(recording_id=klaer.id, author_user_id=alpha, pool_id=pool)
    db.session.add(extra)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=extra.id, author_user_id=alpha, pool_id=pool, assignee_user_id=None,
        status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
    return dict(id=item.id, head=head.id, context=context.id, klaer=extra.id, rule=rule.rule_id)


def _client(user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    token = client.get("/api/csrf-token").json["csrf_token"]
    return client, token


def _worklist(user_id):
    client, _token = _client(user_id)
    page = client.get("/api/clinic/v1/worklist/rows?page_size=100")
    assert page.status_code == 200, page.get_data(as_text=True)[:400]
    return {row["dictation_id"] for row in page.json["items"]}


def _headers(token):
    return {"X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}


def _lease(client, token, dictation_id):
    response = client.post(f"/api/clinic/v1/dictations/{dictation_id}/leases", headers=_headers(token))
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.json


def _release_lease(client, token, dictation_id, held):
    response = client.post(
        f"/api/clinic/v1/dictations/{dictation_id}/leases/release", headers=_headers(token),
        json={"lease_id": held["lease_id"], "generation": held["generation"]})
    assert response.status_code == 200, response.get_data(as_text=True)[:400]


def _command(client, token, dictation_id, command, payload):
    with app.app_context():
        version = db.session.get(ClinicDictation, dictation_id).version_counter
    body = {"command": command, "request_id": str(uuid.uuid4()), "expected_revision": version + 1,
            "payload": payload | {"dictation_id": dictation_id}}
    return client.post(COMMANDS, headers=_headers(token), json=body)


def _rule_id(client, token, dictation_id):
    review = client.get(f"/api/clinic/v1/dictations/{dictation_id}/review")
    assert review.status_code == 200, review.get_data(as_text=True)[:400]
    return review.json["binding"]["acceptance_rule_id"], review.json


def _ui_cycle(_tmp):
    """Real routes. The parent starts this only after CLINIC_MODE is set."""
    from src.init_db import initialize_database

    with app.app_context():
        initialize_database(app)
        _create_identity_tables()
        app.config["TESTING"] = True
        scene = _scene()
        case = _case(scene)
        waiting, klaer = case["id"], case["klaer"]
        assert db.session.get(workflow.ClinicWorkflowDictation, waiting).status == workflow.TO_CORRECT
        assert waiting not in _ids(scene["beta"])
        assert waiting not in _ids(scene["gamma"])
        assert waiting not in _ids(scene["typist"])
        assert _ids(scene["lead"]) == {klaer}
        assert waiting in _ids(scene["schreib"])
        assert {waiting, klaer} <= _ids(scene["alpha"])

    schreib, schreib_token = _client(scene["schreib"])
    held = _lease(schreib, schreib_token, waiting)
    rule_id, _review = _rule_id(schreib, schreib_token, waiting)
    submitted = _command(schreib, schreib_token, waiting, "request_acceptance", {
        "text_revision_id": case["head"], "context_revision_id": case["context"],
        "acceptance_rule_id": rule_id})
    assert submitted.status_code == 200, submitted.get_data(as_text=True)[:400]
    _release_lease(schreib, schreib_token, waiting, held)

    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, waiting).status == workflow.TO_ACCEPT
        assert waiting in _ids(scene["beta"])
        assert waiting not in _ids(scene["gamma"])
        assert waiting not in _ids(scene["typist"])
        assert waiting not in _ids(scene["viewer"])
        assert _ids(scene["lead"]) == {klaer}
        beta_row = next(entry.row for entry in worklist_entries.load_entries(scene["beta"])
                        if entry.row["dictation_id"] == waiting)
        assert beta_row["status"] == workflow.TO_ACCEPT
        assert beta_row["open_mode"] == "correction"
        assert beta_row["assignee_user_id"] == scene["schreib"]
        assert scene["beta"] not in (beta_row["author_user_id"], beta_row["assignee_user_id"])

    assert waiting in _worklist(scene["beta"])
    assert waiting not in _worklist(scene["gamma"])
    assert waiting not in _worklist(scene["typist"])
    assert waiting not in _worklist(scene["viewer"])
    assert _worklist(scene["lead"]) == {klaer}

    beta, beta_token = _client(scene["beta"])
    inspected = beta.get(f"/api/clinic/v1/dictations/{waiting}/leases")
    assert inspected.status_code == 200 and inspected.json["may_acquire"] is True
    held = _lease(beta, beta_token, waiting)
    _rule, review = _rule_id(beta, beta_token, waiting)
    assert "reject" in review["allowed_actions"]
    returned = _command(beta, beta_token, waiting, "decide_acceptance", {
        "text_revision_id": case["head"], "decision": "return", "reason": "Bitte Menge prüfen"})
    assert returned.status_code == 200, returned.get_data(as_text=True)[:400]
    _release_lease(beta, beta_token, waiting, held)

    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, waiting).status == workflow.TO_CORRECT
        assert waiting not in _ids(scene["beta"])
        assert klaer in _ids(scene["lead"]) and waiting not in _ids(scene["lead"])

    schreib, schreib_token = _client(scene["schreib"])
    held = _lease(schreib, schreib_token, waiting)
    correction = f"/api/clinic/v1/dictations/{waiting}/correction"
    loaded = schreib.get(correction)
    assert loaded.status_code == 200, loaded.get_data(as_text=True)[:400]
    etag = loaded.headers["ETag"]
    save = schreib.post(correction + "/save", headers=_headers(schreib_token) | {"If-Match": etag},
                        json={"text": "Korrektur zwei.", "audio_position_ms": 100,
                              "lease_generation": held["generation"],
                              "expected_text_revision_id": case["head"]})
    assert save.status_code == 200, save.get_data(as_text=True)[:400]
    new_head = save.json["text_revision_id"]
    assert new_head != case["head"]
    rule_id, _review = _rule_id(schreib, schreib_token, waiting)
    again = _command(schreib, schreib_token, waiting, "request_acceptance", {
        "text_revision_id": new_head, "context_revision_id": case["context"],
        "acceptance_rule_id": rule_id})
    assert again.status_code == 200, again.get_data(as_text=True)[:400]
    _release_lease(schreib, schreib_token, waiting, held)

    beta, beta_token = _client(scene["beta"])
    held = _lease(beta, beta_token, waiting)
    stale = _command(beta, beta_token, waiting, "decide_acceptance", {
        "text_revision_id": case["head"], "decision": "accept"})
    assert stale.status_code == 409 and stale.json["code"] == "REVISION_CONFLICT"
    state = beta.get(f"/api/clinic/v1/dictations/{waiting}/acceptance-state")
    assert state.status_code == 200 and state.json["state"] != "angenommen"
    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, waiting).status == workflow.TO_ACCEPT
        assert waiting in _ids(scene["beta"])
        assert waiting not in _ids(scene["gamma"])
        assert waiting not in _ids(scene["typist"])

    accepted = _command(beta, beta_token, waiting, "decide_acceptance", {
        "text_revision_id": new_head, "decision": "accept"})
    assert accepted.status_code == 200, accepted.get_data(as_text=True)[:400]
    _release_lease(beta, beta_token, waiting, held)
    state = beta.get(f"/api/clinic/v1/dictations/{waiting}/acceptance-state")
    assert state.json["state"] == "angenommen"
    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, waiting).status == workflow.ACCEPTED
        assert waiting not in _ids(scene["beta"])


def test_beta_finds_the_waiting_case_and_a_new_correction_needs_acceptance(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'p028.db'}",
           "UPLOAD_FOLDER": str(uploads), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false",
           "CLINIC_MODE": "1", "SPEAKR_SKIP_BACKGROUND_TASKS": "true"}
    script = "import runpy, sys\nrunpy.run_path(sys.argv[1])[sys.argv[2]](sys.argv[3])"
    child = subprocess.run(
        [sys.executable, "-c", script, __file__, "_ui_cycle", str(tmp_path)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=110)
    assert child.returncode == 0, child.stderr[-4000:]


def test_only_the_current_acceptance_rule_lists_the_reviewer():
    with app.app_context():
        scene = _scene()
        alpha, pool = scene["alpha"], scene["pool"]
        item = workflow.start(db.session(), "create", author_user_id=alpha, pool_id=pool,
                              assignee_user_id=scene["schreib"], acceptance_required=True)
        rec = Recording(user_id=alpha, title="Superseded rule", status="COMPLETED", original_filename="z.wav")
        db.session.add(rec)
        db.session.flush()
        db.session.add(ClinicDictation(id=item.id, recording_id=rec.id, author_user_id=alpha, pool_id=pool))
        db.session.flush()
        workflow.transition(db.session(), item.id, "send_offline")
        workflow.transition(db.session(), item.id, "skip_recognition")
        row = db.session.get(workflow.ClinicWorkflowDictation, item.id)
        row.status = workflow.TO_ACCEPT
        ledger = AcceptanceLedger()
        ledger.publish(SCOPE_DICTATION, item.id, required=True, approver_user_ids=[scene["beta"]], now=NOW)
        ledger.publish(SCOPE_DICTATION, item.id, required=True, approver_user_ids=[scene["viewer"]], now=NOW,
                       expected_version=1)
        db.session.commit()
        assert item.id not in _ids(scene["beta"])
        assert item.id not in _ids(scene["gamma"])
        assert item.id in _ids(scene["viewer"])
        visible = next(entry.row for entry in worklist_entries.load_entries(scene["viewer"])
                       if entry.row["dictation_id"] == item.id)
        assert scene["viewer"] not in (visible["author_user_id"], visible["assignee_user_id"])
        assert item.id in _ids(scene["alpha"])
