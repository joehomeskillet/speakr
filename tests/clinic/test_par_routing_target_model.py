"""PAR-ADM-007 routing snapshots are durable data, never visibility grants."""

import pytest

from src.clinic import pools, recipient_assignment as targets, recipient_defaults as defaults
from src.clinic import registry, workflow
from src.clinic.models import ClinicDictation
from src.database import db

from test_pools import _dictation, _user, pool_db as pool_db
from test_wp_recipient_defaults import _world


@pytest.mark.parametrize("mode", ["all_eligible", "person", "staff_pool", "self_correction"])
def test_target_survives_reload_without_changing_assignment_or_rights(mode):
    actor, author, first, second, authors, staff = _world()
    dictation = _dictation(author, authors.id)
    dictation_id, author_id = dictation.id, author.id
    item = workflow.ClinicWorkflowDictation(
        id=dictation_id, author_user_id=author_id, pool_id=authors.id,
        status=workflow.TO_WRITE, assignee_user_id=second.id,
    )
    db.session.add(item)
    db.session.commit()
    users = (actor.id, author_id, first.id, second.id)
    before = {uid: pools.grants_for(uid, dictation) for uid in users}
    grants = pools.ClinicVisibilityGrant.query.count()
    selected = [str(first.id)] if mode == "person" else [staff.id] if mode == "staff_pool" else []
    expected = (first.id,) if mode == "person" else (author_id,) if mode == "self_correction" else (first.id, second.id)
    second_id = second.id

    snapshot = targets.set_target(
        dictation_id, actor_id=author_id, override={"mode": mode, "target_ids": selected}
    )
    db.session.commit()
    db.session.remove()

    assert targets.get_target(dictation_id) == snapshot
    assert snapshot.mode == mode
    assert snapshot.target_ids == tuple(selected)
    assert set(snapshot.recipient_user_ids) == set(expected)
    assert db.session.get(workflow.ClinicWorkflowDictation, dictation_id).assignee_user_id == second_id
    loaded = db.session.get(ClinicDictation, dictation_id)
    assert {uid: pools.grants_for(uid, loaded) for uid in users} == before
    assert pools.ClinicVisibilityGrant.query.count() == grants

    # A stored recipient loses visibility when the independent pool link disappears.
    pools.ClinicPoolRoute.query.filter_by(author_pool_id=loaded.pool_id).delete()
    db.session.commit()
    db.session.remove()
    assert targets.get_target(dictation_id) == snapshot
    loaded = db.session.get(ClinicDictation, dictation_id)
    for uid in users:
        if uid != author_id:
            assert pools.grants_for(uid, loaded)["allowed"] == {}


def test_legacy_row_has_no_target_and_default_edits_do_not_change_snapshot():
    _, author, first, _, authors, _ = _world()
    dictation_id = _dictation(author, authors.id).id
    author_id = author.id
    assert "recipient_assignment" in registry.MODEL_MODULES
    registry.create_clinic_tables()
    registry.create_clinic_tables()
    assert targets.get_target(dictation_id) is None
    snapshot = targets.set_target(dictation_id, actor_id=author_id)
    db.session.commit()
    defaults.set_default(
        author_id, "person", actor_id=author_id, expected_version=0, target_ids=[str(first.id)]
    )
    db.session.remove()
    assert targets.get_target(dictation_id) == snapshot
    assert snapshot.default_version == 0


def test_target_write_is_authorized_validated_and_rollbackable():
    actor, author, first, _, authors, _ = _world()
    dictation_id, author_id = _dictation(author, authors.id).id, author.id
    with pytest.raises(defaults.RecipientDefaultError, match="unauthorized"):
        targets.set_target(dictation_id, actor_id=actor.id)
    assert targets.get_target(dictation_id) is None
    targets.set_target(dictation_id, actor_id=author_id)
    db.session.rollback()
    assert targets.get_target(dictation_id) is None
    snapshot = targets.set_target(dictation_id, actor_id=author_id)
    db.session.commit()
    with pytest.raises(defaults.RecipientDefaultError, match="invalid_mode"):
        targets.set_target(dictation_id, actor_id=author_id, override={"mode": "unknown"})
    assert targets.get_target(dictation_id) == snapshot
    updated = targets.set_target(
        dictation_id, actor_id=author_id,
        override={"mode": "person", "target_ids": [str(first.id)]},
    )
    db.session.commit()
    db.session.remove()
    assert targets.get_target(dictation_id) == updated
    assert targets.ClinicRecipientTarget.query.filter_by(dictation_id=dictation_id).count() == 1


def test_self_correction_without_pool_or_typists():
    author = _user()
    author_id = author.id
    dictation_id = _dictation(author, None).id
    snapshot = targets.set_target(
        dictation_id, actor_id=author_id, override={"mode": "self_correction"}
    )
    db.session.commit()
    db.session.remove()
    assert targets.get_target(dictation_id) == snapshot
    assert snapshot.author_pool_id is None
    assert snapshot.recipient_user_ids == (author_id,)
    assert db.session.get(workflow.ClinicWorkflowDictation, dictation_id) is None
