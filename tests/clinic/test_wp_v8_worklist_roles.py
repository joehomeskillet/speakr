"""V8 P02: worklist entries carry dictated_by, staff_pools and lease_holder (v8-04).

Synthetic rows only. The projection itself is owned by src/clinic/dictation_roles.py;
this file proves the worklist DTO passes it through additively.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import workflow, worklist_entries
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicDictation, ClinicReviewLease
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.models import Group, Recording, User
from src.services import audit

ROLE_FIELDS = ("dictated_by", "staff_pools", "lease_holder")


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
    user = User(username=f"v804_{name}_{tag}", email=f"v804_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin, name=f"Staff {name} {tag}")
    db.session.add(user)
    db.session.flush()
    return user


def _staff_pool(admin, author_pool_id, label):
    group = Group(name=f"v804 {label} {uuid.uuid4().hex[:6]}", created_by=admin.id)
    db.session.add(group)
    db.session.flush()
    pool = ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add(pool)
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=author_pool_id, staff_pool_id=pool.id,
                                   created_by=admin.id))
    return pool


@pytest.fixture
def scene():
    with app.app_context():
        admin, author, typist = _user("admin", is_admin=True), _user("author"), _user("typist")
        author_pool = ClinicAuthorPool(name=f"v804 authors {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add(author_pool)
        db.session.flush()
        first = _staff_pool(admin, author_pool.id, "first")
        rec = Recording(user_id=author.id, title="Synthetic", status="COMPLETED",
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
        return dict(admin=admin.id, author=author.id, typist=typist.id, dictation=dictation.id,
                    author_pool=author_pool.id, first=first.id,
                    author_name=author.name, typist_name=typist.name)


def _entry(actor, dictation_id):
    return next(e for e in worklist_entries.load_entries(actor)
                if e.row["dictation_id"] == dictation_id)


def test_entry_carries_the_three_role_fields_and_keeps_existing_ones(scene):
    with app.app_context():
        row = _entry(scene["typist"], scene["dictation"]).row
        for key in ("dictation_id", "title", "status", "author_user_id", "assignee_user_id",
                    "open_mode", "revision"):
            assert key in row
        assert all(key in row for key in ROLE_FIELDS)
        assert row["dictated_by"] == {"id": str(scene["author"]), "display_name": scene["author_name"]}
        assert [pool["id"] for pool in row["staff_pools"]] == [scene["first"]]
        assert row["lease_holder"] is None


def test_lease_holder_is_set_only_while_the_lease_is_active(scene):
    with app.app_context():
        lease = ClinicReviewLease(dictation_id=scene["dictation"], holder_user_id=scene["typist"], generation=1,
                                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        db.session.add(lease)
        db.session.commit()
        holder = _entry(scene["typist"], scene["dictation"]).row["lease_holder"]
        assert holder["id"] == str(scene["typist"]) and holder["display_name"] == scene["typist_name"]
        lease.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        assert _entry(scene["typist"], scene["dictation"]).row["lease_holder"] is None


def test_author_pool_change_does_not_change_dictated_by(scene):
    with app.app_context():
        before = _entry(scene["typist"], scene["dictation"]).row
        second = _staff_pool(db.session.get(User, scene["admin"]), scene["author_pool"], "second")
        db.session.commit()
        after = _entry(scene["typist"], scene["dictation"]).row
        assert {pool["id"] for pool in after["staff_pools"]} == {scene["first"], second.id}
        assert after["dictated_by"] == before["dictated_by"]
        assert after["author_user_id"] == before["author_user_id"]
        # Moving the dictation to a pool without routes empties the staff pools only.
        dictation = db.session.get(ClinicDictation, scene["dictation"])
        dictation.pool_id = str(uuid.uuid4())
        db.session.commit()
        moved = _entry(scene["typist"], scene["dictation"]).row
        assert moved["staff_pools"] == []
        assert moved["dictated_by"] == before["dictated_by"]


def test_entry_carries_no_patient_data(scene):
    with app.app_context():
        row = _entry(scene["typist"], scene["dictation"]).row
        text = str({key: row[key] for key in ROLE_FIELDS}).lower()
        assert "patient" not in text and "synthetic" not in text
