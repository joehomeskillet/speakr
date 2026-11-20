"""V8 worklist rows carry role names, assignee name and a German source label (v8-119).

Synthetic rows only. Rows come from the real loader and the real get_worklist projection.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.app import app, db
from src.clinic import policy, routes_worklist, workflow, worklist_entries
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.worklist_layout import LayoutStore
from src.models import Group, GroupMembership, Recording, User
from src.services import audit
from test_pools import pool_db as pool_db  # remove this scene's upstream groups and memberships


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
def scene(pool_db):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        author = _user("author", full_name="Dr. Anna Autorin")
        typist = _user("typist", full_name="Tina Tippt")
        stranger = _user("stranger", full_name="Sam Stranger")
        lead = _user("lead", full_name="Lena Leitung")
        author_pool = ClinicAuthorPool(name=f"v8119 A {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(author_pool)
        db.session.flush()
        group = Group(name=f"v8119 staff {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(group)
        db.session.flush()
        db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="user"))
        db.session.add(GroupMembership(group_id=group.id, user_id=lead.id, role="admin"))
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
        return dict(author=author.id, typist=typist.id, lead=lead.id, pool=author_pool.id, stranger=stranger.id, dictation=dictation.id,
                    staff=staff.id, staff_name=staff.name)


def _add_dictation(scene, origin, *, assignee=True):
    """A further synthetic dictation of the scene's author; origin None means no original row."""
    rec = Recording(user_id=scene["author"], title="t", status="COMPLETED", original_filename="x.wav")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=scene["author"],
                                pool_id=scene["pool"], version_counter=1)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=scene["author"], pool_id=scene["pool"],
        assignee_user_id=scene["typist"] if assignee else None, status=workflow.TO_WRITE,
        audio_seconds=5))
    if origin is not None:
        digest = uuid.uuid4().hex
        db.session.add(ClinicOriginal(
            dictation_id=dictation.id, content_hash=digest, size_bytes=1, storage_key=digest,
            receipt=json.dumps({"v": 1, "origin": origin}), origin_ref=f"{origin}:ref"))
    db.session.commit()
    return dictation.id


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
        assert row["source_kind"] == "audio" and row["source_label"] is None  # no original stored
        assert row["assignee_user_id"] == scene["typist"] and row["author_user_id"] == scene["author"]


def test_viewer_without_access_gets_no_row_and_so_no_names(scene):
    with app.app_context():
        assert _rows(scene["stranger"]) == ()


def test_source_label_follows_original_receipt_origin(scene):
    with app.app_context():
        expected = {_add_dictation(scene, origin): label for origin, label in (
            ("web-recorder", "Mikrofon"), ("web-upload", "Upload"), ("agent", "Import"),
            ("batch", "Import"), ("legacy-backfill", "Import"), ("martian", None), (None, None))}
        by_id = {row["dictation_id"]: row for row in _rows(scene["typist"])}
        for dictation_id, label in expected.items():
            assert by_id[dictation_id]["source_label"] == label
            assert by_id[dictation_id]["source_kind"] == "audio"
    assert routes_worklist.ORIGIN_LABELS["live"] == "Live"


def test_unassigned_row_has_no_assignee(scene):
    with app.app_context():
        flow = db.session.get(workflow.ClinicWorkflowDictation, scene["dictation"])
        flow.assignee_user_id = None
        db.session.commit()
        (row,) = _rows(scene["author"])
        assert row["assignee"] is None and row["assignee_user_id"] is None


def test_pool_lead_sees_unassigned_row_without_assignee(scene):
    with app.app_context():
        unassigned = _add_dictation(scene, "web-upload", assignee=False)
        by_id = {row["dictation_id"]: row for row in _rows(scene["lead"])}
        assert by_id[unassigned]["assignee"] is None
        assert by_id[unassigned]["dictated_by"]["display_name"] == "Dr. Anna Autorin"


def test_rows_never_contain_names_of_non_participants(scene):
    with app.app_context():
        rows = _rows(scene["typist"])
        dump = json.dumps(rows, default=str)
        assert "Sam Stranger" not in dump and "Lena Leitung" not in dump
        assert "Tina Tippt" in dump and "Dr. Anna Autorin" in dump


def test_rows_carry_no_patient_data(scene):
    with app.app_context():
        (row,) = _rows(scene["typist"])
        assert "PATIENT-TITLE-SECRET" not in str({k: row[k] for k in (
            "dictated_by", "staff_pools", "assignee", "source_label", "lease_holder")})


def test_visible_page_exposes_only_its_unique_case_reference(scene):
    from src.clinic.models import ClinicContextRevision

    with app.app_context():
        second = _add_dictation(scene, None)
        actor = scene["author"]
        lookups = []

        def load_cases(ids):
            lookups.append(ids)
            return worklist_entries.case_displays(ids)

        def page(user_id, number=1):
            return routes_worklist.get_worklist(
                worklist_entries.load_entries(user_id), policy.build_subject(user_id), LayoutStore(),
                page=number, page_size=1, load_case_displays=load_cases)

        initial = page(actor)
        first_id = initial.page.items[0]["dictation_id"]
        assert "case_display" not in initial.page.items[0]
        other_id = next(did for did in (scene["dictation"], second) if did != first_id)
        head = ClinicContextRevision(dictation_id=first_id, case_ref="SYN-0001", patient_ref="PRIVATE-PATIENT")
        db.session.add_all([head, ClinicContextRevision(dictation_id=other_id, case_ref="SYN-0002")])
        db.session.commit()
        bound = page(actor)
        assert bound.page.items[0]["case_display"] == "SYN-0001"
        assert "PRIVATE-PATIENT" not in str(bound.page.items)
        assert bound.cursor != initial.cursor
        assert page(actor, 2).page.items[0]["case_display"] == "SYN-0002"
        assert lookups == [[first_id], [first_id], [other_id]]
        assert page(scene["stranger"]).page.total == 0
        assert len(lookups) == 3

        db.session.add_all([ClinicContextRevision(dictation_id=first_id, parent_revision_id=head.id,
                                                  case_ref=reference)
                            for reference in ("SYN-0003", "SYN-0004")])
        db.session.commit()
        ambiguous = page(actor)
        assert "case_display" not in ambiguous.page.items[0]
        assert ambiguous.cursor != bound.cursor
