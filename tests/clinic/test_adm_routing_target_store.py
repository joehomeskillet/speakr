"""PAR-ADM-007: a submitted routing target stays on the workflow row.

Pool and all targets do not assign a typist and do not grant content rights.
"""

import pytest
from sqlalchemy import create_engine, inspect, text

from src.clinic import pools, recipient_defaults as rd, workflow, worklist_entries
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import GroupMembership, User

from test_pools import _dictation, _user, pool_db as pool_db
from test_wp_recipient_defaults import _world


def _load_user(user_id):
    return db.session.get(User, user_id)


def _open(author_id, pool_id, *, assignee=None):
    dictation = _dictation(_load_user(author_id), pool_id)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author_id, pool_id=pool_id,
        status=workflow.TO_WRITE, assignee_user_id=assignee,
    ))
    db.session.commit()
    return dictation.id


def test_default_person_pool_and_all_survive_reload():
    _actor, author, first, second, authors, staff = _world()
    author_id, first_id, second_id = author.id, first.id, second.id
    pool_id, staff_id = authors.id, staff.id
    rd.set_default(
        author_id, "person", actor_id=author_id, expected_version=0, target_ids=[str(first_id)],
    )
    cases = (
        ("default", None, "person", (str(first_id),), (first_id,)),
        ("person", {"mode": "person", "target_ids": [str(second_id)]}, "person", (str(second_id),), (second_id,)),
        ("pool", {"mode": "staff_pool", "target_ids": [staff_id]}, "staff_pool", (staff_id,), (first_id, second_id)),
        ("all", {"mode": "all_eligible", "target_ids": []}, "all_eligible", (), (first_id, second_id)),
    )
    stored = {}
    for name, override, mode, targets, recipients in cases:
        dictation_id = _open(author_id, pool_id)
        snapshot = workflow.store_routing_target(
            db.session, dictation_id, actor_id=author_id, override=override,
        )
        db.session.commit()
        stored[name] = dictation_id, snapshot, mode, targets, recipients
    db.session.remove()

    for name, dictation_id, snapshot, mode, targets, recipients in (
        (name, *stored[name]) for name, *_rest in cases
    ):
        row = db.session.get(workflow.ClinicWorkflowDictation, dictation_id)
        assert workflow.read_routing_target(row) == snapshot
        assert snapshot.mode == mode
        assert snapshot.target_ids == targets
        assert set(snapshot.recipient_user_ids) == set(recipients)
        assert snapshot.default_version == 1
        assert row.assignee_user_id is None
        public = workflow.routing_public(row)
        assert public["mode"] == mode and public["default_version"] == 1
        assert "Default v1" in public["label"]

    rd.set_default(author_id, "all_eligible", actor_id=author_id, expected_version=1)
    db.session.remove()
    kept_id, kept, *_rest = stored["default"]
    assert workflow.read_routing_target(
        db.session.get(workflow.ClinicWorkflowDictation, kept_id)
    ) == kept


def test_pool_and_all_grant_no_extra_content_and_stay_on_the_klaerliste():
    _actor, author, first, second, authors, staff = _world()
    author_id, first_id, second_id = author.id, first.id, second.id
    pool_id, staff_id = authors.id, staff.id
    dictation_id = _open(author_id, pool_id)
    loaded = db.session.get(ClinicDictation, dictation_id)
    watched = (author_id, first_id, second_id)
    before = {uid: pools.grants_for(uid, loaded) for uid in watched}
    grants = pools.ClinicVisibilityGrant.query.count()
    GroupMembership.query.filter_by(user_id=first_id).one().role = "admin"
    db.session.commit()

    for override in (
        {"mode": "staff_pool", "target_ids": [staff_id]},
        {"mode": "all_eligible", "target_ids": []},
    ):
        workflow.store_routing_target(db.session, dictation_id, actor_id=author_id, override=override)
        db.session.commit()
        db.session.remove()
        row = db.session.get(workflow.ClinicWorkflowDictation, dictation_id)
        assert row.assignee_user_id is None
        loaded = db.session.get(ClinicDictation, dictation_id)
        assert {uid: pools.grants_for(uid, loaded) for uid in watched} == before
        assert pools.ClinicVisibilityGrant.query.count() == grants
        member = {entry.row["dictation_id"] for entry in worklist_entries.load_entries(second_id)}
        lead = {entry.row["dictation_id"] for entry in worklist_entries.load_entries(first_id)}
        assert dictation_id not in member
        assert dictation_id in lead
        shown = next(entry.row for entry in worklist_entries.load_entries(first_id)
                     if entry.row["dictation_id"] == dictation_id)
        assert shown["assignee_user_id"] is None
        assert workflow.routing_public(row)["mode"] == override["mode"]

    outsider = _user()
    assert pools.grants_for(outsider.id, db.session.get(ClinicDictation, dictation_id)) == {
        "allowed": {}, "denies": [],
    }


def test_foreign_actor_stores_nothing():
    actor, author, _first, _second, authors, _staff = _world()
    dictation_id = _open(author.id, authors.id)
    with pytest.raises(workflow.WorkflowError) as raised:
        workflow.store_routing_target(db.session, dictation_id, actor_id=actor.id)
    assert raised.value.code == "unauthorized"
    db.session.rollback()
    row = db.session.get(workflow.ClinicWorkflowDictation, dictation_id)
    assert workflow.read_routing_target(row) is None
    assert row.assignee_user_id is None


def test_legacy_workflow_gains_a_null_routing_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wf.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE clinic_workflow_dictation (id TEXT PRIMARY KEY, assignee_user_id INTEGER)"
        ))
        conn.execute(text("INSERT INTO clinic_workflow_dictation VALUES ('old', 7)"))
    workflow.create_tables(engine)
    workflow.create_tables(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("clinic_workflow_dictation")}
    assert "routing_target_json" in columns
    assert "assigned_at" in columns
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT id, assignee_user_id, routing_target_json FROM clinic_workflow_dictation"
        )).one() == ("old", 7, None)
