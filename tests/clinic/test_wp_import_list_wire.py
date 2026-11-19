"""Direct clinic clarification calls use the predecessor visibility policy."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from src.clinic import import_centre as ic
from src.clinic import import_visibility as visibility
from src.clinic.agent_channel import AgentChannelError
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models.audit import AuditEvent

from test_import_centre import NOW, _data, _env as _env, _finalize, _intent, env as env
from test_wp_unassigned_visibility import scene as scene


def test_direct_list_requires_actor_and_applies_scope(scene):
    policy, admin, lead, author, ids, _ = scene
    for kwargs in ({}, {"policy": policy}, {"actor_user_id": admin}):
        with pytest.raises(AgentChannelError, match="forbidden"):
            ic.list_unassigned(**kwargs)

    for actor, expected in ((admin, ids), (lead, ids[:1]), (author, [])):
        rows = ic.list_unassigned(actor_user_id=actor, policy=policy)
        assert {row["id"] for row in rows} == set(expected)
        assert all(row["snapshot_version"] and "storage_key" not in row for row in rows)
        assert all(row["original_state"] == "author_required" for row in rows)

    def deny(actor, action, scope):
        with pytest.raises(AgentChannelError, match="forbidden"):
            ic.list_unassigned()
        return False

    assert ic.list_unassigned(actor_user_id=admin, policy=replace(policy, may_access=deny)) == []
    assert ic.list_unassigned(actor_user_id=admin, policy=visibility.ClarificationPolicy()) == []


def test_direct_assignment_is_scoped_audited_and_retryable(env, scene, monkeypatch):
    policy, _, lead, author, ids, (ingest_id, body, _) = scene
    delivery_id = ids[0]
    late = NOW + timedelta(days=2)
    first = _finalize(env, ingest_id, body, now=late)
    assert first["state"] == "UNKNOWN"
    original = db.session.get(ic.ClinicImportOriginal, delivery_id)
    path = env["service"].file_path(original.storage_key)
    intent = _intent(env, actor_user_id=lead, target_author_user_id=author,
                     may_assign_author=lambda *_: True, now=late)
    version = ic.list_unassigned(actor_user_id=lead, policy=policy)[0]["snapshot_version"]
    claim = dict(delivery_id=delivery_id, intent_id=intent["id"], now=late)
    scoped = dict(**claim, policy=policy, actor_user_id=lead, expected_snapshot_version=version)
    for kwargs, code in (
        (claim, "forbidden"), (claim | {"actor_user_id": lead}, "forbidden"),
        (scoped | {"actor_user_id": author}, "not_found"),
        (scoped | {"delivery_id": ids[1]}, "not_found"),
        (scoped | {"policy": replace(policy, may_target=None)}, "forbidden"),
        (scoped | {"expected_snapshot_version": "stale"}, "conflict"),
    ):
        with pytest.raises(AgentChannelError, match=code):
            ic.assign_unassigned(**kwargs)

    def fail_commit():
        raise OperationalError("COMMIT", {}, RuntimeError("synthetic commit failure"))

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", fail_commit)
        with pytest.raises(OperationalError, match="synthetic commit failure"):
            ic.assign_unassigned(**scoped)

    events = AuditEvent.query.filter_by(event="recording.edited", actor_user_id=lead)
    assert events.count() == ClinicDictation.query.count() == 0
    assert [row["id"] for row in ic.list_unassigned(actor_user_id=lead, policy=policy)] == [delivery_id]
    assert path.read_bytes() == _data("usb-1")
    assert _finalize(env, ingest_id, body, now=late) == first
    assert ic.assign_unassigned(**scoped) == delivery_id
    with pytest.raises(AgentChannelError, match="conflict"):
        ic.assign_unassigned(**scoped)
    assert events.one().target_user_id == author
    assert ic.list_unassigned(actor_user_id=lead, policy=policy) == []
    receipt = _finalize(env, ingest_id, body, now=late)
    assert receipt["state"] == "STORED"
    assert _finalize(env, ingest_id, body, now=late) == receipt
    assert ClinicDictation.query.one().author_user_id == author
    assert ClinicOriginal.query.one().storage_key == original.storage_key
    assert path.read_bytes() == _data("usb-1")
