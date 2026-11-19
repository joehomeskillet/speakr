"""Synthetic author evidence through the real import channel and original store."""

from datetime import timedelta

import pytest

from src.clinic import agent_channel as ch
from src.clinic import import_centre as ic
from src.clinic import policy
from src.clinic.agent_channel import AgentChannelError
from src.clinic.author_resolution import normalise_alias
from src.clinic.import_visibility import ClarificationPolicy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db

from test_import_centre import NOW, _data, _env as _env, _intent, _upload, env as env


@pytest.fixture(autouse=True)
def _import_grants(env, monkeypatch):
    """Import intents need the central-policy action ``import``; grant it to the synthetic users."""
    allowed = {env["author"].id, env["other"].id}
    monkeypatch.setattr(policy, "_grant_providers", [
        lambda uid: (policy.Grant("all", frozenset({"import"})),) if uid in allowed else (),
    ])


def _finalize(env, ingest_id, body, *, now=NOW, **evidence):
    return ic.finalize_delivery(
        env["principal"], env["policy"], ingest_id=ingest_id, body=body,
        ingest_service=env["service"], now=now, **evidence,
    )


@pytest.mark.parametrize("device,header", [(True, False), (False, True), (True, True)])
def test_resolved_author_selects_the_compatible_intent(env, clinic_on, device, header):
    other = _intent(env, actor_user_id=env["other"].id, now=NOW - timedelta(minutes=1))
    expected = _intent(env)
    data = _data("resolved")
    ingest_id, body, _ = _upload(env, data)
    row = db.session.get(ch.ClinicAgentIngest, ingest_id)
    snapshot = (env["agent_id"], row.source_id, row.snapshot_version, body["sha256"])
    headers = {snapshot: "  A\u0308da   Test  "} if header else {}

    receipt = _finalize(
        env, ingest_id, body, device_map={"USB-1": env["author"].id} if device else {},
        alias_map={normalise_alias("Äda Test"): [env["author"].id]},
        embedded_author_for=lambda *key: headers.get(key),
    )
    assert receipt["state"] == "STORED"
    assert receipt["import"]["intent_id"] == expected["id"]
    assert db.session.get(ic.ClinicImportIntent, other["id"]).status == "OPEN"
    assert db.session.get(ClinicDictation, receipt["dictation_id"]).author_user_id == env["author"].id
    original = db.session.get(ClinicOriginal, receipt["original_id"])
    assert env["service"].file_path(original.storage_key).read_bytes() == data


@pytest.mark.parametrize("case", ["missing", "unknown", "stale-header", "header-conflict", "intent-conflict"])
def test_unresolved_original_survives_until_confirmed_assignment(env, clinic_on, case):
    intent = _intent(env)
    data = _data(case)
    ingest_id, body, _ = _upload(env, data)
    row = db.session.get(ch.ClinicAgentIngest, ingest_id)
    source, version = row.source_id, row.snapshot_version
    headers = {}
    if case == "stale-header":
        headers[(env["agent_id"], source, "old-version", body["sha256"])] = "First"
    if case == "header-conflict":
        headers[(env["agent_id"], source, version, body["sha256"])] = "Second"
    if case == "unknown":
        headers[(env["agent_id"], source, version, body["sha256"])] = "Unknown"
    bindings = {
        "header-conflict": {"USB-1": env["author"].id},
        "intent-conflict": {"USB-1": env["other"].id},
    }
    body.update(embedded_author="First", device_id="usb-1", windows_login=env["author"].username,
                suggested_author_user_id=env["author"].id, author_user_id=env["admin"].id)
    first = _finalize(
        env, ingest_id, body, device_map=bindings.get(case, {}),
        alias_map={"first": [env["author"].id], "second": [env["other"].id]},
        embedded_author_for=lambda *key: headers.get(key),
    )
    assert first["state"] == "UNKNOWN" and first["import"]["status"] == "UNASSIGNED"
    assert first["import"]["intent_id"] is None
    assert first["permitted_post_step"] == "none" and not first["deletion_permitted"]
    assert ClinicDictation.query.count() == 0
    delivery_id = first["import"]["delivery_id"]
    original = db.session.get(ic.ClinicImportOriginal, delivery_id)
    assert original.origin_ref == f"agent:{env['agent_id']}:{source}:{version}"
    assert (original.content_hash, original.size_bytes) == (body["sha256"], len(data))
    path = env["service"].file_path(original.storage_key)
    inode = path.stat().st_ino
    assert path.read_bytes() == data

    # Clarification calls need an explicit policy in clinic mode (w116); this one allows every
    # actor and target so the original refusals below still come from the intent checks.
    clarify = ClarificationPolicy(may_access=lambda *_: True, may_target=lambda *_: True)
    late = NOW + timedelta(days=2)
    assert _finalize(env, ingest_id, body, now=late, device_map={"USB-1": env["author"].id}) == first
    with pytest.raises(AgentChannelError, match="intent is not open"):
        ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id,
                             intent_id=intent["id"], policy=clarify,
                             expected_snapshot_version=version, now=late)
    confirmed = _intent(env, now=late)
    with pytest.raises(AgentChannelError) as error:
        ic.assign_unassigned(actor_user_id=env["other"].id, delivery_id=delivery_id,
                             intent_id=confirmed["id"], policy=clarify,
                             expected_snapshot_version=version, now=late)
    assert error.value.code == "not_found"
    assert ClinicDictation.query.count() == 0
    ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id,
                         intent_id=confirmed["id"], policy=clarify,
                         expected_snapshot_version=version, now=late)
    healed = _finalize(env, ingest_id, body, now=late)
    assert healed["state"] == "STORED"
    assert _finalize(env, ingest_id, body, now=late) == healed
    assert ClinicDictation.query.one().author_user_id == env["author"].id
    assert ClinicOriginal.query.one().storage_key == original.storage_key
    assert path.stat().st_ino == inode and path.read_bytes() == data
