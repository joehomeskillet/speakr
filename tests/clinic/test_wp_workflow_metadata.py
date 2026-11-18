"""Atomic metadata edit and reassignment (PAR-WF-005, PAR-REC-006).

Service-level coverage of this slice. Original BT-REC-006 and BT-WF-005
oracles stay unchanged; routes and audit persistence belong to consumers.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.clinic import lease, pools, recipient_defaults, workflow
from src.clinic import workflow_metadata as wm
from src.clinic.metadata_changes import ChangeError
from src.clinic.models import ClinicDictation, ClinicReviewLease
from src.database import db
from src.models import Recording

from test_pools import _dictation, _suffix, _user, pool_db as pool_db

ALL = {"rename", "priority", "reassign"}
NOW = datetime(2030, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _tables(pool_db):
    workflow.create_tables()
    recipient_defaults.create_tables()
    ClinicReviewLease.__table__.create(bind=db.engine, checkfirst=True)
    yield


def _world():
    admin = _user()
    author = _user()
    other = _user()
    typist = _user()
    authors = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(authors.id, author.id, actor_id=admin.id)
    pools.add_author_member(authors.id, other.id, actor_id=admin.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=admin.id)
    pools.add_staff_member(staff.id, typist.id, actor_id=admin.id)
    dictation = _dictation(author, authors.id)
    row = workflow.start(
        db.session, "create", author_user_id=author.id, pool_id=authors.id
    )
    db.session.commit()
    held = lease.acquire(dictation.id, author.id, NOW)
    db.session.commit()
    return author, other, typist, authors, dictation, row, held


def _apply(world, requested, *, version=0, actions=ALL, actor=None, generation=None):
    author, _o, _t, _a, dictation, row, held = world
    return wm.apply_metadata(
        dictation_id=dictation.id,
        workflow_id=row.id,
        actor_user_id=(actor or author).id,
        expected_version=version,
        lease_generation=held.generation if generation is None else generation,
        requested=requested,
        allowed_actions=actions,
        now=NOW + timedelta(seconds=1),
    )


def test_positive_all_fields_and_version_bump():
    world = _world()
    _a, _o, typist, _p, dictation, row, _l = world
    result = _apply(
        world,
        {
            "title": "  Bericht  ",
            "category": "Befund",
            "recipient_user_id": typist.id,
            "priority": "Dringend",
            "deadline_date": date(2030, 2, 1),
        },
    )
    assert result.version == 1
    assert result.deferred == {"category": "Befund"}
    assert {r["field"] for r in result.audit_rows} == {
        "title", "assignee_user_id", "priority", "deadline_date"
    }
    db.session.refresh(row)
    assert row.priority == 128 and row.assignee_user_id == typist.id
    assert row.deadline_at == datetime(2030, 2, 1, 22, 59, 59, tzinfo=timezone.utc)
    assert db.session.get(Recording, dictation.recording_id).title == "Bericht"
    assert db.session.get(ClinicDictation, dictation.id).version_counter == 1


@pytest.mark.parametrize(
    "label,code", [("Bei Gelegenheit", 0), ("Normal", 1), ("Dringend", 128), (128, 128)]
)
def test_priority_labels_and_codes(label, code):
    world = _world()
    _apply(world, {"priority": label if code != 1 else "Dringend"})
    result = _apply(world, {"priority": label}, version=1)
    assert world[5].priority == code
    assert result.version == 2


def test_priority_rejects_unknown():
    with pytest.raises(ChangeError) as raised:
        _apply(_world(), {"priority": "sofort"})
    assert raised.value.code == "invalid_priority"


def test_deadline_needs_date_and_time_defaults():
    world = _world()
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"deadline_time": time(8, 30)})
    assert raised.value.code == "deadline_date_required"
    _apply(world, {"deadline_date": date(2030, 7, 1), "deadline_time": time(8, 30)})
    assert world[5].deadline_at == datetime(2030, 7, 1, 6, 30, tzinfo=timezone.utc)
    _apply(world, {"deadline_date": None}, version=1)
    assert world[5].deadline_at is None and world[5].deadline_time is None


def test_unauthorized_action_writes_nothing():
    world = _world()
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"title": "x", "priority": 128}, actions={"rename"})
    assert raised.value.code == "forbidden"
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"category": "x"}, actions={"priority"})
    assert raised.value.code == "forbidden"
    assert db.session.get(ClinicDictation, world[4].id).version_counter == 0
    assert world[5].priority == 1


def test_stale_version_writes_nothing_and_retry_succeeds():
    world = _world()
    _apply(world, {"title": "eins"})
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"title": "zwei"}, version=0)
    assert raised.value.code == "stale_version"
    assert db.session.get(Recording, world[4].recording_id).title == "eins"
    assert _apply(world, {"title": "zwei"}, version=1).version == 2


def test_holder_checks():
    world = _world()
    _a, other, _t, _p, _d, _r, held = world
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"title": "x"}, actor=other)
    assert raised.value.code == "lease_foreign_holder"
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"title": "x"}, generation=held.generation + 1)
    assert raised.value.code == "lease_stale_generation"
    assert db.session.get(ClinicDictation, world[4].id).version_counter == 0


def test_reassign_author_pool_and_audit_old_new():
    world = _world()
    author, other, _t, authors, dictation, row, _l = world
    result = _apply(world, {"author_user_id": other.id})
    change = next(r for r in result.audit_rows if r["field"] == "author_user_id")
    assert (change["old"], change["new"]) == (author.id, other.id)
    assert row.author_user_id == other.id
    assert db.session.get(ClinicDictation, dictation.id).author_user_id == other.id


def test_reassign_ineligible_is_rejected_atomically():
    world = _world()
    _a, _o, typist, _p, dictation, row, _l = world
    outsider = _user()
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"title": "neu", "author_user_id": outsider.id})
    assert raised.value.code == "ineligible_author"
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"recipient_user_id": outsider.id})
    assert raised.value.code == "ineligible_recipient"
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"pool_id": "does-not-exist"})
    assert raised.value.code == "ineligible_author"
    assert db.session.get(Recording, dictation.recording_id).title == "d"
    assert db.session.get(ClinicDictation, dictation.id).version_counter == 0


def test_metadata_is_not_parsed_for_patient_context():
    world = _world()
    with pytest.raises(ChangeError) as raised:
        _apply(world, {"patient_id": "12345"})
    assert raised.value.code == "unknown_field"
    _apply(world, {"title": "Patient 12345 Müller"})
    assert not hasattr(world[4], "patient_id")
