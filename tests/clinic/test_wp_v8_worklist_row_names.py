"""V8 worklist rows carry role names, assignee name and a German source label (v8-119).

Synthetic rows only. Rows come from the real loader and the real get_worklist projection.
"""

from __future__ import annotations

import uuid

import pytest

from src.app import app, db
from src.clinic import policy, routes_worklist, workflow, worklist_entries
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicDictation
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.worklist_layout import LayoutStore
from src.models import Group, GroupMembership, Recording, User
from src.services import audit


@pytest.fixture(autouse=True)
def _clinic_mode(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    yield
    audit.flush()
    audit.reset_memory()


def _user(name, *, full_name=None, is_admin=False):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"v8119_{name}_{tag}"[:20], email=f"v8119_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin, name=full_name)
    db.session.add(user)
    db.session.flush()
    return user


@pytest.fixture
def scene():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        author = _user("author", full_name="Dr. Anna Autorin")
        typist = _user("typist", full_name="Tina Tippt")
        stranger = _user("stranger", full_name="Sam Stranger")
        author_pool = ClinicAuthorPool(name=f"v8119 A {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(author_pool)
        db.session.flush()
        group = Group(name=f"v8119 staff {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(group)
        db.session.flush()
        db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="user"))
        staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
        db.session.add(staff)
        db.session.flush()
        db.session.add(ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff.id,
                                       created_by=admin.id))
        rec = Recording(user_id=author.id, title="PATIENT-TITLE-SECRET", status="COMPLETED",
                        original_filename="x.wav")
        db.session.add(rec)
        db.session.flush()
        dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id,
                                    pool_id=author_pool.id, version_counter=1)
        db.session.add(dictation)
        db.session.flush()
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dictation.id, author_user_id=author.id, pool_id=author_pool.id,
            assignee_user_id=typist.id, status=workflow.TO_WRITE, audio_seconds=5))
        db.session.commit()
        return dict(author=author.id, typist=typist.id, stranger=stranger.id, dictation=dictation.id,
                    staff=staff.id, staff_name=staff.name)


def _rows(actor):
    snapshot = routes_worklist.get_worklist(
        worklist_entries.load_entries(actor), policy.build_subject(actor), LayoutStore())
    return snapshot.page.items


def test_rows_carry_names_pools_and_source_label(scene):
    with app.app_context():
        (row,) = _rows(scene["typist"])
        assert row["dictated_by"] == {"id": str(scene["author"]), "display_name": "Dr. Anna Autorin"}
        assert row["staff_pools"] == [{"id": scene["staff"], "name": scene["staff_name"]}]
        assert row["assignee"] == {"id": str(scene["typist"]), "display_name": "Tina Tippt"}
        assert row["lease_holder"] is None
        assert row["source_kind"] == "audio" and row["source_label"] == "Mikrofon"
        assert row["assignee_user_id"] == scene["typist"] and row["author_user_id"] == scene["author"]


def test_viewer_without_access_gets_no_row_and_so_no_names(scene):
    with app.app_context():
        assert _rows(scene["stranger"]) == ()


def test_unassigned_row_has_no_assignee_and_unknown_source_has_no_label(scene):
    with app.app_context():
        flow = db.session.get(workflow.ClinicWorkflowDictation, scene["dictation"])
        flow.assignee_user_id = None
        db.session.commit()
        (row,) = _rows(scene["author"])
        assert row["assignee"] is None and row["assignee_user_id"] is None
    assert {k: routes_worklist.SOURCE_LABELS[k] for k in ("upload", "import", "live")} == {
        "upload": "Upload", "import": "Import", "live": "Live"}


def test_rows_carry_no_patient_data(scene):
    with app.app_context():
        (row,) = _rows(scene["typist"])
        assert "PATIENT-TITLE-SECRET" not in str({k: row[k] for k in (
            "dictated_by", "staff_pools", "assignee", "source_label", "lease_holder")})
