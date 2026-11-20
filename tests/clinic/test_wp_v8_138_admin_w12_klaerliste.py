"""W12/W13 against the production routes and an isolated synthetic clinic database."""

import io
import subprocess
import sys
import uuid
import wave
from datetime import datetime
from pathlib import Path

import pytest

from src.app import app, db
from src.clinic import policy, pool_policy, pools, revisions
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import AuditEvent, GroupMembership, Recording, User
from src.services import audit
from test_wp_mount_correction import _env
from test_wp_v8_152_small_hardening import _scene
from test_wp_worklist_entries import _dictation, _user


def _client(user):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=user.get_id(), _fresh=True)
    return client


def _admin_reads(tmp):
    _, _, draft_id, _ = _scene(tmp)
    with app.app_context():
        admin = _user("admin", is_admin=True, status="active")
        client = _client(admin)
        draft = db.session.get(ClinicDictation, draft_id)
        original = ClinicOriginal.query.filter_by(dictation_id=draft_id).one()
        revisions.add_asr_revision(draft, original, "Synthetic transcript", "synthetic")
        db.session.commit()
        uid, rec_id, author_id = admin.id, draft.recording_id, draft.author_user_id
        subject = policy.build_subject(uid)
        for action in ("edit", "release"):
            assert not pool_policy.decide(subject, action, draft).allowed
        # Both an own row and another foreign row compete for the page.
        own = _dictation(admin, pool_id=draft.pool_id)
        other = _dictation(db.session.get(User, draft.author_user_id), pool_id=draft.pool_id)
        db.session.get(Recording, other.recording_id).meeting_date = datetime(2020, 1, 1)
        db.session.commit()
        own_id, other_id = own.id, other.id

    recordings = "/api/clinic/v1/recordings"
    worklist = "/api/clinic/v1/worklist/rows"
    page = client.get(recordings + f"?author_id={author_id}&limit=1")
    assert page.status_code == 200
    assert [r["dictation_id"] for r in page.json["items"]] == [draft_id]
    assert page.json["next_cursor"]
    page = client.get(worklist)
    assert page.status_code == 200
    assert {r["dictation_id"] for r in page.json["items"]} == {draft_id, own_id, other_id}
    unchanged = client.get(worklist, headers={"If-None-Match": page.headers["ETag"]})
    assert unchanged.status_code == 304
    assert client.get(recordings + "?cursor=invalid").status_code == 400
    assert client.get(recordings + "?author_id=absent").json["items"] == []
    with app.app_context():
        assert audit.flush()
        reads = AuditEvent.query.filter_by(actor_user_id=uid, event="recording.content.read").all()
        other_rec_id = db.session.get(ClinicDictation, other_id).recording_id
        assert sorted(r.recording_id for r in reads) == sorted([rec_id, rec_id, other_rec_id])
        assert all(r.details == {"part": "worklist"} for r in reads)

    url = f"/api/clinic/v1/dictations/{draft_id}"
    transcript = client.get(url + "/correction")
    assert transcript.status_code == 200 and transcript.json["text"] == "Synthetic transcript"
    audio = client.get(url + "/audio/derived")
    assert audio.status_code == 200 and audio.mimetype == "audio/wav"
    with wave.open(io.BytesIO(audio.data)) as wav:
        assert wav.getnframes() == 16000
    headers = {"X-CSRF-Token": client.get("/api/csrf-token").json["csrf_token"],
               "Idempotency-Key": str(uuid.uuid4())}
    assert client.post(url + "/leases", headers=headers, json={}).status_code == 404


def _lead_list(tmp):
    _, _, draft_id, _ = _scene(tmp)
    with app.app_context():
        admin = _user("admin", is_admin=True, status="active")
        lead = _user("lead", status="active")
        client = _client(lead)
        draft = db.session.get(ClinicDictation, draft_id)
        staff = pools.create_staff_pool("Synthetic staff", actor_id=admin.id)
        pools.link_staff_pool(draft.pool_id, staff.id, actor_id=admin.id)
        db.session.add(GroupMembership(group_id=staff.group_id, user_id=lead.id, role="admin"))
        submitted = _dictation(db.session.get(User, draft.author_user_id), pool_id=draft.pool_id)
        sent_id = submitted.id
    page = client.get("/api/clinic/v1/worklist/rows")
    assert page.status_code == 200
    assert [r["dictation_id"] for r in page.json["items"]] == [sent_id]


@pytest.mark.parametrize("check", [_admin_reads, _lead_list], ids=["admin", "lead"])
def test_real_clinic_access(tmp_path, check):
    env = {**_env(tmp_path, True), "CLINIC_ORIGINALS_ROOT": str(tmp_path / "originals"),
           "SPEAKR_SKIP_BACKGROUND_TASKS": "true", "PYTHONPATH": str(Path(__file__).parent)}
    script = "import runpy, sys\nrunpy.run_path(sys.argv[1])[sys.argv[2]](sys.argv[3])"
    child = subprocess.run([sys.executable, "-c", script, __file__, check.__name__, str(tmp_path)], env=env,
                           capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, child.stderr[-4000:]
