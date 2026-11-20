"""V8 autosave contract on the running app: the CLINIC_MODE mount requires If-Match (428).

Synthetic data only. The real application (src.app, CLINIC_MODE on, throwaway SQLite database) is started in a
child process and the correction save route is called through its own test client.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import json, uuid
from src.app import app, db
from src.clinic import identity, pools, workflow
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.init_db import initialize_database
from src.models import Group, GroupMembership, Recording, User

app.config["WTF_CSRF_ENABLED"] = False
with app.app_context():
    initialize_database(app)
    db.create_all()

    def user(name, admin=False):
        row = User(username=name, email=name + "@local.test", password=identity.hash_password("Synthetic-pass-243!"), is_admin=admin)
        db.session.add(row)
        db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only"))
        return row

    admin, author, typist = user("a243", True), user("u243"), user("t243")
    pool = ClinicAuthorPool(name="Autoren 243", created_by=admin.id)
    group = Group(name="Schreibdienst 243", created_by=admin.id)
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
    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")
    init_audio_revision(dic, original, 4000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=author.id, pool_id=pool.id, assignee_user_id=typist.id,
        status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
    did, uid = dic.id, typist.id

client = app.test_client()
with client.session_transaction() as s:
    s["_user_id"] = str(uid)
    s["_fresh"] = True
from flask import session as flask_session
from flask_wtf.csrf import generate_csrf
with app.test_request_context():
    token = generate_csrf()
    secret = flask_session["csrf_token"]
with client.session_transaction() as s:
    s["csrf_token"] = secret
body = {"text": "x", "audio_position_ms": 0, "lease_generation": 1, "expected_text_revision_id": None}
url = f"/api/clinic/v1/dictations/{did}/correction/save"
bare = client.post(url, json=body, headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRFToken": token})
sent = client.post(url, json=body, headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRFToken": token, "If-Match": '"none"'})
print("RESULT " + json.dumps({"bare": bare.status_code, "bare_body": bare.get_data(as_text=True), "sent": sent.status_code}))
'''


def test_app_mount_requires_if_match(tmp_path):
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w243.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false", "CLINIC_MODE": "1"}
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, done.stderr[-3000:]
    out = json.loads(line[len("RESULT "):])
    assert out["bare"] == 428, out
    assert "PRECONDITION_REQUIRED" in out["bare_body"]
    assert out["sent"] != 428, out
