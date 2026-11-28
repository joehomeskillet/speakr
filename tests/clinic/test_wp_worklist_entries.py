"""Worklist visibility (W12/W13) and the complete dictation snapshot (x24).

Synthetic rows only. Runs in the shared clinic test database with clinic mode on.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import src.app as app_module
from src.app import app, db
from src.clinic import routes_dictation_properties, workflow, worklist_entries
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicTranscriptRevision,
)
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.models import AuditEvent, Group, GroupMembership, Recording, User
from src.services import audit

CONTRACT = Path(__file__).resolve().parents[2] / "clinic-spec/contracts/clinic-api.openapi.json"


@pytest.fixture(autouse=True)
def _clinic_mode(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setitem(app.config, "TESTING", True)
    with app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    yield
    audit.flush()
    audit.reset_memory()


def _user(name, *, is_admin=False, status=None):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"{name[:11]}_{tag}", email=f"x24_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin)
    db.session.add(user)
    db.session.flush()
    if status is not None:
        db.session.add(ClinicAccountPolicy(user_id=user.id, status=status, login_policy="device_only"))
    db.session.commit()
    return user


def _dictation(author, *, pool_id, assignee=None, status=workflow.TO_WRITE, title="Synthetic"):
    rec = Recording(user_id=author.id, title=title, status="COMPLETED", original_filename="x.wav",
                    transcription="SECRET-TRANSCRIPT-TEXT")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool_id,
                                version_counter=3)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author.id, pool_id=pool_id,
        assignee_user_id=None if assignee is None else assignee.id, status=status, audio_seconds=12))
    db.session.commit()
    return dictation


def _ids(actor_id):
    return {entry.row["dictation_id"] for entry in worklist_entries.load_entries(actor_id)}


@pytest.fixture
def scene():
    """Plain ids only, so every test runs in its own app context."""
    with app.app_context():
        admin = _user("admin", is_admin=True, status="active")
        revoked = _user("revoked", is_admin=True, status="disabled")
        author, other_author = _user("author"), _user("other_author")
        typist, foreign_typist, lead = _user("typist"), _user("foreign_typist"), _user("lead")
        author_pool = ClinicAuthorPool(name=f"x24 authors {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(author_pool)
        group = Group(name=f"x24 typists {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(group)
        db.session.flush()
        staff_pool = ClinicStaffPool(group_id=group.id, created_by=admin.id)
        db.session.add(staff_pool)
        db.session.flush()
        db.session.add(ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff_pool.id,
                                       created_by=admin.id))
        db.session.add_all([
            GroupMembership(group_id=group.id, user_id=lead.id, role="admin"),
            GroupMembership(group_id=group.id, user_id=typist.id, role="member"),
        ])
        db.session.commit()
        pool = author_pool.id
        own = _dictation(author, pool_id=pool, assignee=typist, title="Own and assigned")
        unassigned = _dictation(author, pool_id=pool, title="Klaerliste")
        foreign_item = _dictation(other_author, pool_id=str(uuid.uuid4()), title="Foreign pool")
        return dict(
            revoked=revoked.id, author=author.id, other=other_author.id, typist=typist.id,
            foreign=foreign_typist.id, lead=lead.id, pool=pool,
            own=own.id, unassigned=unassigned.id, foreign_item=foreign_item.id,
        )


def test_author_sees_own_and_assigned_typist_sees_only_assignments(scene):
    with app.app_context():
        assert _ids(scene["author"]) == {scene["own"], scene["unassigned"]}
        assert _ids(scene["typist"]) == {scene["own"]}
        assert _ids(scene["other"]) == {scene["foreign_item"]}


def test_foreign_typist_is_denied(scene):
    with app.app_context():
        assert _ids(scene["foreign"]) == set()


def test_pool_lead_sees_the_klaerliste_of_the_routed_pool_only(scene):
    with app.app_context():
        assert _ids(scene["lead"]) == {scene["unassigned"]}
        entries = worklist_entries.load_entries(scene["lead"])
        assert entries[0].pool_id == scene["pool"]


def test_row_carries_the_typist_and_assignment_time(scene):
    with app.app_context():
        typist = db.session.get(User, scene["typist"])
        typist.name = "Tina Schreibkraft"
        db.session.commit()
        rows = {entry.row["dictation_id"]: entry.row
                for entry in worklist_entries.load_entries(scene["author"])}
        assigned = rows[scene["own"]]
        assert assigned["assignee_user_id"] == scene["typist"]
        assert assigned["recipient"] == "Tina Schreibkraft"
        assert assigned["assigned_at"] is None
        empty = rows[scene["unassigned"]]
        assert empty["assignee_user_id"] is None
        assert empty["recipient"] is None and empty["assigned_at"] is None

        flow = db.session.get(workflow.ClinicWorkflowDictation, scene["own"])
        flow.assigned_at = datetime(2026, 11, 27, 8, 30, tzinfo=timezone.utc)
        stale = db.session.get(workflow.ClinicWorkflowDictation, scene["unassigned"])
        stale.assigned_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.session.commit()
        again = {entry.row["dictation_id"]: entry.row
                 for entry in worklist_entries.load_entries(scene["author"])}
        assert again[scene["own"]]["assigned_at"] == "2026-11-27T08:30:00Z"
        assert again[scene["own"]]["recipient"] == "Tina Schreibkraft"
        assert again[scene["unassigned"]]["recipient"] is None
        assert again[scene["unassigned"]]["assigned_at"] is None


def test_locked_row_keeps_assignment_and_hides_notes(scene, monkeypatch):
    with app.app_context():
        typist = db.session.get(User, scene["typist"])
        typist.name = "Tina Schreibkraft"
        dictation = db.session.get(ClinicDictation, scene["own"])
        db.session.get(Recording, dictation.recording_id).notes = "Synthetic protected notes"
        flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id)
        flow.assigned_at = datetime(2026, 11, 27, 8, 30, tzinfo=timezone.utc)
        db.session.commit()

        monkeypatch.setattr(routes_dictation_properties, "content_unlocked",
                            lambda actor, did: (actor, did) != (scene["typist"], scene["own"]))
        locked, = worklist_entries.load_entries(scene["typist"])
        assert locked.row["notes"] == ""
        assert locked.row["recipient"] == "Tina Schreibkraft"
        assert locked.row["assigned_at"] == "2026-11-27T08:30:00Z"
        unlocked = next(entry for entry in worklist_entries.load_entries(scene["author"])
                        if entry.row["dictation_id"] == scene["own"])
        assert unlocked.row["notes"] == "Synthetic protected notes"


def test_pool_lead_loses_access_when_dictation_is_assigned(scene):
    with app.app_context():
        flow = db.session.get(workflow.ClinicWorkflowDictation, scene["unassigned"])
        flow.assignee_user_id = scene["typist"]
        db.session.commit()
        assert _ids(scene["lead"]) == set()
        assert scene["unassigned"] in _ids(scene["typist"])


def test_admin_sees_all_and_foreign_reads_are_audited():
    with app.app_context():
        admin = _user("admin", is_admin=True, status="active")
        author = _user("author")
        own = _dictation(admin, pool_id=str(uuid.uuid4()), title="Admin own")
        foreign = _dictation(author, pool_id=str(uuid.uuid4()), title="Foreign")
        own_id, foreign_id, foreign_recording = own.id, foreign.id, foreign.recording_id
        admin_id = admin.id
        baseline = db.session.execute(select(func.max(AuditEvent.id))).scalar_one() or 0
        assert {own_id, foreign_id} <= _ids(admin_id)
        audit.flush()
        rows = AuditEvent.query.filter(
            AuditEvent.id > baseline, AuditEvent.actor_user_id == admin_id,
            AuditEvent.event == "recording.content.read").all()
        assert {row.recording_id for row in rows} == {foreign_recording}
        assert rows[0].details == {"part": "worklist"} and rows[0].dedup_key is None
        assert "SECRET-TRANSCRIPT-TEXT" not in str(rows[0].to_dict())


def test_deactivated_admin_has_no_extra_access(scene):
    with app.app_context():
        baseline = db.session.execute(select(func.max(AuditEvent.id))).scalar_one() or 0
        assert _ids(scene["revoked"]) == set()
        audit.flush()
        assert AuditEvent.query.filter(AuditEvent.id > baseline,
                                       AuditEvent.actor_user_id == scene["revoked"]).count() == 0


def test_snapshot_covers_every_contract_field_without_content():
    required = set(json.loads(CONTRACT.read_text("utf-8"))["components"]["schemas"]["Dictation"]["required"])
    with app.app_context():
        author = _user("author")
        dictation = _dictation(author, pool_id=str(uuid.uuid4()), status=workflow.TO_CORRECT)
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="h", size_bytes=1,
                                  storage_key="k", receipt="r", origin_ref="o")
        db.session.add(original)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="h")
        text = ClinicTranscriptRevision(dictation_id=dictation.id, kind="ASR", body="SECRET-BODY",
                                        normalised_text_hash="t" * 64, original_version_id=original.id,
                                        model_ref="synthetic")
        context = ClinicContextRevision(dictation_id=dictation.id, case_ref="C-1", patient_ref="P-SECRET")
        db.session.add_all([audio, text, context])
        db.session.commit()

        snap = worklist_entries.dictation_snapshot(dictation)
        assert set(snap) == required
        assert snap["dictation_id"] == dictation.id and snap["author_user_id"] == str(author.id)
        assert snap["pool_id"] == dictation.pool_id
        assert snap["ingest_state"] == "STORED" and snap["submission_state"] == "SUBMITTED"
        assert snap["context_state"] == "VERIFIED" and snap["asr_state"] == "SUCCEEDED"
        assert snap["review_state"] == "QUEUED"
        assert snap["current_original_version_id"] == original.id
        assert snap["current_transcript_revision_id"] == text.id
        assert snap["current_context_revision_id"] == context.id
        assert snap["active_release_id"] is None
        assert snap["etag"] == f'"{dictation.id}:3"' and snap["created_at"].endswith("Z")
        assert "SECRET-BODY" not in str(snap) and "P-SECRET" not in str(snap)


def test_snapshot_of_a_bare_dictation_reports_unresolved_states():
    with app.app_context():
        dictation = _dictation(_user("author"), pool_id=str(uuid.uuid4()), status=workflow.DRAFT)
        snap = worklist_entries.dictation_snapshot(dictation)
        assert snap["submission_state"] == "DRAFT" and snap["context_state"] == "CASE_UNRESOLVED"
        assert snap["asr_state"] == "PENDING" and snap["current_transcript_revision_id"] is None


def test_app_wires_both_functions():
    source = inspect.getsource(app_module.register_clinic_worklist)
    assert "worklist_entries.load_entries" in source and "worklist_entries.dictation_snapshot" in source
