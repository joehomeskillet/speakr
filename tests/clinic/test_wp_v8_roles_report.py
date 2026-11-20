"""Run the operator report against the real application's synthetic database."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.app import app, db
from src.clinic import audio_revisions, pools, revisions, workflow
from src.clinic.acceptance_policy import AcceptanceLedger, SCOPE_DICTATION
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicReviewLease
from src.models import AuditEvent, Group, GroupMembership, ProcessingJob, Recording, User

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/clinic_roles_report.py"
PRIVATE = "V8143-PRIVATE"


def _run(uri):
    env = {**os.environ, "CLINIC_MODE": "true", "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
           "SQLALCHEMY_DATABASE_URI": uri}
    return subprocess.run([sys.executable, SCRIPT], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


def test_report_projects_ids_and_states_from_real_database(clinic_on, world):
    author, typist = world["user_a"], world["user_b"]
    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.get(User, author).name = PRIVATE
        db.session.get(Recording, world["rec_a"]).title = PRIVATE
        group = Group(name=PRIVATE, created_by=author)
        authors = pools.ClinicAuthorPool(name=PRIVATE, created_by=author)
        db.session.add_all([group, authors, ClinicAccountPolicy(user_id=author, status="active")])
        db.session.flush()
        staff = pools.ClinicStaffPool(group_id=group.id, created_by=author, active=False)
        db.session.add(staff)
        db.session.flush()
        db.session.add_all([
            GroupMembership(group_id=group.id, user_id=typist, role="admin"),
            pools.ClinicAuthorPoolMember(pool_id=authors.id, user_id=author, created_by=author),
            pools.ClinicPoolRoute(author_pool_id=authors.id, staff_pool_id=staff.id, created_by=author),
        ])
        grant = pools.ClinicVisibilityGrant(user_id=typist, effect="allow", scope="pool",
                                           pool_id=authors.id, actions="play,edit", created_by=author)
        full = ClinicDictation(recording_id=world["rec_a"], author_user_id=author, pool_id=authors.id)
        empty = ClinicDictation(recording_id=world["rec_b"], author_user_id=typist)
        db.session.add_all([grant, full, empty])
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=full.id, content_hash="a" * 64, size_bytes=42, storage_key=PRIVATE,
            receipt=json.dumps({"origin": "web-upload", "storage_key": PRIVATE}),
            origin_ref=f"upload:recording:{empty.recording_id}:time={PRIVATE}",
        )
        imported = ClinicOriginal(
            dictation_id=full.id, content_hash="b" * 64, size_bytes=43, storage_key=PRIVATE,
            receipt=json.dumps({"origin": "batch"}), origin_ref=f"upload:recording:999:{PRIVATE}.wav",
        )
        context = ClinicContextRevision(dictation_id=full.id, case_ref=PRIVATE, patient_ref=PRIVATE)
        db.session.add_all([original, imported, context])
        db.session.flush()
        current = ClinicContextRevision(dictation_id=full.id, parent_revision_id=context.id,
                                        case_ref=PRIVATE, patient_ref=PRIVATE)
        db.session.add(current)
        db.session.flush()
        db.session.add_all([
            ClinicContextGrantUse(revision_id=current.id, actor_user_id=author, nonce_digest="a" * 64,
                                  issuer=PRIVATE, audience=PRIVATE, subject_reference=PRIVATE,
                                  purpose="bind_existing", verified_at=now),
            ClinicReviewLease(dictation_id=full.id, holder_user_id=typist, generation=1,
                              expires_at=now + timedelta(minutes=10)),
            pools.ClinicGrantAuthor(grant_id=grant.id, author_user_id=author),
            workflow.ClinicWorkflowDictation(id=full.id, author_user_id=author, pool_id=authors.id,
                                           assignee_user_id=typist, status=workflow.TO_CORRECT),
            AuditEvent(event="recording.content.read", event_group="access", actor_kind="user",
                       actor_user_id=typist, recording_id=full.recording_id, details={"part": PRIVATE}),
        ])
        db.session.get(ProcessingJob, world["job_a"]).error_message = PRIVATE
        audio = audio_revisions.init_audio_revision(full, original, 1000)
        audio_revisions.apply_cut(full, 100, 200)
        audio_revisions.undo(full)
        asr = revisions.add_asr_revision(full, original, PRIVATE, model_ref=PRIVATE)
        revisions.add_human_revision(full, asr, PRIVATE + "-EDIT", author_user_id=typist)
        ledger = AcceptanceLedger()
        ledger.publish(SCOPE_DICTATION, full.id, required=False, approver_user_ids=[], now=now)
        ledger.publish(SCOPE_DICTATION, full.id, required=True, approver_user_ids=[typist],
                       expected_version=1, now=now)
        db.session.commit()
        uri = db.engine.url.render_as_string(hide_password=False)
        result = _run(uri)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert PRIVATE not in result.stdout
        report = json.loads(result.stdout)
        assert report["pool_routes"][0]["staff_pool_id"] == staff.id
        assert report["staff_pools"][0]["routable"] is False
        assert report["staff_memberships"][0]["is_lead"] is True
        assert report["author_memberships"][0]["user_id"] == author
        assert report["grant_authors"] == [{"grant_id": grant.id, "author_user_id": author}]
        assert report["grants"][0]["actions"] == ["play", "edit"]
        accounts = {item["user_id"]: item["status"] for item in report["accounts"]}
        assert accounts[author] == "active"
        assert accounts[typist] is None
        rows = {row["dictation_id"]: row for row in report["dictations"]}
        row = rows[full.id]
        assert row["status"] == workflow.TO_CORRECT
        assert row["assignee_user_id"] == typist
        assert row["audio_revision_id"] == audio.id
        assert row["asr_revision_ids"] == [asr.id]
        assert row["human_revision_count"] == 1
        assert row["context_revision_id"] == current.id
        assert row["context_state"] == "grant_bound"
        assert row["acceptance_version"] == 2
        assert row["policy_requires_acceptance"] is True
        assert row["acceptance_required"] is False
        assert row["lease_holder_user_id"] == typist
        assert rows[empty.id]["status"] is None
        assert rows[empty.id]["audio_revision_id"] is None
        assert rows[empty.id]["asr_revision_ids"] == []
        assert rows[empty.id]["context_state"] == "missing"
        assert rows[empty.id]["acceptance_version"] is None
        source_ids = {item["original_version_id"]: item["source_recording_id"]
                      for item in report["recent_originals"] if item["dictation_id"] == full.id}
        assert source_ids == {original.id: empty.recording_id, imported.id: None}
        assert [row["version"] for row in report["acceptance_versions"]] == [1, 2]
        assert any(job["job_type"] == "transcribe" for job in report["jobs"])
        assert report["content_reads"] == [{"recording_id": full.recording_id, "actor_user_id": typist,
                                            "count": 1}]
        assert _run(uri).stdout == result.stdout


def test_report_does_not_create_a_missing_database(tmp_path):
    missing = tmp_path / "missing.sqlite"
    result = _run(f"sqlite:///{missing}")
    assert result.returncode == 1
    assert result.stdout == ""
    assert str(missing) not in result.stderr
    assert not missing.exists()
