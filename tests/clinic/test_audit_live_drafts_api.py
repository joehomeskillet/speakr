"""Exercise the mounted draft API with two session-authenticated users."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = r'''
from uuid import uuid4

from src.app import app, db
from src.clinic import live_session, policy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User

with app.app_context():
    owner = User(username="author", email="author@example.invalid", password="x")
    other = User(username="other", email="other@example.invalid", password="x")
    db.session.add_all([owner, other])
    db.session.flush()
    owner_id, other_id = owner.id, other.id
    ids = []
    for title in ("First audio", "Second audio"):
        recording = Recording(user_id=owner_id, title=title, status="PENDING")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner_id)
        db.session.add(dictation)
        db.session.flush()
        ids.append(dictation.id)
        db.session.add(ClinicOriginal(
            dictation_id=dictation.id, content_hash="a" * 64, size_bytes=1,
            storage_key=f"synthetic/{dictation.id}", receipt="stored", origin_ref="synthetic",
        ))
    db.session.commit()

def log_in(user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(user_id), _fresh=True)
    return client, {"X-CSRF-Token": client.get("/api/csrf-token").json["csrf_token"]}

base = "/api/clinic/v1/live/drafts"
alice, auth = log_in(owner_id)
bob, bob_auth = log_in(other_id)
first, second = (f"{base}/{draft_id}" for draft_id in ids)
assert app.test_client().get(base).status_code in (302, 401)
create = {"dictation_id": ids[0], "title": "First"}
assert alice.post(base, json=create).status_code in (400, 403)
assert alice.post(base, headers=auth, json=dict(create, owner_id=other_id)).status_code == 400
# Even a user's explicit cross-author edit grant does not grant access to another user's drafts.
policy.register_grant_provider(lambda uid: [
    policy.Grant("all", frozenset({"edit", "view"})),
] if uid == other_id else [])
assert bob.post(base, headers=bob_auth, json=create).status_code == 403

for draft_id, title in zip(ids, ("First", "Second")):
    response = alice.post(base, headers=auth, json={"dictation_id": draft_id, "title": title})
    assert response.status_code == 201, (response.status_code, response.json)
    assert (response.json["draft_id"], response.json["version"]) == (draft_id, 1)
assert alice.post(base, headers=auth, json=create).status_code == 409
listed = alice.get(base)
assert listed.headers["Cache-Control"] == "private, no-store"
assert {row["title"] for row in listed.json["drafts"]} == {"First", "Second"}
assert bob.get(base).json == {"drafts": []}

for path, text in ((first, "First saved"), (second, "Second saved")):
    loaded = alice.get(path)
    assert (loaded.json["text"], loaded.json["version"]) == ("", 1)
    saved = alice.put(path, headers=auth, json={"text": text, "base_version": 1})
    assert saved.status_code == 200
    assert (saved.json["text"], saved.json["version"]) == (text, 2)
conflict = alice.put(first, headers=auth, json={"text": "Stale", "base_version": 1})
assert conflict.status_code == 409
assert (conflict.json["current_text"], conflict.json["current_version"]) == ("First saved", 2)
assert alice.put(first, headers=auth, json={"text": "Invalid", "base_version": 2**65}).status_code == 400
assert alice.get(first).json["text"] == "First saved"
assert alice.get(second).json["text"] == "Second saved"

protection = f"/api/clinic/v1/dictations/{ids[0]}"
code = "synthetic-draft-code"
assert alice.put(protection + "/protection", headers=auth,
                 json={"version": 1, "secret": code}).status_code == 200
locked_owner, locked_auth = log_in(owner_id)
assert locked_owner.get(first).status_code == 403
assert [row["draft_id"] for row in locked_owner.get(base).json["drafts"]] == [ids[1]]
assert locked_owner.post(protection + "/unlock", headers=locked_auth,
                         json={"secret": code}).status_code == 200
assert locked_owner.get(first).json["text"] == "First saved"

selection = alice.post(first + "/switch", headers=auth, json={"tab_id": "tab-a"})
assert selection.status_code == 200
assert (selection.json["draft_id"], selection.json["state"]) == (ids[0], "PAUSED")
for path in (first, f"{base}/{uuid4()}"):
    assert bob.get(path).status_code == 403
    foreign = bob.put(path, headers=bob_auth, json={"text": "Other account", "base_version": 1})
    assert foreign.status_code == 403 and "current_text" not in foreign.json
    assert bob.post(path + "/discard", headers=bob_auth,
                    json={"confirm": True, "base_version": 2}).status_code == 403
    assert bob.post(path + "/switch", headers=bob_auth, json={"tab_id": "tab-b"}).status_code == 403

assert alice.post(first + "/discard", headers=auth,
                  json={"confirm": False, "base_version": 2}).status_code == 400
assert alice.post(first + "/discard", headers=auth, content_type="application/json",
                  data='{"base_version":2,"confirm":false,"confirm":true}').status_code == 400
assert alice.post(first + "/discard", headers=auth,
                  json={"confirm": True, "base_version": 1}).status_code == 409
assert alice.get(first).json["discarded_at"] is None
assert alice.post(second + "/switch", headers=auth, json={"tab_id": "tab-b"}).status_code == 409
archived = alice.post(first + "/discard", headers=auth, json={"confirm": True, "base_version": 2})
assert archived.status_code == 200 and archived.json["discarded_at"]
assert alice.get(first).json["text"] == "First saved"
assert [row["draft_id"] for row in alice.get(base).json["drafts"]] == [ids[1]]
assert alice.post(first + "/switch", headers=auth, json={"tab_id": "tab-a"}).status_code == 409
restart = alice.post("/api/clinic/v1/parity/commands", headers={**auth, "Idempotency-Key": str(uuid4())}, json={
    "command": "start_live_session", "request_id": str(uuid4()), "expected_revision": 0,
    "payload": {"dictation_id": ids[0], "profile_revision_id": str(uuid4()), "capture_mime": "audio/webm"},
})
assert restart.status_code == 409, (restart.status_code, restart.json)
assert alice.post(second + "/switch", headers=auth, json={"tab_id": "tab-b"}).status_code == 200
with app.app_context():
    assert ClinicOriginal.query.filter(ClinicOriginal.dictation_id.in_(ids)).count() == 2
    assert live_session.ClinicLiveSession.query.filter_by(dictation_id=ids[0]).one().state == live_session.ENDED
'''


def test_mounted_draft_workflow(tmp_path):
    env = {
        **os.environ, "CLINIC_MODE": "1", "CLINIC_WEB_ONLY": "0",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'drafts.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
        "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=Path(__file__).resolve().parents[2],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
