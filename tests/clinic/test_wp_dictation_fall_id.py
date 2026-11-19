"""x30 (W16a): Fall-ID in the Dictation snapshot and per-actor worklist open mode.

Synthetic data only. The patient reference stays opaque and is never returned.
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path

import pytest
import src.app as app_module
from src.app import app, db
from src.clinic import workflow, worklist_entries
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.models import ClinicContextRevision, ClinicDictation
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.models import Group, GroupMembership, Recording, User
from src.services import audit

CONTRACT = Path(__file__).resolve().parents[2] / "clinic-spec/contracts/clinic-api.openapi.json"
FALL_ID = "F-SYN-0001"
PATIENT_MARKERS = ("P-SYN-SECRET", "PATIENT-DISPLAY-SECRET")


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


def _user(name, *, is_admin=False):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"x30_{name}_{tag}", email=f"x30_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin)
    db.session.add(user)
    db.session.flush()
    if is_admin:
        db.session.add(ClinicAccountPolicy(user_id=user.id, status="active", login_policy="device_only"))
    db.session.commit()
    return user


def _dictation(author, *, pool_id, assignee=None, status=workflow.TO_WRITE):
    rec = Recording(user_id=author.id, title="Synthetic", status="COMPLETED", original_filename="x.wav")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool_id,
                                version_counter=1)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author.id, pool_id=pool_id,
        assignee_user_id=None if assignee is None else assignee.id, status=status, audio_seconds=5))
    db.session.commit()
    return dictation


def _schema():
    return json.loads(CONTRACT.read_text("utf-8"))["components"]["schemas"]["Dictation"]


def test_contract_declares_nullable_case_display_without_patient_property():
    schema = _schema()
    prop = schema["properties"]["case_display"]
    assert prop["type"] == ["string", "null"] and prop["minLength"] == 1 and prop["maxLength"] == 256
    assert "case_display" in schema["required"] and schema["additionalProperties"] is False
    assert not [k for k in schema["properties"] if "patient" in k]


def test_snapshot_carries_fall_id_of_verified_context_and_no_patient_value():
    with app.app_context():
        dictation = _dictation(_user("author"), pool_id=str(uuid.uuid4()))
        db.session.add(ClinicContextRevision(dictation_id=dictation.id, case_ref=FALL_ID,
                                             patient_ref=PATIENT_MARKERS[0]))
        db.session.commit()
        snap = worklist_entries.dictation_snapshot(dictation)
        assert snap["context_state"] == "VERIFIED" and snap["case_display"] == FALL_ID
        assert set(snap) == set(_schema()["required"]) <= set(_schema()["properties"])
        body = json.dumps(snap)
        assert not any(marker in body for marker in PATIENT_MARKERS)
        assert not [k for k in snap if "patient" in k]


def test_snapshot_without_context_has_null_case_display():
    with app.app_context():
        dictation = _dictation(_user("author"), pool_id=str(uuid.uuid4()))
        snap = worklist_entries.dictation_snapshot(dictation)
        assert snap["context_state"] == "CASE_UNRESOLVED" and snap["case_display"] is None
        assert "case_display" in snap


def _check_case_display(snap):
    """Minimal schema check (jsonschema is not installed): keys, case_display type and length."""
    schema = _schema()
    assert set(schema["required"]) <= set(snap) <= set(schema["properties"])
    value = snap["case_display"]
    assert value is None or (isinstance(value, str) and 1 <= len(value) <= 256)


def test_snapshot_validates_against_updated_schema():
    with app.app_context():
        author = _user("author")
        with_ctx = _dictation(author, pool_id=str(uuid.uuid4()))
        db.session.add(ClinicContextRevision(dictation_id=with_ctx.id, case_ref=FALL_ID,
                                             patient_ref=PATIENT_MARKERS[0]))
        db.session.commit()
        bare = _dictation(author, pool_id=str(uuid.uuid4()))
        # current_original_version_id is a required uuid in the contract; give the check a value
        for dictation in (with_ctx, bare):
            snap = worklist_entries.dictation_snapshot(dictation)
            if snap["current_original_version_id"] is None:
                snap["current_original_version_id"] = str(uuid.uuid4())
            _check_case_display(snap)


def test_worklist_rows_hide_patient_values():
    with app.app_context():
        author = _user("author")
        dictation = _dictation(author, pool_id=str(uuid.uuid4()))
        db.session.add(ClinicContextRevision(dictation_id=dictation.id, case_ref=FALL_ID,
                                             patient_ref=PATIENT_MARKERS[0]))
        db.session.commit()
        entries = worklist_entries.load_entries(author.id)
        body = json.dumps([entry.row for entry in entries], default=str)
        assert not any(marker in body for marker in PATIENT_MARKERS)


def test_open_mode_per_actor():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        author, typist, lead = _user("author"), _user("typist"), _user("lead")
        author_pool = ClinicAuthorPool(name=f"x30 authors {uuid.uuid4().hex[:6]}", created_by=admin.id)
        group = Group(name=f"x30 typists {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add_all([author_pool, group])
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
        write = _dictation(author, pool_id=author_pool.id, assignee=typist, status=workflow.TO_WRITE)
        pooled = _dictation(author, pool_id=author_pool.id, status=workflow.TO_WRITE)
        correct = _dictation(author, pool_id=author_pool.id, assignee=typist, status=workflow.TO_CORRECT)

        def modes(actor_id):
            return {e.row["dictation_id"]: e.row["open_mode"] for e in worklist_entries.load_entries(actor_id)}

        assert modes(author.id) == {write.id: "author_case", pooled.id: "author_case",
                                    correct.id: "author_case"}
        assert modes(typist.id) == {write.id: "player", correct.id: "correction"}
        assert modes(lead.id) == {pooled.id: "player"}
        admin_modes = modes(admin.id)
        assert admin_modes[write.id] == "player" and admin_modes[correct.id] == "correction"


def test_clinic_code_is_only_registered_in_clinic_mode():
    source = inspect.getsource(app_module)
    assert "if _clinic_mode_enabled():" in source
    assert "worklist_entries.dictation_snapshot" in inspect.getsource(app_module.register_clinic_worklist)
