"""Wiring of the V8 alignment routes and handler, and the honest correction API (v8-86).

Synthetic data only. The mounted-app cases start the real application in a child process with
CLINIC_MODE on (or off) and a throwaway SQLite database. The correction cases use the real
blueprint with the in-memory receipt stand-in of test_wp_v8_save_contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.app import db
from src.clinic import alignment as al
from src.clinic import alignment_tokens as at
from src.clinic.audio_revisions import apply_cut, current_audio_revision
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.models import ProcessingJob
from test_wp_v8_save_contract import get, legacy_api, save  # noqa: F401  (legacy_api is a fixture)

ROOT = Path(__file__).resolve().parents[2]
ENDPOINTS = ("clinic_alignment.get_document", "clinic_alignment.get_alignment",
             "clinic_alignment.post_alignment", "clinic_alignment.get_operation")

CHILD = r'''
import json, sys, uuid
from datetime import datetime, timezone
from flask import session
from flask_wtf.csrf import generate_csrf
from src.app import app, db
from src.clinic import identity, lease, pools, workflow
from src.clinic import alignment as al
from src.clinic import alignment_tokens as at
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.revisions import add_asr_revision
from src.clinic.route_policy import ROUTE_POLICY
from src.init_db import initialize_database
from src.models import Group, GroupMembership, ProcessingJob, Recording, User
from src.services import job_queue as jq

out = {}
endpoints = ["clinic_alignment.get_document", "clinic_alignment.get_alignment",
             "clinic_alignment.post_alignment", "clinic_alignment.get_operation"]
out["present"] = {e: any(r.endpoint == e for r in app.url_map.iter_rules()) for e in endpoints}
out["handler"] = "clinic_alignment" in jq._CLINIC_HANDLERS
out["policy"] = {e: getattr(ROUTE_POLICY.get(e), "kind", None) for e in endpoints}
if sys.argv[1] != "on":
    print("RESULT " + json.dumps(out))
    sys.exit(0)

def user(name, email, admin=False):
    row = User(username=name, email=email, password=identity.hash_password("Synthetic-pass-86!"), is_admin=admin)
    db.session.add(row)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only"))
    return row

with app.app_context():
    initialize_database(app)
    db.create_all()
    admin = user("a86", "admin86@local.test", True)
    author, typist = user("u86", "author86@local.test"), user("t86", "typist86@local.test")
    pool = ClinicAuthorPool(name="Autoren 86", created_by=admin.id)
    group = Group(name="Schreibdienst 86", created_by=admin.id)
    db.session.add_all([pool, group])
    db.session.flush()
    staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add(staff)
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff.id, created_by=admin.id))
    db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="member"))
    db.session.commit()
    pools.allow(typist.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("play", "edit", "release"))
    rec = Recording(user_id=author.id, title="Synthetisch", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec)
    db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool.id, version_counter=0)
    db.session.add(dic)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dic.id, content_hash="o" * 64, size_bytes=1000,
                              storage_key="orig/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:synthetic")
    db.session.add(original)
    db.session.flush()
    asr = add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")
    audio = init_audio_revision(dic, original, 4000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=author.id, pool_id=pool.id, assignee_user_id=typist.id,
        status=workflow.TO_WRITE, audio_seconds=4))
    held = lease.acquire(dic.id, typist.id, datetime.now(timezone.utc))
    db.session.commit()
    ids = dict(dictation=dic.id, typist=typist.id, asr=asr.id, audio=audio.id, generation=held.generation)

    client = app.test_client()
    with app.test_request_context():
        token = generate_csrf()
        raw = session["csrf_token"]
    with client.session_transaction() as s:
        s["_user_id"] = str(ids["typist"]); s["_fresh"] = True; s["csrf_token"] = raw
    base = "/api/clinic/v1/dictations/" + ids["dictation"]

    def hdr():
        return {"X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}

    at.tokens_for(dic, asr)  # the parent token row, so the saved revision carries its word ids
    db.session.commit()
    before = db.session.query(ProcessingJob).count()
    etag = client.get(base + "/correction").headers["ETag"]
    resp = client.post(base + "/correction/save", headers={**hdr(), "If-Match": etag}, json=dict(
        text="Anamnese Husten seit zwei Tagen.", audio_position_ms=100, lease_generation=ids["generation"],
        expected_text_revision_id=ids["asr"]))
    out["save_status"] = resp.status_code
    out["alignment_state"] = (resp.json or {}).get("alignment_state")
    new_rev = (resp.json or {}).get("text_revision_id")
    out["jobs_after_save"] = db.session.query(ProcessingJob).count() - before

    # A synthetic aligned source for the parent revision, so the carry-over has valid words to keep.
    parent = db.session.get(ClinicTranscriptRevision, ids["asr"])
    seed = al.create_pending(dic, audio, parent, source_kind="asr", engine="stub-engine",
                             model_revision="m1", audio_duration_ms=4000)
    words = [(n, t) for n in at.tokens_for(dic, parent).document["nodes"] for t in n["tokens"] if t["kind"] == "word"]
    al.complete(seed, [{"word_id": t["token_id"], "text_node_id": n["text_node_id"],
                        "token_index": n["tokens"].index(t), "displayed_text": t["text"], "state": "valid",
                        "start_ms": i * 400, "end_ms": i * 400 + 300, "reason_code": None}
                       for i, (n, t) in enumerate(words)])
    db.session.commit()
    seed_id = seed.id
    doc = client.get(base + "/transcript-revisions/" + str(new_rev) + "/document")
    out["document_status"] = doc.status_code
    dhash = (doc.json or {}).get("document_hash")
    body = dict(schema_version="2.0", audio_revision_id=ids["audio"], transcript_revision_id=new_rev,
                document_hash=dhash)
    post = client.post(base + "/alignment", headers=hdr(), json=body)
    out["post_status"] = post.status_code
    out["jobs_after_post"] = db.session.query(ProcessingJob).filter_by(job_type="clinic_alignment").count()
    op = post.json or {}
    row = db.session.query(al.ClinicAlignmentRevision).filter_by(
        dictation_id=ids["dictation"], transcript_revision_id=new_rev).first()
    params = {"dictation_id": ids["dictation"], "alignment_revision_id": row.id}
    jq._CLINIC_HANDLERS["clinic_alignment"](params, "corr-86")
    got = client.get(base + "/alignment", query_string=dict(audio_revision_id=ids["audio"],
                                                              transcript_revision_id=new_rev))
    out["get_status"] = got.status_code
    out["get_state"] = (got.json or {}).get("state")
    out["get_binding"] = [(got.json or {}).get("audio_revision_id"), (got.json or {}).get("transcript_revision_id")] == [ids["audio"], new_rev]
    operation = client.get(base + "/operations/" + str(op.get("operation_id") or op.get("id")))
    out["operation_status"] = operation.status_code
print("RESULT " + json.dumps(out))
'''


def _run_child(tmp_path: Path, mode: str) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w86.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if mode == "on":
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", CHILD, mode], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    line = [x for x in done.stdout.splitlines() if x.startswith("RESULT ")]
    assert line, done.stderr[-3000:]
    return json.loads(line[-1][len("RESULT "):])


def test_clinic_mode_off_has_no_alignment_endpoint(tmp_path):
    out = _run_child(tmp_path, "off")
    assert not any(out["present"].values())
    assert out["handler"] is False


def test_mounted_app_save_request_handler_read(tmp_path):
    out = _run_child(tmp_path, "on")
    assert all(out["present"].values()), out["present"]
    assert out["policy"] == {endpoint: "auth" for endpoint in ENDPOINTS}, out["policy"]
    assert out["handler"] is True
    assert out["save_status"] == 200, out
    assert out["alignment_state"] == "unaligned"
    assert out["jobs_after_save"] == 0
    assert out["document_status"] == 200
    assert out["post_status"] == 202, out
    assert out["jobs_after_post"] == 1
    assert out["get_status"] == 200 and out["get_binding"] is True
    assert out["get_state"] in ("succeeded", "partial")
    assert out["operation_status"] == 200


# --------------------------------------------------------------- correction API


def test_get_marks_links_as_segment_and_names_the_audio_head(legacy_api):
    client, _, ids = legacy_api
    body = get(client, ids).json
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    assert body["audio_revision_id"] == current_audio_revision(dictation).id
    assert body["links"] and all(link["granularity"] == "segment" for link in body["links"])


def test_get_after_an_audio_cut_returns_only_compatible_links(legacy_api):
    client, _, ids = legacy_api
    before = get(client, ids).json
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    cut = apply_cut(dictation, 5000, 6000)
    db.session.commit()
    after = get(client, ids).json
    assert after["audio_revision_id"] == cut.id != before["audio_revision_id"]
    kept = {link["id"] for link in after["links"]}
    assert kept < {link["id"] for link in before["links"]}


def test_save_reports_alignment_state_and_stores_tokens_without_a_job(legacy_api):
    client, headers, ids = legacy_api
    jobs = db.session.query(ProcessingJob).count()
    resp = save(client, headers, ids, "Anamnese Husten seit zwei Tagen.", if_match=None)
    assert resp.status_code == 200, resp.json
    assert resp.json["alignment_state"] == "unaligned"
    assert db.session.get(at.ClinicTranscriptTokens, resp.json["text_revision_id"]) is not None
    assert db.session.query(ProcessingJob).count() == jobs


def test_save_reports_stale_when_an_alignment_row_exists(legacy_api):
    client, headers, ids = legacy_api
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    asr = db.session.get(ClinicTranscriptRevision, ids["asr"])
    audio = current_audio_revision(dictation)
    al.create_pending(dictation, audio, asr, source_kind="asr", engine="stub", model_revision="m1",
                      audio_duration_ms=10000)
    db.session.commit()
    resp = save(client, headers, ids, "Anamnese Husten seit zwei Tagen.", if_match=None)
    assert resp.status_code == 200, resp.json
    assert resp.json["alignment_state"] == "stale"
