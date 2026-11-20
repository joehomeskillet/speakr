"""Acceptance publication (v8-74): approvers, Wiedervorlage and the acceptance-state read.

Synthetic data only. The real application (src/app.py, CLINIC_MODE on, staging profile so the
synthetic mock KIS adapter is trusted) runs in a child process against a throwaway database; users
log in through the session cookie, so the real guard, route policy, central policy, lease, ledger
and release services decide. Nothing is mocked in the path under test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import json, sys, uuid
from datetime import datetime, timezone
from src.app import app, db
from src.init_db import initialize_database
from src.models import Recording, User
from src.models.audit import AuditEvent
from src.clinic import policy, revisions, workflow
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import (ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
                               ClinicRelease)
from src.clinic.route_policy import ROUTE_POLICY

out = {}
NOW = datetime.now(timezone.utc)
POLICY = "/api/clinic/v1/dictations/{}/acceptance-policy"
STATE = "/api/clinic/v1/dictations/{}/acceptance-state"
COMMANDS = "/api/clinic/v1/parity/commands"
BASE = "/api/clinic/v1/dictations/{}"

out["classified"] = {e: ROUTE_POLICY[e].kind for e in (
    "clinic_acceptance_policy.read", "clinic_acceptance_policy.save", "clinic_workflow.read_acceptance_state")}
out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)

users = {}
with app.app_context():
    initialize_database(app)
    for name, admin in (("admin", True), ("author", False), ("approver", False), ("outsider", False),
                        ("inactive", False)):
        u = User(username="v874_" + name, email=f"v874_{name}@local.test", password="x", is_admin=admin)
        db.session.add(u); db.session.flush(); users[name] = u.id
        db.session.add(ClinicAccountPolicy(user_id=u.id, status="disabled" if name == "inactive" else "active",
                                           login_policy="local_only"))
    db.session.commit()

policy.register_grant_provider(
    lambda uid: [policy.Grant("all", frozenset({"view", "edit", "play", "release"}))] if uid == users["approver"] else [])


def make_dictation(text="Text A."):
    with app.app_context():
        item = workflow.start(db.session(), "create", author_user_id=users["author"], pool_id="pool-v874",
                              assignee_user_id=users["approver"], acceptance_required=True)
        rec = Recording(user_id=users["author"], title="Synthetisch", status="COMPLETED", original_filename="s.wav")
        db.session.add(rec); db.session.flush()
        dic = ClinicDictation(id=item.id, recording_id=rec.id, author_user_id=users["author"])
        db.session.add(dic); db.session.flush()
        for trigger in ("send_offline", "skip_recognition"):
            workflow.transition(db.session(), item.id, trigger)
        orig = ClinicOriginal(dictation_id=dic.id, content_hash="a" * 64, size_bytes=1, storage_key="k",
                              receipt="r", origin_ref="o")
        db.session.add(orig); db.session.flush()
        head = revisions.add_human_revision(dic, None, text, users["author"], original_version=orig)
        audio = ClinicAudioRevision(dictation_id=dic.id, content_hash="b" * 64)
        ctx = ClinicContextRevision(dictation_id=dic.id, source_system="mock", tenant_scope="t",
                                    case_ref="CASE-0001", patient_ref="opaque")
        db.session.add_all([audio, ctx]); db.session.flush()
        db.session.add(ClinicContextGrantUse(
            revision_id=ctx.id, nonce_digest=uuid.uuid4().hex.ljust(64, "0"), issuer="kis", audience="speakr",
            actor_user_id=users["author"], subject_reference="subject", purpose="bind_existing", verified_at=NOW))
        db.session.commit()
        return dict(id=dic.id, head=head.id, audio=audio.id, context=ctx.id)


class Browser:
    def __init__(self, uid):
        self.c = app.test_client()
        if uid is not None:
            with self.c.session_transaction() as s:
                s["_user_id"] = str(uid); s["_fresh"] = True
        self.token = self.c.get("/api/csrf-token").json["csrf_token"]

    def h(self, **more):
        return {"X-CSRF-Token": self.token, **more}

    def get(self, path):
        return self.c.get(path)

    def put_policy(self, did, version, approvers, required=True):
        return self.c.put(POLICY.format(did), headers=self.h(**{"If-Match": f'"{version}"'}),
                          json={"acceptance_required": required, "approver_ids": approvers})

    def lease(self, did):
        r = self.c.post(BASE.format(did) + "/leases", headers=self.h(**{"Idempotency-Key": str(uuid.uuid4())}))
        return r.json

    def command(self, d, command, payload):
        with app.app_context():
            version = db.session.get(ClinicDictation, d["id"], populate_existing=True).version_counter
        body = {"command": command, "request_id": str(uuid.uuid4()), "expected_revision": version + 1,
                "payload": payload | {"dictation_id": d["id"]}}
        return self.c.post(COMMANDS, headers=self.h(**{"Idempotency-Key": str(uuid.uuid4())}), json=body)

    def release(self, d, held, text_id, text):
        with app.app_context():
            version = db.session.get(ClinicDictation, d["id"], populate_existing=True).version_counter
        body = {"lease_id": held["lease_id"], "lease_generation": held["generation"],
                "original_version_id": d["audio"], "transcript_revision_id": text_id,
                "context_revision_id": d["context"], "document_type": "report",
                "mode": "PLATFORM_REVIEWED_KIS_DRAFT", "text_sha256": revisions.text_hash(text),
                "confirmed_content": True, "confirmed_context": True}
        return self.c.post(BASE.format(d["id"]) + "/releases", json=body, headers=self.h(**{
            "If-Match": f'"{d["id"]}:{version}"', "Idempotency-Key": str(uuid.uuid4())}))


def status_of(d):
    with app.app_context():
        return db.session.get(workflow.ClinicWorkflowDictation, d["id"], populate_existing=True).status


def audit_rows():
    with app.app_context():
        rows = db.session.query(AuditEvent).filter(AuditEvent.event == "recording.edited").order_by(AuditEvent.id)
        return [dict(actor=r.actor_user_id, outcome=r.outcome, details=json.dumps(r.details, default=str))
                for r in rows]


def body(r):
    return r.get_json(silent=True)


anon, admin, author = Browser(None), Browser(users["admin"]), Browser(users["author"])
approver, outsider, inactive = Browser(users["approver"]), Browser(users["outsider"]), Browser(users["inactive"])

# --- Dictation 1: publish, accept, release ---------------------------------------------------
d1 = make_dictation()
out["anon"] = dict(policy_get=anon.get(POLICY.format(d1["id"])).status_code,
                   policy_put=anon.put_policy(d1["id"], 0, [users["approver"]]).status_code,
                   state=anon.get(STATE.format(d1["id"])).status_code)
out["author_read_policy"] = author.get(POLICY.format(d1["id"])).status_code
out["author_put"] = author.put_policy(d1["id"], 0, [users["author"]]).status_code
out["outsider_put"] = outsider.put_policy(d1["id"], 0, [users["author"]]).status_code
out["outsider_get_policy"] = outsider.get(POLICY.format(d1["id"])).status_code
out["audit_after_refusals"] = audit_rows()
out["admin_get_policy"] = body(admin.get(POLICY.format(d1["id"])))
out["bad_approver_inactive"] = admin.put_policy(d1["id"], 0, [users["inactive"]]).status_code
out["bad_approver_unknown"] = admin.put_policy(d1["id"], 0, [999999]).status_code
published = admin.put_policy(d1["id"], 0, [users["approver"]])
out["publish"] = [published.status_code, body(published)]
out["publish_retry"] = [admin.put_policy(d1["id"], 0, [users["approver"]]).status_code]
out["publish_stale"] = admin.put_policy(d1["id"], 0, [users["approver"], users["author"]]).status_code
out["audit_after_publish"] = audit_rows()

held = approver.lease(d1["id"])
submit = {"text_revision_id": d1["head"], "context_revision_id": d1["context"], "acceptance_rule_id": body(approver.get(BASE.format(d1["id"]) + "/detail"))["binding"]["acceptanceRuleId"]}
out["submit"] = approver.command(d1, "request_acceptance", submit).status_code
out["state_before"] = body(approver.get(STATE.format(d1["id"])))["state"]
out["author_cannot_accept"] = author.command(d1, "decide_acceptance", {"text_revision_id": d1["head"], "decision": "accept"}).status_code
out["release_before_accept"] = (lambda r: [r.status_code, (body(r) or {}).get("code")])(approver.release(d1, held, d1["head"], "Text A."))
out["accept"] = approver.command(d1, "decide_acceptance", {"text_revision_id": d1["head"], "decision": "accept"}).status_code
out["state_accepted"] = body(approver.get(STATE.format(d1["id"])))["state"]
out["status_accepted"] = status_of(d1)
rel = approver.release(d1, held, d1["head"], "Text A.")
out["release"] = [rel.status_code, (body(rel) or {}).get("code")]
out["status_after_release"] = status_of(d1)

# --- Dictation 2: accept A, save B, Wiedervorlage --------------------------------------------
d2 = make_dictation()
assert admin.put_policy(d2["id"], 0, [users["approver"]]).status_code == 200
held2 = approver.lease(d2["id"])
approver.command(d2, "request_acceptance", {"text_revision_id": d2["head"], "context_revision_id": d2["context"], "acceptance_rule_id": body(approver.get(BASE.format(d2["id"]) + "/detail"))["binding"]["acceptanceRuleId"]})
assert approver.command(d2, "decide_acceptance", {"text_revision_id": d2["head"], "decision": "accept"}).status_code == 200
out["d2_status_a"] = status_of(d2)
audits_before = len(audit_rows())
correction = BASE.format(d2["id"]) + "/correction"
etag = approver.get(correction).headers["ETag"]
save = approver.c.post(correction + "/save", headers=approver.h(**{"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag}),
                       json={"text": "Text B.", "audio_position_ms": 100, "lease_generation": held2["generation"],
                             "expected_text_revision_id": d2["head"]})
out["save_b"] = [save.status_code, body(save) if save.status_code != 200 else None]
b_id = body(save)["text_revision_id"] if save.status_code == 200 else None
out["d2_status_b"] = status_of(d2)
out["d2_state_b"] = body(approver.get(STATE.format(d2["id"])))["state"]
out["d2_audit_added"] = audit_rows()[audits_before:]
rel_b = approver.release(d2, held2, b_id, "Text B.")
out["d2_release_b_early"] = [rel_b.status_code, (body(rel_b) or {}).get("code")]
out["d2_status_after_early"] = status_of(d2)
out["d2_accept_b"] = approver.command(d2, "decide_acceptance", {"text_revision_id": b_id, "decision": "accept"}).status_code
out["d2_status_accepted_b"] = status_of(d2)
resave = approver.c.post(correction + "/save", headers=approver.h(**{"Idempotency-Key": str(uuid.uuid4()), "If-Match": body(save)["etag"]}),
                         json={"text": "Text B.", "audio_position_ms": 100, "lease_generation": held2["generation"],
                               "expected_text_revision_id": b_id,
                               "expected_state_version": body(save)["state_version"]})
out["d2_unchanged_save"] = [resave.status_code, body(resave)]
out["d2_status_unchanged_save"] = status_of(d2)
rel_b2 = approver.release(d2, held2, b_id, "Text B.")
out["d2_release_b"] = [rel_b2.status_code, (body(rel_b2) or {}).get("code")]

# --- Dictation 3: return with a reason, state read ------------------------------------------
d3 = make_dictation()
assert admin.put_policy(d3["id"], 0, [users["approver"]]).status_code == 200
approver.lease(d3["id"])
approver.command(d3, "request_acceptance", {"text_revision_id": d3["head"], "context_revision_id": d3["context"], "acceptance_rule_id": body(approver.get(BASE.format(d3["id"]) + "/detail"))["binding"]["acceptanceRuleId"]})
ret = approver.command(d3, "decide_acceptance", {"text_revision_id": d3["head"], "decision": "return", "reason": "Dosierung unklar"})
out["d3_return"] = ret.status_code
out["d3_viewer"] = [approver.get(STATE.format(d3["id"])).status_code, body(approver.get(STATE.format(d3["id"])))]
author_view = author.get(STATE.format(d3["id"]))
out["d3_author_own_no_release"] = [author_view.status_code, body(author_view)]
no_view = outsider.get(STATE.format(d3["id"]))
out["d3_no_view"] = [no_view.status_code, no_view.get_data(as_text=True)]
out["d3_missing"] = approver.get(STATE.format(str(uuid.uuid4()))).status_code
out["d3_admin_no_view_grant"] = admin.get(STATE.format(d3["id"])).status_code
with app.app_context():
    out["releases"] = db.session.query(ClinicRelease).count()
print("RESULT:" + json.dumps(out))
'''


def _run(tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'v874.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false",
           "CLINIC_MODE": "1", "CLINIC_PROFILE": "staging"}
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=600)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-4000:]
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("v874"))


def test_new_routes_are_classified_auth_and_nothing_is_unclassified(run):
    assert run["classified"] == {"clinic_acceptance_policy.read": "auth", "clinic_acceptance_policy.save": "auth",
                                 "clinic_workflow.read_acceptance_state": "auth"}
    assert run["unclassified"] == []


def test_anonymous_gets_401_on_every_new_route(run):
    assert run["anon"] == {"policy_get": 401, "policy_put": 401, "state": 401}


def test_non_admin_cannot_publish_and_the_refusal_is_audited(run):
    assert run["author_read_policy"] == 200
    assert run["author_put"] == 403
    assert run["outsider_get_policy"] == 404 and run["outsider_put"] == 404
    denied = [row for row in run["audit_after_refusals"] if row["outcome"] == "denied"]
    assert len(denied) == 1 and "acceptance_policy" in denied[0]["details"]
    assert "Text" not in denied[0]["details"]


def test_admin_publishes_an_approver_and_it_is_audited(run):
    assert run["admin_get_policy"] == {"version": 0, "acceptance_required": True, "approver_ids": []}
    assert run["bad_approver_inactive"] == 422 and run["bad_approver_unknown"] == 422
    status, view = run["publish"]
    assert status == 200 and view["version"] == 1 and len(view["approver_ids"]) == 1
    assert run["publish_retry"] == [200]
    assert run["publish_stale"] == 409
    granted = [row for row in run["audit_after_publish"] if row["outcome"] == "success"
               and "acceptance_policy" in row["details"]]
    assert len(granted) == 1


def test_approver_accepts_and_release_succeeds_end_to_end(run):
    assert run["submit"] == 200
    assert run["state_before"] == "offen"
    assert run["author_cannot_accept"] in (403, 404)
    assert run["release_before_accept"] == [422, "ACCEPTANCE_REQUIRED"]
    assert run["accept"] == 200
    assert (run["state_accepted"], run["status_accepted"]) == ("angenommen", "abgenommen")
    assert run["release"] == [201, None]
    assert run["status_after_release"] == "fertiggestellt"


def test_save_after_accept_returns_to_to_accept_and_b_can_be_accepted(run):
    assert run["d2_status_a"] == "abgenommen"
    assert run["save_b"][0] == 200, run["save_b"]
    assert run["d2_status_b"] == "abzunehmen"
    assert run["d2_state_b"] == "wiedervorlage"
    added = [row for row in run["d2_audit_added"] if "workflow_acceptance_resubmitted" in row["details"]]
    assert len(added) == 1 and "Text B" not in added[0]["details"]
    assert run["d2_release_b_early"] == [422, "ACCEPTANCE_REQUIRED"]
    assert run["d2_status_after_early"] == "abzunehmen"
    assert run["d2_accept_b"] == 200 and run["d2_status_accepted_b"] == "abgenommen"
    # Saving the same text again does not move the head, so the accepted state stays.
    assert run["d2_unchanged_save"][0] == 200, run["d2_unchanged_save"]
    assert run["d2_status_unchanged_save"] == "abgenommen"
    assert run["d2_release_b"] == [201, None]


def test_acceptance_state_read_by_viewer_stranger_and_anonymous(run):
    status, state = run["d3_viewer"]
    assert status == 200 and state["state"] == "zurueckgegeben"
    assert state["reject_reason"] == "Dosierung unklar"
    assert run["d3_author_own_no_release"][0] == 200
    status, text = run["d3_no_view"]
    assert status == 403 and "Dosierung" not in text
    assert run["d3_missing"] == 404
    assert run["releases"] == 2
